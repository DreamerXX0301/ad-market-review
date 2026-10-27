"""strategies application workflows; persistence and side effects live here."""
from __future__ import annotations
import hashlib,json,sqlite3,time
from decimal import Decimal,ROUND_HALF_UP
from fastapi import HTTPException
from app.core import connect,get_market,now_ms,new_id
from app.domain.conditions import validate,evaluate,metrics_for,metrics_from_enriched,names,specs,topic_dependencies,past_limit_up_metrics,prepare_metric_bars
from app.domain.universe import identity_flags
from app.domain.tracking import trading_age
from app.services.workspace_common import *
from app.services.topics import topic_map, seed_topics, complete_topic_ids
from app.services.strategy_limits import LIMIT_FIELDS, DAILY_LIMIT_FIELDS, COUNT_FIELD, LimitConditionEvidence, batch_quote_indexes
from app.domain.trade_rules import normalize_trade_rules
from app.domain.history_events import nodes as history_nodes, current_names
from app.services.history_events import contexts_for_batch, prepare_shared, batch_size as history_batch_size
from app.services.strategy_periods import periods_for,add_period_metrics,cached_metrics,compile_metric_plan,cached_metrics_if_ready,ordinary_metrics_ready,complete_from_basis,remember_prepared,prepare_saved_structures
from app.services import task_service as tasks
from app.services.workspace_deletion import require_strategy_present,strategy_visibility
from app.services import strategy_hot_universe as hot_universe
from app.services.condition_basis_cache import disk_session
from app.services.selection_compute_pool import compute_session,current_session
ENGINE_VERSION="workspace-v8-bounded-history-events"
FORWARD_ENGINE_VERSION='workspace-v17-local-forward-prices-v1'
PRICE_BASIS_CONTRACT='strategy-local-forward-v1'
FORWARD_SELECTION_BATCH_SIZE=16


def normalized_price_basis(value='none'):
    if value not in ('none','forward'):fail('策略价格口径仅支持none（未复权）或forward（本地前复权）')
    return value


def definition_digest(definition,price_basis='none'):
    # Legacy raw definitions keep their established hashes. The opt-in price
    # convention is part of the new immutable definition, not a chart preference.
    return digest(definition if price_basis=='none' else {'definition':definition,'price_basis':price_basis,'price_basis_contract':PRICE_BASIS_CONTRACT})


def stored_period(period):
    return {'period':'D' if period=='M' else period,'signal_period':period}


def evaluator_for(definition,period,trade_rules=None,hot_filter=None,price_basis='none'):
    if price_basis=='forward':return FORWARD_ENGINE_VERSION
    trees=[definition]+[tree for tree in (trade_rules or {}).values() if tree is not None]
    if any(names(tree)&{'volume_expand','volume_shrink'} for tree in trees):return 'workspace-v16-volume-comparison-v1'
    if any('boll_signal' in names(tree) for tree in trees):return 'workspace-v15-boll-signal-v1'
    if hot_filter is not None:return 'workspace-v14-current-hot-universe'
    if any('higher_lows' in names(tree) for tree in trees):return 'workspace-v13-higher-lows-v1'
    if any(event.get('match')=='any' for tree in trees for event in history_nodes(tree)):
        return 'workspace-v12-history-any'
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
    active=rows(c,"SELECT round_id,strategy_id,current_policy_version,entry_snapshot_id,entry_business_date FROM tracking_round WHERE strategy_id=? AND status='tracking'",(sid,))
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
    return {**r,'version':v['version'],'period':v.get('signal_period') or v['period'],'price_basis':v.get('price_basis','none'),'price_basis_contract':PRICE_BASIS_CONTRACT if v.get('price_basis')=='forward' else None,'definition':obj(v['definition_json'],{}),'hot_universe':obj(v.get('hot_universe_json')),'trade_rules':obj(v['trade_rules_json'],{'entry':None,'exit':None}),'policy':policy_view(p),'latest_run':dict(latest) if latest else None,'tracking_count':len(following)+len(custom),'default_policy_tracking_count':len(following),'custom_policy_tracking_count':len(custom),**strategy_visibility(c,sid)}

def list_strategies(deleted=False,limit=None,offset=0):
    if not isinstance(deleted,bool) or (limit is not None and (isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=100)) or isinstance(offset,bool) or not isinstance(offset,int) or offset<0:fail('策略列表分页参数无效')
    with connect() as c:
        condition='d.strategy_id IS NOT NULL' if deleted else 'd.strategy_id IS NULL'
        query='SELECT s.strategy_id FROM strategy s LEFT JOIN strategy_deletion d USING(strategy_id) WHERE '+condition+' ORDER BY s.created_at_ms,s.name,s.strategy_id'
        parameters=()
        if limit is not None:query+=' LIMIT ? OFFSET ?';parameters=(limit,offset)
        elif offset:query+=' LIMIT -1 OFFSET ?';parameters=(offset,)
        data=[strategy_view(c,r[0]) for r in c.execute(query,parameters)]
        total=c.execute('SELECT count(*) FROM strategy s LEFT JOIN strategy_deletion d USING(strategy_id) WHERE '+condition).fetchone()[0]
        return {'items':data,'total':total,'limit':limit,'offset':offset,'has_more':offset+len(data)<total}

def get_strategy(sid):
    with connect() as c:
        if c.execute('SELECT 1 FROM strategy_deletion WHERE strategy_id=?',(sid,)).fetchone():fail('策略已移入回收站，请先恢复',404)
        return strategy_view(c,sid)

def create_strategy(payload,strategy_id=None):
    name=str(payload.get('name','')).strip();period=payload.get('period','D')
    price_basis=normalized_price_basis(payload.get('price_basis','none'))
    if not name or len(name)>100:fail('策略名称需要1至100字')
    if period not in ('D','W','M'):fail('只支持日线/周线/月线策略')
    try:definition=validate(payload.get('definition',{}))
    except ValueError as e:fail(str(e))
    if names(definition)&{'close_above_entry_high','close_below_entry_low'}:fail('事件日高低价属于每轮入池跟踪基准，不能作为选股条件')
    try:trade_rules=normalize_trade_rules(payload.get('trade_rules'))
    except ValueError as e:fail(str(e))
    policy=policy_normalized(payload.get('policy'))
    hot_filter=hot_universe.normalize(payload.get('hot_universe'))
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        sid=strategy_id or new_id('strategy')
        from .workspace_purge import was_purged
        if was_purged(c,'strategy',sid):fail('此策略标识已永久删除，请使用新的策略标识',409)
        insert(c,'strategy',dict(strategy_id=sid,name=name,created_at_ms=now_ms()))
        insert(c,'strategy_version',dict(strategy_id=sid,version=1,**stored_period(period),price_basis=price_basis,definition_json=js(definition),definition_sha256=definition_digest(definition,price_basis),trade_rules_json=js(trade_rules),hot_universe_json=js(hot_filter) if hot_filter else None,evaluator_version=evaluator_for(definition,period,trade_rules,hot_filter,price_basis),recorded_at_ms=now_ms()))
        save_policy(c,sid,1,policy)
        c.execute('UPDATE strategy SET current_version=1,current_policy_version=1 WHERE strategy_id=?',(sid,))
        return strategy_view(c,sid)

def update_strategy(sid,payload):
    apply_active=payload.get('apply_to_active_tracking',False)
    if not isinstance(apply_active,bool):fail('apply_to_active_tracking必须为true/false')
    if apply_active and 'policy' not in payload:fail('同步当前轮次时需要提供policy')
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        require_strategy_present(c,sid)
        old=strategy_view(c,sid);name=str(payload.get('name',old['name'])).strip()
        if not name or len(name)>100:fail('策略名称需要1至100字')
        period=payload.get('period',old['period'])
        price_basis=normalized_price_basis(payload.get('price_basis',old.get('price_basis','none')))
        if period not in ('D','W','M'):fail('只支持日线/周线/月线策略')
        try:definition=validate(payload.get('definition',old['definition']))
        except ValueError as e:fail(str(e))
        if names(definition)&{'close_above_entry_high','close_below_entry_low'}:fail('事件日高低价属于每轮入池跟踪基准，不能作为选股条件')
        try:trade_rules=normalize_trade_rules(payload.get('trade_rules',old['trade_rules']))
        except ValueError as e:fail(str(e))
        hot_filter=hot_universe.normalize(payload.get('hot_universe',old['hot_universe']))
        if 'definition' in payload or 'period' in payload or 'trade_rules' in payload or 'hot_universe' in payload or 'price_basis' in payload:
            version=old['version']+1
            insert(c,'strategy_version',dict(strategy_id=sid,version=version,**stored_period(period),price_basis=price_basis,definition_json=js(definition),definition_sha256=definition_digest(definition,price_basis),trade_rules_json=js(trade_rules),hot_universe_json=js(hot_filter) if hot_filter else None,evaluator_version=evaluator_for(definition,period,trade_rules,hot_filter,price_basis),recorded_at_ms=now_ms()))
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
    require_strategy_present(c,sid)
    st=strategy_view(c,sid)
    old=one(c,'SELECT * FROM strategy_version WHERE strategy_id=? AND version=?',(sid,st['version']))
    desired=evaluator_for(st['definition'],st['period'],st.get('trade_rules'),st.get('hot_universe'),st.get('price_basis','none'))
    if old['evaluator_version']!=desired:
        version=c.execute('SELECT max(version)+1 FROM strategy_version WHERE strategy_id=?',(sid,)).fetchone()[0]
        insert(c,'strategy_version',dict(strategy_id=sid,version=version,**stored_period(st['period']),price_basis=st.get('price_basis','none'),definition_json=old['definition_json'],definition_sha256=old['definition_sha256'],trade_rules_json=old['trade_rules_json'],hot_universe_json=old.get('hot_universe_json'),evaluator_version=desired,recorded_at_ms=now_ms()))
        c.execute('UPDATE strategy SET current_version=? WHERE strategy_id=?',(version,sid))
    return strategy_view(c,sid)


def seed_workspace(price_basis='none'):
    """Explicit post-import bootstrap, does not select stocks or create tracking rounds."""
    price_basis=normalized_price_basis(price_basis)
    with connect() as c:
        day=current(c);existing={r[0] for r in c.execute('SELECT strategy_id FROM strategy')}
        purged={r[0] for r in c.execute("SELECT target_id FROM workspace_purge WHERE target_kind='strategy' AND business_deleted=1")}
    defaults=[('builtin-volume-3x','三倍量观察',{'all':[{'field':'volume_ratio','op':'>=','value':3},{'field':'close_above_ma30','op':'==','value':True}]}),('builtin-ma-trend','均线趋势观察',{'all':[{'field':'close_above_ma30','op':'==','value':True},{'field':'close_above_ma60','op':'==','value':True},{'field':'change_pct','op':'>=','value':3}]}),('builtin-kd-cross','KDJ金叉观察',{'all':[{'field':'kd_cross','op':'==','value':True},{'field':'close_above_ma20','op':'==','value':True}]})]
    for sid,name,definition in defaults:
        if sid in purged:continue
        if sid not in existing:create_strategy({'name':name,'definition':definition,'price_basis':price_basis},strategy_id=sid)
        else:
            with connect() as c:
                c.execute('BEGIN IMMEDIATE')
                if not c.execute('SELECT 1 FROM strategy_deletion WHERE strategy_id=?',(sid,)).fetchone():ensure_evaluator_version(c,sid)
    result=seed_topics()
    return {**result,'default_strategies':3}

def _candidate(r,master):
    return {**code_info(r['instrument_id'],master),'decision':r['decision'],'close_price':money(r['close_price_e8']),'metrics':obj(r['metrics_json'],{}),'checks':obj(r['checks_json'],{}),'topics':obj(r['topic_snapshot_json'],[])}

def candidate_requirements(definition):
    """Compile sparse evidence fields once for a selection's fixed rule."""
    fields=names(definition)
    fields|={'close','change_pct','volume','amount','volume_ratio','observed_date','indicator_period','indicator_warmup_bars','signal_date','signal_close','signal_price','current_daily_close','entry_price_policy','price_basis','price_continuity','quality','_complete_topic_ids','identity_scope','identity_as_of'}
    fields|={'requested_price_basis','anchor_date','adjustment_anchor','adjustment','price_contract','raw_open','raw_high','raw_low','raw_close','raw_volume','raw_amount'}
    for field in list(fields):
        if '_ma' in field:fields.add('ma'+field.rsplit('_ma',1)[1])
    if fields&{'macd_cross','macd_dead_cross'}:fields|={'macd_dif','macd_dea'}
    if fields&{'kd_cross','kd_dead_cross'}:fields|={'kdj_k','kdj_d'}
    if 'close_breakout_high' in fields:fields|={'breakout_valid','breakout_previous_sessions','breakout_blocking_distance','new_high_current_close'}
    if 'higher_lows' in fields:fields.add('higher_lows_state')
    if 'boll_signal' in fields:fields.add('boll_states')
    if 'bullish_ma_alignment' in fields:fields|={'ma5','ma10','ma20','ma30','ma60','bullish_ma_alignment_reason'}
    for field in fields & LIMIT_FIELDS:
        fields|={field+suffix for suffix in ('_date','_reason','_status','_evidence','_confirmed_count','_lower_bound','_upper_bound','_coverage')}
    if 'limit_up_1y' in fields:fields|={'limit_up_1y_reason','limit_up_1y_evidence'}
    references=topic_dependencies(definition)
    return fields,references


def compact_candidate(metrics,checks,topics,decision,definition,*,requirements=None):
    """Sparse non-matches retain evidence; immutable inputs can reconstruct other indicators."""
    if decision=='matched':return metrics,checks,topics
    fields,references=requirements if requirements is not None else candidate_requirements(definition)
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


def selection_metric_batch(market,symbols,day,plan,generation,*,include_default=True,require_history=False,price_basis='none',source_token=None,price_contexts=None,history_cached_symbols=None,history_trees=None,computed_histories=None):
    """Read original seeds only for cache misses, with at most 32 securities.

    Weekly/monthly entry still uses the observation day's own raw close. A
    warm technical summary never substitutes its closed-period signal price.
    """
    if len(symbols)>32:raise ValueError('逐股策略历史每批最多32只证券')
    periods=[period for period in plan.periods if include_default or period!=plan.default]
    contexts=price_contexts if price_contexts is not None else {}
    if price_basis=='forward':
        from app.domain.strategy_prices import prepare_condition_prices,price_cache_key
        for symbol in symbols:contexts[symbol]={'requested_price_basis':'forward','anchor_date':day,
                                              'cache_key':price_cache_key(symbol,day,'forward',source_token)}
        if generation and source_token is not None:prepare_saved_structures(symbols,day,periods,plan,generation,contexts)
    ready={symbol:{period:cached_metrics_if_ready(day,period,instrument_id=symbol,generation=generation,request=plan.requests[period],
             **({'price_context':contexts[symbol]} if price_basis=='forward' else {})) for period in periods} for symbol in symbols}
    daily_method=getattr(market,'history_for_instruments',None)
    needs_daily=include_default and plan.default in ('W','M')
    missing=[symbol for symbol in symbols if (require_history and symbol not in (history_cached_symbols or set())) or any(value is None for value in ready[symbol].values()) or needs_daily and not callable(daily_method)]
    # Ordinary D/W/M proofs and rule-specific structure proofs have separate
    # lifetimes. A new BOLL parameter or history window may miss its proof
    # without invalidating the already verified quotation/adjustment seed.
    basis_keys={};restored={}
    if price_basis=='forward' and missing:
        from app.services import condition_basis_cache as basis_cache
        for symbol in missing:
            key=basis_cache.key_for(symbol,day,generation,source_token)
            basis_keys[symbol]=key
            history_ready=not require_history or symbol in (history_cached_symbols or set())
            can_project=(history_ready and (not needs_daily or callable(daily_method))
                and all(ordinary_metrics_ready(day,period,instrument_id=symbol,generation=generation,
                    price_context=contexts[symbol]) for period in periods))
            if can_project:
                projected=basis_cache.restore_structure(key)
                if projected is not None:
                    for period in periods:
                        if ready[symbol][period] is None:
                            ready[symbol][period]=complete_from_basis(day,market.calendar(),period,
                                instrument_id=symbol,generation=generation,request=plan.requests[period],price_context=projected)
                    if all(value is not None for value in ready[symbol].values()):continue
            saved=basis_cache.restore(key)
            if saved is not None:
                restored[symbol]=saved
                contexts[symbol]=saved
    missing=[symbol for symbol in missing if ((require_history and symbol not in (history_cached_symbols or set()))
        or any(value is None for value in ready[symbol].values()) or needs_daily and not callable(daily_method))]
    cold=[symbol for symbol in missing if symbol not in restored]
    histories=selection_history_batch(market,cold,day) if cold else {}
    histories.update({symbol:context['raw_rows'] for symbol,context in restored.items()})
    if price_basis=='forward' and cold:
        action_method=getattr(market,'actions_for_instruments',None)
        actions=action_method(cold) if callable(action_method) else None
        status_method=getattr(market,'status',None);snapshot=status_method().get('snapshot_id') if callable(status_method) else None
        expected_snapshot=generation[0] if isinstance(generation,tuple) else generation
        from app.services.selection_compute import prepare_cold
        prepared=(prepare_cold(market,cold,histories,actions,day,market.calendar(),plan,periods,generation,
            source_token,basis_keys,snapshot=snapshot,history_trees=history_trees if require_history else None)
            if not require_history or history_trees else None) or {}
        for symbol in cold:
            if symbol in prepared:
                value=prepared[symbol];contexts[symbol]=value['price_context']
                if value['basis_record'] is not None:basis_cache.remember_encoded(basis_keys[symbol],value['basis_record'])
                for period in periods:
                    ready[symbol][period]=value['metrics'][period]
                    remember_prepared(day,period,value['metrics'][period],instrument_id=symbol,generation=generation,
                        request=plan.requests[period],price_context=contexts[symbol],strict_record=value['strict_records'][period])
                if computed_histories is not None and value['history'] is not None:computed_histories[symbol]=value['history']
                continue
            same_generation=expected_snapshot is None or snapshot==expected_snapshot
            known=same_generation and source_token is not None and isinstance(actions,dict) and symbol in actions and isinstance(actions[symbol],list)
            contexts[symbol]=prepare_condition_prices(histories.get(symbol,[]),actions.get(symbol) if isinstance(actions,dict) else None,
                market.calendar(),day,'forward',instrument_id=symbol,
                actions_coverage={'known':known,'source_token':source_token if same_generation else None,'snapshot_id':snapshot,
                                  'reason':None if same_generation else '行情代际在价格准备期间变化，不能混用公司行动证据'})
            if known:basis_cache.mark_verified_unavailable(contexts[symbol],basis_keys.get(symbol))
            shared_seed=(basis_keys.get(symbol) is not None
                         and (contexts[symbol].get('price_basis')=='forward_event_affine'
                              or contexts[symbol].get('_verified_preparation_source') is not None))
            # A reusable seed keeps both original quotations and their pristine
            # normalization. It is captured before attaching any rule/pool
            # decisions and never changes an earlier run's immutable evidence.
            for lane in ('raw_rows','price_rows') if require_history or shared_seed else ():
                values=contexts[symbol][lane]
                # A quarantined legacy adapter may still carry timestamps or
                # unconverted date aliases. Keep its original rejection path;
                # early normalization must never turn that date into a match.
                if all(isinstance(row.get('date'),str) and len(row['date'])==10 for row in values):
                    contexts[symbol][lane]=prepare_metric_bars(values)
            if shared_seed:
                basis_cache.remember(basis_keys[symbol],contexts[symbol])
    histories={symbol:prepare_metric_bars(values) for symbol,values in histories.items()}
    daily={symbol:values for symbol,values in histories.items()}
    daily_only=[symbol for symbol in symbols if symbol not in histories] if needs_daily else []
    if daily_only:daily.update(daily_method(daily_only,start=day,end=day))
    return histories,ready,daily

@compute_session()
@disk_session()
def run_strategy(sid,payload=None,*,queued_job=None,response_decision=None):
    if response_decision not in (None,'matched','unmatched','unknown'):fail('结果筛选无效')
    payload=payload or {};key=str(payload.get('request_key') or new_id('selection-request'))
    if len(key)>200:fail('request_key过长')
    job_id=queued_job['job_id'] if queued_job else new_id('job');rid=new_id('run');owner=queued_job['lease_owner'] if queued_job else api_lease_owner()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        from .increment_access import require_business_unlocked
        require_business_unlocked(c)
        require_strategy_present(c,sid)
        found=c.execute('SELECT r.run_id,r.strategy_id FROM selection_run r JOIN job j USING(job_id) WHERE j.request_key=?',(key,)).fetchone()
        if found:
            if found['strategy_id']!=sid:fail('request_key已用于其他策略，不能复用。',409)
            return get_run(found['run_id'],response_decision)
        day=current(c);st=ensure_evaluator_version(c,sid)
        if st['archived']:fail('策略已归档，不能运行')
        deps,primary=freeze_inputs(c);topics=topic_map(c,day);topic_dependencies_=topic_dependencies(st['definition']);complete_topics=complete_topic_ids(c,topic_dependencies_)
        from .strategy_batches import _topic_revision_signature
        membership_signature=_topic_revision_signature(c) if st.get('hot_universe') else None
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
    next_compute_check=0.0
    def check_compute_task():
        nonlocal next_compute_check
        tick=time.monotonic()
        if tick>=next_compute_check:
            check_task();next_compute_check=tick+.2
    current_session().check_cancel=check_compute_task
    try:
        report('准备个股策略数据',0,'读取已发布行情、交易日历和策略依赖')
        market=get_market();calendar=market.calendar();master=identities(day);result=[];required=names(st['definition'])
        hot_evidence=None;hot_by_stock={}
        if st.get('hot_universe'):
            report('核验近期热点',0,'按留存收盘涨停及当前成员核验窗口排名；缺口保持待核验')
            hot_evidence,hot_by_stock=hot_universe.freeze(st['hot_universe'],expected_day=day)
            check_task()
        current_required=current_names(st['definition']);event_nodes=history_nodes(st['definition'])
        # Structures require exact calendar-defined OHLC windows; the dated
        # display panel intentionally does not carry these strict summaries.
        price_basis=st.get('price_basis','none')
        fast=price_basis=='none' and st['period']=='D' and not required&{'higher_lows','boll_signal'}
        metric_plan=compile_metric_plan(st['definition'],st['period'])
        candidate_fields=candidate_requirements(st['definition'])
        status_method=getattr(market,'status',None)
        generation=status_method().get('snapshot_id') if callable(status_method) else None
        verify_sources=getattr(market,'verify_condition_sources',None)
        source_token=verify_sources() if callable(verify_sources) else None
        if price_basis=='forward':
            from app.domain.strategy_prices import compact_source_token
            source_token=compact_source_token(source_token)
        summary_source=(source_token['verified_closure_sha256'] if price_basis=='forward' and source_token is not None else source_token)
        summary_generation=(generation,summary_source) if generation and summary_source is not None else generation
        # Legacy adapters still verify their sources through the original
        # bounded history read. Only a verified immutable closure can skip it.
        probe_generation=summary_generation if source_token is not None else None
        extra_periods=set(metric_plan.periods)-{st['period']}
        panel_rows={x['instrument_id']:x for x in market_panel(day)} if fast or ('limit_up' in required and price_basis!='forward') else {}
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
        ordered=sorted(master);event_contexts={};metric_batch=FORWARD_SELECTION_BATCH_SIZE if price_basis=='forward' and event_nodes else 32
        prefetched_quotes=(not fast and price_basis=='forward' and source_token is not None
            and limit_evidence is not None and not needs_count and callable(getattr(market,'history_for_instruments',None)))
        if prefetched_quotes:
            for offset in range(0,len(ordered),256):
                check_task();limit_quotes.update(batch_quote_indexes(market,ordered[offset:offset+256],day,calendar))
        history_batch=(history_batch_size([st['definition']]) if event_nodes else 256) if fast else metric_batch
        report('核验个股条件',0,f'准备核验 {len(ordered)} 只证券',{'actual_completed_batches':0,'actual_total_batches':len(ordered)})
        history_shared=prepare_shared(market,ordered,[st['definition']],calendar,day,**({'price_basis':price_basis} if price_basis=='forward' else {})) if event_nodes else None
        batch_bars={};batch_ready={};batch_daily={};batch_price_contexts={};batch_history_contexts={}
        for sequence,ident in enumerate(ordered):
            if sequence%128==0:check_task()
            if (not fast or extra_periods) and sequence%metric_batch==0:
                selected=ordered[sequence:sequence+metric_batch]
                batch_price_contexts={}
                batch_history_contexts={}
                computed_histories={}
                if price_basis=='forward' and event_nodes:
                    from app.services.history_events import cached_forward_contexts
                    batch_history_contexts=cached_forward_contexts(selected,[st['definition']],calendar,day,generation,source_token)
                batch_bars,batch_ready,batch_daily=selection_metric_batch(market,selected,day,metric_plan,probe_generation,include_default=not fast,require_history=bool(event_nodes and history_shared is None and not fast),
                    **({'price_basis':price_basis,'source_token':source_token,'price_contexts':batch_price_contexts,'history_cached_symbols':set(batch_history_contexts),
                        'history_trees':[st['definition']],'computed_histories':computed_histories} if price_basis=='forward' else {}))
                if limit_evidence and not needs_count and not prefetched_quotes:
                    limit_quotes=batch_quote_indexes(market,selected,day,calendar,batch_bars if len(batch_bars)==len(selected) else None)
            if event_nodes and sequence%history_batch==0:
                event_contexts=contexts_for_batch(market,ordered[sequence:sequence+history_batch],[st['definition']],calendar,day,batch_bars if not fast else None,shared=history_shared,limit_evidence=limit_evidence,
                    **({'price_basis':price_basis,'price_contexts':batch_price_contexts,'prepared_contexts':batch_history_contexts,'computed_contexts':computed_histories} if price_basis=='forward' else {}))
            item=master[ident]
            if item.get('kind',item.get('asset_type','stock')) not in ('stock','equity','a_share','A'):continue
            topic_ids=[x['topic_id'] for x in topics.get(ident,[])]
            if fast:ms=metrics_from_enriched(panel_rows.get(ident),previous_panel.get(ident),topics=topic_ids)
            else:
                ms=batch_ready.get(ident,{}).get(st['period'])
                if ms is None:ms=cached_metrics(batch_bars.get(ident,[]),day,calendar,st['period'],instrument_id=ident,generation=summary_generation,request=metric_plan.requests[st['period']],
                    **({'price_context':batch_price_contexts.get(ident)} if price_basis=='forward' else {}))
                ms['topic_ids']=topic_ids
            if breakout_values is not None:ms.update(breakout_values.get(ident,{'breakout_valid':False}))
            ms.update(identity_flags(item,day))
            ms['indicator_period']=st['period']
            if extra_periods:
                ms=add_period_metrics(ms,batch_bars.get(ident,[]),day,calendar,st['definition'],st['period'],
                                      instrument_id=ident,generation=summary_generation,plan=metric_plan,ready=batch_ready.get(ident),
                                      **({'price_context':batch_price_contexts.get(ident)} if price_basis=='forward' else {}))
            if topic_dependencies_:ms['_complete_topic_ids']=complete_topics
            if needs_count:
                ms.update(count_values[ident])
                if 'limit_up_1y' in required:ms.update(past_limit_up_metrics(count_values[ident]))
            elif limit_evidence:ms.update(limit_evidence.metrics(ident,day,calendar,limit_quotes.get(ident,{})))
            context=event_contexts.get(ident)
            check=evaluate(st['definition'],ms,context.at(day) if context else None)
            if hot_evidence is not None:check=hot_universe.combine(check,ident,hot_evidence,hot_by_stock)
            decision='matched' if check['result'] is True else 'unmatched' if check['result'] is False else 'unknown'
            entry_close=ms.get('close')
            if st['period'] in ('W','M'):
                daily=next((bar for bar in reversed(batch_daily.get(ident,[])) if str(bar.get('date',bar.get('trade_date','')))[:10]==day),None)
                entry_close=float(daily['close']) if daily and daily.get('close') is not None else None
                ms.update(signal_date=ms.get('observed_date'),signal_close=ms.get('close'),current_daily_close=entry_close,entry_price_policy='business_day_close-v1')
            stored_metrics,stored_checks,stored_topics=compact_candidate(ms,check,topics.get(ident,[]),decision,st['definition'],requirements=candidate_fields)
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
            if membership_signature is not None and _topic_revision_signature(c)!=membership_signature:fail('本地题材或成员已变化，本次未保存混合热点范围；请重新运行策略。',409)
            universe_definition={'scope':'currently_imported_stock_master','count':len(result),'survivorship_warning':True,'candidate_storage_contract':'sparse-nonmatches-v1'}
            from app.domain.strategy_prices import VERSION as PRICE_CONTRACT
            universe_definition.update(requested_price_basis=price_basis,price_contract=PRICE_CONTRACT)
            if hot_evidence is not None:universe_definition['hot_universe']=hot_evidence
            signature=[deps,st['definition'],day]+([hot_evidence] if hot_evidence is not None else [])
            signature+=[{'price_basis':price_basis,'price_contract':PRICE_CONTRACT}]
            insert(c,'selection_run',dict(run_id=rid,job_id=job_id,strategy_id=sid,strategy_version=st['version'],business_date=day,primary_snapshot_id=primary,input_snapshots_json=js(deps),input_sha256=digest(signature),status='running',universe_definition_json=js(universe_definition),created_at_ms=now_ms()))
            for row in result:insert(c,'selection_candidate',row)
            c.execute("UPDATE selection_run SET status='completed',completed_at_ms=? WHERE run_id=?",(now_ms(),rid))
            for snap in set(deps.values()):pin(c,snap,'selection',rid)
            c.execute("UPDATE job SET status='succeeded',phase='completed',finished_at_ms=?,accepted_batches=100,planned_batches=100,lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?",(now_ms(),job_id))
            counts={k:sum(r['decision']==k for r in result) for k in ('matched','unmatched','unknown')};log(c,job_id,'info','completed','策略运行完成',counts)
        return get_run(rid,response_decision)
    except Exception as e:
        with connect() as c:
            c.execute("UPDATE job SET status='failed',phase='failed',finished_at_ms=?,error_summary=?,lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=? AND status='running' AND lease_owner=?",(now_ms(),str(getattr(e,'detail',e))[:500],job_id,owner))
            if c.execute('SELECT status FROM job WHERE job_id=?',(job_id,)).fetchone()[0]=='failed':log(c,job_id,'error','failed','策略运行失败',{'message':str(getattr(e,'detail',e))[:500]})
        raise
    finally:
        stop_lease()

def get_run(rid,decision=None):
    if decision not in (None,'matched','unmatched','unknown'):fail('结果筛选无效')
    with connect() as c:
        run=one(c,'SELECT r.*,s.name strategy_name,v.price_basis requested_price_basis FROM selection_run r JOIN strategy s USING(strategy_id) JOIN strategy_version v ON v.strategy_id=r.strategy_id AND v.version=r.strategy_version WHERE run_id=?',(rid,),'策略运行不存在')
        counts={r['decision']:r['n'] for r in rows(c,'SELECT decision,count(*) n FROM selection_candidate WHERE run_id=? GROUP BY decision',(rid,))}
        selected=rows(c,'SELECT * FROM selection_candidate WHERE run_id=?'+(' AND decision=?' if decision is not None else '')+' ORDER BY decision,instrument_id',(rid,decision) if decision is not None else (rid,))
        master=identities(run['business_date'])
        return {k:v for k,v in run.items() if not k.endswith('_json')}|{'input_snapshots':obj(run['input_snapshots_json'],{}),'universe_definition':obj(run['universe_definition_json'],{}),'matched_count':counts.get('matched',0),'unmatched_count':counts.get('unmatched',0),'unknown_count':counts.get('unknown',0),'items':[_candidate(r,master) for r in selected]}

def list_runs(sid=None,limit=50):
    with connect() as c:
        sql='SELECT r.run_id,r.strategy_id,r.strategy_version,r.business_date,r.status,r.created_at_ms,s.name strategy_name,v.price_basis requested_price_basis FROM selection_run r JOIN strategy s USING(strategy_id) JOIN strategy_version v ON v.strategy_id=r.strategy_id AND v.version=r.strategy_version';params=[]
        if sid:sql+=' WHERE r.strategy_id=?';params.append(sid)
        sql+=' ORDER BY r.created_at_ms DESC,r.rowid DESC LIMIT ?';params.append(min(max(limit,1),200));data=rows(c,sql,params)
        for r in data:r['counts']={x['decision']:x['n'] for x in rows(c,'SELECT decision,count(*) n FROM selection_candidate WHERE run_id=? GROUP BY decision',(r['run_id'],))}
        return {'items':data,'total':len(data)}
