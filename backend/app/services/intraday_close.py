"""Reopen closing pools from one named immutable generation, never the latest head.

Only a read projection is produced. Original briefs/captures and their SHA stay
unchanged; source pool rows must match positive-volume, unadjusted daily prices.
"""
from collections import Counter, defaultdict
from datetime import datetime, time
from hashlib import sha256
import json

import pyarrow.parquet as pq

from app.services.chart_read_cache import ChartReadCache
from app.services.noon_analysis import number
from app.services.noon_collection import CHINA, STOCK_CODE

VERSION = 'intraday-close-pools-v1'
MAX_STOCKS = 10000
MAX_QUOTES = 22000
MAX_ACTIONS = 8 * 1024 * 1024
POOL_NAMES = {'limit_up': 'limitup', 'limit_break': 'breakpool', 'limit_down': 'limitdown'}
# Reuse only filtered source facts. Each request still verifies the named files.
_source_projections = ChartReadCache(entries=16, bytes_limit=12 * 1024 * 1024)


def _prices(root, generation, summary, days):
    from app.analytics.market_storage import safe_object, verify_immutable_file
    from app.services.noon_prior_events import _file, _json, MAX_MANIFEST, MAX_PRICE_FILE, _day

    path, manifest_sha = _file(root, generation, summary, 'daily.parquet', ('daily.manifest.json',), MAX_MANIFEST)
    descriptor = _json(path, MAX_MANIFEST)
    parts = descriptor.get('partitions')
    if (descriptor.get('version') != 'market-partitions-v1' or descriptor.get('role') != 'daily.parquet'
            or not isinstance(parts, list) or len(parts) > 240):
        raise ValueError('收盘原代际月行情清单无效')
    output, hashes = {}, {}
    for month in sorted({day[:7] for day in days}):
        selected = [part for part in parts if isinstance(part, dict) and part.get('month') == month]
        if len(selected) != 1:
            raise ValueError('收盘原代际没有唯一的指定月行情')
        part = selected[0]
        digest, size = part.get('sha256'), part.get('bytes')
        if (not isinstance(digest, str) or len(digest) != 64 or type(size) is not int or not 0 < size <= MAX_PRICE_FILE
                or part.get('object_key') != f'market-objects/daily/{digest[:2]}/{digest}.parquet'
                or not _day(part.get('data_begin')) or not _day(part.get('data_end'))):
            raise ValueError('收盘行情对象身份或边界无效')
        wanted = [day for day in days if day[:7] == month]
        if any(not part['data_begin'] <= day <= part['data_end'] for day in wanted):
            raise ValueError('收盘行情对象不覆盖指定交易日')
        price_path = safe_object(root, part['object_key'])
        verify_immutable_file(price_path, digest, size)
        key = ('prices', price_path, digest, tuple(wanted))
        projection = _source_projections.read(key)
        if projection is None:
            projection = _price_rows(price_path, wanted)
            verify_immutable_file(price_path, digest, size)
            _source_projections.remember(key, projection)
        output.update(projection)
        hashes.update({day: digest for day in wanted})
    return output, manifest_sha, hashes


def _price_rows(path, wanted):
    metadata = pq.ParquetFile(path).metadata
    if metadata.num_rows > 500000 or metadata.num_row_groups > 128:
        raise ValueError('收盘行情对象超过有界读取预算')
    columns = ['instrument_id', 'date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'source', 'price_basis']
    rows = pq.read_table(path, columns=columns, filters=[('date', 'in', wanted)]).to_pylist()
    if len(rows) > MAX_QUOTES:
        raise ValueError('收盘两日股票报价超过读取预算')
    ids = Counter((row.get('date'), row.get('instrument_id')) for row in rows)
    output = {}
    for row in rows:
        key = row.get('date'), row.get('instrument_id')
        if (key[0] not in wanted or not STOCK_CODE.fullmatch(str(key[1])) or ids[key] != 1
                or row.get('source') != 'fuyao' or row.get('price_basis') != 'none'
                or number(row.get('close'), positive=True) is None
                or number(row.get('volume'), positive=True) is None):
            continue
        output[key] = row
    return output


def _action_days(root, generation, summary, symbols):
    from app.services.noon_prior_events import _file
    from app.analytics.market import date_ms

    try:
        path, digest = _file(root, generation, summary, 'corporate-actions.parquet',
                             ('corporate-actions.parquet',), MAX_ACTIONS)
        wanted = sorted(symbols)
        key = ('actions', path, digest, sha256(','.join(wanted).encode()).hexdigest())
        cached = _source_projections.read(key)
        if cached is not None:
            return cached, digest
        rows = pq.read_table(path, columns=['thscode', 'ex_date_ms'],
                             filters=[('thscode', 'in', wanted)]).to_pylist()
        if len(rows) > 100000:
            raise ValueError('公司行动资料超过读取预算')
        blocked = defaultdict(set)
        for row in rows:
            try:
                day = date_ms(row.get('ex_date_ms'))
            except (ValueError, TypeError, OverflowError, OSError):
                day = None
            blocked[row['thscode']].add(day)
        _file(root, generation, summary, 'corporate-actions.parquet', ('corporate-actions.parquet',), MAX_ACTIONS)
        _source_projections.remember(key, blocked)
        return blocked, digest
    except (OSError, ValueError, KeyError, TypeError):
        return None, None  # Missing evidence is not proof of no actions.


def closing_data(day, generation):
    """Private shared source context: complete current quotes and three dated pools."""
    from app.core import published_dir
    from app.analytics.market_storage import safe_object, load_reference
    from app.services.noon_prior_events import _reference, _file, _json, _day, MAX_MANIFEST, MAX_REFERENCE
    from app.services.noon_context import _event_cohorts

    root = published_dir().resolve()
    original, record, summary, reference_sha = _reference(root, generation, day)
    if summary.get('data_end') != day or summary.get('snapshot_id') != generation:
        raise ValueError('收盘资料不是原指定日正式代际')
    manifest_path = safe_object(root, generation + '/manifest.json')
    manifest = _json(manifest_path, MAX_MANIFEST)
    published = datetime.fromisoformat(manifest['published_at'])
    closing = datetime.combine(datetime.fromisoformat(day).date(), time(15), CHINA)
    if published.tzinfo is None or published < closing:
        raise ValueError('收盘代际发布时间在正式收盘之前')
    path, _ = _file(root, generation, summary, 'reference.json', ('reference.json',), MAX_REFERENCE)
    descriptor = _json(path, MAX_REFERENCE)
    if descriptor.get('storage_version') in ('market-reference-parts-v1', 'market-reference-parts-v2'):
        fields = descriptor.get('fields')
        selected = {key: fields[key] for key in ('calendar', 'master', 'topics') if key in fields} if isinstance(fields, dict) else {}
        if (not selected or any(not isinstance(row, dict) or type(row.get('bytes')) is not int or row['bytes'] < 0
                                for row in selected.values())
                or sum(row['bytes'] for row in selected.values()) > MAX_REFERENCE):
            raise ValueError('收盘原代际成员及日历超过有界资料预算')
        reference = load_reference(path, document={'storage_version': descriptor['storage_version'],
                                                  'fields': selected, 'indices': {}})
    else:
        reference = descriptor
    calendar = reference.get('calendar')
    if (not isinstance(calendar, list) or len(calendar) > 15000 or day not in calendar
            or not all(_day(value) for value in calendar) or calendar != sorted(set(calendar))):
        raise ValueError('原代际交易日历未覆盖所选收盘日')
    position = calendar.index(day)
    previous = calendar[position - 1] if position else None
    days = [previous, day] if previous and previous < day else [day]
    prices, price_manifest_sha, price_shas = _prices(root, generation, summary, days)
    current = {code: row for (at, code), row in prices.items() if at == day}
    if len(current) > MAX_STOCKS:
        raise ValueError('收盘股票报价超过有界预算')
    actions, action_sha = _action_days(root, generation, summary, current)
    master = reference.get('master', {})
    quotes = {}
    for code, row in current.items():
        prior = prices.get((previous, code))
        can_compare = prior and actions is not None and not ({None, day} & actions.get(code, set()))
        quotes[code] = {**row, 'name': master.get(code, {}).get('name') or code,
            'latest_price': row['close'], 'currency': 'CNY',
            'prev_close': prior['close'] if can_compare else None,
            'change_pct': (row['close'] / prior['close'] - 1) * 100 if can_compare else None}
    cohorts = _event_cohorts({**original, '_supplement': {}}, day, current)
    coverage = record.get('pool_coverage') if record is original else record.get('coverage')
    coverage = coverage if isinstance(coverage, dict) else {}
    pools = {}
    for key, name in POOL_NAMES.items():
        cohort, proof = cohorts[key], coverage.get(name) or {}
        raw = record.get(name)
        raw = raw if isinstance(raw, list) else []
        raw_ids = Counter(row.get('thscode') or row.get('instrument_id') for row in raw if isinstance(row, dict))
        events = {row.get('thscode') or row.get('instrument_id'): row for row in raw if isinstance(row, dict)}
        items = []
        for row in cohort['items']:
            code = row['instrument_id']
            quote = quotes.get(code)
            if quote is None or raw_ids[code] != 1:
                continue
            event = events[code]
            items.append({'instrument_id': code, 'name': event.get('name') or quote['name'],
                'latest_price': quote['latest_price'], 'change_pct': quote['change_pct'],
                'continue_day_cnt': row['previous_board_count'] if key == 'limit_up' else None,
                'limit_up_reason': row.get('theme_text'),
                **{field: event[field] for field in ('open_times', 'limit_up_time', 'first_limit_time',
                    'last_limit_time', 'first_seal_time', 'last_seal_time') if field in event}})
            quotes[code]['name'] = items[-1]['name']
        exact = (proof.get('complete') is True and type(proof.get('declared_total')) is int
                 and proof['declared_total'] == len(raw) == len(items)
                 and proof.get('state') in (None, 'complete', 'observed', 'ready')
                 and (proof.get('quarantined_rows') is None
                      or type(proof['quarantined_rows']) is int and proof['quarantined_rows'] == 0)
                 and all(field not in proof or type(proof[field]) is int and proof[field] == len(raw)
                         for field in ('identified_rows', 'observed_rows')))
        pools[key] = {'state': 'observed' if exact else 'partial' if items else 'unknown',
                      'count': len(items) if exact or items else None, 'items': items,
                      'source_count': len(raw), 'declared_total': proof.get('declared_total')}
    conflicts = set()
    for left, right in (('limit_up', 'limit_break'), ('limit_up', 'limit_down'), ('limit_break', 'limit_down')):
        conflicts |= {row['instrument_id'] for row in pools[left]['items']} & {row['instrument_id'] for row in pools[right]['items']}
    for pool in pools.values():
        if conflicts & {row['instrument_id'] for row in pool['items']}:
            pool['items'] = [row for row in pool['items'] if row['instrument_id'] not in conflicts]
            pool.update(state='partial' if pool['items'] else 'unknown', count=len(pool['items']) or None)
    binding = {'snapshot_id': generation, 'date': day, 'slot': 'close', 'source_id': 'fuyao',
        'content_sha256': sha256(manifest_path.read_bytes()).hexdigest(), 'reference_sha256': reference_sha,
        'price_manifest_sha256': price_manifest_sha, 'price_sha256': price_shas,
        'actions_sha256': action_sha, 'collected_start_at': closing.isoformat(),
        'collected_end_at': closing.isoformat(), 'published_at': published.astimezone(CHINA).isoformat(),
        'basis': '原指定日正式代际来源池与同日未复权正成交量收盘核验'}
    return {'date': day, 'generation': generation, 'published_at': binding['published_at'],
            'source_binding': binding, 'reference': reference, 'quotes': quotes, 'pools': pools,
            'conflicts': sorted(conflicts)}


def _themes(context, symbols):
    from app.services.intraday_feedback import stock_themes
    from app.services.noon_prior_events import _day

    topics = context['reference'].get('topics', ())
    counts = Counter(row.get('topic_code') for row in topics if isinstance(row, dict))
    sectors, members = [], {}
    for row in topics:
        code = row.get('topic_code')
        ids, observed = row.get('instrument_ids'), row.get('observed_date')
        if (counts[code] != 1 or row.get('source') != 'fuyao' or row.get('kind') not in ('industry', 'concept')
                or not _day(observed) or observed > context['date'] or not isinstance(ids, list)
                or not 0 < len(ids) <= MAX_STOCKS or len(set(ids)) != len(ids)
                or not all(isinstance(value, str) and STOCK_CODE.fullmatch(value) for value in ids)):
            continue
        sectors.append({'instrument_id': code, 'name': row.get('name'), 'kind': row['kind']})
        members[code] = {'date': observed, 'instrument_ids': ids}
    return stock_themes(sectors, members, symbols)


def close_frame(context):
    """Compact transient input for the same adjacent-pool projection used intraday."""
    binding = context['source_binding']
    symbols = {row['instrument_id'] for pool in context['pools'].values() for row in pool['items']}
    themes = _themes(context, symbols)
    values = []
    for key, pool in context['pools'].items():
        for event in pool['items']:
            code = event['instrument_id']
            values.append({**context['quotes'][code], **event, 'board_count': event.get('continue_day_cnt'),
                'state': 'sealed' if key == 'limit_up' else 'broken' if key == 'limit_break' else 'limit_down',
                'limit_up_state': 'sealed' if key == 'limit_up' else 'broken' if key == 'limit_break' else None,
                **({'limit_down_state': 'sealed'} if key == 'limit_down' else {}), 'themes': themes.get(code, [])})
    pools = {key: {**pool, 'items': [row for row in values if row['instrument_id'] in {
        event['instrument_id'] for event in pool['items']}]} for key, pool in context['pools'].items()}
    frame = {'version': VERSION, 'date': context['date'], 'slot': 'close', 'id': context['generation'],
        'collected_start_at': binding['collected_start_at'], 'collected_end_at': binding['collected_end_at'],
        'published_at': binding['published_at'], 'source_binding': binding,
        'board_pool_evidence': {'version': 'intraday-board-pools-v1', **pools, 'conflicts': context['conflicts']},
        'comparison_input': {'stocks': [context['quotes'][code] for code in sorted(symbols)]},
        'stocks': values, 'sealed_stocks': pools['limit_up']['items'], 'broken_stocks': pools['limit_break']['items']}
    groups = defaultdict(list)
    for row in frame['sealed_stocks']:
        height = row.get('continue_day_cnt')
        height = height if type(height) is int and height > 0 else None
        row['board_count'] = height
        groups[height].append(row)
    exact = pools['limit_up']['state'] == 'observed'
    known = sum(len(rows) for height, rows in groups.items() if height is not None)
    heights_exact = exact and None not in groups
    frame['ladder'] = {'state': pools['limit_up']['state'], 'sealed_count': pools['limit_up']['count'],
        'height_state': 'observed' if heights_exact else 'partial' if known else 'unknown',
        'first_board_count': len(groups.get(1, [])) if heights_exact or groups.get(1) else None,
        'multi_board_count': sum(len(rows) for height, rows in groups.items() if height and height > 1) if heights_exact or any(h and h > 1 for h in groups) else None,
        'highest_board': max((height for height in groups if height is not None), default=None),
        'unknown_height_count': len(groups.get(None, [])) if exact or None in groups else None,
        'groups': [{'board_count': height, 'label': f'{height}板' if height else '高度待核验',
                    'count': len(groups[height]), 'items': groups[height]} for height in sorted(groups, key=lambda n: -(n or 0))],
        'broken_board': pools['limit_break']}
    return frame


def frame_row(frame):
    raw = json.dumps(frame, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(raw.encode()) > 2 * 1024 * 1024:
        raise ValueError('收盘只读板况投影超过2MiB预算')
    return {'trade_date': frame['date'], 'snapshot_id': frame['id'], 'slot': 'close',
            'payload_json': raw, 'content_sha256': sha256(raw.encode()).hexdigest()}


def restore_close_details(anchor, context, *, previous=None, previous_row=None):
    from app.services.intraday_board_changes import build_changes, attach_current_states

    if (anchor.get('date'), anchor.get('id'), anchor.get('collected_end_at')) != (
            context['date'], context['generation'], context['source_binding']['collected_end_at']):
        raise ValueError('原收盘简报与读取代际不一致')
    frame = close_frame(context)
    frame['board_changes'] = build_changes(frame, previous, current_row=frame_row(frame), previous_row=previous_row)
    attach_current_states(frame)
    for key in ('ladder', 'sealed_stocks', 'broken_stocks', 'board_changes', 'source_binding'):
        anchor[key] = frame[key]
    return frame
