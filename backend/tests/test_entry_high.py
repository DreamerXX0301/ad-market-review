"""Frozen entry-day high alerts: immutable fixtures, no remote or production I/O."""
from copy import deepcopy
from contextlib import contextmanager
import json

import duckdb
import pytest

from app import core
from app.analytics.backtest_engine import availability
from app.domain.conditions import evaluate, specs, validate
from app.services import entry_high, tracking
from app.services.workspace_common import insert, js, pin
from test_workspace import ws, make_strategy, add_one, advance

A = '600001.SH'
FIELD = 'close_above_entry_high'


def rule(value=True): return {'field': FIELD, 'op': '==', 'value': value}


@pytest.fixture
def snapshots(ws, tmp_path, monkeypatch):
    market, client = ws
    entry_high.clear_entry_high_cache()
    current = ['market-entry']
    monkeypatch.setattr(market, 'status', lambda: {'snapshot_id': current[0]}, raising=False)

    def publish(generation, until=70, *, duplicate=False, missing=False, wrong_close=False, actions=()):
        current[0] = generation
        directory = tmp_path / 'published' / generation
        directory.mkdir(parents=True)
        daily = directory / 'daily.parquet'; action_file = directory / 'corporate-actions.parquet'
        con = duckdb.connect(':memory:')
        try:
            con.execute('CREATE TABLE daily(instrument_id VARCHAR,date VARCHAR,open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,volume DOUBLE)')
            records = []
            for symbol, values in market.data.items():
                for raw in values[:until + 1]:
                    if missing and symbol == A and raw['date'] == market.entry: continue
                    row = dict(raw)
                    if wrong_close and symbol == A and row['date'] == market.entry: row['close'] += .1
                    value = [symbol, row['date'], *(row[key] for key in ('open', 'high', 'low', 'close', 'volume'))]
                    records.append(value)
                    if duplicate and symbol == A and row['date'] == market.entry: records.append(list(value))
            con.executemany('INSERT INTO daily VALUES (?,?,?,?,?,?,?)', records)
            con.execute('COPY daily TO ? (FORMAT PARQUET)', [str(daily)])
            con.execute('CREATE TABLE actions(thscode VARCHAR,ex_date_ms BIGINT)')
            for observed in actions:
                import datetime as dt
                ms = int(dt.datetime.fromisoformat(observed).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
                con.execute('INSERT INTO actions VALUES (?,?)', [A, ms])
            con.execute('COPY actions TO ? (FORMAT PARQUET)', [str(action_file)])
        finally: con.close()
        identifiers = {}
        for module, file in [('analysis_bars', daily), ('corporate_actions', action_file)]:
            identifiers[module] = core.publish_snapshot(module=module, job_id='fixture-job', start=market.days[0], end=market.days[until],
                artifacts=[{'object_key': str(file.relative_to(tmp_path / 'published')), 'role': file.name,
                            'sha256': core.hash_file(file), 'byte_count': file.stat().st_size, 'row_count': len(records) if module == 'analysis_bars' else len(actions)}],
                lineage={'market_snapshot_id': generation}, price_basis='none' if module == 'analysis_bars' else 'not_applicable',
                quality='partial', scope='all', snapshot_id=module + '-' + generation, activate=True)
        return identifiers, daily, action_file

    publish('market-entry')
    yield market, client, publish
    entry_high.clear_entry_high_cache()


def round_record(client, round_):
    with core.connect() as db:
        return dict(db.execute('SELECT * FROM tracking_round WHERE round_id=?', (round_['round_id'],)).fetchone())


def legacy_round(client, strategy):
    run = client.post('/api/v1/strategies/' + strategy['strategy_id'] + '/runs', json={}).json()
    candidate = next(row for row in run['items'] if row['decision'] == 'matched')
    with core.connect() as db:
        source = dict(db.execute('SELECT * FROM dataset_snapshot WHERE snapshot_id=?', (run['primary_snapshot_id'],)).fetchone())
        basis = {'price_basis': 'none', 'comparison_basis_id': source['snapshot_id'] + ':none:None', 'entry_topics': [], 'signal_period': 'D'}
        row = {'round_id': 'legacy-round', 'strategy_id': strategy['strategy_id'], 'entry_strategy_version': run['strategy_version'],
               'origin_run_id': run['run_id'], 'instrument_id': candidate['instrument_id'], 'entry_business_date': run['business_date'],
               'entry_price_e8': int(round(candidate['close_price'] * 100000000)), 'entry_snapshot_id': source['snapshot_id'],
               'entry_policy_version': strategy['policy']['policy_version'], 'current_policy_version': strategy['policy']['policy_version'],
               'entry_basis_json': js(basis), 'created_at_ms': core.now_ms()}
        insert(db, 'tracking_round', row)
        pin(db, source['snapshot_id'], 'tracking', row['round_id'])
    return row


def test_new_round_freezes_exact_daily_high_not_signal_week_high(snapshots):
    market, client, _ = snapshots
    strategy = make_strategy(client, {'field': 'close', 'op': '>', 'value': 100})
    client.patch('/api/v1/strategies/' + strategy['strategy_id'], json={'period': 'W'})
    _, round_ = add_one(client, client.get('/api/v1/strategies/' + strategy['strategy_id']).json())
    detail = client.get('/api/v1/tracking-rounds/' + round_['round_id']).json()
    assert detail['entry_basis']['signal_date'] == market.days[69]
    assert detail['entry_high_date'] == market.entry
    assert detail['entry_high'] == market.data[A][70]['high']
    assert detail['entry_high_basis']['raw_high_e8'] == round(market.data[A][70]['high'] * 100000000)
    assert detail['entry_high_basis']['market_snapshot_id'] == 'market-entry'
    assert detail['entry_basis']['entry_high'] == detail['entry_high_basis']
    assert detail['entry_high'] != detail['entry_price']


def test_legacy_round_recovers_original_high_without_rewriting_basis(snapshots):
    market, client, publish = snapshots
    strategy = make_strategy(client)
    old = legacy_round(client, strategy)
    original_high = market.data[A][70]['high']
    market.data[A][70]['high'] = 999 # A later generation is expressly not the old anchor.
    market.data[A][71].update(high=500)
    market.data[A][72].update(close=original_high + .01, high=original_high + 1)
    publish('market-current', 72)
    advance(market, 72)
    response = client.patch('/api/v1/tracking-rounds/legacy-round/policy', json={'policy': {'alerts': [rule()], 'exits': []}})
    assert response.status_code == 200, response.text
    assert response.json()['entry_high'] == original_high
    before = round_record(client, old)['entry_basis_json']
    result = client.post('/api/v1/tracking-rounds/review', json={})
    assert result.status_code == 200, result.text
    assert result.json()['alerts'] == 1 and result.json()['ended'] == 0
    detail = client.get('/api/v1/tracking-rounds/legacy-round').json()
    assert detail['status'] == 'tracking'
    assert round_record(client, old)['entry_basis_json'] == before == old['entry_basis_json']
    alert = next(row for row in detail['events'] if row['event_type'] == 'alert')
    proof = alert['evidence']['metrics'][FIELD + '_evidence']['entry_high']
    assert proof['raw_high'] == original_high and proof['market_snapshot_id'] == 'market-entry'
    assert alert['evidence']['alerts']['checks'][0]['evidence']['entry_high'] == proof
    events = deepcopy(detail['events'])
    assert client.post('/api/v1/tracking-rounds/review', json={}).json()['reviewed'] == 0
    assert client.get('/api/v1/tracking-rounds/legacy-round').json()['events'] == events


def test_strict_greater_is_a_daily_state_and_alert_does_not_end_round(snapshots):
    market, client, publish = snapshots
    high = market.data[A][70]['high']
    strategy = make_strategy(client, policy={'alerts': [rule()], 'exits': []})
    _, round_ = add_one(client, strategy)
    market.data[A][71].update(close=high, high=high + 1)
    market.data[A][72].update(close=high + .01, high=high + 1)
    market.data[A][73].update(close=high + .02, high=high + 1)
    publish('market-current', 73); advance(market, 73)
    result = client.post('/api/v1/tracking-rounds/review', json={}).json()
    assert result['alerts'] == 2 and result['ended'] == 0
    detail = client.get('/api/v1/tracking-rounds/' + round_['round_id']).json()
    reviews = {row['business_date']: row for row in detail['events'] if row['event_type'] == 'review'}
    assert reviews[market.days[71]]['evidence']['metrics'][FIELD] is False
    assert reviews[market.days[72]]['evidence']['metrics'][FIELD] is True
    assert reviews[market.days[73]]['evidence']['metrics'][FIELD] is True
    assert detail['status'] == 'tracking'


@pytest.mark.parametrize('mode', ['missing', 'duplicate', 'wrong_close', 'corrupt', 'deleted'])
def test_legacy_missing_conflicting_or_corrupt_source_stays_unknown(snapshots, mode):
    market, client, publish = snapshots
    # Use a separate retained snapshot as the actual entry source.
    ids, daily, _ = publish('market-invalid', 70, missing=mode == 'missing', duplicate=mode == 'duplicate', wrong_close=mode == 'wrong_close')
    old = legacy_round(client, make_strategy(client))
    if mode == 'corrupt': daily.write_bytes(b'corrupt immutable source')
    if mode == 'deleted': daily.unlink()
    publish('market-current', 72)
    proof = entry_high.entry_high_for(old, entry_high.load_entry_highs([old]))
    assert proof['state'] == 'unknown' and proof['raw_high'] is None
    assert proof['entry_snapshot_id'] == ids['analysis_bars']
    assert proof['reason']


@pytest.mark.parametrize('mode', ['actions', 'missing_actions', 'missing_quote', 'duplicate_quote', 'suspended'])
def test_insufficient_comparison_evidence_never_turns_into_false(snapshots, mode):
    market, client, publish = snapshots
    strategy = make_strategy(client, policy={'alerts': [rule(False)], 'exits': []})
    _, round_ = add_one(client, strategy)
    high = market.data[A][70]['high']
    market.data[A][71].update(close=high + 2, high=high + 3)
    _, _, actions = publish('market-current', 71, actions=[market.days[71]] if mode == 'actions' else [])
    if mode == 'missing_actions': actions.unlink()
    if mode == 'missing_quote': market.data[A] = [row for row in market.data[A] if row['date'] != market.days[71]]
    if mode == 'duplicate_quote': market.data[A].append(dict(market.data[A][71]))
    if mode == 'suspended': market.data[A][71]['volume'] = 0
    # Even if an old accessor returned [], validated source action dates win.
    advance(market, 71)
    response = client.post('/api/v1/tracking-rounds/review', json={})
    assert response.status_code == 200, response.text
    assert response.json()['alerts'] == 0
    detail = client.get('/api/v1/tracking-rounds/' + round_['round_id']).json()
    review = next(row for row in detail['events'] if row['event_type'] == 'review' and row['business_date'] == market.days[71])
    assert review['evidence']['metrics'][FIELD] is None
    assert review['evidence']['metrics'][FIELD + '_reason']


def test_new_frozen_high_survives_unavailable_original_artifact(snapshots):
    market, client, publish = snapshots
    _, round_ = add_one(client, make_strategy(client))
    before = client.get('/api/v1/tracking-rounds/' + round_['round_id']).json()
    (core.published_dir() / 'market-entry' / 'daily.parquet').unlink()
    after = client.get('/api/v1/tracking-rounds/' + round_['round_id']).json()
    assert after['entry_high_basis'] == before['entry_high_basis']
    assert after['entry_high_state'] == 'ready'


def test_alert_only_scope_and_backtest_are_explicit(ws):
    market, client = ws
    field = next(row for row in specs() if row['field'] == FIELD)
    assert field['type'] == 'boolean' and field['alerts_only'] is True and field['hidden_in_builder'] is True
    assert client.post('/api/v1/strategies', json={'name': '错误选股', 'definition': rule()}).status_code == 422
    strategy = make_strategy(client)
    assert client.patch('/api/v1/strategies/' + strategy['strategy_id'], json={'definition': {'any': [rule(), {'field': 'close', 'op': '>', 'value': 1}]}}).status_code == 422
    assert client.patch('/api/v1/strategies/' + strategy['strategy_id'], json={'policy': {'exits': [rule()]}}).status_code == 422
    assert availability(market, {'period': 'D', 'definition': rule()})['available'] is False
    assert evaluate(validate(rule(False)), {FIELD: None})['result'] is None


def test_action_range_is_checked_per_round_without_poisoning_newer_rounds(snapshots):
    market, client, publish = snapshots
    _, round_ = add_one(client, make_strategy(client))
    row = round_record(client, round_)
    anchor = entry_high.entry_high_for(row, entry_high.load_entry_highs([row]))
    publish('market-current', 71)
    with core.connect() as db:
        action_snapshot = db.execute("SELECT snapshot_id FROM published_head WHERE module='corporate_actions'").fetchone()[0]
    proof = entry_high.load_action_evidence(action_snapshot, [A], '2025-01-01', market.days[71], 'market-current')[A]
    assert proof['state'] == 'ready' and proof['coverage_start'] == market.days[0]
    # One out-of-range older round cannot invalidate the source for a newer round.
    older = {**row, 'entry_business_date': '2025-01-01'}
    current_quote = {'date': market.days[71], 'close': anchor['raw_high'] + 1, 'volume': 100}
    metrics = entry_high.condition_metrics(older, market.days[71], current_quote, anchor, current_quote['close'], 'not_required', None, proof)
    assert metrics[FIELD] is None and '未覆盖' in metrics[FIELD + '_reason']
    metrics = entry_high.condition_metrics(row, market.days[71], current_quote, anchor, current_quote['close'], 'not_required', None, proof)
    assert metrics[FIELD] is True


def test_high_and_action_file_io_are_prepared_outside_write_transactions(snapshots, monkeypatch):
    market, client, publish = snapshots
    connections = []; original_connect = tracking.connect; original_artifact = entry_high._artifact
    @contextmanager
    def monitored():
        with original_connect() as connection:
            connections.append(connection)
            try: yield connection
            finally: connections.remove(connection)
    def artifact(*args, **kwargs):
        assert not any(connection.in_transaction for connection in connections)
        return original_artifact(*args, **kwargs)
    monkeypatch.setattr(tracking, 'connect', monitored)
    monkeypatch.setattr(entry_high, '_artifact', artifact)
    strategy = make_strategy(client, policy={'alerts': [rule()], 'exits': []})
    _, round_ = add_one(client, strategy)
    publish('market-current', 71); advance(market, 71)
    original_compare = tracking._entry_comparison
    def comparable(*args, **kwargs):
        assert not any(connection.in_transaction for connection in connections)
        return original_compare(*args, **kwargs)
    monkeypatch.setattr(tracking, '_entry_comparison', comparable)
    response = client.post('/api/v1/tracking-rounds/review', json={})
    assert response.status_code == 200, response.text
