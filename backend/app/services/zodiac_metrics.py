"""Batched, dated pool statistics using the shared stock-history proof contract."""
from collections import Counter, defaultdict
from statistics import median

from app.core import connect, get_market
from app.analytics.adjustments import continuous_prices
from app.analytics.indicators import enrich_bars
from app.analytics.boll_conditions import latest_states
from app.domain.universe import identity_flags, BOARDS
from app.services.stock_board_presentation import retained_proof, down_state, build_board_presentation, source_board_events, pattern_fields
from app.services.market_service import dated_events
from app.domain.conditions import metrics_for
from app.services import topic_member_metrics as metrics
from app.services.market_supplement import load_supplement_with_fingerprint
from app.services.member_turnover import turnover_index, turnover_for
from app.services.stock_memberships import current_memberships
from app.services.stock_summary import _master, summary_fields
from app.services.tracking_quotes import action_dates_for, tracking_quotes
from app.services.watchlist import _since_added
from app.services.workspace_common import fail, rows
from app.services import zodiac

TECH_FIELDS = [*(f'ma{n}' for n in (5, 10, 20, 30, 60, 250)), 'macd_dif', 'macd_dea', 'macd_hist', 'kdj_k', 'kdj_d', 'kdj_j', 'boll_upper', 'boll_mid', 'boll_lower']
SORT_FIELDS = {'name', 'instrument_id', 'focused', 'added_business_date', 'round_number', 'close', 'change_pct', 'return_ytd_pct', 'change_30d_pct', 'return_mtd_pct', 'limit_up_ytd', 'last_limit_up_date', 'since_entry_pct', 'amount', 'volume', 'turnover_rate', 'volume_vs_5d', 'holding_sessions', 'max_drawdown_pct', 'return_5d_pct', 'return_10d_pct', 'return_20d_pct', 'return_60d_pct', *TECH_FIELDS}


def valid_quote(row):
    return all(metrics._number(row.get(k)) is not None and row[k] > 0 for k in ('open', 'high', 'low', 'close', 'volume'))


def unique_history(history, day):
    counts = Counter(row.get('date') for row in history)
    return sorted([r for r in history if metrics._date(r.get('date')) == r.get('date') and r.get('date') is not None and r['date'] <= day and counts[r['date']] == 1 and valid_quote(r)], key=lambda r: r['date'])


def process_metrics(row, history, actions, calendar, day, quote, benchmark, benchmark_actions):
    start = row['added_business_date']
    since = _since_added({'instrument_id': row['instrument_id'], 'added_business_date': start,
        'watchlist_entry_price': row['entry_price'], 'watchlist_entry_price_basis': row['entry_basis'].get('price_basis'),
        'watchlist_entry_reason': row['entry_basis'].get('reason')}, quote, action_dates_for(actions, row['instrument_id']), day)
    result = {'since_entry_pct': since['watchlist_change_pct'], 'since_entry_reason': since['watchlist_change_reason'],
        'holding_sessions': calendar.index(day)-calendar.index(start) if start in calendar and day in calendar and start <= day else None,
        'peak_return_pct': None, 'drawdown_pct': None, 'max_drawdown_pct': None, 'relative_benchmark_pp': None,
        'benchmark': '000001.SH · 上证指数', 'path_reason': None, 'benchmark_reason': None}
    selected = [r for r in history if start <= r['date'] <= day]
    expected = [d for d in calendar if start <= d <= day]
    reason = result['since_entry_reason']
    if not reason and (not expected or [r['date'] for r in selected] != expected):
        reason = '加入以来存在停牌、缺数或无效报价，路径指标保持未知'
    if not reason and (not selected or abs(selected[0]['close'] - row['entry_price']) > .00000001):
        reason = '当前历史起点与冻结原始基准不一致，路径指标不重写原基准'
    if not reason:
        values = [r['close'] for r in selected]; peak = values[0]; drawdown = 0
        for price in values:
            peak = max(peak, price); drawdown = min(drawdown, (price/peak-1)*100)
        result.update(peak_return_pct=(max(values)/row['entry_price']-1)*100,
                      drawdown_pct=(values[-1]/max(values)-1)*100, max_drawdown_pct=drawdown)
    result['path_reason'] = reason
    end_quote = next((r for r in benchmark if r['date'] == day), None)
    reference, reference_reason, coverage = metrics._return(benchmark, benchmark_actions, calendar, start, day, end_quote)
    if result['since_entry_pct'] is not None and reference is not None:
        result['relative_benchmark_pp'] = result['since_entry_pct'] - reference
    result['benchmark_reason'] = result['since_entry_reason'] or reference_reason
    result['benchmark_coverage'] = coverage
    return result


def bands(history, calendar, day, period='D'):
    state = next(iter(latest_states(history, calendar, day, period, configs=[{'require_squeeze': False}]).values()), {})
    evidence = state.get('evidence', {})
    return {f'boll_{field}': evidence.get(source) for field, source in (('upper', 'upper'), ('mid', 'middle'), ('lower', 'lower'))}


def extended(history, actions, calendar, day, quote):
    result = {}; position = calendar.index(day) if day in calendar else -1
    for n in (5, 10, 20, 60):
        start = calendar[position-n] if position >= n else None
        value, reason, coverage = metrics._return(history, actions, calendar, start, day, quote, baseline_proof=f'精确T/T-{n}市场交易日')
        result.update({f'return_{n}d_pct': value, f'return_{n}d_reason': reason, f'return_{n}d_coverage': coverage})
    previous = [r for r in history if position >= 5 and r['date'] in calendar[position-5:position]]
    known_actions = isinstance(actions, list) and all(isinstance(a, dict) and metrics._date(a.get('date')) is not None and metrics._date(a.get('date')) == a.get('date') for a in actions)
    comparable = known_actions and position >= 5 and not any(calendar[position-5] < a['date'] <= day for a in actions)
    result['volume_vs_5d'] = quote['volume']/(sum(r['volume'] for r in previous)/5) if quote and len(previous) == 5 and comparable else None
    result['volume_vs_5d_reason'] = None if result['volume_vs_5d'] is not None else '精确前5个市场交易日正成交量或公司行动可比性未齐；此项为日量比较，不是盘口量比'
    result.update(total_market_cap=None, float_market_cap=None, market_cap_reason='当前本地日线及补充未提供同日可核验股本和市值来源')
    adjusted, info = [], {}
    if known_actions:
        try: adjusted, info = continuous_prices(history, actions, day, calendar)
        except (ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError): pass
    if info.get('usable'):
        for bar in adjusted: bar['corporate_action_coverage_known'] = True
        enriched = enrich_bars(adjusted, calendar, tail=1)
        last = enriched[-1] if enriched and enriched[-1]['date'] == day else {}
        result.update({field: last.get(field) for field in TECH_FIELDS})
        result.update(bands(adjusted, calendar, day))
    else:
        result.update({field: None for field in TECH_FIELDS})
    result['technical_reason'] = '按市场交易日严格计算；停牌缺数、未完成周期或预热不足为空' if info.get('usable') else '公司行动连续价格口径未核验，技术指标保持未知'
    result['technical_price_basis'] = info.get('price_basis')
    result['technical_anchor'] = day
    for n in (20, 60):
        dates = calendar[max(0, position-n+1):position+1] if position >= n-1 else []
        window = [r for r in adjusted if r['date'] in dates] if info.get('usable') else []
        result[f'distance_{n}d_high_pct'] = (window[-1]['close']/max(r['high'] for r in window)-1)*100 if len(window) == n and [r['date'] for r in window] == dates else None
    return result


def enrich(saved, market, head):
    if not saved: return []
    calendar = sorted(set(market.calendar())); master = _master(market)
    day = head['business_date']; status = market.status() if callable(getattr(market, 'status', None)) else {}
    snapshot = status.get('snapshot_id')
    supplement, fingerprint = load_supplement_with_fingerprint(snapshot, dates=[d for d in calendar if d <= day], fields=('public_pools', 'annual_pools', 'annual_down_pools')) if snapshot else ({}, None)
    reference = market.reference() if callable(getattr(market, 'reference', None)) else {}
    reference = {**reference, '_supplement': supplement}
    board_proof = retained_proof(reference, snapshot)
    memberships = current_memberships(market, [r['instrument_id'] for r in saved], expected_day=day)
    quotes = {}
    for offset in range(0, len(saved), 128):
        quotes.update(tracking_quotes(market, saved[offset:offset+128], calendar, day, strict_close=True))
    groups = defaultdict(list)
    for row in saved: groups[row['end_business_date'] or day].append(row)
    result = []
    for cutoff, group in groups.items():
        cutoff_calendar = [d for d in calendar if d <= cutoff]
        sources = metrics._sources(reference, supplement, cutoff_calendar[-30:])
        turnover = turnover_index(reference, supplement, cutoff)
        events = dated_events(reference, cutoff)
        for offset in range(0, len(group), 128):
            batch = group[offset:offset+128]; symbols = sorted({r['instrument_id'] for r in batch})
            position = calendar.index(cutoff) if cutoff in calendar else 0
            start = min([r['added_business_date'] for r in batch] + [calendar[max(0, position-300)] if calendar else cutoff])
            history = market.history_for_instruments([*symbols, '000001.SH'], start=start, end=cutoff)
            action_reader = getattr(market, 'actions_for_instruments', None)
            actions = action_reader([*symbols, '000001.SH']) if callable(action_reader) else {}
            actions = actions or {}
            cleaned = {s: unique_history(history.get(s, []), cutoff) for s in set(symbols) | {'000001.SH'}}
            exact = {s: next((r for r in cleaned[s] if r['date'] == cutoff), None) for s in symbols}
            stats = metrics.member_metrics(market, cutoff, symbols, master, {s: q for s, q in exact.items() if q}, supplement, supplement_fingerprint=fingerprint)
            extras = {s: extended(cleaned[s], actions.get(s), calendar, cutoff, exact[s]) for s in symbols}
            for row in batch:
                symbol = row['instrument_id']; quote = exact[symbol]; tags = memberships['memberships'].get(symbol, [])
                value = summary_fields({**row, **stats.get(symbol, {}), **extras[symbol]}, master.get(symbol), cutoff)
                value.update({k: metrics._number((quote or {}).get(k)) for k in ('open', 'high', 'low', 'close', 'volume', 'amount')})
                value['amount_reason'] = None if value['amount'] is not None else '截止日未留存可核验成交额字段'
                value.update(change_pct=quotes.get((symbol, cutoff), {}).get('change_pct'), metrics_as_of=cutoff, stats_year=int(cutoff[:4]),
                    snapshot_id=snapshot or head['generation'], quote_date=cutoff if quote else None, quote_reason=None if quote else '截止日缺少唯一有效且有成交的日K',
                    current_topics=tags, current_industries=[t for t in tags if t['kind'] == 'industry'], current_topics_observed_date=memberships['business_date'], current_topics_scope=memberships['scope'])
                value.update(turnover_for(symbol, cutoff, quote, turnover))
                value['is_st'] = identity_flags(master.get(symbol, {}), cutoff).get('is_st')
                own_quotes = {r['date']: r for r in cleaned[symbol] if r['date'] in cutoff_calendar[-3:]}
                prior, before = (cutoff_calendar[-2] if len(cutoff_calendar) >= 2 else None,
                                 cutoff_calendar[-3] if len(cutoff_calendar) >= 3 else None)
                up = board_proof._state(symbol, cutoff, quote)
                down = down_state(board_proof, symbol, cutoff, quote, up)
                shapes = pattern_fields(symbol, master.get(symbol, {}), cutoff, snapshot, calendar, own_quotes, value, up, down)
                board_view = build_board_presentation(value, symbol=symbol, day=cutoff, snapshot_id=snapshot, calendar=calendar,
                    quotes=own_quotes, events=events.get('limitup', []), break_events=events.get('breakpool', []),
                    up=up, previous_up=board_proof._state(symbol, prior, own_quotes.get(prior)),
                    before_previous_up=board_proof._state(symbol, before, own_quotes.get(before)),
                    down=down, patterns=shapes, board_text_events=source_board_events(board_proof,symbol,cutoff))
                value.update(board_presentation=board_view, today_limit_down=down.get('state'),
                    board_height=board_view['height']['value'], board_height_reason=board_view['height']['reason'],
                    board_types=' / '.join(t['label'] for t in board_view['types']) or None,
                    board_types_reason='同日来源及原始报价共同核验；没有已确认标签不代表盘中未发生事件')
                own = {symbol: actions.get(symbol)}
                value.update(process_metrics(row, cleaned[symbol], own, calendar, cutoff, quote or {}, cleaned['000001.SH'], actions.get('000001.SH')))
                daily = metrics._daily_states(symbol, {r['date']: r['close'] for r in cleaned[symbol]}, cutoff_calendar[-30:], sources)
                states = [daily[d].get('state') for d in cutoff_calendar[-30:]]
                value.update(limit_up_30d=sum(s is True for s in states) if len(states) == 30 and all(s is not None for s in states) else None,
                    limit_up_30d_observed=sum(s is True for s in states), limit_up_30d_reason='最近30个市场交易日逐日核验；缺档时只保留已确认下界',
                    today_limit_up=daily.get(cutoff, {}).get('state'))
                result.append(value)
    return result


def listing(year, *, status='tracking', query='', relation='', focused=False, board='', completeness='', sort='focused:desc,added_business_date:desc', offset=0, limit=50):
    zodiac.year_info(year)
    if status not in ('tracking', 'ended'): fail('无效成员状态')
    if board and board not in {*BOARDS, 'unknown'}: fail('无效上市板块')
    if relation and relation not in zodiac.RELATIONS: fail('无效关联类型')
    sorting = []
    for term in sort.split(','):
        parts = term.split(':')
        if len(parts) != 2 or parts[0] not in SORT_FIELDS or parts[1] not in ('asc', 'desc'): fail('无效排序字段或方向')
        sorting.append(parts)
    if len(sorting) > 3: fail('最多同时使用3个排序条件')
    if completeness not in ('', 'complete', 'missing'): fail('无效覆盖过滤')
    with connect() as c:
        c.execute('BEGIN')
        head = zodiac._head(c)
        p = c.execute('SELECT revision FROM zodiac_pool WHERE observation_year=?', (year,)).fetchone()
        saved = [zodiac.view(r) for r in rows(c, 'SELECT * FROM zodiac_pool_member WHERE observation_year=? AND status=? ORDER BY created_at_ms,member_id', (year, status))]
    values = enrich(saved, get_market(), head) if saved and head['business_date'] else []
    def complete(r): return all(r.get(k) is not None for k in ('return_ytd_pct', 'change_30d_pct', 'limit_up_ytd'))
    query = query.strip().casefold()
    selected = [r for r in values if (not query or query in ' '.join([r['name'], r['instrument_id'], r['reason'], r['custom_label'], *(t['name'] for t in r['current_topics'])]).casefold())
                and (not relation or r['relation_type'] == relation) and (not focused or r['focused'])
                and (not board or r['board'] == board) and (not completeness or complete(r) == (completeness == 'complete'))]
    selected.sort(key=lambda r: r['member_id'])
    for key, direction in reversed(sorting):
        known = [r for r in selected if r.get(key) is not None]; unknown = [r for r in selected if r.get(key) is None]
        selected = sorted(known, key=lambda r: r[key], reverse=direction == 'desc') + unknown
    changes = [r['change_pct'] for r in selected if r['change_pct'] is not None]
    # Fresh mutable state is read each request. Detect a publication change across market reads.
    with connect() as c:
        c.execute('BEGIN')
        if zodiac._head(c) != head: fail('行情已更新，请刷新生肖池', 409)
        current_revision = c.execute('SELECT revision FROM zodiac_pool WHERE observation_year=?', (year,)).fetchone()
        if (current_revision[0] if current_revision else 0) != (p[0] if p else 0): fail('生肖池已更新，请刷新', 409)
    return {**zodiac.year_info(year), **head, 'pool_revision': p[0] if p else 0, 'total': len(values), 'matched_count': len(selected),
        'items': selected[offset:offset+limit], 'navigation': [{k: r.get(k) for k in ('instrument_id','name','code','metrics_as_of')} for r in selected],
        'summary': {'focused': sum(r['focused'] for r in selected), 'up': sum(v > 0 for v in changes), 'down': sum(v < 0 for v in changes),
                    'flat': sum(v == 0 for v in changes), 'unknown': len(selected)-len(changes), 'median_change_pct': median(changes) if changes else None,
                    'known_count': len(changes), 'sample_count': len(selected), 'confirmed_limit_up': sum(r['today_limit_up'] is True for r in selected)},
        'coverage': {'total': len(selected), 'return_ytd_complete': sum(r.get('return_ytd_pct') is not None for r in selected),
            'change_30d_complete': sum(r.get('change_30d_pct') is not None for r in selected), 'limit_up_ytd_complete': sum(r.get('limit_up_ytd') is not None for r in selected),
            'last_limit_up_complete': sum(r.get('last_limit_up_complete') is True for r in selected)}}


def detail(member_id):
    with connect() as c:
        c.execute('BEGIN'); saved = zodiac.view(zodiac._get(c, member_id)); head = zodiac._head(c, True)
    market = get_market(); item = enrich([saved], market, head)[0]
    cutoff = item['metrics_as_of']; symbol = item['instrument_id']
    calendar = market.calendar(); start = calendar[max(0, calendar.index(cutoff)-1500)] if cutoff in calendar else cutoff
    history = unique_history(market.history_for_instruments([symbol], start=start, end=cutoff).get(symbol, []), cutoff)
    actions = market.actions_for_instruments([symbol]) if callable(getattr(market, 'actions_for_instruments', None)) else None
    evidence = actions.get(symbol) if isinstance(actions, dict) else None
    trend = {}
    if isinstance(evidence, list):
        try:
            adjusted, info = continuous_prices(history, evidence, cutoff, calendar)
            if info.get('usable'):
                for row in adjusted: row['corporate_action_coverage_known'] = True
                for period in ('D', 'W', 'M'):
                    computed = metrics_for(adjusted, cutoff, calendar, period=period)
                    trend[period] = {field: computed.get(field) for field in TECH_FIELDS}
                    trend[period].update(bands(adjusted, calendar, cutoff, period))
        except (TypeError, ValueError, KeyError, ZeroDivisionError, OverflowError): pass
    events = retained_events(symbol, saved['added_business_date'], cutoff)
    history = zodiac.history(member_id)['items']
    with connect() as c:
        c.execute('BEGIN')
        if zodiac._head(c) != head or zodiac._get(c, member_id)['revision'] != saved['revision']:
            fail('行情或观察记录已变化，请重新打开详情', 409)
    return {'item': item, 'period_metrics': trend, 'retained_events': events, 'history': history, **head}


def retained_events(symbol, start, end):
    """Existing SQLite records and bounded local dragon-tiger objects only."""
    from app.services.dragon_tiger import Store
    from app.services.supplement_access import supplement_access
    result = []
    with connect() as c:
        alerts = rows(c, "SELECT event_id,trade_date,rule,state FROM line_alert_event WHERE instrument_id=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date DESC LIMIT 100", (symbol,start,end))
        reports = rows(c, "SELECT report_id,data_cutoff FROM research_report WHERE instrument_id=? AND data_cutoff BETWEEN ? AND ? ORDER BY data_cutoff DESC LIMIT 30", (symbol,start,end))
    result.extend({'id':r['event_id'],'date':r['trade_date'],'label':'划线提醒 · '+r['rule'],'state':r['state']} for r in alerts)
    result.extend({'id':r['report_id'],'date':r['data_cutoff'],'label':'已保存个股研究（含当时获取资料）'} for r in reports)
    store = Store()
    with supplement_access():
        try:
            index = store.index()
            for day in sorted((d for d in index.get('days', {}) if start <= d <= end), reverse=True)[:30]:
                record = store.day(index, day)
                if not record: continue
                for kind, board in record.get('boards', {}).items():
                    items = board.get('items', []) if isinstance(board, dict) else []
                    if any(isinstance(r, dict) and (r.get('instrument_id') == symbol or any(x.get('instrument_id') == symbol for x in r.get('rows', []) if isinstance(x, dict))) for r in items):
                        result.append({'id':f'lhb:{day}:{kind}', 'date':day,'label':'已留存龙虎榜 · '+kind})
        except (OSError, ValueError, TypeError, KeyError):
            result.append({'id':'lhb-unknown','date':end,'label':'龙虎榜本地凭据未通过核验','state':'unknown'})
    return sorted(result, key=lambda r:r['date'], reverse=True)
