"""Board strategies stay isolated, causal and unknown-safe; no real sources."""
from copy import deepcopy
import json
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app import core
from app.domain import sector_conditions as conditions
from app.services import sector_charts as charts, sector_strategies as strategies, task_service as tasks
from test_platform import isolated
from test_sector_charts import Adapter, Market, CODE, DAY


def leaf(field='close', op='>', value=0, **extra):
    return {'field': field, 'op': op, 'value': value, **extra}


@pytest.fixture
def workspace(isolated, monkeypatch):
    market = Market(start='2021-10-01'); market.state['ready'] = True
    adapter = Adapter(market)
    monkeypatch.setattr(charts, '_source_today', lambda: DAY)
    monkeypatch.setattr(strategies, 'get_market', lambda: market)
    monkeypatch.setattr(core, 'get_market', lambda: market)
    from app.services.source_service import ProviderClient
    monkeypatch.setattr(ProviderClient, 'request', lambda *a, **k: pytest.fail('real source request forbidden'))
    with strategies._SUMMARY_LOCK: strategies._SUMMARIES.clear()
    task = tasks.create_task('source_check', {'fixture': 'sector-strategies'})
    path = core.published_dir()/'sector-fixture.json'; core.atomic_json(path, {'fixture': True})
    snapshot = core.publish_snapshot(module='daily_raw', job_id=task['job_id'],
        start=market.days[0], end=DAY, lineage={'market_snapshot_id': market.state['snapshot_id']},
        artifacts=[{'object_key': 'sector-fixture.json', 'sha256': core.hash_file(path),
                    'byte_count': path.stat().st_size, 'row_count': 1}])
    calendar_snapshot = core.publish_snapshot(module='calendar', job_id=task['job_id'],
        start=market.days[0], end=DAY,
        artifacts=[{'object_key': 'sector-fixture.json', 'sha256': core.hash_file(path),
                    'byte_count': path.stat().st_size, 'row_count': len(market.days)}])
    tasks.finish(task['job_id'], 'succeeded')
    with core.connect() as connection:
        connection.execute('INSERT INTO business_clock VALUES(1,?,?,?)', (DAY, calendar_snapshot, core.now_ms()))
    context = SimpleNamespace(market=market, adapter=adapter, snapshot=snapshot)
    yield context
    with strategies._SUMMARY_LOCK: strategies._SUMMARIES.clear()


def cached(workspace, code=CODE, transform=None):
    adapter = Adapter(workspace.market, transform)
    plan = charts.plan_sector_chart(workspace.market, code, DAY)
    return charts.collect_sector_chart(workspace.market, plan, adapter)


def create(**extra):
    return strategies.create_strategy({'name': '板块条件样本', 'definition': leaf(), **extra})


def test_sector_specs_reject_stock_identity_and_source_events():
    fields = {item['field']: item for item in conditions.specs()}
    assert not ({'limit_up', 'prev_limit_up', 'is_st', 'board', 'turnover_ratio_pct', 'topic_ids'} & fields.keys())
    assert fields['close_breakout_high']['fixed_period'] == 'D'
    assert fields['close_breakout_high']['min'] == 1 and fields['close_breakout_high']['max'] == 750
    assert fields['macd_dead_cross']['type'] == 'boolean'
    assert fields['close_below_entry_low']['tracking_only'] is True


@pytest.mark.parametrize('node', [leaf('is_st', '==', False), leaf('limit_up', '==', True),
    leaf('turnover_ratio_pct'), leaf('close_below_entry', '==', True),
    leaf('close', value=float('nan')), leaf('close', value=True),
    leaf('close_breakout_high', 'new_high', 0), leaf('close_breakout_high', 'new_high', 751),
    leaf('close_breakout_high', 'new_high', 60, period='W'),
    {'field': 'close', 'op': '>', 'right_field': 'close_breakout_high'},
    {'history_event': {}}, {'field': [], 'op': '>', 'value': 1}])
def test_invalid_or_stock_conditions_never_enter_sector_ast(node):
    with pytest.raises(ValueError): conditions.validate(node)


def test_mixed_period_ast_and_three_valued_evidence():
    definition = conditions.validate({'all': [leaf('close', period='D'), leaf('macd_cross', '==', True, period='W')]}, 'M')
    data = {'D': {'close': 101, 'observed_date': DAY}, 'W': {'macd_cross': None, 'observed_date': '2026-10-16'}}
    checked = conditions.evaluate(definition, data, 'M')
    assert checked['result'] is None
    assert checked['checks'][0]['period'] == 'D'
    assert checked['checks'][1]['observation_date'] == '2026-10-16'
    data['D']['close'] = -1
    assert conditions.evaluate(definition, data, 'M')['result'] is False
    assert conditions.evaluate({'any': definition['all']}, data, 'M')['result'] is None
    assert conditions.evaluate(leaf('macd_cross', '==', False), {'D': {'macd_cross': None}})['result'] is None


def test_daily_breakout_never_uses_weekly_metric_and_equality_blocks():
    node = conditions.validate(leaf('close_breakout_high', 'new_high', 60), 'M')
    daily = {'breakout_valid': True, 'breakout_previous_sessions': 60,
             'breakout_blocking_distance': 60, 'new_high_current_close': 100, 'observed_date': DAY}
    assert conditions.evaluate(node, {'D': daily, 'M': {'breakout_valid': True}}, 'M')['result'] is False
    daily['breakout_blocking_distance'] = None
    assert conditions.evaluate(node, {'D': daily}, 'M')['result'] is True
    daily['breakout_previous_sessions'] = 59
    assert conditions.evaluate(node, {'D': daily}, 'M')['result'] is None


def test_policy_validates_independent_periods_and_tracking_only_fields():
    policy = conditions.policy_normalized({'alerts': [leaf('close_above_entry_high', '==', True)],
        'exits': [leaf('macd_dead_cross', '==', True, period='W'), leaf('close_below_entry_low', '==', True)]}, 'M')
    assert policy['duration_sessions'] == 30 and policy['allow_entry_day_alerts'] is False
    assert policy['alerts'][0]['period'] == 'D' and policy['exits'][1]['period'] == 'D'
    with pytest.raises(ValueError): conditions.policy_normalized({'exits': [leaf('close_above_entry_high', '==', True)]})
    with pytest.raises(ValueError): conditions.policy_normalized({'duration_sessions': True})
    with pytest.raises(ValueError): conditions.validate(leaf('close_below_entry_low', '==', True, period='W'), scope='exit')


def test_crud_versions_and_archival_do_not_touch_stock_storage(workspace):
    first = create(period='M', sector_kind='concept')
    assert first['target_type'] == 'sector' and first['version'] == 1
    second = strategies.update_strategy(first['strategy_id'], {'definition': leaf('close', value=50, period='W'),
        'policy': {'duration_sessions': 60}})
    assert second['version'] == 2 and second['period'] == 'M'
    with core.connect() as connection:
        old = connection.execute('SELECT * FROM sector_strategy_version WHERE strategy_id=? AND version=1', (first['strategy_id'],)).fetchone()
        assert json.loads(old['definition_json']) == leaf()
        assert connection.execute('SELECT count(*) FROM strategy').fetchone()[0] == 0
    archived = strategies.update_strategy(first['strategy_id'], {'archived': True})
    assert archived['version'] == 2 and archived['archived'] == 1
    assert strategies.list_strategies()['total'] == 1
    with pytest.raises(HTTPException) as caught: strategies.run_strategy(first['strategy_id'])
    assert caught.value.status_code == 409


def test_verified_daily_once_produces_dwm_summaries_and_exact_daily_anchor(workspace):
    accepted = cached(workspace)
    data = strategies.metrics_for_sector(workspace.market, CODE, DAY, ('D', 'W', 'M'))
    assert data['coverage']['available'] is True
    assert data['metrics_by_period']['D']['observed_date'] == DAY
    assert data['metrics_by_period']['W']['observed_date'] == '2026-10-16'
    assert data['metrics_by_period']['M']['observed_date'] == '2026-09-30'
    assert data['close_price'] == accepted['record']['bars'][-1]['close']
    assert data['metrics_by_period']['D']['high'] == accepted['record']['bars'][-1]['high']
    assert data['metrics_by_period']['M']['macd_dif'] is not None
    assert data['cache_refs']['files'] and all(ref['sha256'] for ref in data['cache_refs']['files'])


def test_warm_summary_hashes_raw_but_never_reenriches_and_returns_deepcopy(workspace, monkeypatch):
    cached(workspace)
    first = strategies.metrics_for_sector(workspace.market, CODE, DAY)
    expected = deepcopy(first)
    first['metrics_by_period']['D']['close'] = -1
    monkeypatch.setattr(strategies, '_indicators', lambda *a: pytest.fail('warm summary must not recalculate'))
    second = strategies.metrics_for_sector(workspace.market, CODE, DAY)
    assert second == expected
    second['coverage']['notice'] = 'caller changed'
    assert strategies.metrics_for_sector(workspace.market, CODE, DAY) == expected
    ref = next(ref for ref in second['cache_refs']['files'] if ref['artifact'].startswith('raw/'))
    path = charts._root()/ref['artifact']; original = path.read_bytes()
    path.write_bytes(b' ' * len(original))
    # Metadata timestamp is not an integrity guarantee: SHA is always checked.
    unknown = strategies.metrics_for_sector(workspace.market, CODE, DAY)
    assert unknown['coverage']['available'] is False
    assert unknown['metrics_by_period']['D']['close'] is None


def test_unknown_amount_is_not_zero_and_does_not_break_price_rules(workspace):
    def no_amount(payload):
        for row in payload['data']['item']: row.pop('turnover')
        return payload
    cached(workspace, transform=no_amount)
    data = strategies.metrics_for_sector(workspace.market, CODE, DAY)
    assert data['coverage']['available'] is True
    assert data['metrics_by_period']['D']['amount'] is None
    assert conditions.evaluate(leaf('amount', '==', 0), data['metrics_by_period'])['result'] is None
    assert conditions.evaluate(leaf(), data['metrics_by_period'])['result'] is True


@pytest.mark.parametrize('damage', ['missing', 'zero_volume'])
def test_missing_or_zero_volume_intermediate_day_restarts_daily_proof(workspace, damage):
    def transform(payload):
        if damage == 'missing': payload['data']['item'].pop(-2)
        else: payload['data']['item'][-2]['volume'] = 0
        return payload
    cached(workspace, transform=transform)
    data = strategies.metrics_for_sector(workspace.market, CODE, DAY)
    daily = data['metrics_by_period']['D']
    assert daily['close'] is not None and daily['ma5'] is None
    assert daily['macd_cross'] is None and daily['kd_cross'] is None
    for field in ('volume_expand','volume_shrink'):
        assert daily[field] is None
        assert conditions.evaluate(leaf(field,'==',True), data['metrics_by_period'])['result'] is None
    assert conditions.evaluate(conditions.validate(leaf('close_breakout_high', 'new_high', 2)), data['metrics_by_period'])['result'] is None


def test_incomplete_last_closed_week_month_do_not_fallback_to_older_period(workspace):
    def transform(payload):
        payload['data']['item'] = [row for row in payload['data']['item']
            if row['date_ms'] not in (charts._midnight('2026-10-15'), charts._midnight('2026-09-29'))]
        return payload
    cached(workspace, transform=transform)
    data = strategies.metrics_for_sector(workspace.market, CODE, DAY)
    assert data['metrics_by_period']['D']['close'] is not None
    assert data['metrics_by_period']['W']['close'] is None
    assert data['metrics_by_period']['M']['close'] is None


def test_later_source_cache_projects_without_future_metrics(workspace):
    cached(workspace)
    earlier = '2026-10-19'
    data = strategies.metrics_for_sector(workspace.market, CODE, earlier)
    assert data['metrics_by_period']['D']['observed_date'] == earlier
    assert data['cache_refs']['cache_as_of'] == DAY
    assert data['coverage']['projected_from_later_cache'] is True
    raw = charts.read_sector_chart(workspace.market, CODE, earlier, 'D')['bars'][-1]
    assert data['metrics_by_period']['D']['ma60'] == raw['ma60']
    assert data['metrics_by_period']['D']['macd_dif'] == raw['macd_dif']


def test_selection_all_candidates_frozen_with_unknown_missing_board_and_real_progress(workspace):
    cached(workspace)
    st = create()
    run = strategies.run_strategy(st['strategy_id'], {'request_key': 'sector-run-one'})
    assert run['total'] == 2 and run['matched_count'] == 1 and run['unknown_count'] == 1
    assert run['business_date'] == DAY and run['market_snapshot_id'] == workspace.market.state['snapshot_id']
    job = tasks.task_view(run['job_id'])
    assert job['status'] == 'succeeded'
    assert any(event['details'].get('total_count') == 2 for event in job['logs'])
    assert run['items'][0]['topic_code'] == '881101.TI'
    assert run['items'][0]['decision'] == 'unknown' and run['items'][0]['close_price'] is None
    assert run['items'][1]['target_id'] == CODE
    assert len(strategies.get_run(run['run_id'], 'matched')['items']) == 1
    assert strategies.run_strategy(st['strategy_id'], {'request_key': 'sector-run-one'})['run_id'] == run['run_id']
    strategies.update_strategy(st['strategy_id'], {'definition': leaf('close', value=99999)})
    assert strategies.get_run(run['run_id'])['definition'] == leaf()
    assert strategies.list_runs(st['strategy_id'])['total'] == 1
    with core.connect() as connection:
        for table in ('selection_run', 'selection_candidate', 'tracking_round'):
            assert connection.execute('SELECT count(*) FROM '+table).fetchone()[0] == 0


def test_kind_filter_and_observation_date_are_independent_from_market_view(workspace):
    cached(workspace)
    st = create(sector_kind='concept')
    with pytest.raises(HTTPException) as caught:
        strategies.run_strategy(st['strategy_id'], {'date': '2026-10-19'})
    assert caught.value.status_code == 409
    run = strategies.run_strategy(st['strategy_id'])
    assert run['total'] == 1 and run['items'][0]['sector_kind'] == 'concept'


def test_generation_change_during_evaluation_saves_no_mixed_run(workspace, monkeypatch):
    cached(workspace); st = create()
    original = strategies.metrics_for_sector
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        workspace.market.state['snapshot_id'] = 'market-replaced'
        return result
    monkeypatch.setattr(strategies, 'metrics_for_sector', changed)
    with pytest.raises(HTTPException) as caught: strategies.run_strategy(st['strategy_id'])
    assert caught.value.status_code == 409
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM sector_selection_run').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM sector_selection_candidate').fetchone()[0] == 0
    assert tasks.list_tasks()['items'][0]['status'] == 'failed'


def test_cancel_during_evaluation_preserves_previous_results(workspace, monkeypatch):
    cached(workspace); st = create()
    original = strategies.metrics_for_sector
    def cancelled(*args, **kwargs):
        result = original(*args, **kwargs)
        with core.connect() as connection:
            identifier = connection.execute("SELECT job_id FROM job WHERE status='running'").fetchone()[0]
        tasks.change_task(identifier, 'cancel')
        return result
    monkeypatch.setattr(strategies, 'metrics_for_sector', cancelled)
    with pytest.raises(HTTPException): strategies.run_strategy(st['strategy_id'])
    assert tasks.list_tasks()['items'][0]['status'] == 'cancelled'
    assert strategies.list_runs()['total'] == 0


def test_summary_lru_is_bounded_by_small_results(workspace, monkeypatch):
    cached(workspace)
    monkeypatch.setattr(strategies, 'MAX_SUMMARIES', 2)
    for day in ('2026-10-16', '2026-10-19', DAY):
        strategies.metrics_for_sector(workspace.market, CODE, day)
    assert len(strategies._SUMMARIES) == 2
    assert all('bars' not in entry['value'] for entry in strategies._SUMMARIES.values())


def test_sector_trendline_checks_verified_index_without_members(workspace):
    pattern = [19, 20, 21, 23, 21, 19, 18, 19, 21, 19, 18, 21]

    def transform(payload):
        rows = payload['data']['item']
        for row in rows:
            row.update(open_price=15, close_price=15, high_price=17, low_price=14)
        for row, high in zip(rows[-len(pattern):], pattern):
            row['high_price'] = high
        rows[-1]['close_price'] = 20
        return payload

    workspace.market.ref['topics'] = []
    cached(workspace, transform=transform)
    definition = {'all': [leaf(), leaf('trendline_breakout', 'matches', 0.5, period='D')]}
    run = strategies.run_strategy(create(definition=definition)['strategy_id'])
    assert run['matched_count'] == 1 and run['unknown_count'] == 1
    item = next(row for row in run['items'] if row['topic_code'] == CODE)
    assert item['checks']['checks'][1]['evidence']['line']['projected_price'] == pytest.approx(19.8)
