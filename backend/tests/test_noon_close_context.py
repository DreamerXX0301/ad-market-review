"""Freeze only previously-known close lists; independent in-memory SQLite."""
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from datetime import datetime
import json
from pathlib import Path
import sqlite3

import pytest

from app.core import dumps
from app.services import noon_close_context as context
from app.services.noon_close_analysis import build_close_review
from app.services.noon_workbench import build_workbench
from test_noon_close_analysis import fixture, DAY
from test_workspace import ws

NEXT = '2026-10-29'


@pytest.fixture
def ledger(monkeypatch):
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA recursive_triggers=ON')
    db.execute('PRAGMA busy_timeout=10000')
    db.create_function('noon_purge_allowed', 1, lambda _: 0)
    db.execute('CREATE TABLE job(job_id TEXT PRIMARY KEY)')
    migrations = Path(__file__).resolve().parents[1] / 'app/migrations'
    db.executescript((migrations/'027_noon_snapshots.sql').read_text())
    db.executescript((migrations/'029_noon_close_reviews.sql').read_text())
    db.execute("INSERT INTO job VALUES('capture')")
    db.execute("INSERT INTO job VALUES('increment')")
    noon, _, _ = fixture()
    encoded = dumps(noon); noon_sha = sha256(encoded.encode()).hexdigest()
    db.execute('INSERT INTO market_noon_capture VALUES(?,?,?,?)', (DAY, 'capture', 'request', 1))
    db.execute('INSERT INTO market_noon_snapshot VALUES(?,?,?,?,?,?,?,?,?,?)',
        ('noon_fixture', DAY, 'capture', DAY+'T12:31:00+08:00', DAY+'T12:32:00+08:00',
         1, 'complete', noon_sha, encoded, '[]'))
    db.commit()
    @contextmanager
    def connection():
        try:
            yield db
        finally:
            if db.in_transaction:
                db.rollback()
    monkeypatch.setattr(context, 'connect', connection)
    yield db, noon_sha
    db.close()


def retain(ledger, *, revision=1, generated=1000, update=None, closing_update=None):
    db, noon_sha = ledger
    noon, closing, ids = fixture()
    if revision != 1:
        closing['fixture_revision'] = revision
        closing['stocks'][0].update(close=11.01, change_pct=10.1)
        closing['events']['limit_up']['items'][0]['previous_close'] = 11.01
    if closing_update:
        closing_update(closing)
    value = build_close_review(noon, closing)
    if update:
        update(value)
    items = sorted(value.pop('items'), key=lambda row: row['instrument_id'])
    item_jsons = [dumps(row) for row in items]
    inputs = dumps(closing); provenance = dumps({'source': 'synthetic', 'known_at_ms': generated})
    value.update(date=DAY, items_count=len(items),
                 items_sha256=sha256(('['+','.join(item_jsons)+']').encode()).hexdigest(),
                 provenance_sha256=sha256(provenance.encode()).hexdigest())
    encoded = dumps(value)
    identifier = 'close_fixture_' + str(revision)
    with db:
        db.execute('''INSERT INTO market_noon_close_review
            (review_id,snapshot_id,trade_date,revision,rule_version,generated_at_ms,source_job_id,source_snapshot_id,
             noon_sha256,close_input_sha256,result_sha256,state,backfilled,input_json,provenance_json,result_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (identifier, 'noon_fixture', DAY, revision, 'noon-close-v1', generated, 'increment',
             'synthetic-generation', noon_sha, sha256(inputs.encode()).hexdigest(), sha256(encoded.encode()).hexdigest(),
             value['state'], 0, inputs, provenance, encoded))
        for row, raw in zip(items, item_jsons):
            db.execute('''INSERT INTO market_noon_close_item VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (identifier, 'noon_fixture', row['instrument_id'], row['name'], row['board'], row['transition'],
                 row['final_board_count'], row['afternoon_change_pct'], row['close_change_pct'], row['noon_change_pct'],
                 row['close_amount'], dumps(row['sections']), dumps(row['classification_keys']), raw, sha256(raw.encode()).hexdigest()))
    return identifier, ids


def next_noon(frozen):
    _, _, ids = fixture()
    return {'date': NEXT, 'calendar': {'verified': True, 'previous_day': DAY},
        'stocks': [{'instrument_id': ids[2], 'name': '次日合成', 'latest_price': 11, 'change_pct': 0},
                   {'instrument_id': ids[5], 'latest_price': 11.1, 'change_pct': 1}],
        'previous_review': {'date': DAY, 'known_at_ms': 3000, 'close_review_summary': frozen,
            'event_cohorts': {'limit_up': {'date': DAY, 'state': 'observed_sample', 'source_count': 1,
                'verified_count': 1, 'items': [{'instrument_id': ids[2], 'previous_board_count': 3}]}}},
        'events': {key: {'state': 'observed', 'items': []} for key in ('limit_up', 'limit_break', 'limit_down')}}


def test_freeze_exact_prior_revision_known_before_capture_and_do_not_pin_generation(ledger):
    first, ids = retain(ledger, generated=1000)
    retain(ledger, revision=2, generated=5000)
    db, _ = ledger
    before = list(db.iterdump())
    frozen = context.freeze_previous_close_summary(DAY, 3000)
    assert frozen['review_id'] == first and frozen['generated_at_ms'] == 1000
    assert frozen['state'] == 'retained' and frozen['items']
    assert 'source_snapshot_id' not in frozen and 'snapshot_id' not in frozen and 'source_job_id' not in frozen
    assert 'synthetic-generation' not in dumps(frozen)
    original = deepcopy(frozen)
    later = context.freeze_previous_close_summary(DAY, 6000)
    assert later['review_id'] != first and later['generated_at_ms'] == 5000
    assert frozen == original
    assert list(db.iterdump()) == before
    assert {row['instrument_id'] for row in frozen['items']} == {ids[1], ids[2], ids[5], ids[8]}


def test_no_review_before_capture_or_other_day_never_falls_back(ledger):
    retain(ledger, generated=5000)
    assert context.freeze_previous_close_summary(DAY, 3000)['state'] == 'unavailable'
    assert context.freeze_previous_close_summary('2026-10-29', 6000)['groups'] == []
    assert context.freeze_previous_close_summary('2026-10-27', 6000)['items'] == []


def test_corrupt_latest_review_not_replaced_by_an_older_favorable_one(ledger):
    retain(ledger, generated=1000)
    latest, _ = retain(ledger, revision=2, generated=2000)
    db, _ = ledger
    db.execute('DROP TRIGGER retain_noon_close_item_update')
    db.execute('UPDATE market_noon_close_item SET item_json=? WHERE review_id=? AND instrument_id=?',
               ('{}', latest, '600003.SH'))
    db.commit()
    frozen = context.freeze_previous_close_summary(DAY, 3000)
    assert frozen['state'] == 'unavailable' and frozen['review_id'] is None
    assert 'SHA' in frozen['reason']


def test_bounded_copy_preserves_source_counts_and_omission_as_unknown(ledger, monkeypatch):
    retain(ledger)
    monkeypatch.setattr(context, 'MAX_ITEMS', 1)
    frozen = context.freeze_previous_close_summary(DAY, 3000)
    assert frozen['state'] == 'partial'
    assert len(frozen['items']) == 1
    assert frozen['coverage']['omitted_items'] == 3
    assert any(group['omitted_count'] for group in frozen['groups'])
    assert len(dumps(frozen).encode()) < context.MAX_BYTES
    feedback = build_workbench(next_noon(frozen))['previous_close_feedback']
    assert feedback['state'] == 'partial'
    assert all(group['unknown_count'] == group['source_count'] - group['valid_count'] for group in feedback['groups'])


def test_next_noon_feedback_preserves_real_zero_height_and_original_membership(ledger):
    retain(ledger)
    frozen = context.freeze_previous_close_summary(DAY, 3000)
    payload = next_noon(frozen)
    before = deepcopy(payload)
    feedback = build_workbench(payload, membership_context={'stock_classifications': {
        '600003.SH': {'state': 'observed_sample', 'industries': [{'name': '今日归属不得替换'}]}}})['previous_close_feedback']
    assert payload == before
    groups = {group['key']: group for group in feedback['groups']}
    back = groups['resealed']
    assert back['source_count'] == 1 and back['valid_count'] == 1
    assert back['up'] == 0 and back['down'] == 0 and back['flat'] == 1 and back['mean_pct'] == 0
    row = back['items'][0]
    assert row['previous_board_count'] == 3
    assert row['industries'][0]['name'] == '合成行业'
    assert row['industries'][0]['members_observed_date'] == '2026-10-27'
    assert groups['opened']['unknown_count'] == 2
    assert groups['opened']['mean_pct'] is None
    assert groups['new_limit_up']['source_count'] == 2 and groups['new_limit_up']['valid_count'] == 2


def test_missing_frozen_review_does_not_disable_normal_yesterday_feedback():
    payload = next_noon({'state': 'unavailable', 'reason': '尚未生成'})
    workbench = build_workbench(payload)
    assert workbench['previous_close_feedback']['state'] == 'unavailable'
    assert workbench['previous_close_feedback']['groups'] == []
    assert workbench['previous_feedback']['source_count'] == 1
    assert workbench['previous_feedback']['groups'][2]['count'] == 1


def test_future_or_wrong_date_frozen_result_rejected_without_requery(ledger, monkeypatch):
    retain(ledger)
    frozen = context.freeze_previous_close_summary(DAY, 3000)
    monkeypatch.setattr(context, 'connect', lambda: (_ for _ in ()).throw(AssertionError('historical GET must not query review')))
    payload = next_noon(frozen)
    frozen['generated_at_ms'] = 4000
    assert build_workbench(payload)['previous_close_feedback']['state'] == 'unavailable'
    frozen['generated_at_ms'] = 1000
    frozen['date'] = '2026-10-27'
    assert build_workbench(payload)['previous_close_feedback']['state'] == 'unavailable'


def test_height_relationship_conflict_is_not_carried_as_confirmed_yesterday_height(ledger):
    def wrong_height(closing):
        closing['events']['limit_up']['items'][1]['previous_board_count'] = 7
    retain(ledger, closing_update=wrong_height)
    frozen = context.freeze_previous_close_summary(DAY, 3000)
    row = next(row for row in frozen['items'] if row['instrument_id'] == '600003.SH')
    assert row['reported_final_board_count'] == 7 and row['final_board_count'] is None
    assert row['relay_state'] == 'height_conflict'
    feedback = build_workbench(next_noon(frozen))['previous_close_feedback']
    back = next(group for group in feedback['groups'] if group['key'] == 'resealed')
    assert back['items'][0]['previous_board_count'] is None


@pytest.mark.parametrize('day,known', [('bad', 1000), (DAY, True), (DAY, 0), (DAY, -1)])
def test_invalid_freeze_request_does_not_open_a_connection(monkeypatch, day, known):
    monkeypatch.setattr(context, 'connect', lambda: (_ for _ in ()).throw(AssertionError('invalid input')))
    assert context.freeze_previous_close_summary(day, known)['state'] == 'unavailable'


def test_collector_binds_close_fragment_at_the_same_known_at_clock(ws, monkeypatch):
    from app.services.noon_context import build_previous_review
    market, _ = ws
    current, previous = market.days[71], market.days[70]
    calls = []
    def frozen(day, stamp):
        calls.append((day, stamp))
        return {'date': day, 'state': 'retained', 'generated_at_ms': 1000,
                'review_id': 'close-then-known', 'result_sha256': 'a'*64, 'groups': [], 'items': []}
    monkeypatch.setattr(context, 'freeze_previous_close_summary', frozen)
    value = build_previous_review(market, current, previous, 3000, [])
    assert calls == [(previous, 3000)]
    assert value['close_review_summary']['review_id'] == 'close-then-known'
    before = deepcopy(value)
    monkeypatch.setattr(context, 'freeze_previous_close_summary', lambda *_: {'state': 'unavailable'})
    workbench = build_workbench({'date': current, 'calendar': {'verified': True, 'previous_day': previous},
                                'previous_review': value, 'stocks': [], 'events': {}})
    assert workbench['previous_close_feedback']['state'] == 'available'
    assert value == before


def frozen_capture(ledger):
    generated = int(datetime.fromisoformat(DAY+'T17:00:00+08:00').timestamp() * 1000)
    start = NEXT+'T11:31:00+08:00'
    known = int(datetime.fromisoformat(start).timestamp() * 1000)
    retain(ledger, generated=generated)
    value = context.freeze_previous_close_summary(DAY, known)
    payload = next_noon(value)
    payload.update(version='noon-snapshot-v1', collected_start_at=start)
    payload['previous_review']['known_at_ms'] = known
    return payload


def test_frozen_provenance_validator_is_pure_and_accepts_self_contained_exact_prior(ledger, monkeypatch):
    payload = frozen_capture(ledger)
    original = deepcopy(payload)
    monkeypatch.setattr(context, 'connect', lambda: pytest.fail('pure validator must not open SQLite'))
    assert context.validated_frozen_provenance(payload) == 'close_fixture_1'
    assert payload == original
    payload['previous_review']['close_review_summary']['items'][0]['close_change_pct'] = 0
    assert context.validated_frozen_provenance(payload) == 'close_fixture_1'


@pytest.mark.parametrize('damage', ['no_version', 'calendar', 'future_review', 'naive_start', 'known_mismatch',
    'bad_sha', 'scope', 'hard_reference', 'missing_item', 'count', 'tag_date', 'tag_identity',
    'transition', 'height', 'future_member_name', 'before_day', 'bool_count'])
def test_frozen_provenance_validator_keeps_invalid_or_non_self_contained_references_live(ledger, damage):
    payload = frozen_capture(ledger)
    value = payload['previous_review']['close_review_summary']
    if damage == 'no_version': payload.pop('version')
    if damage == 'calendar': payload['calendar']['previous_day'] = '2026-10-26'
    if damage == 'future_review': value['generated_at_ms'] = payload['previous_review']['known_at_ms'] + 1
    if damage == 'naive_start': payload['collected_start_at'] = NEXT+'T11:31:00'
    if damage == 'known_mismatch': payload['previous_review']['known_at_ms'] += 1
    if damage == 'bad_sha': value['result_sha256'] = 'bad'
    if damage == 'scope': value['scope'] = '人工插入的来源标签'
    if damage == 'hard_reference': value['source_snapshot_id'] = 'market-external-reference'
    if damage == 'missing_item': value['items'].pop()
    if damage == 'count': value['groups'][0]['retained_count'] += 1
    if damage == 'tag_date': value['items'][0]['industries'][0]['members_observed_date'] = NEXT
    if damage == 'tag_identity': value['items'][0]['industries'][0]['kind'] = 'concept'
    if damage == 'transition': value['items'][0].update(transition='unknown', transition_flags=[])
    if damage == 'height': value['items'][0]['final_board_count'] = True
    if damage == 'future_member_name': value['items'][0]['topics'][0]['source_snapshot_id'] = 'market-now'
    if damage == 'before_day': value['generated_at_ms'] = 1000
    if damage == 'bool_count': value['coverage']['retained_items'] = True
    assert context.validated_frozen_provenance(payload) is None


def test_frozen_provenance_validator_accepts_explicitly_bounded_partial_lists(ledger, monkeypatch):
    generated = int(datetime.fromisoformat(DAY+'T17:00:00+08:00').timestamp() * 1000)
    start = NEXT+'T11:31:00+08:00'
    known = int(datetime.fromisoformat(start).timestamp() * 1000)
    retain(ledger, generated=generated)
    monkeypatch.setattr(context, 'MAX_ITEMS', 1)
    value = context.freeze_previous_close_summary(DAY, known)
    payload = next_noon(value)
    payload.update(version='noon-snapshot-v1', collected_start_at=start)
    payload['previous_review']['known_at_ms'] = known
    assert value['state'] == 'partial'
    assert context.validated_frozen_provenance(payload) == 'close_fixture_1'
