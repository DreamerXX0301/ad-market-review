"""Bounded stateless Responses tool loop. IO thread never reads SQLite/files."""
from __future__ import annotations
import asyncio
import codecs
from concurrent.futures import Future
import json
import os
import queue
import time
import uuid
import httpx
from app.adapters.ai_responses import BASE_URLS, ProviderError, error_code, usage_of
from app.services.ai_catalog import MAX_RESPONSE_BYTES, max_output
from app.services import ai_web, ai_scope
from app.services.ai_tool_budget import assess_tool, classify_tool, tool_budget
from app.services.ai_agent_protocol import FINALIZE_SYSTEM_PROMPT, planner_messages
from app.services.ai_scope_semantic import classify_frozen_read

MAX_STREAM_BYTES=4*MAX_RESPONSE_BYTES
MAX_TEXT_BYTES=64*1024

class AgentBridge:
    def __init__(self):
        self.queue=queue.Queue(maxsize=128)
    def emit(self,kind,data):
        try:self.queue.put_nowait((kind,data,None))
        except queue.Full:raise ProviderError('stream_limit') from None
    async def exchange(self,kind,data,cancel):
        response=Future()
        try:self.queue.put_nowait((kind,data,response))
        except queue.Full:raise ProviderError('stream_limit') from None
        while not response.done():
            if cancel.is_set():raise ProviderError('cancelled')
            await asyncio.sleep(.02)
        result=response.result()
        if isinstance(result,dict) and result.get('_error'):
            raise ProviderError(result['_error'])
        return result


def payload_for(request,items,tools):
    payload={'model':request['model'],'input':items,'stream':True,'store':False,
             'reasoning':{'effort':'low' if request['provider_id']=='openai' else 'none'},
             'max_output_tokens':max_output(request['provider_id']),
             'tools':tools,'parallel_tool_calls':request['provider_id']=='openai' and bool(tools) and not any(tool.get('type')=='web_search' for tool in tools)}
    if request['provider_id']=='openai':payload['include']=['reasoning.encrypted_content']
    if any(tool.get('type')=='web_search' for tool in tools):
        payload.update(max_tool_calls=1,tool_choice='required')
        payload.setdefault('include',[]).append('web_search_call.action.sources')
    return payload


async def stream_response(client,path,payload,cancel,bridge,*,emit_text=True):
    body=None;items={};wire=0;buffer='';decoder=codecs.getincrementaldecoder('utf-8')()
    pending='';text='';last_flush=time.monotonic();data_lines=[]
    def flush():
        nonlocal pending,last_flush
        if pending:
            if emit_text:bridge.emit('delta',{'text':pending})
            pending=''
        last_flush=time.monotonic()
    async def pulse():
        while True:
            await asyncio.sleep(.1)
            flush()
    flusher=asyncio.create_task(pulse())
    try:
        async with client.stream('POST',path,json=payload) as response:
            if response.status_code>=400:
                raw=await response.aread()
                if len(raw)>MAX_RESPONSE_BYTES:raise ProviderError('response_too_large')
                try:error=json.loads(raw)
                except ValueError:error={}
                raise ProviderError(error_code(response.status_code,error))
            if not 200<=response.status_code<300:raise ProviderError('provider_error')
            async for chunk in response.aiter_bytes():
                if cancel.is_set():raise ProviderError('cancelled')
                wire+=len(chunk)
                if wire>MAX_STREAM_BYTES:raise ProviderError('response_too_large')
                buffer+=decoder.decode(chunk)
                if len(buffer.encode())>MAX_RESPONSE_BYTES:raise ProviderError('response_too_large')
                while '\n' in buffer:
                    line,buffer=buffer.split('\n',1);line=line.rstrip('\r')
                    if line.startswith('data:'):data_lines.append(line[5:].lstrip())
                    if line or not data_lines:continue
                    data='\n'.join(data_lines);data_lines=[]
                    if data=='[DONE]':continue
                    try:event=json.loads(data)
                    except ValueError:raise ProviderError('invalid_response') from None
                    kind=event.get('type')
                    if kind=='response.output_text.delta':
                        delta=event.get('delta')
                        if not isinstance(delta,str):raise ProviderError('invalid_response')
                        pending+=delta;text+=delta
                        if len(text.encode())>MAX_TEXT_BYTES:raise ProviderError('stream_limit')
                        if time.monotonic()-last_flush>=.1 or len(pending.encode())>=1024:flush()
                    elif kind=='response.output_item.done':
                        item=event.get('item');index=event.get('output_index')
                        if isinstance(item,dict) and type(index)is int:items[index]=item
                    elif kind in ('response.completed','response.incomplete','response.failed'):
                        body=event.get('response')
                        if not isinstance(body,dict):raise ProviderError('invalid_response')
                    elif kind=='error':
                        code=event.get('code')
                        raise ProviderError('quota_exhausted' if code=='insufficient_quota' else 'provider_error')
                if body is not None:break
    finally:
        flusher.cancel()
        try:await flusher
        except (asyncio.CancelledError,Exception):pass
        flush()
    flush()
    if body is None:raise ProviderError('incomplete_response')
    # The completed response is authoritative, including ordering and statuses.
    # A prior output_item.done must not replace it by index. Supplement only a
    # missing encrypted reasoning field from the exact same stable item ID.
    reasoning={item.get('id'):item for item in items.values()
               if item.get('type')=='reasoning' and isinstance(item.get('id'),str)}
    for item in body.get('output',[]):
        if item.get('type')=='reasoning' and 'encrypted_content' not in item:
            encrypted=reasoning.get(item.get('id'),{}).get('encrypted_content')
            if isinstance(encrypted,str):item['encrypted_content']=encrypted
    if len(json.dumps(body,ensure_ascii=False).encode())>MAX_RESPONSE_BYTES:raise ProviderError('response_too_large')
    final=''.join(content.get('text','') for item in body.get('output',[]) if item.get('type')=='message'
                  for content in item.get('content',[]) if content.get('type')=='output_text')
    if text and final and text!=final:raise ProviderError('invalid_response',usage=usage_of(body))
    if not text and final:
        if emit_text:bridge.emit('delta',{'text':final})
        text=final
    if body.get('status')!='completed':raise ProviderError('incomplete_response',usage=usage_of(body))
    return body,text


async def _run(request,key,cancel,bridge):
    frozen=request['round_protocol'];items=list(request['seed_messages']);local_calls=[];web_planned=False;web_used=False
    local_limits=tool_budget(frozen);force_summary=False;summary_prompt_added=False
    scope=ai_scope.scope_for(request);scope_denied=False
    all_text='';sources=[];citations=[];traces=[];results=[];usages=[];tool_calls=[];web_request=None;query_target=None;web_source_counts={}
    timeout=request['timeout_seconds']
    semantic=frozen.get('version')=='ai-agent-round-v5'
    plan_accepted=not semantic
    async def plan_error(code,path,reason='invalid_tool_arguments'):
        await bridge.exchange('plan_diagnostic',{'stage':'provider_plan','code':code,'path':path},cancel)
        raise ProviderError(reason)
    def answer(body):
        return {'output':{'response_type':'agent_answer','text':all_text,'citations':citations,'sources':sources,
            'source_context':{'page_anchor':request['page_anchor'],'request_time':request['request_time'],
                'submitted_request_time':request.get('submitted_request_time'),'tool_results':results,'scope':scope,
                'web':{'allowed':scope['resources_allowed'] and ai_web.supported(request['provider_id'],request['model']),
                       'used':web_used,'search_call_count':int(web_used),'queried_at_ms':time.time_ns()//1000000,
                       'requested_as_of':query_target,'page_as_of':request['page_anchor'].get('as_of'),
                       'validation':'native_citations_only',**web_source_counts}},
            'tool_traces':traces,'round_protocol':frozen,
            'used_context':{'seed_messages':request['seed_messages'],'planner_messages':request.get('planner_messages'),
                            'tool_schemas':request['tool_schemas'],
                            'tool_calls':tool_calls,'web_request':web_request}},'response_model':body.get('model')}
    configured=os.environ.get('SHIXU_AI_AGENT_TIMEOUT_SECONDS')
    if configured in ('60','120'):timeout=int(configured)
    async with asyncio.timeout(timeout):
        async with httpx.AsyncClient(base_url=BASE_URLS[request['provider_id']],headers={'Authorization':'Bearer '+key},
            timeout=httpx.Timeout(timeout),trust_env=os.environ.get('SHIXU_AI_SYSTEM_PROXY_ENABLED')=='1',follow_redirects=False) as client:
            for ordinal in range(1,frozen['max_model_requests']+1):
                if cancel.is_set():raise ProviderError('cancelled')
                last=ordinal==frozen['max_model_requests']
                summary_only=force_summary
                tools=[]
                planning=semantic and not plan_accepted
                hosted=not planning and not force_summary and web_planned and not web_used and ai_web.supported(request['provider_id'],request['model'])
                restricted=not planning and ((scope_denied and not scope['local_only']) or scope['state'] in ('outside','unknown'))
                if planning:
                    tools=[request['intent_plan_schema']]
                elif hosted:
                    policy=frozen['web_policy']
                    tools=[{'type':'web_search','filters':{'allowed_domains':policy['allowed_domains']},
                            'search_context_size':'low','return_token_budget':'default'}]
                elif not force_summary and not last and scope['resources_allowed'] and not scope_denied:
                    if len(local_calls)<local_limits['max_reads']:tools=list(request['tool_schemas'])
                    if ai_web.supported(request['provider_id'],request['model']) and not web_planned:
                        tools.append(request['web_plan_schema'])
                if force_summary and not summary_prompt_added:
                    items.append({'role':'system','content':frozen.get('finalize_system_prompt') or FINALIZE_SYSTEM_PROMPT})
                    summary_prompt_added=True
                outbound=items
                if planning:
                    # The router receives actual users and verified identity
                    # metadata, not previous assistant prose or local reports.
                    outbound=request.get('planner_messages') or planner_messages(request)
                payload=payload_for(request,outbound,tools)
                if planning:
                    payload['tool_choice']='required';payload['parallel_tool_calls']=False
                if restricted:payload['max_output_tokens']=min(payload['max_output_tokens'],256)
                size=len(json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode())
                if size>frozen['max_input_bytes']:raise ProviderError('input_too_large')
                await bridge.exchange('authorize',{'ordinal':ordinal,'input_bytes':size,'hosted_web':hosted},cancel)
                bridge.emit('status',{'state':'running','message':'正在准备回答' if restricted else '正在查询并组织回答'})
                try:
                    body,text=await stream_response(client,'/v1/responses' if request['provider_id']=='openai' else '/responses',payload,cancel,bridge,emit_text=not restricted and not planning)
                except ProviderError as error:
                    await bridge.exchange('receipt',{'ordinal':ordinal,'usage':error.usage,'reason_code':error.reason_code},cancel)
                    raise
                usage=usage_of(body);usages.append(usage)
                if restricted and text:
                    # A model ignoring the refusal-only instruction must not
                    # stream unrelated factual content before the guard acts.
                    if (len(text)>220 or not ai_scope._TARGET.search(text) or not any(word in text for word in ('只能','仅','范围','专注'))
                            or ai_scope._OTHER.search(text) or '?' in text or '？' in text):text=ai_scope.DENIED_MESSAGE
                    bridge.emit('delta',{'text':text})
                web_accounting=None
                refused=any(content.get('type')=='refusal' for item in body.get('output',[]) if item.get('type')=='message' for content in item.get('content',[]))
                if refused:
                    await bridge.exchange('receipt',{'ordinal':ordinal,'usage':usage,'reason_code':'refusal'},cancel)
                    raise ProviderError('refusal',usage=usage)
                if hosted:
                    web_request={**request,'web_policy':frozen['web_policy'],'requested_as_of':query_target}
                    try:native=ai_web.native_answer(body,web_request)
                    except ValueError as error:
                        await bridge.exchange('receipt',{'ordinal':ordinal,'usage':usage,'reason_code':str(error)},cancel)
                        raise ProviderError(str(error),usage=usage) from None
                    web_source_counts={key:native['web'][key] for key in ('consulted_source_count','retained_source_count','omitted_unreferenced_source_count','invalid_unreferenced_source_count','duplicate_source_count','ignored_attempt_count','annotation_only_source_count')}
                    base=len(all_text)
                    citations.extend({**cite,'start_index':cite['start_index']+base,'end_index':cite['end_index']+base} for cite in native['citations'])
                    sources.extend(native['sources']);web_used=web_used or native['web']['used']
                    web_accounting={'used':native['web']['used'],'search_call_count':native['web']['search_call_count'],
                                    'complete':True,'search_content_in_input':None}
                await bridge.exchange('receipt',{'ordinal':ordinal,'usage':usage,'web_accounting':web_accounting},cancel)
                function_calls=[item for item in body.get('output',[]) if item.get('type')=='function_call']
                if planning:
                    # Reject the whole response before executing anything if
                    # the provider bypasses or duplicates the semantic plan.
                    if len(function_calls)!=1:await plan_error('first_call_count','response.output')
                    if function_calls[0].get('name')!='plan_stock_task':
                        await plan_error('first_call_name','response.output[].name')
                    call=function_calls[0];call_id=call.get('call_id')
                    if not isinstance(call_id,str) or not 1<=len(call_id)<=200:
                        await plan_error('call_id','response.function_call.call_id','invalid_response')
                    try:args=json.loads(call.get('arguments','{}'))
                    except (ValueError,TypeError):
                        await plan_error('arguments_json','response.function_call.arguments')
                    if not isinstance(args,dict):await plan_error('arguments_object','response.function_call.arguments')
                    result=await bridge.exchange('plan_stock',{'call_id':call_id,'args':args},cancel)
                    if result.get('ok') is not True:
                        await plan_error('runtime_plan_rejected','ipc.plan_stock')
                    scope=result['scope'];plan_accepted=True
                    request['accepted_stock_plan']=args;request['semantic_scope']=scope
                    tool_calls.append({'name':'plan_stock_task','call_id':call_id,'args':args})
                    for nested in result.get('tool_results',[]):
                        name,nargs,nid,value=nested['name'],nested['args'],nested['call_id'],nested['result']
                        classify_tool(name,nargs)
                        if nested['executed']:local_calls.append({'name':name,'args':nargs})
                        results.append(value);tool_calls.append({'name':name,'call_id':nid,'args':nargs})
                        traces.append({'name':name,'call_id':nid,'state':value.get('state'),
                                       'date':value.get('date'),'summary':value.get('summary')})
                        if isinstance(value.get('date'),str):query_target=value['date']
                    if result.get('web_gap'):results.append(result['web_gap'])
                    if result.get('web_plan'):
                        web_planned=True;web_request=result['web_plan'];request['accepted_web_plan']=web_request
                    force_summary=bool(result.get('force_summary'))
                    if result.get('reply'):
                        all_text=result['reply'];bridge.emit('delta',{'text':all_text});return answer(body)
                    # Restore normal conversation context only after domain
                    # routing, then provide the paired data/plan output.
                    items.extend(item for item in body.get('output',[])
                                 if item.get('type') in ('function_call','reasoning'))
                    items.append({'type':'function_call_output','call_id':call_id,
                                  'output':json.dumps(result,ensure_ascii=False,separators=(',',':'))})
                    items.append({'role':'system','content':'领域计划已经由控制层接受，当前状态：'+json.dumps(scope,ensure_ascii=False)+'。请按工具的真实结果继续回答；资料缺口不是领域拒绝。概念和招呼直接自然回答，仍不读取资料。'})
                    continue
                all_text+=text
                if not function_calls:
                    if not all_text.strip():raise ProviderError('invalid_output')
                    return answer(body)
                if last or summary_only:raise ProviderError('agent_tool_limit')
                items.extend(body.get('output',[]))
                for call in function_calls:
                    name=call.get('name');call_id=call.get('call_id')
                    if not isinstance(call_id,str) or len(call_id)>200:raise ProviderError('invalid_response')
                    try:args=json.loads(call.get('arguments','{}'))
                    except ValueError:raise ProviderError('invalid_tool_arguments') from None
                    if not isinstance(args,dict):raise ProviderError('invalid_tool_arguments')
                    tool_calls.append({'name':name,'call_id':call_id,'args':args})
                    if name=='request_web_search':
                        if web_planned or not ai_web.supported(request['provider_id'],request['model']):raise ProviderError('agent_tool_limit')
                        result=await bridge.exchange('plan_web',{'call_id':call_id,**args},cancel)
                        if result.get('ok') is True:
                            if force_summary:raise ProviderError('invalid_response')
                            web_planned=True;web_request=args
                            bridge.emit('tool',{'name':name,'call_id':call_id,'state':'completed','label':'准备查询网络资料'})
                        elif result.get('state')=='not_read' and result.get('reason_code')=='tool_budget_exhausted':
                            force_summary=True
                            results.append(result)
                        else:scope_denied=True
                    else:
                        try:
                            classify_frozen_read(request,name,args)
                            expected_gap=assess_tool(frozen,local_calls,name,args)
                        except ValueError:
                            raise ProviderError('invalid_tool_arguments') from None
                        result=await bridge.exchange('read_tool',{'name':name,'args':args,'call_id':call_id},cancel)
                        if result.get('state')=='scope_denied':scope_denied=True
                        elif result.get('state')=='not_read' and result.get('reason_code')=='tool_budget_exhausted':
                            # Finish every call in this response, then request only
                            # the answer. A skipped read is not missing source data.
                            force_summary=True
                        elif expected_gap is not None:
                            # Both lanes must use the same execution budget. A
                            # mismatched control response is not publishable data.
                            raise ProviderError('invalid_response')
                        else:
                            local_calls.append({'name':name,'args':args})
                            if len(local_calls)>=local_limits['max_reads']:force_summary=True
                        results.append(result)
                        if name in ('read_market_data','check_data_availability') and isinstance(result.get('date'),str):query_target=result['date']
                    trace={'name':name,'call_id':call_id,'state':result.get('state'),'date':result.get('date'),'summary':result.get('summary')}
                    traces.append(trace)
                    items.append({'type':'function_call_output','call_id':call_id,'output':json.dumps(result,ensure_ascii=False,separators=(',',':'))})
    raise ProviderError('incomplete_response')


async def _supervise(request,key,cancel,bridge):
    task=asyncio.create_task(_run(request,key,cancel,bridge))
    try:
        while not task.done():
            if cancel.is_set():raise ProviderError('cancelled')
            await asyncio.wait({task},timeout=.1)
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            try:await task
            except (asyncio.CancelledError,Exception):pass


def perform_agent(request,key,cancel,bridge):
    try:return asyncio.run(_supervise(request,key,cancel,bridge))
    except ProviderError:raise
    except (TimeoutError,httpx.TimeoutException):raise ProviderError('timeout') from None
    except (httpx.HTTPError,OSError):raise ProviderError('network_error') from None
    except Exception:raise ProviderError('invalid_response') from None
