"""Adjacent saved observations describe point-in-time pool changes, never tick events."""
from collections import Counter
from datetime import datetime, time, timedelta
from hashlib import sha256
import json
import re

from app.services.noon_collection import CHINA, STOCK_CODE
from app.services.noon_analysis import number

VERSION = 'intraday-board-changes-v1'
EVIDENCE_VERSION = 'intraday-board-pools-v1'
LABELS = {'new_sealed': '较上档新入封板池', 'new_broken': '较上档新增炸板样本',
          'new_limit_down': '较上档新入跌停池', 'opened': '上档封板、本档炸板',
          'resealed': '上档炸板、本档封板'}
LIMIT = 32
MAX_POOL = 2000
SCOPE = '对照两份已留存观察，非连续分时；新入池不等于首次涨停，池中缺席不代表开板或跌停解除。'


def _record_payload(row, maximum, read_cache=None, *, binding_only=False):
    """Reuse syntax trees only for the identical row/body within one GET.

    Size and the original body SHA are still checked at every entry. Holding
    both objects prevents identity reuse; a different SQLite row, body string
    or digest must be parsed again. A small independent binding projection is
    protected from the display enrichments later attached to live snapshots.
    """
    text = row['payload_json']
    raw = text.encode()
    digest = row['content_sha256']
    if len(raw) > maximum or sha256(raw).hexdigest() != digest:
        raise ValueError('快照原文大小或SHA校验失败')
    records = read_cache.setdefault('record_payloads', {}) if read_cache is not None else None
    entry = records.get(id(row)) if records is not None else None
    if entry is not None and entry[0] is row and entry[1] is text and entry[2] == digest:
        return entry[4] if binding_only else entry[3]
    value = json.loads(raw)
    if records is not None and len(records) < 32:
        from copy import deepcopy

        binding = {key: deepcopy(value[key]) for key in (
            'id', 'date', 'slot', 'version', 'collected_start_at', 'collected_end_at',
            'source_binding', 'source_checks', 'sources') if key in value}
        records[id(row)] = row, text, digest, value, binding
        return binding if binding_only else value
    return value


def _binding(snapshot, row, *, read_cache=None):
    if row is None:
        return None
    try:
        maximum = 8 * 1024 * 1024 if snapshot.get('slot') == 'noon' else 2 * 1024 * 1024
        digest = row['content_sha256']
        if not row['payload_json']:
            return None
        original = _record_payload(row, maximum, read_cache, binding_only=True)
        slot = snapshot.get('slot')
        if (original.get('date') != snapshot.get('date') or row['trade_date'] != snapshot.get('date')
                or row['snapshot_id'] != snapshot.get('id')):
            return None
        start, end = (datetime.fromisoformat(original[key]) for key in ('collected_start_at', 'collected_end_at'))
        if (start.tzinfo is None or end.tzinfo is None or start > end
                or any(value.astimezone(CHINA).date().isoformat() != snapshot['date'] for value in (start, end))):
            return None
        if slot == 'close':
            from app.services.intraday_close import VERSION as CLOSE_VERSION

            receipt = original.get('source_binding', {})
            if (original.get('version') != CLOSE_VERSION or original.get('id') != snapshot.get('id')
                    or original.get('slot') != 'close' or receipt != snapshot.get('source_binding')
                    or (receipt.get('snapshot_id'), receipt.get('date'), receipt.get('source_id')) != (
                        snapshot['id'], snapshot['date'], 'fuyao')
                    or not re.fullmatch('[a-f0-9]{64}', str(receipt.get('content_sha256')))
                    or not re.fullmatch('[a-f0-9]{64}', str(receipt.get('reference_sha256')))
                    or start != end or start.astimezone(CHINA).strftime('%H:%M:%S') != '15:00:00'
                    or datetime.fromisoformat(receipt['published_at']) < end):
                return None
            return {**receipt, 'projection_sha256': digest}
        if slot == 'noon':
            if original.get('version') != 'noon-snapshot-v1' or not '11:30' <= start.strftime('%H:%M') <= end.strftime('%H:%M') < '13:00':
                return None
        else:
            from app.services.intraday_service import MODERN_VERSIONS, slots_for_version

            if (original.get('id') != snapshot.get('id') or original.get('slot') != slot or row['slot'] != slot
                    or original.get('version') != snapshot.get('version') or original['version'] not in MODERN_VERSIONS):
                return None
            window = next((value for value in slots_for_version(original['version']) if value['key'] == slot), None)
            if not window or not window['start'] <= start.astimezone(CHINA).strftime('%H:%M') <= end.astimezone(CHINA).strftime('%H:%M') < window['end']:
                return None
        frame = {**original, 'slot': slot, 'id': snapshot['id']}
        if not any(_pool_receipt(frame, key) for key in ('limit_up', 'limit_break', 'limit_down')):
            return None
        return {'snapshot_id': snapshot['id'], 'content_sha256': digest, 'date': snapshot['date'],
                'slot': slot, 'source_id': 'fuyao', 'collected_start_at': start.astimezone(CHINA).isoformat(),
                'collected_end_at': end.astimezone(CHINA).isoformat()}
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def _pool_receipt(snapshot, key):
    """Only a same-window, checksummed response can support a saved pool claim."""
    if snapshot.get('slot') == 'close':
        from app.services.intraday_close import VERSION as CLOSE_VERSION

        receipt = snapshot.get('source_binding', {})
        return (snapshot.get('version') == CLOSE_VERSION
                and (receipt.get('snapshot_id'), receipt.get('date'), receipt.get('source_id')) == (
                    snapshot.get('id'), snapshot.get('date'), 'fuyao')
                and bool(re.fullmatch('[a-f0-9]{64}', str(receipt.get('reference_sha256')))))
    noon = snapshot.get('slot') == 'noon'
    checks = snapshot.get('sources' if noon else 'source_checks', ())
    if not isinstance(checks, list):
        return False
    for check in checks:
        if not isinstance(check, dict) or check.get('domain', check.get('name')) != key + '_page_1':
            continue
        digest = check.get('sha256' if noon else 'response_sha256')
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            continue
        stamp = check.get('source_timestamp', {}) if noon else {}
        stamp = stamp.get('time') if isinstance(stamp, dict) and stamp.get('state') == 'observed' else check.get('source_time')
        try:
            clock = datetime.fromisoformat(stamp)
            if clock.tzinfo is None:
                continue
            clock = clock.astimezone(CHINA)
            start, end = (datetime.fromisoformat(snapshot[field]).astimezone(CHINA)
                          for field in ('collected_start_at', 'collected_end_at'))
            lower = datetime.combine(start.date(), time(11, 30), CHINA) if noon else start - timedelta(seconds=60)
            if clock.date().isoformat() != snapshot['date'] or not lower <= clock <= end:
                continue
        except (TypeError, ValueError, KeyError):
            continue
        if noon:
            if (check.get('state') == 'received' and check.get('source') == 'fuyao'
                    and check.get('endpoint') == '/api/a-share/special-data/' + key.replace('_', '-') + '-pool'):
                return True
        elif check.get('state') == 'accepted':
            return True  # The v5 frozen task contract fixes all these endpoints to Fuyao.
    return False


def noon_frame(row, *, read_cache=None):
    """The optional lunchtime baseline is read from its original checksummed body."""
    if row is None:
        return None
    try:
        value = _record_payload(row, 8 * 1024 * 1024, read_cache)
        if value.get('version') != 'noon-snapshot-v1' or value.get('date') != row['trade_date']:
            return None
        frames = read_cache.setdefault('noon_frames', {}) if read_cache is not None else None
        cached = frames.get(id(row)) if frames is not None else None
        identity = row['snapshot_id'], row['trade_date']
        if cached is not None and cached[0] is row and cached[1] is value and cached[2] == identity:
            return cached[3]
        from app.services.noon_service import retained_empty_event_pools

        restored = retained_empty_event_pools(row, value)
        frame = {**value, 'id': row['snapshot_id'], 'slot': 'noon',
                 'events': {**value.get('events', {}), **restored},
                 'comparison_input': {'stocks': value.get('stocks', ())}}
        if frames is not None and len(frames) < 8:
            frames[id(row)] = row, value, identity, frame
        return frame
    except (ValueError, TypeError, KeyError):
        return None


def _pool(snapshot, key):
    if not _pool_receipt(snapshot, key):
        return {}, False
    if snapshot.get('slot') == 'noon':
        pool = snapshot.get('events', {}).get(key)
    elif snapshot.get('board_pool_evidence', {}).get('version') == EVIDENCE_VERSION:
        pool = snapshot['board_pool_evidence'].get(key)
    elif key == 'limit_up':
        pool = snapshot.get('sealed_pool_input')
    elif key == 'limit_break':
        pool = snapshot.get('ladder', {}).get('broken_board')
    else:
        # No intraday down pool was collected by v5. A quote near -10% is not evidence.
        pool = None
    if not isinstance(pool, dict) or pool.get('state') not in ('observed', 'partial'):
        return {}, False
    rows = pool.get('items')
    if not isinstance(rows, (list, tuple)) or len(rows) > MAX_POOL:
        return {}, False
    codes = Counter(row.get('instrument_id') for row in rows if isinstance(row, dict))
    values = {row['instrument_id']: row for row in rows if isinstance(row, dict)
              and STOCK_CODE.fullmatch(str(row.get('instrument_id'))) and codes[row['instrument_id']] == 1}
    exact = pool['state'] == 'observed' and len(values) == len(rows)
    count = pool.get('count')
    if count is not None and (type(count) is not int or count != len(values)):
        exact = False
    return values, exact


def _conflicts(snapshot, pools=None):
    pools = pools or {key: _pool(snapshot, key) for key in ('limit_up', 'limit_break', 'limit_down')}
    conflicts = set()
    for left, right in (('limit_up', 'limit_break'), ('limit_up', 'limit_down'), ('limit_break', 'limit_down')):
        conflicts |= pools[left][0].keys() & pools[right][0].keys()
    recorded = snapshot.get('board_pool_evidence', {}).get('conflicts', ())
    if isinstance(recorded, (list, tuple)) and len(recorded) <= MAX_POOL:
        conflicts.update(code for code in recorded if isinstance(code, str) and STOCK_CODE.fullmatch(code))
    return conflicts


def _pools(snapshot):
    pools = {key: _pool(snapshot, key) for key in ('limit_up', 'limit_break', 'limit_down')}
    conflicts = _conflicts(snapshot, pools)
    return {key: ({code: row for code, row in values.items() if code not in conflicts}, exact and not conflicts)
            for key, (values, exact) in pools.items()}


def _quotes(snapshot):
    rows = snapshot.get('comparison_input', {}).get('stocks', ())
    codes = Counter(row.get('instrument_id') for row in rows if isinstance(row, dict))
    result = {}
    for row in rows:
        if not isinstance(row, dict) or codes[row.get('instrument_id')] != 1:
            continue
        price, previous, change = (number(row.get(key), positive=key != 'change_pct')
                                   for key in ('latest_price', 'prev_close', 'change_pct'))
        if (price is not None and previous is not None and change is not None
                and abs((price / previous - 1) * 100 - change) <= .001
                and row.get('price_basis') == 'none' and row.get('currency') == 'CNY'):
            result[row['instrument_id']] = row
    return result


def frame_quotes(snapshot, cache=None):
    """Same immutable input object within one request; never cache across requests or dates."""
    if cache is None:
        return _quotes(snapshot)
    source = snapshot.get('comparison_input')
    key = (id(source), snapshot.get('date'), snapshot.get('slot'), snapshot.get('id'))
    indexed = cache.setdefault('quote_indices', {})
    saved = indexed.get(key)
    if saved is None or saved[0] is not source:
        saved = source, _quotes(snapshot)
        indexed[key] = saved
    return saved[1]


def _read_binding(snapshot, row, cache):
    if cache is None:
        return _binding(snapshot, row)
    if row is None:
        return None
    try:
        record = (id(row['payload_json']), row['content_sha256'], row['trade_date'], row['snapshot_id'])
    except (KeyError, IndexError, TypeError):
        return _binding(snapshot, row)
    key = (id(snapshot), id(row), snapshot.get('id'), snapshot.get('date'), snapshot.get('slot'),
           snapshot.get('collected_start_at'), snapshot.get('collected_end_at'), record)
    bindings = cache.setdefault('frame_bindings', {})
    if key not in bindings:
        bindings[key] = snapshot, row, _binding(snapshot, row, read_cache=cache)
    return bindings[key][2]


def build_changes(current, previous, *, current_row, previous_row=None, read_cache=None):
    """Exact differences require complete enumerations; positive cross-pool matches give lower bounds."""
    from app.services.intraday_service import baseline_for_slot

    baseline = baseline_for_slot(current.get('slot'), current.get('version'))
    result = {'version': VERSION, 'scope': SCOPE, 'baseline_slot': baseline, 'state': 'unknown',
              'source_bindings': {}, 'groups': {key: {'label': label, 'state': 'unknown', 'count': None,
                   'items': [], 'omitted_count': 0, 'reason_code': 'missing_adjacent_snapshot'}
                   for key, label in LABELS.items()}}
    now = _read_binding(current, current_row, read_cache)
    before = _read_binding(previous, previous_row, read_cache) if previous else None
    if now:
        result['source_bindings']['current'] = now
    if before:
        result['source_bindings']['previous'] = before
    if (not baseline or not now or not before or before['slot'] != baseline
            or before['date'] != now['date'] or before['source_id'] != now['source_id']
            or before['collected_end_at'] >= now['collected_start_at']):
        return result
    current_pools, prior_pools = _pools(current), _pools(previous)
    up, up_exact = current_pools['limit_up']; old_up, old_up_exact = prior_pools['limit_up']
    broken, broken_exact = current_pools['limit_break']; old_broken, old_broken_exact = prior_pools['limit_break']
    down, down_exact = current_pools['limit_down']; old_down, old_down_exact = prior_pools['limit_down']
    current_quotes, previous_quotes = frame_quotes(current, read_cache), frame_quotes(previous, read_cache)
    sample_rows = {}
    for snapshot in (current,):
        ladder = snapshot.get('ladder', {})
        for row in [*snapshot.get('stocks', ()), *snapshot.get('sealed_stocks', ()), *(entry for group in ladder.get('groups', ())
                    for entry in group.get('items', ())), *ladder.get('broken_board', {}).get('items', ())]:
            if isinstance(row, dict) and row.get('instrument_id'):
                sample_rows[row['instrument_id']] = row
    candidates = {
        'new_sealed': (set(up) - old_up.keys() if old_up_exact else set(up) & old_broken.keys(), up_exact and old_up_exact),
        'new_broken': (set(broken) - old_broken.keys() if old_broken_exact else set(broken) & old_up.keys(), broken_exact and old_broken_exact),
        'new_limit_down': (set(down) - old_down.keys() if old_down_exact else set(), down_exact and old_down_exact),
        'opened': (set(old_up) & broken.keys(), old_up_exact and broken_exact),
        'resealed': (set(old_broken) & up.keys(), old_broken_exact and up_exact),
    }
    for key, (codes, exact) in candidates.items():
        rows = []
        for code in sorted(codes):
            quote, old_quote = current_quotes.get(code), previous_quotes.get(code)
            if not quote or old_quote and quote['prev_close'] != old_quote['prev_close']:
                exact = False
                continue
            # A state-to-state transition needs real prices in both observations.
            if key in ('opened', 'resealed') and not old_quote:
                exact = False
                continue
            sample = {**sample_rows.get(code, {}), **current_pools[
                'limit_up' if key in ('new_sealed', 'resealed') else 'limit_down' if key == 'new_limit_down' else 'limit_break'][0].get(code, {})}
            height = sample.get('continue_day_cnt', sample.get('board_count'))
            height = height if type(height) is int and height > 0 else None
            rows.append({'instrument_id': code, 'name': quote.get('name') or sample.get('name') or code,
                         'latest_price': quote['latest_price'], 'change_pct': quote['change_pct'],
                         'amount': quote.get('amount'), 'price_basis': 'none', 'currency': 'CNY',
                         'previous_change_pct': old_quote.get('change_pct') if old_quote else None,
                         'board_count': height, 'continue_day_cnt': height,
                         **({'themes': sample['themes']} if sample.get('themes') else {}),
                         **({'board_transition': 'resealed'} if key == 'resealed' else {}),
                         **({'limit_down_state': 'sealed'} if key == 'new_limit_down' else
                            {'limit_up_state': 'sealed' if key in ('new_sealed', 'resealed') else 'broken'}),
                         'quote_context': {'date': now['date'], 'slot': now['slot'], 'snapshot_id': now['snapshot_id']}})
        rows.sort(key=lambda row: (-row['change_pct'], row['instrument_id']))
        state = 'observed' if exact else 'partial' if rows else 'unknown'
        missing_down = key == 'new_limit_down' and 'limit_down' not in current.get('board_pool_evidence', {})
        reason = ('complete_adjacent_pools' if exact else 'confirmed_intersection_only' if rows else
                  'no_saved_down_pool' if missing_down else 'incomplete_pool_evidence')
        result['groups'][key].update(state=state, count=len(rows) if exact or rows else None,
                                   items=rows[:LIMIT], omitted_count=max(0, len(rows) - LIMIT), reason_code=reason)
    states = [group['state'] for group in result['groups'].values()]
    result['state'] = 'observed' if all(state == 'observed' for state in states) else 'partial' if any(
        state != 'unknown' for state in states) else 'unknown'
    return result


def read_noon_row(connection, day):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='market_noon_snapshot'").fetchone():
        return None
    return connection.execute('''SELECT * FROM market_noon_snapshot WHERE trade_date=?
        AND length(CAST(payload_json AS BLOB))<=?''', (day, 8 * 1024 * 1024)).fetchone()


def attach_changes(snapshots, rows, noon_row=None, *, read_cache=None):
    from app.services.intraday_service import MODERN_VERSIONS, baseline_for_slot

    records = {row['slot']: row for row in rows}
    by_slot = {snapshot['slot']: snapshot for snapshot in snapshots}
    noon = noon_frame(noon_row, read_cache=read_cache)
    for snapshot in snapshots:
        if snapshot.get('version') not in MODERN_VERSIONS:
            continue
        key = baseline_for_slot(snapshot['slot'], snapshot['version'])
        prior = noon if key == 'noon' else by_slot.get(key)
        snapshot['board_changes'] = build_changes(snapshot, prior, current_row=records.get(snapshot['slot']),
                                                  previous_row=noon_row if key == 'noon' else records.get(key),
                                                  read_cache=read_cache)


def stock_rows(snapshot):
    for group in snapshot.get('board_changes', {}).get('groups', {}).values():
        yield from group.get('items', ())


def attach_current_states(snapshot):
    """Keep feedback's historical state and add only this snapshot's positive pool state."""
    from app.services.intraday_feedback import feedback_stock_rows
    pools = _pools(snapshot)
    conflicts = _conflicts(snapshot)
    sealed, broken, down = (pools[key][0] for key in ('limit_up', 'limit_break', 'limit_down'))
    resealed_group = snapshot.get('board_changes', {}).get('groups', {}).get('resealed', {})
    resealed = ({row['instrument_id'] for row in resealed_group.get('items', ())}
                if resealed_group.get('state') in ('observed', 'partial') else set())
    heights = {row['instrument_id']: row.get('continue_day_cnt') for row in snapshot.get('sealed_stocks', ())}
    for group in snapshot.get('ladder', {}).get('groups', ()):
        heights.update({row['instrument_id']: group.get('board_count') for row in group.get('items', ())})
    rows = [*feedback_stock_rows(snapshot), *snapshot.get('stocks', ()), *snapshot.get('candidates', ()),
            *snapshot.get('sealed_stocks', ()), *snapshot.get('broken_stocks', ()), *stock_rows(snapshot)]
    for key in ('strong_sectors', 'limit_up_sectors', 'wide_limit_up_sectors', 'index_only_sectors'):
        rows.extend(row for board in snapshot.get(key, ()) for row in board.get('candidates', ()))
    rows.extend(row for board in snapshot.get('board_stocks', {}).values() for row in board.get('items', ()))
    for row in rows:
        code = row.get('instrument_id')
        if code in conflicts:
            row.update(limit_up_state='unknown', limit_down_state='unknown', continue_day_cnt=None)
            row.pop('board_transition', None)
            continue
        if code in sealed or code in broken:
            row['limit_up_state'] = 'sealed' if code in sealed else 'broken'
            if code in sealed and code in resealed:
                row['board_transition'] = 'resealed'
            height = sealed.get(code, broken.get(code, {})).get('continue_day_cnt', heights.get(code))
            if type(height) is int and height > 0:
                row['continue_day_cnt'] = height
        if code in down:
            row['limit_down_state'] = 'sealed'
