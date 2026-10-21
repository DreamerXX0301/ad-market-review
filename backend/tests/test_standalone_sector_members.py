"""Current sector constituents can be read without creating tracking state."""
from copy import deepcopy

from fastapi import HTTPException
import pytest

from app import core
from app.services import sector_tracking as tracking, topics, topic_members, market_service
from test_workspace import ws
from test_sector_tracking import local, add, CODE


@pytest.fixture
def catalog(local, monkeypatch):
    reference = {'concept_catalog': [{'thscode': CODE, 'name': '人工智能'}], 'industry_catalog': []}
    monkeypatch.setattr(local['market'], 'reference', lambda: deepcopy(reference), raising=False)
    return local


def _counts():
    with core.connect() as connection:
        return tuple(connection.execute('SELECT count(*) FROM '+table).fetchone()[0]
                     for table in ('sector_strategy', 'sector_selection_run', 'sector_tracking_round', 'tracking_round', 'job'))


def test_standalone_latest_reads_real_dates_four_stats_without_a_round(catalog, monkeypatch):
    topics.seed_topics(); calls = []
    def statistics(market, day, symbols, *rest):
        calls.append((day, set(symbols)))
        return {'600001.SH': {'return_ytd_pct': 20, 'change_30d_pct': 8, 'return_mtd_pct': 4,
            'limit_up_ytd': None, 'limit_up_ytd_observed': 3,
            'last_limit_up_date': day, 'last_limit_up_complete': True, 'sessions_since_limit_up': 0}}
    monkeypatch.setattr(topic_members, 'member_metrics', statistics)
    before = _counts()
    result = tracking.sector_members(CODE)
    assert _counts() == before and before == (0, 0, 0, 0, 1)
    assert 'round_id' not in result and result['total'] == 1
    assert result['membership_as_of'] == catalog['market'].days[71]
    assert result['quote_as_of'] == catalog['state']['day']
    assert result['stock_entry_baseline'] is None
    row = result['items'][0]
    assert row['board'] == 'main' and row['board_label'] == '主板'
    assert row['return_ytd_pct'] == 20 and row['return_30d_pct'] == 8
    assert row['limit_up_ytd'] is None and row['annual_limit_up_count_observed'] == 3
    assert row['last_limit_up_date'] == catalog['state']['day'] and row['sessions_since_limit_up'] == 0
    assert calls == [(catalog['state']['day'], {'600001.SH'})]


def test_current_provider_overrides_apply_to_standalone_and_round_identically(catalog):
    topics.seed_topics()
    _, _, rid = add(catalog)
    selected = topics.list_topics()['items'][0]
    topics.add_member(selected['topic_id'], {'instrument_id': '600001.SH', 'action': 'exclude'})
    topics.add_member(selected['topic_id'], {'instrument_id': '000001.SZ', 'action': 'include'})
    direct = tracking.sector_members(CODE)
    round_result = tracking.members(rid)
    assert round_result['round_id'] == rid and 'round_id' not in direct
    assert direct['items'] == round_result['items']
    assert [row['instrument_id'] for row in direct['items']] == ['000001.SZ']
    assert direct['items'][0]['manual_override'] == 'include'
    assert direct['items'][0]['provider_member'] is False


def test_known_catalog_without_topic_import_is_unknown_and_does_not_seed(catalog):
    before = _counts()
    result = tracking.sector_members(CODE)
    assert result['items'] == [] and result['members_complete'] is False
    assert result['unavailable_reason'] and result['membership_scope'] == 'latest'
    assert result['topic']['topic_code'] == CODE
    assert _counts() == before
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM topic').fetchone()[0] == 0


@pytest.mark.parametrize('code', ['不存在', '999999.TI', 'BK0574'])
def test_standalone_rejects_unknown_catalog_identity_without_name_guessing(catalog, code):
    with pytest.raises(HTTPException) as caught: tracking.sector_members(code)
    assert caught.value.status_code == 404


def test_latest_requires_business_quote_date_and_historical_keeps_missing_archive(catalog, monkeypatch):
    day = catalog['market'].days[69]
    with pytest.raises(HTTPException) as caught: tracking.sector_members(CODE, day)
    assert caught.value.status_code == 422
    called = []
    def historical(observed, **kwargs):
        called.append((observed, kwargs))
        return {'items': [], 'total': 0, 'members_complete': False, 'membership_observed_date': None,
                'unavailable_reason': '该日没有成员档案，不能用新日期回填'}
    monkeypatch.setattr(market_service, 'market_topic_members', historical)
    result = tracking.sector_members(CODE, day, 'historical')
    assert result['items'] == [] and result['members_complete'] is False
    assert result['quote_as_of'] == day and result['membership_as_of'] is None
    assert called == [(day, {'source_code': CODE, 'kind': 'concept'})]


@pytest.mark.parametrize('day', ['2020-01-01', '2026-02-30', '9999-12-31'])
def test_outside_published_historical_dates_rejected_before_member_service(catalog, monkeypatch, day):
    monkeypatch.setattr(market_service, 'market_topic_members', lambda *a, **k: pytest.fail('invalid date must not read members'))
    with pytest.raises(HTTPException) as caught: tracking.sector_members(CODE, day, 'historical')
    assert caught.value.status_code == 422


def test_standalone_http_uses_catalog_and_needs_no_saved_strategy(catalog):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers.workspace import router
    topics.seed_topics()
    app = FastAPI(); app.include_router(router)
    before = _counts()
    with TestClient(app) as client:
        response = client.get('/api/v1/sectors/'+CODE+'/members')
    assert response.status_code == 200
    assert response.json()['membership_scope'] == 'latest'
    assert response.json()['items'][0]['board_label'] == '主板'
    assert _counts() == before
