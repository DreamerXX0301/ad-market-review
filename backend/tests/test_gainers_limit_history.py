"""Ranking and detail share dated, batched closing-limit evidence."""
from copy import deepcopy
from math import ceil

import pytest

from test_topic_member_metrics import MetricMarket, source_pool, evaluate, A, B
from app.services.topic_member_metrics import BATCH_SIZE, limit_metrics
from app.services import market_service


LIMIT_KEYS = ('limit_up_ytd', 'limit_up_ytd_observed', 'limit_up_ytd_coverage',
              'last_limit_up_date', 'last_limit_up_complete', 'last_limit_up_coverage',
              'sessions_since_limit_up')


def rank_market(market):
    def history(as_of=None, lookback=None):
        days = [day for day in market.days if as_of is None or day <= as_of]
        start = days[-lookback] if lookback and len(days) >= lookback else days[0]
        return {symbol: [dict(row) for row in values if start <= row['date'] <= (as_of or days[-1])]
                for symbol, values in market.data.items()}
    market.history_panel = history
    return market


def test_rank_latest_date_matches_member_statistics_and_excludes_future_source():
    market=rank_market(MetricMarket()); day=market.day
    old=market.days[8]; future=market.days[market.days.index(day)+1]
    supplement={'annual_pools':{old:source_pool(market,old,[A]),future:source_pool(market,future,[A])}}
    expected=evaluate(market,symbols=[A,B],supplement=supplement)
    market.calls.clear();market.history_requests.clear()
    rows,_=market_service.gainers_rows(market,{'_supplement':supplement},day,market.panel(day))
    by_symbol={row['instrument_id']:row for row in rows}
    for symbol in (A,B):
        for key in LIMIT_KEYS:assert by_symbol[symbol][key]==expected[symbol][key]
    assert by_symbol[A]['last_limit_up_date']==old
    assert not by_symbol[A]['last_limit_up_complete']
    assert by_symbol[B]['last_limit_up_date'] is None
    assert market.calls['history_for_instruments']==1
    assert market.history_requests[0]['start']==old
    assert market.calls['actions_for_instruments']==0


@pytest.mark.parametrize('case',['missing_quote','duplicate_quote','wrong_source_price'])
def test_recent_limit_needs_unique_matching_historical_close(case):
    market=MetricMarket(); source_day=market.days[8]
    supplement={'annual_pools':{source_day:source_pool(market,source_day,[A])}}
    own=next(row for row in market.data[A] if row['date']==source_day)
    if case=='missing_quote':market.data[A].remove(own)
    elif case=='duplicate_quote':market.data[A].append(deepcopy(own))
    else:supplement['annual_pools'][source_day]['items'][0]['last_price']+=1
    result=limit_metrics(market,market.day,[A],supplement)[A]
    assert result['last_limit_up_date'] is None
    assert result['limit_up_ytd'] is None and result['limit_up_ytd_observed']==0


def test_no_historical_source_does_not_turn_ten_percent_bar_into_limit_or_read_history():
    market=MetricMarket()
    market.data[A][-4]['close']=market.data[A][-5]['close']*1.1
    result=limit_metrics(market,market.day,[A],{})[A]
    assert result['last_limit_up_date'] is None
    assert result['limit_up_ytd'] is None and result['limit_up_ytd_observed']==0
    assert market.calls['history_for_instruments']==0
    assert result['last_limit_up_coverage']['unknown_days']==len([day for day in market.days if day<=market.day])


def test_reference_and_public_positive_dates_share_year_lower_bound_without_double_counting():
    market=MetricMarket(); old=market.days[0]; first=market.days[8]; second=market.days[9]
    market.reference_data={'pool_history':{
        observed:{'limitup':source_pool(market,observed,[A])['items'],'coverage':{'limitup':{'complete':False}}}
        for observed in (old,first)}}
    supplement={'annual_pools':{first:source_pool(market,first,[A],complete=False)},
                'public_pools':{observed:{**source_pool(market,observed,[A]),'source':'eastmoney-public'}
                                for observed in (first,second,market.days[-1])}}
    limits=limit_metrics(market,market.day,[A],supplement)[A]
    detailed=evaluate(market,symbols=[A],supplement=supplement)[A]
    assert limits['limit_up_ytd'] is None
    assert limits['limit_up_ytd_observed']==2
    assert limits['last_limit_up_date']==second
    assert detailed['limit_up_ytd_observed']==limits['limit_up_ytd_observed']
    assert not limits['limit_up_ytd_coverage']['complete']
    historical=limit_metrics(market,market.days[7],[A],supplement)[A]
    assert historical['limit_up_ytd_observed']==0
    assert historical['limit_up_ytd'] is None
    assert historical['last_limit_up_date']==old


def test_whole_rank_limit_history_reads_at_most_256_securities_per_batch():
    market=MetricMarket(); source_day=market.days[8]
    supplement={'annual_pools':{source_day:source_pool(market,source_day,[B])}}
    symbols=[f'{600000+index:06}.SH' for index in range(600)]
    for symbol in symbols:market.data[symbol]=deepcopy(market.data[A])
    result=limit_metrics(market,market.day,symbols,supplement)
    assert len(result)==600
    assert market.calls['history_for_instruments']==ceil(600/BATCH_SIZE)
    assert all(len(request['symbols'])<=BATCH_SIZE and request['start']==source_day and request['end']==market.day
               for request in market.history_requests)
    assert market.calls['actions_for_instruments']==0


@pytest.mark.parametrize('order',['asc','desc'])
def test_recent_limit_sort_keeps_unknown_dates_last(monkeypatch, order):
    market=MetricMarket(); day=market.day
    rows=[{'instrument_id':A,'last_limit_up_date':market.days[8]},
          {'instrument_id':B,'last_limit_up_date':market.days[9]},
          {'instrument_id':'688001.SH','last_limit_up_date':None}]
    monkeypatch.setattr(market_service,'panel',lambda *_:(market,market.status(),day,{},rows))
    monkeypatch.setattr(market_service,'cached_gainers',lambda *_:(deepcopy(rows),{}))
    result=market_service.market_page('gainers',day,sort_by='last_limit_up_date',sort_order=order,page_size=2)
    assert result['items'][0]['instrument_id']==(A if order=='asc' else B)
    page2=market_service.market_page('gainers',day,sort_by='last_limit_up_date',sort_order=order,page_size=2,page=2)
    assert page2['items'][0]['last_limit_up_date'] is None
