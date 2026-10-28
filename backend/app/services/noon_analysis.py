"""午间事实整理；不调用收盘指标、策略或交易逻辑。"""
from collections import Counter
from datetime import date
from app.services.noon_collection import STOCK_CODE, SECTOR_CODE, finite

INDEX_NAMES = {'000001.SH': '上证指数', '399001.SZ': '深证成指',
               '399006.SZ': '创业板指', '000688.SH': '科创50'}


def number(value, *, positive=False, nonnegative=False):
    if not finite(value) or positive and value <= 0 or nonnegative and value < 0:
        return None
    return value


def normalize_quotes(rows, identities=None, *, sectors=None):
    output, seen = [], set()
    code_counts = Counter(row['thscode'] for row in rows or []
                          if isinstance(row, dict) and isinstance(row.get('thscode'), str))
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        code = raw.get('thscode')
        pattern = SECTOR_CODE if sectors is not None else STOCK_CODE
        if (not isinstance(code, str) or not pattern.fullmatch(code) or code in seen
                or code_counts[code] != 1 or sectors is not None and code not in sectors):
            continue
        seen.add(code)
        name = (sectors or {}).get(code, {}).get('name') if sectors is not None else (identities or {}).get(code)
        row = {'instrument_id': code, 'name': str(name or raw.get('name') or INDEX_NAMES.get(code) or code),
               'latest_price': number(raw.get('last_price'), positive=True),
               'change_pct': number(raw.get('price_change_ratio_pct')),
               'open': number(raw.get('open_price'), positive=True),
               'high': number(raw.get('high_price'), positive=True),
               'low': number(raw.get('low_price'), positive=True),
               'prev_close': number(raw.get('prev_price'), positive=True),
               'volume': number(raw.get('volume'), nonnegative=True),
               'amount': number(raw.get('turnover'), nonnegative=True),
               'quote_at': None, 'price_basis': 'none', 'currency': 'CNY',
               'volume_unit': 'share', 'time_reason': '来源单条报价未提供交易时间'}
        reasons = []
        if row['latest_price'] is None:
            reasons.append('最新价缺失或无效，不能认定停牌')
        if row['prev_close'] is None:
            reasons.append('前收缺失或无效，涨跌幅不可独立对账')
        if row['latest_price'] is not None and row['prev_close'] is not None and row['change_pct'] is not None:
            if abs((row['latest_price'] / row['prev_close'] - 1) * 100 - row['change_pct']) > .001:
                reasons.append('来源涨跌幅与最新价/前收不一致')
                row['change_pct'] = None
        lo, hi = row['low'], row['high']
        if lo is not None and hi is not None and (lo > hi or any(
                row[k] is not None and not lo - .011 <= row[k] <= hi + .011 for k in ('open', 'latest_price'))):
            reasons.append('来源价格范围不一致')
            row['latest_price'], row['change_pct'] = None, None
        if row['amount'] is None or row['volume'] is None:
            reasons.append('成交量或成交额缺失')
        row['missing_reason'] = '；'.join(reasons) or None
        if sectors is not None:
            row.update(topic_code=code, kind=sectors[code]['kind'], members_observed_date=None,
                       member_coverage=None, member_up=None, member_down=None)
            row['volume_unit'] = 'source_raw'
        output.append(row)
    return output


def valid_breadth_quote(row):
    return row['latest_price'] is not None and row['prev_close'] is not None and row['change_pct'] is not None


def build_summary(stocks, total, enumeration_complete):
    valid = [r for r in stocks if valid_breadth_quote(r)]
    priced = [r for r in stocks if r['latest_price'] is not None]
    amounts = [r['amount'] for r in priced if r['amount'] is not None]
    known = total if isinstance(total, int) and not isinstance(total, bool) else None
    breadth = {'up': sum(r['change_pct'] > 0 for r in valid) if valid else None,
               'down': sum(r['change_pct'] < 0 for r in valid) if valid else None,
               'flat': sum(r['change_pct'] == 0 for r in valid) if valid else None,
               'valid': len(valid), 'total': known, 'missing': max(0, known - len(valid)) if known is not None else None,
               'scope': '本次午间采集的有价且前收/涨跌幅可核对A股样本；不等同全市场当日可交易集合',
               'distribution': {'above_5_pct': sum(r['change_pct'] >= 5 for r in valid) if valid else None,
                                'below_minus_5_pct': sum(r['change_pct'] <= -5 for r in valid) if valid else None}}
    return {'breadth': breadth,
            'amount': {'value': sum(amounts) if amounts else None, 'valid': len(amounts), 'total': known,
                       'scope': '已获有效最新价的沪深京A股股票累计成交额；指数不相加，非资金净流入',
                       'complete': bool(known and enumeration_complete and len(amounts) == known)},
            'coverage': {'stock_codes': len(stocks), 'declared_stock_codes': known,
                         'priced_stocks': len(priced), 'missing_price_count': max(0, known - len(priced)) if known is not None else None,
                         'enumeration_complete': enumeration_complete, 'individual_quote_times_verified': False},
            'top_amount': sorted((r for r in stocks if r['latest_price'] is not None and r['amount'] is not None),
                                 key=lambda r: (-r['amount'], r['instrument_id']))[:20],
            'top_gainers': sorted(valid, key=lambda r: (-r['change_pct'], r['instrument_id']))[:20]}


def sector_members(rows, topics, quotes, day, limit_up_pool=None):
    by_code = {topic.get('topic_code'): topic for topic in topics or [] if isinstance(topic, dict)}
    for row in rows:
        row.update(member_limit_up_count=None, member_limit_up_state='unknown',
                   member_limit_up_reason='本次未取得可与成员名单核对的完整涨停池')
        topic = by_code.get(row['topic_code'], {})
        observed = topic.get('observed_date')
        try:
            valid_day = isinstance(observed, str) and date.fromisoformat(observed).isoformat() == observed and observed <= day
        except ValueError:
            valid_day = False
        members = topic.get('instrument_ids')
        if not valid_day or not isinstance(members, list):
            row['member_reason'] = '未取得采集开始前可核验的成员名单；指数报价独立保留'
            continue
        member_ids_valid = all(isinstance(code, str) and STOCK_CODE.fullmatch(code) for code in members)
        members = set(code for code in members if isinstance(code, str) and STOCK_CODE.fullmatch(code))
        values = [quotes[code] for code in members if code in quotes and valid_breadth_quote(quotes[code])]
        row.update(members_observed_date=observed,
                   member_coverage={'valid': len(values), 'total': len(members),
                                    'complete': topic.get('members_complete') is True and member_ids_valid and len(values) == len(members)},
                   member_up=sum(r['change_pct'] > 0 for r in values) if values else None,
                   member_down=sum(r['change_pct'] < 0 for r in values) if values else None,
                   member_reason='本地已留存名单与本次报价交集；成员采集日不等同股票报价日')
        pool = limit_up_pool or {}
        if pool.get('state') in ('observed', 'partial'):
            up_ids = {item.get('instrument_id') for item in pool.get('items', []) if isinstance(item, dict)}
            count = len(members & up_ids)
            if topic.get('members_complete') is True and member_ids_valid and pool.get('state') == 'observed':
                row.update(member_limit_up_count=count, member_limit_up_state='observed',
                           member_limit_up_reason='本次已核验涨停池与已留存完整成员名单交集；不是收盘涨停确认')
            elif count:
                row.update(member_limit_up_count=count, member_limit_up_state='partial',
                           member_limit_up_reason='成员或涨停池覆盖不完整，仅为已确认样本下界')
            else:
                row['member_limit_up_reason'] = '成员或涨停池覆盖不完整，未匹配不能视为0家'
    return sorted(rows, key=lambda r: (r['change_pct'] is None, -(r['change_pct'] or 0), r['instrument_id']))


def normalize_pool(rows, declared, complete, quotes, name):
    output, excluded = [], 0
    for raw in rows or []:
        if not isinstance(raw, dict) or not isinstance(raw.get('thscode'), str) or not STOCK_CODE.fullmatch(raw['thscode']):
            excluded += 1
            continue
        code = raw['thscode']; price = number(raw.get('last_price'), positive=True)
        quote = quotes.get(code)
        if price is None or not quote or quote['latest_price'] is None or abs(price - quote['latest_price']) > .011:
            excluded += 1
            continue
        item = {'instrument_id': code, 'name': raw.get('name') or quote['name'],
                       'latest_price': price, 'change_pct': number(raw.get('price_change_ratio_pct')),
                       **{key: raw.get(key) for key in ('limit_up_time', 'first_limit_time', 'last_limit_time',
                          'limit_up_reason', 'continue_day_text', 'continue_day_cnt', 'seal_money',
                          'max_seal_money', 'open_times', 'turnover_ratio_pct')}}
        for key in ('seal_money', 'max_seal_money', 'turnover_ratio_pct'):
            item[key] = number(raw.get(key), nonnegative=True)
        value = raw.get('open_times')
        item['open_times'] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        output.append(item)
    counts = Counter(r['instrument_id'] for r in output)
    if any(n > 1 for n in counts.values()):
        output = [r for r in output if counts[r['instrument_id']] == 1]
        excluded += sum(n for n in counts.values() if n > 1)
    state = 'observed' if complete and not excluded else 'partial' if output else 'unknown'
    return {'state': state, 'count': len(output) if output else None,
            'declared_total': declared, 'items': output, 'excluded_rows': excluded,
            'reason': None if state == 'observed' else '来源范围、身份、价格或分页未完整核验，不能把缺失视为零事件',
            'scope': f'扶摇{name}来源池；价格与本次股票快照交叉核对，尚未收盘'}


def board_summary(pool):
    values = [row.get('continue_day_cnt') for row in pool.get('items', [])]
    known = [value for value in values if isinstance(value, int) and not isinstance(value, bool) and value > 0]
    counts = Counter(known)
    maximum = max(known) if known else None
    unknown = len(values) - len(known)
    return {'max_confirmed': maximum, 'max_continuous_board': maximum,
            'first_board_count': counts.get(1, 0) if known else None,
            'multi_board_count': sum(count for height, count in counts.items() if height >= 2) if known else None,
            'continuous_board_count': sum(count for height, count in counts.items() if height >= 2) if known else None,
            'height_unknown_count': unknown, 'unknown_count': unknown,
            'distribution': [{'board_count': height, 'count': count} for height, count in sorted(counts.items(), reverse=True)],
            'scope': '本次已核验涨停池价格样本的来源连续板数字段；不是全市场收盘确认高度，不解析几天几板文本',
            'reason': None if known and pool.get('state') == 'observed' and not unknown else
                      '样本或来源连续板数字段不完整，家数与高度只表示已核验部分'}


def build_analysis(summary, indices, events, sectors, gaps):
    texts, observations, risks = [], [], []
    breadth = summary['breadth']; valid = breadth['valid']
    if valid:
        texts.append(f'本次{valid}只有价可比样本中，上涨{breadth["up"]}只、下跌{breadth["down"]}只、平盘{breadth["flat"]}只。')
    values = [row for row in indices if row['latest_price'] is not None and row['change_pct'] is not None]
    if values:
        texts.append('主要指数午间表现：' + '、'.join(f'{r["name"]}{r["change_pct"]:+.2f}%' for r in values) + '。')
    if summary['amount']['value'] is not None:
        observations.append(f'有价样本累计成交额{summary["amount"]["value"]/1e8:.2f}亿元；没有昨日同期数据，不推算全天成交额。')
    leading = [r for r in sectors if r['latest_price'] is not None and r['change_pct'] is not None][:3]
    if leading:
        observations.append('已采板块样本涨幅前列：' + '、'.join(f'{r["name"]}({r["change_pct"]:+.2f}%)' for r in leading) + '；范围见板块覆盖。')
    up = events['limit_up']['declared_total']; stats = events['statistics'].get('limit_up_stock_count')
    if up is not None and stats is not None and up != stats:
        risks.append(f'涨停池声明{up}家，统计接口封板{stats}家，来源范围或采样时点存在差异，分别列示。')
    if summary['coverage']['missing_price_count']:
        risks.append(f'{summary["coverage"]["missing_price_count"]}个代码没有有效最新价，原因待核验，不能认定停牌。')
    risks.append('单条行情时间未知；单次封单额不代表稳定性，午间结果不等同收盘信号。')
    if gaps:
        risks.append('部分来源或范围尚未通过核验，请结合数据与缺口页判断适用范围。')
    return {'summary': texts or ['当前没有足够可比样本形成市场广度结论。'], 'observations': observations,
            'risks': risks, 'afternoon': ['继续核对主要指数及上涨范围是否保持。',
            '对照上个交易日原关注项，核对板块与观察股票的当前事实。',
            '午间观察不触发策略、跟踪或回测事件；收盘后另按原流程核验。']}
