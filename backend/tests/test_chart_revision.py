"""Regression checks for action gaps, explicit basis and delayed swing candidates."""
from copy import deepcopy
from datetime import date,timedelta
import pytest
from app.analytics.market import MarketData
from app.analytics.adjustments import continuous_prices
from app.analytics.indicators import enrich_bars,aggregate_weekly
from app.analytics.waves import candidate_waves


def series(n=90):
    first=date(2026,1,1)
    return [{'date':(first+timedelta(days=i)).isoformat(),'open':10+i*.01,'high':11+i*.01,'low':9+i*.01,'close':10+i*.01,'volume':100,'amount':1000,'corporate_action':i==45} for i in range(n)]


def event(rows,**extra):
    return {'date':rows[45]['date'],'dividend_per_share':.5,'per_share_bonus':0,'allotment_ratio':0,'allotment_price':0,**extra}


def test_explicit_forward_removes_only_resolved_action_gap_without_mutating_raw():
    rows=series();before=deepcopy(rows);cal=[r['date'] for r in rows]
    raw=enrich_bars(rows,cal);adjusted,info=continuous_prices(rows,[event(rows)],rows[-1]['date'],cal)
    computed=enrich_bars(adjusted,cal)
    assert rows==before and info['usable']
    assert raw[45]['macd_dif'] is None and computed[45]['macd_dif'] is not None
    assert adjusted[44]['close']==pytest.approx(rows[44]['close']-.5)
    assert adjusted[45]['close']==rows[45]['close'] and adjusted[-1]['close']==rows[-1]['close']
    assert adjusted[0]['volume']==rows[0]['volume']


def test_forward_does_not_repair_missing_quotes_or_guess_uncertain_action():
    rows=series();cal=[r['date'] for r in rows];rows.pop(70)
    adjusted,info=continuous_prices(rows,[event(rows)],rows[-1]['date'],cal)
    out=enrich_bars(adjusted,cal)
    assert info['usable'] and len(out)==89
    assert out[70]['missing_market_sessions_before']==1 and out[70]['macd_dif'] is None
    bad,info=continuous_prices(rows,[event(rows),event(rows)],rows[-1]['date'],cal)
    assert not info['usable'] and bad==rows


class ChartMarket(MarketData):
    def __init__(self,rows):self.rows=rows
    def status(self):return {'data_end':self.rows[-1]['date'],'snapshot_id':'fixture','quality':'partial','warnings':[]}
    def calendar(self):return [r['date'] for r in self.rows]
    def bars(self,symbol,as_of=None):return [dict(r) for r in self.rows if not as_of or r['date']<=as_of]
    def actions(self,symbol,start=None,end=None):return [event(self.rows)]
    def instrument(self,symbol):return {'kind':'stock','instrument_id':symbol}


def test_chart_price_contract_default_is_raw_and_explicit_anchor_excludes_future_event():
    m=ChartMarket(series());raw=m.chart('600000.SH');fwd=m.chart('600000.SH',price_basis='forward')
    assert raw['price_basis']=='none' and len(raw['indicator_gaps'])==2
    assert fwd['price_basis']=='forward_event_affine' and len(fwd['indicator_gaps'])==1
    earlier=m.chart('600000.SH',as_of=m.rows[40]['date'],price_basis='forward')
    assert earlier['adjustment']['events_count']==0 and earlier['bars'][0]['close']==m.rows[0]['close']
    with pytest.raises(ValueError):m.chart('600000.SH',price_basis='unknown')


def test_waves_have_delayed_confirmation_and_never_use_a_future_bar():
    rows=series(30)
    for i,row in enumerate(rows):
        p=[10,11,12,11,10,9,8,9][i%8]
        row.update(open=p,close=p,high=p+.1,low=p-.1)
    early=candidate_waves(rows[:20]);late=candidate_waves(rows)
    assert early['points'] and late['points']
    positions={r['date']:i for i,r in enumerate(rows)}
    for point in early['points']:
        assert positions[point['confirmed_at']]-positions[point['date']]==2
        assert point['confirmed_at']<=rows[19]['date']
    assert all(p['date']<=rows[-3]['date'] for p in late['points'])


def stock_wave_series():
    rows=series()
    for index,row in enumerate(rows):
        price=[10,11,13,12,10,9,7,8][index%8]
        row.update(open=price,close=price,high=price+.1,low=price-.1)
    return rows


def test_stock_chart_exposes_same_period_waves_and_never_reads_beyond_as_of():
    rows=stock_wave_series();market=ChartMarket(rows);cutoff=rows[29]['date']
    early=market.chart('600000.SH',as_of=cutoff)
    assert early['instrument']['kind']=='stock' and early['waves']['points']
    assert all(point['date']<=cutoff and point['confirmed_at']<=cutoff for point in early['waves']['points'])
    assert {point['label'] for point in early['waves']['points']} <= {'高点','低点'}
    assert all(point['date'] in {bar['date'] for bar in early['bars']} for point in early['waves']['points'])
    saved=deepcopy(early['waves'])
    rows[60]['high']=10000
    assert market.chart('600000.SH',as_of=cutoff)['waves']==saved
    for period in ('W','M'):
        result=market.chart('600000.SH',period=period)
        assert result['period']==period and result['waves'] is not None
        assert all(point['date'] in {bar['date'] for bar in result['bars']} for point in result['waves']['points'])


def test_stock_chart_raw_action_breaks_run_while_verified_forward_uses_display_prices():
    rows=stock_wave_series();market=ChartMarket(rows);action_day=rows[45]['date']
    raw=market.chart('600000.SH');forward=market.chart('600000.SH',price_basis='forward')
    assert raw['price_basis']=='none' and forward['price_basis']=='forward_event_affine'
    assert [row['date'] for row in raw['waves']['continuity_breaks']]==[action_day]
    assert forward['waves']['continuity_breaks']==[]
    assert len({point['segment_id'] for point in raw['waves']['points']})==2
    assert len({point['segment_id'] for point in forward['waves']['points']})==1
    assert all(segment['end_date']<action_day or segment['start_date']>=action_day for segment in raw['waves']['segments'])
    shared=next(point for point in raw['waves']['points'] if point['date']<action_day)
    adjusted=next(point for point in forward['waves']['points'] if point['date']==shared['date'])
    assert adjusted['price']==pytest.approx(shared['price']-.5)


def market_weekdays(start,end):
    first=date.fromisoformat(start);last=date.fromisoformat(end)
    return [(first+timedelta(days=i)).isoformat() for i in range((last-first).days+1) if (first+timedelta(days=i)).weekday()<5]


def quoted_days(calendar,missing=()):
    return [{'date':day,'open':10,'high':11,'low':9,'close':10+i*.01,'volume':100,'amount':1000,'corporate_action':False} for i,day in enumerate(calendar) if day not in missing]


def test_weekly_xinhua_september_gap_preserves_ten_missing_sessions_and_restarts():
    # Real gap shape: 2026-09-04 -> 2026-09-21, weekly quote ends 09-24;
    # 09-25 is an exchange holiday, so it must not be counted as a missing quote.
    calendar=[day for day in market_weekdays('2025-10-20','2026-10-19') if day!='2026-09-25']
    missing=market_weekdays('2026-09-07','2026-09-18')
    rows=quoted_days(calendar,missing)
    weekly=aggregate_weekly(rows,calendar,'2026-10-19');out=enrich_bars(weekly)
    before=next(row for row in out if row['date']=='2026-09-04')
    resumed=next(row for row in out if row['date']=='2026-09-24')
    assert before['macd_dif'] is not None
    assert resumed['weekly_missing_market_dates']==missing
    assert resumed['missing_market_sessions_before']==10
    assert resumed['price_continuity'] is False
    assert resumed['macd_dif'] is None and resumed['kdj_k'] is None
    assert '缺少10个' in resumed['indicator_reset_reason']
    assert resumed['weekly_quote_coverage_complete'] is True


def test_weekly_missing_midweek_quote_is_incomplete_and_cannot_seed_next_full_week():
    calendar=market_weekdays('2025-10-20','2026-10-09')
    rows=quoted_days(calendar,{'2026-09-30'})
    weekly=aggregate_weekly(rows,calendar)
    broken=next(row for row in weekly if row['date']=='2026-10-02')
    assert broken['week_closed'] is True and broken['complete'] is False
    assert broken['weekly_quote_coverage_complete'] is False
    assert broken['weekly_missing_market_sessions']==1
    # Strategy callers can filter incomplete weeks before calculating indicators;
    # the next complete bar still carries the reset diagnosis independently.
    out=enrich_bars([row for row in weekly if row['complete']])
    last=out[-1]
    assert last['date']=='2026-10-09' and last['indicator_warmup_bars']==1
    assert last['macd_dif'] is None and last['kdj_k'] is None
    assert last['indicator_reset_reason']=='前一周报价不完整，周指标重新预热'


def test_weekly_normal_weekend_and_current_week_do_not_trigger_false_gap():
    calendar=market_weekdays('2025-10-20','2026-10-09')
    rows=quoted_days([day for day in calendar if day<='2026-10-07'])
    weekly=aggregate_weekly(rows,calendar,'2026-10-07');out=enrich_bars(weekly)
    assert all(row['weekly_missing_market_sessions']==0 for row in out)
    assert all(row['weekly_quote_coverage_complete'] for row in out)
    assert out[-1]['complete'] is False and out[-1]['indicator_reset_reason'] is None
    assert out[-1]['macd_dif'] is not None
    without_calendar=enrich_bars(aggregate_weekly(rows,None))
    assert all(row['weekly_quote_coverage_complete'] is None for row in without_calendar)
    assert all(row['indicator_reset_reason'] is None for row in without_calendar)


def test_weekly_chart_reports_same_gap_even_with_explicit_forward_basis():
    calendar=[day for day in market_weekdays('2025-10-20','2026-10-19') if day!='2026-09-25']
    missing=market_weekdays('2026-09-07','2026-09-18')
    rows=quoted_days(calendar,missing)
    class MissingQuoteMarket(ChartMarket):
        def calendar(self):return calendar
        def actions(self,*args,**kwargs):return []
    result=MissingQuoteMarket(rows).chart('600825.SH',period='W',as_of='2026-10-19',price_basis='forward')
    resumed=next(row for row in result['bars'] if row['date']=='2026-09-24')
    assert result['price_basis']=='forward_event_affine'
    assert resumed['missing_market_sessions_before']==10 and resumed['macd_dif'] is None
    assert any('缺少10个' in row['reason'] for row in result['indicator_gaps'])
