"""Project at most six verified, same-day saved intraday snapshots for AI."""
from datetime import date, datetime, time
import json

from fastapi import HTTPException

from app.core import dumps
from app.services.ai_read_tools import _connect, _gap, _paths, _pick_fields
from app.services.auction_followup import model_input_allowed
from app.services.intraday_service import MAX_PAYLOAD, SLOTS, decode_snapshot
from app.services.sync_clock import CHINA

_SLOTS = {item['key']: item for item in SLOTS}
_SOURCE = 'saved_intraday_observation'
_SCOPE = '同一采集窗口内已核验的报价和事件。涨跌幅相对昨收，变化为百分点，成交额为人民币元。'
_MARKET = ('up', 'down', 'flat', 'valid', 'observed')
_STOCK = ('instrument_id', 'name', 'latest_price', 'change_pct', 'change_pp', 'amount',
          'amount_delta', 'limit_up_state', 'continue_day_cnt', 'board_count', 'previous_board_count', 'state')
_DIRECTION = ('instrument_id', 'name', 'kind', 'signal', 'change_pct', 'change_pp',
              'amount_delta', 'member_date', 'up_ratio', 'limit_up_count', 'limit_up_count_state',
              'broken_count', 'broken_count_state')
_AMOUNT_COMPARISON = ('previous_amount', 'comparison_amount', 'change_from_previous',
                      'previous_date', 'comparable_count')
_INTERVAL_AMOUNT = ('amount', *_AMOUNT_COMPARISON, 'previous_comparable_count', 'scope', 'state')


def _matches(row, query):
    return not query or any(query in str(row.get(key) or '').casefold()
                            for key in ('instrument_id', 'name'))


def _source_rows(connection, day, slots):
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE name IN ('market_intraday_capture','job')")}
    if tables != {'market_intraday_capture', 'job'}:
        return {}
    placeholders = ','.join('?' for _ in slots)
    return {row['slot']: dict(row) for row in connection.execute(
        '''SELECT c.trade_date,c.slot,c.job_id,c.snapshot_id,c.content_sha256,j.status,j.finished_at_ms,
                  length(CAST(c.payload_json AS BLOB)) AS payload_bytes
           FROM market_intraday_capture c JOIN job j USING(job_id)
           WHERE c.trade_date=? AND c.slot IN (''' + placeholders + ')', (day, *slots))}


def _calendar_state(connection, day):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='platform_setting'").fetchone():
        return None
    rows = connection.execute('''SELECT value_json FROM platform_setting WHERE key IN
        ('intraday-automation-calendar-v1','automatic-review-calendar-v1')
        AND length(CAST(value_json AS BLOB))<=131072
        ORDER BY key DESC''')
    for row in rows:
        try:
            value = json.loads(row[0])
        except (ValueError, TypeError):
            continue
        if not isinstance(value, dict):
            continue
        days = value.get('days', [])
        sha = value.get('source_sha256', '')
        if (value.get('state') != 'ready' or value.get('source_id') != 'fuyao'
                or not isinstance(days, list) or not 0 < len(days) <= 4096
                or not isinstance(sha, str) or len(sha) != 64
                or any(char not in '0123456789abcdef' for char in sha)):
            continue
        try:
            valid = all(isinstance(item, str) and date.fromisoformat(item).isoformat() == item for item in days)
        except ValueError:
            valid = False
        if valid and min(days) <= day <= max(days):
            return 'trading_day' if day in days else 'rest_day'
    return None


def _clock(value, day):
    observed = datetime.fromisoformat(value)
    if observed.tzinfo is None:
        raise ValueError('盘中来源时间缺少时区')
    observed = observed.astimezone(CHINA)
    if observed.date().isoformat() != day:
        raise ValueError('盘中来源时点不属于所选日')
    return observed


def _read_snapshot(connection, metadata, day, slot, now):
    size = metadata['payload_bytes']
    if type(size) is not int or not 0 < size <= MAX_PAYLOAD:
        raise ValueError('盘中正文超出有界范围')
    row = connection.execute('''SELECT trade_date,slot,snapshot_id,content_sha256,payload_json
        FROM market_intraday_capture WHERE trade_date=? AND slot=? AND snapshot_id=? AND content_sha256=?''',
        (day, slot, metadata['snapshot_id'], metadata['content_sha256'])).fetchone()
    if row is None:
        raise ValueError('盘中快照身份变化')
    snapshot = decode_snapshot(row)
    if not model_input_allowed(snapshot):
        raise ValueError('盘中材料禁止AI处理')
    start, end = (_clock(snapshot[field], day) for field in ('collected_start_at', 'collected_end_at'))
    window = _SLOTS[slot]
    finished = metadata['finished_at_ms']
    if (not time.fromisoformat(window['start']) <= start.time() <= end.time() < time.fromisoformat(window['end'])
            or type(finished) is not int or not int(end.timestamp() * 1000) <= finished <= int(now.timestamp() * 1000)):
        raise ValueError('盘中采集时间与所选时段不符')
    return snapshot


def _stock_samples(rows, query, limit):
    selected = {}
    for row in rows:
        code = row.get('instrument_id')
        if code and code not in selected and _matches(row, query):
            selected[code] = _pick_fields(row, _STOCK)
    return list(selected.values())[:limit]


def _direction_samples(snapshot, query, limit, stock_limit):
    from app.services.intraday_discovery import build_intraday_discovery

    discovery = build_intraday_discovery(snapshot)
    directions = []
    for row in discovery['directions']:
        name_match = _matches(row, query)
        stocks = _stock_samples(row.get('stocks', ()), '' if name_match else query, stock_limit)
        if name_match or stocks:
            directions.append({**_pick_fields(row, _DIRECTION), 'stocks': stocks})
    diverse = {}
    for row in directions:
        diverse.setdefault(row['signal'], row)
    ranked = [*diverse.values(), *(row for row in directions if row is not diverse[row['signal']])]
    return {**_pick_fields(discovery, ('state', 'reason', 'scope', 'baseline_slot')),
            'matching_direction_count': len(directions), 'directions': ranked[:limit],
            'omitted_matching_directions': max(0, len(directions) - limit)}


def _project(snapshot, query, detailed):
    market = snapshot.get('market_pulse', {})
    summary = snapshot.get('summary', {})
    ladder = snapshot.get('ladder', {})
    broken = ladder.get('broken_board', {})
    feedback = snapshot.get('relay_feedback', {})
    top = max((group.get('board_count', 0) for group in ladder.get('groups', ())), default=None)
    leaders = next((group.get('items', ()) for group in ladder.get('groups', ())
                    if group.get('board_count') == top), ())
    industry = {row.get('instrument_id'): row for row in
                [*snapshot.get('strong_sectors', ()), *snapshot.get('limit_up_sectors', ())]}
    sectors = []
    for row in snapshot.get('sectors', ()):
        if _matches(row, query):
            sectors.append(_pick_fields({**industry.get(row.get('instrument_id'), {}), **row}, _DIRECTION))
    direction_limit, stock_limit = (6, 3) if detailed else (4, 1)
    samples = [*leaders, *snapshot.get('candidates', ()), *snapshot.get('sealed_stocks', ()),
               *broken.get('items', ()), *snapshot.get('stocks', ())]
    if query:
        # The compact input retains verified quotes for exact codes. Names in
        # older or compact snapshots may be unavailable beyond the saved lists.
        from app.services.intraday_analysis import _rank
        exact = [row for row in snapshot.get('comparison_input', {}).get('stocks', ())
                 if row.get('currency') == 'CNY' and row.get('price_basis') == 'none'
                 and query in (row.get('instrument_id', '').casefold().split('.')[0],
                               row.get('instrument_id', '').casefold())]
        if len(exact) == 1:
            samples.extend(_rank(exact))
    turnover = market.get('turnover') or {}
    indices = []
    for row in market.get('indices', ())[:4]:
        item = _pick_fields(row, ('instrument_id', 'name', 'change_pct', 'amount'))
        if detailed:
            for key, fields in (('amount_comparison', _AMOUNT_COMPARISON),
                                ('interval_amount', _INTERVAL_AMOUNT)):
                if evidence := _pick_fields(row.get(key), fields):
                    item[key] = evidence
        indices.append(item)
    data = {
        'scope': _SCOPE, 'snapshot_quality': snapshot.get('quality'),
        'baseline_slot': summary.get('comparison_slot'),
        'breadth': _pick_fields(market.get('breadth'), _MARKET),
        'turnover': _pick_fields(turnover, ('amount', 'valid')),
        'interval_amount': _pick_fields(summary.get('interval_amount'), _INTERVAL_AMOUNT),
        'indices': indices,
        'ladder': {**_pick_fields(ladder, ('state', 'sealed_count', 'height_state', 'highest_board',
                    'first_board_count', 'multi_board_count', 'unknown_height_count')),
                   'highest_board_samples': _stock_samples(leaders, query, stock_limit),
                   'broken_board': {**_pick_fields(broken, ('state', 'count', 'conflict_count')),
                                    'samples': _stock_samples(broken.get('items', ()), query, stock_limit)}},
        'previous_limit_up_feedback': _pick_fields(feedback.get('prior_session'), (
            'date', 'state', 'total', 'priced', 'average_change_pct', 'promotion_count', 'promotion_state')),
        'same_day_limit_up_feedback': _pick_fields(feedback.get('same_day'), (
            'state', 'label', 'collected_end_at', 'held_count', 'entered_count', 'absent_count',
            'held_state', 'entered_state', 'absent_state')),
        'direction_discovery': _direction_samples(snapshot, query, direction_limit, stock_limit),
        'sector_samples': sectors[:direction_limit], 'matching_sector_sample_count': len(sectors),
        'stock_samples': _stock_samples(samples, query, stock_limit),
        'sample_note': '方向和个股只返回已存资料中的有限样本，名称或代码未命中时不能认定全市场没有。',
    }
    if comparison := _pick_fields(turnover.get('amount_comparison'), _AMOUNT_COMPARISON):
        data['turnover']['amount_comparison'] = comparison
    # Each slot keeps its date and essential facts in a six-slot reply.
    cap = (10 if detailed else 3) * 1024
    discovery = data['direction_discovery']
    while len(dumps(data).encode()) > cap:
        for key in ('sector_samples', 'stock_samples'):
            if data[key]:
                data[key].pop()
                break
        else:
            if not discovery['directions']:
                raise ValueError('必要时点摘要超出容量')
            discovery['directions'].pop()
            discovery['omitted_matching_directions'] += 1
    return data


def _missing_state(record, day, slot, now, calendar):
    if record and record['status'] in ('queued', 'running'):
        return 'collecting', '该时点任务仍在采集'
    if record and record['status'] in ('failed', 'cancelled', 'paused'):
        return 'not_collected', '该时点任务未完成留存'
    if calendar == 'rest_day':
        return 'rest_day', '已核验交易日历明确为休市日'
    due = datetime.combine(date.fromisoformat(day), time.fromisoformat(_SLOTS[slot]['start']), CHINA)
    if now < due:
        return 'not_due', '该观察时点尚未到'
    if day == now.date().isoformat() or calendar == 'trading_day':
        return 'not_collected', '该时点尚无已保存记录'
    return 'unknown', '无该时点记录，且没有覆盖该日的已核验交易日历'


def fill_intraday(result, day, slot, query, now):
    slots = tuple(_SLOTS) if slot == 'all' else (slot,)
    source = None
    calendar = None
    phase_results = {}
    for label, path in _paths():
        connection = _connect(path)
        if connection is None:
            continue
        try:
            connection.execute('BEGIN')
            records = _source_rows(connection, day, slots)
            source_phases = {}
            if calendar is None:
                calendar = _calendar_state(connection, day)
            for key in slots:
                record = records.get(key)
                if not record or not record['snapshot_id'] or record['status'] not in ('succeeded', 'partial'):
                    if record:
                        source_phases[key] = _missing_state(record, day, key, now, calendar)
                    continue
                try:
                    value = _read_snapshot(connection, record, day, key, now)
                    data = _project(value, query.casefold(), slot != 'all')
                except (ValueError, TypeError, KeyError, HTTPException):
                    source_phases[key] = ('unknown', '该时点快照未通过身份、时间或AI处理权限核验')
                    continue
                identifier = f'intraday:{key}'
                result['evidence'].append({'evidence_id': identifier, 'source': _SOURCE, 'kind': identifier,
                    'title': _SLOTS[key]['start'] + ' ' + _SLOTS[key]['label'],
                    'dates': {'trade_date': day, 'collected_end_at': value['collected_end_at']}, 'data': data})
                result['source_bindings'][key] = _pick_fields(record, ('snapshot_id', 'content_sha256', 'finished_at_ms'))
                source_phases[key] = ('available' if value.get('quality') == 'complete' else 'partial',
                                      '已核验本时点保存记录')
            if result['evidence']:
                source = label
                calendar = 'trading_day'
                phase_results = source_phases
                break
            phase_results = {**source_phases, **phase_results}
        finally:
            connection.close()
    phases = {}
    for key in slots:
        state, reason = phase_results.get(key) or _missing_state(None, day, key, now, calendar)
        phases[key] = {'phase_state': state, 'time': _SLOTS[key]['start'], 'reason': reason}
        if state in ('unknown', 'not_collected', 'rest_day'):
            _gap(result, 'intraday:' + key, reason, state)
    result['summary'] = {'phases': phases, 'query': query, 'scope': _SCOPE, 'calendar_state': calendar or 'unknown'}
    if source:
        result['source'] = [source]
    states = [phase['phase_state'] for phase in phases.values()]
    result['state'] = ('available' if all(state == 'available' for state in states)
                       else 'partial' if result['evidence']
                       else states[0] if len(set(states)) == 1 else 'unknown')
