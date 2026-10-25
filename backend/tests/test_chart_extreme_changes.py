"""Display extremes use the same verified previous close; strategy high stays daily."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from app.analytics.indicators import aggregate_weekly, chart_extreme_changes, enrich_bars as original_enrich_bars
from app.analytics.chart_indicators import display_indicators
from app.analytics.monthly import aggregate_monthly, enrich_monthly_bars as original_enrich_monthly_bars


def enrich_bars(*args, **kwargs):
    return original_enrich_bars(*args, include_chart_extremes=True, **kwargs)


def enrich_monthly_bars(*args, **kwargs):
    return original_enrich_monthly_bars(*args, include_chart_extremes=True, **kwargs)


def days(count=70):
    day=date(2026,1,1);result=[]
    while len(result)<count:
        if day.weekday()<5:result.append(day.isoformat())
        day+=timedelta(days=1)
    return result


def rows(calendar):
    return [dict(date=day,open=100,high=101,low=99,close=100,volume=1000,amount=100000,
                 corporate_action=False,corporate_action_coverage_known=True) for day in calendar]


def test_daily_high_and_low_share_exact_previous_close_and_keep_prices():
    calendar=days();source=rows(calendar);source[-2].update(open=520,high=521,low=519,close=520)
    source[-1].update(open=502.02,high=624,low=502,close=624)
    before=deepcopy(source);bar=enrich_bars(source,calendar)[-1]
    assert bar['chart_high_change_pct']==20
    assert bar['chart_low_change_pct']==pytest.approx(-3.461538461538)
    assert bar['chart_extreme_change_reference_date']==calendar[-2]
    assert bar['chart_extreme_change_period']=='D'
    assert bar['intraday_high_change_pct']==20
    assert {k:bar[k] for k in ('open','high','low','close')}=={k:source[-1][k] for k in ('open','high','low','close')}
    assert source==before


@pytest.mark.parametrize('change',[
    {'corporate_action':True},{'corporate_action_coverage_known':False},{'price_continuity':False},
    {'high':float('inf')},{'high':99},{'low':101},{'close':0},{'open':True},
])
def test_unverified_daily_pair_keeps_both_values_unknown(change):
    calendar=days(2);prior,current=rows(calendar);current.update(change)
    state=chart_extreme_changes(current,prior,{d:i for i,d in enumerate(calendar)})
    assert state['chart_high_change_pct'] is state['chart_low_change_pct'] is None
    assert state['chart_high_change_pct_reason'] and state['chart_low_change_pct_reason']
    assert state['chart_extreme_change_reference_date'] is None


def test_gap_first_row_and_unknown_prior_actions_do_not_use_visible_neighbor():
    calendar=days(4);source=rows(calendar)
    for prior,positions in [(None,{d:i for i,d in enumerate(calendar)}),
                            (source[0],{d:i for i,d in enumerate(calendar)}),
                            (source[2],{}),
                            ({**source[2],'corporate_action_coverage_known':False},{d:i for i,d in enumerate(calendar)})]:
        state=chart_extreme_changes(source[3],prior,positions)
        assert state['chart_high_change_pct'] is state['chart_low_change_pct'] is None


def test_quote_continuity_only_changes_plotted_indicators_not_extreme_comparison():
    calendar=days();source=rows(calendar);del source[-2]
    strict=enrich_bars(source,calendar)
    plotted,actual=display_indicators(strict,source,'quotes','D')
    assert actual=='quotes' and plotted[-1]['ma20'] is not None
    assert plotted[-1]['chart_high_change_pct'] is plotted[-1]['chart_low_change_pct'] is None
    assert '前一个市场交易日' in plotted[-1]['chart_low_change_pct_reason']


def test_same_anchor_adjusted_input_allows_extremes_without_altering_strategy_high():
    calendar=days(3);source=rows(calendar)
    source[1].update(open=50,high=51,low=49,close=50,corporate_action=True)
    source[2].update(open=50,high=55,low=48,close=52)
    raw=enrich_bars(source,calendar)
    assert raw[1]['chart_high_change_pct'] is None
    assert raw[2]['chart_high_change_pct']==10
    assert raw[2]['chart_low_change_pct']==-4
    adjusted=[{**r,**{k:r[k]*.5 for k in ('open','high','low','close')}} if i==0 else {**r} for i,r in enumerate(source)]
    adjusted[1]['corporate_action']=False
    assert enrich_bars(adjusted,calendar)[1]['chart_high_change_pct']==2
    assert enrich_bars(adjusted,calendar)[1]['intraday_high_change_pct']==2


def test_week_extremes_use_previous_complete_week_close_and_preserve_daily_strategy_contract():
    calendar=days(30);source=rows(calendar)
    source[8].update(high=130,low=95)
    weekly=enrich_bars(aggregate_weekly(source,calendar,calendar[-1]))
    target=next(r for r in weekly if r['high']==130)
    assert target['chart_high_change_pct']==30 and target['chart_low_change_pct']==-5
    assert target['chart_extreme_change_period']=='W'
    assert target['intraday_high_change_pct'] is None
    broken=deepcopy(source);del broken[6]
    unknown=enrich_bars(aggregate_weekly(broken,calendar,calendar[-1]))
    bad=next(r for r in unknown if r['high']==130)
    assert bad['chart_high_change_pct'] is bad['chart_low_change_pct'] is None


def test_month_uses_complete_previous_month_and_forming_month_stays_unknown():
    calendar=days(90);source=rows(calendar)
    target_index=next(i for i,d in enumerate(calendar) if d.startswith('2026-02'))
    source[target_index].update(high=125,low=90)
    monthly=enrich_monthly_bars(aggregate_monthly(source,calendar,calendar[-1]))
    target=next(r for r in monthly if r['date'].startswith('2026-02'))
    assert target['chart_high_change_pct']==25 and target['chart_low_change_pct']==-10
    assert target['chart_extreme_change_period']=='M'
    assert target['chart_extreme_change_reference_date'].startswith('2026-01')
    partial=enrich_monthly_bars(aggregate_monthly(source,calendar,'2026-02-16'))[-1]
    assert partial['chart_high_change_pct'] is partial['chart_low_change_pct'] is None
    assert partial['chart_extreme_change_reference_date'] is None
    assert partial['chart_low_change_pct_reason']


def test_tail_keeps_same_extreme_values_and_no_future_dependence():
    calendar=days();source=rows(calendar)
    full=enrich_bars(source,calendar);tail=enrich_bars(source,calendar,tail=2)
    assert tail==full[-2:]
    early=enrich_bars(source[:40],calendar)
    source[-1].update(high=1e6,close=1e6)
    assert enrich_bars(source[:40],calendar)==early


def test_default_strategy_path_has_no_extra_extreme_fields_or_work():
    calendar=days();source=rows(calendar)
    plain=original_enrich_bars(source,calendar)
    chart=enrich_bars(source,calendar)
    assert all(not any(key.startswith('chart_') for key in row) for row in plain)
    assert [{key:value for key,value in row.items() if not key.startswith('chart_')} for row in chart]==plain
    monthly=aggregate_monthly(source,calendar,calendar[-1])
    plain_month=original_enrich_monthly_bars(monthly)
    chart_month=enrich_monthly_bars(monthly)
    assert [{key:value for key,value in row.items() if not key.startswith('chart_')} for row in chart_month]==plain_month


@pytest.mark.parametrize('kind',['stock','index'])
@pytest.mark.parametrize('period',['D','W','M'])
@pytest.mark.parametrize('basis',['none','forward'])
def test_real_market_chart_entry_materializes_only_same_basis_display_fields(kind,period,basis):
    from app.analytics.market import MarketData
    class DisplayMarket(MarketData):
        def __init__(self):self.days=days(100);self.rows=rows(self.days)
        def status(self):return {'data_end':self.days[-1],'snapshot_id':'fixture','warnings':[],'quality':'complete'}
        def calendar(self):return list(self.days)
        def bars(self,symbol,as_of=None):return [dict(row) for row in self.rows if not as_of or row['date']<=as_of]
        def actions(self,*args,**kwargs):return []
        def instrument(self,symbol):return {'instrument_id':symbol,'kind':kind}
    market=DisplayMarket();payload=market.chart('600001.SH',period=period,price_basis=basis,indicator_continuity='quotes')
    assert payload['period']==period
    by_date={bar['date']:bar for bar in payload['bars']}
    comparable=[bar for bar in payload['bars'] if bar['chart_high_change_pct'] is not None]
    assert comparable
    for bar in comparable:
        prior=by_date[bar['chart_extreme_change_reference_date']]
        assert bar['chart_extreme_change_period']==period
        assert bar['chart_high_change_pct']==pytest.approx((bar['high']/prior['close']-1)*100)
        assert bar['chart_low_change_pct']==pytest.approx((bar['low']/prior['close']-1)*100)
        assert prior['date']<bar['date']


@pytest.mark.parametrize('period',['D','W','M'])
def test_sector_chart_entry_exposes_the_same_extremes_without_member_quotes(tmp_path,monkeypatch,period):
    from app.services import sector_charts as charts
    from test_sector_charts import Market, Adapter, CODE, DAY
    market=Market();published=tmp_path/'published'
    monkeypatch.setattr(charts,'published_dir',lambda:published)
    monkeypatch.setattr(charts,'_source_today',lambda:DAY)
    adapter=Adapter(market);plan=charts.plan_sector_chart(market,CODE,DAY)
    charts.collect_sector_chart(market,plan,adapter)
    before=len(adapter.calls)
    payload=charts.read_sector_chart(market,CODE,DAY,period)
    assert len(adapter.calls)==before,'reading the chart must remain offline'
    by_date={bar['date']:bar for bar in payload['bars']}
    comparable=[bar for bar in payload['bars'] if bar['chart_high_change_pct'] is not None]
    assert comparable
    for bar in comparable:
        prior=by_date[bar['chart_extreme_change_reference_date']]
        assert bar['chart_extreme_change_period']==period
        assert bar['chart_high_change_pct']==pytest.approx((bar['high']/prior['close']-1)*100)
        assert bar['chart_low_change_pct']==pytest.approx((bar['low']/prior['close']-1)*100)
