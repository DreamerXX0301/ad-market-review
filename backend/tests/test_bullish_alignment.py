"""One MA definition for selection, tracking and historical signal evaluation."""
from datetime import date,timedelta
import pytest
from app.domain.conditions import validate,evaluate,specs,metrics_from_enriched,metrics_for
from app.analytics.backtest_engine import warmup,availability
from test_workspace import ws,make_strategy

RULE={'field':'bullish_ma_alignment','op':'==','value':True}


def test_builder_spec_is_a_visible_boolean_trend():
    spec=next(row for row in specs() if row['field']=='bullish_ma_alignment')
    assert spec['type']=='boolean' and spec['operators']==['==','!=']
    assert not spec['hidden_in_builder'] and spec['category']=='趋势状态'
    for bad in (1,'true',None):
        with pytest.raises(ValueError):validate({**RULE,'value':bad})


@pytest.mark.parametrize('averages,expected', [([12,11,10,9,8],True),([12,12,10,9,8],False),([8,9,10,11,12],False),([12,11,None,9,8],None),([12,11,float('nan'),9,8],None),([12,11,10,9,0],None)])
def test_strict_alignment_requires_all_five_valid_averages(averages,expected):
    row={f'ma{n}':v for n,v in zip((5,10,20,30,60),averages)}
    row['volume_ratio']=None
    metrics=metrics_from_enriched(row)
    assert metrics['bullish_ma_alignment'] is expected
    assert evaluate(validate(RULE),metrics)['result'] is expected
    # Long MA250 and today's close are separate conditions, not hidden requirements.
    if expected is True:assert metrics['ma250'] is None and metrics['close'] is None


def bars(count=330):
    days=[];cursor=date(2024,1,1)
    while len(days)<count:
        if cursor.weekday()<5:days.append(cursor.isoformat())
        cursor+=timedelta(days=1)
    return days,[{'date':day,'open':100+i,'high':102+i,'low':99+i,'close':101+i,'volume':100,'amount':10000} for i,day in enumerate(days)]


def test_daily_weekly_warmup_and_no_future_data():
    calendar,rows=bars()
    assert metrics_for(rows,calendar[58],calendar)['bullish_ma_alignment'] is None
    assert metrics_for(rows,calendar[59],calendar)['bullish_ma_alignment'] is True
    assert metrics_for(rows,calendar[295],calendar,'W')['bullish_ma_alignment'] is None
    assert metrics_for(rows,calendar[-1],calendar,'W')['bullish_ma_alignment'] is True
    before=metrics_for(rows,calendar[70],calendar)
    rows[-1]['close']=1
    after=metrics_for(rows,calendar[70],calendar)
    assert after['bullish_ma_alignment']==before['bullish_ma_alignment'] is True


def test_gap_and_company_action_restart_alignment_warmup():
    calendar,rows=bars(80)
    rows[70]['corporate_action']=True
    assert metrics_for(rows,calendar[-1],calendar)['bullish_ma_alignment'] is None
    rows[70].pop('corporate_action')
    del rows[70]
    assert metrics_for(rows,calendar[-1],calendar)['bullish_ma_alignment'] is None


def test_strategy_runs_real_pipeline_and_retains_ma_evidence(ws):
    _,client=ws
    strategy=make_strategy(client,RULE)
    response=client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs',json={})
    assert response.status_code==200,response.text
    items={row['instrument_id']:row for row in response.json()['items']}
    assert items['600001.SH']['decision']=='matched'
    assert items['000001.SZ']['decision']=='unmatched'
    for item in items.values():
        assert all(item['metrics'][f'ma{n}'] is not None for n in (5,10,20,30,60))
    # Boolean false remains a valid way to exclude aligned stocks.
    assert evaluate(validate({**RULE,'value':False}),items['000001.SZ']['metrics'])['result'] is True


def test_backtest_availability_requires_sixty_periods(ws):
    market,_=ws
    # Keep this a warmup test with valid listing identities; missing identity
    # rejection has its own backtest availability tests.
    base_instruments=market.instruments
    market.instruments=lambda limit=10000:[{**row,'list_date':'2020-01-01'} for row in base_instruments(limit=limit)]
    assert warmup(validate(RULE))==60
    result=availability(market,{'definition':RULE,'period':'D'})
    assert result['available'] and result['warmup_bars']==60
    assert result['min_date']==market.days[60]
    result=availability(market,{'definition':RULE,'period':'W'})
    assert not result['available'] and result['warmup_bars']==60
