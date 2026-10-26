"""Explicit local line rules, immutable trigger receipts and atomic auto-collection.

GETs read SQLite only. User saves, manual checks and newly authorized syncs may
queue checks; no scheduler, remote requests or historical strategy backfills.
"""
from datetime import date
import json
import logging
import math

from fastapi import HTTPException
from app.core import connect, get_market, now_ms, new_id, digest, dumps
from app.services import task_service as tasks
from app.services.workspace_common import current, e8, pin
from app.analytics.adjustments import forward_prices, finite_number

OPERATION = 'line_alert_check'
VERSION = 'explicit-local-line-alert-v1'
MODES = {'support': {'support_reclaim','support_breakdown'},
         'resistance': {'pressure_close_breakout','pressure_touch'}, 'reference': set()}
TITLES = {'support_reclaim':'回踩支撑后站回', 'support_breakdown':'支撑失守',
          'pressure_close_breakout':'收盘突破压力', 'pressure_touch':'触及压力位'}
BATCH_SIZE = 128
READ_SESSION_WINDOW = 20


class StaleCheck(Exception):
    pass


def _date(value):
    try: return isinstance(value,str) and date.fromisoformat(value).isoformat()==value
    except ValueError: return False


def annotation_view(row):
    result = dict(row)
    result['alert_modes'] = json.loads(result.pop('alert_modes_json','[]'))
    result['auto_watchlist'] = bool(result.get('auto_watchlist',False))
    result['origin'] = json.loads(result.pop('origin_json','{}'))
    result.pop('origin_key', None)
    result.pop('instrument_kind', None)
    return result


def active_rules(connection):
    return [annotation_view(row) for row in connection.execute("SELECT * FROM chart_annotation WHERE instrument_kind='stock' AND line_type!='reference' AND json_array_length(alert_modes_json)>0 ORDER BY annotation_id")]


def _signature(rules):
    return digest([{key: rule.get(key) for key in ('annotation_id','revision','instrument_id','price','price_basis','anchor_date','line_type','alert_modes','auto_watchlist','effective_business_date')} for rule in rules])


def _rule_clock(connection):
    row=connection.execute('SELECT revision FROM line_alert_rule_clock WHERE singleton=1').fetchone()
    return row[0] if row else 0


def _head(connection):
    row = connection.execute("SELECT s.snapshot_id,s.lineage_json FROM published_head h JOIN dataset_snapshot s USING(snapshot_id) WHERE h.module='daily_raw' ORDER BY h.changed_at_ms DESC LIMIT 1").fetchone()
    if not row: raise HTTPException(409,'请先发布真实行情再核验划线提醒')
    return {'primary_snapshot_id':row['snapshot_id'],'market_snapshot_id':json.loads(row['lineage_json']).get('market_snapshot_id') or row['snapshot_id']}


def enqueue(connection, request_key=None, origin='manual', *, no_rules_ok=False):
    """Caller owns BEGIN IMMEDIATE, so save and queue cannot fall between commits."""
    key = request_key or new_id('line-check-request')
    if not isinstance(key,str) or not 1<=len(key)<=200: raise HTTPException(422,'请求标识需要1至200字符')
    old = connection.execute('SELECT * FROM job WHERE request_key=?',(key,)).fetchone()
    if old:
        if json.loads(old['parameters_json']).get('operation')!=OPERATION: raise HTTPException(409,'请求标识已用于其他任务')
        return old['job_id']
    rules = active_rules(connection)
    if not rules and no_rules_ok:return None
    head = _head(connection); day = current(connection)
    for rule in rules:
        rule['start_dates'] = {}
        for mode in rule['alert_modes']:
            last = connection.execute("SELECT max(trade_date) FROM line_alert_evaluation WHERE annotation_id=? AND annotation_revision=? AND rule=? AND state!='unknown'",(rule['annotation_id'],rule['revision'],mode)).fetchone()[0]
            unresolved = connection.execute("SELECT min(a.trade_date) FROM line_alert_evaluation a WHERE a.annotation_id=? AND a.annotation_revision=? AND a.rule=? AND a.state='unknown' AND NOT EXISTS(SELECT 1 FROM line_alert_evaluation b WHERE b.annotation_id=a.annotation_id AND b.annotation_revision=a.annotation_revision AND b.rule=a.rule AND b.trade_date=a.trade_date AND b.state!='unknown')",(rule['annotation_id'],rule['revision'],mode)).fetchone()[0]
            rule['start_dates'][mode] = max(rule.get('effective_business_date') or day,min(value for value in (last or rule.get('effective_business_date') or day,unresolved or day)))
    parameters = {'operation':OPERATION,'version':VERSION,'origin':origin,'business_date':day,**head,
                  'rules_signature':_signature(rules),'rules_clock':_rule_clock(connection),'rules':rules,
                  'input_snapshots':{record['module']:record['snapshot_id'] for record in connection.execute('SELECT module,snapshot_id FROM published_head ORDER BY module,scope_key')}}
    logical = digest({'operation':OPERATION,'date':day,**head,'rules_signature':parameters['rules_signature']})
    existing = connection.execute("SELECT job_id FROM job WHERE logical_key=? AND status IN ('queued','running','paused')",(logical,)).fetchone()
    if existing:return existing[0]
    identifier = new_id('job')
    connection.execute("INSERT INTO job(job_id,request_key,logical_key,kind,target_date,parameters_json,parameters_hash,status,planned_batches,created_at_ms) VALUES(?,?,?,'source_check',?,?,?,'queued',100,?)",
        (identifier,key,logical,day,dumps(parameters),digest(parameters),now_ms()))
    tasks.append_event(connection,identifier,'queued','已保存本地划线核验任务；只读取已发布行情，不请求远端',details={'actual_completed_batches':0,'actual_total_batches':sum(len(rule['alert_modes']) for rule in rules)})
    return identifier


def create_check(payload=None):
    if not isinstance(payload,dict) or set(payload)-{'request_key'}: raise HTTPException(422,'划线核验只接受请求标识')
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE'); identifier=enqueue(connection,payload.get('request_key'))
    return tasks.task_view(identifier)


def _guard(connection,job,parameters):
    row=connection.execute('SELECT status,lease_owner FROM job WHERE job_id=?',(job['job_id'],)).fetchone()
    if not row or row['status']!='running' or row['lease_owner']!=job['lease_owner']:raise tasks.TaskCancelled()
    if current(connection)!=parameters['business_date'] or _head(connection)!={key:parameters[key] for key in ('primary_snapshot_id','market_snapshot_id')}:
        raise StaleCheck('已发布行情已更新，此旧核验不继续提交')
    if _rule_clock(connection)!=parameters['rules_clock']:raise StaleCheck('划线规则已修改或删除，此旧版本不继续提交')


def _valid_quote(row):
    try:
        return (all(not isinstance(row.get(key),bool) and isinstance(row.get(key),(int,float)) and math.isfinite(row[key]) and row[key]>0 for key in ('open','high','low','close','volume'))
                and row['low']<=min(row['open'],row['close'])<=max(row['open'],row['close'])<=row['high']<92233720368)
    except (TypeError,OverflowError):return False


def _events(actions,start,end):
    if not isinstance(actions,list):raise ValueError('公司行动证据缺失，不能转换固定复权锚')
    selected=[];seen=set()
    for action in actions:
        day=action.get('date') if isinstance(action,dict) else None
        if not _date(day):raise ValueError('公司行动日期未核验')
        if not start<day<=end:continue
        if day in seen:raise ValueError('同日多条公司行动未经合并核验')
        seen.add(day)
        values=[finite_number(action.get(key)) for key in ('dividend_per_share','per_share_bonus','allotment_ratio','allotment_price')]
        if min(values)<0 or action.get('currency','CNY')!='CNY':raise ValueError('公司行动字段或币种未核验')
        selected.append({'date':day,'cash':values[0],'bonus':values[1],'rights':values[2],'rights_price':values[3]})
    return sorted(selected,key=lambda row:row['date'])


def _affine(price,start,end,events):
    if start>end:raise ValueError('行情观察日早于固定复权锚')
    bar={'date':start,**{key:price for key in ('open','high','low','close')}}
    value=forward_prices([bar],{'usable':True,'anchor':end,'events':events},'event_affine')[0]['close']
    if not math.isfinite(value) or value<=0:raise ValueError('固定锚转换后价格无效')
    return value


def evaluate_rule(line, mode, day, previous_day, histories, actions):
    if not line.get('effective_business_date') or day<line['effective_business_date']:
        return None,'规则尚未在该交易日生效',None
    today=[row for row in histories if row.get('date')==day]
    if len(today)!=1 or not _valid_quote(today[0]):return None,'观察日缺少唯一有效成交报价',None
    raw=today[0]; previous=[row for row in histories if row.get('date')==previous_day]
    previous_close=None
    if mode!='pressure_touch':
        if len(previous)!=1 or not _valid_quote(previous[0]):return None,'前一市场交易日缺少唯一有效成交报价，不能回退最近有报价日',None
        previous_close=previous[0]['close']
    price=line['price'];proof={'model':'raw-threshold','market_day':day,'action_events':[]}
    try:
        if line['price_basis']=='forward_event_affine':
            events=_events(actions,min(line['anchor_date'],previous_day or day),day)
            price=_affine(price,line['anchor_date'],day,events)
            if previous_close is not None:previous_close=_affine(previous_close,previous_day,day,events)
            proof={'model':'event_affine','fixed_anchor':line['anchor_date'],'comparison_anchor':day,'action_events':events}
        elif previous_close is not None:
            events=_events(actions,previous_day,day)
            if events:raise ValueError('两日间存在公司行动，未复权水平线不能混比前收盘')
    except (ValueError,TypeError,KeyError,OverflowError) as error:return None,str(error),None
    hit={'pressure_touch':raw['low']<=price<=raw['high'],
         'pressure_close_breakout':previous_close is not None and previous_close<=price<raw['close'],
         'support_reclaim':previous_close is not None and previous_close>=price and raw['low']<=price<=raw['close'],
         'support_breakdown':previous_close is not None and previous_close>=price>raw['close']}[mode]
    evidence={'line_price':line['price'],'comparison_line_price':price,'price_basis':line['price_basis'],'anchor_date':line['anchor_date'],
              **{key:raw[key] for key in ('open','high','low','close')},'previous_close':previous_close,
              'raw_previous_close':previous[0]['close'] if len(previous)==1 else None,'comparison_evidence':proof}
    return hit,None,evidence


def _auto_add(connection,line,day,raw_close,generation, *, eligible=True):
    if connection.execute('SELECT 1 FROM watchlist_item WHERE instrument_id=?',(line['instrument_id'],)).fetchone():return False,'already_present'
    if not eligible:return False,'not_eligible'
    if not line['auto_watchlist']:return False,'disabled'
    if raw_close is None:return False,'missing_current_quote'
    from app.services.watchlist import MAX_ITEMS,ENTRY_BASELINE_METHOD
    if connection.execute('SELECT count(*) FROM watchlist_item').fetchone()[0]>=MAX_ITEMS:return False,'capacity_reached'
    price=e8(raw_close)
    if not price or price<=0:raise ValueError('触发日原始收盘价不能用作自选基准')
    basis={'date':day,'price_basis':'none','market_snapshot_id':generation,'state':'ready','reason':None,
           'captured_at_ms':now_ms(),'method':ENTRY_BASELINE_METHOD,'source':'line_alert'}
    connection.execute("INSERT INTO watchlist_item(instrument_id,source_kind,source_id,source_name,observed_date,added_business_date,event_basis_json,created_at_ms,watchlist_entry_price_e8,watchlist_entry_basis_json) VALUES(?,'stock',?,'划线提醒',?,?,'{}',?,?,?)",
        (line['instrument_id'],line['annotation_id'],day,day,now_ms(),price,dumps(basis)))
    return True,'added'


def execute_check(job):
    parameters=json.loads(job['parameters_json']);target=parameters['business_date'];rules=parameters['rules']
    try:
        with connect() as connection:_guard(connection,job,parameters)
        market=get_market();status=market.status()
        if status.get('snapshot_id')!=parameters['market_snapshot_id']:raise StaleCheck('行情实例代际与冻结任务不符')
        calendar=market.calendar()
        if target not in calendar:raise ValueError('已发布业务日不在真实交易日历内')
        reader=getattr(market,'history_for_instruments',None);action_reader=getattr(market,'actions_for_instruments',None)
        master={row['instrument_id']:row for row in market.instruments(limit=10000)}
        total=sum(sum(start<=day<=target for day in calendar) for line in rules for start in line['start_dates'].values())
        processed=0
        for offset in range(0,len(rules),BATCH_SIZE):
            batch=rules[offset:offset+BATCH_SIZE];symbols=sorted({line['instrument_id'] for line in batch})
            current_quotes=reader(symbols,start=target,end=target) if callable(reader) else {}
            current_closes={symbol:next((row['close'] for row in quotes if row.get('date')==target and _valid_quote(row)),None) if len([row for row in quotes if row.get('date')==target])==1 else None for symbol,quotes in current_quotes.items()}
            actions=action_reader(symbols) if callable(action_reader) else None
            first=min((start for line in batch for start in line['start_dates'].values()),default=target)
            sessions=[(position,day) for position,day in enumerate(calendar) if first<=day<=target]
            for window_start in range(0,len(sessions),READ_SESSION_WINDOW):
                window=sessions[window_start:window_start+READ_SESSION_WINDOW]
                first_position=window[0][0];read_start=calendar[first_position-1] if first_position>0 else window[0][1]
                with connect() as connection:_guard(connection,job,parameters)
                histories=reader(symbols,start=read_start,end=window[-1][1]) if callable(reader) else {}
                for position,day in window:
                    previous=calendar[position-1] if position>0 else None
                    for line in batch:
                        for mode in line['alert_modes']:
                            if day<line['start_dates'][mode]:continue
                            processed+=1
                            with connect() as connection:
                                _guard(connection,job,parameters)
                                saved=connection.execute('SELECT 1 FROM line_alert_evaluation WHERE job_id=? AND annotation_id=? AND annotation_revision=? AND rule=? AND trade_date=?',(job['job_id'],line['annotation_id'],line['revision'],mode,day)).fetchone()
                            if saved:continue
                            hit,reason,evidence=evaluate_rule(line,mode,day,previous,histories.get(line['instrument_id'],[]),actions.get(line['instrument_id']) if isinstance(actions,dict) else None)
                            with connect() as connection:
                                connection.execute('BEGIN IMMEDIATE');_guard(connection,job,parameters)
                                event_id=None;state='unknown' if hit is None else 'not_triggered'
                                if hit:
                                    state='invalidated' if mode=='support_breakdown' else 'triggered'
                                    existing=connection.execute('SELECT event_id FROM line_alert_event WHERE annotation_id=? AND annotation_revision=? AND rule=? AND trade_date=?',(line['annotation_id'],line['revision'],mode,day)).fetchone()
                                    if existing:event_id=existing[0]
                                    else:
                                        event_id=new_id('line-event');added,add_state=_auto_add(connection,line,target,current_closes.get(line['instrument_id']),parameters['market_snapshot_id'],eligible=state=='triggered')
                                        identity=master.get(line['instrument_id'],{});title=TITLES[mode]
                                        explanation='收盘低于支撑，不视为回踩收回，不自动新增自选' if state=='invalidated' else '按已发布完整日K核验'
                                        if add_state=='capacity_reached':explanation+='；自选容量已满，未自动加入'
                                        if add_state=='missing_current_quote':explanation+='；当前业务日收盘缺失，未自动加入'
                                        event={**evidence,'event_id':event_id,'instrument_id':line['instrument_id'],'code':line['instrument_id'][:6],
                                            'name':identity.get('name') or line['instrument_id'],'annotation_id':line['annotation_id'],'annotation_revision':line['revision'],
                                        'line_type':line['line_type'],'annotation_label':line.get('label',''),'rule':mode,'trade_date':day,'state':state,'title':title,
                                            'reason':explanation,'created_at_ms':now_ms(),'added_to_watchlist':added,'watchlist_add_state':add_state,
                                            'watchlist_added_business_date':target if added else None,'market_snapshot_id':parameters['market_snapshot_id'],
                                            'input_snapshots':parameters['input_snapshots']}
                                        connection.execute('INSERT INTO line_alert_event VALUES(?,?,?,?,?,?,?,?,?,?,?)',(event_id,line['annotation_id'],line['revision'],line['instrument_id'],line['line_type'],mode,day,state,job['job_id'],dumps(event),event['created_at_ms']))
                                        for snapshot in set(parameters['input_snapshots'].values()):pin(connection,snapshot,'manual','line-alert:'+event_id)
                                connection.execute('INSERT INTO line_alert_evaluation VALUES(?,?,?,?,?,?,?,?,?)',(job['job_id'],line['annotation_id'],line['revision'],mode,day,state,reason,event_id,now_ms()))
                            tasks.progress(job['job_id'],'核验本地划线',min(99,round(processed/max(1,total)*99)),details={'actual_completed_batches':processed,'actual_total_batches':total},lease_owner=job['lease_owner'])
        with connect() as connection:
            connection.execute('BEGIN IMMEDIATE');_guard(connection,job,parameters)
            counts=dict(connection.execute("SELECT count(*) checked,sum(state IN ('triggered','invalidated')) triggered,sum(state='unknown') unknown FROM line_alert_evaluation WHERE job_id=?",(job['job_id'],)).fetchone())
            connection.execute('INSERT OR REPLACE INTO line_alert_check VALUES(?,?,?,?,?,?,?,?)',(job['job_id'],target,parameters['market_snapshot_id'],parameters['rules_signature'],counts['checked'],counts['triggered'] or 0,counts['unknown'] or 0,now_ms()))
        tasks.finish(job['job_id'],'succeeded','划线提醒核验完成；命中及失守分别留存，缺数保持待核验',lease_owner=job['lease_owner'])
    except StaleCheck as error:
        tasks.finish(job['job_id'],'no_change',str(error),lease_owner=job['lease_owner'])
    except Exception as error:
        tasks.finish(job['job_id'],'failed','划线提醒核验未完成',str(getattr(error,'detail',error))[:500],lease_owner=job['lease_owner'])
        raise


def list_alerts(as_of=None,limit=100):
    if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=1000:raise HTTPException(422,'提醒条数需1至1000')
    with connect() as connection:
        clock=connection.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone();day=clock[0] if clock else None
        if as_of is not None and (not _date(as_of) or not day or as_of>day):raise HTTPException(422,'提醒观察日期需要是不晚于业务日的有效日期')
        observed=as_of or day
        items=[json.loads(row[0]) for row in connection.execute('SELECT event_json FROM line_alert_event WHERE trade_date<=? ORDER BY trade_date DESC,created_at_ms DESC,event_id DESC LIMIT ?',(observed or '',limit))]
        total=connection.execute('SELECT count(*) FROM line_alert_event WHERE trade_date<=?',(observed or '',)).fetchone()[0]
        latest=connection.execute('SELECT * FROM line_alert_check WHERE business_date<=? ORDER BY checked_at_ms DESC LIMIT 1',(observed or '',)).fetchone()
        unknowns=[];latest_view=None
        if latest:
            frozen_row=connection.execute('SELECT parameters_json,status FROM job WHERE job_id=?',(latest['job_id'],)).fetchone()
            frozen_parameters=json.loads(frozen_row[0]);frozen={line['annotation_id']:line['instrument_id'] for line in frozen_parameters['rules']}
            latest_view=dict(latest);stale_reason=None
            if frozen_row['status'] not in ('succeeded','partial','no_change'):stale_reason='本次核验任务尚未成功完成'
            elif latest['business_date']!=day:stale_reason='核验后业务行情日已更新'
            elif frozen_parameters['rules_clock']!=_rule_clock(connection):stale_reason='划线规则已修改，等待本次核验'
            elif _head(connection)!={key:frozen_parameters[key] for key in ('primary_snapshot_id','market_snapshot_id')}:stale_reason='核验后行情代际已更新'
            latest_view.update(is_current=stale_reason is None,stale=stale_reason is not None,stale_reason=stale_reason)
            unknowns=[{'instrument_id':frozen.get(row['annotation_id']),'annotation_id':row['annotation_id'],'trade_date':row['trade_date'],'reason':row['reason']} for row in connection.execute("SELECT e.annotation_id,e.reason,e.trade_date,a.instrument_id FROM line_alert_evaluation e LEFT JOIN chart_annotation a USING(annotation_id) WHERE e.job_id=? AND e.state='unknown' ORDER BY e.annotation_id,e.rule",(latest['job_id'],))]
        return {'items':items,'total':total,'business_date':day,'rules_total':sum(len(rule['alert_modes']) for rule in active_rules(connection)),
                'latest_check':latest_view,'unknowns':unknowns}


def latest_by_stock(connection,identifiers,day):
    if not identifiers:return {}
    placeholders=','.join('?' for _ in identifiers)
    rows=connection.execute('SELECT event_json FROM (SELECT event_json,row_number() OVER (PARTITION BY instrument_id ORDER BY trade_date DESC,created_at_ms DESC,event_id DESC) n FROM line_alert_event WHERE trade_date<=? AND instrument_id IN ('+placeholders+')) WHERE n=1',[day,*identifiers])
    return {item['instrument_id']:item for item in (json.loads(row[0]) for row in rows)}


def after_sync(job):
    """Recoverable follow-up only for new, explicitly marked manual sync jobs."""
    parameters=json.loads(job['parameters_json'])
    if job['kind'] not in ('initialise','increment') or parameters.get('line_alert_check_version')!=VERSION:return None
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        saved=connection.execute('SELECT * FROM job WHERE job_id=?',(job['job_id'],)).fetchone()
        if not saved or saved['status'] not in ('succeeded','partial','no_change'):return None
        from .sync_progress import chain_stop_action
        if chain_stop_action(connection,saved):return None
        if connection.execute("SELECT 1 FROM job_event WHERE job_id=? AND stage='line_alert_check_queued'",(job['job_id'],)).fetchone():return None
        publication=connection.execute("SELECT data_end FROM dataset_snapshot WHERE job_id=? AND module='daily_raw' AND state='published' ORDER BY published_at_ms DESC LIMIT 1",(job['job_id'],)).fetchone()
        target=publication[0] if publication else saved['target_date']
        # no_change syncs may have an unspecified request target; their own
        # completed chain_target_frozen receipt already records the exact date.
        if target is None:
            frozen=connection.execute("SELECT details_json FROM job_event WHERE job_id=? AND stage='chain_target_frozen' ORDER BY seq LIMIT 1",(job['job_id'],)).fetchone()
            target=json.loads(frozen[0]).get('target_date') if frozen else None
        if target is None:return None
        if current(connection)!=target:
            tasks.append_event(connection,job['job_id'],'line_alert_check_queued','原同步业务日已被新发布取代，不追溯创建旧核验',details={'skipped':True,'target_date':target})
            return None
        identifier=enqueue(connection,'line-alert-after-sync:'+job['job_id'],'manual-sync',no_rules_ok=True)
        tasks.append_event(connection,job['job_id'],'line_alert_check_queued','已接续本地划线核验；没有启用规则时不创建任务',details={'next_task_id':identifier})
        return identifier


def recover_sync_followups():
    with connect() as connection:
        saved=[dict(row) for row in connection.execute("SELECT * FROM job j WHERE kind IN ('initialise','increment') AND status IN ('succeeded','partial','no_change') AND json_extract(parameters_json,'$.line_alert_check_version')=? AND NOT EXISTS(SELECT 1 FROM job_event e WHERE e.job_id=j.job_id AND e.stage='line_alert_check_queued') ORDER BY created_at_ms LIMIT 20",(VERSION,))]
    for job in saved:
        try:after_sync(job)
        except Exception as error:
            logging.getLogger(__name__).warning('划线核验补漏未完成 %s %s；保留原任务供下次核对',job['job_id'],type(error).__name__)
