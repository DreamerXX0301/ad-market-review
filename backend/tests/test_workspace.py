"""Small controlled fixtures exercise persisted research workflows; no credentials/network."""
import datetime as dt
import json
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from app import core
from app.domain.conditions import validate,evaluate,metrics_for
from app.services import workspace_common,topics,strategies,tracking
from app.routers.workspace import router

class FixtureMarket:
    def __init__(self):
        self.days=[];d=dt.date(2026,1,5)
        while len(self.days)<130:
            if d.weekday()<5:self.days.append(d.isoformat())
            d+=dt.timedelta(days=1)
        self.entry=self.days[70];self.action_dates={};self.data={}
        for symbol,direction in [('600001.SH',1),('000001.SZ',-1)]:
            self.data[symbol]=[{'date':day,'open':100+i*.3*direction,'high':102+i*.3*direction,'low':99+i*.3*direction,'close':101+i*.3*direction,'volume':400 if i==70 and direction==1 else 100,'amount':10000,'corporate_action':False,'source':'fixture','price_basis':'none'} for i,day in enumerate(self.days)]
    def calendar(self):return self.days
    def instruments(self,query='',limit=10000):return [{'instrument_id':s,'code':s[:6],'name':'合成测试'+s,'kind':'stock'} for s in self.data]
    def panel(self,as_of):
        from app.analytics.indicators import enrich_bars
        return [dict(self.instruments()[i],**enrich_bars(self.bars(s,as_of),self.days)[-1]) for i,s in enumerate(self.data) if self.bars(s,as_of)[-1]['date']==as_of]
    def history_panel(self,as_of=None,lookback=None):return {s:self.bars(s,as_of)[-lookback:] if lookback else self.bars(s,as_of) for s in self.data}
    def bars(self,instrument_id,as_of=None):return [dict(r) for r in self.data[instrument_id] if as_of is None or r['date']<=as_of]
    def corporate_actions_between(self,ident,start,end):return [{'date':x} for x in self.action_dates.get(ident,[]) if start<x<=end]
    def topics(self):return [{'topic_id':'fixture-topic','name':'合成题材','members_complete':True,'instrument_ids':['600001.SH']}]
    def topic_members(self,topic_id):return ['600001.SH'] if topic_id=='fixture-topic' else []

@pytest.fixture
def ws(tmp_path,monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path))
    for key in ('SHIXU_SQLITE_PATH','SHIXU_PUBLISHED_DIR','SHIXU_RAW_DIR','SHIXU_WORK_DIR'):monkeypatch.delenv(key,raising=False)
    core.ensure_ready();market=FixtureMarket()
    for module in (core,workspace_common,topics,strategies,tracking):monkeypatch.setattr(module,'get_market',lambda:market)
    with core.connect() as c:
        now=core.now_ms();h='a'*64
        c.execute("INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,parameters_hash,status,created_at_ms) VALUES('fixture-job','fixture-request','fixture-logical','initialise','{}',?,'succeeded',?)",(h,now))
        for sid,module in [('cal','calendar'),('bars','analysis_bars'),('raw','daily_raw')]:
            c.execute('''INSERT INTO dataset_snapshot(snapshot_id,module,scope_key,job_id,data_begin,data_end,knowledge_cutoff_ms,price_basis,algorithm_version,lineage_json,manifest_path,manifest_sha256,state,quality,created_at_ms) VALUES(?,?,?,'fixture-job',?,?,?,'none','fixture','{}',?,?,'staged','ready',?)''',(sid,module,'all',market.days[0],market.days[-1],now,sid+'/manifest.json',h,now))
            c.execute('INSERT INTO snapshot_artifact VALUES(?,?,?,?,?,?)',(sid,sid+'/data.parquet',module,h,100,100))
            c.execute('INSERT INTO snapshot_coverage(snapshot_id,feature,scope_json,coverage_state,observed_rows,continuous_begin,continuous_end) VALUES(?,?,?,\'ready\',100,?,?)',(sid,module,'{}',market.days[0],market.days[-1]))
            c.execute("UPDATE dataset_snapshot SET state='validated' WHERE snapshot_id=?",(sid,));c.execute("UPDATE dataset_snapshot SET state='published',published_at_ms=? WHERE snapshot_id=?",(now,sid))
            c.execute('INSERT INTO published_head VALUES(?,?,?,?)',(module,'all',sid,now))
        c.execute('INSERT INTO business_clock VALUES(1,?,?,?)',(market.entry,'cal',now))
    app=FastAPI();app.include_router(router)
    return market,TestClient(app)

def make_strategy(client,definition=None,policy=None):
    payload={'name':'测试策略','period':'D','definition':definition or {'field':'volume_ratio','op':'>=','value':3}}
    if policy is not None:payload['policy']=policy
    response=client.post('/api/v1/strategies',json=payload);assert response.status_code==200,response.text
    return response.json()
def add_one(client,strategy):
    r=client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs',json={});assert r.status_code==200,r.text
    run=r.json();matched=[x for x in run['items'] if x['decision']=='matched'];assert matched
    a=client.post('/api/v1/tracking-rounds',json={'run_id':run['run_id'],'instrument_ids':[matched[0]['instrument_id']]});assert a.status_code==200,a.text
    return run,a.json()['items'][0]
def advance(market,index):
    with core.connect() as c:c.execute('UPDATE business_clock SET business_date=? WHERE singleton=1',(market.days[index],))

def test_three_valued_logic_and_right_field():
    a=validate({'logic':'AND','conditions':[{'field':'close','op':'>','right_field':'ma30'},{'field':'kd_cross','op':'==','value':True}]})
    assert evaluate(a,{'close':10,'ma30':11,'kd_cross':None})['result'] is False
    assert evaluate(a,{'close':12,'ma30':11,'kd_cross':None})['result'] is None
    assert evaluate(validate({'any':a['all']}),{'close':12,'ma30':11,'kd_cross':None})['result'] is True

def test_stock_universe_identity_and_unknown_semantics():
    from app.domain.universe import identity_flags
    assert identity_flags({'instrument_id':'920001.BJ','code':'920001','name':'测试公司'})['board']=='beijing'
    assert identity_flags({'instrument_id':'300001.SZ','name':'测试公司'})['board']=='chinext'
    assert identity_flags({'instrument_id':'688001.SH','name':'测试公司'})['board']=='star'
    assert identity_flags({'instrument_id':'600001.SH','name':'*ST测试'})['is_st'] is True
    assert identity_flags({'instrument_id':'000001.SZ','name':'SST测试'})['is_st'] is True
    assert identity_flags({'instrument_id':'600001.SH','name':'600001.SH'})['is_st'] is None
    assert identity_flags({'instrument_id':'600001.SH'})['is_st'] is None
    rule=validate({'all':[{'field':'board','op':'not_in','value':['star','beijing']},{'field':'is_st','op':'==','value':False}]})
    assert evaluate(rule,identity_flags({'instrument_id':'600001.SH','name':'正常公司'}))['result'] is True
    assert evaluate(rule,identity_flags({'instrument_id':'300001.SZ'}))['result'] is None
    assert evaluate(rule,identity_flags({'instrument_id':'999999.UNKNOWN','name':'正常公司'}))['result'] is None
    with pytest.raises(ValueError):validate({'field':'board','op':'not_in','value':[]})
    with pytest.raises(ValueError):validate({'field':'board','op':'in','value':['main','all']})

def test_strategy_stock_universe_executes_and_freezes_runs(ws,monkeypatch):
    market,client=ws
    template=market.data['600001.SH']
    for symbol in ('300001.SZ','688001.SH','920001.BJ','300002.SZ'):
        market.data[symbol]=[dict(row) for row in template]
    names={'600001.SH':'主板测试','000001.SZ':'*ST测试','300001.SZ':'创业板测试','688001.SH':'科创板测试','920001.BJ':'北交所测试','300002.SZ':''}
    monkeypatch.setattr(market,'instruments',lambda query='',limit=10000:[{'instrument_id':symbol,'code':symbol[:6],'name':names[symbol],'kind':'stock'} for symbol in market.data])
    definition={'all':[{'field':'board','op':'not_in','value':['star','beijing']},{'field':'is_st','op':'==','value':False},{'field':'close','op':'>','value':0}]}
    strategy=make_strategy(client,definition)
    run=client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs',json={}).json()
    assert {row['instrument_id'] for row in run['items'] if row['decision']=='matched'}=={'600001.SH','300001.SZ'}
    assert {row['instrument_id'] for row in run['items'] if row['decision']=='unknown'}=={'300002.SZ'}
    matched=next(row for row in run['items'] if row['instrument_id']=='300001.SZ')
    assert matched['metrics']['board']=='chinext' and matched['metrics']['identity_scope']=='current'
    updated=client.patch('/api/v1/strategies/'+strategy['strategy_id'],json={'definition':{'field':'board','op':'in','value':['beijing']}}).json()
    assert updated['version']==2
    later=client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs',json={}).json()
    assert {row['instrument_id'] for row in later['items'] if row['decision']=='matched'}=={'920001.BJ'}
    original=client.get('/api/v1/strategy-runs/'+run['run_id']).json()
    assert original['strategy_version']==1 and original['matched_count']==2

def test_topic_manual_override_state_and_undo(ws):
    _,client=ws
    client.post('/api/v1/topics/initialize',json={})
    topic=client.get('/api/v1/topics').json()['items'][0]
    path='/api/v1/topics/'+topic['topic_id']
    before=client.get(path).json()
    assert before['members'][0]['manual_override'] is None and before['members'][0]['provider_member'] is True
    included=client.post(path+'/members',json={'instrument_id':'000001.SZ','action':'include'}).json()
    manual=next(row for row in included['members'] if row['instrument_id']=='000001.SZ')
    assert manual['manual_override']=='include' and manual['provider_member'] is False
    undone=client.post(path+'/members',json={'instrument_id':'000001.SZ','action':'reset_to_provider'}).json()
    assert len(undone['members'])==1
    excluded=client.post(path+'/members',json={'instrument_id':'600001.SH','action':'exclude'}).json()
    assert excluded['member_count']==0 and excluded['excluded_members'][0]['manual_override']=='exclude'
    restored=client.post(path+'/members',json={'instrument_id':'600001.SH','action':'reset_to_provider'}).json()
    assert restored['member_count']==1 and restored['excluded_members']==[] and restored['members'][0]['manual_override'] is None

def test_whitelist_rejects_code(ws):
    _,client=ws
    r=client.post('/api/v1/strategies',json={'name':'不安全','definition':{'field':'__import__','op':'==','value':True}})
    assert r.status_code==422
    assert client.get('/api/v1/strategies').json()['total']==0

def test_topic_crud_manual_override_and_archive(ws):
    m,c=ws
    r=c.post('/api/v1/topics',json={'name':'测试题材','instrument_ids':['600001.SH']});assert r.status_code==200,r.text
    t=r.json();assert t['member_count']==1
    r=c.post('/api/v1/topics/'+t['topic_id']+'/members',json={'instrument_id':'600001.SH','action':'exclude'});assert r.json()['member_count']==0
    r=c.patch('/api/v1/topics/'+t['topic_id'],json={'name':'更名','archived':True});assert r.json()['revision']==2
    assert c.get('/api/v1/topics').json()['total']==0
    assert c.get('/api/v1/topics?include_archived=true').json()['total']==1
    assert c.get('/api/v1/topics/'+t['topic_id']+'/history').json()['total']==4

def test_bootstrap_idempotent_without_fake_candidates(ws):
    m,c=ws
    assert c.post('/api/v1/strategies/bootstrap').status_code==200
    assert c.post('/api/v1/strategies/bootstrap').status_code==200
    assert c.get('/api/v1/strategies').json()['total']==3
    assert c.get('/api/v1/topics').json()['items'][0]['member_count']==1
    assert c.get('/api/v1/strategy-runs').json()['total']==0

def test_real_computation_persists_candidates_and_request_idempotency(ws):
    m,c=ws;s=make_strategy(c)
    path='/api/v1/strategies/'+s['strategy_id']+'/runs'
    r=c.post(path,json={'request_key':'click1'});assert r.status_code==200,r.text
    data=r.json();assert data['matched_count']==1;assert data['unmatched_count']==1
    assert data['items'][0]['checks']
    again=c.post(path,json={'request_key':'click1'}).json();assert again['run_id']==data['run_id']
    assert c.get('/api/v1/strategy-runs').json()['total']==1
    with core.connect() as db:assert db.execute("SELECT count(*) FROM retention_pin WHERE owner_kind='selection'").fetchone()[0]==3

def test_unknown_not_false_and_no_fake_hit(ws):
    m,c=ws;s=make_strategy(c,{'field':'ma250','op':'>','value':0})
    r=c.post('/api/v1/strategies/'+s['strategy_id']+'/runs',json={}).json()
    assert r['matched_count']==0 and r['unknown_count']==2

def test_strategy_versions_preserve_old_run(ws):
    m,c=ws;s=make_strategy(c);run,tracked=add_one(c,s)
    new=c.patch('/api/v1/strategies/'+s['strategy_id'],json={'definition':{'field':'change_pct','op':'>','value':99}}).json()
    assert new['version']==2
    old=c.get('/api/v1/strategy-runs/'+run['run_id']).json();assert old['strategy_version']==1 and old['matched_count']==1
    assert c.get('/api/v1/tracking-rounds/'+tracked['round_id']).json()['entry_strategy_version']==1

def test_duplicate_add_and_ignore_reentry_history(ws):
    m,c=ws;s=make_strategy(c);run,t=add_one(c,s)
    r=c.post('/api/v1/tracking-rounds',json={'run_id':run['run_id'],'instrument_ids':[t['instrument_id']]}).json();assert r['added_count']==0
    assert c.post('/api/v1/tracking-rounds/'+t['round_id']+'/remove',json={}).json()['status']=='manual_removed'
    r=c.post('/api/v1/tracking-rounds',json={'run_id':run['run_id'],'instrument_ids':[t['instrument_id']]}).json();assert r['added_count']==0 and '忽略' in r['skipped'][0]['reason']
    assert c.delete('/api/v1/strategies/'+s['strategy_id']+'/ignores/'+t['instrument_id']).status_code==200
    new=c.post('/api/v1/tracking-rounds',json={'run_id':run['run_id'],'instrument_ids':[t['instrument_id']]}).json();assert new['added_count']==1
    assert new['items'][0]['round_id']!=t['round_id']
    assert c.get('/api/v1/tracking-rounds?status=all').json()['total']==2

def test_30_exchange_sessions_not_natural_days_and_idempotent_review(ws):
    m,c=ws;s=make_strategy(c);run,t=add_one(c,s);advance(m,100)
    r=c.post('/api/v1/tracking-rounds/review',json={});assert r.status_code==200,r.text
    assert r.json()['reviewed']==31 and r.json()['expired']==1
    round_=c.get('/api/v1/tracking-rounds/'+t['round_id']).json();assert round_['status']=='expired'
    assert round_['end_business_date']==m.days[100]
    assert (dt.date.fromisoformat(m.days[100])-dt.date.fromisoformat(m.entry)).days>30
    again=c.post('/api/v1/tracking-rounds/review',json={}).json();assert again['reviewed']==0
    reviews=[x for x in round_['events'] if x['event_type']=='review'];assert len(reviews)==31

def test_alert_and_end_are_independent_with_default_day_zero_off(ws):
    m,c=ws;p={'alerts':[{'field':'close_above_ma5','op':'==','value':True}],'exits':[]}
    s=make_strategy(c,policy=p);run,t=add_one(c,s)
    assert c.post('/api/v1/tracking-rounds/review',json={}).json()['alerts']==0
    advance(m,71);r=c.post('/api/v1/tracking-rounds/review',json={});assert r.status_code==200,r.text
    assert r.json()['alerts']==1 and r.json()['ended']==0
    assert c.get('/api/v1/tracking-rounds?triggered_only=true').json()['total']==1
    assert c.get('/api/v1/tracking-events?event_type=alert').json()['total']==1
    assert c.get('/api/v1/tracking-rounds/'+t['round_id']).json()['status']=='tracking'

def test_policy_migration_keeps_entry_price_and_uses_effective_day(ws):
    m,c=ws;s=make_strategy(c);run,t=add_one(c,s);advance(m,72)
    p={'duration_sessions':5,'alerts':[{'field':'close_above_ma5','op':'==','value':True}],'exits':[]}
    r=c.patch('/api/v1/tracking-rounds/'+t['round_id']+'/policy',json={'policy':p});assert r.status_code==200,r.text
    detail=r.json();assert detail['entry_price']==t['entry_price'];assert detail['entry_business_date']==m.entry
    reviewed=c.post('/api/v1/tracking-rounds/review',json={}).json();assert reviewed['alerts']==1
    assert c.get('/api/v1/tracking-events?event_type=alert').json()['items'][0]['business_date']==m.days[72]

def test_break_entry_price_real_exit_and_company_action_unknown(ws):
    m,c=ws;s=make_strategy(c,policy={'exits':[{'field':'close_below_entry','op':'==','value':True}]});run,t=add_one(c,s)
    m.data['600001.SH'][71]['close']=90;advance(m,71)
    r=c.post('/api/v1/tracking-rounds/review',json={});assert r.status_code==200,r.text
    assert r.json()['ended']==1
    e=c.get('/api/v1/tracking-events?event_type=condition_end').json()['items'][0]['evidence'];assert e['reference_entry_date']==m.entry
    assert e['normalized_observed_close_e8']<e['reference_entry_e8']

def test_action_prevents_unverified_entry_exit(ws):
    m,c=ws;s=make_strategy(c,policy={'exits':[{'field':'close_below_entry','op':'==','value':True}]});run,t=add_one(c,s)
    m.data['600001.SH'][71]['close']=90;m.action_dates['600001.SH']=[m.days[71]];m.data['600001.SH'][71]['corporate_action']=True;advance(m,71)
    r=c.post('/api/v1/tracking-rounds/review',json={});assert r.status_code==200,r.text
    assert r.json()['ended']==0 and r.json()['unknown']>=1
    assert c.get('/api/v1/tracking-rounds/'+t['round_id']).json()['tracking_change_pct'] is None

def test_missing_day_review_saved_as_unknown(ws):
    m,c=ws;s=make_strategy(c,policy={'alerts':[{'field':'macd_cross','op':'==','value':True}]});run,t=add_one(c,s)
    m.data['600001.SH']=[r for r in m.data['600001.SH'] if r['date']!=m.days[71]];advance(m,71)
    r=c.post('/api/v1/tracking-rounds/review',json={});assert r.status_code==200,r.text
    assert r.json()['unknown']>=1
    reviews=c.get('/api/v1/tracking-events?event_type=review').json()['items'];assert any(x['business_date']==m.days[71] and x['outcome']=='unknown' for x in reviews)

def test_old_day_run_cannot_join_current_tracking(ws):
    m,c=ws;s=make_strategy(c);run=c.post('/api/v1/strategies/'+s['strategy_id']+'/runs',json={}).json();advance(m,71)
    r=c.post('/api/v1/tracking-rounds',json={'run_id':run['run_id'],'instrument_ids':['600001.SH']});assert r.status_code==409

def test_topic_filters_select_manual_members(ws):
    m,c=ws;topic=c.post('/api/v1/topics',json={'name':'自定义','instrument_ids':['000001.SZ']}).json()
    s=make_strategy(c,{'field':'topic_ids','op':'contains','value':topic['topic_id']})
    r=c.post('/api/v1/strategies/'+s['strategy_id']+'/runs',json={}).json();assert r['matched_count']==1
    assert next(x for x in r['items'] if x['decision']=='matched')['instrument_id']=='000001.SZ'

def test_corporate_action_restarts_indicator_warmup():
    m=FixtureMarket();bars=m.bars('600001.SH',m.entry);bars[-3]['corporate_action']=True
    result=metrics_for(bars,m.entry,m.days)
    assert result['ma30'] is None and result['macd_cross'] is None

def test_policy_replay_uses_old_effective_duration(ws):
    m,c=ws;s=make_strategy(c,policy={'duration_sessions':5});run,t=add_one(c,s);advance(m,80)
    changed=c.patch('/api/v1/tracking-rounds/'+t['round_id']+'/policy',json={'policy':{'duration_sessions':30}})
    assert changed.status_code==200,changed.text
    result=c.post('/api/v1/tracking-rounds/review',json={});assert result.status_code==200,result.text
    detail=c.get('/api/v1/tracking-rounds/'+t['round_id']).json()
    assert detail['status']=='expired' and detail['end_business_date']==m.days[75]
    event=next(e for e in detail['events'] if e['event_type']=='expire')
    assert event['evidence']['policy_version']==1

def test_review_request_idempotency_saved_result(ws):
    m,c=ws;s=make_strategy(c);run,t=add_one(c,s);advance(m,71)
    first=c.post('/api/v1/tracking-rounds/review',json={'request_key':'same-review'}).json()
    again=c.post('/api/v1/tracking-rounds/review',json={'request_key':'same-review'}).json()
    assert first==again and first['reviewed']==2

@pytest.mark.parametrize('scenario',['daily','gap','corporate_action','weekly'])
def test_chart_strategy_shared_indicator_equality(scenario):
    from app.analytics.indicators import enrich_bars,aggregate_weekly
    m=FixtureMarket();bars=m.bars('600001.SH',m.entry);period='D'
    if scenario=='gap':bars.pop(-4)
    if scenario=='corporate_action':bars[-4]['corporate_action']=True
    if scenario=='weekly':
        period='W';expected_rows=[b for b in aggregate_weekly(bars,m.days) if b['complete']]
        expected=enrich_bars(expected_rows,None)[-1]
    else:expected=enrich_bars(bars,m.days)[-1]
    actual=metrics_for(bars,m.entry,m.days,period)
    for field in ['ma5','ma10','ma20','ma30','ma60','ma250','macd_dif','macd_dea','macd_hist','kdj_k','kdj_d','kdj_j','volume_ratio','macd_cross','kd_cross']:
        if isinstance(expected[field],float):assert actual[field]==pytest.approx(expected[field],abs=1e-10),field
        else:assert actual[field]==expected[field],field

def test_round_policy_does_not_collide_with_next_strategy_policy(ws):
    m,c=ws;s=make_strategy(c);run,t=add_one(c,s)
    assert c.patch('/api/v1/tracking-rounds/'+t['round_id']+'/policy',json={'policy':{'duration_sessions':12}}).status_code==200
    r=c.patch('/api/v1/strategies/'+s['strategy_id'],json={'policy':{'duration_sessions':20}})
    assert r.status_code==200,r.text
    assert r.json()['current_policy_version']==3

def test_new_evaluator_creates_new_immutable_strategy_version(ws,monkeypatch):
    m,c=ws;s=make_strategy(c);run,t=add_one(c,s)
    monkeypatch.setattr(strategies,'ENGINE_VERSION','fixture-next-engine')
    r=c.post('/api/v1/strategies/'+s['strategy_id']+'/runs',json={})
    assert r.status_code==200,r.text
    assert r.json()['strategy_version']==2
    assert c.get('/api/v1/strategy-runs/'+run['run_id']).json()['strategy_version']==1

def test_ended_round_valuation_stops_at_end_day(ws):
    m,c=ws;s=make_strategy(c,policy={'duration_sessions':2});run,t=add_one(c,s);advance(m,74)
    assert c.post('/api/v1/tracking-rounds/review',json={}).status_code==200
    first=c.get('/api/v1/tracking-rounds/'+t['round_id']).json();assert first['status']=='expired'
    advance(m,80);later=c.get('/api/v1/tracking-rounds/'+t['round_id']).json()
    assert later['trading_age']==2 and later['latest_price']==first['latest_price']
    assert later['valuation_date']==m.days[72]

def test_invalid_boolean_policy_is_not_coerced(ws):
    m,c=ws
    r=c.post('/api/v1/strategies',json={'name':'条件','definition':{'field':'close','op':'>','value':0},'policy':{'allow_entry_day_alerts':'false'}})
    assert r.status_code==422

def test_weekly_signal_friday_but_entry_price_monday(ws):
    m,c=ws
    assert dt.date.fromisoformat(m.entry).weekday()==0
    s=c.post('/api/v1/strategies',json={'name':'周观察','period':'W','definition':{'field':'close','op':'>','value':0}}).json()
    run=c.post('/api/v1/strategies/'+s['strategy_id']+'/runs',json={}).json()
    candidate=next(x for x in run['items'] if x['instrument_id']=='600001.SH')
    assert candidate['metrics']['signal_date']==m.days[69]
    assert candidate['metrics']['signal_close']==m.data['600001.SH'][69]['close']
    assert candidate['close_price']==m.data['600001.SH'][70]['close']
    joined=c.post('/api/v1/tracking-rounds',json={'run_id':run['run_id'],'instrument_ids':['600001.SH']}).json()
    assert joined['items'][0]['entry_business_date']==m.entry
    assert joined['items'][0]['entry_price']==m.data['600001.SH'][70]['close']
    detail=c.get('/api/v1/tracking-rounds/'+joined['items'][0]['round_id']).json()
    assert detail['entry_basis']['signal_date']==m.days[69]
    assert detail['entry_basis']['entry_price_date']==m.entry

def test_weekly_current_day_missing_price_cannot_enter(ws):
    m,c=ws;m.data['600001.SH']=[r for r in m.data['600001.SH'] if r['date']!=m.entry]
    s=c.post('/api/v1/strategies',json={'name':'周观察','period':'W','definition':{'field':'close','op':'>','value':0}}).json()
    run=c.post('/api/v1/strategies/'+s['strategy_id']+'/runs',json={}).json()
    row=next(x for x in run['items'] if x['instrument_id']=='600001.SH');assert row['decision']=='matched' and row['close_price'] is None
    result=c.post('/api/v1/tracking-rounds',json={'run_id':run['run_id'],'instrument_ids':['600001.SH']}).json()
    assert result['added_count']==0 and result['skipped']

def test_synchronous_strategy_task_has_expiring_lease_then_releases(ws,monkeypatch):
    m,c=ws;original=m.panel;seen=[]
    def check_panel(as_of):
        with core.connect() as db:
            task=db.execute("SELECT lease_owner,lease_expires_at_ms FROM job WHERE status='running' AND kind='selection'").fetchone()
            seen.append(task is not None and task['lease_owner'].startswith('api:') and task['lease_expires_at_ms']>core.now_ms())
        return original(as_of)
    monkeypatch.setattr(m,'panel',check_panel)
    s=make_strategy(c);result=c.post('/api/v1/strategies/'+s['strategy_id']+'/runs',json={});assert result.status_code==200
    assert seen and all(seen)
    with core.connect() as db:
        assert db.execute("SELECT count(*) FROM job WHERE kind='selection' AND (lease_owner IS NOT NULL OR lease_expires_at_ms IS NOT NULL)").fetchone()[0]==0

def test_cancelled_selection_never_commits_candidates_or_overwrites_cancelled(ws,monkeypatch):
    m,c=ws;original=m.panel
    def cancel_during_panel(as_of):
        with core.connect() as db:db.execute("UPDATE job SET status='cancelled' WHERE kind='selection' AND status='running'")
        return original(as_of)
    monkeypatch.setattr(m,'panel',cancel_during_panel)
    s=make_strategy(c);r=c.post('/api/v1/strategies/'+s['strategy_id']+'/runs',json={})
    assert r.status_code==409
    assert c.get('/api/v1/strategy-runs').json()['total']==0
    with core.connect() as db:
        assert db.execute("SELECT status FROM job WHERE kind='selection'").fetchone()[0]=='cancelled'

def test_cancelled_review_final_gate_preserves_cancelled(ws,monkeypatch):
    m,c=ws;s=make_strategy(c);run,t=add_one(c,s)
    def cancel_before_final(payload,job_id=None,expected_inputs=None):
        with core.connect() as db:db.execute("UPDATE job SET status='cancelled' WHERE job_id=?",(job_id,))
        return {'reviewed':0}
    monkeypatch.setattr(tracking,'_review_tracking_impl',cancel_before_final)
    r=c.post('/api/v1/tracking-rounds/review',json={});assert r.status_code==409
    with core.connect() as db:
        assert db.execute("SELECT status FROM job WHERE kind='tracking_review'").fetchone()[0]=='cancelled'

def test_partial_topic_membership_absence_is_unknown_not_false():
    node=validate({'field':'topic_ids','op':'contains','value':'incomplete'})
    assert evaluate(node,{'topic_ids':[],'_complete_topic_ids':[]})['result'] is None
    assert evaluate(node,{'topic_ids':['incomplete'],'_complete_topic_ids':[]})['result'] is True
    assert evaluate(node,{'topic_ids':[],'_complete_topic_ids':['incomplete']})['result'] is False

def test_topic_initialization_keeps_industry_kind_and_does_not_create_strategy(ws,monkeypatch):
    m,c=ws
    monkeypatch.setattr(m,'topics',lambda:[{'topic_id':'fixture-topic','name':'行业样本','kind':'industry','observed_date':m.entry,'members_complete':True,'instrument_ids':['600001.SH']}])
    result=c.post('/api/v1/topics/initialize',json={});assert result.status_code==200,result.text
    items=c.get('/api/v1/topics?kind=industry').json()['items']
    assert len(items)==1 and items[0]['kind']=='industry' and items[0]['source_observed_date']==m.entry
    assert c.get('/api/v1/topics?kind=concept').json()['total']==0
    assert c.get('/api/v1/strategies').json()['total']==0

def test_nonmatch_storage_is_sparse_but_checks_and_dependencies_stay(ws):
    m,c=ws;s=make_strategy(c);result=c.post('/api/v1/strategies/'+s['strategy_id']+'/runs',json={}).json()
    matched=next(x for x in result['items'] if x['decision']=='matched');unmatched=next(x for x in result['items'] if x['decision']=='unmatched')
    assert 'kdj_k' in matched['metrics'] and 'ma250' in matched['metrics']
    assert 'kdj_k' not in unmatched['metrics'] and 'ma250' not in unmatched['metrics']
    assert 'volume_ratio' in unmatched['metrics'] and 'indicator_warmup_bars' in unmatched['metrics']
    assert unmatched['checks']['actual']==unmatched['metrics']['volume_ratio']
    assert unmatched['checks']['result'] is False
    with core.connect() as db:
        manifest=json.loads(db.execute('SELECT universe_definition_json FROM selection_run WHERE run_id=?',(result['run_id'],)).fetchone()[0])
        assert manifest['candidate_storage_contract']=='sparse-nonmatches-v1'

def test_unknown_sparse_storage_retains_unknown_dependency(ws):
    m,c=ws;s=make_strategy(c,{'field':'ma250','op':'>','value':0})
    run=c.post('/api/v1/strategies/'+s['strategy_id']+'/runs',json={}).json()
    assert all(row['decision']=='unknown' and row['metrics']['ma250'] is None and row['checks']['result'] is None for row in run['items'])

def test_incomplete_empty_topic_count_is_unknown_but_verified_empty_is_zero(ws,monkeypatch):
    m,c=ws
    monkeypatch.setattr(m,'topics',lambda:[{'topic_id':'fixture-topic','name':'空或未知','kind':'concept','members_complete':False,'instrument_ids':[]}])
    result=c.post('/api/v1/topics/initialize',json={}).json()['items'][0]
    assert result['member_count'] is None and result['observed_member_count']==0
    monkeypatch.setattr(m,'topics',lambda:[{'topic_id':'fixture-topic','name':'空或未知','kind':'concept','members_complete':True,'instrument_ids':[]}])
    result=c.get('/api/v1/topics').json()['items'][0]
    assert result['member_count']==0 and result['observed_member_count']==0
