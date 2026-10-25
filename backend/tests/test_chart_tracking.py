"""Search-open charts discover existing frozen references without new research."""
import json
from copy import deepcopy
from contextlib import contextmanager
import pytest
from app import core
from app.routers.platform import router
from app.services import chart_tracking
from test_workspace import ws, make_strategy, add_one, advance


@pytest.fixture
def baselines(ws):
    market, client = ws
    client.app.include_router(router)
    first = make_strategy(client, {'field': 'close', 'op': '>', 'value': 100})
    _, older = add_one(client, first)
    advance(market, 75)
    second = make_strategy(client, {'field': 'close', 'op': '>', 'value': 101})
    _, recent = add_one(client, second)
    return market, client, older, recent


def test_search_loads_own_frozen_rounds_without_market_reads_or_writes(baselines, monkeypatch):
    market, client, older, recent = baselines
    monkeypatch.setattr(market, 'panel', lambda *a, **k: pytest.fail('No market history is needed'))
    monkeypatch.setattr(market, 'history_panel', lambda *a, **k: pytest.fail('No full panel is needed'))
    with core.connect() as connection:
        counts = [connection.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('job', 'tracking_round', 'tracking_event')]
    result = client.get('/api/v1/instruments/600001.sh/tracking-baselines').json()
    assert result['instrument_id'] == '600001.SH'
    assert result['default_round_id'] == recent['round_id']
    assert [row['round_id'] for row in result['items']] == [recent['round_id'], older['round_id']]
    assert result['items'][1]['entry_price'] == older['entry_price']
    assert result['items'][0]['entry_basis']['price_basis'] == 'none'
    assert client.get('/api/v1/instruments/000001.SZ/tracking-baselines').json()['items'] == []
    with core.connect() as connection:
        assert counts == [connection.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('job', 'tracking_round', 'tracking_event')]


def test_observation_date_excludes_later_entry_and_exit(baselines):
    market, client, older, recent = baselines
    assert client.post('/api/v1/tracking-rounds/'+older['round_id']+'/remove', json={}).status_code == 200
    earlier = client.get('/api/v1/instruments/600001.SH/tracking-baselines', params={'as_of': market.entry}).json()
    assert [row['round_id'] for row in earlier['items']] == [older['round_id']]
    assert earlier['items'][0]['status'] == 'tracking'
    assert earlier['items'][0]['end_business_date'] is None
    current = client.get('/api/v1/instruments/600001.SH/tracking-baselines').json()
    assert current['default_round_id'] == recent['round_id']
    assert current['items'][1]['status'] == 'manual_removed'
    assert client.get('/api/v1/instruments/600001.SH/tracking-baselines', params={'as_of': market.days[69]}).json()['items'] == []


def test_bounded_history_and_deterministic_active_default(baselines, monkeypatch):
    _, client, _, recent = baselines
    monkeypatch.setattr(chart_tracking, 'MAX_BASELINES', 1)
    result = client.get('/api/v1/instruments/600001.SH/tracking-baselines').json()
    assert result['total'] == 2 and result['truncated'] is True
    assert result['default_round_id'] == recent['round_id'] and len(result['items']) == 1


@pytest.mark.parametrize('day', ['2026-02-30', 'not-a-date', '2099-01-01'])
def test_invalid_and_future_dates_do_not_load_references(baselines, day):
    _, client, _, _ = baselines
    assert client.get('/api/v1/instruments/600001.SH/tracking-baselines', params={'as_of': day}).status_code == 422


def test_search_returns_only_frozen_historical_event_proofs(ws, monkeypatch):
    market, client = ws
    client.app.include_router(router)
    for values in market.data.values():
        for row in values:
            row['corporate_action_coverage_known'] = True
    monkeypatch.setattr(market, 'history_for_instruments', lambda symbols, start=None, end=None:
                        {symbol: [row for row in market.bars(symbol, end) if start is None or row['date'] >= start]
                         for symbol in symbols}, raising=False)
    market.data['600001.SH'][40]['volume'] = 300
    definition = {'all': [{'history_event': {
        'window': {'from': 1, 'to': 60}, 'match': 'latest',
        'where': {'field': 'volume_ratio', 'op': '>=', 'value': 3},
        'then': {'all': [
            {'field': 'close', 'op': '>=', 'right_event_field': 'low', 'right_event_change_pct': -3},
            {'any': [{'field': 'close', 'op': '>', 'right_event_field': 'high', 'right_event_change_pct': 3},
                     {'field': 'close', 'op': '>', 'value': 100}]}]},
        'price_path': {'reclaim': {'event_field': 'high', 'mode': 'high_at_or_above'}}}},
        {'field': 'close', 'op': '>', 'value': 100}]}
    strategy = make_strategy(client, definition)
    run, saved_round = add_one(client, strategy)
    original = next(row for row in run['items'] if row['decision'] == 'matched')['checks']['checks'][0]
    assert client.patch('/api/v1/strategies/' + strategy['strategy_id'], json={
        'definition': {'field': 'close', 'op': '>', 'value': 999}}).status_code == 200
    with core.connect() as connection:
        counts = [connection.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                  for table in ('job', 'tracking_round', 'tracking_event')]
    for name in ('panel', 'history_panel', 'bars', 'history_for_instruments'):
        monkeypatch.setattr(market, name, lambda *a, **k: pytest.fail('Frozen proofs need no market reads'))
    from app.domain import conditions
    monkeypatch.setattr(conditions, 'evaluate', lambda *a, **k: pytest.fail('Never recompute saved signals'))
    connect = chart_tracking.connect

    @contextmanager
    def frozen_only_connection():
        with connect() as connection:
            # The endpoint must obtain proofs from the round, even if the run's
            # candidate evidence is unavailable to this read path.
            import sqlite3
            connection.set_authorizer(lambda action, table, *rest:
                                      sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == 'selection_candidate'
                                      else sqlite3.SQLITE_OK)
            yield connection

    monkeypatch.setattr(chart_tracking, 'connect', frozen_only_connection)
    response = client.get('/api/v1/instruments/600001.SH/tracking-baselines')
    assert response.status_code == 200, response.text
    item = response.json()['items'][0]
    assert item['round_id'] == saved_round['round_id']
    assert item['historical_event_checks'] == [{
        'type': 'history_event', 'result': True, 'reference_fields': ['high', 'low'],
        'selected_event': original['selected_event'], 'event_checks': {'result': True},
        'history_event': {'match': 'latest', 'where': definition['all'][0]['history_event']['where'],
                          'price_path': {'reclaim': {'event_field': 'high'}}},
        'evidence': {'as_of': market.entry, 'price_basis': 'none'}}]
    assert item['historical_event_checks'][0]['selected_event']['date'] == market.days[40]
    assert item['historical_event_checks'][0]['selected_event']['low'] == market.data['600001.SH'][40]['low']
    assert item['historical_event_checks_truncated'] is False
    assert 'price_path_checks' not in response.text and 'source_lineage' not in response.text
    with core.connect() as connection:
        assert counts == [connection.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                          for table in ('job', 'tracking_round', 'tracking_event')]


def frozen_event():
    return {'type': 'history_event', 'result': False,
            'selected_event': {'date': '2026-01-06', 'open': 10, 'high': 12, 'low': 9, 'close': 11,
                               'offset': 2, 'price_basis': 'none'},
            'event_checks': {'result': True, 'evidence': {'large_proof': 'never-export'}},
            'history_event': {'match': 'earliest', 'where': {'field': 'limit_up', 'op': '==', 'value': True}},
            'evidence': {'as_of': '2026-01-08', 'price_basis': 'none', 'large_proof': 'never-export'}}


def test_forward_frozen_event_exports_original_prices_and_its_own_anchor():
    node=frozen_event()
    node['selected_event'].update(price_basis='forward_event_affine',anchor_date='2026-01-08',
        raw_open=11,raw_high=13,raw_low=10,raw_close=12)
    node['evidence'].update(price_basis='forward_event_affine',anchor_date='2026-01-08')
    items,truncated=chart_tracking._historical_event_checks(node,'2026-01-09')
    assert not truncated and len(items)==1
    assert items[0]['selected_event']==node['selected_event']
    assert items[0]['selected_event']['raw_close']==12
    assert items[0]['selected_event']['close']==11
    assert items[0]['evidence']['anchor_date']=='2026-01-08'
    node['selected_event']['anchor_date']='2026-01-09'
    assert chart_tracking._historical_event_checks(node,'2026-01-09')==([],False)
    node['selected_event']['anchor_date']='2026-01-08'
    node['selected_event']['raw_close']=None
    assert chart_tracking._historical_event_checks(node,'2026-01-09')==([],False)


@pytest.mark.parametrize('change', [
    lambda node: node['event_checks'].update(result=None),
    lambda node: node['event_checks'].update(result=1),
    lambda node: node.update(selected_event=None),
    lambda node: node['selected_event'].update(date='2026-02-30'),
    lambda node: node['selected_event'].update(date='2026-01-09'),
    lambda node: node['evidence'].update(as_of='2026-01-12'),
    lambda node: node['selected_event'].update(price_basis='unknown'),
    lambda node: node['evidence'].update(price_basis='unknown'),
    lambda node: node['selected_event'].update(high=float('inf')),
    lambda node: node['selected_event'].update(high=10 ** 400),
    lambda node: node['selected_event'].update(close=True),
    lambda node: node['selected_event'].update(close=13),
])
def test_incomplete_or_invalid_event_proofs_never_create_chart_anchors(change):
    node = frozen_event()
    change(node)
    assert chart_tracking._historical_event_checks(node, '2026-01-08') == ([], False)


def test_event_projection_preserves_false_result_and_reports_node_limit():
    node = frozen_event()
    original = deepcopy(node)
    items, truncated = chart_tracking._historical_event_checks({'checks': [node]}, '2026-01-08')
    assert len(items) == 1 and items[0]['result'] is False and truncated is False
    assert items[0]['reference_fields'] == ['close']
    assert 'never-export' not in json.dumps(items, allow_nan=False)
    assert node == original
    items, truncated = chart_tracking._historical_event_checks({'checks': [node] * 101}, '2026-01-08')
    assert truncated is True and 0 < len(items) < 101
    assert chart_tracking._historical_event_checks(None, '2026-01-08') == ([], False)


def test_event_reference_fields_are_unique_ordered_raw_fields_with_bounded_rules():
    node = frozen_event()
    node['history_event'].update(then={'all': [
        {'field': 'close', 'op': '<', 'right_event_field': 'low', 'right_event_change_pct': -3},
        {'any': [{'field': 'close', 'op': '>', 'right_event_field': 'high'},
                 {'field': 'close', 'op': '>=', 'right_event_field': 'close'}]}]},
        price_path={'reclaim': {'event_field': 'high'}})
    items, truncated = chart_tracking._historical_event_checks(node, '2026-01-08')
    assert truncated is False and items[0]['reference_fields'] == ['close', 'high', 'low']
    assert items[0]['selected_event'] == node['selected_event'], 'Percent offsets do not transform raw event OHLC'
    assert 'threshold' not in json.dumps(items)
    node['history_event']['then'] = {'any': [{'field': 'close', 'right_event_field': 'low'}] * 101}
    assert chart_tracking._historical_event_checks(node, '2026-01-08')[1] is True
