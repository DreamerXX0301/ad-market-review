"""Synthetic monitor records verify retained evidence semantics, without IO."""
from copy import deepcopy

import pytest

from app.services.ai_abnormal_read import EVENT_FIELDS, project_data


def page(count=1):
    return {'date': '2026-10-27', 'total': count, 'monitor_scope': 'historical',
            'requested_date': '2026-10-27', 'monitor_observed_date': '2026-10-27',
            'monitor_fetched_at': '2026-10-27T16:05:00+08:00', 'quote_as_of': '2026-10-27',
            'source': 'fuyao', 'items': [
                {'instrument_id': f'{600000+index}.SH', 'code': str(600000+index), 'name': f'fixture {index}',
                 'close': 10, 'change_pct': 1.2, 'amount': 3000,
                 'event_type': '供应商重点监控', 'start_date': '2026-10-20', 'end_date': '2026-11-03',
                 'observed_date': '2026-10-27', 'fetched_at': '2026-10-27T16:05:00+08:00',
                 'evidence_type': 'provider_monitor', 'official_document': None,
                 'official_regulation_end_date': None, 'source': 'fuyao', 'source_snapshot_id': 'fixture-source',
                 'quote_as_of': '2026-10-27', 'reason': '供应商监控标签', 'private_debug': 'never project'}
                for index in range(count)]}


def test_monitor_projection_retains_all_original_regulatory_evidence_without_inventing_official_expiry():
    data = page(); before = deepcopy(data)
    result = project_data(data)
    row = result['stocks']['items'][0]
    assert {key: row[key] for key in EVENT_FIELDS} == {key: data['items'][0][key] for key in EVENT_FIELDS}
    assert row['end_date'] == '2026-11-03' and row['official_regulation_end_date'] is None
    assert row['evidence_type'] == 'provider_monitor' and row['source_snapshot_id'] == 'fixture-source'
    assert result['monitor_scope'] == 'historical' and result['monitor_observed_date'] == '2026-10-27'
    assert '不等于' in result['scope'] and 'private_debug' not in row
    assert data == before


def test_monitor_filters_complete_loaded_list_before_bounding_name_or_code():
    data = page(100)
    assert len(project_data(data)['stocks']['items']) == 30
    result = project_data(data, '600099')
    assert result['stocks']['returned_count'] == result['stocks']['observed_count'] == 1
    assert result['stocks']['items'][0]['name'] == 'fixture 99'
    assert result['query_source_count'] == 100 and result['query_source_complete'] is True
    assert project_data(data, 'fixture 99')['stocks'] == result['stocks']
    assert not project_data(data, '供应商重点监控')['stocks']['items']


def test_monitor_source_limit_and_missing_days_remain_explicit_unknown_not_no_regulation():
    data = page(35); data['total'] = 50
    result = project_data(data)
    assert result['stocks']['truncated'] and not result['query_source_complete']
    assert result['stocks']['observed_count'] == 35
    result = project_data({'date': '2026-10-26', 'items': [], 'total': 0,
                           'unavailable_reason': '该日名单未留存，不能回填历史'})
    assert result['unavailable_reason'] == '该日名单未留存，不能回填历史'
    assert result['stocks']['items'] == []
    assert '不证明没有监管事件' in result['scope']


@pytest.mark.parametrize('maximum', [0, 31, True, '30'])
def test_monitor_projection_has_fixed_small_return_budget(maximum):
    with pytest.raises(ValueError):
        project_data(page(), maximum=maximum)


def test_ai_detail_dispatch_keeps_monitor_evidence_and_name_filter(monkeypatch, tmp_path):
    from app.services import ai_read_tools as tools, market_service
    from app.core import dumps
    from datetime import datetime
    from types import SimpleNamespace
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path/'data'))
    day = '2026-10-27'; heads = {'daily_raw': {'market_snapshot_id': 'fixture-market'}}
    market = SimpleNamespace()
    monkeypatch.setattr(tools, '_completed_market', lambda *_: (market, {}, heads, {}))
    monkeypatch.setattr(tools, '_heads', lambda: deepcopy(heads))
    monkeypatch.setattr(tools, '_supplement_head', lambda: {})
    monkeypatch.setattr(tools, 'local_now', lambda: datetime.fromisoformat(day+'T18:00:00+08:00'))
    calls = []
    def saved_page(kind, date, **kwargs):
        calls.append((kind, date, kwargs['query']))
        data = page(40)
        data['items'][-1]['topics'] = [{'topic_id': 'fixture-theme', 'name': 'fixture theme', 'kind': 'concept'}]
        return {**data, 'snapshot_id': 'fixture-market'}
    monkeypatch.setattr(market_service, 'market_page', saved_page)
    result = tools.execute_read_tool('read_market_detail', {'date': day, 'section': 'abnormal', 'query': '600039'})
    assert result['ok'] and result['state'] == 'available'
    assert calls == [('abnormal', day, '600039')]
    data = result['evidence'][0]['data']; row = data['stocks']['items'][0]
    assert row['code'] == '600039' and row['event_type'] == '供应商重点监控'
    assert row['end_date'] == '2026-11-03' and row['official_regulation_end_date'] is None
    assert row['observed_date'] == day and row['source_snapshot_id'] == 'fixture-source'
    assert row['evidence_type'] == 'provider_monitor'
    assert row['topics'][0]['topic_id'] == 'fixture-theme'
    assert len(dumps(result).encode()) <= tools.MAX_RESULT_BYTES
