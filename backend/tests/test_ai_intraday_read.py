"""End-to-end local tool projection and frozen plan validation."""
from datetime import datetime
from hashlib import sha256
import sqlite3

import pytest

from app.core import dumps
from app.services import ai_read_tools, ai_scope_semantic
from app.services.intraday_service import SLOTS, VERSION

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
        CREATE TABLE job(job_id TEXT PRIMARY KEY,status TEXT,finished_at_ms INTEGER);
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
    return {'date': DAY, 'slot': slot, 'id': 'intraday-'+slot, 'quality': 'partial', 'version': VERSION,
            'collected_start_at': DAY+'T'+clock+':00+08:00', 'collected_end_at': DAY+'T'+clock+':30+08:00',
            'summary': {'comparison_slot': 'noon', 'interval_amount': {
                'amount': 99800, 'previous_amount': 95100, 'comparison_amount': 96000,
                'change_from_previous': 900, 'previous_comparable_count': 20}},
            'market_pulse': {'breadth': {'up': 3200, 'down': 2100, 'flat': 5}, 'turnover': {'amount': 120000}},
            'ladder': {'sealed_count': 10, 'broken_board': {'state': 'observed', 'count': 0}},
            'strong_sectors': [sector], 'sectors': [sector], 'stocks': [stock],
            'comparison_input': {'stocks': [{'instrument_id': '000001.SZ', 'currency': 'CNY',
                'price_basis': 'none', 'latest_price': 11, 'prev_close': 10, 'change_pct': 10}]},
            'source_checks': []}


def _save(connection, snapshot, *, sha=None):
    raw = dumps(snapshot)
    identity = snapshot['id']
    finished = int(datetime.fromisoformat(snapshot['collected_end_at']).timestamp() * 1000) + 10
    connection.execute('INSERT INTO job VALUES (?,?,?)', (identity, 'succeeded', finished))
    connection.execute('INSERT INTO market_intraday_capture VALUES (?,?,?,?,?,?)', (
        snapshot['date'], snapshot['slot'], identity, identity, sha or sha256(raw.encode()).hexdigest(), raw))
    connection.commit()


def _call(slot='all', query='', day='today'):
    return ai_read_tools.execute_read_tool('read_intraday_data', {'date': day, 'slot': slot, 'query': query})


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
    assert found['stock_samples'][0]['instrument_id'] == '688012.SH'
    assert 'comparison_input' not in dumps(result) and source.execute('PRAGMA data_version').fetchone()[0] == before
    assert len(dumps(result).encode()) <= ai_read_tools.MAX_RESULT_BYTES


def test_single_slot_filters_direction_or_exact_code_and_never_substitutes(source):
    _save(source, _snapshot())
    result = _call('1330', '半导体')
    assert result['evidence'][0]['data']['direction_discovery']['directions'][0]['name'] == '半导体设备'
    assert _call('1330', '000001')['evidence'][0]['data']['stock_samples'][0]['latest_price'] == 11
    assert _call('1000')['evidence'] == []
    assert _call('1330', day='2026-11-03')['evidence'] == []


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
