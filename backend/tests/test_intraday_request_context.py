"""Request-only reuse is source/date isolated and never serialized or persisted."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from app.services import intraday_rotation as rotation
from app.services import intraday_narrative as narrative
from test_intraday_sector_metrics import _live, DAY, PRIOR, GENERATION


def test_router_creates_fresh_private_context_per_call(monkeypatch):
    from app.routers import intraday
    from app.services import intraday_brief

    contexts = []
    def read(day, *, read_context):
        assert not read_context
        contexts.append(read_context)
        read_context['original-evidence'] = object()
        return {'date': day, 'snapshots': []}
    def brief(day, *, snapshots, read_context):
        assert read_context is contexts[-1] and read_context.get('original-evidence')
        return {'briefs': [], 'anchors': []}
    monkeypatch.setattr(intraday.intraday_service, 'read_view', read)
    monkeypatch.setattr(intraday_brief, 'read_brief_view', brief)
    first, second = intraday.view(DAY), intraday.view(PRIOR)
    assert contexts[0] is not contexts[1]
    assert set(json.loads(first.body)) == set(json.loads(second.body)) == {'date', 'snapshots', 'briefs', 'anchors'}
    assert first.media_type == second.media_type == 'application/json'


def test_router_response_preserves_json_bytes_without_the_extra_fastapi_encoder_pass(monkeypatch):
    from fastapi import FastAPI
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from app.routers import intraday
    from app.services import intraday_brief
    import fastapi.routing

    snapshot = {'slot': '1000', 'label': '盘中', 'change_pct': 0.0, 'amount': 0,
                'unknown': None, 'complete': True, 'levels': (1, 2)}
    expected = {'date': DAY, 'snapshots': [snapshot], 'briefs': [], 'anchors': []}
    def read(day, *, read_context):
        read_context['not-for-serialization'] = object()
        return {'date': day, 'snapshots': [snapshot]}
    def brief(day, *, snapshots, read_context):
        assert read_context.get('not-for-serialization') is not None
        assert snapshots == [snapshot]
        return {'briefs': [], 'anchors': []}
    monkeypatch.setattr(intraday.intraday_service, 'read_view', read)
    monkeypatch.setattr(intraday_brief, 'read_brief_view', brief)
    original = JSONResponse(jsonable_encoder(expected)).body
    monkeypatch.setattr(fastapi.routing, 'jsonable_encoder',
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unneeded recursive encoder')))
    app = FastAPI(); app.include_router(intraday.router)
    response = TestClient(app).get('/api/v1/market/intraday', params={'date': DAY})
    assert response.status_code == 200 and response.content == original
    assert response.headers['content-type'] == 'application/json'
    assert response.headers['content-length'] == str(len(original))
    assert response.json()['snapshots'][0]['unknown'] is None


def test_member_proofs_reused_only_for_same_original_reference_generation_and_date(monkeypatch):
    from app.services import intraday_brief

    snapshot, reference = _live()
    original, calls = intraday_brief.sector_membership_fingerprint, []
    def fingerprint(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(intraday_brief, 'sector_membership_fingerprint', fingerprint)
    cache = {}
    first = rotation.live_rotation_sectors(snapshot, reference=reference, identity=(GENERATION, PRIOR), member_cache=cache)
    second = rotation.live_rotation_sectors(snapshot, reference=reference, identity=(GENERATION, PRIOR), member_cache=cache)
    assert first == second and len(calls) == 1
    rotation.live_rotation_sectors(snapshot, reference=reference, identity=(GENERATION + '-new', PRIOR), member_cache=cache)
    assert len(calls) == 2
    rotation.live_rotation_sectors({**snapshot, 'date': '2026-11-06'}, reference=reference,
                                   identity=(GENERATION, PRIOR), member_cache=cache)
    assert len(calls) == 3
    changed = deepcopy(reference)
    changed['topics'][0]['instrument_ids'] = ['600001.SH']
    row = rotation.live_rotation_sectors(snapshot, reference=changed, identity=(GENERATION, PRIOR), member_cache=cache)[0]
    assert len(calls) == 4 and row['member_count'] == 1 and row['up_ratio'] == 100


def test_narrative_reuses_same_original_frame_inputs_but_changed_source_time_rechecks(monkeypatch):
    from app.services import intraday_board_changes, intraday_members

    snapshot, reference = _live()
    snapshot.update(id='saved-original', version='intraday-snapshot-v5',
                    member_reference={'generation': GENERATION, 'date': PRIOR})
    inputs = {}
    sector = rotation.live_rotation_sectors(snapshot, reference=reference, identity=(GENERATION, PRIOR), input_cache=inputs)[0]
    frame = {'id': snapshot['id'], 'slot': '1030', 'date': DAY,
             'collected_end_at': snapshot['collected_end_at'], '_raw': snapshot}
    original, calls = intraday_board_changes._quotes, []
    def quotes(value):
        calls.append(value)
        return original(value)
    monkeypatch.setattr(intraday_board_changes, '_quotes', quotes)
    monkeypatch.setattr(intraday_members, '_bound_market', lambda *_: (_ for _ in ()).throw(AssertionError('must reuse checked reference')))
    members = narrative._Members({(GENERATION, PRIOR): reference}, inputs)
    rows = members.stocks(frame, sector)
    assert len(rows) == 2 and not calls
    changed = {**frame, 'collected_end_at': DAY + 'T10:30:11+08:00'}
    rows = narrative._Members({(GENERATION, PRIOR): reference}, inputs).stocks(changed, sector)
    assert len(rows) == 2 and len(calls) == 1


def test_same_request_binding_memo_rechecks_changed_content_sha(monkeypatch):
    from app.services import intraday_board_changes as changes

    snapshot, _ = _live()
    snapshot.update(id='original', version='intraday-snapshot-v5')
    encoded = json.dumps(snapshot)
    row = {'payload_json': encoded, 'content_sha256': sha256(encoded.encode()).hexdigest(),
           'trade_date': DAY, 'slot': '1030', 'snapshot_id': 'original'}
    original, calls = changes._binding, []
    def bind(value, record, **kwargs):
        calls.append(record['content_sha256'])
        return original(value, record, **kwargs)
    monkeypatch.setattr(changes, '_binding', bind)
    cache = {}
    assert changes._read_binding(snapshot, row, cache) is not None
    assert changes._read_binding(snapshot, row, cache) is not None and len(calls) == 1
    row['content_sha256'] = '0' * 64
    assert changes._read_binding(snapshot, row, cache) is None and len(calls) == 2


def test_original_live_body_is_parsed_once_but_binding_does_not_share_display_mutations(monkeypatch):
    from app.services import intraday_board_changes as changes, intraday_service as live
    from test_intraday_board_changes import frame, raw_row

    value = frame('1430', ['000001.SZ'])
    row = raw_row(value)
    expected = changes._binding(value, row)
    original, reads = changes.json.loads, []
    def loads(body):
        reads.append(body)
        return original(body)
    monkeypatch.setattr(changes.json, 'loads', loads)
    cache = {}
    projected = live.decode_snapshot(row, read_context=cache)
    projected['source_checks'][0]['state'] = 'unknown'
    projected['rotation_sectors'] = [{'name': 'only a display field'}]
    assert changes._binding(projected, row, read_cache=cache) == expected
    assert len(reads) == 1
    # Another SQLite row may contain equal bytes, but is not the original row.
    assert changes._binding(projected, dict(row), read_cache=cache) == expected
    assert len(reads) == 2


@pytest.mark.parametrize('damage', ['body', 'digest', 'date', 'id', 'new_signed_body'])
def test_cached_live_record_does_not_skip_integrity_or_identity_checks(damage):
    from fastapi import HTTPException
    from app.services import intraday_board_changes as changes, intraday_service as live
    from test_intraday_board_changes import frame, raw_row

    row = raw_row(frame('1430'))
    cache = {}
    live.decode_snapshot(row, read_context=cache)
    if damage == 'body':
        row['payload_json'] += ' '
    elif damage == 'digest':
        row['content_sha256'] = '0' * 64
    elif damage == 'date':
        row['trade_date'] = '2026-11-06'
    elif damage == 'id':
        row['snapshot_id'] = 'another-capture'
    else:
        changed = json.loads(row['payload_json'])
        changed['date'] = '2026-11-06'
        row['payload_json'] = json.dumps(changed)
        row['content_sha256'] = sha256(row['payload_json'].encode()).hexdigest()
    with pytest.raises(HTTPException) as error:
        live.decode_snapshot(row, read_context=cache)
    assert error.value.status_code == 409


def _frozen_noon():
    from app.services.noon_sector_views import freeze_memberships
    from test_noon_membership_context import payload, topic

    value = payload()
    day = value['date']
    value.update(version='noon-snapshot-v1', collected_start_at=day + 'T11:35:00+08:00',
                 collected_end_at=day + 'T11:35:30+08:00')
    value['sectors'][0].update(latest_price=101, prev_close=100, change_pct=1,
                             member_limit_up_count=1, member_limit_up_state='observed')
    for row in value['stocks']:
        row.update(price_basis='none', currency='CNY')
    value['sector_memberships'] = freeze_memberships(value, [topic()])
    body = json.dumps(value, ensure_ascii=False)
    row = {'trade_date': day, 'snapshot_id': 'original-noon', 'payload_json': body,
           'content_sha256': sha256(body.encode()).hexdigest(),
           'collected_start_at': value['collected_start_at'], 'collected_end_at': value['collected_end_at']}
    return value, row


def test_noon_frame_and_brief_share_only_the_same_checked_row_and_keep_identity_checks(monkeypatch):
    from app.services import intraday_board_changes as changes, intraday_brief as brief, noon_service

    _, row = _frozen_noon()
    monkeypatch.setattr(noon_service, 'retained_empty_event_pools', lambda *_: {})
    original, reads = changes.json.loads, []
    def loads(body):
        reads.append(body)
        return original(body)
    monkeypatch.setattr(changes.json, 'loads', loads)
    cache = {}
    first = changes.noon_frame(row, read_cache=cache)
    payload = brief._verified_noon_payload(row, read_context=cache)
    assert changes.noon_frame(row, read_cache=cache) is first
    assert first['stocks'] is payload['stocks'] and len(reads) == 1
    copied = changes.noon_frame(dict(row), read_cache=cache)
    assert copied == first and copied is not first and len(reads) == 2
    row['collected_end_at'] = row['trade_date'] + 'T11:39:00+08:00'
    with pytest.raises(ValueError, match='身份'):
        brief._verified_noon_payload(row, read_context=cache)
    row['content_sha256'] = '0' * 64
    assert changes.noon_frame(row, read_cache=cache) is None
    with pytest.raises(ValueError, match='校验'):
        brief._verified_noon_payload(row, read_context=cache)


def test_noon_member_proof_is_shared_by_rotation_and_brief_without_erasing_zero_or_unknown(monkeypatch):
    from app.services import noon_sector_views, intraday_brief as brief

    value, _ = _frozen_noon()
    before = deepcopy(value)
    expected = rotation.noon_rotation_sectors(value)
    original, calls = noon_sector_views._members, []
    def members(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(noon_sector_views, '_members', members)
    cache = {}
    result = rotation.noon_rotation_sectors(value, read_context=cache)
    sector = value['sectors'][0]
    proof = rotation.noon_member_proof(value, sector, cache, direct=True)
    quotes = {row['instrument_id']: row for row in value['stocks']}
    metadata = brief._noon_member_metadata(value, sector, quotes, read_context=cache)
    assert proof[0] and not proof[2] and metadata['member_count'] == 2
    assert result == expected and len(calls) == 1 and value == before
    # Member-entry corruption must not hit a previously checked proof.
    value['sector_memberships']['items'][sector['topic_code']]['instrument_ids'].append('600003.SH')
    assert rotation.noon_member_proof(value, sector, cache)[0] is None
    assert len(calls) == 2


@pytest.mark.parametrize('damage', ['member_sha', 'typed_sha', 'missing_quote', 'future_member_date', 'different_generation'])
def test_another_original_noon_payload_rechecks_member_and_date_proofs(damage):
    value, _ = _frozen_noon()
    cache = {}
    rotation.noon_rotation_sectors(value, read_context=cache)
    other = deepcopy(value)
    sector = other['sectors'][0]
    if damage == 'member_sha':
        other['sector_memberships']['items'][sector['topic_code']]['sha256'] = '0' * 64
    elif damage == 'typed_sha':
        other['sector_memberships']['items'][sector['topic_code']]['sha256'] = {'not': 'a checksum'}
    elif damage == 'missing_quote':
        other['stocks'].pop()
    elif damage == 'future_member_date':
        sector['members_observed_date'] = '2099-01-01'
    else:
        other['sector_memberships']['source_snapshot_id'] = 'market-not-the-original'
    expected = rotation.noon_rotation_sectors(other)
    actual = rotation.noon_rotation_sectors(other, read_context=cache)
    assert actual == expected
    assert actual[0]['latest_price'] == 101 and actual[0]['breadth_state'] == 'unknown'


def test_noon_detail_reuses_only_the_same_request_row_and_exact_anchor_identity(monkeypatch):
    from contextlib import contextmanager
    from app.services import intraday_brief as brief, noon_service

    value, _ = _frozen_noon()
    value['indices'] = []
    body = json.dumps(value, ensure_ascii=False)
    row = {'trade_date': value['date'], 'snapshot_id': 'original-noon', 'payload_json': body,
           'content_sha256': sha256(body.encode()).hexdigest(),
           'collected_start_at': value['collected_start_at'], 'collected_end_at': value['collected_end_at']}
    anchor = {'id': row['snapshot_id'], 'slot': 'noon', 'sectors': [],
              'collected_start_at': row['collected_start_at'], 'collected_end_at': row['collected_end_at']}
    reads = []
    class Connection:
        def execute(self, sql, params):
            reads.append(params)
            return self
        def fetchone(self):
            return row
    @contextmanager
    def connect():
        yield Connection()
    monkeypatch.setattr(brief, 'connect', connect)
    monkeypatch.setattr(noon_service, 'retained_empty_event_pools', lambda *_: {})
    baseline = deepcopy(anchor)
    brief._restore_noon_details(baseline, value['date'])
    assert len(reads) == 1 and baseline['rotation_sectors']
    reads.clear()
    shared = {'date': value['date'], 'noon_row': row}
    actual = deepcopy(anchor)
    brief._restore_noon_details(actual, value['date'], read_context=shared)
    assert actual == baseline and not reads
    invalid = {**anchor, 'id': 'not-the-read-row'}
    brief._restore_noon_details(invalid, value['date'], read_context=shared)
    assert 'rotation_sectors' not in invalid and not reads
    # A context for another date is not reusable, even when IDs happen to match.
    brief._restore_noon_details(deepcopy(anchor), value['date'], read_context={**shared, 'date': '2099-01-01'})
    assert len(reads) == 1


def test_close_detail_checks_original_previous_row_cutoff_without_another_database_read(monkeypatch):
    from app.services import intraday_brief as brief, intraday_close as closing
    from test_intraday_board_changes import frame, raw_row

    previous = frame('1430')
    row = {**raw_row(previous), 'status': 'succeeded',
           'finished_at_ms': brief._stamp(DAY + 'T14:31:00+08:00', DAY)}
    context = {'date': DAY, 'published_at': DAY + 'T15:05:00+08:00'}
    received = []
    monkeypatch.setattr(closing, 'closing_data', lambda *_: context)
    monkeypatch.setattr(closing, 'restore_close_details', lambda _anchor, _context, **values: received.append(values))
    monkeypatch.setattr(brief, 'connect', lambda: pytest.fail('the original previous row is already held by this request'))
    shared = {'date': DAY, 'rows': [row]}
    assert brief._restore_close_details({'id': GENERATION}, DAY, read_context=shared) is context
    assert received[-1]['previous']['id'] == previous['id'] and received[-1]['previous_row'] is row
    row['finished_at_ms'] = brief._stamp(DAY + 'T15:06:00+08:00', DAY)
    assert brief._restore_close_details({'id': GENERATION}, DAY, read_context=shared) is context
    assert received[-1]['previous'] is None and received[-1]['previous_row'] is None
    row['finished_at_ms'] = brief._stamp(DAY + 'T14:31:00+08:00', DAY)
    row['content_sha256'] = '0' * 64
    assert brief._restore_close_details({'id': GENERATION}, DAY, read_context=shared) is context
    assert received[-1]['previous'] is None and received[-1]['previous_row'] is None
