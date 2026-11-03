"""Adding Beijing changes both totals and the comparison, never breadth scope."""
import pandas as pd
from app.analytics.market import MarketData
from app.services import market_service
from app.services.market_amount import amount_change_metadata


def test_three_exchange_turnover_history_excludes_nonstocks_and_caches(tmp_path,monkeypatch):
    market=MarketData(tmp_path)
    frame=pd.DataFrame([{'date':day,'instrument_id':symbol,'amount':amount}
        for day,values in [('2026-10-19',(100,200,30,9000)),('2026-10-20',(120,180,50,9999))]
        for symbol,amount in zip(('600001.SH','000001.SZ','920001.BJ','000001.SH'),values)])
    master={symbol:{'kind':'index' if symbol=='000001.SH' else 'stock'} for symbol in frame.instrument_id.unique()}
    monkeypatch.setattr(market,'_load',lambda:{})
    monkeypatch.setattr(market,'_reference',lambda:{'master':master})
    calls=[]
    monkeypatch.setattr(market,'_daily_frame',lambda:calls.append(1) or frame)
    history=market.turnover_history('2026-10-20')
    assert [row['amount'] for row in history]==[330,350]
    assert all(row['scope']=='沪深京已留存A股股票成交额' for row in history)
    result=amount_change_metadata(350,'2026-10-20',['2026-10-19','2026-10-20'],history)
    assert result['amount_change']==20
    assert market.turnover_history('2026-10-19')==history[:1]
    assert len(calls)==1


def test_overview_total_reconciles_three_exchanges(monkeypatch):
    class Market:
        def status(self):return {'snapshot_id':'fixture'}
        def calendar(self):return ['2026-10-19','2026-10-20']
        def turnover_history(self,day):return [{'date':d,'amount':v,'scope':'沪深京','source':'fixture'} for d,v in zip(self.calendar(),(330,350))]
        def indices(self,day):return []
    market=Market();day='2026-10-20'
    rows=[{'instrument_id':symbol,'amount':amount} for symbol,amount in (('600001.SH',120),('000001.SZ',180),('920001.BJ',50))]
    monkeypatch.setattr(market_service,'panel',lambda date, **_:(market,{'snapshot_id':'fixture','quality':'partial'},day,{},rows))
    monkeypatch.setattr(market_service,'cached_gainers',lambda *args:([],{}))
    monkeypatch.setattr(market_service,'_breadth',lambda *args:{'scope':'unchanged'})
    monkeypatch.setattr(market_service,'data_coverage',lambda *args:{})
    result=market_service.overview(day)
    assert result['total_amount']==350
    assert result['sh_amount']==120 and result['sz_amount']==180 and result['bj_amount']==50
    assert result['shsz_total_amount']==300
    assert result['amount_change']==20
    assert result['breadth']['scope']=='unchanged'
