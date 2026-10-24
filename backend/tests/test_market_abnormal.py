"""监管观察日、报价日与历史缺口必须分别保留。"""
from fastapi.testclient import TestClient
import json
import pytest

from test_market_history import archives, install_panel, reference, A, B
from app.services import market_service
from app import core
from app.analytics.market_storage import write_reference


def test_weekend_current_list_is_readable_without_becoming_friday_history(archives, monkeypatch):
    _, save = archives
    current = {**reference('2026-10-20', B), 'abnormal_observed_date': '2026-10-21',
               'abnormal_fetched_at': '2026-10-21T09:30:00+08:00'}
    save('market-current', '2026-10-20', current)
    _, _, calls = install_panel(monkeypatch, current)
    historical = market_service.market_page('abnormal', '2026-10-20')
    latest = market_service.market_page('abnormal', '2026-10-19', monitor_scope='latest')
    assert calls == ['2026-10-20', '2026-10-20']
    assert historical['monitor_scope'] == 'historical' and historical['items'] == []
    assert not historical['data_coverage']['selected_date_available']
    assert latest['requested_date'] == '2026-10-19'
    assert latest['monitor_observed_date'] == '2026-10-21'
    assert latest['monitor_fetched_at'] == '2026-10-21T09:30:00+08:00'
    assert latest['quote_as_of'] == '2026-10-20'
    assert latest['data_coverage']['available_dates'] == ['2026-10-21']
    row = latest['items'][0]
    assert row['instrument_id'] == B and row['observed_date'] == '2026-10-21'
    assert row['quote_as_of'] == '2026-10-20' and row['official_regulation_end_date'] is None
    assert row['official_document'] is None and row['evidence_type'] == 'provider_monitor'


def test_latest_uses_source_time_not_later_topic_publication(archives, monkeypatch):
    _, save = archives
    for generation, day, observed, fetched, symbol in [
        ('market-first', '2026-10-19', '2026-10-20', '2026-10-20T15:00:00+08:00', A),
        ('market-fresh', '2026-10-20', '2026-10-20', '2026-10-20T18:00:00+08:00', B),
        ('market-copied', '2026-10-20', '2026-10-20', '2026-10-20T15:00:00+08:00', A),
    ]:
        save(generation, day, {**reference(day, symbol), 'abnormal_observed_date': observed,
                               'abnormal_fetched_at': fetched})
    install_panel(monkeypatch, reference('2026-10-20', A), snapshot='market-copied')
    result = market_service.market_page('abnormal', '2026-10-19', monitor_scope='latest')
    assert result['items'][0]['instrument_id'] == B
    assert result['data_coverage']['source_snapshot_id'] == 'market-fresh'


def test_latest_corrupt_archive_falls_back_only_to_verified_older_observation(archives, monkeypatch):
    _, save = archives
    save('market-old', '2026-10-19', reference('2026-10-19', A))
    current = reference('2026-10-20', B)
    path, _ = save('market-current', '2026-10-20', current)
    install_panel(monkeypatch, current)
    assert market_service.market_page('abnormal', monitor_scope='latest')['items'][0]['instrument_id'] == B
    path.write_text(path.read_text().replace(B, A))
    result = market_service.market_page('abnormal', monitor_scope='latest')
    assert result['items'][0]['instrument_id'] == A
    assert result['monitor_observed_date'] == '2026-10-19'
    assert '校验失败' in result['data_coverage']['notice']


@pytest.mark.parametrize('value', [None, '', '2026-02-30'])
def test_latest_never_dates_unknown_monitor_list_from_market_day(archives, monkeypatch, value):
    _, save = archives
    current = {**reference('2026-10-20'), 'abnormal_observed_date': value}
    save('market-current', '2026-10-20', current)
    install_panel(monkeypatch, current)
    result = market_service.market_page('abnormal', monitor_scope='latest')
    assert result['items'] == [] and result['monitor_observed_date'] is None
    assert result['unavailable_reason'] and not result['data_coverage']['selected_date_available']


def test_latest_does_not_trust_uncatalogued_current_reference(archives, monkeypatch):
    install_panel(monkeypatch, reference('2026-10-20'))
    result = market_service.market_page('abnormal', monitor_scope='latest')
    assert result['items'] == [] and result['unavailable_reason']


@pytest.mark.parametrize('field',['abnormal','abnormal_observed_date','abnormal_fetched_at'])
@pytest.mark.parametrize('damage',['missing','changed'])
def test_latest_rechecks_monitor_cas_objects_after_warming_reference_cache(archives, monkeypatch, field, damage):
    root, _ = archives
    current = {**reference('2026-10-20', B), 'abnormal_fetched_at':'2026-10-20T18:00:00+08:00'}
    path = root/'published'/'market-current'/'reference.json'
    path.parent.mkdir(parents=True)
    write_reference(current, root/'published', path.parent, 'market-current')
    core.publish_snapshot(module='topics',job_id='history-test-job',start='2026-10-20',end='2026-10-20',
                          artifacts=[{'object_key':'market-current/reference.json','sha256':core.hash_file(path),
                                      'byte_count':path.stat().st_size,'role':'reference.json','row_count':1}],
                          lineage={'market_snapshot_id':'market-current'},snapshot_id='topics-market-current',activate=False)
    install_panel(monkeypatch, current)
    assert market_service.market_page('abnormal', monitor_scope='latest')['items'][0]['instrument_id'] == B
    descriptor = json.loads(path.read_text())
    target = root/'published'/descriptor['fields'][field]['object_key']
    if damage == 'missing':
        target.unlink()
    else:
        content=target.read_bytes();target.write_bytes(bytes([content[0]^1])+content[1:])
    result = market_service.market_page('abnormal', monitor_scope='latest')
    assert result['items'] == [] and result['unavailable_reason']
    assert result['monitor_observed_date'] is None
    assert result['data_coverage']['available_dates'] == []
    assert not result['data_coverage']['selected_date_available']
    assert '校验失败' in result['data_coverage']['notice']


@pytest.mark.parametrize('dependency',['master','index'])
def test_latest_cold_read_does_not_expand_unrelated_master_or_index_objects(archives, monkeypatch, dependency):
    root, _ = archives
    current = {**reference('2026-10-20', B), 'abnormal_fetched_at':'2026-10-20T18:00:00+08:00',
               'master':{B:{'name':'合成独立证券资料'}},
               'indices':{'000001.SH':[{'date':'2026-10-20','close':3010}]}}
    path = root/'published'/'market-current'/'reference.json';path.parent.mkdir(parents=True)
    write_reference(current, root/'published', path.parent, 'market-current')
    core.publish_snapshot(module='topics',job_id='history-test-job',start='2026-10-20',end='2026-10-20',
                          artifacts=[{'object_key':'market-current/reference.json','sha256':core.hash_file(path),
                                      'byte_count':path.stat().st_size,'role':'reference.json','row_count':1}],
                          lineage={'market_snapshot_id':'market-current'},snapshot_id='topics-market-current',activate=False)
    document=json.loads(path.read_text())
    descriptor=(document['master_columns']['columns']['name']['reference'] if dependency=='master'
                else document['indices']['000001.SH'][0])
    (root/'published'/descriptor['object_key']).unlink()
    install_panel(monkeypatch, current)
    result=market_service.market_page('abnormal',monitor_scope='latest')
    assert result['items'][0]['instrument_id']==B
    assert result['data_coverage']['selected_date_available']
    assert result['monitor_observed_date']=='2026-10-20'


def test_latest_empty_verified_list_is_known_empty_not_unknown(archives, monkeypatch):
    _, save = archives
    current = {**reference('2026-10-20'), 'abnormal': []}
    save('market-current', '2026-10-20', current)
    install_panel(monkeypatch, current)
    result = market_service.market_page('abnormal', monitor_scope='latest')
    assert result['items'] == [] and result['data_coverage']['selected_date_available']
    assert 'unavailable_reason' not in result


def test_http_defaults_to_history_and_validates_scope_before_reading_data(archives, monkeypatch):
    from app.main import app
    calls = []
    def read(kind, day, limit, **kwargs):
        calls.append((kind, day, kwargs['monitor_scope']))
        return {'monitor_scope': kwargs['monitor_scope']}
    monkeypatch.setattr(market_service, 'market_page', read)
    with TestClient(app) as client:
        assert client.get('/api/v1/market/abnormal?date=2026-10-23').json()['monitor_scope'] == 'historical'
        assert client.get('/api/v1/market/abnormal?monitor_scope=latest').json()['monitor_scope'] == 'latest'
        assert client.get('/api/v1/market/abnormal?monitor_scope=current').status_code == 422
        assert client.get('/api/v1/market/gainers?monitor_scope=latest').status_code == 422
    assert calls == [('abnormal', '2026-10-23', 'historical'), ('abnormal', None, 'latest')]
