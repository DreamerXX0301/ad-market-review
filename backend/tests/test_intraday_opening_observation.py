"""Opening snapshots keep their own clocks and frozen comparison baselines."""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app import core
from app.services import intraday_service as live, intraday_brief as brief, intraday_board_changes as changes
from app.services import intraday_members as members, intraday_stock_detail as details
from app.services import automatic_tasks, source_service, task_service as tasks
from app.services.noon_collection import CHINA

DAY, PRIOR = '2026-11-05', '2026-11-04'
SYMBOL, TOPIC, GENERATION = '000001.SZ', '881001.TI', 'market-' + 'a' * 24


@pytest.mark.parametrize('version,count', [(version, 4) for version in
    (live.LEGACY_VERSION, live.STRENGTH_VERSION, live.FEEDBACK_VERSION, live.POOL_VERSION)]
    + [(live.SIX_SLOT_VERSION, 6), (live.SEVEN_SLOT_VERSION, 7), (live.VERSION, 8),
       ('intraday-snapshot-v999', 0), (None, 0)])
def test_versions_select_exact_frozen_catalogs(version, count):
    slots = live.slots_for_version(version)
    assert len(slots) == count
    observed = datetime.fromisoformat(DAY + 'T09:35:30+08:00')
    slot = live.slot_for(DAY, observed, version=version)
    if version in (live.SEVEN_SLOT_VERSION, live.VERSION):
        assert slot['key'] == '0935' and slot['end'] == '09:38'
    elif version == live.SIX_SLOT_VERSION or count == 0:
        assert slot is None
    else:
        assert slot['key'] == 'opening' and slot['end'] == '10:00'
    assert ('0945' in {row['key'] for row in slots}) is (version == live.VERSION)
    assert live.V6_SLOTS[1:] == live.SLOTS[2:] == live.V5_SLOTS


@pytest.mark.parametrize('slot,clock,creation,capture', [
    ('0935', '09:34:59', False, False), ('0935', '09:35:00', True, True),
    ('0935', '09:35:59', True, True), ('0935', '09:36:00', False, True),
    ('0935', '09:37:59', False, True), ('0935', '09:38:00', False, False),
    ('0945', '09:44:59', False, False), ('0945', '09:45:00', True, True),
    ('0945', '09:45:59', True, True), ('0945', '09:46:00', False, True),
    ('0945', '09:47:59', False, True), ('0945', '09:48:00', False, False),
])
def test_opening_windows_have_one_minute_creation_and_three_minute_capture(slot, clock, creation, capture):
    now = datetime.fromisoformat(DAY + 'T' + clock + '+08:00')
    assert (live.slot_for(DAY, now, creation=True) or {}).get('key') == (slot if creation else None)
    assert (live.slot_for(DAY, now) or {}).get('key') == (slot if capture else None)
    assert live.slot_for(PRIOR, now) is None


def test_old_version_cannot_accept_a_new_slot_or_legacy_window():
    for version, slot in [(live.SIX_SLOT_VERSION, live.SLOTS[0]), (live.SEVEN_SLOT_VERSION, live.SLOTS[1]),
                          (live.VERSION, live.LEGACY_SLOTS[0]), ('intraday-snapshot-v999', live.SLOTS[0])]:
        with pytest.raises(ValueError, match='版本与冻结时段'):
            live._enqueue(None, 'invalid', DAY, slot, datetime.now(CHINA), version=version)
    for version, slot in [(live.SIX_SLOT_VERSION, '0935'), (live.SEVEN_SLOT_VERSION, '0945')]:
        snapshot = {'version': version, 'id': 'old', 'date': DAY, 'slot': slot}
        with pytest.raises(HTTPException, match='身份校验'):
            live.decode_snapshot(_row(snapshot))


def test_cumulative_brief_keeps_auction_then_real_opening_and_excludes_late_or_future_sources():
    from test_intraday_brief import _projection
    records = {'auction': {'time': 925, 'ready': 926}, '0935': {'time': 935, 'ready': 936},
               '1000': {'time': 1000, 'ready': 1001}, '1030': {'time': 1030, 'ready': 1031}}
    selected = brief._preceding(records, '0935')
    assert [slot for slot, _ in selected] == ['auction', '0935']
    value = brief._brief('0935', [(slot, _projection(slot, 4)) for slot, _ in selected])
    assert [row['slot'] for row in value['context']] == ['auction', '0935']
    assert all(row['status'] == 'complete' for row in value['context'])
    assert '10:00' not in value['body'] and '10:30' not in value['body']
    records['0935']['ready'] = 1002
    selected = brief._preceding(records, '1000')
    assert [slot for slot, _ in selected] == ['auction', '1000']
    value = brief._brief('1000', [(slot, _projection(slot, 4)) for slot, _ in selected])
    assert value['context'][1]['slot'] == '0935' and value['context'][1]['status'] == 'missing'
    assert {row['slot'] for row in value['evidence']} == {'auction', '1000'}


@pytest.mark.parametrize('version,capture_tracking,expected_baseline', [
    (live.SEVEN_SLOT_VERSION, False, '0935'),
    (live.VERSION, True, '0945'),
    (live.VERSION, False, '0945'),
])
def test_worker_keeps_opening_windows_and_frozen_baselines(tmp_path, monkeypatch, version,
                                                          capture_tracking, expected_baseline):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR'):
        monkeypatch.delenv(key, raising=False)
    core.ensure_ready()
    clock = [datetime.fromisoformat(DAY + 'T09:35:07+08:00')]
    monkeypatch.setattr(live, 'now_china', lambda: clock[0])
    monkeypatch.setattr(tasks, 'now_ms', lambda: int(clock[0].timestamp() * 1000))
    monkeypatch.setattr(live, '_configured', lambda _: True)
    monkeypatch.setattr(tasks, 'initialization_state', lambda _: {'main_published': True})
    reference = {'calendar': [PRIOR, DAY], 'master': {SYMBOL: {'kind': 'stock', 'list_date': '2000-01-01'}},
                 'industry_catalog': [{'thscode': TOPIC, 'name': '合成测试行业'}], 'concept_catalog': [],
                 'topics': [{'topic_code': TOPIC, 'kind': 'industry', 'source': 'fuyao',
                             'observed_date': PRIOR, 'members_complete': True, 'instrument_ids': [SYMBOL]}]}
    market = SimpleNamespace(reference=lambda: reference, calendar=lambda: [PRIOR, DAY],
        status=lambda: {'ready': True, 'snapshot_id': GENERATION, 'data_end': PRIOR},
        instruments=lambda **_: [{'instrument_id': SYMBOL, 'kind': 'stock', 'name': '合成测试股'}],
        history_for_instruments=lambda symbols, **_: {code: [{'date': PRIOR, 'instrument_id': code,
            'close': 10, 'volume': 100, 'price_basis': 'none', 'source': 'fuyao'}] for code in symbols})
    for module in (live, members, details):
        monkeypatch.setattr(module, 'get_market', lambda: market)
    monkeypatch.setattr(live, '_prior_closing_cohort', lambda *args, **kwargs:
                        {'date': PRIOR, 'complete': False, 'items': [], 'broken': {'date': PRIOR, 'complete': False, 'items': []}})
    monkeypatch.setattr(details, 'load_supplement_with_fingerprint', lambda *args, **kwargs: ({}, 'fixture'))
    metric_calls = []
    def metrics(actual, day, symbols, *_args, **_kwargs):
        metric_calls.append((day, list(symbols)))
        return {code: {'change_30d_pct': 3} for code in symbols}
    monkeypatch.setattr(details, 'member_metrics', metrics)
    monkeypatch.setattr(members, '_topic_members', lambda *args, **kwargs: ([SYMBOL], PRIOR, True))
    monkeypatch.setattr(members, 'suspensions_for_date', lambda _: {})
    requests = []
    def request(endpoint, params, before_attempt, **kwargs):
        before_attempt()
        requests.append((clock[0].strftime('%H:%M'), endpoint, deepcopy(params)))
        stamp = int(clock[0].timestamp() * 1000)
        if endpoint.endswith('trading-days'):
            rows = [{'date': value.replace('-', '')} for value in (PRIOR, DAY)]
        elif endpoint.endswith('-pool'):
            return {'code': 0, 'data': {'item': [], 'timestamp': stamp,
                'pagination': {'total': 0, 'page': 1, 'size': 200, 'pages': 0}}}
        else:
            codes = params.get('thscodes', SYMBOL).split(',')
            rows = [{'thscode': code, 'last_price': 10.1, 'prev_price': 10, 'open_price': 10,
                     'high_price': 10.1, 'low_price': 10, 'turnover': 100, 'price_change_ratio_pct': 1} for code in codes]
        return {'code': 0, 'data': {'item': rows, 'timestamp': stamp, 'total': len(rows)}}
    monkeypatch.setattr(source_service, 'client', lambda _: SimpleNamespace(request=request))
    def submit(slot_key, request_key):
        if version == live.VERSION:
            return live.create_capture({'date': DAY, 'request_key': request_key})
        slot = next(item for item in live.slots_for_version(version) if item['key'] == slot_key)
        with core.connect() as connection:
            identifier = live._enqueue(connection, request_key, DAY, slot, clock[0], version=version)
        return tasks.task_view(identifier)

    def execute():
        job = tasks.claim_next('opening-test')
        result = live.execute_capture(job)
        tasks.finish(job['job_id'], 'partial', lease_owner=job['lease_owner'])
        return result

    created = submit('0935', '0935-only')
    if version == live.VERSION:
        assert submit('0935', '0935-only')['job_id'] == created['job_id']
    assert requests == []
    frozen = created['parameters']
    assert frozen['version'] == version and frozen['slot'] == '0935' and frozen['request_budget'] == 32
    result = execute()
    with core.connect() as connection:
        original = live._saved_slot(connection, DAY, '0935')
        assert live._saved_slot(connection, DAY, '1000') is None
    assert original['version'] == version and original['scheduled_at'] == DAY + 'T09:35:00+08:00'
    assert original['summary']['comparison_slot'] is None
    assert len(requests) == 7 and all(at == '09:35' for at, _, _ in requests)
    assert [path for _, path, _ in requests].count('/api/a-share/prices/snapshot') == 1
    with pytest.raises(HTTPException):
        live.create_capture({'date': DAY, 'request_key': 'duplicate-0935'})
    before = len(requests)
    member_page = members.read_members.__wrapped__(DAY, '0935', result['snapshot_id'], TOPIC)
    history = details.read_history.__wrapped__(DAY, '0935', SYMBOL)
    assert member_page['items'][0]['instrument_id'] == SYMBOL
    assert member_page['items'][0]['change_pct'] == pytest.approx(1)
    assert history['as_of'] == PRIOR and history['items'][SYMBOL]['change_30d_pct'] == 3
    assert metric_calls == [(PRIOR, [SYMBOL])] and len(requests) == before

    clock[0] = clock[0].replace(minute=40)
    assert '0945' in {slot['key'] for slot in live.read_view(DAY)['slots']}
    clock[0] = clock[0].replace(minute=45)
    tracking = None
    if capture_tracking:
        configured = live.configure_automation({'enabled': True, 'expected_revision': 0})
        receipt = {'date': DAY, 'state': 'ready', 'days': [DAY], 'source_id': 'fuyao', 'source_sha256': 'a' * 64}
        with core.connect() as connection:
            automatic_tasks._save(connection, live.AUTOMATION_CALENDAR_KEY, receipt)
        identifier = live.tick_automatic()
        assert live.tick_automatic() is None and len(requests) == before
        task = tasks.task_view(identifier)
        assert task['request_key'] == f'intraday-auto:{DAY}:0945'
        assert task['parameters']['slot'] == '0945' and task['parameters']['version'] == live.VERSION
        assert task['parameters']['automation'] == {'revision': configured['revision'], 'calendar_sha256': 'a' * 64}
        result = execute()
        with core.connect() as connection:
            tracking = live._saved_slot(connection, DAY, '0945')
        assert tracking['scheduled_at'] == DAY + 'T09:45:00+08:00'
        assert tracking['summary']['comparison_snapshot_id'] == original['id']
        tracking_requests = requests[before:]
        assert len(tracking_requests) == 6 and all(at == '09:45' for at, _, _ in tracking_requests)
        assert not any(endpoint.endswith('trading-days') for _, endpoint, _ in tracking_requests)
        assert members.read_members.__wrapped__(DAY, '0945', result['snapshot_id'], TOPIC)['items'][0]['instrument_id'] == SYMBOL
        assert details.read_history.__wrapped__(DAY, '0945', SYMBOL)['as_of'] == PRIOR
        assert details.read_quote(result['snapshot_id'], SYMBOL)['quote']['latest_price'] == 10.1
        assert len(requests) == before + len(tracking_requests)

    clock[0] = clock[0].replace(hour=10, minute=0)
    submit('1000', '1000-after-opening')
    execute()
    with core.connect() as connection:
        current = live._saved_slot(connection, DAY, '1000')
    previous = original if version == live.SEVEN_SLOT_VERSION else tracking
    assert current['summary']['comparison_slot'] == (expected_baseline if previous else None)
    projection = changes.build_changes(current, previous,
        current_row=_row(current), previous_row=_row(previous) if previous else None)
    assert projection['baseline_slot'] == expected_baseline
    assert projection['groups']['new_sealed']['state'] == ('observed' if previous else 'unknown')
    assert projection['groups']['new_sealed']['count'] == (0 if previous else None)
    wrong_previous = original if version == live.VERSION else None
    missing = changes.build_changes(current, wrong_previous,
        current_row=_row(current), previous_row=_row(wrong_previous) if wrong_previous else None)
    assert missing['state'] == 'unknown' and missing['baseline_slot'] == expected_baseline
    assert all(group['reason_code'] == 'missing_adjacent_snapshot' for group in missing['groups'].values())
    clock[0] = clock[0].replace(hour=15, minute=0)
    assert ('0945' in {slot['key'] for slot in live.read_view(DAY)['slots']}) is (version == live.VERSION)
    clock[0] = clock[0].replace(day=6)
    assert ('0945' in {slot['key'] for slot in live.read_view(DAY)['slots']}) is (version == live.VERSION)
    clock[0] = clock[0].replace(day=5)
    # Explicitly stopped captures keep their occupied slot and never reach the adapter.
    for hour, minute, status, failure in [(10, 30, 'paused', tasks.TaskPaused),
                                           (11, 0, 'cancelled', tasks.TaskCancelled)]:
        clock[0] = clock[0].replace(hour=hour, minute=minute)
        created = live.create_capture({'date': DAY, 'request_key': status})
        job = tasks.claim_next('opening-test')
        tasks.change_task(created['job_id'], 'pause' if status == 'paused' else 'cancel')
        before = len(requests)
        with pytest.raises(failure):
            live.execute_capture(job)
        assert len(requests) == before
        with pytest.raises(HTTPException):
            live.create_capture({'date': DAY, 'request_key': 'repeat-' + status})


def _row(snapshot):
    raw = json.dumps(snapshot)
    return {'trade_date': snapshot['date'], 'slot': snapshot['slot'], 'snapshot_id': snapshot['id'],
            'payload_json': raw, 'content_sha256': sha256(raw.encode()).hexdigest()}
