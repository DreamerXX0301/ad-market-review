"""Rank prices keep verified source dates and every input to action validation."""
import json

import pandas as pd
import pytest

from app.analytics.market import MarketData
from app.core import json_clean
from test_market_compact_reads import retained_market


@pytest.mark.parametrize('start,end', [(None,None),('2026-01-05','2026-01-05'),
    ('2026-01-06',None),(None,'2026-01-06'),('2026-01-07','2026-01-05')])
def test_return_projection_keeps_ohlc_duplicates_gaps_nulls_and_order(retained_market,monkeypatch,start,end):
    root,days,symbols=retained_market;path=root/'published/fixture/daily.parquet'
    frame=pd.read_parquet(path)
    duplicate=frame.iloc[:1].copy();duplicate['high']=None
    null=frame.iloc[:1].copy();null['instrument_id']=symbols[1];null['date']=None
    frame=frame.loc[~((frame.instrument_id==symbols[0])&(frame.date==days[1]))]
    pd.concat([frame,duplicate,null]).to_parquet(path,index=False)
    market=MarketData(root);requested=[symbols[1],symbols[0],symbols[1],'999999.SH']
    fields=('instrument_id','date','open','high','low','close')
    raw=market.history_for_instruments(requested,start=start,end=end)
    expected={s:[{k:r[k] for k in fields} for r in rows] for s,rows in raw.items()}
    cold=market.return_prices_for_instruments(requested,start=start,end=end)
    assert market._frame is None
    market._daily_frame()
    with monkeypatch.context() as patch:
        native=pd.DataFrame.to_dict
        def projected(frame,*args,**kwargs):
            assert tuple(frame.columns)==fields
            return native(frame,*args,**kwargs)
        patch.setattr(pd.DataFrame,'to_dict',projected)
        warm=market.return_prices_for_instruments(requested,start=start,end=end)
    assert json_clean(cold)==json_clean(warm)==json_clean(expected)
    assert list(cold)==list(warm)==list(expected)
    market.close()


def test_return_reader_bound_and_source_change_invalidation(retained_market):
    root,days,symbols=retained_market;market=MarketData(root);market._daily_frame()
    with pytest.raises(ValueError,match='256'):
        market.return_prices_for_instruments([f'{600000+i:06}.SH' for i in range(257)])
    assert market.return_prices_for_instruments([])=={}
    path=root/'published/fixture/daily.parquet';frame=pd.read_parquet(path)
    frame['high']+=.25;frame.to_parquet(path,index=False)
    rows=market.return_prices_for_instruments([symbols[0]],start=days[-1],end=days[-1])
    assert rows[symbols[0]][0]['high']==13.25 and market._frame is None
    rows[symbols[0]][0]['high']=-100
    assert market.return_prices_for_instruments([symbols[0]],start=days[-1],end=days[-1])[symbols[0]][0]['high']==13.25
    market.close()


def test_gainers_projected_prices_preserve_action_validation_and_full_result(monkeypatch):
    from app.services.market_service import gainers_rows
    from test_overview_gainers_scope import ranked_market
    market,reference,symbols=ranked_market();day=market.day
    baseline=market.days[market.days.index(day)-30]
    action_day=market.days[market.days.index(day)-2]
    action={'date':action_day,'dividend_per_share':.1,'per_share_bonus':0,
            'allotment_ratio':0,'allotment_price':0}
    monkeypatch.setattr(market,'actions',lambda symbol,*_: [action] if symbol==symbols[0] else [])
    expected=gainers_rows(market,reference,day,market.panel(day))
    calls=[]
    def prices(batch,start,end):
        calls.append((start,end,len(batch)))
        return {s:[{k:r[k] for k in ('instrument_id','date','open','high','low','close')}
                   for r in rows] for s,rows in market.history_for_instruments(batch,start,end).items()}
    monkeypatch.setattr(market,'return_prices_for_instruments',prices,raising=False)
    assert gainers_rows(market,reference,day,market.panel(day))==expected
    assert calls and all(start==baseline and end==day and n<=256 for start,end,n in calls)


@pytest.fixture
def suspension_archive(tmp_path, monkeypatch):
    from app.services import market_suspensions as suspensions

    monkeypatch.setattr(suspensions, 'work_dir', lambda: tmp_path)
    path = tmp_path / 'market-suspensions' / 'confirmed.json'
    path.parent.mkdir()

    def write(symbol, periods):
        items = [{'instrument_id': symbol, 'start_date': start, 'end_date': end,
                  'state': 'suspended', 'full_session': True, 'source': 'exchange',
                  'source_url': 'https://www.sse.com.cn/fixture'} for start, end in periods]
        path.write_text(json.dumps({'version': suspensions.VERSION, 'items': items}))

    return write


@pytest.mark.parametrize('endpoint', ['baseline', 'observation', 'both'])
def test_gainers_ranks_suspended_endpoint_using_dated_actual_trade(endpoint, suspension_archive):
    from app.services.market_service import gainers_rows
    from test_gainers_limit_history import rank_market
    from test_topic_member_metrics import A, B, MetricMarket, close_at

    market = rank_market(MetricMarket())
    day = market.day
    baseline_index = market.days.index(day) - 30
    baseline = market.days[baseline_index]
    start_quote, end_quote = baseline, day
    periods = []
    if endpoint in ('baseline', 'both'):
        periods.append((market.days[baseline_index - 1], baseline))
        start_quote = market.days[baseline_index - 2]
    if endpoint in ('observation', 'both'):
        periods.append((market.days[-4], day))
        end_quote = market.days[-5]
    suspension_archive(A, periods)
    market.data[A] = [row for row in market.data[A] if not any(
        start <= row['date'] <= end for start, end in periods)]
    # This distinct security has an unexplained missing endpoint.
    market.data[B] = [row for row in market.data[B] if row['date'] != day]
    rows, summary = gainers_rows(market, {}, day, market.panel(day))
    by_code = {row['instrument_id']: row for row in rows}
    observed = by_code[A]
    assert observed['rank'] is not None and observed['return_30d_quality'] == 'verified_suspension_endpoints'
    assert (observed['return_30d_start'], observed['return_30d_end']) == (baseline, day)
    assert (observed['return_30d_start_quote_date'], observed['return_30d_end_quote_date']) == (start_quote, end_quote)
    assert observed['return_30d'] == pytest.approx((close_at(market, A, end_quote) / close_at(market, A, start_quote) - 1) * 100)
    if endpoint in ('observation', 'both'):
        assert observed['change_pct'] is None and observed['return_30d_observation_suspended'] is True
        assert 'close' not in observed and day in observed['return_30d_missing_dates']
    assert by_code[B]['rank'] is None and by_code[B]['return_30d'] is None
    assert by_code[B].get('return_30d_observation_suspended') is None
    assert summary['covered_stocks'] == sum(row['rank'] is not None for row in rows)
    assert market.history_requests[-1] == {
        'symbols': {A}, 'start': baseline if endpoint == 'observation' else market.days[0], 'end': day}


@pytest.mark.parametrize('case', ['unverified_gap', 'positive_volume_conflict', 'unverified_action'])
def test_gainers_halt_proof_cannot_bypass_missing_or_conflicting_trade(case, suspension_archive):
    from app.services.market_service import gainers_rows
    from test_gainers_limit_history import rank_market
    from test_topic_member_metrics import A, MetricMarket, action

    market = rank_market(MetricMarket())
    day, prior_day = market.day, market.days[-4]
    suspension_archive(A, [(day, day)])
    if case != 'positive_volume_conflict':
        market.data[A] = [row for row in market.data[A] if row['date'] != day]
    if case == 'unverified_gap':
        market.data[A] = [row for row in market.data[A] if row['date'] != prior_day]
    if case == 'unverified_action':
        market.events[A] = [action(day)]
    rows, _ = gainers_rows(market, {}, day, market.panel(day))
    observed = next(row for row in rows if row['instrument_id'] == A)
    assert observed['rank'] is None and observed['return_30d'] is None
    assert observed['return_30d_quality'] == 'unknown'
    assert 'return_30d_end_quote_date' not in observed
    if case == 'positive_volume_conflict':
        assert 'return_30d_observation_suspended' not in observed
