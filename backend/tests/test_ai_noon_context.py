"""Noon AI reads freeze the actual same-day morning prefix, not a later page state."""
from datetime import datetime, timedelta
import json

import pytest

from app.core import digest, dumps
from app.services import ai_read_tools as tools, source_service
from test_ai_market_brief import database, DAY, auction_payload, noon_payload, save, save_close
from test_ai_intraday_read import _snapshot, _save


@pytest.fixture
def noon_source(database, monkeypatch):
    monkeypatch.delenv('SHIXU_AI_READ_SQLITE_PATH', raising=False)
    monkeypatch.setattr(tools, 'local_now', lambda: datetime.fromisoformat(DAY + 'T16:30:00+08:00'))
    monkeypatch.setattr(tools, '_market', lambda *args: pytest.fail('noon context never consults current head'))
    monkeypatch.setattr(source_service, 'client', lambda *args: pytest.fail('read-only context never collects'))
    save(database, 'market_auction_snapshot', 'auction', auction_payload())
    save(database, 'market_noon_snapshot', 'noon', noon_payload())
    save_close(database)
    database.executescript('''CREATE TABLE job(job_id TEXT PRIMARY KEY,status TEXT,finished_at_ms INTEGER,parameters_json TEXT);
        CREATE TABLE market_intraday_capture(trade_date,slot,job_id,snapshot_id,content_sha256,payload_json);''')
    return database


def stage(database, slot, *, late=False):
    value = _snapshot(slot)
    value['date'] = DAY
    for key in ('collected_start_at', 'collected_end_at'):
        value[key] = DAY + value[key][10:]
    _save(database, value)
    if late:
        database.execute('UPDATE job SET finished_at_ms=? WHERE job_id=?',
                         (int(datetime.fromisoformat(DAY + 'T11:37:00+08:00').timestamp() * 1000), value['id']))
        database.commit()
    return value


def call():
    return tools.execute_read_tool('read_market_data', {'date': DAY, 'section': 'noon'})


def test_noon_chains_auction_and_completed_morning_only_without_future_stages(noon_source):
    for slot in ('0935', '0945', '1000', '1030', '1100', '1330', '1430'):
        stage(noon_source, slot, late=slot == '1030')
    original = noon_source.execute('SELECT payload_json FROM market_intraday_capture ORDER BY slot').fetchall()
    result = call()
    assert result['ok']
    kinds = [row['kind'] for row in result['evidence']]
    assert kinds == ['auction_brief', 'intraday:0935', 'intraday:0945', 'intraday:1000', 'intraday:1100', 'noon_brief']
    context = result['summary']['noon_context']
    assert context['cutoff_at'] == DAY + 'T11:36:00+08:00'
    assert context['phases']['1030']['phase_state'] == 'not_collected'
    assert '截至午评' in context['phases']['1030']['reason']
    assert list(context['phases']) == ['auction', '0935', '0945', '1000', '1030', '1100', 'noon']
    assert set(result['source_bindings']['intraday']['current_instance']) == {'auction', 'noon'}
    assert set(result['source_bindings']['noon_morning']) == {'0935', '0945', '1000', '1100'}
    assert '1330' not in dumps(result) and '1430' not in dumps(result) and 'noon_close' not in dumps(result)
    assert len(dumps(result).encode()) <= tools.MAX_RESULT_BYTES
    assert noon_source.execute('SELECT payload_json FROM market_intraday_capture ORDER BY slot').fetchall() == original


def test_each_tool_turn_revalidates_same_noon_cutoff_and_new_source_sha(noon_source):
    value = stage(noon_source, '1100')
    first = call()
    first_binding = first['source_bindings']['noon_morning']['1100']['content_sha256']
    value['market_pulse']['breadth']['up'] = 987
    noon_source.execute('UPDATE market_intraday_capture SET payload_json=?,content_sha256=? WHERE slot=?',
                        (dumps(value), digest(value), '1100'))
    noon_source.commit()
    second = call()
    assert second['summary']['noon_context']['cutoff_at'] == first['summary']['noon_context']['cutoff_at']
    assert second['source_bindings']['noon_morning']['1100']['content_sha256'] != first_binding
    morning = next(row for row in second['evidence'] if row['kind'] == 'intraday:1100')
    assert morning['data']['breadth']['up'] == 987
    assert next(row for row in first['evidence'] if row['kind'] == 'intraday:1100')['data']['breadth']['up'] != 987


def test_current_noon_keeps_all_missing_morning_stages_explicit(noon_source):
    result = call()
    assert [row['kind'] for row in result['evidence']] == ['auction_brief', 'noon_brief']
    context = result['summary']['noon_context']
    assert all(context['phases'][slot]['phase_state'] in ('not_collected', 'unknown')
               for slot in ('0935', '0945', '1000', '1030', '1100'))
    assert result['source_bindings']['noon_morning'] == {}


def test_historical_noon_omits_a_stage_that_was_not_scheduled(noon_source, monkeypatch):
    monkeypatch.setattr(tools, 'local_now',
                        lambda: datetime.fromisoformat(DAY + 'T16:30:00+08:00') + timedelta(days=1))
    result = call()
    assert [row['kind'] for row in result['evidence']] == ['auction_brief', 'noon_brief']
    assert list(result['summary']['noon_context']['phases']) == ['auction', '0935', '1000', '1030', '1100', 'noon']
    assert not any(row['capability'] == 'intraday:0945' for row in result['gaps'])


def test_future_or_other_date_noon_does_not_borrow_current_or_close(noon_source, monkeypatch):
    monkeypatch.setattr(tools, 'local_now', lambda: datetime.fromisoformat(DAY + 'T11:03:00+08:00'))
    result = call()
    assert result['evidence'] == [] and result['metrics'] == []
    assert result['summary']['noon_context']['state'] == 'unknown'
    assert result['source_bindings']['intraday'] == {}
    result = tools.execute_read_tool('read_market_data', {'date': '2026-11-02', 'section': 'noon'})
    assert result['evidence'] == [] and result['date'] == '2026-11-02'


def test_denied_noon_cannot_open_its_other_morning_materials(noon_source):
    value = noon_payload()
    value['ai_processing_allowed'] = False
    noon_source.execute('UPDATE market_noon_snapshot SET payload_json=?,content_sha256=?', (dumps(value), digest(value)))
    noon_source.commit()
    stage(noon_source, '0935')
    result = call()
    assert result['evidence'] == [] and result['gaps']


def test_dense_morning_prefix_keeps_every_stage_within_total_budget(noon_source):
    for slot in ('0935', '0945', '1000', '1030', '1100'):
        value = stage(noon_source, slot)
        value['sectors'] = value['strong_sectors'] = [
            {**value['sectors'][0], 'instrument_id': f'{881000 + index}.TI',
             'name': '合成多方向与午评累计容量验证行业' + str(index)} for index in range(30)]
        noon_source.execute('UPDATE market_intraday_capture SET payload_json=?,content_sha256=? WHERE slot=?',
                            (dumps(value), digest(value), slot))
    noon_source.commit()
    result = call()
    assert [row['kind'] for row in result['evidence']] == ['auction_brief', 'intraday:0935', 'intraday:0945',
        'intraday:1000', 'intraday:1030', 'intraday:1100', 'noon_brief']
    assert len(dumps(result).encode()) <= tools.MAX_RESULT_BYTES
