"""strategies application workflows; persistence and side effects live here."""
from __future__ import annotations
import hashlib,json,sqlite3
from decimal import Decimal,ROUND_HALF_UP
from fastapi import HTTPException
from app.core import connect,get_market,now_ms,new_id
from app.domain.conditions import validate,evaluate,metrics_for,metrics_from_enriched,names,specs,topic_dependencies,past_limit_up_metrics
from app.domain.universe import identity_flags
from app.domain.tracking import trading_age
from app.services.workspace_common import *
from app.services.topics import topic_map, seed_topics, complete_topic_ids
from app.services.strategy_limits import LIMIT_FIELDS, DAILY_LIMIT_FIELDS, COUNT_FIELD, LimitConditionEvidence, batch_quote_indexes
from app.domain.trade_rules import normalize_trade_rules
from app.domain.history_events import nodes as history_nodes, current_names
from app.services.history_events import contexts_for_batch, prepare_shared, batch_size as history_batch_size
from app.services.strategy_periods import periods_for,add_period_metrics,cached_metrics
from app.services import task_service as tasks
ENGINE_VERSION="workspace-v8-bounded-history-events"


def stored_period(period):
    return {'period':'D' if period=='M' else period,'signal_period':period}


def evaluator_for(definition,period,trade_rules=None):
    trees=[definition]+[tree for tree in (trade_rules or {}).values() if tree is not None]
    def relative_price(node):
        if 'right_event_change_pct' in node:return True
        if 'history_event' in node:return relative_price(node['history_event']['then'])
        return any(relative_price(child) for child in node.get('all',node.get('any',[])))
    if any(relative_price(tree) or any('cross_cycle' in event.get('price_path',{}) for event in history_nodes(tree)) for tree in trees):
        return 'workspace-v11-event-percent-cross-cycle'
    if any('price_path' in event for tree in trees for event in history_nodes(tree)):
        return 'workspace-v10-history-price-path'
    return 'workspace-v9-multi-period' if period=='M' or periods_for(definition,period)-{period} else ENGINE_VERSION


def policy_normalized(payload=None):
    p={} if payload is None else payload
    if not isinstance(p,dict):fail('policy必须为对象')
    duration=p.get('duration_sessions',30)
    for field in ('allow_entry_day_alerts','allow_entry_day_exits'):
        if field in p and not isinstance(p[field],bool):fail(field+'必须为true/false')
    if isinstance(duration,bool) or not isinstance(duration,int) or not 1<=duration<=250:fail('跟踪期限应为1至250个交易日')
    result={'duration_sessions':duration,'allow_entry_day_alerts':bool(p.get('allow_entry_day_alerts',False)),'allow_entry_day_exits':bool(p.get('allow_entry_day_exits',False)),'alert_logic':p.get('alert_logic','OR'),'exit_logic':p.get('exit_logic','OR')}
    if result['alert_logic'] not in ('AND','OR') or result['exit_logic'] not in ('AND','OR'):fail('条件逻辑仅支持AND/OR')
    for key in ('alerts','exits'):
        values=p.get(key,[])
        if not isinstance(values,list) or len(values)>50:fail('提醒/结束条件需为最多50项的数组')
        try:result[key]=[validate(x) for x in values]
        except ValueError as e:fail(str(e))
        if key=='exits' and any('close_above_entry_high' in names(node) for node in result[key]):fail('收盘价高于本轮事件日最高价仅用于跟踪提醒，触发后继续跟踪，不能配置为结束条件')
    return result

def save_policy(c,sid,version,policy):
    insert(c,'tracking_policy_version',dict(strategy_id=sid,policy_version=version,duration_sessions=policy['duration_sessions'],allow_entry_day_alerts=int(policy['allow_entry_day_alerts']),allow_entry_day_exits=int(policy['allow_entry_day_exits']),alert_logic=policy['alert_logic'],alerts_json=js(policy['alerts']),exit_logic=policy['exit_logic'],exits_json=js(policy['exits']),recorded_at_ms=now_ms()))

def policy_view(r):
    return {**{k:v for k,v in r.items() if k not in ('alerts_json','exits_json')},'alerts':obj(r.get('alerts_json'),[]),'exits':obj(r.get('exits_json'),[]),'allow_entry_day_alerts':bool(r['allow_entry_day_alerts']),'allow_entry_day_exits':bool(r['allow_entry_day_exits'])}

def active_policy_scope(c,sid):
    """Entry defaults keep following defaults; an explicit round edit opts out.

    Application/event evidence, rather than equal policy values, identifies the
    scope. Thus an older entry default is still eligible after a future-only
    update, while a round with manually chosen rules is preserved.
    """
    active=rows(c,"SELECT * FROM tracking_round WHERE strategy_id=? AND status='tracking'",(sid,))
    applications=rows(c,'SELECT a.* FROM tracking_policy_application a JOIN tracking_round r USING(round_id) WHERE r.strategy_id=? AND r.status=\'tracking\' ORDER BY a.effective_business_date,a.recorded_at_ms,a.rowid',(sid,))
    latest={row['round_id']:row for row in applications}
    default_applications={obj(row['evidence_json'],{}).get('application_id') for row in rows(c,"SELECT e.evidence_json FROM tracking_event e JOIN tracking_round r USING(round_id) WHERE r.strategy_id=? AND r.status='tracking' AND e.event_type='policy_change'",(sid,)) if obj(row['evidence_json'],{}).get('scope')=='strategy_default'}
    following=[];custom=[]
    for row in active:
        application=latest.get(row['round_id'])
        (following if application is None or application['application_id'] in default_applications else custom).append(row)
    return following,custom

def apply_tracking_policy(c,r,version,day,calendar,*,reason,scope='round'):
    """Append a current-day application without resetting entry/history anchors."""
    if version==r['current_policy_version']:return False
    application_id=new_id('policy_application');recorded=now_ms()
    insert(c,'tracking_policy_application',dict(application_id=application_id,round_id=r['round_id'],strategy_id=r['strategy_id'],previous_policy_version=r['current_policy_version'],policy_version=version,effective_business_date=day,recorded_at_ms=recorded,reason=reason))
    c.execute('UPDATE tracking_round SET current_policy_version=? WHERE round_id=?',(version,r['round_id']))
    insert(c,'tracking_event',dict(event_id=new_id('event'),event_key=new_id('policy-event'),round_id=r['round_id'],event_type='policy_change',business_date=day,data_snapshot_id=r['entry_snapshot_id'],data_state='not_applicable',outcome='not_applicable',trading_age=trading_age(calendar,r['entry_business_date'],day) or 0,evidence_json=js({'application_id':application_id,'previous_policy_version':r['current_policy_version'],'policy_version':version,'scope':scope,'effective_business_date':day,'reason':reason}),recorded_at_ms=recorded))
    return True

def strategy_view(c,sid):
    r=one(c,'SELECT * FROM strategy WHERE strategy_id=?',(sid,),'策略不存在')
    v=one(c,'SELECT * FROM strategy_version WHERE strategy_id=? AND version=?',(sid,r['current_version']))
    p=one(c,'SELECT * FROM tracking_policy_version WHERE strategy_id=? AND policy_version=?',(sid,r['current_policy_version']))
    latest=c.execute('SELECT run_id,business_date,status FROM selection_run WHERE strategy_id=? ORDER BY created_at_ms DESC,rowid DESC LIMIT 1',(sid,)).fetchone()
    following,custom=active_policy_scope(c,sid)
    return {**r,'version':v['version'],'period':v.get('signal_period') or v['period'],'definition':obj(v['definition_json'],{}),'trade_rules':obj(v['trade_rules_json'],{'entry':None,'exit':None}),'policy':policy_view(p),'latest_run':dict(latest) if latest else None,'tracking_count':len(following)+len(custom),'default_policy_tracking_count':len(following),'custom_policy_tracking_count':len(custom)}

def list_strategies():
    with connect() as c:
        data=[strategy_view(c,r[0]) for r in c.execute('SELECT strategy_id FROM strategy ORDER BY created_at_ms,name')]
        return {'items':data,'total':len(data)}

def get_strategy(sid):
    with connect() as c:return strategy_view(c,sid)

def create_strategy(payload,strategy_id=None):
    name=str(payload.get('name','')).strip();period=payload.get('period','D')
    if not name or len(name)>100:fail('策略名称需要1至100字')
    if period not in ('D','W','M'):fail('只支持日线/周线/月线策略')
    try:definition=validate(payload.get('definition',{}))
    except ValueError as e:fail(str(e))
    if names(definition)&{'close_above_entry_high','close_below_entry_low'}:fail('事件日高低价属于每轮入池跟踪基准，不能作为选股条件')
    try:trade_rules=normalize_trade_rules(payload.get('trade_rules'))
    except ValueError as e:fail(str(e))
    policy=policy_normalized(payload.get('policy'))
    with connect() as c:
        sid=strategy_id or new_id('strategy')
        insert(c,'strategy',dict(strategy_id=sid,name=name,created_at_ms=now_ms()))
        insert(c,'strategy_version',dict(strategy_id=sid,version=1,**stored_period(period),definition_json=js(definition),definition_sha256=digest(definition),trade_rules_json=js(trade_rules),evaluator_version=evaluator_for(definition,period,trade_rules),recorded_at_ms=now_ms()))
        save_policy(c,sid,1,policy)
        c.execute('UPDATE strategy SET current_version=1,current_policy_version=1 WHERE strategy_id=?',(sid,))
        return strategy_view(c,sid)

def update_strategy(sid,payload):
    apply_active=payload.get('apply_to_active_tracking',False)
    if not isinstance(apply_active,bool):fail('apply_to_active_tracking必须为true/false')
    if apply_active and 'policy' not in payload:fail('同步当前轮次时需要提供policy')
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        old=strategy_view(c,sid);name=str(payload.get('name',old['name'])).strip()
        if not name or len(name)>100:fail('策略名称需要1至100字')
        period=payload.get('period',old['period'])
        if period not in ('D','W','M'):fail('只支持日线/周线/月线策略')
        try:definition=validate(payload.get('definition',old['definition']))
        except ValueError as e:fail(str(e))
        if names(definition)&{'close_above_entry_high','close_below_entry_low'}:fail('事件日高低价属于每轮入池跟踪基准，不能作为选股条件')
        try:trade_rules=normalize_trade_rules(payload.get('trade_rules',old['trade_rules']))
        except ValueError as e:fail(str(e))
        if 'definition' in payload or 'period' in payload or 'trade_rules' in payload:
            version=old['version']+1
            insert(c,'strategy_version',dict(strategy_id=sid,version=version,**stored_period(period),definition_json=js(definition),definition_sha256=digest(definition),trade_rules_json=js(trade_rules),evaluator_version=evaluator_for(definition,period,trade_rules),recorded_at_ms=now_ms()))
            c.execute('UPDATE strategy SET current_version=? WHERE strategy_id=?',(version,sid))
        if 'policy' in payload:
            following,custom=active_policy_scope(c,sid)
            pv=c.execute('SELECT coalesce(max(policy_version),0)+1 FROM tracking_policy_version WHERE strategy_id=?',(sid,)).fetchone()[0];save_policy(c,sid,pv,policy_normalized(payload['policy']))
            c.execute('UPDATE strategy SET current_policy_version=? WHERE strategy_id=?',(pv,sid))
            day=current(c);applied=[]
            if apply_active:
                calendar=get_market().calendar()
                for round_ in following:
                    if apply_tracking_policy(c,round_,pv,day,calendar,reason='用户保存默认政策并同步沿用默认政策的当前轮次',scope='strategy_default'):applied.append(round_['round_id'])
        c.execute('UPDATE strategy SET name=?,archived=? WHERE strategy_id=?',(name,int(bool(payload.get('archived',old['archived']))),sid))
        result=strategy_view(c,sid)
        if 'policy' in payload:result['policy_application']={'scope':'active_default' if apply_active else 'future_only','effective_business_date':day,'applied_count':len(applied),'skipped_custom_count':len(custom) if apply_active else 0,'eligible_count':len(following),'applied_round_ids':applied,'requires_manual_review':bool(applied)}
        return result

def ensure_evaluator_version(c,sid):
    st=strategy_view(c,sid)
    old=one(c,'SELECT * FROM strategy_version WHERE strategy_id=? AND version=?',(sid,st['version']))
    desired=evaluator_for(st['definition'],st['period'],st.get('trade_rules'))
    if old['evaluator_version']!=desired:
        version=c.execute('SELECT max(version)+1 FROM strategy_version WHERE strategy_id=?',(sid,)).fetchone()[0]
        insert(c,'strategy_version',dict(strategy_id=sid,version=version,**stored_period(st['period']),definition_json=old['definition_json'],definition_sha256=old['definition_sha256'],trade_rules_json=old['trade_rules_json'],evaluator_version=desired,recorded_at_ms=now_ms()))
        c.execute('UPDATE strategy SET current_version=? WHERE strategy_id=?',(version,sid))
    return strategy_view(c,sid)


def seed_workspace():
    """Explicit post-import bootstrap, does not select stocks or create tracking rounds."""
    with connect() as c:
        day=current(c);existing={r[0] for r in c.execute('SELECT strategy_id FROM strategy')}
    defaults=[('builtin-volume-3x','三倍量观察',{'all':[{'field':'volume_ratio','op':'>=','value':3},{'field':'close_above_ma30','op':'==','value':True}]}),('builtin-ma-trend','均线趋势观察',{'all':[{'field':'close_above_ma30','op':'==','value':True},{'field':'close_above_ma60','op':'==','value':True},{'field':'change_pct','op':'>=','value':3}]}),('builtin-kd-cross','KDJ金叉观察',{'all':[{'field':'kd_cross','op':'==','value':True},{'field':'close_above_ma20','op':'==','value':True}]})]
    for sid,name,definition in defaults:
        if sid not in existing:create_strategy({'name':name,'definition':definition},strategy_id=sid)
        else:
            with connect() as c:ensure_evaluator_version(c,sid)
    result=seed_topics()
    return {**result,'default_strategies':3}

def _candidate(r,master):
    return {**code_info(r['instrument_id'],master),'decision':r['decision'],'close_price':money(r['close_price_e8']),'metrics':obj(r['metrics_json'],{}),'checks':obj(r['checks_json'],{}),'topics':obj(r['topic_snapshot_json'],[])}

def compact_candidate(metrics,checks,topics,decision,definition):
    """Sparse non-matches retain evidence; immutable inputs can reconstruct other indicators."""
    if decision=='matched':return metrics,checks,topics
    fields=names(definition)
    fields|={'close','change_pct','volume','amount','volume_ratio','observed_date','indicator_period','indicator_warmup_bars','signal_date','signal_close','signal_price','current_daily_close','entry_price_policy','price_basis','price_continuity','quality','_complete_topic_ids','identity_scope','identity_as_of'}
    for field in list(fields):
        if '_ma' in field:fields.add('ma'+field.rsplit('_ma',1)[1])
    if fields&{'macd_cross','macd_dead_cross'}:fields|={'macd_dif','macd_dea'}
    if fields&{'kd_cross','kd_dead_cross'}:fields|={'kdj_k','kdj_d'}
    if 'close_breakout_high' in fields:fields|={'breakout_valid','breakout_previous_sessions','breakout_blocking_distance','new_high_current_close'}
    if 'bullish_ma_alignment' in fields:fields|={'ma5','ma10','ma20','ma30','ma60','bullish_ma_alignment_reason'}
    for field in fields & LIMIT_FIELDS:
        fields|={field+suffix for suffix in ('_date','_reason','_status','_evidence','_confirmed_count','_lower_bound','_upper_bound','_coverage')}
    if 'limit_up_1y' in fields:fields|={'limit_up_1y_reason','limit_up_1y_evidence'}
    references=topic_dependencies(definition)
    reduced={key:value for key,value in metrics.items() if key in fields}
    if references:reduced['topic_ids']=[key for key in metrics.get('topic_ids') or [] if key in references]
    def small_check(node):
        result=dict(node)
        if result.get('field')=='topic_ids' and isinstance(result.get('actual'),list):
            result['actual']=[key for key in result['actual'] if key in references]
            result['actual_scope']='strategy_topic_dependencies'
        if 'checks' in result:result['checks']=[small_check(child) for child in result['checks']]
        return result
    return reduced,small_check(checks),[topic for topic in topics if topic['topic_id'] in references]


def selection_history_batch(market,symbols,day):
    """Bound the slow D/W path; never call all-market histories(day)."""
    if len(symbols)>32:raise ValueError('逐股策略历史每批最多32只证券')
    method=getattr(market,'condition_histories',None)
    return method(symbols,end=day) if callable(method) else {symbol:market.bars(symbol,as_of=day) for symbol in symbols}

def run_strategy(sid,payload=None,*,queued_job=None):
    payload=payload or {};key=str(payload.get('request_key') or new_id('selection-request'))
    if len(key)>200:fail('request_key过长')
    job_id=queued_job['job_id'] if queued_job else new_id('job');rid=new_id('run');owner=queued_job['lease_owner'] if queued_job else api_lease_owner()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        found=c.execute('SELECT r.run_id,r.strategy_id FROM selection_run r JOIN job j USING(job_id) WHERE j.request_key=?',(key,)).fetchone()
        if found:
            if found['strategy_id']!=sid:fail('request_key已用于其他策略，不能复用。',409)
            return get_run(found['run_id'])
        day=current(c);st=ensure_evaluator_version(c,sid)
        if st['archived']:fail('策略已归档，不能运行')
        deps,primary=freeze_inputs(c);topics=topic_map(c,day);topic_dependencies_=topic_dependencies(st['definition']);complete_topics=complete_topic_ids(c,topic_dependencies_)
        if queued_job:
            from .strategy_batches import validate_item
            validate_item(c,queued_job,st,day,deps,primary)
        else:
            try:insert(c,'job',dict(job_id=job_id,request_key=key,logical_key=new_id('selection'),kind='selection',target_date=day,parameters_json=js({'strategy_id':sid,'version':st['version']}),parameters_hash=digest([sid,st['version'],day,deps]),status='running',phase='evaluating',lease_owner=owner,lease_expires_at_ms=now_ms()+120000,created_at_ms=now_ms()))
            except sqlite3.IntegrityError:tasks.active_task_conflict(c)
        log(c,job_id,'info','evaluating','正在按已发布行情计算策略条件')
    stop_lease=(lambda:None) if queued_job else start_api_heartbeat(job_id,owner)
    def check_task(connection=None):
        if queued_job:
            from .strategy_batches import check_item_lease
            if connection is not None: check_item_lease(queued_job, connection)
            else:
                with connect() as checked: check_item_lease(queued_job, checked)
        else: check_api_task(job_id, connection)
    def report(*args):
        return tasks.progress(job_id, *args, lease_owner=owner if queued_job else None)
    try:
        report('准备个股策略数据',0,'读取已发布行情、交易日历和策略依赖')
        market=get_market();calendar=market.calendar();master=identities(day);result=[];required=names(st['definition'])
        current_required=current_names(st['definition']);event_nodes=history_nodes(st['definition'])
        fast=st['period']=='D'
        status_method=getattr(market,'status',None)
        generation=status_method().get('snapshot_id') if callable(status_method) else None
        extra_periods=periods_for(st['definition'],st['period'])-{st['period']}
        panel_rows={x['instrument_id']:x for x in market_panel(day)} if fast or 'limit_up' in required else {}
        fast=fast and all('indicator_warmup_bars' in x for x in panel_rows.values())
        previous_panel={}
        if fast and any(field.startswith('cross_above_ma') for field in current_required):
            position=calendar.index(day) if day in calendar else 0
            previous_panel={row['instrument_id']:row for row in market_panel(calendar[position-1])} if position>0 else {}
        breakout_method=getattr(market,'breakout_states',None)
        breakout_values=breakout_method(day) if fast and 'close_breakout_high' in current_required and callable(breakout_method) else None
        needs_count=COUNT_FIELD in required or 'limit_up_1y' in required
        limit_evidence=LimitConditionEvidence(market) if required & LIMIT_FIELDS or needs_count else None
        count_values=limit_evidence.batch_count_metrics(market,sorted(master),day,calendar,include_daily=bool(required & DAILY_LIMIT_FIELDS)) if needs_count else {}
        limit_quotes=batch_quote_indexes(market,sorted(master),day,calendar) if fast and limit_evidence and not needs_count else {}
        check_task()
        ordered=sorted(master);event_contexts={};history_batch=(history_batch_size([st['definition']]) if event_nodes else 256) if fast else 32
        report('核验个股条件',0,f'准备核验 {len(ordered)} 只证券',{'actual_completed_batches':0,'actual_total_batches':len(ordered)})
        history_shared=prepare_shared(market,ordered,[st['definition']],calendar,day) if event_nodes else None
        batch_bars={}
        for sequence,ident in enumerate(ordered):
            if sequence%128==0:check_task()
            if (not fast or extra_periods) and sequence%32==0:
                batch_bars=selection_history_batch(market,ordered[sequence:sequence+32],day)
                if limit_evidence and not needs_count:
                    limit_quotes=batch_quote_indexes(market,ordered[sequence:sequence+32],day,calendar,batch_bars)
            if event_nodes and sequence%history_batch==0:
                event_contexts=contexts_for_batch(market,ordered[sequence:sequence+history_batch],[st['definition']],calendar,day,batch_bars if not fast else None,shared=history_shared,limit_evidence=limit_evidence)
            item=master[ident]
            if item.get('kind',item.get('asset_type','stock')) not in ('stock','equity','a_share','A'):continue
            topic_ids=[x['topic_id'] for x in topics.get(ident,[])]
            ms=metrics_from_enriched(panel_rows.get(ident),previous_panel.get(ident),topics=topic_ids) if fast else cached_metrics(batch_bars.get(ident,[]),day,calendar,st['period'],topics=topic_ids,instrument_id=ident,generation=generation)
            if breakout_values is not None:ms.update(breakout_values.get(ident,{'breakout_valid':False}))
            ms.update(identity_flags(item,day))
            ms['indicator_period']=st['period']
            if extra_periods:
                ms=add_period_metrics(ms,batch_bars.get(ident,[]),day,calendar,st['definition'],st['period'],
                                      instrument_id=ident,generation=generation)
            if topic_dependencies_:ms['_complete_topic_ids']=complete_topics
            if needs_count:
                ms.update(count_values[ident])
                if 'limit_up_1y' in required:ms.update(past_limit_up_metrics(count_values[ident]))
            elif limit_evidence:ms.update(limit_evidence.metrics(ident,day,calendar,limit_quotes.get(ident,{})))
            context=event_contexts.get(ident)
            check=evaluate(st['definition'],ms,context.at(day) if context else None);decision='matched' if check['result'] is True else 'unmatched' if check['result'] is False else 'unknown'
            entry_close=ms.get('close')
            if st['period'] in ('W','M'):
                daily=next((bar for bar in reversed(batch_bars.get(ident,[])) if str(bar.get('date',bar.get('trade_date','')))[:10]==day),None)
                entry_close=float(daily['close']) if daily and daily.get('close') is not None else None
                ms.update(signal_date=ms.get('observed_date'),signal_close=ms.get('close'),current_daily_close=entry_close,entry_price_policy='business_day_close-v1')
            stored_metrics,stored_checks,stored_topics=compact_candidate(ms,check,topics.get(ident,[]),decision,st['definition'])
            result.append(dict(run_id=rid,instrument_id=ident,decision=decision,close_price_e8=e8(entry_close) if entry_close and entry_close>0 else None,checks_json=js(stored_checks),metrics_json=js(stored_metrics),topic_snapshot_json=js(stored_topics)))
            if sequence%128==0 or sequence+1==len(ordered):
                report('核验个股条件',min(95,round((sequence+1)/max(1,len(ordered))*95)),
                    f'已核验 {sequence+1}/{len(ordered)} 只证券',
                    {'actual_completed_batches':sequence+1,'actual_total_batches':len(ordered)})
        report('保存策略结果',97,'条件已核验，正在保存本次运行与候选依据')
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            check_task(c)
            if current(c)!=day:fail('业务日已更新，请重新运行策略。',409)
            if freeze_inputs(c)!=(deps,primary):fail('行情或补充数据代际已更新，本次未保存混合来源结果，请重新运行策略。',409)
            insert(c,'selection_run',dict(run_id=rid,job_id=job_id,strategy_id=sid,strategy_version=st['version'],business_date=day,primary_snapshot_id=primary,input_snapshots_json=js(deps),input_sha256=digest([deps,st['definition'],day]),status='running',universe_definition_json=js({'scope':'currently_imported_stock_master','count':len(result),'survivorship_warning':True,'candidate_storage_contract':'sparse-nonmatches-v1'}),created_at_ms=now_ms()))
            for row in result:insert(c,'selection_candidate',row)
            c.execute("UPDATE selection_run SET status='completed',completed_at_ms=? WHERE run_id=?",(now_ms(),rid))
            for snap in set(deps.values()):pin(c,snap,'selection',rid)
            c.execute("UPDATE job SET status='succeeded',phase='completed',finished_at_ms=?,accepted_batches=100,planned_batches=100 WHERE job_id=?",(now_ms(),job_id))
            counts={k:sum(r['decision']==k for r in result) for k in ('matched','unmatched','unknown')};log(c,job_id,'info','completed','策略运行完成',counts)
        return get_run(rid)
    except Exception as e:
        with connect() as c:
            c.execute("UPDATE job SET status='failed',phase='failed',finished_at_ms=?,error_summary=? WHERE job_id=? AND status='running' AND lease_owner=?",(now_ms(),str(getattr(e,'detail',e))[:500],job_id,owner))
            if c.execute('SELECT status FROM job WHERE job_id=?',(job_id,)).fetchone()[0]=='failed':log(c,job_id,'error','failed','策略运行失败',{'message':str(getattr(e,'detail',e))[:500]})
        raise
    finally:
        stop_lease()

def get_run(rid,decision=None):
    with connect() as c:
        run=one(c,'SELECT r.*,s.name strategy_name FROM selection_run r JOIN strategy s USING(strategy_id) WHERE run_id=?',(rid,),'策略运行不存在')
        allrows=rows(c,'SELECT * FROM selection_candidate WHERE run_id=? ORDER BY decision,instrument_id',(rid,));master=identities(run['business_date'])
        return {k:v for k,v in run.items() if not k.endswith('_json')}|{'input_snapshots':obj(run['input_snapshots_json'],{}),'matched_count':sum(r['decision']=='matched' for r in allrows),'unmatched_count':sum(r['decision']=='unmatched' for r in allrows),'unknown_count':sum(r['decision']=='unknown' for r in allrows),'items':[_candidate(r,master) for r in allrows if decision is None or r['decision']==decision]}

def list_runs(sid=None,limit=50):
    with connect() as c:
        sql='SELECT r.run_id,r.strategy_id,r.strategy_version,r.business_date,r.status,r.created_at_ms,s.name strategy_name FROM selection_run r JOIN strategy s USING(strategy_id)';params=[]
        if sid:sql+=' WHERE r.strategy_id=?';params.append(sid)
        sql+=' ORDER BY r.created_at_ms DESC,r.rowid DESC LIMIT ?';params.append(min(max(limit,1),200));data=rows(c,sql,params)
        for r in data:r['counts']={x['decision']:x['n'] for x in rows(c,'SELECT decision,count(*) n FROM selection_candidate WHERE run_id=? GROUP BY decision',(r['run_id'],))}
        return {'items':data,'total':len(data)}
