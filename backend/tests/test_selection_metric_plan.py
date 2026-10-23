"""Selection plans reuse bounded pure proofs without changing stored evidence."""
from copy import deepcopy
from datetime import date

import pytest

from app.analytics import boll_conditions, higher_lows, indicators
from app.domain.conditions import metrics_for, validate
from app.services import strategies, strategy_periods as summaries
from test_boll_signal_integration import breakout_rows, config, leaf as boll_leaf
from test_workspace import ws, make_strategy


@pytest.fixture(autouse=True)
def clean_summaries():
    summaries.clear_metric_cache()
    yield
    summaries.clear_metric_cache()


def leaf(field='close',period=None):
    return {'field':field,'op':'>','value':0,**({'period':period} if period else {})}


@pytest.mark.parametrize('period',['D','W','M'])
@pytest.mark.parametrize('fault',['none','missing','duplicate','zero','unknown_action','action'])
def test_split_proofs_equal_full_calculation_including_unknown_reasons(tmp_path,monkeypatch,period,fault):
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path))
    rows,calendar=breakout_rows(120)
    if fault=='missing':rows.pop(-4)
    elif fault=='duplicate':rows.append(dict(rows[-4]))
    elif fault=='zero':rows[-4]['volume']=0
    elif fault=='unknown_action':rows[-4].pop('corporate_action_coverage_known')
    elif fault=='action':rows[-4]['corporate_action']=True
    day=calendar[-1];args={'instrument_id':'600001.SH','generation':'immutable-one'}
    # Populate ordinary and higher lows first, then add an unrelated BOLL
    # rule. All full fields, blockers and observed dates must remain equal.
    first=summaries.cached_metrics(rows,day,calendar,period,include_higher_lows=True,**args)
    assert first==metrics_for(rows,day,calendar,period,include_higher_lows=True)
    actual=summaries.cached_metrics(rows,day,calendar,period,include_higher_lows=True,boll_configs=[config()],**args)
    expected=metrics_for(rows,day,calendar,period,include_higher_lows=True,boll_configs=[config()])
    assert actual==expected
    request=summaries.metric_request(True,[config()])
    assert summaries.cached_metrics_if_ready(day,period,request=request,**args)==expected


def test_a_then_b_higher_lows_then_c_boll_reuses_ordinary_and_separate_parameters(tmp_path,monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path))
    rows,calendar=breakout_rows();day=calendar[-1]
    calls=[];original=summaries.metrics_for
    def observed(*args,**kwargs):
        calls.append(kwargs)
        return original(*args,**kwargs)
    monkeypatch.setattr(summaries,'metrics_for',observed)
    args={'instrument_id':'600001.SH','generation':'fixed'}
    a=summaries.cached_metrics(rows,day,calendar,'D',include_higher_lows=True,**args)
    b=summaries.cached_metrics([],day,calendar,'D',include_higher_lows=True,**args)
    c=summaries.cached_metrics(rows,day,calendar,'D',boll_configs=[config()],**args)
    wide=summaries.cached_metrics(rows,day,calendar,'D',boll_configs=[config(deviations=3)],**args)
    both=summaries.cached_metrics([],day,calendar,'D',boll_configs=[config(),config(deviations=3)],**args)
    assert a==b and len(calls)==1
    assert 'higher_lows_state' not in c and 'boll_states' not in a
    assert both['boll_states']=={**c['boll_states'],**wide['boll_states']}
    assert len(summaries._cache)==1 and len(summaries._higher_lows_cache)==1 and len(summaries._boll_cache)==2


def test_cache_keys_isolate_generation_root_day_period_and_all_parameter_proofs(tmp_path,monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path/'one'))
    rows,calendar=breakout_rows();day=calendar[-1]
    request=summaries.metric_request(True,[config()])
    args={'instrument_id':'600001.SH','generation':'one','request':request}
    result=summaries.cached_metrics(rows,day,calendar,'D',**args)
    assert summaries.cached_metrics_if_ready(day,'D',**args)==result
    for change in ({'instrument_id':'600002.SH'},{'generation':'two'},{'request':summaries.metric_request(True,[config(deviations=3)])}):
        assert summaries.cached_metrics_if_ready(day,'D',**{**args,**change}) is None
    assert summaries.cached_metrics_if_ready(calendar[-2],'D',**args) is None
    assert summaries.cached_metrics_if_ready(day,'W',**args) is None
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path/'two'))
    assert summaries.cached_metrics_if_ready(day,'D',**args) is None
    assert summaries.cached_metrics(rows,day,calendar,'D',**args)==result


def test_cached_compositions_cannot_be_polluted_by_policy_identity_or_extra_periods(tmp_path,monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path))
    rows,calendar=breakout_rows();day=calendar[-1]
    request=summaries.metric_request(True,[config()])
    args={'instrument_id':'600001.SH','generation':'one','request':request}
    expected=summaries.cached_metrics(rows,day,calendar,'D',**args)
    first=summaries.cached_metrics([],day,calendar,'D',topics=['local-topic'],**args)
    first.update(board='main',_complete_topic_ids=['local-topic'],_period_metrics={'W':{'close':-1}})
    first['higher_lows_state']['recent_lows'].append({'price':-1})
    first['boll_states'].clear();first['topic_ids'].append('injected')
    assert summaries.cached_metrics_if_ready(day,'D',**args)==expected
    assert summaries.cached_metrics([],day,calendar,'D',topics=['other-topic'],**args)['topic_ids']==['other-topic']


def test_all_three_cache_categories_evict_independently_and_recompute_exactly(tmp_path,monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(summaries,'MAX_SUMMARIES',2)
    monkeypatch.setattr(summaries,'MAX_STRUCTURE_SUMMARIES',2)
    rows,calendar=breakout_rows();day=calendar[-1];request=summaries.metric_request(True,[config()])
    for symbol in ('one','two','three'):
        summaries.cached_metrics(rows,day,calendar,'D',instrument_id=symbol,generation='fixed',request=request)
    assert [len(cache) for cache in (summaries._cache,summaries._higher_lows_cache,summaries._boll_cache)]==[2,2,2]
    assert summaries.cached_metrics_if_ready(day,'D',instrument_id='one',generation='fixed',request=request) is None
    assert summaries.cached_metrics(rows,day,calendar,'D',instrument_id='one',generation='fixed',request=request)==metrics_for(rows,day,calendar,include_higher_lows=True,boll_configs=[config()])


def test_compiled_nested_plan_preserves_history_daily_scope_and_redundant_matched_proofs():
    tree=validate({'any':[{'all':[leaf(period='W'),leaf('ma5',period='M')]},
        {'history_event':{'window':{'from':1,'to':10},'match':'any','where':leaf('volume'),
            'then':{'all':[boll_leaf(),{'field':'higher_lows','op':'confirmed_rising_lows','value':8}]}}}]})
    plan=summaries.compile_metric_plan(tree,'M')
    assert plan.periods==('D','M','W') and plan.default=='M'
    assert all(request.include_higher_lows for request in plan.requests.values())
    assert len(plan.requests['D'].boll_configs)==1
    assert not plan.requests['W'].boll_configs and not plan.requests['M'].boll_configs
    assert tree['any'][1]['history_event']['match']=='any'


@pytest.mark.parametrize('period',['D','W','M'])
def test_batch_reads_only_misses_and_preserves_current_day_entry_price(tmp_path,monkeypatch,period):
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path))
    rows,calendar=breakout_rows();day=calendar[-1]
    plan=summaries.compile_metric_plan(leaf(period='W'),period);full=[];short=[]
    class Market:
        def condition_histories(self,symbols,end):full.append(symbols);return {symbol:deepcopy(rows) for symbol in symbols}
        def history_for_instruments(self,symbols,start,end):short.append((symbols,start,end));return {symbol:[dict(rows[-1])] for symbol in symbols}
    market=Market()
    for target in plan.periods:summaries.cached_metrics(rows,day,calendar,target,instrument_id='warm',generation='fixed',request=plan.requests[target])
    histories,ready,daily=strategies.selection_metric_batch(market,['warm','cold'],day,plan,'fixed')
    assert full==[['cold']] and set(histories)=={'cold'}
    assert all(value is not None for value in ready['warm'].values())
    if period in ('W','M'):
        assert short==[(['warm'],day,day)] and daily['warm']==[rows[-1]]
    else:assert not short
    # Compatibility history contexts without a prepared panel still require
    # the full seed, including a previously warmed security.
    strategies.selection_metric_batch(market,['warm'],day,plan,'fixed',require_history=True)
    assert full[-1]==['warm']


def test_batch_without_generation_never_uses_stale_proofs_and_is_bounded():
    rows,calendar=breakout_rows();day=calendar[-1];plan=summaries.compile_metric_plan(leaf())
    class Market:
        def condition_histories(self,symbols,end):return {symbol:deepcopy(rows) for symbol in symbols}
    histories,ready,_=strategies.selection_metric_batch(Market(),['one'],day,plan,None)
    assert histories['one']==rows and ready['one']=={'D':None}
    with pytest.raises(ValueError):strategies.selection_metric_batch(Market(),list(map(str,range(33))),day,plan,'fixed')


@pytest.mark.parametrize('outer',['D','W','M'])
def test_warm_selection_keeps_full_candidates_and_entry_prices_without_full_history(ws,monkeypatch,outer):
    market,client=ws
    for values in market.data.values():
        for row in values:row['corporate_action_coverage_known']=True
    monkeypatch.setattr(market,'status',lambda:{'snapshot_id':'immutable-fixture'},raising=False)
    monkeypatch.setattr(market,'verify_condition_sources',lambda:('verified-source',),raising=False)
    short=[]
    def day_quotes(symbols,start=None,end=None):
        short.append((list(symbols),start,end))
        return {symbol:[row for row in market.bars(symbol,end) if row['date']>=start] for symbol in symbols}
    monkeypatch.setattr(market,'history_for_instruments',day_quotes,raising=False)
    created=client.post('/api/v1/strategies',json={'name':'复用完整证据','period':outer,
        'definition':{'any':[leaf(period='W'),boll_leaf()]}}).json()
    first=client.post('/api/v1/strategies/'+created['strategy_id']+'/runs',json={})
    assert first.status_code==200,first.text
    monkeypatch.setattr(strategies,'selection_history_batch',lambda *a,**k:pytest.fail('warm run must not materialize complete history'))
    second=client.post('/api/v1/strategies/'+created['strategy_id']+'/runs',json={})
    assert second.status_code==200,second.text
    assert first.json()['items']==second.json()['items']
    if outer in ('W','M'):assert all(start==end==market.entry for _,start,end in short)


def test_legacy_adapter_without_verifiable_source_closure_keeps_full_history_reads(ws,monkeypatch):
    market,client=ws
    monkeypatch.setattr(market,'status',lambda:{'snapshot_id':'legacy-fixture'},raising=False)
    original=strategies.selection_history_batch;reads=[]
    def observed(*args,**kwargs):reads.append(args[1]);return original(*args,**kwargs)
    monkeypatch.setattr(strategies,'selection_history_batch',observed)
    strategy=make_strategy(client,boll_leaf())
    for _ in range(2):
        response=client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs',json={})
        assert response.status_code==200,response.text
    assert len(reads)==2


def test_warm_run_rejects_bad_sources_and_source_token_change_recomputes(ws,monkeypatch):
    market,client=ws;token=['original'];reads=[]
    monkeypatch.setattr(market,'status',lambda:{'snapshot_id':'same-generation'},raising=False)
    def verify():
        if token[0]=='damaged':raise ValueError('source SHA mismatch')
        return (token[0],)
    monkeypatch.setattr(market,'verify_condition_sources',verify,raising=False)
    original=strategies.selection_history_batch
    def observed(*args,**kwargs):reads.append(args[1]);return original(*args,**kwargs)
    monkeypatch.setattr(strategies,'selection_history_batch',observed)
    strategy=make_strategy(client,{'any':[leaf(),boll_leaf()]})
    first=client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs',json={}).json()
    token[0]='damaged'
    with pytest.raises(ValueError,match='source SHA mismatch'):
        client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs',json={})
    assert len(reads)==1
    token[0]='new-source-context'
    next(row for row in market.data['600001.SH'] if row['date']==market.entry)['close']+=.25
    second=client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs',json={}).json()
    assert len(reads)==2
    before=next(row for row in first['items'] if row['instrument_id']=='600001.SH')
    after=next(row for row in second['items'] if row['instrument_id']=='600001.SH')
    assert before['close_price']!=after['close_price']


def legacy_weekly(rows,calendar=None,as_of=None):
    """Independent pre-optimization calendar scans, kept only as test oracle."""
    groups={}
    for row in rows:groups.setdefault(date.fromisoformat(row['date']).isocalendar()[:2],[]).append(row)
    calendar=sorted(set(calendar or []));horizon=as_of or max((row['date'] for row in rows),default=None)
    start=min((row['date'] for row in rows),default=None);expected={};result=[]
    for day in calendar:
        if start and start<=day<=horizon:expected.setdefault(date.fromisoformat(day).isocalendar()[:2],[]).append(day)
    for key,bs in sorted(groups.items()):
        bs=sorted(bs,key=lambda row:row['date']);last=bs[-1]['date']
        following=next((day for day in calendar if day>last),None)
        observed={row['date'] for row in bs};within=[day for day in expected.get(key,[]) if day not in observed]
        between=[day for day in calendar if result and result[-1]['date']<day<bs[0]['date']]
        missing=sorted(set(within+between));coverage=not within if expected.get(key) else None
        prior=bool(result and result[-1]['weekly_quote_coverage_complete'] is False)
        reason=f'周线区间缺少{len(missing)}个市场交易日报价，尚未区分停牌与缺数' if missing else '前一周报价不完整，周指标重新预热' if prior else None
        closed=(following is not None and date.fromisoformat(following).isocalendar()[:2]!=key) or date.fromisoformat(last).weekday()==4
        result.append({**bs[-1],'date':last,'open':bs[0]['open'],'high':max(row['high'] for row in bs),
            'low':min(row['low'] for row in bs),'close':bs[-1]['close'],'volume':sum(row['volume'] for row in bs),
            'amount':sum(row['amount'] for row in bs),'corporate_action':any(row.get('corporate_action') for row in bs),
            'complete':closed and coverage is not False,'week_closed':closed,'weekly_quote_coverage_complete':coverage,
            'weekly_missing_market_sessions':len(missing),'weekly_missing_market_dates':missing,'weekly_continuity_reset_reason':reason})
    return result


@pytest.mark.parametrize('case',['unsorted','duplicates','empty','no_calendar','gap','partial','future'])
def test_bisected_week_calendar_preserves_old_coverage_and_anomalous_inputs(case):
    rows,calendar=breakout_rows(80);as_of=None
    if case=='unsorted':rows.reverse();calendar.reverse()
    elif case=='duplicates':rows.append(dict(rows[5]));calendar+=calendar[2:7]
    elif case=='empty':rows=[]
    elif case=='no_calendar':calendar=None
    elif case=='gap':rows=rows[:20]+rows[35:]
    elif case=='partial':rows=rows[:-3]
    elif case=='future':as_of=calendar[25]
    assert indicators.aggregate_weekly(rows,calendar,as_of)==legacy_weekly(rows,calendar,as_of)
