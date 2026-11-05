"""Explain yesterday's frozen candidates at auction, without reading later prices.

Callers verify the original auction row and supply its original, verified topic
summary. This module performs no IO and never treats a price reference as a
confirmed limit, a promotion, a one-price session or an executable order.
"""
from collections import Counter
from datetime import date, datetime
from statistics import fmean
import re

from app.services.auction_feedback import _events, _opening_reference
from app.services.noon_analysis import number
from app.services.noon_collection import CHINA

_SYMBOL = re.compile(r'\d{6}\.(?:SH|SZ|BJ)\Z')
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_BROAD = {'融资融券', '深股通', '沪股通', '转融券标的', '富时罗素概念', 'MSCI概念',
          '标普道琼斯A股', '国企改革', '央企国企改革', '同花顺漂亮100'}
_SCOPE = '仅原竞价冻结候选与严格前日事件交集；高低开不代表全天结果，常规涨停参考价不等于已确认封板。'


def _valid_day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _timely(payload):
    review = payload.get('previous_review', {})
    try:
        start, end = (datetime.fromisoformat(payload[key]) for key in ('collected_start_at', 'collected_end_at'))
        known = review.get('known_at_ms')
        recorded = review.get('recorded_at_ms')
        return (start.tzinfo is not None and end.tzinfo is not None and start <= end
                and start.astimezone(CHINA).date().isoformat() == end.astimezone(CHINA).date().isoformat() == payload['date']
                and type(known) is int and known <= int(start.timestamp() * 1000)
                and (recorded is None or type(recorded) is int and recorded <= known))
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def _stats(rows):
    valid = [row for row in rows if number(row.get('change_pct')) is not None]
    checked = [row for row in valid if type(row.get('reference_matched')) is bool]
    return {'sample_count': len(rows), 'valid_count': len(valid), 'unknown_count': len(rows) - len(valid),
            'high_open': sum(row['change_pct'] > 0 for row in valid),
            'low_open': sum(row['change_pct'] < 0 for row in valid),
            'flat_open': sum(row['change_pct'] == 0 for row in valid),
            'average_auction_pct': round(fmean(row['change_pct'] for row in valid), 4) if valid else None,
            'reference_checked_count': len(checked),
            'reference_count': sum(row['reference_matched'] for row in checked) if checked else None}


def _samples(rows):
    valid = [row for row in rows if number(row.get('change_pct')) is not None]
    valid.sort(key=lambda row: (-(row.get('previous_board_count') or 0), -row['change_pct'], row['instrument_id']))
    if not valid:
        return []
    chosen = valid[:1]
    opposite = [row for row in valid[1:] if row['change_pct'] * valid[0]['change_pct'] < 0]
    if opposite or len(valid) > 1:
        chosen.append((opposite or valid[1:])[0])
    return chosen


def auction_carryover(payload, topics):
    """Return bounded factual counts from an already verified original payload."""
    payload = payload if isinstance(payload, dict) else {}
    day, previous = payload.get('date'), payload.get('previous_date')
    unknown = {'state': 'unknown', 'previous_date': previous, 'scope': _SCOPE,
               'reason': '未留存可与本次竞价严格对照的前日候选资料。', 'directions': []}
    review = payload.get('previous_review')
    if (not _valid_day(day) or not _valid_day(previous) or previous >= day
            or not isinstance(review, dict) or review.get('date') != previous or not _timely(payload)):
        return unknown
    universe = payload.get('universe', {}).get('items', [])
    if not isinstance(universe, list) or not 1 <= len(universe) <= 100:
        return unknown
    codes = [row.get('instrument_id') for row in universe if isinstance(row, dict)]
    if len(codes) != len(universe) or len(set(codes)) != len(codes) or any(
            not isinstance(code, str) or not _SYMBOL.fullmatch(code) for code in codes):
        return unknown
    quotes = payload.get('quotes', {}).get('items', [])
    counts = Counter(row.get('instrument_id') for row in quotes if isinstance(row, dict))
    quotes = {row['instrument_id']: row for row in quotes if isinstance(row, dict)
              and counts[row.get('instrument_id')] == 1 and row.get('instrument_id') in codes}
    events, _ = _events(payload, previous, [])
    rows = []
    for code in codes:
        event = events.get(code)
        if not event or event[0] != 'limit_up':
            continue
        prior, quote = event[1], quotes.get(code, {})
        price, before, pct = (number(quote.get(key)) for key in ('auction_price', 'pre_close_price', 'auction_pct'))
        prior_close = number(prior.get('previous_close'))
        valid = (quote.get('state') == 'observed' and price is not None and price > 0
                 and before is not None and before > 0 and pct is not None
                 and abs((price / before - 1) * 100 - pct) <= .06
                 and prior_close is not None and abs(prior_close - before) <= .011)
        height = prior.get('previous_board_count')
        reference = _opening_reference(quote, code) if valid else None
        rows.append({'instrument_id': code, 'name': prior.get('name') or quote.get('name') or code,
                     'previous_board_count': height if type(height) is int and height > 0 else None,
                     'change_pct': pct if valid else None, 'auction_price': price if valid else None,
                     'reference_matched': bool(reference and reference['matched']) if reference else None,
                     'reference_price': reference['reference_price'] if reference and reference['matched'] else None})
    if not rows:
        return unknown
    result = {'state': 'observed_sample', 'previous_date': previous, 'scope': _SCOPE,
              'prior_limit_up': _stats(rows), 'directions': []}
    heights = [row['previous_board_count'] for row in rows if row['previous_board_count'] is not None]
    if heights:
        top = max(heights)
        leaders = [row for row in rows if row['previous_board_count'] == top]
        result['highest_candidates'] = {'previous_board_count': top, **_stats(leaders), 'stocks': _samples(leaders)}
    reference_rows = [row for row in rows if row['reference_matched'] is True]
    checked = sum(type(row['reference_matched']) is bool for row in rows)
    result['price_reference'] = {'state': 'observed' if checked == len(rows) else 'partial' if checked else 'unknown',
                                 'count': len(reference_rows) if checked else None, 'checked_count': checked,
                                 'unknown_count': len(rows) - checked,
                                 'stocks': _samples(reference_rows),
                                 'confirmed_limit_price': False}
    # Topic metadata has already passed the original generation/member SHA reader.
    # Never accept a current, missing-SHA or post-auction name-based membership.
    market_context = payload.get('market_context')
    generation = market_context.get('source_snapshot_id') if isinstance(market_context, dict) else None
    if (not isinstance(topics, dict) or topics.get('date') != day
            or topics.get('source_snapshot_id') != generation
            or not _SHA.fullmatch(str(topics.get('source_sha256')))):
        return result
    indexed = {row['instrument_id']: row for row in rows}
    groups = topics.get('groups', [])
    identities = Counter((row.get('topic_code'), row.get('kind')) for row in groups if isinstance(row, dict))
    directions = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        code, kind, observed = group.get('topic_code'), group.get('kind'), group.get('members_observed_date')
        members = group.get('instrument_ids')
        if (group.get('source') != 'fuyao' or kind not in ('industry', 'concept')
                or identities[code, kind] != 1 or not group.get('name') or group['name'] in _BROAD
                or group.get('source_snapshot_id') != generation
                or group.get('source_sha256') != topics['source_sha256']
                or not _valid_day(observed) or observed > previous
                or not isinstance(members, list) or len(members) > 100
                or any(not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol) for symbol in members)
                or len(set(members)) != len(members)):
            continue
        matched = [indexed[symbol] for symbol in members if symbol in indexed]
        if len(matched) < 2:
            continue
        directions.append({'sector_id': code, 'sector_name': group['name'], 'kind': kind,
                           'member_date': observed, **_stats(matched), 'stocks': _samples(matched)})
    directions.sort(key=lambda row: (row['kind'] != 'industry', -row['sample_count'],
                                    -(row['average_auction_pct'] or 0), row['sector_id']))
    result['directions'] = directions[:2]
    result['member_source_binding'] = {'snapshot_id': generation, 'content_sha256': topics['source_sha256']}
    return result


def _opening_text(stats):
    text = f'高开{stats["high_open"]}只、低开{stats["low_open"]}只、平开{stats["flat_open"]}只'
    if stats['unknown_count']:
        text += f'，另{stats["unknown_count"]}只竞价报价或前收对照未核验'
    return text


def _stock_text(rows):
    texts = []
    for row in rows[:2]:
        height = row.get('previous_board_count')
        text = row['name'] + (f'（前日{height}板）' if height else '') + f' {row["change_pct"]:+.2f}%'
        if row.get('reference_matched'):
            text += '，竞价价触及常规涨停参考'
        texts.append(text)
    return '；代表样本：' + '、'.join(texts) if texts else ''


def auction_highlights(context):
    """At most four short paragraphs; every linked example is an original candidate."""
    if context.get('state') != 'observed_sample':
        return []
    result = []
    def add(key, text, facts, stocks, **extra):
        result.append({'id': 'auction.carryover.' + key, 'kind': 'auction', 'text': text,
                       'stocks': stocks, 'stock_ids': [row['instrument_id'] for row in stocks],
                       'carryover_facts': facts, **extra})
    if top := context.get('highest_candidates'):
        text = f'前日已核验候选最高{top["previous_board_count"]}板，共{top["sample_count"]}只；本次竞价' + _opening_text(top)
        text += _stock_text(top['stocks']) + '。这是前日高度股的竞价反馈，不能据此认定今日晋级或全天一字。'
        add('height', text, {key: value for key, value in top.items() if key != 'stocks'}, top['stocks'])
    for group in context['directions']:
        text = f'前日涨停候选中，{group["sector_name"]}方向有{group["sample_count"]}只；本次竞价' + _opening_text(group)
        if group['average_auction_pct'] is not None:
            text += f'，有效样本平均{group["average_auction_pct"]:+.2f}%'
        text += _stock_text(group['stocks']) + '。仅反映这批前日强势候选的延续或分歧，不代表全板块或昨日主线排名。'
        add('direction.' + group['sector_id'], text, {key: value for key, value in group.items() if key != 'stocks'},
            group['stocks'], sector_id=group['sector_id'], sector_name=group['sector_name'])
    reference = context['price_reference']
    if reference['count']:
        text = f'前日涨停候选中，本次已核验到{reference["count"]}只竞价价触及常规涨停参考价'
        text += _stock_text(reference['stocks']) + '。这只是价格参考，封板与开盘承接须看09:35及其后已留存的盘中观察。'
        add('reference', text, {key: value for key, value in reference.items() if key != 'stocks'}, reference['stocks'])
    return result
