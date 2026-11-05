"""Read-only AI proofs: original members, formal close pools, and noon's causal prefix."""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
import sqlite3

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app import core
from app.services import ai_read_tools as tools, intraday_service as live
from app.services.noon_collection import CHINA
from test_auction_topic_summary import isolated, publish, topic, GENERATION, PRIOR
from test_intraday_close import source

DAY = '2026-11-05'
A, B, C = '600001.SH', '600002.SH', '600003.SH'


def capture(slot):
    from test_intraday_sector_metrics import _live, _quote
    value, _ = _live()
    clock = next(row['start'] for row in live.SLOTS if row['key'] == slot)
    start = datetime.fromisoformat(DAY + 'T' + clock + ':00+08:00')
    value.update(version=live.VERSION, id='intraday_' + sha256(slot.encode()).hexdigest()[:32], slot=slot,
                 collected_start_at=start.isoformat(), collected_end_at=(start + timedelta(seconds=20)).isoformat(),
                 member_reference={'generation': GENERATION, 'date': PRIOR}, quality='partial')
    value['comparison_input']['stocks'].append(_quote(C, 9))
    value['stocks'] = [{**row, 'name': '合成股票' + row['instrument_id']} for row in value['comparison_input']['stocks']]
    value['sectors'] = []  # The real retained industry quote is outside every short ranking.
    value['summary'] = {}
    value['source_checks'] = [{'domain': key + '_page_1', 'state': 'accepted', 'response_sha256': 'a' * 64,
                               'source_time': (start + timedelta(seconds=5)).isoformat()}
                              for key in ('limit_up', 'limit_break', 'limit_down')]
    value['board_pool_evidence'] = {'version': 'intraday-board-pools-v1',
        **{key: {'state': 'observed', 'count': 1, 'items': [{'instrument_id': code,
                    **({'continue_day_cnt': 2} if key == 'limit_up' else {})}]}
           for key, code in (('limit_up', A), ('limit_break', B), ('limit_down', C))}}
    return value


def save_full(value):
    at = datetime.fromisoformat(value['collected_start_at'])
    slot = next(row for row in live.SLOTS if row['key'] == value['slot'])
    with core.connect() as connection:
        job = live._enqueue(connection, 'ai-' + value['slot'], DAY, slot, at)
        connection.execute("UPDATE job SET status='succeeded',finished_at_ms=? WHERE job_id=?",
                           (int(datetime.fromisoformat(value['collected_end_at']).timestamp() * 1000) + 10, job))
        connection.execute('UPDATE market_intraday_capture SET snapshot_id=?,payload_json=?,content_sha256=? WHERE job_id=?',
                           (value['id'], core.dumps(value), core.digest(value), job))


@pytest.mark.parametrize('split', [False, True])
def test_original_reference_enriches_query_outside_rankings_and_stock_labels_without_current_head(isolated, monkeypatch, split):
    rows = [topic('884306.TI', members=[A, B], name='合成原行业'),
            topic('885001.TI', 'concept', [A, B], name='合成概念甲'),
            topic('885002.TI', 'concept', [A], name='合成概念乙')]
    publish(isolated, rows, split=split)
    value = capture('1030')
    save_full(value)
    monkeypatch.delenv('SHIXU_AI_READ_SQLITE_PATH', raising=False)
    monkeypatch.setattr(tools, 'local_now', lambda: datetime.fromisoformat(DAY + 'T16:00:00+08:00'))
    monkeypatch.setattr(tools, '_market', lambda *args: pytest.fail('AI must not follow a current head'))
    monkeypatch.setattr(core, 'connect', lambda: pytest.fail('AI must use its selected read-only connection'))
    result = tools.execute_read_tool('read_intraday_data', {'date': DAY, 'slot': '1030', 'query': '合成原行业'})
    row = result['evidence'][0]['data']['sector_samples'][0]
    assert row['name'] == '合成原行业' and row['up_ratio'] == 50
    assert row['limit_up_count'] == row['broken_count'] == 1
    assert row['breadth_state'] == row['broken_count_state'] == 'observed'
    binding = result['source_bindings']['1030']['member_reference']
    assert binding['generation'] == GENERATION and len(binding['reference_sha256']) == 64
    result = tools.execute_read_tool('read_intraday_data', {'date': DAY, 'slot': '1030', 'query': A})
    stock = result['evidence'][0]['data']['stock_samples'][0]
    assert stock['limit_up_state'] == 'sealed' and stock['board'] == 'main'
    assert stock['themes'][0]['name'] == '合成原行业'
    assert {row['name'] for row in stock['themes'][1:]} == {'合成概念甲', '合成概念乙'}
    assert all(row['member_date'] <= PRIOR for row in stock['themes'])
    down = tools.execute_read_tool('read_intraday_data', {'date': DAY, 'slot': '1030', 'query': C})
    assert down['evidence'][0]['data']['stock_samples'][0]['limit_down_state'] == 'sealed'


@pytest.mark.parametrize('damage', ['descriptor_sha', 'future_identity', 'unanchored_source'])
def test_missing_original_proof_keeps_prices_and_explains_unknown_metrics(isolated, monkeypatch, damage):
    folder = publish(isolated, [topic('884306.TI', members=[A, B], name='合成原行业')])
    value = capture('1030')
    if damage == 'descriptor_sha':
        path = folder / 'reference.json'
        path.write_text(path.read_text() + '\n')
    elif damage == 'future_identity':
        value['member_reference']['date'] = DAY
    else:
        value['member_reference']['generation'] = 'market-unanchored'
    save_full(value)
    monkeypatch.delenv('SHIXU_AI_READ_SQLITE_PATH', raising=False)
    monkeypatch.setattr(tools, 'local_now', lambda: datetime.fromisoformat(DAY + 'T16:00:00+08:00'))
    monkeypatch.setattr(tools, '_market', lambda *args: pytest.fail('must not substitute current members'))
    result = tools.execute_read_tool('read_intraday_data', {'date': DAY, 'slot': '1030', 'query': '884306'})
    row = result['evidence'][0]['data']['sector_samples'][0]
    assert row['change_pct'] == 1 and row['limit_up_count'] is None
    assert row['breadth_state'] == row['broken_count_state'] == 'unknown'
    assert row['breadth_reason'] and row['broken_count_reason']


def test_seven_enriched_slots_share_one_original_proof_and_fit_without_dropping_phases(isolated, monkeypatch):
    publish(isolated, [topic('884306.TI', members=[A, B], name='合成原行业')])
    for slot in live.SLOTS:
        value = capture(slot['key'])
        value['sectors'] = [{'instrument_id': '884306.TI', 'kind': 'industry', 'name': '合成原行业', 'change_pct': 1}]
        save_full(value)
    monkeypatch.delenv('SHIXU_AI_READ_SQLITE_PATH', raising=False)
    monkeypatch.setattr(tools, 'local_now', lambda: datetime.fromisoformat(DAY + 'T16:00:00+08:00'))
    traces = []
    original = tools._connect
    def connect(path):
        connection = original(path)
        if connection:
            connection.set_trace_callback(traces.append)
        return connection
    monkeypatch.setattr('app.services.ai_intraday_read._connect', connect)
    result = tools.execute_read_tool('read_intraday_data', {'date': DAY, 'slot': 'all', 'query': ''})
    assert len(result['evidence']) == 7 and len(core.dumps(result).encode()) <= tools.MAX_RESULT_BYTES
    assert all(len(core.dumps(row['data']).encode()) <= 3 * 1024 for row in result['evidence'])
    for row in result['evidence']:
        if row['data']['sector_samples']:
            assert row['data']['sector_samples'][0]['up_ratio'] == 50
        else:
            assert row['data']['omitted_for_budget']['sector_samples'] > 0
    assert sum('SELECT s.snapshot_id,s.data_end,s.manifest_path' in sql for sql in traces) == 1


def test_change_classification_counts_all_40_events_before_ui_32_and_ai_3_limits(isolated, monkeypatch):
    from test_intraday_sector_metrics import _quote
    symbols = [f'{600000 + index}.SH' for index in range(1, 41)]
    publish(isolated, [topic('884306.TI', members=symbols, name='合成完整行业'),
                       topic('885001.TI', 'concept', symbols[:20], name='合成部分概念')])
    for slot, price in (('1000', 10), ('1030', 11)):
        value = capture(slot)
        value['comparison_input']['stocks'] = [_quote(code, price) for code in symbols]
        value['stocks'] = [{**row, 'name': '合成样本' + row['instrument_id']} for row in value['comparison_input']['stocks']]
        for key, pool in value['board_pool_evidence'].items():
            if key == 'version':
                continue
            items = [{'instrument_id': code, 'continue_day_cnt': 1} for code in symbols] if slot == '1030' and key == 'limit_up' else []
            pool.update(items=items, count=len(items))
        save_full(value)
    monkeypatch.delenv('SHIXU_AI_READ_SQLITE_PATH', raising=False)
    monkeypatch.setattr(tools, 'local_now', lambda: datetime.fromisoformat(DAY + 'T16:00:00+08:00'))
    result = tools.execute_read_tool('read_intraday_data', {'date': DAY, 'slot': '1030', 'query': ''})
    group = result['evidence'][0]['data']['board_changes']['groups']['new_sealed']
    assert group['count'] == 40 and len(group['samples']) <= 3
    assert group['classification']['total'] == group['classification']['classified'] == 40
    assert group['classification']['missing'] == 0
    assert group['classification']['industries'][0]['count'] == 40
    assert group['classification']['concepts'][0]['count'] == 20
    assert result['evidence'][0]['data']['historical_metrics_state'] == 'not_in_snapshot'


@pytest.fixture
def close_read_source(source, tmp_path, monkeypatch):
    root, directory, reference, publish_source = source
    up = [f'{600000 + index:06}.SH' for index in range(1, 86)]
    broken = [f'{601000 + index:06}.SH' for index in range(1, 48)]
    reference['limitup'] = [{'thscode': code, 'name': '合成封板股' + code[:6], 'last_price': 11,
                             'continue_day_cnt': 3 if index == 0 else 1} for index, code in enumerate(up)]
    reference['breakpool'] = [{'thscode': code, 'name': '合成炸板股' + code[:6], 'last_price': 10.5} for code in broken]
    reference['pool_coverage']['limitup']['declared_total'] = 85
    reference['pool_coverage']['breakpool']['declared_total'] = 47
    rows = [{'instrument_id': code, 'date': day, 'open': 10., 'high': 11., 'low': 9.,
             'close': 10. if day == '2026-11-04' else 11. if code in up else 10.5,
             'volume': 100., 'amount': 1000., 'source': 'fuyao', 'price_basis': 'none'}
            for day in ('2026-11-04', DAY) for code in up + broken]
    temporary = tmp_path / 'close-prices.parquet'
    pq.write_table(pa.Table.from_pylist(rows), temporary)
    digest = sha256(temporary.read_bytes()).hexdigest()
    target = root / f'market-objects/daily/{digest[:2]}/{digest}.parquet'
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary.rename(target)
    descriptor = {'version': 'market-partitions-v1', 'role': 'daily.parquet', 'partitions': [{
        'month': DAY[:7], 'data_begin': '2026-11-04', 'data_end': DAY, 'sha256': digest,
        'bytes': target.stat().st_size, 'object_key': str(target.relative_to(root))}]}
    (directory / 'daily.manifest.json').write_text(json.dumps(descriptor))
    publish_source()
    path = tmp_path / 'close-read.sqlite3'
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE market_intraday_brief(trade_date,slot,source_sha256,body_json,generated_at_ms)')
    stored = {'brief': {'slot': 'close'}, 'anchor': {'date': DAY, 'slot': 'close', 'id': directory.name,
               'collected_end_at': DAY + 'T15:00:00+08:00'}}
    connection.execute('INSERT INTO market_intraday_brief VALUES(?,?,?,?,?)',
                       (DAY, 'close', 'a' * 64, core.dumps(stored), 1))
    connection.commit()
    monkeypatch.setenv('SHIXU_SQLITE_PATH', str(path))
    monkeypatch.delenv('SHIXU_AI_READ_SQLITE_PATH', raising=False)
    monkeypatch.setattr(tools, 'local_now', lambda: datetime.fromisoformat(DAY + 'T18:00:00+08:00'))
    monkeypatch.setattr(tools, '_market', lambda *args: pytest.fail('close must not read current head'))
    yield connection, directory, target
    connection.close()


def test_close_tool_restores_85_and_47_from_real_parquet_without_noon_review(close_read_source):
    connection, directory, _ = close_read_source
    original = connection.execute('SELECT body_json FROM market_intraday_brief').fetchone()[0]
    result = tools.execute_read_tool('read_market_data', {'date': DAY, 'section': 'close'})
    assert result['ok'] and result['state'] == 'available'
    evidence = next(row for row in result['evidence'] if row['kind'] == 'closing_pools_brief')
    assert evidence['data']['pools']['limit_up']['count'] == 85
    assert evidence['data']['pools']['limit_break']['count'] == 47
    assert evidence['data']['pools']['limit_down']['count'] == 0
    assert all(len(pool['samples']) <= 3 for pool in evidence['data']['pools'].values())
    assert result['source_bindings']['closing_pools']['snapshot_id'] == directory.name
    assert len(result['source_bindings']['closing_pools']['reference_sha256']) == 64
    assert len(evidence['data']['board_changes']['groups']) == 5
    assert evidence['data']['board_changes']['state'] == 'unknown'  # No 14:30 source was retained.
    assert len(core.dumps(evidence['data']).encode()) <= 2304
    assert len(core.dumps(result).encode()) <= tools.MAX_RESULT_BYTES
    assert connection.execute('SELECT body_json FROM market_intraday_brief').fetchone()[0] == original


def test_close_tool_chains_every_same_day_phase_and_keeps_numeric_facts_when_dense(close_read_source):
    from test_ai_market_brief import auction_payload, noon_payload
    from test_ai_intraday_read import _snapshot, _save
    connection, _, _ = close_read_source
    for table, factory, identifier in (('market_auction_snapshot', auction_payload, 'auction'),
                                      ('market_noon_snapshot', noon_payload, 'noon')):
        connection.execute('CREATE TABLE ' + table + '(snapshot_id,trade_date,collected_start_at,collected_end_at,content_sha256,payload_json)')
        value = factory()
        original_day = value['date']
        value = json.loads(json.dumps(value).replace(original_day, DAY))
        connection.execute('INSERT INTO ' + table + ' VALUES(?,?,?,?,?,?)', (identifier, DAY,
            value['collected_start_at'], value['collected_end_at'], core.digest(value), core.dumps(value)))
    connection.executescript('''CREATE TABLE job(job_id TEXT PRIMARY KEY,status TEXT,finished_at_ms INTEGER);
        CREATE TABLE market_intraday_capture(trade_date,slot,job_id,snapshot_id,content_sha256,payload_json);''')
    for slot in live.SLOTS:
        value = _snapshot(slot['key'])
        original_day = value['date']
        value = json.loads(json.dumps(value).replace(original_day, DAY))
        value['sectors'] = value['strong_sectors'] = [{**value['sectors'][0], 'instrument_id': f'{881000 + index}.TI',
            'name': '合成全天串联容量测试行业' + str(index)} for index in range(30)]
        _save(connection, value)
    before = connection.execute('SELECT payload_json FROM market_intraday_capture ORDER BY slot').fetchall()
    result = tools.execute_read_tool('read_market_data', {'date': DAY, 'section': 'close'})
    kinds = [row['kind'] for row in result['evidence']]
    assert kinds == ['auction_brief', 'intraday:0935', 'intraday:1000', 'intraday:1030', 'intraday:1100',
                     'noon_brief', 'intraday:1330', 'intraday:1400', 'intraday:1430', 'closing_pools_brief']
    assert result['summary']['close_context']['cutoff_at'] == DAY + 'T15:00:00+08:00'
    close = result['evidence'][-1]['data']
    assert close['pools']['limit_up']['count'] == 85 and close['pools']['limit_break']['count'] == 47
    assert len(core.dumps(result).encode()) <= tools.MAX_RESULT_BYTES
    assert connection.execute('SELECT payload_json FROM market_intraday_capture ORDER BY slot').fetchall() == before
    connection.execute('UPDATE job SET finished_at_ms=? WHERE job_id=?',
        (int(datetime.fromisoformat(DAY + 'T15:01:00+08:00').timestamp() * 1000), 'intraday-1330'))
    connection.commit()
    later = tools.execute_read_tool('read_market_data', {'date': DAY, 'section': 'close'})
    assert not any(row['kind'] == 'intraday:1330' for row in later['evidence'])
    assert later['summary']['close_context']['phases']['1330']['phase_state'] == 'not_collected'


@pytest.mark.parametrize('damage', ['reference_sha', 'price_sha', 'wrong_date', 'denied', 'manifest_denied'])
def test_close_tool_never_replaces_a_damaged_or_denied_original_source(close_read_source, damage):
    connection, directory, target = close_read_source
    if damage == 'reference_sha':
        path = directory / 'reference.json'
        path.write_text(path.read_text() + '\n')
    elif damage == 'price_sha':
        target.write_bytes(b'changed parquet source')
    elif damage == 'manifest_denied':
        path = directory / 'manifest.json'
        body = json.loads(path.read_text())
        body['ai_processing_allowed'] = False
        path.write_text(json.dumps(body))
    else:
        body = json.loads(connection.execute('SELECT body_json FROM market_intraday_brief').fetchone()[0])
        if damage == 'wrong_date':
            body['anchor']['date'] = '2026-11-04'
        else:
            body['anchor']['ai_processing_allowed'] = False
        connection.execute('UPDATE market_intraday_brief SET body_json=?', (core.dumps(body),))
        connection.commit()
    result = tools.execute_read_tool('read_market_data', {'date': DAY, 'section': 'close'})
    assert not any(row['kind'] == 'closing_pools_brief' for row in result['evidence'])
    assert result['gaps'] and result['state'] == 'unknown'
