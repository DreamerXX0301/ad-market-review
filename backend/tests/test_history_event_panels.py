"""Compact history matrices preserve the scalar event semantics and seed."""
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import math

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.analytics.market import MarketData
from app.analytics.panel_inputs import PanelInputs
from app.domain.conditions import evaluate
from app.domain.history_events import HistoryContext
from app.services.history_events import contexts_for_batch, prepare_shared
from test_history_events import days, quotes, event


def fixture_market(tmp_path, rows, calendar, actions=True):
    folder=tmp_path/'published'/'history-matrix'
    folder.mkdir(parents=True)
    (folder/'manifest.json').write_text(json.dumps({'summary':{'snapshot_id':'history-matrix','data_end':calendar[-1]}}))
    (folder/'reference.json').write_text(json.dumps({'calendar':calendar}))
    frame=pd.DataFrame(rows).drop(columns=['corporate_action','corporate_action_coverage_known'],errors='ignore')
    pq.write_table(pa.Table.from_pandas(frame,preserve_index=False),folder/'daily.parquet')
    if actions:
        records=[{'thscode':row['instrument_id'],'ex_date_ms':int(datetime.fromisoformat(row['date']).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()*1000)}
                 for row in rows if row.get('corporate_action')]
        table=pa.Table.from_pylist(records,schema=pa.schema([('thscode',pa.string()),('ex_date_ms',pa.int64())]))
        pq.write_table(table,folder/'corporate-actions.parquet')
    return MarketData(tmp_path,'history-matrix')


@pytest.mark.parametrize('field',['volume_ratio','volume_expand','volume_shrink','macd_cross','macd_dead_cross','kd_cross','kd_dead_cross','macd_dif','kdj_j','ma60','bullish_ma_alignment','cross_above_ma20','intraday_high_change_pct'])
def test_compact_daily_fields_match_original_seed_and_scalar_unknowns(tmp_path,field):
    calendar=days(430);rows=quotes(calendar)
    for i,row in enumerate(rows):
        close=25+math.sin(i*.21)*2+i*.01
        row.update(open=close-.1,high=close+.5,low=close-.5,close=close,volume=100+(i%11)*20)
    rows[210]['corporate_action']=True
    rows[360]['volume']=0
    rows.pop(370)
    boolean=field.endswith(('cross','alignment','expand','shrink')) or field.startswith('cross_')
    node=event(1,30,where={'field':field,'op':'==' if boolean else '>', 'value':True if boolean else 0})
    market=fixture_market(tmp_path,rows,calendar)
    shared=prepare_shared(market,['600001.SH'],[node],calendar,calendar[-1])
    compact=contexts_for_batch(market,['600001.SH'],[node],calendar,calendar[-1],shared=shared)['600001.SH']
    scalar=HistoryContext(rows,calendar,calendar[-1],retain_start=calendar[-31])
    assert set(compact.daily)==set(scalar.daily)
    for day in compact.daily:
        expected=scalar.daily[day][field];actual=compact.daily[day][field]
        if isinstance(expected,float):assert actual==pytest.approx(expected,abs=1e-10)
        else:assert actual is expected, (day,field,expected,actual)
    left=evaluate(node,{'observed_date':calendar[-1]},compact.at(calendar[-1]))
    right=evaluate(node,{'observed_date':calendar[-1]},scalar.at(calendar[-1]))
    assert left['result'] is right['result']
    assert left['selected_event']==right['selected_event']
    assert len(compact.daily)<=31 and len(compact.daily[calendar[-1]])<10


@pytest.mark.parametrize('mode',['missing','duplicate','zero','invalid_open','action'])
def test_strict_matrix_price_binding_keeps_intermediate_day_unknown(tmp_path,mode):
    calendar=days(100);rows=quotes(calendar);rows[-31]['volume']=300
    if mode=='missing':rows.pop(-5)
    elif mode=='duplicate':rows.append(dict(rows[-5]))
    elif mode=='zero':rows[-5]['volume']=0
    elif mode=='invalid_open':rows[-5]['open']=0
    else:rows[-5]['corporate_action']=True
    node=event();market=fixture_market(tmp_path,rows,calendar)
    shared=prepare_shared(market,['600001.SH'],[node],calendar,calendar[-1])
    context=contexts_for_batch(market,['600001.SH'],[node],calendar,calendar[-1],shared=shared)['600001.SH']
    result=evaluate(node,{},context.at(calendar[-1]))
    assert result['selected_event']['date']==calendar[-31]
    assert result['result'] is None and result['evidence']['price_comparable'] is False


def test_strict_history_state_is_separate_and_cannot_mutate_current_panel_checkpoints():
    calendar=days(110);rows=quotes(calendar);rows[80]['volume']=0
    frame=pd.DataFrame(rows);inputs=PanelInputs(frame,calendar)
    original=inputs.latest(calendar[-1]);checkpoint_keys=list(inputs._checkpoints)
    strict=inputs.history_view(frame)
    assert strict.values is inputs.values and strict.actions is inputs.actions
    assert strict._checkpoints is not inputs._checkpoints
    assert strict.latest(calendar[-1])['600001.SH']['macd_cross'] is None
    assert list(inputs._checkpoints)==checkpoint_keys
    assert inputs.latest(calendar[-1])==original
    assert strict.MAX_CHECKPOINTS==64 and inputs.MAX_CHECKPOINTS==8


def test_repeated_30_day_query_reuses_historical_seed_and_remains_identical():
    calendar=days(430);rows=quotes(calendar);frame=pd.DataFrame(rows)
    inputs=PanelInputs(frame,calendar).history_view(frame)
    first=[inputs.latest(day,requested_fields={'macd_cross'},arrays=True)['macd_cross'].copy() for day in calendar[-32:]]
    steps=0
    second=[]
    for day in calendar[-32:]:
        second.append(inputs.latest(day,requested_fields={'macd_cross'},arrays=True)['macd_cross'].copy())
        steps+=inputs.last_compute_steps
    assert steps<100, 'warm repeated window must not replay 430-day seed'
    assert all(left.tolist()==right.tolist() for left,right in zip(first,second))
    assert len(inputs._checkpoints)<=64


def test_shared_preparation_uses_one_bulk_interface_and_batches_only_materialization(tmp_path,monkeypatch):
    calendar=days(100);rows=quotes(calendar)
    rows += [{**row,'instrument_id':'600002.SH'} for row in rows]
    market=fixture_market(tmp_path,rows,calendar);node=event(1,30,where={'field':'macd_dif','op':'>','value':0})
    calls=[];method=market.history_condition_metrics
    def read(*args,**kwargs):calls.append((args,kwargs));return method(*args,**kwargs)
    monkeypatch.setattr(market,'history_condition_metrics',read)
    monkeypatch.setattr(market,'history_for_instruments',lambda *a,**k:pytest.fail('prepared path must not reread each batch history'))
    shared=prepare_shared(market,['600001.SH','600002.SH'],[node],calendar,calendar[-1])
    for symbol in ('600001.SH','600002.SH'):
        context=contexts_for_batch(market,[symbol],[node],calendar,calendar[-1],shared=shared)[symbol]
        assert evaluate(node,{},context.at(calendar[-1]))['result'] is True
    assert len(calls)==1 and len(shared['daily'])==32
    assert all(len(values)<10 for values in shared['daily'])


def test_missing_action_file_is_unknown_without_loading_full_history(tmp_path,monkeypatch):
    calendar=days(100);rows=quotes(calendar);market=fixture_market(tmp_path,rows,calendar,actions=False)
    monkeypatch.setattr(market,'_daily_frame',lambda:pytest.fail('unknown actions must not scan ten years'))
    node=event(1,30,where={'field':'macd_cross','op':'==','value':True})
    shared=prepare_shared(market,['600001.SH'],[node],calendar,calendar[-1])
    context=contexts_for_batch(market,['600001.SH'],[node],calendar,calendar[-1],shared=shared)['600001.SH']
    assert shared['unavailable'] is True and context.daily=={}
    assert evaluate(node,{},context.at(calendar[-1]))['result'] is None


def test_past_matrix_replay_ignores_future_action_and_price_changes(tmp_path):
    calendar=days(140);rows=quotes(calendar);day=calendar[109]
    rows[135]['corporate_action']=True
    for row in rows[120:]:
        for field in ('open','high','low','close'):row[field]*=100
    market=fixture_market(tmp_path,rows,calendar)
    node=event(1,30,where={'field':'macd_dif','op':'>','value':0})
    market.history_condition_metrics(['600001.SH'],start=calendar[109],end=calendar[-1],fields={'macd_dif'})
    prepared=prepare_shared(market,['600001.SH'],[node],calendar,day)
    context=contexts_for_batch(market,['600001.SH'],[node],calendar,day,shared=prepared)['600001.SH']
    expected=HistoryContext([row for row in rows if row['date']<=day],calendar,day)
    assert context.daily[day]['macd_dif']==pytest.approx(expected.daily[day]['macd_dif'])
    assert evaluate(node,{},context.at(day))['selected_event']==evaluate(node,{},expected.at(day))['selected_event']
