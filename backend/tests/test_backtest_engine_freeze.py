"""An engine upgrade cannot reuse or execute an incompatible frozen run."""
import pytest

from app.core import connect, published_dir
from app.analytics.backtest_engine import VERSION
from test_backtests import creation_context


def create_legacy(store, payload, monkeypatch):
    # Model the previous factory at insertion time. Never disable the frozen
    # input trigger or mutate engine/hash fields on an existing run.
    original_digest = store.digest
    def old_digest(value):
        if isinstance(value, dict) and 'engine_version' in value:
            value = {key: item for key, item in value.items() if key != 'engine_version'}
        return original_digest(value)
    with monkeypatch.context() as legacy:
        legacy.setattr(store, 'VERSION', 'restricted-next-open-v3-history-events')
        legacy.setattr(store, 'digest', old_digest)
        return store.create_backtest(payload)


def test_new_engine_does_not_reuse_old_active_logical_key(creation_context, monkeypatch):
    store, payload = creation_context
    old = create_legacy(store, payload, monkeypatch)
    with connect() as connection:
        connection.execute("UPDATE job SET status='paused' WHERE job_id=?", (old['job_id'],))
    new = store.create_backtest({**payload, 'request_key': 'new-v4-click'})
    assert new['backtest_id'] != old['backtest_id'] and not new.get('reused')
    with connect() as connection:
        rows = connection.execute('SELECT engine_version,input_sha256 FROM backtest_run ORDER BY created_at_ms').fetchall()
        assert len(rows) == 2 and rows[0]['input_sha256'] != rows[1]['input_sha256']
        assert rows[1]['engine_version'] == VERSION
    with pytest.raises(store.BacktestRequestConflict, match='引擎版本不同'):
        store.create_backtest(payload)


@pytest.mark.parametrize('status', ['queued', 'running'])
def test_old_incomplete_engine_refuses_calculation_and_publication(creation_context, monkeypatch, status):
    store, payload = creation_context
    old = create_legacy(store, payload, monkeypatch)
    with connect() as connection:
        connection.execute("UPDATE backtest_run SET status=? WHERE backtest_id=?", (status, old['backtest_id']))
        connection.execute("UPDATE job SET status='running' WHERE job_id=?", (old['job_id'],))
    def never(*args, **kwargs): pytest.fail('incompatible frozen job must not touch market, simulate, or publish')
    monkeypatch.setattr(store, 'MarketData', never)
    monkeypatch.setattr(store, 'run_backtest', never)
    monkeypatch.setattr(store, 'publish_snapshot', never)
    with pytest.raises(ValueError, match='手动新建'):
        store.execute_backtest(old['job_id'])
    saved = store.get_backtest(old['backtest_id'])
    assert saved['status'] == 'failed'
    assert saved['engine_version'] == 'restricted-next-open-v3-history-events'
    assert '手动新建' in saved['error']
    assert not (published_dir()/'backtests').exists()
    with connect() as connection:
        assert connection.execute("SELECT count(*) FROM dataset_snapshot WHERE module='backtest_result'").fetchone()[0] == 0


def test_old_completed_engine_remains_readable_without_execution(creation_context, monkeypatch):
    store, payload = creation_context
    old = create_legacy(store, payload, monkeypatch)
    with connect() as connection:
        connection.execute("UPDATE job SET status='running' WHERE job_id=?", (old['job_id'],))
    report = {'engine_version': 'restricted-next-open-v3-history-events',
              'stats': {'total_return': 1.2, 'trade_count': 0}, 'data_quality': {},
              **{key: [] for key in ('signals', 'orders', 'fills', 'positions', 'trades', 'equity')}}
    # Produce the completed legacy result through the actual immutable publish
    # pipeline, rather than bypassing its completed-result database gates.
    with monkeypatch.context() as legacy:
        legacy.setattr(store, 'VERSION', 'restricted-next-open-v3-history-events')
        legacy.setattr(store, 'MarketData', lambda *args, **kwargs: object())
        legacy.setattr(store, 'run_backtest', lambda *args, **kwargs: report)
        store.execute_backtest(old['job_id'])
    monkeypatch.setattr(store, 'run_backtest', lambda *args, **kwargs: pytest.fail('completed runs are never recomputed'))
    saved = store.get_backtest(old['backtest_id'])
    assert saved['engine_version'] == 'restricted-next-open-v3-history-events'
    assert saved['stats']['total_return'] == 1.2
    assert store.execute_backtest(old['job_id']) == saved
