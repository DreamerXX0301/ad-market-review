"""原竞价候选中的题材集中度；复用已核验归属，不推断今日涨停或一字。"""
from collections import Counter
import math
import re

from fastapi import HTTPException

from app.core import connect
from app.services import auction_topic_summary as topics_service
from app.services.auction_integrity import validate_snapshot

VERSION = 'auction-resonance-v1'
TOP_LIMIT = 20
MIN_TOPIC_SAMPLES = 3
SHA = re.compile(r'[0-9a-f]{64}\Z')
SCOPE = ('仅分析本份竞价最多100只冻结候选中的题材关联样本，不代表全板块或全市场。'
         '强势样本是有效竞价涨幅排名前20只，弱市也可能含平开或低开股票；'
         '题材至少匹配3只候选才列入，不能以接近10%或昨日标签判断今日涨停、一字或可成交。')
ANCHOR_REASON = ('本份竞价没有已核验的今日涨停价及一字时序/报价证据，无法确认今日一字涨停锚股；'
                 '不能以近10%竞价涨幅、昨日涨停/一字标签或来源题材词替代，因此不生成剔除锚股后的强度结论。')


def _number(value, *, positive=False, nonnegative=False):
    try:
        if (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)
                or positive and value <= 0 or nonnegative and value < 0):
            return None
        return value
    except OverflowError:
        return None


def _mean(values):
    return math.fsum(value / len(values) for value in values) if values else None


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else ordered[middle - 1] / 2 + ordered[middle] / 2


def _base(payload):
    return {'version': VERSION, 'date': payload.get('date'), 'previous_date': payload.get('previous_date'),
        'state': 'unknown', 'scope': SCOPE, 'source_snapshot_id': None, 'source_sha256': None,
        'rules': {'top_limit': TOP_LIMIT, 'min_topic_samples': MIN_TOPIC_SAMPLES,
                  'tie_break': 'instrument_id',
                  'top_share_denominator': 'actual_strong_sample_count',
                  'top_ratio_denominator': 'topic_valid_quote_count',
                  'up_ratio_denominator': 'topic_valid_quote_count',
                  'ranking': ['top_count_desc', 'average_auction_pct_desc', 'sample_count_desc', 'kind', 'topic_code']},
        'units': {'auction_pct': 'percent', 'amount': 'CNY', 'up_ratio': 'fraction',
                  'top_share': 'fraction', 'top_ratio': 'fraction', 'concentration_lift': 'ratio'},
        'strong_sample': {'requested': 0, 'valid_count': 0, 'count': 0, 'instrument_ids': [], 'items': []},
        'groups': [], 'anchors': {'state': 'unknown', 'items': [], 'groups': [], 'reason': ANCHOR_REASON},
        'coverage': {'requested': 0, 'valid_quotes': 0, 'unknown_quotes': 0,
                     'classified': 0, 'unclassified': 0, 'eligible_groups': 0,
                     'omitted_small_groups': 0, 'rejected_groups': 0}, 'gaps': []}


def build_resonance(payload, topics=None):
    """Reuse a supplied verified summary; resolve it once only when omitted."""
    payload = payload if isinstance(payload, dict) else {}
    result = _base(payload)
    try:
        codes, _ = topics_service._samples(payload)
    except (TypeError, ValueError, AttributeError):
        result['gaps'].append('冻结候选或报价身份无法核验，未重建强势名单。')
        return result
    quotes = {row['instrument_id']: row for row in payload['quotes']['items']}
    valid = {symbol: row for symbol, row in quotes.items()
             if row.get('state') == 'observed' and _number(row.get('auction_pct')) is not None
             and _number(row.get('auction_price'), positive=True) is not None}
    strongest = sorted(valid, key=lambda symbol: (-valid[symbol]['auction_pct'], symbol))[:TOP_LIMIT]
    top_set = set(strongest)
    result['strong_sample'].update(requested=len(codes), valid_count=len(valid), count=len(strongest),
        instrument_ids=strongest, items=[{'instrument_id': symbol, 'name': valid[symbol].get('name') or symbol,
            'auction_price': valid[symbol]['auction_price'], 'auction_pct': valid[symbol]['auction_pct'],
            'auction_amount': _number(valid[symbol].get('auction_amount'), nonnegative=True)} for symbol in strongest])
    result['coverage'].update(requested=len(codes), valid_quotes=len(valid), unknown_quotes=len(codes) - len(valid))
    if topics is None:
        topics = topics_service.build_topic_summary(payload)
    try:
        generation, previous, _ = topics_service._source(payload)
    except (TypeError, ValueError, AttributeError, OverflowError):
        result['gaps'].append('本份竞价没有明确可核验的原题材代际，不用当前归属补齐。')
        return result
    if (not isinstance(topics, dict) or topics.get('version') != topics_service.VERSION
            or topics.get('date') != payload.get('date') or topics.get('source_snapshot_id') != generation
            or not isinstance(topics.get('source_sha256'), str) or not SHA.fullmatch(topics['source_sha256'])
            or not isinstance(topics.get('groups'), list) or len(topics['groups']) > 1500):
        result['gaps'].append('原题材归属未通过日期、代际和SHA身份核验，集中度保持未知。')
        return result
    result.update(source_snapshot_id=generation, source_sha256=topics['source_sha256'])
    identities = Counter((row.get('source'), row.get('kind'), row.get('topic_code'))
        for row in topics['groups'] if isinstance(row, dict)
        and all(isinstance(row.get(key), str) for key in ('source', 'kind', 'topic_code')))
    classified, omitted, rejected = set(), 0, 0
    candidate_set = set(codes)
    for group in topics['groups']:
        if not isinstance(group, dict):
            rejected += 1
            continue
        code, kind, observed = group.get('topic_code'), group.get('kind'), group.get('members_observed_date')
        symbols = group.get('instrument_ids')
        if (group.get('source') != 'fuyao' or kind not in ('industry', 'concept')
                or not isinstance(code, str) or not topics_service.SECTOR.fullmatch(code)
                or not topics_service._day(observed) or observed > previous
                or identities.get(('fuyao', kind, code)) != 1
                or group.get('source_snapshot_id') != generation
                or group.get('source_sha256') != topics['source_sha256']
                or not isinstance(symbols, list) or len(symbols) > 100
                or any(not isinstance(symbol, str) or symbol not in candidate_set for symbol in symbols)
                or len(set(symbols)) != len(symbols) or group.get('sample_count') != len(symbols)):
            rejected += 1
            continue
        classified.update(symbols)
        if len(symbols) < MIN_TOPIC_SAMPLES:
            omitted += 1
            continue
        selected = [symbol for symbol in codes if symbol in set(symbols)]
        observed_rows = [valid[symbol] for symbol in selected if symbol in valid]
        changes = [row['auction_pct'] for row in observed_rows]
        amounts = [_number(row.get('auction_amount'), nonnegative=True) for row in observed_rows]
        amounts = [value for value in amounts if value is not None]
        try:
            amount = math.fsum(amounts) if amounts else None
        except OverflowError:
            amount = None
        top_members = [symbol for symbol in strongest if symbol in symbols]
        up = sum(change > 0 for change in changes) if changes else None
        average, median = _mean(changes), _median(changes)
        top_share = len(top_members) / len(top_set) if top_set else None
        lift = top_share / (len(changes) / len(valid)) if changes and valid and top_share is not None else None
        if lift is not None and not math.isfinite(lift):
            lift = None
        complete = group.get('members_complete') is True
        result['groups'].append({key: group.get(key) for key in
            ('topic_code', 'name', 'kind', 'source', 'members_observed_date', 'source_snapshot_id', 'source_sha256')} | {
            'sample_count': len(selected), 'valid_count': len(changes), 'missing_count': len(selected) - len(changes),
            'up_count': up, 'up_ratio': up / len(changes) if changes else None,
            'average_auction_pct': average, 'median_auction_pct': median,
            'average': average, 'median': median, 'amount': amount, 'amount_valid': len(amounts),
            'top_count': len(top_members), 'top_share': top_share, 'concentration_lift': lift,
            'top_ratio': len(top_members) / len(changes) if changes else None,
            'top_instrument_ids': top_members, 'instrument_ids': selected, 'members_complete': complete,
            'state': 'observed_sample' if complete and len(changes) == len(selected) else 'partial'})
    result['groups'].sort(key=lambda row: (-row['top_count'], row['average_auction_pct'] is None,
        -(row['average_auction_pct'] or 0), -row['sample_count'], row['kind'], row['topic_code']))
    result['coverage'].update(classified=len(classified), unclassified=len(codes) - len(classified),
        eligible_groups=len(result['groups']), omitted_small_groups=omitted, rejected_groups=rejected)
    result['state'] = ('observed_sample' if topics.get('state') == 'observed_sample' and not rejected
                       and all(row['state'] == 'observed_sample' for row in result['groups']) and result['groups']
                       else 'partial' if result['groups'] else 'unknown')
    if topics.get('state') != 'observed_sample':
        result['gaps'].append('原题材或报价覆盖存在缺口，仅描述已确认候选关联样本，不代表完整方向。')
    if not result['groups']:
        result['gaps'].append('没有取得至少3只冻结候选的可核验同题材样本，不推断集中方向。')
    if rejected:
        result['gaps'].append(f'{rejected}组派生归属与原代际身份无法对账，未纳入集中度。')
    return result


def read_view(day):
    if not topics_service._day(day):
        raise HTTPException(422, '竞价共振日期须为YYYY-MM-DD')
    with connect() as connection:
        row = connection.execute('SELECT * FROM market_auction_snapshot WHERE trade_date=?', (day,)).fetchone()
    if row is None:
        result = _base({'date': day})
        result['gaps'].append('所选日尚无已留存竞价，不重建竞价或题材样本。')
        return result
    try:
        payload = validate_snapshot(dict(row))
    except ValueError:
        raise HTTPException(409, '原竞价正文或来源原件未通过SHA核验，未派生集中度') from None
    topics = topics_service.build_topic_summary(payload)
    return build_resonance(payload, topics=topics)
