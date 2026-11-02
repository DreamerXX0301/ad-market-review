"""AI-control-thread side of streamed agent IO and read-tool handshakes."""
import json
import queue
import re
from app.core import connect, now_ms, new_id, dumps, digest
from app.adapters.ai_agent import AgentBridge, perform_agent
from app.services import ai_service as service, ai_web, ai_scope
from app.services.ai_catalog import estimated_nusd, max_output
from app.services.ai_tool_budget import assess_tool, tool_budget
from app.services.ai_scope_semantic import classify_frozen_read

EVENT_TTL=15*60*1000
MAX_EVENTS=2048
MAX_STREAM_TEXT=64*1024


def append_event(db,task,kind,data):
    row=db.execute('SELECT result_json,session_id FROM ai_task WHERE task_id=?',(task['task_id'],)).fetchone()
    if row is None:return
    metadata=json.loads(row['result_json'] or '{}');seq=metadata.get('stream_last_seq',0)+1
    if seq>MAX_EVENTS:return
    if seq>MAX_EVENTS-2 and kind not in ('done','error'):raise ValueError('stream_limit')
    payload={**data,'task_id':task['task_id'],'session_id':row['session_id'],'seq':seq,'type':kind}
    if len(dumps(payload).encode())>8192:raise ValueError('stream_limit')
    db.execute('INSERT INTO ai_task_event VALUES(?,?,?,?,?)',(task['task_id'],seq,kind,dumps(payload),now_ms()))
    metadata['stream_last_seq']=seq
    db.execute('UPDATE ai_task SET result_json=? WHERE task_id=?',(dumps(metadata),task['task_id']))


def _usage_valid(usage):
    return (all(type(usage.get(k))is int and 0<=usage[k]<=10000000 for k in ('input_tokens','output_tokens'))
            and 0<=(usage.get('cached_tokens') or 0)+(usage.get('cache_write_tokens') or 0)<=usage['input_tokens']
            and 0<=(usage.get('reasoning_tokens') or 0)<=usage['output_tokens'])


def settle_parent(db,task_id):
    rows=[dict(row) for row in db.execute('SELECT * FROM ai_request_receipt WHERE task_id=? ORDER BY ordinal',(task_id,))]
    sent=[row for row in rows if row['sent_at_ms'] is not None]
    known=bool(sent) and all(row['cost_state']=='estimated' and row['cost_nusd'] is not None for row in sent)
    held=sum(row['cost_nusd'] if row['cost_state']=='estimated' and row['cost_nusd'] is not None else row['reservation_nusd'] for row in sent)
    usage={}
    for key in ('input_tokens','cached_tokens','cache_write_tokens','output_tokens','reasoning_tokens'):
        values=[json.loads(row['usage_json']).get(key) for row in sent]
        usage[key]=sum(values) if values and all(type(value)is int for value in values) else None
    db.execute('UPDATE ai_task SET reservation_nusd=?,cost_nusd=?,cost_state=?,usage_json=? WHERE task_id=?',
               (held,held if known else None,'estimated' if known else 'pending' if sent else 'not_sent',dumps(usage),task_id))


def _bind_stock_anchor(db,session_id,result):
    """Establish an unbound new chat's subject from an actual permitted read.

    Historical evidence/protocol and this round's original anchor stay intact.
    Only future rounds use this current page anchor; model prose is never read.
    """
    if (not session_id or result.get('tool_name')!='read_stock_research' or result.get('ok') is not True
            or result.get('state') not in ('available','partial') or not result.get('evidence')
            or any(isinstance(gap,dict) and gap.get('capability')=='research_permission' for gap in result.get('gaps',[]))):return
    summary=result.get('summary') or {}
    if not isinstance(summary,dict):return
    instrument=summary.get('instrument') or {}
    if not isinstance(instrument,dict):return
    identifier=summary.get('instrument_id') or instrument.get('instrument_id')
    if (not isinstance(identifier,str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',identifier)
            or (instrument.get('instrument_id') and instrument['instrument_id']!=identifier)):
        return
    row=db.execute('SELECT instrument_id,context_json,protocol_json FROM ai_session WHERE session_id=?',(session_id,)).fetchone()
    if row is None or row['instrument_id']:return
    context=json.loads(row['context_json']);protocol=json.loads(row['protocol_json'])
    if (protocol.get('version')!='ai-session-binding-v1' or not protocol.get('binding')
            or context.get('kind')!='page_anchor' or context.get('instrument_id')):return
    subject=instrument.get('name')
    context={**context,'instrument_id':identifier,'subject':subject[:100] if isinstance(subject,str) and subject.strip() else identifier,'stock_focus_verified':True}
    db.execute("UPDATE ai_session SET kind='stock',instrument_id=?,context_json=?,context_digest=? WHERE session_id=? AND instrument_id IS NULL",
               (identifier,dumps(context),digest(context),session_id))


class AgentRuntimeMixin:
    def _start_agent(self,task,request,key):
        import threading
        from app.services.ai_agent_protocol import request_time, planner_messages
        from app.services.ai_scope_focus import resolve_scope_focus
        trusted=request.get('scope_input') or {}
        if trusted and not trusted.get('validated_stock_focus') and ai_scope.scope_for(request)['state'] in ('unknown','planning'):
            focus=resolve_scope_focus(trusted.get('current_text',''))
            trusted['validated_stock_focus']=focus
            if focus:
                request['page_anchor']={**request['page_anchor'],'instrument_id':focus['instrument_id'],'subject':focus['name']}
        ai_scope.apply_to_request(request)
        request['submitted_request_time']=request['request_time']
        request['request_time']=request_time(now_ms())
        request['seed_messages'][1]={'role':'user','content':'页面锚点与本次执行时间（仅查询上下文）：\n'+dumps({'page_anchor':request['page_anchor'],'request_time':request['request_time']})}
        if request['round_protocol'].get('version')=='ai-agent-round-v5':
            request['planner_messages']=planner_messages(request)
        with connect() as db:db.execute('UPDATE ai_task SET request_json=? WHERE task_id=?',(dumps(request),task['task_id']))
        bridge=AgentBridge();cancel=threading.Event()
        self.agent_bridge=bridge
        self.agent_state={'text':'','tool_results':[],'tool_traces':[],'page_anchor':request['page_anchor'],
                          'request_time':request['request_time'],'round_protocol':request['round_protocol'],
                          'seed_messages':request['seed_messages'],'tool_schemas':request['tool_schemas'],'tool_calls':[],
                          'local_tool_calls':[]}
        with connect() as db:append_event(db,task,'status',{'state':'running','message':'正在读取问题并准备查询资料'})
        future=self.executor.submit(self.agent_transport,request,key,cancel,bridge)
        self.active=task['task_id'],future,cancel

    def _agent_authorize(self,task,data):
        ordinal=data.get('ordinal');size=data.get('input_bytes');hosted=data.get('hosted_web') is True
        if type(ordinal)is not int or not 1<=ordinal<=3 or type(size)is not int or not 0<size<=96*1024:
            return {'_error':'input_too_large'}
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=dict(db.execute('SELECT * FROM ai_task WHERE task_id=?',(task['task_id'],)).fetchone())
            row,limits=service._provider(db,current['provider_id']),service._limits(db)
            if current['cancel_requested']:return {'_error':'cancelled'}
            if not row['enabled'] or service.config_signature(row,limits)!=current['config_signature']:return {'_error':'configuration_changed'}
            if db.execute('SELECT 1 FROM ai_request_receipt WHERE task_id=? AND ordinal=?',(task['task_id'],ordinal)).fetchone():
                return {'_error':'agent_tool_limit'}
            metadata=json.loads(current['result_json']);rates=metadata['price_rates']
            trusted=json.loads(current['request_json'])
            if hosted and (trusted.get('agent_state') or {}).get('force_summary'):
                return {'_error':'agent_tool_limit'}
            if hosted and (trusted.get('scope_denied') or not ai_scope.authorize_plan(trusted,trusted.get('accepted_web_plan'))):
                return {'_error':'stock_scope_required'}
            reservation=estimated_nusd(current['provider_id'],current['model'],size,max_output(current['provider_id']),rates=rates)
            price={'price_rates':rates,'price_version':metadata['price_version']}
            if hosted:
                if not ai_web.supported(current['provider_id'],current['model']):return {'_error':'web_search_unavailable'}
                previous=[json.loads(row[0]) for row in db.execute('SELECT price_json FROM ai_request_receipt WHERE task_id=?',(task['task_id'],))]
                if any(value.get('web_policy') for value in previous):return {'_error':'agent_tool_limit'}
                policy=json.loads(current['request_json'])['round_protocol']['web_policy']
                reservation+=ai_web.extra_reservation_nusd(rates,policy);price['web_policy']=policy
            day=service.day_key();budget=service.budget_state(db,day,exclude_pending_parent=task['task_id'])
            if budget['requests']+1>limits['daily_request_limit'] or budget['amount']+reservation>limits['daily_budget_nusd']:
                return {'_error':'budget_exceeded'}
            stamp=now_ms();request_id=new_id('ai-request')
            db.execute('INSERT INTO ai_request_receipt(request_id,task_id,ordinal,day,provider_id,model,state,reservation_nusd,cost_state,price_json,created_at_ms,sent_at_ms) VALUES(?,?,?,?,?,?,\'sent\',?,\'pending\',?,?,?)',
                       (request_id,task['task_id'],ordinal,day,current['provider_id'],current['model'],reservation,dumps(price),stamp,stamp))
            db.execute("UPDATE ai_task SET sent_at_ms=COALESCE(sent_at_ms,?),cost_state='pending' WHERE task_id=?",(stamp,task['task_id']))
            settle_parent(db,task['task_id'])
        return {'request_id':request_id}

    def _agent_receipt(self,task,data):
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM ai_request_receipt WHERE task_id=? AND ordinal=?',(task['task_id'],data.get('ordinal'))).fetchone()
            if row is None:return {'_error':'internal_error'}
            price=json.loads(row['price_json']);usage=data.get('usage') or {};amount=None;state='pending'
            if _usage_valid(usage):
                amount=estimated_nusd(row['provider_id'],row['model'],usage['input_tokens'],usage['output_tokens'],usage.get('cached_tokens') or 0,usage.get('cache_write_tokens') or 0,rates=price['price_rates'])
                state='estimated'
            if price.get('web_policy'):
                accounting=data.get('web_accounting') or {};price['web_accounting']=accounting
                if amount is not None:
                    price['known_base_cost_nusd']=amount
                    count=accounting.get('search_call_count')
                    if (accounting.get('complete') is True and type(count)is int and 0<=count<=1
                            and (accounting.get('used') is False or accounting.get('search_content_in_input') is True)):
                        amount+=count*price['web_policy']['tool_nusd']
                    else:amount,state=None,'pending'
            db.execute('UPDATE ai_request_receipt SET state=?,cost_nusd=?,cost_state=?,usage_json=?,price_json=?,finished_at_ms=?,reason_code=? WHERE request_id=?',
                       ('failed' if data.get('reason_code') else 'completed',amount,state,dumps(usage),dumps(price),now_ms(),data.get('reason_code'),row['request_id']))
            settle_parent(db,task['task_id'])
        return {'ok':True}

    def _agent_tool(self,task,data):
        name=data.get('name');args=data.get('args');call_id=data.get('call_id')
        if not isinstance(name,str) or not isinstance(args,dict):
            return {'_error':'invalid_tool_arguments'}
        with connect() as db:
            raw=db.execute('SELECT request_json FROM ai_task WHERE task_id=?',(task['task_id'],)).fetchone()
        request=json.loads(raw['request_json']) if raw else {}
        try:classify_frozen_read(request,name,args)
        except ValueError:return {'_error':'invalid_tool_arguments'}
        if request.get('scope_denied') or not ai_scope.scope_for(request)['resources_allowed']:return ai_scope.denial()
        executed=self.agent_state.setdefault('local_tool_calls',[])
        try:gap=assess_tool(request['round_protocol'],executed,name,args)
        except ValueError:return {'_error':'invalid_tool_arguments'}
        self.agent_state['tool_calls'].append({'name':name,'args':args,'call_id':call_id})
        if gap is not None:
            result=gap
            self.agent_state['force_summary']=True
        else:
            with connect() as db:append_event(db,task,'tool',{'name':name,'call_id':call_id,'state':'running','label':'正在查询本地资料'})
            from app.services.ai_read_tools import execute_read_tool
            result=execute_read_tool(name,args,page_context=request['page_anchor'],fallback_context=request.get('fallback_context'))
            executed.append({'name':name,'args':args})
            if len(executed)>=tool_budget(request['round_protocol'])['max_reads']:
                self.agent_state['force_summary']=True
        if len(dumps(result).encode())>24576:return {'_error':'stream_limit'}
        self.agent_state['tool_results'].append(result)
        state=result.get('state','unknown');date=result.get('date')
        label='本轮该项未读取，正在整理已取得资料' if gap is not None else '已读取本地资料' if result.get('ok') else '本地查询已返回缺口'
        trace={'name':name,'call_id':call_id,'state':state,'date':date,'summary':label}
        self.agent_state['tool_traces'].append(trace)
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT cancel_requested,session_id FROM ai_task WHERE task_id=?',(task['task_id'],)).fetchone()
            if current is None or current['cancel_requested']:return {'_error':'cancelled'}
            if name=='read_stock_research':_bind_stock_anchor(db,current['session_id'],result)
            append_event(db,task,'tool',{**trace,'label':label})
            request['agent_state']=self.agent_state
            db.execute('UPDATE ai_task SET request_json=? WHERE task_id=?',(dumps(request),task['task_id']))
        return result

    def _agent_stock_plan(self,task,data):
        """Accept the model's domain route before any proposed data is read.

        Routing shares the ordinary first planning request. It is not another
        classifier request and it cannot change concrete tool/cost permissions.
        """
        from app.services import ai_scope_semantic as semantic
        with connect() as db:
            row=db.execute('SELECT request_json,cancel_requested FROM ai_task WHERE task_id=?',(task['task_id'],)).fetchone()
            first=db.execute('SELECT state FROM ai_request_receipt WHERE task_id=? AND ordinal=1',(task['task_id'],)).fetchone()
        if row is None or row['cancel_requested']:return {'_error':'cancelled'}
        request=json.loads(row['request_json'])
        if ((request.get('round_protocol') or {}).get('version')!=semantic.ROUND_VERSION
                or request.get('accepted_stock_plan') is not None or first is None or first['state']!='completed'):
            return {'_error':'invalid_tool_arguments'}
        accepted=semantic.validate_plan(request,data.get('args'))
        if accepted.get('ok') is not True:return accepted
        call_id=data.get('call_id')
        if not isinstance(call_id,str) or not 1<=len(call_id)<=200:return {'_error':'invalid_tool_arguments'}
        request['accepted_stock_plan']=accepted['accepted_plan'];request['semantic_scope']=accepted['scope']
        request['scope']=accepted['scope'];request['agent_state']=self.agent_state
        self.agent_state['tool_calls'].append({'name':'plan_stock_task','args':accepted['accepted_plan'],'call_id':call_id})
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT request_json,cancel_requested FROM ai_task WHERE task_id=?',(task['task_id'],)).fetchone()
            if current is None or current['cancel_requested']:return {'_error':'cancelled'}
            if json.loads(current['request_json']).get('accepted_stock_plan') is not None:return {'_error':'invalid_tool_arguments'}
            db.execute('UPDATE ai_task SET request_json=? WHERE task_id=?',(dumps(request),task['task_id']))
        outputs=[]
        for index,read in enumerate(accepted['reads']):
            nested_id=call_id[:160]+':read:'+str(index+1)
            prior=len(self.agent_state.get('local_tool_calls',[]))
            result=self._agent_tool(task,{'name':read['name'],'args':read['arguments'],'call_id':nested_id})
            if result.get('_error'):return result
            outputs.append({'name':read['name'],'args':read['arguments'],'call_id':nested_id,'result':result,
                            'executed':len(self.agent_state.get('local_tool_calls',[]))>prior})
        web_plan=accepted['web_plan'];web_gap=None
        if web_plan:
            if self.agent_state.get('force_summary'):
                web_gap={'ok':False,'state':'not_read','reason_code':'tool_budget_exhausted',
                         'summary':'本轮未进行网络查询，请依据已取得资料回答。'}
                web_plan=None
            elif not ai_web.supported(task['provider_id'],task['model']):
                web_gap={'ok':False,'state':'not_read','reason_code':'web_search_unavailable',
                         'summary':'当前模型尚未接通原生联网查询，不能据此说本地数据不存在。'}
                web_plan=None
            else:
                request['accepted_web_plan']=web_plan;self.agent_state['web_request']=web_plan
        if web_gap:self.agent_state['tool_results'].append(web_gap)
        # _agent_tool persists its counters/results. Re-read it so the domain
        # plan handshake never overwrites a completed read's state or anchor.
        with connect() as db:
            raw=db.execute('SELECT request_json,cancel_requested FROM ai_task WHERE task_id=?',(task['task_id'],)).fetchone()
            if raw is None or raw['cancel_requested']:return {'_error':'cancelled'}
            updated=json.loads(raw['request_json']);updated['agent_state']=self.agent_state
            if web_plan:updated['accepted_web_plan']=web_plan
            db.execute('UPDATE ai_task SET request_json=? WHERE task_id=?',(dumps(updated),task['task_id']))
        return {'ok':True,'scope':accepted['scope'],'tool_results':outputs,'web_plan':web_plan,
                'web_gap':web_gap,'reply':accepted['reply'],'force_summary':bool(self.agent_state.get('force_summary'))}

    def _drain_agent(self,task_id):
        if self.agent_bridge is None:return
        with connect() as db:
            raw=db.execute('SELECT * FROM ai_task WHERE task_id=?',(task_id,)).fetchone()
        if raw is None:return
        task=dict(raw)
        for _ in range(128):
            try:kind,data,reply=self.agent_bridge.queue.get_nowait()
            except queue.Empty:break
            try:
                if self.active and self.active[2].is_set() and kind in ('authorize','read_tool','plan_stock'):
                    result={'_error':'cancelled'}
                elif kind=='authorize':result=self._agent_authorize(task,data)
                elif kind=='receipt':result=self._agent_receipt(task,data)
                elif kind=='read_tool':result=self._agent_tool(task,data)
                elif kind=='plan_stock':result=self._agent_stock_plan(task,data)
                elif kind=='plan_web':
                    with connect() as db:
                        raw=db.execute('SELECT request_json FROM ai_task WHERE task_id=?',(task_id,)).fetchone()
                        request=json.loads(raw['request_json']);plan={key:data.get(key) for key in ('query','reason','purpose','user_quote')}
                        permitted=not request.get('scope_denied') and ai_scope.authorize_plan(request,plan)
                        accepted=permitted and not self.agent_state.get('force_summary')
                        if accepted:
                            request['accepted_web_plan']=plan;self.agent_state['web_request']=plan
                            result={'ok':True,'state':'planned'}
                        elif not permitted:
                            request['scope_denied']=True
                            result=ai_scope.denial(local_only=ai_scope.scope_for(request)['local_only'])
                        else:
                            summary='本轮未进行网络查询。请依据已经取得的资料直接回答，并说明仍未取得的具体资料；不要再申请查询。'
                            label='本轮未进行网络查询，正在整理已取得资料'
                            result={'ok':False,'state':'not_read','reason_code':'tool_budget_exhausted',
                                    'tool_name':'request_web_search','date':None,'summary':summary,
                                    'source':[],'evidence':[],'metrics':[],
                                    'gaps':[{'capability':'web_search','state':'not_read','reason':summary}]}
                            trace={'name':'request_web_search','call_id':data.get('call_id'),
                                   'state':'not_read','date':None,'summary':label}
                            self.agent_state['tool_results'].append(result)
                            self.agent_state['tool_traces'].append(trace)
                            append_event(db,task,'tool',{**trace,'label':label})
                        self.agent_state['tool_calls'].append({'name':'request_web_search','call_id':data.get('call_id'),'args':plan,'accepted':accepted})
                        request['agent_state']=self.agent_state
                        db.execute('UPDATE ai_task SET request_json=? WHERE task_id=?',(dumps(request),task_id))
                elif kind in ('delta','status','tool'):
                    if kind=='delta':
                        self.agent_state['text']+=data.get('text','')
                        if len(self.agent_state['text'].encode())>MAX_STREAM_TEXT:raise ValueError('stream_limit')
                    with connect() as db:append_event(db,task,kind,data)
                    result={'ok':True}
                else:result={'_error':'internal_error'}
            except Exception as error:
                code=str(error) if str(error) in service.MESSAGES else 'internal_error'
                result={'_error':code}
                with connect() as db:db.execute('UPDATE ai_task SET cancel_requested=1,reason_code=? WHERE task_id=?',(code,task_id))
                if self.active:self.active[2].set()
            if reply is not None and not reply.done():reply.set_result(result)

    def _finish_agent(self,identifier,future):
        from app.adapters.ai_responses import ProviderError
        reason=None;response={}
        try:response=future.result()
        except ProviderError as error:reason=error.reason_code
        except Exception:reason='internal_error'
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            raw=db.execute('SELECT * FROM ai_task WHERE task_id=?',(identifier,)).fetchone()
            if raw is None:return
            task=dict(raw)
            if task['state']!='running':return
            row,limits=service._provider(db,task['provider_id']),service._limits(db)
            if task['cancel_requested']:reason=task['reason_code'] or 'cancelled'
            elif not row['enabled'] or service.config_signature(row,limits)!=task['config_signature']:reason='configuration_changed'
            state='completed' if reason is None else 'cancelled' if reason=='cancelled' else 'failed'
            output=response.get('output')
            if not isinstance(output,dict) or output.get('response_type')!='agent_answer':
                if reason is None:reason='invalid_output';state='failed'
                source=self.agent_state or {}
                output={'response_type':'agent_answer','text':source.get('text') or service.MESSAGES.get(reason,'本次回答未完成'),
                        'citations':[],'sources':[],'tool_traces':source.get('tool_traces',[]),
                        'source_context':{'page_anchor':source.get('page_anchor',{}),'request_time':source.get('request_time',{}),
                            'tool_results':source.get('tool_results',[]),'web':{'allowed':False,'used':False,'validation':'native_citations_only'}},
                        'round_protocol':source.get('round_protocol',{}),
                        'used_context':{key:source.get(key) for key in ('seed_messages','tool_schemas','tool_calls','web_request')}}
            output['partial']=reason is not None
            if len(dumps(output).encode())>256*1024:
                output={'response_type':'agent_answer','text':(self.agent_state or {}).get('text','')[:8000],
                        'source_context':{'tool_results':[],'gaps':['本轮内容超出保存上限，来源未完整保存']},
                        'tool_traces':[],'citations':[],'sources':[],'partial':True}
                reason,state='stream_limit','failed'
            stamp=now_ms();message_id=new_id('ai-message')
            if task['session_id']:
                db.execute('INSERT INTO ai_message VALUES(?,?,?,?,?,?,?,?)',(message_id,task['session_id'],identifier,'assistant',output['text'],dumps(output),state,stamp))
                db.execute('UPDATE ai_session SET updated_at_ms=?,expires_at_ms=? WHERE session_id=?',(stamp,stamp+service.SESSION_TTL,task['session_id']))
            if reason:
                db.execute("UPDATE ai_request_receipt SET state='failed',finished_at_ms=?,reason_code=? WHERE task_id=? AND state='sent'",(stamp,reason,identifier))
            metadata=json.loads(task['result_json']);metadata['message_id']=message_id
            metadata['execution_request_time']=(self.agent_state or {}).get('request_time')
            db.execute("UPDATE ai_task SET state=?,reason_code=?,finished_at_ms=?,result_json=?,response_model=?,request_json='{}',credential_ref='' WHERE task_id=?",(state,reason,stamp,dumps(metadata),response.get('response_model'),identifier))
            settle_parent(db,identifier)
            if reason:append_event(db,task,'error',{'reason_code':reason,'message':service.MESSAGES.get(reason,'本次回答未完成')})
            append_event(db,task,'done',{'state':state,'message_id':message_id})
        self.agent_bridge=None;self.agent_state=None

    def _recover_agents(self,db):
        for raw in db.execute("SELECT * FROM ai_task WHERE state='running' AND json_extract(result_json,'$.agent')=1").fetchall():
            task=dict(raw);sent=db.execute('SELECT 1 FROM ai_request_receipt WHERE task_id=? AND sent_at_ms IS NOT NULL',(task['task_id'],)).fetchone()
            if not sent:
                db.execute("UPDATE ai_task SET state=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'queued' END,owner=NULL,started_at_ms=NULL WHERE task_id=?",(task['task_id'],));continue
            request=json.loads(task['request_json']);source=request.get('agent_state') or {}
            text=''.join(json.loads(row[0]).get('text','') for row in db.execute("SELECT data_json FROM ai_task_event WHERE task_id=? AND kind='delta' ORDER BY seq",(task['task_id'],)))
            output={'response_type':'agent_answer','text':text or service.MESSAGES['interrupted_manual_retry'],'partial':True,
                    'source_context':{'page_anchor':request.get('page_anchor',{}),'request_time':request.get('request_time',{}),'tool_results':source.get('tool_results',[])},
                    'tool_traces':source.get('tool_traces',[]),'citations':[],'sources':[],'round_protocol':request.get('round_protocol',{}),
                    'used_context':{key:source.get(key,request.get(key)) for key in ('seed_messages','tool_schemas','tool_calls','web_request')}}
            message_id=new_id('ai-message');stamp=now_ms()
            if task['session_id']:db.execute('INSERT INTO ai_message VALUES(?,?,?,?,?,?,?,?)',(message_id,task['session_id'],task['task_id'],'assistant',output['text'],dumps(output),'interrupted_manual_retry',stamp))
            db.execute("UPDATE ai_request_receipt SET state='interrupted',finished_at_ms=?,reason_code='interrupted_manual_retry' WHERE task_id=? AND state='sent'",(stamp,task['task_id']))
            meta=json.loads(task['result_json']);meta['message_id']=message_id
            db.execute("UPDATE ai_task SET state='interrupted_manual_retry',reason_code='interrupted_manual_retry',finished_at_ms=?,result_json=?,request_json='{}',credential_ref='' WHERE task_id=?",(stamp,dumps(meta),task['task_id']))
            settle_parent(db,task['task_id'])
            append_event(db,task,'done',{'state':'interrupted_manual_retry','message_id':message_id})

    def _cleanup_agent_events(self,db):
        db.execute("DELETE FROM ai_task_event WHERE task_id IN (SELECT task_id FROM ai_task t WHERE finished_at_ms<? AND state NOT IN ('queued','running') AND EXISTS(SELECT 1 FROM ai_task_event e WHERE e.task_id=t.task_id) ORDER BY finished_at_ms LIMIT 50)",(now_ms()-EVENT_TTL,))
