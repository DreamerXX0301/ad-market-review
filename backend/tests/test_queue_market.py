"""Whole-result sorting precedes pagination and preserves unknown observations."""
import pytest
from fastapi import HTTPException
from app.services import market_service as service


@pytest.fixture
def ranking(monkeypatch):
    rows = [
        {'instrument_id':'600001.SH','name':'B','change_30d_pct':20,'change_pct':2,'limit_up_ytd':None,'limit_up_ytd_observed':30,'topics':[{'name':'Z'}]},
        {'instrument_id':'600002.SH','name':'A','change_30d_pct':10,'change_pct':4,'limit_up_ytd':2,'topics':[{'name':'A'}]},
        {'instrument_id':'600003.SH','name':'C','change_30d_pct':None,'change_pct':None,'limit_up_ytd':3,'topics':[]},
    ]
    monkeypatch.setattr(service,'panel',lambda day:(object(),{'snapshot_id':'fixture','data_end':day},day,{},[]))
    monkeypatch.setattr(service,'cached_gainers',lambda *args:(rows,{}))
    return rows


@pytest.mark.parametrize('order,expected',[('asc',['600002.SH','600001.SH','600003.SH']),('desc',['600001.SH','600002.SH','600003.SH'])])
def test_sort_before_pagination_keeps_unknown_last(ranking,order,expected):
    original=[r['instrument_id'] for r in ranking]
    result=service.market_page('gainers','2026-10-19',sort_by='change_30d_pct',sort_order=order,page=2,page_size=1)
    assert result['items'][0]['instrument_id']==expected[1]
    assert result['total']==3 and result['pages']==3
    all_rows=service.market_page('gainers','2026-10-19',sort_by='change_30d_pct',sort_order=order)['items']
    assert [r['instrument_id'] for r in all_rows]==expected
    assert [r['instrument_id'] for r in ranking]==original


def test_year_count_lower_bound_remains_unknown_sort_tail(ranking):
    result=service.market_page('gainers','2026-10-19',sort_by='limit_up_ytd',sort_order='desc')
    assert [r['instrument_id'] for r in result['items']]==['600003.SH','600002.SH','600001.SH']


def test_label_sort_search_and_page_together(ranking):
    result=service.market_page('gainers','2026-10-19',sort_by='topics',sort_order='asc',query='60000',page_size=2)
    assert [r['name'] for r in result['items']]==['A','B']
    assert result['total']==3


@pytest.mark.parametrize('field,order',[('private_sql','asc'),('name','sideways')])
def test_invalid_sort_is_rejected(ranking,field,order):
    with pytest.raises(HTTPException) as error:service.market_page('gainers','2026-10-19',sort_by=field,sort_order=order)
    assert error.value.status_code==422


def test_no_comparable_quotes_does_not_claim_broad_decline(monkeypatch):
    class Market:
        def breadth_history(self,day):return []
    monkeypatch.setattr(service,'panel',lambda day:(Market(),{'snapshot_id':'fixture'},day,{},[]))
    monkeypatch.setattr(service,'_breadth',lambda *args:{'up':0,'down':0,'flat':0,'unknown':5500})
    result=service.market_page('emotion','2025-10-20')
    assert result['summary']['temperature'] is None
    assert result['summary']['cycle']=='待核验'
