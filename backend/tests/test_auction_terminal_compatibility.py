"""来源两组终态穿过自动采集、只读API与标准恢复的合成兼容验收。

不读取真实响应、凭据或运行目录；每份原件均由本文件构造。
"""
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import json
import sqlite3

import pytest

from app import core, worker
from app.services import auction_automation as automation
from app.services import source_service, task_service as tasks
from app.services.auction_collection import canonical
from test_auction_snapshots import DAY, isolated as auction_environment


CODES = [f'{600001 + index:06d}.SH' for index in range(100)]
AMOUNT = 105000


@pytest.fixture
def compatible(auction_environment, monkeypatch):
    environment = auction_environment
    monkeypatch.setattr(automation, 'now_china', lambda: environment.clock[0])
    monkeypatch.setenv('SHIXU_CREDENTIAL_MASTER_KEY_FILE', str(core.data_dir() / 'unused-synthetic-master'))
    return environment


def source_envelope(environment, codes, phase, status):
    values = [{'thscode': code, 'ticker': code[:6], 'name': '兼容性合成样本' + code[:6],
               'auction_price': 10.5, 'auction_pct': 5, 'auction_volume': 100,
               'auction_amount': AMOUNT, 'auction_unmatched': 200,
               'auction_turnover_pct': .2, 'auction_yesterday_ratio_pct': 12.5,
               'auction_volume_ratio': 1.3, 'pre_close_price': 10,
               'open_price': 10.5, 'last_price': 10.5, 'float_market_cap': 1000000000}
              for code in codes]
    return {'code': 0, 'data': {'timestamp': int(environment.clock[0].timestamp() * 1000),
            'auction_phase': phase, 'data_status': status, 'total': len(values), 'item': values}}


def install_source(environment, phase, status):
    originals = []
    def response(path, params):
        if path.endswith('/auction/snapshot'):
            body = source_envelope(environment, params['thscodes'].split(','), phase, status)
            originals.append((body, deepcopy(body)))
            return body
    environment.source.hook = response
    return originals


def create_automatic(environment, codes):
    config = automation.read_view()
    # Default candidates come from saved pre-start watchlist, never manual IDs.
    with core.connect() as connection:
        for index, code in enumerate(codes):
            connection.execute('''INSERT INTO watchlist_item(instrument_id,source_kind,source_name,
                added_business_date,event_basis_json,created_at_ms) VALUES(?,'stock','合成默认名单',?,'{}',?)''',
                (code, DAY, int(environment.clock[0].timestamp()*1000)-index-1))
    automation.update_config({'mode': 'daily', 'expected_revision': config['revision']})
    assert environment.source.calls == []
    automation.tick()
    assert environment.source.calls == []
    job = tasks.claim_next('terminal-compatibility-worker')
    assert job and job['target_date'] == DAY
    assert json.loads(job['parameters_json'])['automatic'] is True
    return job


def stored_snapshot(database=None):
    if database is None:
        with core.connect() as connection:
            return dict(connection.execute('SELECT * FROM market_auction_snapshot WHERE trade_date=?', (DAY,)).fetchone())
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA recursive_triggers=ON')
        connection.execute('PRAGMA busy_timeout=10000')
        return dict(connection.execute('SELECT * FROM market_auction_snapshot WHERE trade_date=?', (DAY,)).fetchone())


def assert_source_states(payload, phase, status):
    assert payload['quotes']['auction_phase'] == phase
    assert payload['quotes']['data_status'] == status


@pytest.mark.parametrize('phase,status', [('final', 'ready'), ('matched', 'final')])
def test_both_terminal_pairs_pass_full_automatic_capture_api_and_backup_restore(compatible, tmp_path, monkeypatch, phase, status):
    originals = install_source(compatible, phase, status)
    job = create_automatic(compatible, CODES)
    worker.dispatch(job)
    assert tasks.task_view(job['job_id'])['status'] == 'succeeded'
    assert [path for path, _ in compatible.source.calls] == [
        '/api/a-share/calendar/trading-days', '/api/a-share/auction/snapshot',
        '/api/a-share/auction/short-term-benchmark']
    assert originals[0][0] == originals[0][1]
    before = stored_snapshot()
    assert before['quality'] == 'complete'
    assert sha256(before['payload_json'].encode()).hexdigest() == before['content_sha256']
    payload = json.loads(before['payload_json'])
    assert_source_states(payload, phase, status)
    assert payload['quotes']['summary']['requested'] == 100
    assert payload['quotes']['summary']['valid'] == 100
    assert payload['quotes']['summary']['amount'] == AMOUNT * 100
    assert [item['instrument_id'] for item in payload['quotes']['items']] == CODES
    assert all(item['unit_check']['state'] == 'verified' and item['quote_at'] is None
               for item in payload['quotes']['items'])
    original = next(item for item in payload['evidence'] if item['name'] == 'auction')
    assert original['body'] == originals[0][1]
    assert original['params'] == {'stage': 'final', 'thscodes': ','.join(CODES)}
    assert original['body']['data']['auction_phase'] == phase
    assert original['body']['data']['data_status'] == status
    assert original['sha256'] == sha256(canonical(original['body'])).hexdigest()

    # 成功后的读取与重放只用已保存结果；任何再次请求来源都会使测试失败。
    calls = list(compatible.source.calls)
    monkeypatch.setattr(source_service, 'client', lambda *_args: pytest.fail('retained reads cannot fetch'))
    response = compatible.client.get('/api/v1/market/auction', params={'date': DAY})
    assert response.status_code == 200
    view = response.json()
    assert view['state'] == 'available' and view['snapshot']['quality'] == 'complete'
    assert view['snapshot']['evidence_count'] == 3
    assert 'evidence' not in view['snapshot']['payload']
    assert_source_states(view['snapshot']['payload'], phase, status)
    automation.tick()
    assert compatible.source.calls == calls

    monkeypatch.syspath_prepend(str(core.PROJECT_ROOT / 'scripts'))
    from backup import perform_backup
    from restore import perform_restore, verify_backup
    bundle = tmp_path.parent / (tmp_path.name + '-terminal-bundle')
    restored = tmp_path.parent / (tmp_path.name + '-terminal-restored')
    perform_backup(core.data_dir(), bundle)
    verify_backup(bundle)
    perform_restore(bundle, restored)
    after = stored_snapshot(restored / 'business/app.sqlite3')
    assert after == before
    restored_payload = json.loads(after['payload_json'])
    assert_source_states(restored_payload, phase, status)
    assert next(item['body'] for item in restored_payload['evidence'] if item['name'] == 'auction') == originals[0][1]
    monkeypatch.setenv('SHIXU_DATA_DIR', str(restored))
    restored_response = compatible.client.get('/api/v1/market/auction', params={'date': DAY})
    assert restored_response.status_code == 200
    restored_view = restored_response.json()
    assert restored_view['state'] == 'available'
    assert restored_view['snapshot'] == view['snapshot']
    assert compatible.source.calls == calls


@pytest.mark.parametrize('phase,status', [
    ('matched', 'ready'), ('final', 'final'), ('closed', 'not_ready'),
    ('closed', 'ready'), ('matched', 'not_ready'), ('live', 'final')])
def test_nonempty_wrong_or_unfinished_pairs_never_publish_automatic_success(compatible, phase, status):
    originals = install_source(compatible, phase, status)
    job = create_automatic(compatible, CODES[:1])
    with pytest.raises(ValueError):
        worker.dispatch(job)
    tasks.finish(job['job_id'], 'failed', error='合成未核验终态', lease_owner=job['lease_owner'])
    assert originals[0][0] == originals[0][1]
    assert [path for path, _ in compatible.source.calls] == [
        '/api/a-share/calendar/trading-days', '/api/a-share/auction/snapshot']
    response = compatible.client.get('/api/v1/market/auction', params={'date': DAY})
    assert response.status_code == 200
    assert response.json()['state'] == 'failed' and response.json()['snapshot'] is None
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_auction_snapshot').fetchone()[0] == 0
        if (phase, status) in [('matched', 'ready'), ('final', 'final')]:
            run = connection.execute('SELECT * FROM market_auction_automation_run WHERE job_id=?', (job['job_id'],)).fetchone()
            assert run['state'] == 'failed' and not run['automatic_control']
            assert run['retry_after_at_ms'] is None


def test_not_ready_then_matched_final_retries_original_job_and_finishes(compatible):
    install_source(compatible, 'closed', 'not_ready')
    job = create_automatic(compatible, CODES[:1])
    with pytest.raises(automation.AuctionNotReady):
        worker.dispatch(job)
    tasks.finish(job['job_id'], 'failed', error='合成来源尚未就绪', lease_owner=job['lease_owner'])
    compatible.clock[0] += timedelta(seconds=10)
    originals = install_source(compatible, 'matched', 'final')
    automation.tick()
    recovered = tasks.claim_next('terminal-compatible-retry-worker')
    assert recovered['job_id'] == job['job_id']
    worker.dispatch(recovered)
    assert tasks.task_view(job['job_id'])['status'] == 'succeeded'
    assert_source_states(json.loads(stored_snapshot()['payload_json']), 'matched', 'final')
    assert originals[0][0] == originals[0][1]
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_auction_capture').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM job').fetchone()[0] == 1
        run = connection.execute('SELECT * FROM market_auction_automation_run WHERE job_id=?', (job['job_id'],)).fetchone()
        assert run['attempts'] == 2 and run['state'] == 'available' and run['retry_after_at_ms'] is None
