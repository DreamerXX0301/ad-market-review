"""Cross-page date visits retain bounded, independently computed rank scopes."""
from copy import deepcopy

import pytest

from app.services import market_service as service
from test_overview_gainers_scope import ranked_market


@pytest.fixture(autouse=True)
def empty_rank_cache():
    with service._gainers_lock:service._gainers_cache.clear()
    yield
    with service._gainers_lock:service._gainers_cache.clear()


def _inputs():
    market,reference,_=ranked_market()
    position=market.days.index(market.day)
    days=[market.days[position-offset] for offset in range(6)]
    rows={day:market.panel(day) for day in days}
    return market,reference,{**market.status(),'path':'fixture-date-visits'},days,rows


def _observe_computations(monkeypatch):
    actual=service.gainers_rows;calls=[]
    def compute(market,reference,day,rows,max_items=None):
        calls.append((day,max_items))
        return actual(market,reference,day,rows,max_items)
    monkeypatch.setattr(service,'gainers_rows',compute)
    return calls


def test_three_dates_across_overview_and_full_rank_revisit_without_recomputing(monkeypatch):
    market,reference,status,days,rows=_inputs()
    expected={(day,limit):service.gainers_rows(market,reference,day,rows[day],limit)
              for day in days[:3] for limit in (5,None)}
    calls=_observe_computations(monkeypatch)
    # The user opens the latest day first, then two historical observations.
    for day in days[:3]:
        for limit in (5,None):
            assert service.cached_gainers(market,status,reference,day,rows[day],limit)==expected[day,limit]
    assert len(calls)==6
    history_reads=len(market.history_requests)
    for limit in (5,None):
        assert service.cached_gainers(market,status,reference,days[0],rows[days[0]],limit)==expected[days[0],limit]
    assert len(calls)==6 and len(market.history_requests)==history_reads
    # Full ranking still contains the unquoted unknown tail and top-five keeps
    # its original cohort evidence; the two values are never substituted.
    full,_=expected[days[0],None];top,_=expected[days[0],5]
    assert len(full)==600 and len(top)==5 and top==full[:5]
    assert full[-1]['rank'] is None


def test_partial_date_churn_preserves_full_rank_lru_and_both_scopes_stay_bounded(monkeypatch):
    market,reference,status,days,rows=_inputs()
    calls=_observe_computations(monkeypatch)
    expected={day:deepcopy(service.cached_gainers(market,status,reference,day,rows[day]))
              for day in days[:4]}
    for day in days:
        service.cached_gainers(market,status,reference,day,rows[day],5)
    assert len(service._gainers_cache)==8
    assert sum(key[-1] is None for key in service._gainers_cache)==4
    before=len(calls)
    for day in days[:4]:
        assert service.cached_gainers(market,status,reference,day,rows[day])==expected[day]
    assert len(calls)==before
    service.cached_gainers(market,status,reference,days[4],rows[days[4]])
    service.cached_gainers(market,status,reference,days[0],rows[days[0]])
    assert calls[-2:]==[(days[4],None),(days[0],None)]
    assert len(service._gainers_cache)==8


def test_revisited_date_still_invalidates_on_source_revision_with_same_timestamp(monkeypatch):
    market,reference,status,days,rows=_inputs()
    calls=_observe_computations(monkeypatch)
    day=days[0]
    original=service.cached_gainers(market,status,reference,day,rows[day],5)
    changed=deepcopy(reference)
    source_day=market.days[8]
    changed['_supplement']['annual_pools'][source_day]['items'][0]['last_price']+=1
    assert changed['_supplement']['created_at_ms']==reference['_supplement']['created_at_ms']
    revised=service.cached_gainers(market,status,changed,day,rows[day],5)
    assert len(calls)==2
    assert original[0][0]['last_limit_up_date']==source_day
    assert revised[0][0]['last_limit_up_date'] is None


def test_full_rank_reuses_overview_prices_and_survives_restart(tmp_path, monkeypatch):
    from app.services import pane_read_cache, topic_member_metrics

    market, reference, status, days, rows = _inputs()
    day = days[0]
    proof = ('fixed-snapshot', 'actual-price-and-reference-sha', 'retained-pool-sha')
    market.verify_condition_sources = lambda: proof
    monkeypatch.setattr(pane_read_cache, '_root', lambda: tmp_path)
    monkeypatch.setattr(pane_read_cache, '_memory', pane_read_cache.OrderedDict())
    monkeypatch.setattr(pane_read_cache, '_memory_bytes', 0)
    expected = service.gainers_rows(market, reference, day, rows[day])
    top = service.cached_gainers(market, status, reference, day, rows[day], 5, source_evidence=proof)
    assert top[0] == expected[0][:5]
    assert (tmp_path / day / 'gainers-price-rank-v1.json').is_file()

    def must_use_cache(*args, **kwargs):
        pytest.fail('unexpected ranking recomputation')

    monkeypatch.setattr(service, '_gainers_price_rank', must_use_cache)
    assert service.cached_gainers(market, status, reference, day, rows[day], source_evidence=proof) == expected
    assert (tmp_path / day / 'gainers-full-events-v1.json').is_file()

    service._gainers_cache.clear()
    pane_read_cache._memory.clear()
    monkeypatch.setattr(pane_read_cache, '_memory_bytes', 0)
    monkeypatch.setattr(topic_member_metrics, 'limit_metrics', must_use_cache)
    new_panel = [{**row, 'name': '页面数据仍取当前输入'} for row in rows[day]]
    actual, summary = service.cached_gainers(market, status, reference, day, new_panel, source_evidence=proof)
    quoted = {row['instrument_id'] for row in new_panel}
    assert actual == [{**row, 'name': '页面数据仍取当前输入'} if row['instrument_id'] in quoted else row
                      for row in expected[0]]
    assert summary == expected[1]
