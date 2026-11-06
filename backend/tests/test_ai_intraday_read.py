"""End-to-end local tool projection and frozen plan validation."""
from datetime import datetime
from hashlib import sha256
import sqlite3

import pytest

from app.core import dumps
from app.services import ai_read_tools, ai_scope_semantic
from app.services.intraday_service import SEVEN_SLOT_VERSION, SLOTS, VERSION

DAY = '2026-11-04'


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('SHIXU_AI_READ_SQLITE_PATH', raising=False)
    directory = tmp_path / 'business'
    directory.mkdir()
    path = directory / 'app.sqlite3'
    connection = sqlite3.connect(path)
    connection.executescript('''
        CREATE TABLE job(job_id TEXT PRIMARY KEY,status TEXT,finished_at_ms INTEGER,parameters_json TEXT);
        CREATE TABLE market_intraday_capture(trade_date TEXT,slot TEXT,job_id TEXT,
            snapshot_id TEXT,content_sha256 TEXT,payload_json TEXT,PRIMARY KEY(trade_date,slot));
    ''')
    monkeypatch.setattr(ai_read_tools, 'local_now', lambda: datetime.fromisoformat(DAY+'T13:40:00+08:00'))
    yield connection
    connection.close()


def _snapshot(slot='1330'):
    clock = next(item['start'] for item in SLOTS if item['key'] == slot)
    stock = {'instrument_id': '688012.SH', 'name': '中微公司', 'change_pct': 3.5, 'amount': 12340000}
    sector = {'instrument_id': '881121.TI', 'name': '半导体设备', 'kind': 'industry',
              'change_pct': 2, 'change_pp': 3, 'candidates': [stock], 'up_ratio': 80,
              'limit_up_count': 2, 'limit_up_count_state': 'partial'}
    comparison = {'comparison_amount': 120000, 'previous_amount': 125000,
                  'change_from_previous': -5000, 'comparable_count': 1}
    return {'date': DAY, 'slot': slot, 'id': 'intraday-'+slot, 'quality': 'partial', 'version': VERSION,
            'collected_start_at': DAY+'T'+clock+':00+08:00', 'collected_end_at': DAY+'T'+clock+':30+08:00',
            'summary': {'comparison_slot': 'noon', 'interval_amount': {
                'amount': 99800, 'previous_amount': 95100, 'comparison_amount': 96000,
                'change_from_previous': 900, 'previous_comparable_count': 20}},
            'market_pulse': {'breadth': {'up': 3200, 'down': 2100, 'flat': 5},
                             'turnover': {'amount': 120000, 'amount_comparison': comparison},
                             'indices': [{'instrument_id': '000001.SH', 'name': '上证指数', 'amount': 120000,
                                          'amount_comparison': comparison, 'interval_amount': {'amount': 24000}}]},
            'ladder': {'sealed_count': 10, 'broken_board': {'state': 'observed', 'count': 0}},
            'strong_sectors': [sector], 'sectors': [sector], 'stocks': [stock],
            'comparison_input': {'stocks': [{'instrument_id': '000001.SZ', 'currency': 'CNY',
                'price_basis': 'none', 'latest_price': 11, 'prev_close': 10, 'change_pct': 10}]},
            'source_checks': []}


def _save(connection, snapshot, *, sha=None):
    raw = dumps(snapshot)
    identity = snapshot['id']
    finished = int(datetime.fromisoformat(snapshot['collected_end_at']).timestamp() * 1000) + 10
    connection.execute('INSERT INTO job VALUES (?,?,?,?)',
                       (identity, 'succeeded', finished, dumps({'version': snapshot.get('version')})))
    connection.execute('INSERT INTO market_intraday_capture VALUES (?,?,?,?,?,?)', (
        snapshot['date'], snapshot['slot'], identity, identity, sha or sha256(raw.encode()).hexdigest(), raw))
    connection.commit()


def _call(slot='all', query='', day='today'):
    return ai_read_tools.execute_read_tool('read_intraday_data', {'date': day, 'slot': slot, 'query': query})


@pytest.mark.parametrize('dense', [False, True])
def test_eight_slots_keep_early_observations_independent_and_fit_existing_tool_budget(source, monkeypatch, dense):
    from app.services import ai_intraday_read
    traces = []
    original_connect = ai_intraday_read._connect
    def traced_connect(path):
        connection = original_connect(path)
        if connection:
            connection.set_trace_callback(traces.append)
        return connection
    monkeypatch.setattr(ai_intraday_read, '_connect', traced_connect)
    monkeypatch.setattr(ai_read_tools, 'local_now', lambda: datetime.fromisoformat(DAY + 'T15:00:00+08:00'))
    for slot in SLOTS:
        value = _snapshot(slot['key'])
        if dense:
            value['sectors'] = value['strong_sectors'] = [
                {**value['sectors'][0], 'instrument_id': f'{881000 + index}.TI',
                 'name': '合成多方向有界摘要容量验证行业' + str(index)} for index in range(30)]
            value['stocks'] = [{**value['stocks'][0], 'instrument_id': f'{600000 + index}.SH',
                                'name': '合成容量验证股票' + str(index)} for index in range(30)]
        _save(source, value)
    before = source.execute('SELECT payload_json FROM market_intraday_capture ORDER BY slot').fetchall()
    result = _call('all')
    assert result['ok'] and len(result['evidence']) == 8
    assert list(result['summary']['phases']) == [slot['key'] for slot in SLOTS]
    assert set(result['source_bindings']) == {slot['key'] for slot in SLOTS}
    assert all(len(dumps(item['data']).encode()) <= 3 * 1024 for item in result['evidence'])
    assert len(dumps(result).encode()) <= ai_read_tools.MAX_RESULT_BYTES
    assert sum('SELECT trade_date,slot,snapshot_id,content_sha256,payload_json' in sql for sql in traces) == 8
    for key, clock in (('0935', '09:35'), ('0945', '09:45')):
        opening = _call(key)
        assert len(opening['evidence']) == 1 and opening['evidence'][0]['kind'] == 'intraday:' + key
        assert opening['evidence'][0]['dates']['collected_end_at'].startswith(DAY + 'T' + clock + ':')
    assert source.execute('SELECT payload_json FROM market_intraday_capture ORDER BY slot').fetchall() == before


def test_0935_without_real_snapshot_is_missing_not_auction_or_1000(source):
    _save(source, _snapshot('1000'))
    result = _call('0935')
    assert result['evidence'] == [] and result['source_bindings'] == {}
    assert result['summary']['phases']['0935']['phase_state'] == 'not_collected'


def test_0945_without_saved_snapshot_separates_pending_and_missing(source, monkeypatch):
    _save(source, _snapshot('0935'))
    monkeypatch.setattr(ai_read_tools, 'local_now', lambda: datetime.fromisoformat(DAY + 'T09:44:59+08:00'))
    result = _call('0945')
    assert result['state'] == 'not_due' and result['evidence'] == []
    monkeypatch.setattr(ai_read_tools, 'local_now', lambda: datetime.fromisoformat(DAY + 'T10:05:00+08:00'))
    _save(source, _snapshot('1000'))
    result = _call('0945')
    assert result['state'] == 'not_collected' and result['evidence'] == []


@pytest.mark.parametrize('query_day', [DAY, '2026-11-05'])
@pytest.mark.parametrize('version, scheduled', [(SEVEN_SLOT_VERSION, False), (VERSION, True)])
def test_legacy_plan_only_marks_0945_missing_if_it_was_scheduled(source, monkeypatch, version, scheduled, query_day):
    for slot in ('0935', '1000'):
        _save(source, {**_snapshot(slot), 'version': version})
    monkeypatch.setattr(ai_read_tools, 'local_now', lambda: datetime.fromisoformat(query_day + 'T13:40:00+08:00'))
    result = _call(day=DAY)
    assert result['ok'] and ('0945' in result['summary']['phases']) is scheduled
    assert any(row['capability'] == 'intraday:0945' for row in result['gaps']) is scheduled
    exact = _call('0945', day=DAY)
    assert exact['evidence'] == []
    assert (exact['summary']['phases']['0945']['reason'] == '所选日未设置此观察档') is not scheduled


@pytest.mark.parametrize('version,with_tracking,baseline,previous_slot,count', [
    (VERSION, True, '0945', '0945', 1),
    (VERSION, False, '0945', None, None),
    (SEVEN_SLOT_VERSION, True, '0935', '0935', 2),
])
def test_1000_board_change_reads_the_baseline_of_its_original_capture(source, version, with_tracking,
                                                                     baseline, previous_slot, count):
    from test_intraday_board_changes import A, B, C, frame

    def save_pool(key, stage_version, codes):
        value = _snapshot(key)
        captured = frame('1000', codes)
        value['version'] = stage_version
        value['comparison_input'] = captured['comparison_input']
        value['board_pool_evidence'] = captured['board_pool_evidence']
        value['source_checks'] = [{**receipt, 'source_time': value['collected_start_at']}
                                  for receipt in captured['source_checks']]
        _save(source, value)

    save_pool('0935', SEVEN_SLOT_VERSION, [A])
    if with_tracking:
        save_pool('0945', VERSION, [A, B])
    save_pool('1000', version, [A, B, C])
    result = _call('1000')
    changes = result['evidence'][0]['data']['board_changes']
    assert changes['baseline_slot'] == baseline
    assert changes['groups']['new_sealed']['count'] == count
    assert changes['source_bindings'].get('previous', {}).get('slot') == previous_slot


def test_adjacent_board_changes_are_read_from_same_database_and_keep_sha_and_bounded_summary(source, monkeypatch):
    from test_intraday_board_changes import frame, A, B, C, D

    monkeypatch.setattr(ai_read_tools, 'local_now', lambda: datetime.fromisoformat(DAY+'T14:40:00+08:00'))
    prior, current = frame('1400', [A, B], [C]), frame('1430', [B, C], [A], [D])
    for value in (prior, current):
        value['date'] = DAY
        for key in ('collected_start_at', 'collected_end_at'):
            value[key] = DAY + value[key][10:]
        for check in value['source_checks']:
            check['source_time'] = DAY + check['source_time'][10:]
        _save(source, value)
    raw_before = source.execute('SELECT payload_json FROM market_intraday_capture ORDER BY slot').fetchall()
    result = _call('1430', C.split('.')[0])
    data = result['evidence'][0]['data']['board_changes']
    assert data['groups']['resealed']['count'] == 1 and data['groups']['new_limit_down']['count'] == 1
    assert data['groups']['resealed']['samples'][0]['instrument_id'] == C
    assert result['source_bindings']['1430']['board_change_previous']['content_sha256'] == sha256(dumps(prior).encode()).hexdigest()
    assert data['source_bindings']['current']['snapshot_id'] == current['id']
    all_slots = _call('all')
    assert len(all_slots['evidence']) == 2
    assert all(len(dumps(item['data']).encode()) <= 3 * 1024 for item in all_slots['evidence'])
    assert source.execute('SELECT payload_json FROM market_intraday_capture ORDER BY slot').fetchall() == raw_before


def test_all_intraday_reads_only_available_times_and_direction_sample(source):
    _save(source, _snapshot('1000'))
    _save(source, _snapshot('1330'))
    before = source.execute('PRAGMA data_version').fetchone()[0]
    result = _call()
    assert result['date'] == DAY and result['state'] == 'partial'
    assert set(result['source_bindings']) == {'1000', '1330'}
    assert result['summary']['phases']['1400']['phase_state'] == 'not_due'
    assert result['summary']['phases']['1030']['phase_state'] == 'not_collected'
    found = result['evidence'][1]['data']
    assert found['direction_discovery']['directions'][0]['signal'] == 'new'
    assert found['ladder']['broken_board']['count'] == 0
    assert found['interval_amount']['change_from_previous'] == 900
    assert found['turnover']['amount_comparison']['change_from_previous'] == -5000
    assert found['indices'][0]['amount'] == 120000
    assert 'amount_comparison' not in found['indices'][0]
    assert found['stock_samples'][0]['instrument_id'] == '688012.SH'
    assert 'comparison_input' not in dumps(result) and source.execute('PRAGMA data_version').fetchone()[0] == before
    assert len(dumps(result).encode()) <= ai_read_tools.MAX_RESULT_BYTES


def test_single_slot_filters_direction_or_exact_code_and_never_substitutes(source):
    _save(source, _snapshot())
    result = _call('1330', '半导体')
    assert result['evidence'][0]['data']['direction_discovery']['directions'][0]['name'] == '半导体设备'
    index = result['evidence'][0]['data']['indices'][0]
    assert index['amount_comparison']['change_from_previous'] == -5000
    assert index['interval_amount']['amount'] == 24000
    assert _call('1330', '000001')['evidence'][0]['data']['stock_samples'][0]['latest_price'] == 11
    assert _call('1000')['evidence'] == []
    assert _call('1330', day='2026-11-03')['evidence'] == []


def test_explicit_topic_query_reads_retained_full_quotes_outside_strength_top_twenty(source):
    snapshot = _snapshot()
    snapshot['comparison_input']['sectors'] = [
        {'instrument_id': code, 'name': name, 'kind': 'concept', 'latest_price': 102,
         'prev_close': 100, 'change_pct': 2, 'currency': 'CNY', 'price_basis': 'none'}
        for code, name in [('886021.TI', '国企改革'), ('886108.TI', 'AI应用'), ('886100.TI', 'DeepSeek概念')]]
    _save(source, snapshot)
    for query in ('国企改革', 'AI应用', 'DeepSeek'):
        result = _call('1330', query)
        rows = result['evidence'][0]['data']['sector_samples']
        assert len(rows) == 1 and query.casefold() in rows[0]['name'].casefold()
        assert rows[0]['change_pct'] == 2
    assert _call()['evidence'][0]['data']['sector_samples'][0]['name'] == '半导体设备'


def test_all_six_keeps_each_timestamp_under_global_result_budget(source, monkeypatch):
    monkeypatch.setattr(ai_read_tools, 'local_now', lambda: datetime.fromisoformat(DAY+'T14:40:00+08:00'))
    for item in SLOTS:
        snapshot = _snapshot(item['key'])
        base = snapshot['strong_sectors'][0]
        snapshot['strong_sectors'] = snapshot['sectors'] = [
            {**base, 'name': '半导体设备应用细分方向'*20 + str(index),
             'instrument_id': f'{881121+index}.TI'} for index in range(12)]
        _save(source, snapshot)
    result = _call()
    assert result['state'] == 'partial' and len(result['evidence']) == len(SLOTS)
    assert len(dumps(result).encode()) <= ai_read_tools.MAX_RESULT_BYTES


@pytest.mark.parametrize('corruption', ['sha', 'time', 'egress'])
def test_slot_validation_never_sends_bad_source_material(source, corruption):
    snapshot = _snapshot()
    if corruption == 'time':
        snapshot['collected_start_at'] = DAY + 'T11:00:00+08:00'
    if corruption == 'egress':
        snapshot['source_checks'] = [{'egress_allowed': False}]
    _save(source, snapshot, sha='bad' if corruption == 'sha' else None)
    result = _call('1330')
    assert result['state'] == 'unknown' and not result['evidence']


def test_natural_language_tools_are_allowed_only_when_frozen():
    for text, name, args in (
        ('研究盘中情况', 'read_intraday_data', {'date': 'today', 'slot': 'all', 'query': ''}),
        ('看看今天09:45早盘跟踪', 'read_intraday_data', {'date': 'today', 'slot': '0945', 'query': ''}),
        ('看一下竞价情况', 'read_market_data', {'date': 'today', 'section': 'auction'}),
    ):
        request = {'tool_schemas': ai_read_tools.tool_schemas(),
                   'round_protocol': {'version': ai_scope_semantic.ROUND_VERSION},
                   'scope_input': {'current_text': text, 'recent_user_texts': [], 'validated_stock_focus': {}}}
        plan = {'purpose': 'market_review', 'current_user_quote': text, 'context_user_quote': '',
                'goal': '分析当前时点', 'reads': [{'name': name, 'arguments': args}], 'web_query': '', 'web_reason': ''}
        assert ai_scope_semantic.validate_plan(request, plan)['ok']
        request['tool_schemas'] = [tool for tool in request['tool_schemas'] if tool['name'] != name]
        with pytest.raises(ValueError, match='invalid_tool_arguments'):
            ai_scope_semantic.classify_frozen_read(request, name, args)
