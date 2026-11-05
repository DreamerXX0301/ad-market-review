"""Request-only reuse is source/date isolated and never serialized or persisted."""
from copy import deepcopy
from hashlib import sha256
import json

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
    def bind(value, record):
        calls.append(record['content_sha256'])
        return original(value, record)
    monkeypatch.setattr(changes, '_binding', bind)
    cache = {}
    assert changes._read_binding(snapshot, row, cache) is not None
    assert changes._read_binding(snapshot, row, cache) is not None and len(calls) == 1
    row['content_sha256'] = '0' * 64
    assert changes._read_binding(snapshot, row, cache) is None and len(calls) == 2
