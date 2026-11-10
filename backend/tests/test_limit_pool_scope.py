"""Rolling source pool scope and delta reuse with isolated replay evidence."""
from app.adapters.providers import ProviderError
from app.services import market_supplement as supplement, source_service
from app.services.limit_pool_scope import VERSION, rolling_days, rolling_start
from app.services.supplement_policy import FUND_FLOW_RETIRED
from test_supplement_revalidation import DOWN, UP, LocalMarket


def test_rolling_calendar_boundary_includes_leap_day():
    assert rolling_start('2024-02-29') == '2023-03-01'
    assert rolling_days(['2025-11-10','2025-11-11','2026-11-10','2026-11-11'],'2026-11-10') == [
        '2025-11-11','2026-11-10']


def test_initial_fill_then_daily_append_reuses_unchanged_days(tmp_path, monkeypatch):
    outside='2025-11-10'; oldest='2025-11-11'; stable='2026-01-05'
    revised='2026-11-09'; previous_day='2026-11-10'; target='2026-11-11'
    first=LocalMarket(tmp_path/'previous',{day:{UP:10.,DOWN:10.} for day in (outside,oldest,stable,revised,previous_day)})
    monkeypatch.setattr(supplement,'_root',lambda:tmp_path/'supplement')
    requests=[]
    class Adapter:
        def request(self,path,params):
            day=next(day for day in (*first.calendar(),target) if supplement._midnight(day)==params['date_ms'])
            symbol=UP if path.endswith('limit-up-pool') else DOWN
            requests.append((day,symbol))
            return {'data':{'item':[{'ticker':symbol[:6],'thscode':symbol,'last_price':10.,'date':day}],
                            'pagination':{'total':1}}}
    class Public:
        def limit_pool(self,day):raise ProviderError('isolated test')
    monkeypatch.setattr(source_service,'client',lambda *_:Adapter())
    monkeypatch.setattr(supplement,'PublicMarketClient',Public)
    opts={'pool_history_version':VERSION,'fund_flow_policy':FUND_FLOW_RETIRED}
    initial=supplement.refresh_for_market(first,previous_day,year_history=True,**opts)
    assert {day for day,_ in requests}=={oldest,stable,revised,previous_day}
    assert initial['annual_coverage']['expected_days']==3
    assert '近一年 2025-11-11 至 2026-11-10' in initial['message']

    requests.clear()
    proof={'previous_snapshot':'previous','previous_begin':outside,'previous_end':previous_day,
           'same_stock_directory':True,'changed_dates':[revised,target]}
    class IncrementMarket(LocalMarket):
        def status(self):
            return {**super().status(),'coverage':{'limit_pool_quote_delta':proof}}
    market=IncrementMarket(tmp_path/'current',{**first.quotes,revised:{UP:9.,DOWN:10.},target:{UP:10.,DOWN:10.}})
    progress=[]
    result=supplement.refresh_for_market(market,target,progress=lambda *args:progress.append(args),**opts)
    assert requests==[(target,UP),(target,DOWN)]
    assert [entry[2].split(' · ')[0] for entry in progress if entry[0]=='核验本地涨跌停证据']==[revised,revised,target,target]
    assert result['annual_pools'][stable]==initial['annual_pools'][stable]
    assert revised not in result['annual_pools'] and revised in result['annual_down_pools']
    assert '近一年 2025-11-12 至 2026-11-11，涨停 3/4 日完整，跌停 4/4 日完整' in result['message']
