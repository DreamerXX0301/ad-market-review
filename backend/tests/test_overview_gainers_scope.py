"""Top-five proof work cannot change full ranking or its frozen batch metadata."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from app.services import market_service as service
from test_gainers_limit_history import rank_market
from test_topic_member_metrics import A, MetricMarket, source_pool


def ranked_market():
    market=rank_market(MetricMarket());base=deepcopy(market.data[A]);market.data={};market.master={}
    symbols=[f'{600000+i:06}.SH' for i in range(600)]
    for index,symbol in enumerate(symbols):
        values=[{**row,'instrument_id':symbol} for row in deepcopy(base)]
        current=next(row for row in values if row['date']==market.day)
        current['close']*=3-index*.1 if index<5 else 1
        market.data[symbol]=values
        market.master[symbol]={'instrument_id':symbol,'code':symbol[:6],'name':symbol,'kind':'stock'}
    # Missing quote keeps this identity in the full searchable unknown tail.
    market.data[symbols[-1]]=[row for row in market.data[symbols[-1]] if row['date']!=market.day]
    old=market.days[8];future=market.days[market.days.index(market.day)+1]
    proof=source_pool(market,old,[symbols[0],symbols[50]])
    proof['items'][1]['last_price']+=1  # Unrelated invalid member of the original top-row cohort.
    supplement={'created_at_ms':1,'annual_pools':{old:proof,future:source_pool(market,future,[symbols[0]])}}
    return market,{'_supplement':supplement},symbols


def test_top_five_matches_full_fields_including_original_cohort_coverage_and_gaps():
    market,reference,symbols=ranked_market()
    market.data[symbols[1]]=[row for row in market.data[symbols[1]] if row['date']!=market.days[-15]]
    rows=market.panel(market.day)
    full,summary=service.gainers_rows(market,reference,market.day,rows)
    assert full[0]['limit_up_ytd_coverage']['market_full_days']==0
    market.history_requests.clear()
    top,top_summary=service.gainers_rows(market,reference,market.day,rows,5)
    assert top==[row for row in full if row['rank'] is not None][:5]
    assert top_summary==summary and top[1]['return_30d_missing_sessions']==1
    assert len(market.history_requests)==1
    assert market.history_requests[0]['symbols']==set(symbols[:256])
    assert top[0]['last_limit_up_date']==market.days[8], 'future source must not enter a past observation'


def test_full_and_partial_cache_are_isolated_in_parallel_and_fingerprint_actual_supplement(monkeypatch):
    market,reference,symbols=ranked_market();day=market.day;rows=market.panel(day)
    status={**market.status(),'path':'fixture-scope'}
    with service._gainers_lock:service._gainers_cache.clear()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            top_future=pool.submit(service.cached_gainers,market,status,reference,day,rows,5)
            full_future=pool.submit(service.cached_gainers,market,status,reference,day,rows)
            top,_=top_future.result();full,_=full_future.result()
        assert len(full)==600 and len(top)==5 and top==full[:5]
        monkeypatch.setattr(service,'panel',lambda *_:(market,status,day,reference,rows))
        first=service.market_page('gainers',day,page_size=25,top_n=100,sort_by='change_30d_pct',sort_order='desc')
        second=service.market_page('gainers',day,page_size=25,page=2,top_n=100,sort_by='change_30d_pct',sort_order='desc')
        assert first['total']==second['total']==100 and first['items']==full[:25] and second['items']==full[25:50]
        ascending=service.market_page('gainers',day,sort_by='change_30d_pct',sort_order='asc',limit=1000)
        assert ascending['items'][-1]['instrument_id']==symbols[-1] and ascending['items'][-1]['rank'] is None
        changed=deepcopy(reference)
        changed['_supplement']['annual_pools'][market.days[8]]['items'][0]['last_price']+=1
        changed_top,_=service.cached_gainers(market,status,changed,day,rows,5)
        assert changed_top[0]['last_limit_up_date'] is None
        assert changed['_supplement']['created_at_ms']==reference['_supplement']['created_at_ms']
        assert len(service._gainers_cache)==3
    finally:
        with service._gainers_lock:service._gainers_cache.clear()
