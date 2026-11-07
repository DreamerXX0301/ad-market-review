"""Bounded cumulative briefs derived from verified same-day market records."""
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import json
import logging
import math
import re
import threading

import httpx
from fastapi import HTTPException

from app.core import connect, dumps, get_market, now_ms
from app.services import auction_followup
from app.services.noon_collection import CHINA

LOGGER = logging.getLogger('shixu.worker')
MODEL = 'qwen3.5:latest'
MODEL_URL = 'http://127.0.0.1:11434/api/chat'
TIMELINE = (
    ('auction', '09:25 竞价'), ('0935', '09:35 开盘观察'),
    ('0945', '09:45 早盘跟踪'), ('1000', '10:00 开盘首轮'),
    ('1030', '10:30 早盘一小时'), ('1100', '11:00 上午观察'),
    ('noon', '11:30 午评'), ('1330', '13:30 午后首轮'),
    ('1400', '14:00 午后观察'), ('1430', '14:30 尾盘观察'),
    ('close', '15:00 收盘复盘'),
)
LABELS = dict(TIMELINE)
ORDER = {key: index for index, (key, _) in enumerate(TIMELINE)}
INDEX_IDS = ('000001.SH', '399001.SZ', '399006.SZ', '000688.SH')
MAX_BRIEF_BYTES = 384 * 1024
MAX_NOON_BYTES = 8 * 1024 * 1024
MAX_NOON_POOL_ROWS = 2000  # Noon collection keeps at most ten 200-row pages per source pool.
BRIEF_VERSION = 1
LENSES = {
    'market': '市场广度与成交',
    'ladder': '封板与炸板',
    'sectors': '方向与题材表现',
    'close': '全天收盘回看',
}


def _stamp(value, day):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.astimezone(CHINA).date().isoformat() != day:
        raise ValueError('盘中简报来源日期无效')
    return int(parsed.timestamp() * 1000)


def _digest(value):
    return sha256(dumps(value).encode()).hexdigest()


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _known_count(value):
    return value if type(value) is int and value >= 0 else None


def _index_rows(rows):
    return [{k: row[k] for k in ('instrument_id', 'name', 'change_pct', 'latest_price') if k in row}
            | {'amount': row['amount'] if row.get('amount_unit', row.get('currency')) == 'CNY'
               and _number(row.get('amount')) and row['amount'] >= 0 else None}
            for row in rows if row.get('instrument_id') in INDEX_IDS and _number(row.get('change_pct'))]


def _anchor_amount_comparison(current, previous, previous_date, label, count=None):
    return {'previous_date': previous_date, 'previous_amount': previous,
            'comparison_amount': current, 'change_from_previous': current - previous,
            'comparable_count': count, 'label': label}


def _pool_count(value, state):
    count = _known_count(value)
    if count is None or state not in ('observed', 'partial'):
        return None
    return f'{count:,} 只' if state == 'observed' else f'至少 {count:,} 只'


def sector_comparison_metadata(row, *, source):
    """Comparable raw index facts from an already verified provider response."""
    code = row.get('topic_code') or row.get('instrument_id')
    price, previous = row.get('latest_price', row.get('last_price')), row.get('prev_close', row.get('prev_price'))
    change = row.get('change_pct', row.get('price_change_ratio_pct'))
    if (source != 'fuyao' or not isinstance(code, str) or not re.fullmatch(r'\d{6}\.TI', code)
            or row.get('kind') not in ('industry', 'concept')
            or row.get('price_basis', 'none') != 'none' or row.get('currency', 'CNY') != 'CNY'
            or not _number(price) or price <= 0 or not _number(previous) or previous <= 0
            or not _number(change) or abs((price / previous - 1) * 100 - change) > .001):
        return {}
    return {'quote_source': source, 'quote_basis': 'provider_index',
            'latest_price': price, 'prev_close': previous, 'price_basis': 'none', 'currency': 'CNY'}


def sector_membership_fingerprint(generation, reference_date, code, kind, member_date, symbols):
    return _digest({'source': 'fuyao', 'generation': generation, 'reference_date': reference_date,
                    'date': member_date, 'code': code, 'kind': kind, 'instrument_ids': sorted(set(symbols))})


def _noon_member_metadata(payload, sector, quotes, *, read_context=None):
    from app.services import noon_membership_context as membership
    from app.services.noon_sector_views import _members

    if not isinstance(payload.get('sector_memberships'), dict):
        return {}
    generation = membership._source_id(payload)
    if generation is None:
        return {}
    if read_context is not None:
        from app.services.intraday_rotation import noon_member_proof
        proof, _, error = noon_member_proof(payload, sector, read_context, direct=True)
    else:
        proof, _, error = _members(payload, sector, quotes)
    sealed, pool_complete = membership._limit_pool(payload, quotes)
    if (error or not proof or not proof['complete'] or not pool_complete
            or sector.get('member_limit_up_state') != 'observed'
            or type(sector.get('member_limit_up_count')) is not int
            or sector['member_limit_up_count'] != len(set(proof['members']) & sealed)):
        return {}
    code = sector.get('topic_code') or sector.get('instrument_id')
    observed = proof['topic']['observed_date']
    return {'member_reference_sha256': sector_membership_fingerprint(
                generation, payload['previous_review']['date'], code, sector['kind'], observed, proof['members']),
            'member_count': len(proof['members']), 'member_date': observed, 'member_list_complete': True}


def _sector(row, stocks=(), *, quote_source=None):
    return {
        'instrument_id': row.get('topic_code') or row.get('instrument_id'),
        'name': row['name'], 'kind': row.get('kind', 'concept'),
        'change_pct': row.get('change_pct'), 'amount': row.get('amount'),
        'limit_up_count': row.get('limit_up_count'),
        'limit_up_count_state': row.get('limit_up_count_state', 'unknown'),
        'stocks': list(stocks)[:6],
        **sector_comparison_metadata(row, source=quote_source),
    }


def _stock(row, *, change=None, board_count=None, state=None):
    return {'instrument_id': row['instrument_id'], 'name': row['name'],
            'change_pct': change if change is not None else row.get('change_pct'),
            'board_count': board_count, 'state': state}


def _anchor(row, slot, *, market, scope, ladder=None, sectors=()):
    return {
        'slot': slot, 'id': row['snapshot_id'], 'date': row['trade_date'],
        'collected_start_at': row['collected_start_at'],
        'collected_end_at': row['collected_end_at'],
        'quality': row['quality'], 'market': market, 'market_pulse': market,
        'scope': scope, 'ladder': ladder or {}, 'sectors': list(sectors),
    }


def _auction_anchor(row):
    from app.services.auction_integrity import validate_snapshot
    from app.services.auction_topic_summary import build_topic_summary
    payload = validate_snapshot(row)
    quotes = payload['quotes']['summary']
    scope = '竞价仅覆盖冻结关注候选'
    market = {
        'indices': [], 'breadth': {**quotes, 'observed': quotes['requested'], 'scope': scope},
        'turnover': {'amount': quotes.get('amount'), 'valid': quotes.get('amount_valid')},
        'median_change_pct': quotes.get('median_pct'),
    }
    candidates = {item['instrument_id']: item for item in payload['quotes']['items']}
    groups = [item for item in build_topic_summary(payload)['groups']
              if item['valid_count'] >= 3 and _number(item.get('average_auction_pct'))]
    groups.sort(key=lambda item: (-item['average_auction_pct'], -item['valid_count'], item['topic_code']))
    sectors = []
    for group in groups[:16]:
        members = [candidates[symbol] for symbol in group['instrument_ids'] if symbol in candidates
                   and _number(candidates[symbol].get('auction_pct'))]
        members.sort(key=lambda item: (-item['auction_pct'], item['instrument_id']))
        sectors.append(_sector({**group, 'change_pct': group['average_auction_pct']},
                               [_stock(item, change=item['auction_pct']) for item in members[:6]]))
    return _anchor(row, 'auction', market=market, scope=scope, sectors=sectors)


def _verified_noon_payload(row, *, read_context=None):
    from app.services.intraday_board_changes import _record_payload

    try:
        payload = _record_payload(row, MAX_NOON_BYTES, read_context)
    except json.JSONDecodeError:
        raise
    except ValueError:
        raise ValueError('午评快照正文校验失败')
    if (payload.get('date'), payload.get('version'), payload.get('collected_start_at'), payload.get('collected_end_at')) != (
        row['trade_date'], 'noon-snapshot-v1', row['collected_start_at'], row['collected_end_at'],
    ):
        raise ValueError('午评快照身份校验失败')
    return payload


def _noon_indices(payload):
    from app.services.noon_comparison import build_index_comparisons

    indices = _index_rows(payload['indices'])
    comparisons = build_index_comparisons(payload)
    for index in indices:
        comparison = comparisons.get(index['instrument_id'], {})
        if comparison.get('state') in ('snapshot_comparison', 'matched_period'):
            index['amount_comparison'] = _anchor_amount_comparison(
                comparison['current_amount'], comparison['previous_amount'],
                payload['calendar']['previous_day'], comparison['label'], count=1)
    return indices


def _noon_anchor(row):
    from app.services.noon_service import retained_empty_event_pools

    payload = _verified_noon_payload(row)
    summary = payload['summary']
    events = {**payload['events'], **retained_empty_event_pools(row, payload)}
    up, broken = events.get('limit_up', {}), events.get('limit_break', {})
    heights = up.get('board_summary', events.get('board_summary', {}))
    ladder = {
        'state': up.get('state'), 'sealed_count': up.get('count'),
        'highest_board': heights.get('max_confirmed'),
        'first_board_count': heights.get('first_board_count'),
        'multi_board_count': heights.get('multi_board_count'),
        'broken_board': {'state': broken.get('state'), 'count': broken.get('count')},
    }
    market = {
        'indices': _noon_indices(payload),
        'breadth': {**summary['breadth'], 'observed': summary['breadth']['total']},
        'turnover': {'amount': summary['amount'].get('value'), 'valid': summary['amount'].get('valid')},
    }
    stocks = {value['instrument_id']: value for value in payload['stocks']}
    sealed = {value['instrument_id']: value for value in up.get('items', [])}
    broken_ids = {value['instrument_id'] for value in broken.get('items', [])}
    memberships = payload.get('sector_memberships', {}).get('items', {})
    sectors = []
    for sector in payload['sectors']:
        if not _number(sector.get('change_pct')):
            continue
        proof = memberships.get(sector['topic_code'], {})
        proof_body = {k: v for k, v in proof.items() if k != 'sha256'}
        members = []
        if proof and _digest(proof_body) == proof.get('sha256') and proof.get('source') == 'fuyao':
            members = [stocks[symbol] for symbol in proof['instrument_ids']
                       if symbol in stocks and _number(stocks[symbol].get('change_pct'))]
            members.sort(key=lambda value: (-value['change_pct'], value['instrument_id']))
        sector_stocks = [_stock(value, board_count=sealed.get(value['instrument_id'], {}).get('continue_day_cnt'),
                                state='sealed' if value['instrument_id'] in sealed else
                                'broken' if value['instrument_id'] in broken_ids else None) for value in members[:6]]
        sectors.append({**_sector({**sector, 'limit_up_count': sector.get('member_limit_up_count'),
                                   'limit_up_count_state': sector.get('member_limit_up_state')}, sector_stocks,
                                  quote_source='fuyao'),
                        **_noon_member_metadata(payload, sector, stocks)})
    return _anchor(row, 'noon', market=market, ladder=ladder, sectors=sectors[:24],
                   scope='午评留存范围，逐股报价时刻可能未知')


def _compact_live(snapshot):
    # The full verified live detail remains in the existing snapshots API.
    sectors = [_sector(row, quote_source='fuyao') for row in snapshot.get('sectors', []) if _number(row.get('change_pct'))]
    return {
        'slot': snapshot['slot'], 'id': snapshot['id'], 'quality': snapshot['quality'], 'version': snapshot['version'],
        'collected_end_at': snapshot['collected_end_at'],
        'market': snapshot.get('market_pulse', {}), 'ladder': snapshot.get('ladder', {}),
        'sectors': sectors, 'scope': '同一采集窗口内已核验报价',
        'interval_amount': snapshot.get('summary', {}).get('interval_amount') or {},
        'sector_index': {row['instrument_id']: row['change_pct']
                         for row in snapshot.get('comparison_input', {}).get('sectors', [])
                         if _number(row.get('change_pct'))},
    }


def _first_seals(rows):
    ranges = (
        ('09:25', '09:30'), ('09:30', '10:00'), ('10:00', '10:30'), ('10:30', '11:00'),
        ('11:00', '11:30'), ('13:00', '13:30'), ('13:30', '14:00'), ('14:00', '14:30'), ('14:30', '15:00'),
    )
    stamps = []
    for row in rows:
        value = row.get('first_limit_time')
        if isinstance(value, str) and re.fullmatch(r'[0-2][0-9]:[0-5][0-9](:[0-5][0-9])?', value):
            stamps.append(value if len(value) == 8 else value + ':00')
    if not stamps:
        return None
    buckets = []
    for start, end in ranges:
        count = sum(start + ':00' <= value < end + ':00' or (
            end in ('11:30', '15:00') and value == end + ':00'
        ) for value in stamps)
        buckets.append({'label': f'{start}–{end}', 'count': count})
    valid = sum(item['count'] for item in buckets)
    if not valid:
        return None
    return {'title': '盘后回溯 · 首次封板', 'scope': f'收盘梯队 {len(rows)} 只样本，{valid} 只留有可用的首次封板时间',
            'events': buckets}


def _close_anchor(day, record):
    from app.services import market_service
    from app.services.noon_close_service import read_view
    generation = record['generation']
    if get_market().status().get('snapshot_id') != generation:
        raise ValueError('收盘正式行情代际已变化')
    overview = market_service.overview(day)
    sectors_view = market_service.market_page('sectors', day, 1000)
    ladder_view = market_service.market_page('ladder', day, 300)
    if any(view.get('date') != day or view.get('snapshot_id') != generation
           for view in (overview, sectors_view, ladder_view)):
        raise ValueError('收盘事实与正式发布身份不符')
    if get_market().status().get('snapshot_id') != generation:
        raise ValueError('生成收盘事实时正式行情代际已变化')
    breadth = overview['breadth']
    confirmed_ladder = ladder_view.get('items', [])
    board_coverage = ladder_view.get('main_coverage', {})
    broken_verified = overview.get('broken_board_analysis', {}).get('current') or {}
    day_sealed = _known_count(board_coverage.get('confirmed_count'))
    day_broken = _known_count(broken_verified.get('observed_broken_count'))
    height = max((_known_count(item.get('board_count')) or 0 for item in confirmed_ladder), default=0)
    ladder = {
        'state': 'observed' if board_coverage.get('complete') else 'partial' if day_sealed else 'unknown',
        'sealed_count': day_sealed, 'highest_board': height or None,
        'broken_board': {'state': broken_verified.get('state', 'unknown'), 'count': day_broken},
    }
    closing_indices = [row for row in overview.get('indices', []) if row.get('date') == day]
    market = {
        'indices': _index_rows([{**row, 'latest_price': row.get('close')} for row in closing_indices]),
        'breadth': {**breadth, 'valid': sum(breadth[k] for k in ('up', 'down', 'flat')),
                    'observed': breadth.get('expected_current_universe') or breadth['total']},
        'turnover': {'amount': overview.get('total_amount'), 'valid': breadth['total']},
    }
    if _number(overview.get('amount_change')):
        market['turnover']['previous_day_delta'] = overview['amount_change']
        market['turnover']['amount_comparison'] = _anchor_amount_comparison(
            overview['total_amount'], overview['total_amount'] - overview['amount_change'],
            overview['amount_previous_date'], '较前一交易日收盘')
    projected_indices = {row['instrument_id']: row for row in market['indices']}
    for row in closing_indices:
        index = projected_indices.get(row['instrument_id'])
        if index and _number(index['amount']) and _number(row.get('amount_previous')):
            index['amount_comparison'] = _anchor_amount_comparison(
                index['amount'], row['amount_previous'], row['amount_previous_date'], '较前一交易日收盘', count=1)
    sectors = []
    for row in sectors_view['items']:
        if (row.get('quote_source') == 'fuyao' and row.get('quote_basis') == 'provider_index'
                and row.get('quote_date') == day and _number(row.get('change_pct'))):
            sectors.append(_sector({**row, 'limit_up_count': None}, quote_source='fuyao'))
    close_review = read_view(day).get('review')
    if close_review and close_review.get('source_snapshot_id') == generation:
        market['noon_to_close'] = {
            'breadth': close_review['summary']['breadth'].get('common'),
            'turnover': close_review['summary'].get('amount'),
        }
        # Dated frozen classification groups stay bounded to the same-day review.
        from app.services.noon_close_service import read_items
        member_groups = close_review.get('classification_counts', {})
        by_code = {entry['topic_code']: entry for key in ('industries', 'topics', 'concepts')
                   for entry in member_groups.get(key, []) if entry.get('topic_code')}
        for sector in sectors[:8]:
            group = by_code.get(sector['instrument_id'])
            if group and group.get('key'):
                count = _known_count(group.get('close_limit_up_count'))
                sector.update(limit_up_count=count, limit_up_count_state='partial' if count else 'unknown')
                values = read_items(day, review_id=close_review['review_id'], section='all',
                                    classification=group['key'], sort='close_change_pct', limit=6)['items']
                sector['stocks'] = [_stock(item, change=item.get('close_change_pct'),
                                            board_count=item.get('final_board_count'),
                                            state={'limit_up': 'sealed', 'limit_break': 'broken'}.get(item.get('close_state')))
                                    for item in values]
    when = datetime.combine(date.fromisoformat(day), time(15), CHINA).isoformat()
    row = {'snapshot_id': generation, 'trade_date': day, 'collected_start_at': when,
           'collected_end_at': when, 'quality': overview['quality']}
    anchor = _anchor(row, 'close', market=market, ladder=ladder, sectors=sectors[:24],
                     scope='正式收盘行情与盘后核验。盘中快照各自保留实际采集时间。')
    if record.get('time'):
        anchor['published_at'] = datetime.fromtimestamp(record['time'] / 1000, CHINA).isoformat()
    if events := _first_seals(confirmed_ladder):
        anchor['retrospective'] = events
    return anchor


def _metadata(connection, day, now):
    output = {}
    for slot, table in (('auction', 'market_auction_snapshot'), ('noon', 'market_noon_snapshot')):
        # No full snapshot body is read by the idle poll.
        row = connection.execute(f'''SELECT snapshot_id,content_sha256,collected_end_at,recorded_at_ms
            FROM {table} WHERE trade_date=?''', (day,)).fetchone()
        if row:
            observed = _stamp(row['collected_end_at'], day)
            output[slot] = {'id': row['snapshot_id'], 'sha': row['content_sha256'], 'time': observed,
                            'ready': max(observed, row['recorded_at_ms'])}
    if 'auction' in output and (discovery := auction_followup.discovery_metadata(connection, day)):
        if (discovery['primary_snapshot_id'] == output['auction']['id']
                and discovery['recorded_at_ms'] <= now):
            output['auction']['discovery'] = discovery
    live_rows = connection.execute('''SELECT c.slot,c.snapshot_id,c.content_sha256,
            json_extract(c.payload_json, '$.collected_end_at') AS collected_end_at,j.finished_at_ms
        FROM market_intraday_capture c JOIN job j USING(job_id)
        WHERE c.trade_date=? AND c.snapshot_id IS NOT NULL AND j.status IN ('partial','succeeded')''', (day,))
    for row in live_rows:
        if row['slot'] not in ORDER or not row['finished_at_ms']:
            continue
        observed = _stamp(row['collected_end_at'], day)
        output[row['slot']] = {'id': row['snapshot_id'], 'sha': row['content_sha256'], 'time': observed,
                               'ready': max(observed, row['finished_at_ms'])}
    close_floor = int(datetime.combine(date.fromisoformat(day), time(15), CHINA).timestamp() * 1000)
    if now >= close_floor:
        head = connection.execute("""SELECT s.snapshot_id,s.lineage_json,s.published_at_ms
          FROM published_head h JOIN dataset_snapshot s USING(snapshot_id)
          WHERE h.module='daily_raw' AND s.state='published' AND s.data_end=?""", (day,)).fetchone()
        if head and close_floor <= head['published_at_ms'] <= now:
            generation = json.loads(head['lineage_json']).get('market_snapshot_id')
            if generation:
                review = connection.execute('''SELECT review_id,result_sha256 FROM market_noon_close_review
                    WHERE trade_date=? AND source_snapshot_id=? ORDER BY revision DESC LIMIT 1''',
                    (day, generation)).fetchone()
                output['close'] = {'id': head['snapshot_id'], 'generation': generation,
                                   'sha': _digest([generation, list(review) if review else None]),
                                   'time': head['published_at_ms'], 'ready': head['published_at_ms']}
    return {slot: row for slot, row in output.items() if row['ready'] <= now}


def _preceding(records, slot):
    cutoff = records[slot]['time']
    result = []
    for key, _ in TIMELINE:
        row = records.get(key)
        if not row or (key != slot and (ORDER[key] >= ORDER[slot] or row['ready'] > cutoff)):
            continue
        if key == 'auction' and key != slot and (discovery := row.get('discovery')):
            if discovery['recorded_at_ms'] > cutoff:
                row = {k: v for k, v in row.items() if k != 'discovery'}
        result.append((key, row))
    return result


def _read_projection(slot, day, record):
    if slot == 'close':
        return _close_anchor(day, record)
    tables = {'auction': 'market_auction_snapshot', 'noon': 'market_noon_snapshot'}
    table = tables.get(slot, 'market_intraday_capture')
    with connect() as connection:
        row = connection.execute(f'''SELECT * FROM {table}
            WHERE trade_date=? AND snapshot_id=? AND content_sha256=?''',
            (day, record['id'], record['sha'])).fetchone()
    if not row:
        raise ValueError('盘中简报来源已变化')
    if slot == 'auction':
        anchor = _auction_anchor(row)
        if identity := record.get('discovery'):
            from app.services.auction_discovery import read_view
            try:
                discovery = read_view(day)
                if (discovery.get('state') in ('available', 'partial') and discovery.get('stocks')
                        and auction_followup.model_input_allowed(discovery)
                        and all(discovery.get(key) == val for key, val in identity.items())):
                    anchor['auction_discovery'] = discovery
            except (ValueError, KeyError, TypeError, OSError, HTTPException) as error:
                LOGGER.warning('竞价发现来源核验未通过 %s', type(error).__name__)
        return anchor
    if slot == 'noon':
        return _noon_anchor(row)
    from app.services.intraday_service import decode_snapshot
    value = decode_snapshot(row)
    if value['slot'] != slot or _stamp(value['collected_end_at'], day) != record['time']:
        raise ValueError('盘中简报时点身份不符')
    projection = _compact_live(value)
    if slot == '1000' and auction_followup.model_input_allowed(value):
        projection['opening_quotes'] = value
    return projection


def _evidence(projections):
    from app.services.intraday_service import VERSION, baseline_for_slot

    lines = []
    previous = None
    discovery = None
    for slot, projection in projections:
        def add(key, label, text):
            lines.append({'id': f'{slot}.{key}', 'slot': slot, 'label': label, 'text': text,
                          'source_id': projection.get('id'),
                          'collected_end_at': projection.get('collected_end_at')})

        market, ladder = projection['market'], projection['ladder']
        breadth = market.get('breadth', {})
        counts = {key: _known_count(breadth.get(key)) for key in ('up', 'down', 'flat', 'valid')}
        if all(counts[key] is not None for key in ('up', 'down', 'flat')):
            if slot == 'auction':
                label = '竞价候选'
                text = f'关注候选高开 {counts["up"]:,} 只、低开 {counts["down"]:,} 只、平开 {counts["flat"]:,} 只'
                median = market.get('median_change_pct')
                if _number(median):
                    text += f'，涨幅中位数 {median:+.2f}%'
            else:
                label = '有效报价样本'
                text = f'上涨 {counts["up"]:,} 只、下跌 {counts["down"]:,} 只、平盘 {counts["flat"]:,} 只'
            add('breadth', label, f'{text}。')
        amount = market.get('turnover', {}).get('amount')
        if _number(amount):
            add('turnover', '样本成交额', f'已留存范围累计成交 {amount / 100_000_000:,.2f} 亿元。')
        interval = projection.get('interval_amount', {})
        if _number(interval.get('amount')) and _known_count(interval.get('comparable_count')):
            text = f'区间内同股 {interval["comparable_count"]:,} 只，累计成交增加 {interval["amount"] / 100_000_000:,.2f} 亿元'
            if _number(interval.get('change_from_previous')) and interval.get('previous_date'):
                text += f'，较 {interval["previous_date"]} 同段变化 {interval["change_from_previous"] / 100_000_000:+,.2f} 亿元'
            add('interval', '区间成交', text + '。')
        indices = market.get('indices', [])
        if changes := '、'.join(f'{row["name"]} {row["change_pct"]:+.2f}%'
                               for row in indices if _number(row.get('change_pct'))):
            add('index', '主要指数', changes + '。')
        broken = ladder.get('broken_board', {})
        sealed_count = _pool_count(ladder.get('sealed_count'), ladder.get('state'))
        broken_count = _pool_count(broken.get('count'), broken.get('state'))
        if sealed_count or broken_count:
            add('ladder', '已核验来源池', '，'.join(
                value for value in (f'封板 {sealed_count}' if sealed_count else '',
                                    f'炸板 {broken_count}' if broken_count else '') if value) + '。')
        if sector_changes := '、'.join(f'{row["name"]} {row["change_pct"]:+.2f}%'
                                    for row in projection['sectors'][:3] if _number(row.get('change_pct'))):
            label = '题材候选竞价均值前列' if slot == 'auction' else '方向涨幅前列'
            add('sectors', label, sector_changes + '。')
        if slot == 'auction' and (discovery := projection.get('auction_discovery')):
            coverage = discovery['coverage']
            directions = '、'.join(
                f'{row["name"]} {row["stock_count"]} 只竞价上涨'
                for row in discovery['directions'][:3])
            text = f'竞价异动样本核验 {coverage["valid"]}/{coverage["candidates"]} 只'
            if directions:
                text += '，' + directions
            add('discovery', '今日竞价发现样本', text + '。')
            lines[-1].update(source_id=discovery['content_sha256'],
                             collected_end_at=datetime.fromtimestamp(discovery['captured_at_ms'] / 1000, CHINA).isoformat())
        if slot == '1000' and (followup := auction_followup.compare(discovery, projection.get('opening_quotes'))):
            size = followup['comparable']
            if size:
                text = (f'竞价发现中有 {size}/{followup["sample_count"]} 只同股可比。'
                        f'较竞价涨幅扩大 {followup["strengthened"]} 只，回落 {followup["faded"]} 只，'
                        f'竞价未涨、首轮转涨 {followup["turned_positive"]} 只。')
                examples = '、'.join(
                    f'{row["name"]} {row["auction_pct"]:+.2f}% → {row["opening_pct"]:+.2f}%'
                    for row in followup['examples'])
                if examples:
                    text += '代表个股 ' + examples + '。'
            else:
                text = '竞价发现样本与开盘首轮暂无同代码、同昨收的可比报价。'
            add('auction_followup', '竞价发现 · 同股开盘验证', text)
            lines[-1].update(auction_discovery_sha256=discovery['content_sha256'], scope=followup['scope'])
            if followup['directions']:
                direction_changes = '、'.join(
                    f'{row["name"]} {row["comparable"]}/{row["sample_count"]} 只可比，'
                    f'同股涨幅变化中位数 {row["median_change_pp"]:+.2f} 个百分点'
                    for row in followup['directions'][:3])
                add('auction_direction_followup', '竞价发现 · 同题材样本验证', direction_changes + '。')
                lines[-1]['auction_discovery_sha256'] = discovery['content_sha256']
        expected = (baseline_for_slot(slot, VERSION)
                    if slot in ('0945', '1000') and projection.get('version') == VERSION else None)
        if previous and (expected is None or previous[0] == expected):
            previous_slot, previous_projection = previous
            prior_sector_changes = previous_projection.get('sector_index') or {
                row['instrument_id']: row['change_pct'] for row in previous_projection['sectors']}
            changes = []
            for row in projection['sectors'][:3]:
                start = prior_sector_changes.get(row['instrument_id'])
                end = row['change_pct']
                if _number(start) and _number(end):
                    changes.append(f'{row["name"]} {start:+.2f}% → {end:+.2f}%')
            if changes:
                add('rotation', '同代码板块指数对照', LABELS[previous_slot] + '至' + LABELS[slot]
                    + '，' + '、'.join(changes) + '。')
        if slot != 'auction':
            previous = slot, projection
        if slot == 'close':
            comparison = market.get('noon_to_close', {}).get('breadth') or {}
            before, after = comparison.get('noon', {}).get('up'), comparison.get('close', {}).get('up')
            size = comparison.get('count')
            if all(_known_count(value) is not None for value in (size, before, after)):
                add('noon_to_close', '午评与收盘同股对照', f'同一批 {size:,} 只可比股，上涨家数从午评 {before:,} 只到收盘 {after:,} 只。')
    return lines


def _brief(slot, projections):
    from app.services.intraday_service import VERSION

    observed = dict(projections)
    has_early_followup = '0945' in observed or any(value.get('version') == VERSION for value in observed.values())
    context = [{'slot': key, 'label': label,
                'status': observed[key]['quality'] if key in observed else 'missing'}
               for key, label in TIMELINE[:ORDER[slot] + 1] if key != '0945' or has_early_followup]
    evidence = _evidence(projections)
    chosen = []
    for key, _ in projections:
        options = [row for row in evidence if row['slot'] == key]
        if options:
            chosen.append(options[0])
    followup_facts = _followup_facts(evidence)
    if final_facts := [row for row in evidence if row['slot'] == slot and row not in followup_facts][1:]:
        most_useful = ('rotation', 'noon_to_close', 'ladder', 'interval')
        final_facts.sort(key=lambda row: row['id'].rsplit('.', 1)[1] not in most_useful)
        chosen.extend(final_facts[:3])
    chosen = sorted(chosen + [row for row in followup_facts if row not in chosen], key=lambda row: ORDER[row['slot']])
    return _render_brief(slot, context, evidence, chosen, origin='deterministic')


def _followup_facts(evidence):
    return [row for row in evidence if row['id'] in ('auction.discovery', '1000.auction_followup',
                                                       '1000.auction_direction_followup')]


def _render_brief(slot, context, evidence, chosen, *, origin, lens=None):
    status = 'partial' if any(row['status'] != 'complete' for row in context) else 'complete'
    headline = f'{LABELS[slot]}累计简报'
    if lens:
        headline += f' · {LENSES[lens]}'
    body = '\n'.join(f'{LABELS[row["slot"]]} · {row["text"]}' for row in chosen)
    if missing := [row['label'] for row in context if row['status'] == 'missing']:
        body += '\n留存缺口 · ' + '、'.join(missing) + '。'
    return {'slot': slot, 'status': status, 'headline': headline, 'body': body.strip(),
            'origin': origin, 'context': context, 'evidence': evidence}


def _model_selection(brief):
    evidence = brief['evidence']
    if not evidence:
        return brief
    catalog = {row['id']: row for row in evidence}
    expected_stages = {row['slot'] for row in evidence}
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['lens', 'ids'],
              'properties': {'lens': {'type': 'string', 'enum': list(LENSES)},
                             'ids': {'type': 'array', 'items': {'type': 'string', 'enum': list(catalog)},
                                     'minItems': len(expected_stages), 'maxItems': min(len(evidence), 12)}}}
    message = ('你为市场时间线挑选值得读的核验事实。每个已取得的时点至少选一条，当前时点优先选有用数字。'
               '只输出事实编号列表和栏目编号。保留候选竞价与全市场范围区别。')
    payload = {'model': MODEL, 'stream': False, 'think': False, 'format': schema, 'keep_alive': '3m',
               'options': {'temperature': 0, 'num_predict': 320, 'num_ctx': 8192},
               'messages': [{'role': 'system', 'content': message},
                            {'role': 'user', 'content': dumps({'target': brief['slot'],
                                                              'context': brief['context'],
                                                              'evidence': evidence})}]}
    with httpx.Client(trust_env=False, timeout=httpx.Timeout(35, connect=2)) as client:
        with client.stream('POST', MODEL_URL, json=payload) as response:
            response.raise_for_status()
            chunks = bytearray()
            for chunk in response.iter_bytes():
                chunks.extend(chunk)
                if len(chunks) > 65536:
                    raise ValueError('本机模型响应超出预算')
    content = json.loads(chunks)['message']['content']
    result = json.loads(content)
    if not isinstance(result, dict) or set(result) != {'lens', 'ids'}:
        raise ValueError('本机模型未返回事实索引')
    lens, ids = result['lens'], result['ids']
    if lens not in LENSES or (lens == 'close' and brief['slot'] != 'close') or not isinstance(ids, list):
        raise ValueError('本机模型返回的栏目无效')
    if (not len(expected_stages) <= len(ids) <= min(len(evidence), 12)
            or not all(isinstance(item, str) and item in catalog for item in ids)
            or len(ids) != len(set(ids))
            or {catalog[item]['slot'] for item in ids} != expected_stages):
        raise ValueError('本机模型的事实选择遗漏时点或引用未知编号')
    # Keep deterministic cross-stage facts visible even if the model favored a
    # single snapshot's index or breadth facts. All numbers are rendered here.
    keep = [f'{brief["slot"]}.{suffix}' for suffix in ('rotation', 'noon_to_close', 'interval')]
    keep.extend(row['id'] for row in _followup_facts(evidence))
    for key in keep:
        if key in catalog and key not in ids:
            ids.append(key)
    chosen = sorted((catalog[item] for item in ids), key=lambda row: ORDER[row['slot']])
    return _render_brief(brief['slot'], brief['context'], evidence, chosen, origin='local_ai', lens=lens)


def _can_work(connection):
    from app.services.intraday_service import _automation_config
    if not _automation_config(connection)['enabled']:
        return False
    return not connection.execute("SELECT 1 FROM job WHERE status IN ('queued','running') LIMIT 1").fetchone()


def _tick(now, stopping, *, allow_start=None):
    def blocked():
        return stopping.is_set() or allow_start is not None and not allow_start()

    if blocked():
        return
    day = now.date().isoformat()
    at_ms = int(now.timestamp() * 1000)
    with connect() as connection:
        if not _can_work(connection):
            return
        metadata = _metadata(connection, day, at_ms)
        existing = {row['slot']: row for row in connection.execute(
            'SELECT slot,source_sha256,model_attempted FROM market_intraday_brief WHERE trade_date=?', (day,))}
        cutoff = (now.date() - timedelta(days=27)).isoformat()
        expired = connection.execute('SELECT 1 FROM market_intraday_brief WHERE trade_date<? LIMIT 1', (cutoff,)).fetchone()
        if expired:
            connection.execute('DELETE FROM market_intraday_brief WHERE trade_date<?', (cutoff,))
    projections = {}
    ready = []
    for slot, _ in TIMELINE:
        if blocked():
            return
        if slot not in metadata:
            continue
        records = _preceding(metadata, slot)
        source_sha = _digest([BRIEF_VERSION, records])
        previous = existing.get(slot)
        if previous and previous['source_sha256'] == source_sha and previous['model_attempted']:
            continue
        keys = [(key, _digest(record)) for key, record in records]
        for cache_key, (key, record) in zip(keys, records):
            if cache_key not in projections:
                try:
                    projections[cache_key] = _read_projection(key, day, record)
                except (ValueError, KeyError, TypeError, OSError, HTTPException) as error:
                    LOGGER.warning('盘中简报来源核验稍后重试 %s %s', key, type(error).__name__)
                    projections[cache_key] = None
        if any(projections[key] is None for key in keys):
            # A source failing integrity is not equivalent to a confirmed missing slot.
            continue
        observed = {key[0]: projections[key] for key in keys}
        brief = _brief(slot, list(observed.items()))
        anchor = ({key: val for key, val in observed[slot].items() if key != 'auction_discovery'}
                  if slot in ('auction', 'noon', 'close') else None)
        stored = {'brief': brief, 'anchor': anchor}
        encoded = dumps(stored)
        if len(encoded.encode()) > MAX_BRIEF_BYTES:
            raise ValueError('盘中简报超出存档预算')
        with connect() as connection:
            if not _can_work(connection):
                return
            connection.execute('''INSERT INTO market_intraday_brief VALUES(?,?,?,?,?,0)
                ON CONFLICT(trade_date,slot) DO UPDATE SET source_sha256=excluded.source_sha256,
                  body_json=excluded.body_json,generated_at_ms=excluded.generated_at_ms,model_attempted=0
                WHERE market_intraday_brief.source_sha256<>excluded.source_sha256''',
                (day, slot, source_sha, encoded, now_ms()))
        ready.append((slot, source_sha, brief, anchor))
    for slot, source_sha, brief, anchor in ready:
        if blocked():
            return
        with connect() as connection:
            if not _can_work(connection):
                return
        if blocked():
            return
        try:
            enriched = _model_selection(brief)
        except (ValueError, TypeError, KeyError, httpx.HTTPError) as error:
            LOGGER.info('盘中简报保留已核验事实 %s %s', slot, type(error).__name__)
            enriched = brief
        if stopping.is_set():
            return
        encoded = dumps({'brief': enriched, 'anchor': anchor})
        if len(encoded.encode()) <= MAX_BRIEF_BYTES:
            with connect() as connection:
                connection.execute('''UPDATE market_intraday_brief SET body_json=?,model_attempted=1
                    WHERE trade_date=? AND slot=? AND source_sha256=?''', (encoded, day, slot, source_sha))


def start_brief_worker(*, allow_start=None):
    """Starts in the exclusively locked worker. No market requests or DB lock span inference."""
    stopping = threading.Event()

    def run():
        while not stopping.is_set():
            try:
                _tick(datetime.now(CHINA), stopping, allow_start=allow_start)
            except Exception as error:
                LOGGER.warning('盘中累计简报检查稍后重试 %s', type(error).__name__)
            stopping.wait(10)

    thread = threading.Thread(target=run, name='intraday-brief-control', daemon=True)
    thread.start()

    def close():
        stopping.set()
        thread.join()

    return close


def read_brief_view(day, *, snapshots=None, include_sections=True, read_context=None):
    with connect() as connection:
        rows = connection.execute('SELECT slot,body_json FROM market_intraday_brief WHERE trade_date=?', (day,)).fetchall()
    result = {'briefs': [], 'anchors': []}
    for row in sorted(rows, key=lambda value: ORDER.get(value['slot'], len(ORDER))):
        stored = json.loads(row['body_json'])
        result['briefs'].append(stored['brief'])
        if stored.get('anchor'):
            anchor = stored['anchor']
            if row['slot'] == 'noon':
                _restore_noon_details(anchor, day, read_context=read_context)
            elif row['slot'] == 'close':
                closing_context = _restore_close_details(anchor, day, read_context=read_context)
                from app.services.intraday_rotation import close_rotation_sectors
                try:
                    anchor['rotation_sectors'] = close_rotation_sectors(day, anchor['id'], closing_context=closing_context)
                except (ValueError, TypeError, KeyError, OSError, HTTPException):
                    anchor['rotation_sectors'] = []
            result['anchors'].append(anchor)
    if include_sections:
        from app.services.intraday_narrative import attach_sections
        attach_sections(day, result['briefs'], result['anchors'], snapshots, read_context=read_context)
    return result


def _restore_close_details(anchor, day, *, read_context=None):
    """Read original closing evidence only; never rewrite the archived brief body."""
    from app.services.intraday_close import closing_data, restore_close_details
    from app.services.intraday_service import decode_snapshot

    try:
        context = closing_data(day, anchor['id'])
        previous = previous_row = None
        shared = read_context if read_context and read_context.get('date') == day else None
        if shared is not None:
            # These are the same original rows read by this GET, not a lookup
            # cache usable by another connection, date, request or generation.
            candidates = [row for row in shared.get('rows', ()) if row['trade_date'] == day
                          and row['slot'] == '1430' and row['status'] in ('succeeded', 'partial')
                          and isinstance(row['payload_json'], str)
                          and len(row['payload_json'].encode()) <= 2 * 1024 * 1024
                          and row['finished_at_ms'] is not None
                          and row['finished_at_ms'] <= _stamp(context['published_at'], day)]
            previous_row = candidates[0] if candidates else None
        else:
            with connect() as connection:
                tables = {row[0] for row in connection.execute('SELECT name FROM sqlite_master WHERE type=\'table\'')}
                if {'market_intraday_capture', 'job'} <= tables:
                    previous_row = connection.execute('''SELECT c.*,j.finished_at_ms FROM market_intraday_capture c
                        JOIN job j USING(job_id) WHERE c.trade_date=? AND c.slot='1430'
                        AND j.status IN ('succeeded','partial') AND length(CAST(c.payload_json AS BLOB))<=?
                        AND j.finished_at_ms<=?''',
                        (day, 2 * 1024 * 1024, _stamp(context['published_at'], day))).fetchone()
        if previous_row is not None:
            try:
                previous = (decode_snapshot(previous_row, read_context=shared) if shared is not None
                            else decode_snapshot(previous_row))
            except (ValueError, TypeError, KeyError, HTTPException):
                previous_row = None
        restore_close_details(anchor, context, previous=previous, previous_row=previous_row)
        return context
    except (OSError, ValueError, TypeError, KeyError, AttributeError, HTTPException):
        return None


def _restore_noon_details(anchor, day, *, read_context=None):
    """Project original noon pool rows and amounts without modifying stored briefs."""
    from app.services.noon_service import retained_empty_event_pools

    shared = read_context if read_context and read_context.get('date') == day else None
    if shared is not None:
        row = shared.get('noon_row')
        if row is not None and ((row['trade_date'], row['snapshot_id'], row['collected_start_at'], row['collected_end_at']) != (
                day, anchor.get('id'), anchor.get('collected_start_at'), anchor.get('collected_end_at'))
                or len(row['payload_json'].encode()) > MAX_NOON_BYTES):
            row = None
    else:
        with connect() as connection:
            row = connection.execute("""SELECT * FROM market_noon_snapshot
                WHERE trade_date=? AND snapshot_id=? AND collected_start_at=? AND collected_end_at=?
                  AND length(CAST(payload_json AS BLOB))<=?""",
                (day, anchor.get('id'), anchor.get('collected_start_at'),
                 anchor.get('collected_end_at'), MAX_NOON_BYTES)).fetchone()
    if row is None:
        return
    try:
        payload = _verified_noon_payload(row, read_context=shared)
        indices = _noon_indices(payload)
        restored = retained_empty_event_pools(row, payload)
        ladder = _noon_ladder_details({**payload, 'events': {**payload.get('events', {}), **restored}})
    except (ValueError, TypeError, KeyError, AttributeError):
        return
    by_code = {index['instrument_id']: index for index in indices}
    for field in ('market', 'market_pulse'):
        for index in anchor.get(field, {}).get('indices', ()):
            if original := by_code.get(index['instrument_id']):
                index.update({key: original[key] for key in ('amount', 'amount_comparison', 'latest_price')
                              if key in original})
    if ladder is not None:
        anchor['ladder'] = ladder
    from app.services.intraday_rotation import noon_rotation_sectors
    anchor['rotation_sectors'] = noon_rotation_sectors(payload, read_context=shared)
    sector_metadata = {row.get('topic_code') or row.get('instrument_id'): row for row in payload.get('sectors', [])}
    quotes = {row['instrument_id']: row for row in payload.get('stocks', [])}
    for sector in anchor.get('sectors', []):
        original = sector_metadata.get(sector.get('instrument_id'), {})
        if (original.get('kind') == sector.get('kind') and original.get('change_pct') == sector.get('change_pct')):
            sector.update(sector_comparison_metadata(original, source='fuyao'))
            sector.update(_noon_member_metadata(payload, original, quotes, read_context=shared))


def _noon_ladder_details(payload):
    from app.services.intraday_feedback import build_intraday_feedback, feedback_stock_rows
    from app.services.noon_membership_context import frozen_stock_classifications

    events = payload.get('events', {})
    def bounded_pool(key):
        pool = events.get(key)
        if isinstance(pool, dict) and isinstance(pool.get('items'), list) and len(pool['items']) <= MAX_NOON_POOL_ROWS:
            return pool
        return None

    up, broken = bounded_pool('limit_up'), bounded_pool('limit_break')
    if up is None and broken is None:
        return None
    feedback = build_intraday_feedback(payload['stocks'], pool=up, break_pool=broken or {'state': 'unknown', 'items': []},
                                       detail_limit=MAX_NOON_POOL_ROWS)
    rows = list(feedback_stock_rows(feedback))
    if rows and 'stock_classifications' in payload:
        classes = frozen_stock_classifications(payload, {row['instrument_id'] for row in rows})
        for row in rows:
            classification = classes[row['instrument_id']]
            tags = classification['industries'][:1] + classification['topics']
            row['themes'] = [{'name': tag['name'], 'kind': tag['kind'],
                              'member_date': tag['members_observed_date']} for tag in tags]
    return feedback['ladder']
