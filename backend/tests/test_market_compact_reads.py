"""Cold stock reads stay bounded; compact whole-market reads preserve evidence."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil

import pandas as pd
import duckdb
import pytest

from app.analytics.market import MarketData
from app.services.sync_service import midnight_ms


@pytest.fixture
def retained_market(tmp_path):
    days = ['2026-01-05', '2026-01-06', '2026-01-07']
    symbols = ['000001.SZ', '600000.SH', '688001.SH']
    folder = tmp_path/'published'/'fixture'; folder.mkdir(parents=True)
    rows = [{'instrument_id': symbol, 'date': day, 'open': 10., 'high': 11., 'low': 9.,
             'close': 10.+i, 'volume': 100., 'amount': 1000., 'source': 'fixture', 'price_basis': 'none'}
            for symbol in symbols for i, day in enumerate(days)]
    # Keep valid OHLC while changing closes.
    for row in rows: row['high'] = row['close'] + 1
    pd.DataFrame(rows).to_parquet(folder/'daily.parquet', index=False)
    pd.DataFrame([{'thscode': '600000.SH', 'ex_date_ms': midnight_ms(days[1])},
                  {'thscode': '600000.SH', 'ex_date_ms': midnight_ms(days[1])}]).to_parquet(folder/'corporate-actions.parquet', index=False)
    reference = {'calendar': days, 'master': {s: {'kind': 'stock', 'name': s} for s in symbols},
                 'pool_history': {days[0]: {'limitup': [{'thscode': '600000.SH'}], 'coverage': {'limitup': {'complete': True}}},
                                  days[1]: {'limitup': [{'thscode': '000001.SZ'}], 'coverage': {'limitup': {'complete': False}}}},
                 'pool_trade_date': days[2], 'limitup': [], 'pool_coverage': {'limitup': {'complete': True}}}
    (folder/'reference.json').write_text(json.dumps(reference))
    head = {'snapshot_id': 'fixture', 'data_begin': days[0], 'data_end': days[-1]}
    (folder/'manifest.json').write_text(json.dumps({'summary': head}))
    (tmp_path/'published'/'market-head.json').write_text(json.dumps(head))
    return tmp_path, days, symbols


def test_cold_bars_are_bounded_and_match_cached_rows_including_evidence(retained_market, monkeypatch):
    root, days, symbols = retained_market
    market = MarketData(root)
    with monkeypatch.context() as patch:
        patch.setattr(market, '_daily_frame', lambda: pytest.fail('a stock chart must not load all securities'))
        cold = {s: market.bars(s) for s in symbols}
        assert market.bars(symbols[0], days[0]) == cold[symbols[0]][:1]
        assert market.bars('999999.SH') == []
        assert market._frame is None
    market._daily_frame()
    assert {s: market.bars(s) for s in symbols} == cold
    assert [r['limit_up'] for r in cold['600000.SH']] == [True, None, False]
    assert [r['limit_up'] for r in cold['000001.SZ']] == [False, True, False]
    assert [r['corporate_action'] for r in cold['600000.SH']] == [False, True, False]
    assert len(cold['600000.SH']) == 3  # Duplicate evidence cannot duplicate quotes.
    assert all(r['corporate_action_coverage_known'] is True for r in cold['600000.SH'])
    cold['600000.SH'][0]['close'] = -1
    assert market.bars('600000.SH')[0]['close'] == 10.


def test_compact_frame_keeps_date_filters_and_aggregations_compatible(retained_market):
    root, days, symbols = retained_market
    market = MarketData(root)
    frame = market._daily_frame()
    assert all('pyarrow' in str(frame[field].dtype) for field in ('instrument_id', 'date', 'limit_up'))
    assert frame.close.dtype == 'float64'
    # Arbitrary calendar bounds (including nontrading dates) keep string-date semantics.
    assert sum(len(v) for v in market.history_panel('2026-01-10', lookback=2).values()) == 6
    assert len(market.panel(days[-1])) == 3
    assert [r['amount'] for r in market.turnover_history()] == [3000.] * 3
    breadth = market.breadth_history()
    assert [r['up'] for r in breadth] == [0, 2, 3]  # Action day is excluded for 600000.SH.
    assert market._daily_frame() is frame


def test_fixed_generation_small_stock_cache_reuses_quotes_without_sharing_mutable_rows(retained_market,monkeypatch):
    root,days,symbols=retained_market;market=MarketData(root);original=market._read_daily_frame;calls=[]
    def read(head,symbol=None,as_of=None):
        calls.append((symbol,as_of));return original(head,symbol,as_of)
    monkeypatch.setattr(market,'_read_daily_frame',read)
    for period in ('D','W','M'):market.chart(symbols[0],period,days[-1])
    assert calls==[(symbols[0],days[-1])]
    rows=market.bars(symbols[0],days[-1]);rows[0]['close']=-100
    assert market.bars(symbols[0],days[-1])[0]['close']==10.
    for number in range(20):market.bars(f'{number+100000:06}.SH',days[-1])
    assert len(market._symbol_bars)==16 and market._frame is None


def test_scalar_reader_serializes_threads_and_releases_on_generation_switch(retained_market):
    root,days,symbols=retained_market;market=MarketData(root)
    expected={symbol:market.bars(symbol) for symbol in symbols}
    reader=market._daily_reader;spill=Path(market._daily_reader_spill.name)
    assert reader.execute("SELECT current_setting('enable_external_file_cache')").fetchone()==(False,)
    assert reader.execute("SELECT current_setting('parquet_metadata_cache')").fetchone()==(True,)
    requests=symbols*8
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(market.bars,requests))
    assert results==[expected[symbol] for symbol in requests]
    assert market._daily_reader is reader and market._frame is None
    next_folder=root/'published'/'next-fixture';shutil.copytree(root/'published'/'fixture',next_folder)
    frame=pd.read_parquet(next_folder/'daily.parquet');frame['close']+=.25
    frame.to_parquet(next_folder/'daily.parquet',index=False)
    head={'snapshot_id':'next-fixture','data_begin':days[0],'data_end':days[-1]}
    (root/'published'/'market-head.json').write_text(json.dumps(head))
    assert market.bars(symbols[0])[0]['close']==10.25
    assert not spill.exists() and market._daily_reader is not reader
    with pytest.raises(Exception,match='closed'):reader.execute('SELECT 1')
    new_reader=market._daily_reader;new_spill=Path(market._daily_reader_spill.name)
    market.close()
    assert market._daily_reader is None and not new_spill.exists()
    with pytest.raises(Exception,match='closed'):new_reader.execute('SELECT 1')


@pytest.mark.parametrize('partitioned',[False,True])
@pytest.mark.parametrize('day_index',[0,1,2])
def test_raw_batch_history_matches_native_parquet_before_and_after_frame(retained_market,monkeypatch,partitioned,day_index):
    """Reuse only requested cached rows, preserving the raw source projection."""
    import app.analytics.market as module
    from app.analytics.market_storage import write_partitions
    root,old_days,symbols=retained_market;folder=root/'published'/'fixture'
    days=['2025-12-31','2026-01-05','2026-02-02'];path=folder/'daily.parquet'
    frame=pd.read_parquet(path);frame['date']=frame.date.map(dict(zip(old_days,days)))
    # Additional nullable source columns must stay raw, including their order.
    frame['source_note']=[None,'','原始来源']*3
    frame['verified_source']=pd.array([True,None,False]*3,dtype='boolean')
    frame['source_revision']=pd.array([1,None,2]*3,dtype='Int32')
    frame.to_parquet(path,index=False)
    requested=[symbols[2],symbols[0],symbols[2],'999999.SH','000001.SH']
    selected_day=days[day_index]
    con=duckdb.connect(':memory:')
    try:
        native=con.execute('SELECT * FROM read_parquet(?) WHERE instrument_id IN (SELECT unnest(?)) AND date>=? AND date<=? ORDER BY instrument_id,date',[str(path),requested,selected_day,selected_day]).fetchdf().to_dict('records')
        if partitioned:
            con.read_parquet(str(path)).create_view('prices')
            files,_=write_partitions(con,'prices','daily.parquet',root/'published',folder,'fixture')
            path.unlink()
    finally:con.close()
    expected={symbol:[] for symbol in dict.fromkeys(requested)}
    for row in native:expected[row['instrument_id']].append(row)
    market=MarketData(root);resolutions=[];resolve=module.parquet_input
    def recorded(path,**scope):
        value=resolve(path,**scope);resolutions.append((scope,value));return value
    monkeypatch.setattr(module,'parquet_input',recorded)
    with monkeypatch.context() as patch:
        patch.setattr(market,'_daily_frame',lambda:pytest.fail('bounded history must not build a full frame'))
        assert market.history_for_instruments(requested,start=selected_day,end=selected_day)==expected
    assert market._frame is None and resolutions[-1][0]=={'start':selected_day,'end':selected_day}
    if partitioned:assert len(resolutions[-1][1])==1  # Requested month only, not all three months.
    cached=market._daily_frame()
    assert 'limit_up' in cached.columns and 'corporate_action' in cached.columns
    assert market._daily_columns==tuple(frame.columns)
    before=len(resolutions)
    with monkeypatch.context() as patch:
        patch.setattr(module.duckdb,'connect',lambda *args,**kwargs:pytest.fail('existing frame must not open another reader'))
        actual=market.history_for_instruments(requested,start=selected_day,end=selected_day)
        assert actual==expected
        assert list(actual)==list(expected)
        assert all(tuple(row)==tuple(frame.columns) for rows in actual.values() for row in rows)
        assert market.history_for_instruments(requested,start=days[-1],end=days[0])=={symbol:[] for symbol in expected}
    assert len(resolutions)==before+2  # Check source signatures, never scan Parquet rows.
    actual[symbols[0]][0]['close']=-1
    assert market.history_for_instruments([symbols[0]],start=days[-1])[symbols[0]][0]['close']>0
    market.close()


def test_cached_raw_history_reloads_changed_legacy_input_without_stale_rows(retained_market):
    root,days,symbols=retained_market;market=MarketData(root)
    market._daily_frame();assert market._daily_columns and market._frame_source_stamp
    path=root/'published'/'fixture'/'daily.parquet'
    frame=pd.read_parquet(path);frame['close']+=.25;frame.to_parquet(path,index=False)
    result=market.history_for_instruments([symbols[0]],start=days[-1],end=days[-1])
    assert result[symbols[0]][0]['close']==12.25
    assert market._frame is None and market._frame_source_stamp is None


@pytest.mark.parametrize('start,end',[(None,None),('2026-01-05','2026-01-05'),('2026-01-06','2026-01-06'),('2026-01-04','2026-01-06'),('2026-01-06',None),(None,'2026-01-06'),('2026-01-07','2026-01-05')])
def test_cached_batch_date_clipping_keeps_duplicate_boundaries_gaps_and_nulls(retained_market,start,end):
    root,days,symbols=retained_market;path=root/'published'/'fixture'/'daily.parquet'
    frame=pd.read_parquet(path)
    # One security has duplicate exact endpoints and a missing middle quote;
    # another has a legacy NULL date, which cannot satisfy a date comparison.
    duplicate=frame.loc[(frame.instrument_id==symbols[0])&(frame.date==days[0])]
    frame=frame.loc[~((frame.instrument_id==symbols[0])&(frame.date==days[1]))]
    null=frame.iloc[:1].copy();null['instrument_id']=symbols[1];null['date']=None
    pd.concat([frame,duplicate,null]).to_parquet(path,index=False)
    market=MarketData(root);requested=[symbols[1],symbols[0],'999999.SH']
    expected=market.history_for_instruments(requested,start=start,end=end)
    market._daily_frame()
    actual=market.history_for_instruments(requested,start=start,end=end)
    assert actual==expected
    market.close()


def test_cached_raw_null_inference_is_preserved_across_internal_batches(retained_market):
    root,days,_=retained_market;path=root/'published'/'fixture'/'daily.parquet'
    base=pd.read_parquet(path).iloc[0].to_dict();symbols=[f'{600001+number:06}.SH' for number in range(270)]
    rows=[{**base,'instrument_id':symbol,'note':None if number<256 else 'later batch'} for number,symbol in enumerate(symbols)]
    pd.DataFrame(rows).to_parquet(path,index=False)
    market=MarketData(root);expected=market.history_for_instruments(symbols,start=days[0],end=days[0])
    market._daily_frame();actual=market.history_for_instruments(symbols,start=days[0],end=days[0])
    assert actual==expected
    market.close()
