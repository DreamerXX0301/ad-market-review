"""Isolated sector rounds never touch stock pools or request a provider."""
from copy import deepcopy
from hashlib import sha256
import json
import sqlite3

from fastapi import HTTPException
import pytest

from test_workspace import ws
from app import core
from app.services import sector_tracking as tracking, sector_strategies as strategies
from app.services import task_service, topics, topic_members
from app.services.workspace_common import js, e8

CODE = '885728.TI'


@pytest.fixture
def local(ws, monkeypatch):
    market, _ = ws
    state = {'day': market.entry, 'snapshot': 'market-fixture'}; values = {}; calls = []
    monkeypatch.setattr(market, 'status', lambda: {'ready': True, 'snapshot_id': state['snapshot'],
        'data_begin': market.days[0], 'data_end': state['day']}, raising=False)
    monkeypatch.setattr(tracking, 'get_market', lambda: market)
    monkeypatch.setattr(topic_members, 'get_market', lambda: market)
    monkeypatch.setattr(topic_members, 'member_metrics', lambda *args: {})
    monkeypatch.setattr(market, 'topics', lambda: [{'topic_id': 'fuyao-'+CODE, 'topic_code': CODE,
        'name': '人工智能', 'kind': 'concept', 'members_complete': True,
        'observed_date': market.days[71], 'instrument_ids': ['600001.SH']}])
    def metrics(m, code, day, periods=('D',)):
        # A separate write reservation must succeed: quote/cache work may not
        # occur under the service's SQLite write transaction.
        with core.connect() as connection:
            connection.execute('BEGIN IMMEDIATE'); connection.rollback()
        calls.append((code, day, periods))
        custom = values.get((code, day), {})
        close = custom.get('close', 100)
        known = close is not None
        daily = {'close': close, 'high': close+2 if known else None, 'low': close-2 if known else None,
            'observed_date': day if known else None, 'indicator_period': 'D', 'change_pct': 1 if known else None,
            'kd_cross': False, 'macd_cross': False, 'macd_dead_cross': False, 'kd_dead_cross': False,
            **custom}
        refs = {'artifact': 'artifacts/synthetic-proof.json', 'sha256': sha256(js(daily).encode()).hexdigest(), 'files': []} if known else {}
        return {'close_price': close, 'metrics_by_period': {'D': daily,
            'W': {'close': 95, 'observed_date': market.days[69], 'indicator_period': 'W'}},
            'cache_refs': refs, 'coverage': {'available': known, 'observation_date': day}}
    monkeypatch.setattr(tracking, '_metrics_for', metrics)
    from app.adapters.providers import ProviderClient
    monkeypatch.setattr(ProviderClient, 'request', lambda *args: (_ for _ in ()).throw(AssertionError('No remote requests')))
    return {'market': market, 'state': state, 'values': values, 'calls': calls, 'metrics': metrics}


def advance(local, index):
    day = local['market'].days[index]; local['state']['day'] = day
    with core.connect() as connection:
        connection.execute('UPDATE business_clock SET business_date=? WHERE singleton=1', (day,))
    return day


def create(local, policy=None, period='D'):
    strategy = strategies.create_strategy({'name': '板块跟踪测试', 'period': period,
        'definition': {'field': 'close', 'op': '>', 'value': 0}, 'policy': policy or {}})
    job = task_service.create_task('selection', {'fixture_sector': strategy['strategy_id']})
    task_service.finish(job['job_id'], 'succeeded')
    day = local['state']['day']; run_id = core.new_id('sector-run')
    info = local['metrics'](local['market'], CODE, day)
    with core.connect() as connection:
        connection.execute('INSERT INTO sector_selection_run VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (run_id, job['job_id'], strategy['strategy_id'], strategy['version'], day,
             local['state']['snapshot'], 'catalog', 'input', js(info['cache_refs']), 'completed', core.now_ms(), core.now_ms()))
        connection.execute('INSERT INTO sector_selection_candidate VALUES(?,?,?,?,?,?,?,?,?)',
            (run_id, CODE, 'concept', '人工智能', 'matched', e8(100),
             js({'metrics_by_period': info['metrics_by_period'], 'coverage': info['coverage']}), '{}', js(info['cache_refs'])))
    return strategy, run_id


def add(local, policy=None, period='D'):
    strategy, run = create(local, policy, period)
    result = tracking.add_tracking({'run_id': run, 'target_ids': [CODE]})
    assert result['added_count'] == 1
    return strategy, run, result['items'][0]['round_id']


def test_entry_freezes_daily_points_high_low_cache_and_stays_separate_from_stock(local):
    _, run, rid = add(local, period='W')
    row = tracking.get_tracking(rid)
    assert row['entry_price'] == 100 and row['entry_high'] == 102 and row['entry_low'] == 98
    assert row['entry_period'] == 'W' and row['trading_age'] == 0
    assert row['target_type'] == 'sector' and row['target_id'] == row['topic_code'] == CODE
    assert row['entry_basis']['cache_refs']['sha256'] and row['entry_high_state'] == 'ready'
    assert tracking.add_tracking({'run_id': run, 'target_ids': [CODE, CODE]})['added_count'] == 0
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM tracking_round').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM selection_candidate').fetchone()[0] == 0


@pytest.mark.parametrize('fault', ['old_day', 'changed_version', 'archived', 'changed_snapshot', 'run_not_completed'])
def test_entry_requires_current_completed_unmodified_run(local, fault):
    strategy, run = create(local)
    if fault == 'old_day': advance(local, 71)
    elif fault == 'changed_version': strategies.update_strategy(strategy['strategy_id'], {'definition': {'field': 'close', 'op': '>', 'value': 2}})
    elif fault == 'archived': strategies.update_strategy(strategy['strategy_id'], {'archived': True})
    elif fault == 'changed_snapshot': local['state']['snapshot'] = 'different-snapshot'
    else:
        with core.connect() as connection: connection.execute("UPDATE sector_selection_run SET status='running' WHERE run_id=?", (run,))
    with pytest.raises(HTTPException) as error: tracking.add_tracking({'run_id': run, 'target_ids': [CODE]})
    assert error.value.status_code == 409


def test_unmatched_candidate_is_skipped_not_entered(local):
    _, run = create(local)
    with core.connect() as connection:
        connection.execute("UPDATE sector_selection_candidate SET decision='unknown' WHERE run_id=?", (run,))
    result = tracking.add_tracking({'run_id': run, 'target_ids': [CODE]})
    assert result['added_count'] == 0 and result['skipped']


def test_d0_default_no_alert_no_end_and_review_is_idempotent(local):
    _, _, rid = add(local, {'alerts': [{'field': 'close', 'op': '>', 'value': 0}],
                          'exits': [{'field': 'close', 'op': '>', 'value': 0}]})
    first = tracking.review_tracking({})
    second = tracking.review_tracking({})
    assert first['reviewed'] == 1 and first['alerts'] == first['ended'] == 0
    assert second['reviewed'] == 0 and second['skipped'] == 1
    assert tracking.get_tracking(rid)['status'] == 'tracking'


def test_strict_entry_low_equality_does_not_end_then_real_break_ends(local):
    _, _, rid = add(local, {'exits': [{'field': 'close_below_entry_low', 'op': '==', 'value': True}]})
    day = advance(local, 71); local['values'][(CODE, day)] = {'close': 98}
    assert tracking.review_tracking({})['ended'] == 0
    row = tracking.get_tracking(rid)
    assert row['status'] == 'tracking' and row['trading_age'] == 1
    day2 = advance(local, 72); local['values'][(CODE, day2)] = {'close': 97}
    assert tracking.review_tracking({})['ended'] == 1
    ended = tracking.get_tracking(rid)
    assert ended['status'] == 'condition_ended' and ended['current_price'] == 97
    assert ended['tracking_change_pct'] == pytest.approx(-3)
    assert ended['entry_price'] == 100 and ended['entry_low'] == 98
    assert tracking.list_tracking(status='ended')['total'] == 1


def test_entry_high_alert_continues_tracking_and_is_visible_in_trigger_filter(local):
    _, _, rid = add(local, {'alerts': [{'field': 'close_above_entry_high', 'op': '==', 'value': True}]})
    day = advance(local, 71); local['values'][(CODE, day)] = {'close': 103}
    result = tracking.review_tracking({})
    assert result['alerts'] == 1 and result['ended'] == 0
    assert tracking.get_tracking(rid)['status'] == 'tracking'
    assert tracking.list_tracking(triggered_only=True)['items'][0]['round_id'] == rid
    event = tracking.list_events(event_type='alert')['items'][0]
    assert event['topic_code'] == CODE and event['name'] == '人工智能' and event['strategy_name']
    assert event['evidence']['alerts']['checks'][0]['actual'] is True


def test_missing_quote_does_not_expire_and_can_be_rechecked_after_repair(local):
    _, _, rid = add(local, {'duration_sessions': 1})
    day = advance(local, 71); local['values'][(CODE, day)] = {'close': None}
    first = tracking.review_tracking({})
    assert first['unknown'] == 1 and first['expired'] == 0
    assert tracking.get_tracking(rid)['current_price'] is None
    assert tracking.get_tracking(rid)['status'] == 'tracking'
    local['values'][(CODE, day)] = {'close': 101}
    fixed = tracking.review_tracking({})
    assert fixed['expired'] == 1 and tracking.get_tracking(rid)['status'] == 'expired'
    reviews = tracking.list_events(event_type='review', business_date=day)['items']
    assert len(reviews) == 2 and {row['data_state'] for row in reviews} == {'ready','unknown'}


def test_policy_revision_does_not_rewrite_entry_or_old_review(local):
    _, _, rid = add(local)
    tracking.review_tracking({}); before = tracking.get_tracking(rid)
    day = advance(local, 71)
    changed = tracking.change_tracking_policy(rid, {'policy': {'duration_sessions': 2,
        'exits': [{'field': 'close_below_entry_low', 'op': '==', 'value': True}]}})
    assert changed['policy_revision'] == 2 and changed['entry_basis'] == before['entry_basis']
    assert changed['entry_business_date'] == before['entry_business_date']
    assert changed['policy_review_pending']
    tracking.review_tracking({})
    old = tracking.list_events(event_type='review', business_date=before['entry_business_date'])['items']
    assert len(old) == 1 and old[0]['policy_revision'] == 1
    assert tracking.get_tracking(rid)['trading_age'] == 1


def test_review_request_key_same_inputs_idempotent_other_scope_conflicts(local):
    _, _, rid = add(local)
    first = tracking.review_tracking({'request_key': 'one-review'})
    assert tracking.review_tracking({'request_key': 'one-review'}) == first
    with pytest.raises(HTTPException) as error:
        tracking.review_tracking({'request_key': 'one-review', 'round_ids': [rid]})
    assert error.value.status_code == 409


def test_no_quote_or_cache_io_inside_write_transaction_and_no_worker_collision(local):
    _, _, rid = add(local)
    job = task_service.create_task('source_check', {'fixture': 'already-running'})
    task_service.claim_next('fake-worker')
    with pytest.raises(HTTPException) as error: tracking.review_tracking({})
    assert error.value.status_code == 409
    task_service.finish(job['job_id'], 'succeeded')
    assert tracking.review_tracking({})['reviewed'] == 1
    assert local['calls']


def test_manual_remove_is_idempotent_and_new_round_can_be_added(local):
    _, run, rid = add(local)
    assert tracking.remove_tracking(rid, {'reason': '不再观察'})['status'] == 'manual_removed'
    assert tracking.remove_tracking(rid, {})['status'] == 'manual_removed'
    assert tracking.list_events(event_type='manual_remove')['total'] == 1
    replacement = tracking.add_tracking({'run_id': run, 'target_ids': [CODE]})
    assert replacement['added_count'] == 1 and replacement['items'][0]['round_id'] != rid
    assert tracking.list_tracking(status='all')['total'] == 2
    with pytest.raises(HTTPException): tracking.change_tracking_policy(rid, {'policy': {}})


def test_entry_and_events_cannot_be_mutated_in_sql(local):
    _, _, rid = add(local)
    with core.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError): connection.execute('UPDATE sector_tracking_round SET entry_price_e8=1 WHERE round_id=?', (rid,))
        with pytest.raises(sqlite3.IntegrityError): connection.execute("UPDATE sector_tracking_event SET outcome='satisfied' WHERE round_id=?", (rid,))
        with pytest.raises(sqlite3.IntegrityError): connection.execute('DELETE FROM sector_tracking_event WHERE round_id=?', (rid,))


def test_latest_members_use_real_collection_date_and_manual_override_without_stock_entry(local):
    _, _, rid = add(local)
    topics.seed_topics()
    definition = topics.list_topics()['items'][0]
    topics.add_member(definition['topic_id'], {'instrument_id': '600001.SH', 'action': 'exclude'})
    topics.add_member(definition['topic_id'], {'instrument_id': '000001.SZ', 'action': 'include'})
    result = tracking.members(rid, membership_scope='latest')
    assert result['membership_as_of'] == local['market'].days[71]
    assert result['quote_as_of'] == local['market'].entry
    assert result['stock_entry_baseline'] is None
    assert [row['instrument_id'] for row in result['items']] == ['000001.SZ']
    assert result['items'][0]['manual_override'] == 'include'
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM tracking_round').fetchone()[0] == 0


def test_historical_members_do_not_fallback_to_current_collection(local, monkeypatch):
    _, _, rid = add(local)
    from app.services import market_service
    calls = []
    def historical(day, **kwargs):
        calls.append((day, kwargs))
        return {'items': [], 'total': 0, 'membership_observed_date': None,
                'unavailable_reason': '该日没有成员历史档案'}
    monkeypatch.setattr(market_service, 'market_topic_members', historical)
    result = tracking.members(rid, local['market'].entry, 'historical')
    assert result['total'] == 0 and result['membership_as_of'] is None
    assert calls[0][1]['source_code'] == CODE
    with pytest.raises(HTTPException): tracking.members(rid, local['market'].days[69], 'latest')


def test_unknown_entry_low_never_becomes_zero_or_an_exit(local):
    _, run = create(local, {'exits': [{'field': 'close_below_entry_low', 'op': '==', 'value': True}]})
    with core.connect() as connection:
        row = connection.execute('SELECT metrics_json FROM sector_selection_candidate WHERE run_id=?', (run,)).fetchone()
        value = json.loads(row[0]); value['metrics_by_period']['D']['low'] = None
        connection.execute('UPDATE sector_selection_candidate SET metrics_json=? WHERE run_id=?', (js(value), run))
    rid = tracking.add_tracking({'run_id': run, 'target_ids': [CODE]})['items'][0]['round_id']
    day = advance(local, 71); local['values'][(CODE, day)] = {'close': 1}
    result = tracking.review_tracking({})
    assert result['ended'] == 0 and result['unknown'] >= 1
    row = tracking.get_tracking(rid)
    assert row['entry_low'] is None and row['entry_low_state'] == 'unknown'
    assert row['status'] == 'tracking'


def test_d0_switches_explicitly_allow_signal_without_expiring_entry_day(local):
    _, _, rid = add(local, {'duration_sessions': 1, 'allow_entry_day_alerts': True,
        'alerts': [{'field': 'close', 'op': '>', 'value': 0}]})
    result = tracking.review_tracking({})
    assert result['alerts'] == 1 and result['expired'] == 0
    assert tracking.get_tracking(rid)['status'] == 'tracking'


def test_composite_conditions_keep_three_states(local):
    _, _, rid = add(local, {'alert_logic': 'ANY', 'alerts': [
        {'field': 'close', 'op': '>', 'value': 0}, {'field': 'ma60', 'op': '>', 'value': 0}],
        'exit_logic': 'ALL', 'exits': [
        {'field': 'close', 'op': '>', 'value': 0}, {'field': 'ma60', 'op': '>', 'value': 0}]})
    advance(local, 71)
    result = tracking.review_tracking({})
    assert result['alerts'] == 1 and result['ended'] == 0
    event = tracking.list_events(event_type='review')['items'][0]
    assert event['evidence']['alerts']['result'] is True
    assert event['evidence']['exits']['result'] is None
    assert tracking.get_tracking(rid)['status'] == 'tracking'


def test_same_sector_can_have_independent_rounds_for_two_strategies_with_shared_quote_read(local):
    _, _, first = add(local)
    _, _, second = add(local)
    local['calls'].clear()
    result = tracking.list_tracking()
    assert {row['round_id'] for row in result['items']} == {first, second}
    assert len(local['calls']) == 1


def test_ended_history_uses_immutable_review_quote_not_later_cache_state(local):
    _, _, rid = add(local, {'duration_sessions': 1})
    day = advance(local, 71); local['values'][(CODE, day)] = {'close': 105}
    tracking.review_tracking({})
    assert tracking.get_tracking(rid)['current_price'] == 105
    local['values'][(CODE, day)] = {'close': None}
    assert tracking.get_tracking(rid)['current_price'] == 105
    assert tracking.list_tracking(status='ended')['items'][0]['tracking_change_pct'] == pytest.approx(5)


def test_real_sector_run_can_add_round_using_daily_close_for_weekly_strategy(local, monkeypatch):
    market = local['market']
    monkeypatch.setattr(strategies, 'get_market', lambda: market)
    monkeypatch.setattr(strategies, 'metrics_for_sector', local['metrics'])
    monkeypatch.setattr(market, 'reference', lambda: {'concept_catalog': [{'thscode': CODE, 'name': '人工智能'}]}, raising=False)
    strategy = strategies.create_strategy({'name': '周线选板', 'period': 'W',
        'definition': {'field': 'close', 'op': '>', 'value': 0}})
    run = strategies.run_strategy(strategy['strategy_id'])
    assert run['matched_count'] == 1
    result = tracking.add_tracking({'run_id': run['run_id'], 'target_ids': [CODE]})
    row = tracking.get_tracking(result['items'][0]['round_id'])
    assert row['entry_price'] == 100 and row['entry_period'] == 'W'
    assert row['entry_price'] != 95


def test_weekly_match_without_current_daily_quote_cannot_enter(local, monkeypatch):
    market = local['market']; local['values'][(CODE, market.entry)] = {'close': None}
    monkeypatch.setattr(strategies, 'get_market', lambda: market)
    monkeypatch.setattr(strategies, 'metrics_for_sector', local['metrics'])
    monkeypatch.setattr(market, 'reference', lambda: {'concept_catalog': [{'thscode': CODE, 'name': '人工智能'}]}, raising=False)
    strategy = strategies.create_strategy({'name': '周线无日价', 'period': 'W',
        'definition': {'field': 'close', 'op': '>', 'value': 0}})
    run = strategies.run_strategy(strategy['strategy_id'])
    assert run['matched_count'] == 1 and run['items'][0]['close_price'] is None
    result = tracking.add_tracking({'run_id': run['run_id'], 'target_ids': [CODE]})
    assert result['added_count'] == 0
