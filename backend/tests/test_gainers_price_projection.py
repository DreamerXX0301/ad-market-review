"""Return-only reads preserve every price used by action validation."""
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
