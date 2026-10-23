"""Display-only IPO-age lower bounds from a fixed published quote snapshot.

Five distinct, valid traded dates strictly before T prove T is beyond the first
five market sessions. They do not establish the actual listing date and must
not feed stock-universe, backtest, execution or other trading rules.
"""
from datetime import date
from pathlib import Path
import json
import tempfile

import duckdb
from app.analytics.market_storage import descriptor_path, parquet_input

BASIS = 'five-prior-unique-valid-traded-dates'
SCOPE = '仅梯队20%制度展示的年龄下界；不补写上市日期，不用于回测或交易风控'
BATCH_SIZE = 128


def quote_age_evidence(market, status, symbols, calendar, observed):
    symbols=sorted(set(symbols))
    def unknown(symbol,reason):
        return {'instrument_id':symbol,'snapshot_id':status.get('snapshot_id'),'observation_date':observed,
                'state':'unknown','basis':BASIS,'observed_trade_dates':[],
                'prior_trade_dates_observed':None,'minimum_listing_session':None,
                'listing_date_known':False,'scope':SCOPE,'reason':reason}
    result={symbol:unknown(symbol,'未取得固定行情快照内的有效成交日年龄证明') for symbol in symbols}
    if not symbols:return result
    prior=sorted(set(day for day in calendar if isinstance(day,str) and day<observed))
    if observed not in calendar or len(prior)<5:return result
    try:
        if any(date.fromisoformat(day).isoformat()!=day for day in prior):return result
        data_dir=getattr(market,'data_dir',None);snapshot=status.get('snapshot_id')
        if data_dir is None or not isinstance(snapshot,str) or not snapshot:return result
        base=(Path(data_dir).resolve()/'published').resolve()
        folder=(base/snapshot).resolve()
        if not folder.is_relative_to(base) or Path(status.get('path','')).resolve()!=folder:return result
        manifest=json.loads((folder/'manifest.json').read_text())
        if manifest.get('summary',{}).get('snapshot_id')!=snapshot:return result
        raw=descriptor_path(folder,'daily.parquet')
        if not raw.is_file():return result
        with tempfile.TemporaryDirectory(prefix='shixu-ladder-age-') as temporary:
            con=duckdb.connect(':memory:')
            try:
                con.execute("SET memory_limit='256MB'")
                con.execute('SET threads=2')
                con.execute('SET temp_directory=?',[temporary])
                for offset in range(0,len(symbols),BATCH_SIZE):
                    batch=symbols[offset:offset+BATCH_SIZE]
                    rows=con.execute('''WITH per_day AS (
                        SELECT instrument_id,date,count(*) AS row_count,
                            bool_and(coalesce(isfinite(open) AND open>0 AND isfinite(high) AND high>0
                                AND isfinite(low) AND low>0 AND isfinite(close) AND close>0
                                AND isfinite(volume) AND volume>0
                                AND high>=greatest(open,close,low) AND low<=least(open,close,high),FALSE)) AS is_valid
                        FROM read_parquet(?)
                        WHERE instrument_id IN (SELECT unnest(?)) AND date IN (SELECT unnest(?)) AND date<?
                        GROUP BY instrument_id,date
                    ), ranked AS (
                        SELECT instrument_id,date,row_number() OVER(PARTITION BY instrument_id ORDER BY date) AS position
                        FROM per_day WHERE row_count=1 AND is_valid
                    ) SELECT instrument_id,list(date ORDER BY date) AS dates
                        FROM ranked WHERE position<=5 GROUP BY instrument_id''',[parquet_input(raw),batch,prior,observed]).fetchall()
                    for symbol,dates in rows:
                        ready=len(dates)==5
                        result[symbol]={**unknown(symbol,'观察日前未能确认五个不同有效成交日'),
                            'state':'proven' if ready else 'unknown','observed_trade_dates':dates,
                            'prior_trade_dates_observed':len(dates),'minimum_listing_session':6 if ready else None,
                            'first_observed_date':dates[0] if dates else None,
                            'fifth_observed_date':dates[4] if ready else None,
                            'artifact':'daily.parquet','reason':None if ready else '已留存有效成交日不足五个；上市日期仍未知'}
            finally:con.close()
    except (OSError,ValueError,TypeError,duckdb.Error):
        return {symbol:unknown(symbol,'固定行情快照或成交日年龄查询未通过核验') for symbol in symbols}
    return result
