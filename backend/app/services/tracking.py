"""tracking application workflows; persistence and side effects live here."""
from __future__ import annotations
import hashlib,json,sqlite3
from decimal import Decimal,ROUND_HALF_UP
from fastapi import HTTPException
from app.core import connect,get_market,now_ms,new_id
from app.domain.conditions import validate,evaluate,metrics_for,names,specs,topic_dependencies,finite,past_limit_up_metrics
from app.services.strategy_limits import LIMIT_FIELDS, DAILY_LIMIT_FIELDS, COUNT_FIELD, LimitConditionEvidence, quote_index
from app.services.workspace_common import *
from app.services.topics import topic_map,complete_topic_ids
from app.domain.tracking import trading_age,condition_decisions
from app.services.strategies import policy_view, strategy_view, save_policy, policy_normalized, apply_tracking_policy
from app.services.entry_high import FIELD as ENTRY_HIGH_FIELD, load_entry_highs, entry_high_for, condition_metrics as entry_high_metrics, load_action_evidence
from app.services.entry_high import LOW_FIELD as ENTRY_LOW_FIELD, load_entry_lows, entry_low_for, low_condition_metrics as entry_low_metrics
from app.domain.history_events import HistoryContext, nodes as history_nodes, retain_start as history_retain_start
from app.services.tracking_quotes import tracking_quotes
from app.services.strategy_periods import add_period_metrics


def add_tracking(payload):
    rid=payload.get('run_id');ids=payload.get('instrument_ids',[])
    if not isinstance(ids,list) or not ids or len(ids)>500:fail('请选择1至500只已命中股票')
    # Resolve exact original daily highs before acquiring the write transaction.
    # The second phase rechecks the business date/run/version before insertion.
    with connect() as c:
        prepared_run=one(c,'SELECT * FROM selection_run WHERE run_id=?',(rid,),'策略运行不存在')
        prepared_snapshot=one(c,'SELECT * FROM dataset_snapshot WHERE snapshot_id=?',(prepared_run['primary_snapshot_id'],))
        prepared=[]
        for ident in dict.fromkeys(str(value).upper() for value in ids):
            candidate=c.execute('SELECT close_price_e8,decision FROM selection_candidate WHERE run_id=? AND instrument_id=?',(rid,ident)).fetchone()
            if candidate and candidate['decision']=='matched' and candidate['close_price_e8']:
                prepared.append({'entry_snapshot_id':prepared_run['primary_snapshot_id'],'instrument_id':ident,'entry_business_date':prepared_run['business_date'],'entry_price_e8':candidate['close_price_e8'],'entry_basis_json':js({'price_basis':prepared_snapshot['price_basis']})})
    entry_highs=load_entry_highs(prepared)
    entry_lows=load_entry_lows(prepared)
    added=[];skipped=[]
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        day=current(c);run=one(c,'SELECT * FROM selection_run WHERE run_id=?',(rid,),'策略运行不存在')
        if run['business_date']!=day:fail('只能从当前业务日的运行结果入池，请重新运行策略。',409)
        strategy=strategy_view(c,run['strategy_id'])
        if strategy['version']!=run['strategy_version']:fail('策略已修改，请重新运行后加入跟踪。',409)
        snapshot=one(c,'SELECT * FROM dataset_snapshot WHERE snapshot_id=?',(run['primary_snapshot_id'],))
        for ident in dict.fromkeys(str(x).upper() for x in ids):
            found=c.execute('SELECT * FROM selection_candidate WHERE run_id=? AND instrument_id=?',(rid,ident)).fetchone()
            if not found or found['decision']!='matched' or not found['close_price_e8']:
                skipped.append({'instrument_id':ident,'reason':'未命中或没有可用入池价'});continue
            if c.execute("SELECT 1 FROM tracking_round WHERE strategy_id=? AND instrument_id=? AND status='tracking'",(run['strategy_id'],ident)).fetchone():
                skipped.append({'instrument_id':ident,'reason':'此策略已在跟踪，不重复入池'});continue
            if c.execute('SELECT 1 FROM tracking_ignore WHERE strategy_id=? AND instrument_id=? AND active=1',(run['strategy_id'],ident)).fetchone():
                skipped.append({'instrument_id':ident,'reason':'已手动忽略，再次入池前先解除忽略'});continue
            tid=new_id('tracking');basis={'comparison_basis_id':snapshot['snapshot_id']+':'+snapshot['price_basis']+':'+str(snapshot['adjustment_anchor']),'price_basis':snapshot['price_basis'],'adjustment_anchor':snapshot['adjustment_anchor'],'algorithm_version':snapshot['algorithm_version'],'entry_snapshot_id':snapshot['snapshot_id'],'source_lineage':obj(snapshot['lineage_json'],{}),'entry_topics':obj(found['topic_snapshot_json'],[]),'entry_checks':obj(found['checks_json'],{}),'entry_complete_topic_ids':obj(found['metrics_json'],{}).get('_complete_topic_ids',[]),'entry_price_date':day,'signal_date':obj(found['metrics_json'],{}).get('signal_date',day),'signal_price':obj(found['metrics_json'],{}).get('signal_close',money(found['close_price_e8'])),'signal_period':obj(found['metrics_json'],{}).get('indicator_period','D')}
            basis['entry_high']=entry_high_for({'entry_snapshot_id':snapshot['snapshot_id'],'instrument_id':ident,'entry_business_date':day,'entry_price_e8':found['close_price_e8']},entry_highs)
            basis['entry_low']=entry_low_for({'entry_snapshot_id':snapshot['snapshot_id'],'instrument_id':ident,'entry_business_date':day,'entry_price_e8':found['close_price_e8']},entry_lows)
            insert(c,'tracking_round',dict(round_id=tid,strategy_id=run['strategy_id'],entry_strategy_version=run['strategy_version'],origin_run_id=rid,instrument_id=ident,entry_business_date=day,entry_price_e8=found['close_price_e8'],entry_snapshot_id=snapshot['snapshot_id'],entry_policy_version=strategy['current_policy_version'],current_policy_version=strategy['current_policy_version'],entry_basis_json=js(basis),created_at_ms=now_ms()))
            insert(c,'tracking_event',dict(event_id=new_id('event'),event_key=tid+':'+day+':entry',round_id=tid,event_type='entry',business_date=day,data_snapshot_id=snapshot['snapshot_id'],data_state='ready',outcome='not_applicable',trading_age=0,evidence_json=js({'entry_price_e8':found['close_price_e8'],'policy_version':strategy['current_policy_version'],'basis':basis}),recorded_at_ms=now_ms()))
            for snapid in set(obj(run['input_snapshots_json'],{}).values()):pin(c,snapid,'tracking',tid)
            added.append({'round_id':tid,'instrument_id':ident,'status':'tracking','entry_business_date':day,'entry_price':money(found['close_price_e8'])})
    return {'items':added,'added_count':len(added),'skipped':skipped}

def _policy_at(c,r,day):
    application=c.execute('SELECT policy_version FROM tracking_policy_application WHERE round_id=? AND effective_business_date<=? ORDER BY effective_business_date DESC,recorded_at_ms DESC,rowid DESC LIMIT 1',(r['round_id'],day)).fetchone()
    version=application[0] if application else r['entry_policy_version']
    return policy_view(one(c,'SELECT * FROM tracking_policy_version WHERE strategy_id=? AND policy_version=?',(r['strategy_id'],version)))

def _age(calendar,entry,day):
    return trading_age(calendar,entry,day)

def _entry_comparison(r,day,price,market=None):
    basis=obj(r['entry_basis_json'],{})
    if price is None:return None,'unknown','缺少当日收盘价'
    if basis.get('price_basis')!='none':return None,'unknown','当前实现没有经过验收的跨复权锚转换'
    market=market or get_market()
    if day==r['entry_business_date']:return price,'not_required',None
    if hasattr(market,'corporate_actions_between'):
        actions=market.corporate_actions_between(r['instrument_id'],r['entry_business_date'],day)
        if actions is None:return None,'unknown','期间公司行动覆盖未知'
        if actions:return None,'unknown','期间存在公司行动，未经验证的复权转换不触发结束'
        return price,'not_required',None
    return None,'unknown','未验证期间公司行动，不能直接跨日比较入池基准'

def _event_view(r):
    return {k:v for k,v in r.items() if k!='evidence_json'}|{'evidence':obj(r.get('evidence_json'),{})}

def _round_view(c,r,master,calendar,bars,day,with_events=False,current_memberships=None,entry_high=None,entry_low=None,quote=None,market=None):
    valuation_date=r['end_business_date'] if r['status']!='tracking' else day
    ms=quote if quote is not None else metrics_for(bars,valuation_date,calendar);age=_age(calendar,r['entry_business_date'],valuation_date);p=_policy_at(c,r,valuation_date)
    saved_review=c.execute("SELECT evidence_json FROM tracking_event WHERE round_id=? AND event_type='review' AND business_date=? ORDER BY recorded_at_ms DESC,rowid DESC LIMIT 1",(r['round_id'],valuation_date)).fetchone()
    if r['status']!='tracking' and saved_review:ms=obj(saved_review[0],{}).get('metrics',ms)
    price=ms.get('close');normalized,state,reason=_entry_comparison(r,valuation_date,price,market)
    latest=c.execute('SELECT * FROM tracking_event WHERE round_id=? ORDER BY business_date DESC,recorded_at_ms DESC,rowid DESC LIMIT 1',(r['round_id'],)).fetchone()
    alerts=rows(c,"SELECT * FROM tracking_event WHERE round_id=? AND event_type='alert' AND business_date=? ORDER BY recorded_at_ms DESC",(r['round_id'],day))
    latest_review=c.execute("SELECT * FROM tracking_event WHERE round_id=? AND event_type='review' ORDER BY business_date DESC,recorded_at_ms DESC,rowid DESC LIMIT 1",(r['round_id'],)).fetchone()
    topics=obj(latest_review['evidence_json'],{}).get('topics') if latest_review else None
    if topics is None:topics=obj(r['entry_basis_json'],{}).get('entry_topics',[])
    item={**{k:v for k,v in r.items() if k not in ('entry_basis_json','entry_price_e8')},**code_info(r['instrument_id'],master),'valuation_date':valuation_date,'entry_price':money(r['entry_price_e8']),'entry_basis':obj(r['entry_basis_json'],{}),'latest_price':price,'today_change_pct':ms.get('change_pct'),'tracking_change_pct':(normalized/money(r['entry_price_e8'])-1)*100 if normalized is not None else None,'comparison_state':state,'comparison_reason':reason,'trading_age':age,'remaining_sessions':max(0,p['duration_sessions']-age) if age is not None else None,'policy':p,'topics':topics,'latest_event':_event_view(dict(latest)) if latest else None,'alerts':[_event_view(a) for a in alerts]}
    if entry_high is not None:item.update(entry_high=entry_high.get('raw_high'),entry_high_date=entry_high.get('date'),entry_high_state=entry_high.get('state'),entry_high_reason=entry_high.get('reason'),entry_high_basis=entry_high)
    if entry_low is not None:item.update(entry_low=entry_low.get('raw_low'),entry_low_date=entry_low.get('date'),entry_low_state=entry_low.get('state'),entry_low_reason=entry_low.get('reason'),entry_low_basis=entry_low)
    # Current membership is a browsing filter only; frozen entry/review evidence
    # continues to drive historical decisions and is never overwritten by this map.
    if current_memberships is None:current_memberships=topic_map(c,day)
    item.update(current_topics=current_memberships.get(r['instrument_id'],[]),current_topics_observed_date=day,topics_snapshot=obj(r['entry_basis_json'],{}).get('entry_topics',[]),topics_snapshot_date=r['entry_business_date'],reviewed_topics_snapshot_date=latest_review['business_date'] if latest_review else r['entry_business_date'])
    reviewed_policy=obj(latest_review['evidence_json'],{}).get('policy_version') if latest_review and latest_review['business_date']==valuation_date else None
    item['policy_review_pending']=r['status']=='tracking' and reviewed_policy!=p['policy_version']
    item['duration_reached']=r['status']=='tracking' and age is not None and age>0 and age>=p['duration_sessions']
    if with_events:item['events']=[_event_view(x) for x in rows(c,'SELECT * FROM tracking_event WHERE round_id=? ORDER BY business_date,recorded_at_ms,rowid',(r['round_id'],))]
    return item

def list_tracking(strategy_id=None,status='tracking',triggered_only=False):
    with connect() as c:
        day=current(c);calendar=get_market().calendar();master=identities(day);sql='SELECT r.*,s.name strategy_name FROM tracking_round r JOIN strategy s USING(strategy_id) WHERE 1=1';params=[]
        if strategy_id:sql+=' AND r.strategy_id=?';params.append(strategy_id)
        if status and status!='all':
            if status not in ('tracking','expired','condition_ended','manual_removed'):fail('不支持的跟踪状态')
            sql+=' AND r.status=?';params.append(status)
        sql+=' ORDER BY r.created_at_ms DESC';data=rows(c,sql,params);market=get_market();items=[];current_memberships=topic_map(c,day)
    entry_highs=load_entry_highs(data)
    entry_lows=load_entry_lows(data)
    quotes=tracking_quotes(market,data,calendar,day)
    with connect() as c:
        for r in data:
            valuation_date=r['end_business_date'] if r['status']!='tracking' else day
            item=_round_view(c,r,master,calendar,[],day,current_memberships=current_memberships,entry_high=entry_high_for(r,entry_highs),entry_low=entry_low_for(r,entry_lows),quote=quotes[(r['instrument_id'],valuation_date)],market=market)
            if not triggered_only or item['alerts']:items.append(item)
    return {'items':items,'total':len(items),'business_date':day,'current_topics_scope':'当前业务日有效的本地来源成员与人工修改；不是历史归属，未完整分类的缺席不能判为不属于。'}

def get_tracking(tid):
    with connect() as c:
        day=current(c);r=one(c,'SELECT r.*,s.name strategy_name FROM tracking_round r JOIN strategy s USING(strategy_id) WHERE round_id=?',(tid,),'跟踪轮次不存在')
    entry_highs=load_entry_highs([r]);entry_lows=load_entry_lows([r]);market=get_market();master=identities(day);calendar=market.calendar();quotes=tracking_quotes(market,[r],calendar,day)
    valuation_date=r['end_business_date'] if r['status']!='tracking' else day
    with connect() as c:return _round_view(c,r,master,calendar,[],day,True,entry_high=entry_high_for(r,entry_highs),entry_low=entry_low_for(r,entry_lows),quote=quotes[(r['instrument_id'],valuation_date)],market=market)

def change_tracking_policy(tid,payload):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        day=current(c);r=one(c,'SELECT * FROM tracking_round WHERE round_id=?',(tid,),'跟踪轮次不存在')
        if r['status']!='tracking':fail('历史轮次不能修改规则')
        if payload.get('policy') is not None:
            version=c.execute('SELECT coalesce(max(policy_version),0)+1 FROM tracking_policy_version WHERE strategy_id=?',(r['strategy_id'],)).fetchone()[0]
            save_policy(c,r['strategy_id'],version,policy_normalized(payload['policy']))
        else:
            version=payload.get('policy_version')
            if not isinstance(version,int):fail('需要policy或已有policy_version')
            one(c,'SELECT * FROM tracking_policy_version WHERE strategy_id=? AND policy_version=?',(r['strategy_id'],version),'规则版本不存在')
        if version!=r['current_policy_version']:apply_tracking_policy(c,r,version,day,get_market().calendar(),reason=str(payload.get('reason','用户将规则应用到本轮跟踪'))[:500])
    return get_tracking(tid)

def remove_tracking(tid,payload=None):
    payload=payload or {}
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        day=current(c);r=one(c,'SELECT * FROM tracking_round WHERE round_id=?',(tid,),'跟踪轮次不存在')
        if r['status']!='tracking':return {'round_id':tid,'status':r['status'],'already_ended':True}
        reason=str(payload.get('reason') or '用户手动移出')[:500]
        insert(c,'tracking_event',dict(event_id=new_id('event'),event_key=tid+':'+day+':manual_remove',round_id=tid,event_type='manual_remove',business_date=day,data_snapshot_id=r['entry_snapshot_id'],data_state='not_applicable',outcome='not_applicable',trading_age=_age(get_market().calendar(),r['entry_business_date'],day) or 0,evidence_json=js({'reason':reason,'ignore_reentry':payload.get('ignore_reentry',True)}),recorded_at_ms=now_ms()))
        c.execute("UPDATE tracking_round SET status='manual_removed',end_business_date=?,end_reason=? WHERE round_id=?",(day,reason,tid))
        if payload.get('ignore_reentry',True):
            c.execute('INSERT INTO tracking_ignore(strategy_id,instrument_id,active,reason,changed_at_ms) VALUES(?,?,1,?,?) ON CONFLICT(strategy_id,instrument_id) DO UPDATE SET active=1,reason=excluded.reason,changed_at_ms=excluded.changed_at_ms',(r['strategy_id'],r['instrument_id'],reason,now_ms()))
    return get_tracking(tid)

def release_ignore(sid,ident):
    with connect() as c:
        c.execute('UPDATE tracking_ignore SET active=0,changed_at_ms=? WHERE strategy_id=? AND instrument_id=?',(now_ms(),sid,ident.upper()))
    return {'released':True}

def list_events(strategy_id=None,event_type=None,business_date=None,limit=100):
    with connect() as c:
        sql='SELECT e.*,r.instrument_id,r.strategy_id,s.name strategy_name FROM tracking_event e JOIN tracking_round r USING(round_id) JOIN strategy s USING(strategy_id) WHERE 1=1';args=[]
        for col,value in [('r.strategy_id',strategy_id),('e.event_type',event_type),('e.business_date',business_date)]:
            if value:sql+=' AND '+col+'=?';args.append(value)
        total=c.execute('SELECT count(*) FROM ('+sql+')',args).fetchone()[0]
        sql+=' ORDER BY e.business_date DESC,e.recorded_at_ms DESC,e.event_id DESC LIMIT ?';args.append(min(max(limit,1),1000));master=identities(current(c))
        data=[{**_event_view(r),**code_info(r['instrument_id'],master)} for r in rows(c,sql,args)]
        return {'items':data,'total':total}

def _historical_topics(c,round_row,day,current_day,current_topics):
    if day==current_day:return current_topics.get(round_row['instrument_id'],[])
    if day==round_row['entry_business_date']:return obj(round_row['entry_basis_json'],{}).get('entry_topics',[])
    # No provider PIT archive is assumed. Saved past reviews are immutable and reused on reruns.
    return None

def _review_tracking_impl(payload=None,job_id=None,expected_inputs=None):
    payload=payload or {};stats={'reviewed':0,'alerts':0,'ended':0,'expired':0,'unknown':0,'skipped':0}
    market=get_market();calendar=market.calendar()
    with connect() as c:
        day=current(c);deps,primary=freeze_inputs(c);topics=topic_map(c,day)
        if expected_inputs is not None and (day,deps,primary)!=expected_inputs:
            fail('行情或补充数据代际已更新，请重新核对跟踪池。',409)
        sql="SELECT * FROM tracking_round WHERE status='tracking'";args=[]
        if payload.get('strategy_id'):sql+=' AND strategy_id=?';args.append(payload['strategy_id'])
        active=rows(c,sql,args)
        policies_for_history=rows(c,'SELECT strategy_id,alerts_json,exits_json FROM tracking_policy_version') if active else []
        count_strategies={policy['strategy_id'] for policy in policies_for_history if any(names(node)&{COUNT_FIELD,'limit_up_1y'} for node in obj(policy['alerts_json'],[])+obj(policy['exits_json'],[]))}
        history_strategies={policy['strategy_id'] for policy in policies_for_history if any(history_nodes(node) for node in obj(policy['alerts_json'],[])+obj(policy['exits_json'],[]))}
    limit_evidence=LimitConditionEvidence(market) if active else None
    entry_highs=load_entry_highs(active)
    entry_lows=load_entry_lows(active)
    status_method=getattr(market,'status',None)
    generation=status_method().get('snapshot_id') if callable(status_method) else None
    action_inputs=load_action_evidence(deps.get('corporate_actions'),sorted({round_['instrument_id'] for round_ in active}),min((round_['entry_business_date'] for round_ in active),default=day),day,generation) if active else {}
    for original in active:
        bars=market.bars(original['instrument_id'],as_of=day)
        # Already-loaded bars carry the immutable generation's action flags.
        # Build outside BEGIN IMMEDIATE; never persist the full context.
        history_trees=[node for policy in policies_for_history if policy['strategy_id']==original['strategy_id'] for node in obj(policy['alerts_json'],[])+obj(policy['exits_json'],[])]
        historical_start=history_retain_start(history_trees,calendar,original['entry_business_date'])
        history_context=HistoryContext(bars,calendar,day,generation,retain_start=historical_start) if original['strategy_id'] in history_strategies else None
        limit_quotes=quote_index(bars)
        dates=[d for d in calendar if original['entry_business_date']<=d<=day]
        comparisons={observed:_entry_comparison(original,observed,finite(limit_quotes[observed].get('close')) if limit_quotes.get(observed) else None,market) for observed in dates}
        count_values=limit_evidence.count_series(original['instrument_id'],dates,calendar,limit_quotes) if original['strategy_id'] in count_strategies else {}
        for date_ in dates:
            with connect() as c:
                c.execute("BEGIN IMMEDIATE")
                if job_id:check_api_task(job_id,c)
                if current(c)!=day or freeze_inputs(c)!=(deps,primary):
                    fail('业务日、行情或补充数据代际已更新，本次未继续写入混合来源核对，请重新核对。',409)
                r=one(c,'SELECT * FROM tracking_round WHERE round_id=?',(original['round_id'],))
                if r['status']!='tracking':break
                p=_policy_at(c,r,date_);age=_age(calendar,r['entry_business_date'],date_)
                previous_reviews=rows(c,"SELECT evidence_json FROM tracking_event WHERE round_id=? AND event_type='review' AND business_date=?",(r['round_id'],date_))
                if any(obj(event['evidence_json'],{}).get('policy_version')==p['policy_version'] for event in previous_reviews):stats['skipped']+=1;continue
                key=r['round_id']+':'+date_+':review'+(':policy:'+str(p['policy_version']) if previous_reviews else '')
                if age is None:stats['unknown']+=1;continue
                current_topics=_historical_topics(c,r,date_,day,topics)
                ms=metrics_for(bars,date_,calendar,'D',[x['topic_id'] for x in current_topics] if current_topics is not None else None)
                ms=add_period_metrics(ms,bars,date_,calendar,p['alerts']+p['exits'],'D',
                                      instrument_id=r['instrument_id'],generation=generation)
                if any(names(node) & DAILY_LIMIT_FIELDS for node in p['alerts']+p['exits']):
                    ms.update(limit_evidence.metrics(r['instrument_id'],date_,calendar,limit_quotes))
                if any(names(node)&{COUNT_FIELD,'limit_up_1y'} for node in p['alerts']+p['exits']):
                    # A user may append a policy while review is in flight.
                    # The fallback is pure local computation over frozen inputs;
                    # it never performs source I/O inside this transaction.
                    if date_ not in count_values:count_values=limit_evidence.count_series(r['instrument_id'],dates,calendar,limit_quotes)
                    ms.update(count_values[date_])
                    if any('limit_up_1y' in names(node) for node in p['alerts']+p['exits']):ms.update(past_limit_up_metrics(count_values[date_]))
                references=set().union(*(topic_dependencies(node) for node in p['alerts']+p['exits']))
                if references:
                    ms['_complete_topic_ids']=complete_topic_ids(c,references) if date_==day else obj(r['entry_basis_json'],{}).get('entry_complete_topic_ids',[]) if date_==r['entry_business_date'] else []
                normalized,nstate,nreason=comparisons[date_] if ms.get('close') is not None else (None,'unknown','缺少当日收盘价')
                ms['trading_age']=age;ms['tracking_change_pct']=(normalized/money(r['entry_price_e8'])-1)*100 if normalized is not None else None;ms['close_below_entry']=e8(normalized)<r['entry_price_e8'] if normalized is not None else None
                if any(ENTRY_HIGH_FIELD in names(node) for node in p['alerts']):
                    ms.update(entry_high_metrics(r,date_,limit_quotes.get(date_),entry_high_for(r,entry_highs),normalized,nstate,nreason,action_inputs.get(r['instrument_id'],{'state':'unknown'})))
                if any(ENTRY_LOW_FIELD in names(node) for node in p['alerts']+p['exits']):
                    ms.update(entry_low_metrics(r,date_,limit_quotes.get(date_),entry_low_for(r,entry_lows),normalized,nstate,nreason,action_inputs.get(r['instrument_id'],{'state':'unknown'})))
                alert,exit_=condition_decisions(p,ms,age,history_context.at(date_) if history_context else None)
                raw_state='ready' if ms.get('close') is not None else 'unknown'
                outcome='satisfied' if alert['result'] is True or exit_['result'] is True else 'unknown' if alert['result'] is None or exit_['result'] is None or raw_state!='ready' else 'not_satisfied'
                evidence={'policy_version':p['policy_version'],'alerts':alert,'exits':exit_,'metrics':ms,'topics':current_topics,'comparison_state':nstate,'comparison_reason':nreason,'source_snapshot_id':primary,'historical_replay':date_!=day}
                insert(c,'tracking_event',dict(event_id=new_id('event'),event_key=key,round_id=r['round_id'],event_type='review',business_date=date_,data_snapshot_id=primary,data_state=raw_state,outcome=outcome,trading_age=age,evidence_json=js(evidence),recorded_at_ms=now_ms()))
                for snap in set(deps.values()):pin(c,snap,'tracking',r['round_id'])
                stats['reviewed']+=1
                if outcome=='unknown':stats['unknown']+=1
                if alert['result'] is True:
                    insert(c,'tracking_event',dict(event_id=new_id('event'),event_key=r['round_id']+':'+date_+':alert:'+str(p['policy_version']),round_id=r['round_id'],event_type='alert',business_date=date_,data_snapshot_id=primary,data_state='ready',outcome='satisfied',trading_age=age,evidence_json=js(evidence),recorded_at_ms=now_ms()));stats['alerts']+=1
                ended=None;reason=None
                if exit_['result'] is True:
                    exit_evidence=dict(evidence)
                    if any('close_below_entry' in names(x) for x in p['exits']) and ms['close_below_entry'] is True:
                        exit_evidence.update(condition_key='close_below_entry',provider_observed_close_e8=e8(ms['close']),normalized_observed_close_e8=e8(normalized),reference_entry_e8=r['entry_price_e8'],reference_entry_date=r['entry_business_date'],comparison_basis_id=obj(r['entry_basis_json'],{})['comparison_basis_id'],normalization_state=nstate,adjustment_snapshot_id=primary)
                    if any(ENTRY_LOW_FIELD in names(node) for node in p['exits']) and ms.get(ENTRY_LOW_FIELD) is True:
                        low=entry_low_for(r,entry_lows)
                        exit_evidence.update(condition_key=ENTRY_LOW_FIELD,provider_observed_close_e8=e8(ms['close']),normalized_observed_close_e8=e8(normalized),reference_entry_low_e8=low['raw_low_e8'],reference_entry_date=r['entry_business_date'],comparison_basis_id=obj(r['entry_basis_json'],{}).get('comparison_basis_id'),normalization_state=nstate,adjustment_snapshot_id=primary)
                    insert(c,'tracking_event',dict(event_id=new_id('event'),event_key=r['round_id']+':'+date_+':condition_end:'+str(p['policy_version']),round_id=r['round_id'],event_type='condition_end',business_date=date_,data_snapshot_id=primary,data_state='ready',outcome='satisfied',trading_age=age,evidence_json=js(exit_evidence),recorded_at_ms=now_ms()));ended='condition_ended';reason='触发用户配置的结束条件';stats['ended']+=1
                elif age>=p['duration_sessions'] and age>0:
                    insert(c,'tracking_event',dict(event_id=new_id('event'),event_key=r['round_id']+':'+date_+':expire',round_id=r['round_id'],event_type='expire',business_date=date_,data_snapshot_id=primary,data_state=raw_state,outcome='not_applicable',trading_age=age,evidence_json=js(evidence),recorded_at_ms=now_ms()));ended='expired';reason=f"达到{p['duration_sessions']}个市场交易日";stats['expired']+=1
                if ended:c.execute('UPDATE tracking_round SET status=?,end_business_date=?,end_reason=? WHERE round_id=?',(ended,date_,reason,r['round_id']))
    return {'business_date':day,**stats}


def review_tracking(payload=None):
    """Claim the local task slot so a sync cannot replace inputs halfway through review."""
    payload=payload or {};job_id=new_id('job');owner=api_lease_owner();key=str(payload.get('request_key') or new_id('review-request'))
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        previous=c.execute('SELECT job_id,status FROM job WHERE request_key=?',(key,)).fetchone()
        if previous:
            if previous['status']=='succeeded':
                saved=c.execute("SELECT details_json FROM job_event WHERE job_id=? AND stage='completed' ORDER BY seq DESC LIMIT 1",(previous['job_id'],)).fetchone()
                return obj(saved[0],{}) if saved else {}
            fail('这次核对请求已经提交，请等待完成或使用新请求重试。',409)
        day=current(c);deps,primary=freeze_inputs(c)
        try:insert(c,'job',dict(job_id=job_id,request_key=key,logical_key=new_id('review'),kind='tracking_review',target_date=day,parameters_json=js(payload),parameters_hash=digest([payload,day,deps]),status='running',phase='reviewing',lease_owner=owner,lease_expires_at_ms=now_ms()+120000,created_at_ms=now_ms()))
        except sqlite3.IntegrityError:fail('已有任务运行中，请稍后核对。',409)
        for snap in set(deps.values()):pin(c,snap,'request',job_id)
        log(c,job_id,'info','reviewing','开始逐交易日核对跟踪轮次')
    stop_lease=start_api_heartbeat(job_id,owner)
    try:
        result=_review_tracking_impl(payload,job_id=job_id,expected_inputs=(day,deps,primary))
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            check_api_task(job_id,c)
            if current(c)!=day or freeze_inputs(c)!=(deps,primary):fail('业务日、行情或补充数据代际已更新，请重新核对跟踪池。',409)
            c.execute("UPDATE job SET status='succeeded',phase='completed',finished_at_ms=? WHERE job_id=?",(now_ms(),job_id))
            log(c,job_id,'info','completed','跟踪核对完成',result)
        return result
    except BaseException as exc:
        with connect() as c:
            c.execute("UPDATE job SET status='failed',phase='failed',error_summary=?,finished_at_ms=? WHERE job_id=? AND status='running'",(str(getattr(exc,'detail',exc))[:500],now_ms(),job_id))
            if c.execute('SELECT status FROM job WHERE job_id=?',(job_id,)).fetchone()[0]=='failed':log(c,job_id,'error','failed','跟踪核对失败',{'message':str(getattr(exc,'detail',exc))[:500]})
        raise
    finally:
        stop_lease()
        with connect() as c:c.execute("DELETE FROM retention_pin WHERE owner_kind='request' AND owner_id=?",(job_id,))
