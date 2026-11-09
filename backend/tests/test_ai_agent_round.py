"""Per-question agent queries, real streamed HTTP parsing and control-thread tools."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import threading
import time
import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import core
from app.routers.ai import router
from app.services import ai_service as service, ai_read_tools
from app.services.ai_runtime import AIRuntime
from app.services.ai_agent_runtime import append_event
from app.services.ai_agent_runtime import _bind_stock_anchor
from app.services.ai_read_tools import execute_read_tool as actual_read_tool
from app.adapters import ai_agent
from test_ai_service import enable,fake,drive

TOOL={'ok':True,'state':'available','tool_name':'read_market_data','date':'2026-11-02','source':'fixture',
      'summary':{'phase_state':'available','sample_count':86},'evidence':[{'evidence_id':'read:today:e1','data':{'valid_count':86}}],
      'metrics':[{'metric_id':'read:today:m1','value':86,'unit':'只','evidence_ids':['read:today:e1']}],
      'gaps':[],'source_bindings':{'snapshot_id':'fixture-today'}}

def _isolated_agent_client(tmp_path,monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path/'data'))
    monkeypatch.setenv('SHIXU_CREDENTIAL_DIR',str(tmp_path/'credentials'))
    master=tmp_path/'master';master.write_bytes(Fernet.generate_key());monkeypatch.setenv('SHIXU_CREDENTIAL_MASTER_KEY_FILE',str(master))
    for key in ('SHIXU_SQLITE_PATH','SHIXU_PUBLISHED_DIR','SHIXU_RAW_DIR','SHIXU_WORK_DIR','SHIXU_AI_READ_SOURCE_ENV','SHIXU_AI_AGENT_TIMEOUT_SECONDS'):
        monkeypatch.delenv(key,raising=False)
    core.ensure_ready();app=FastAPI();app.include_router(router)
    monkeypatch.setattr(ai_read_tools,'execute_read_tool',lambda *a,**kw:deepcopy(TOOL))
    return TestClient(app)


@pytest.fixture
def modern_agent_client(tmp_path,monkeypatch):
    """The production v5 semantic-plan path; no protocol fixture overrides."""
    return _isolated_agent_client(tmp_path,monkeypatch)


@pytest.fixture
def agent_client(tmp_path,monkeypatch):
    """Replay explicitly frozen v4 records, not the new production default.

    Existing direct-tool/SSE and scope-v3 regressions describe historical round
    contracts. New semantic production behavior uses ``modern_agent_client`` in
    test_ai_semantic_round.py. This fixture never changes production defaults.
    """
    from app.services import ai_agent_protocol as agent
    actual_protocol=agent.protocol;actual_tools=ai_read_tools.tool_schemas
    actual_request=service._agent_request
    public_prompt=Path(__file__).resolve().parents[2]/'contracts/prompts/ai-agent-round-v4.md'
    texts=[part.split('\n```',1)[0] for part in public_prompt.read_text().split('```text\n')[1:]]
    assert len(texts)==2
    def frozen_v4():
        result=deepcopy(actual_protocol());result.update(version='ai-agent-round-v4',
            system_prompt=texts[0],finalize_system_prompt=texts[1])
        result.pop('scope_policy',None)
        return result
    def historical_request(*args,**kwargs):
        result=actual_request(*args,**kwargs);result.pop('intent_plan_schema',None);return result
    monkeypatch.setattr(agent,'protocol',frozen_v4)
    monkeypatch.setattr(ai_read_tools,'tool_schemas',lambda:[tool for tool in actual_tools()
        if tool['name'] in ('read_market_data','read_stock_research','check_data_availability')])
    monkeypatch.setattr(service,'_agent_request',historical_request)
    return _isolated_agent_client(tmp_path,monkeypatch)


def create(client,key='agent-first',**changes):
    return client.post('/api/v1/ai/sessions',json={'kind':'market','provider_id':'openai','text':'今天早上竞价如何？',
        'as_of':'2026-10-30','consent':True,'request_key':key,**changes})


def frame(event):return ('data: '+json.dumps(event,ensure_ascii=False)+'\n\n').encode()


def complete(output,text=None):
    events=[]
    if text:
        for chunk in (text[:5],text[5:]):events.append(frame({'type':'response.output_text.delta','delta':chunk}))
    for index,item in enumerate(output):events.append(frame({'type':'response.output_item.done','output_index':index,'item':item}))
    events.append(frame({'type':'response.completed','response':{'status':'completed','model':'gpt-6.1-sol','output':output,
        'usage':{'input_tokens':100,'output_tokens':50,'input_tokens_details':{'cached_tokens':0}}}}))
    return b''.join(events)


def function(name='read_market_data',args=None,call_id='call_fixture'):
    return {'type':'function_call','name':name,'arguments':json.dumps(args or {'date':'today','section':'auction'}),
            'call_id':call_id,'id':call_id+'_item','status':'completed'}


def final(text):return {'type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':text,'annotations':[]}]}


def mock_network(monkeypatch,handler):
    actual=httpx.AsyncClient
    monkeypatch.setattr(ai_agent.httpx,'AsyncClient',lambda **kwargs:actual(**kwargs,transport=httpx.MockTransport(handler)))


def test_today_question_queries_tool_on_control_thread_then_streams_fresh_answer(agent_client,monkeypatch):
    client=agent_client;calls=[];tool_threads=[];control=threading.get_ident()
    def read(name,args,**kwargs):
        tool_threads.append(threading.get_ident());assert args['date']=='today'
        assert kwargs['page_context']['as_of']=='2026-10-30';return deepcopy(TOOL)
    monkeypatch.setattr(ai_read_tools,'execute_read_tool',read)
    answer='今天早上的竞价资料已经采集，当前样本有86只股票；这些是竞价样本，不能代表全市场涨跌分布。'
    async def handler(request):
        body=json.loads(request.content);calls.append(body)
        assert body['stream'] is True and body['store'] is False
        if len(calls)==1:
            assert len(body['input'])==3 and 'INPUT_REPORT' not in body['input'][1]['content']
            assert 'tools' in body and any(x.get('name')=='read_market_data' for x in body['tools'])
            return httpx.Response(200,content=complete([function()]),headers={'content-type':'text/event-stream'})
        result=next(item for item in body['input'] if item.get('type')=='function_call_output')
        assert json.loads(result['output'])['summary']['sample_count']==86
        return httpx.Response(200,content=complete([final(answer)],answer),headers={'content-type':'text/event-stream'})
    mock_network(monkeypatch,handler);runtime=AIRuntime(fake)
    try:
        enable(client,runtime,'openai')
        response=create(client);assert response.status_code==202,response.text
        result=response.json();task=drive(runtime,result['task_id'])
        assert task['state']=='completed',task
        detail=client.get('/api/v1/ai/sessions/'+result['session_id']).json();message=detail['messages'][-1]
        assert message['response_type']=='agent_answer' and message['text']==answer and message['interpretation'] is None
        assert message['source_context']['tool_results'][0]['summary']['sample_count']==86
        assert tool_threads==[control] and len(calls)==2
        with core.connect() as db:
            receipts=db.execute('SELECT * FROM ai_request_receipt WHERE task_id=? ORDER BY ordinal',(result['task_id'],)).fetchall()
            assert len(receipts)==2 and all(r['cost_state']=='estimated' for r in receipts)
            events=[dict(row) for row in db.execute('SELECT * FROM ai_task_event WHERE task_id=? ORDER BY seq',(result['task_id'],))]
            assert [e['seq'] for e in events]==list(range(1,len(events)+1))
            assert ''.join(json.loads(e['data_json']).get('text','') for e in events if e['kind']=='delta')==answer
            assert events[-1]['kind']=='done'
        assert service.providers()['usage']['requests']==3  # probe + two requests, no parent double-count.
        # GET history neither starts queries nor extends TTL.
        stamp=detail['session']['expires_at_ms'];client.get('/api/v1/ai/sessions')
        assert client.get('/api/v1/ai/sessions/'+result['session_id']).json()['session']['expires_at_ms']==stamp
        assert len(calls)==2
    finally:runtime.shutdown()


def test_each_request_is_budget_checked_and_does_not_automatically_retry(agent_client,monkeypatch):
    client=agent_client;calls=[]
    async def handler(request):
        calls.append(request)
        return httpx.Response(200,content=complete([function()]),headers={'content-type':'text/event-stream'})
    mock_network(monkeypatch,handler);runtime=AIRuntime(fake)
    try:
        enable(client,runtime,'openai')
        with core.connect() as db:db.execute('UPDATE ai_instance_limits SET daily_request_limit=2 WHERE singleton=1')
        result=create(client).json();task=drive(runtime,result['task_id'])
        assert task['state']=='failed' and task['reason_code']=='budget_exceeded'
        detail=client.get('/api/v1/ai/sessions/'+result['session_id']).json()
        assert 'reason_code' not in detail['messages'][0]
        assert detail['messages'][-1]['reason_code']=='budget_exceeded'
        assert len(calls)==1 and service.providers()['usage']['requests']==2
        for _ in range(3):runtime.tick('fixture')
        assert len(calls)==1
    finally:runtime.shutdown()


def test_cancel_closes_stream_and_saves_partial_text_with_pending_receipt(agent_client,monkeypatch):
    client=agent_client;closed=threading.Event();entered=threading.Event()
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            try:
                yield frame({'type':'response.output_text.delta','delta':'正在结合已查询资料说明。'})
                entered.set();await asyncio.sleep(10)
            finally:closed.set()
    async def handler(request):return httpx.Response(200,stream=Slow(),headers={'content-type':'text/event-stream'})
    mock_network(monkeypatch,handler);runtime=AIRuntime(fake)
    try:
        enable(client,runtime,'openai')
        result=create(client).json()
        for _ in range(200):
            runtime.tick('fixture')
            if entered.is_set():break
            time.sleep(.005)
        assert entered.is_set()
        client.post('/api/v1/ai/tasks/'+result['task_id']+'/cancel')
        task=drive(runtime,result['task_id'])
        assert closed.is_set() and task['state']=='cancelled' and task['cost']['state']=='pending'
        message=client.get('/api/v1/ai/sessions/'+result['session_id']).json()['messages'][-1]
        assert message['response_type']=='agent_answer' and message['partial'] is True
        assert '正在结合' in message['text']
        assert service.providers()['usage']['requests']==2
    finally:runtime.shutdown()


def test_restart_keeps_sent_child_receipt_and_partial_without_resending(agent_client):
    client=agent_client;runtime=AIRuntime(fake)
    try:
        enable(client,runtime,'openai');result=create(client).json()
        with core.connect() as db:
            task=dict(db.execute('SELECT * FROM ai_task WHERE task_id=?',(result['task_id'],)).fetchone())
            stamp=core.now_ms();price=json.loads(task['result_json'])
            db.execute("UPDATE ai_task SET state='running',sent_at_ms=? WHERE task_id=?",(stamp,result['task_id']))
            db.execute("INSERT INTO ai_request_receipt VALUES('child-fixture',?,1,?,'openai','gpt-6.1-sol','sent',123456,NULL,'pending','{}',?,?,?,NULL,NULL)",(result['task_id'],service.day_key(),core.dumps(price),stamp,stamp))
            append_event(db,task,'delta',{'text':'中断前已收到的文字'})
        runtime.recover_interrupted()
        task=service.get_task(result['task_id']);assert task['state']=='interrupted_manual_retry' and task['cost']['state']=='pending'
        detail=client.get('/api/v1/ai/sessions/'+result['session_id']).json()
        assert detail['messages'][-1]['text']=='中断前已收到的文字'
        assert detail['messages'][-1]['reason_code']=='interrupted_manual_retry'
        runtime.tick('fixture',allow_start=False)
        with core.connect() as db:assert db.execute('SELECT count(*) FROM ai_request_receipt WHERE task_id=?',(result['task_id'],)).fetchone()[0]==1
        assert service.providers()['usage']['requests']==2
    finally:runtime.shutdown()


def test_old_session_future_round_queries_today_without_rewriting_frozen_history(agent_client,monkeypatch):
    from test_ai_service import CONTEXT,ANSWER
    client=agent_client;runtime=AIRuntime(fake);calls=[]
    monkeypatch.setattr(service,'context_preview',lambda *a,**kw:{'eligible':True,'context_digest':'fixture-sha','context':deepcopy(CONTEXT)})
    answer='今天竞价已有86只有效样本，不能把页面旧日期当作今天。'
    async def handler(request):
        calls.append(json.loads(request.content))
        output=[function()] if len(calls)==1 else [final(answer)]
        return httpx.Response(200,content=complete(output,answer if len(calls)>1 else None),headers={'content-type':'text/event-stream'})
    mock_network(monkeypatch,handler)
    try:
        enable(client,runtime,'openai')
        old=service._legacy_create_session({'kind':'stock','report_id':'old-report','provider_id':'openai','context_digest':'fixture-sha','consent':True,'request_key':'old-frozen'})
        assert drive(runtime,old['task_id'])['state']=='completed'
        before=client.get('/api/v1/ai/sessions/'+old['session_id']).json()
        assert before['messages'][-1]['interpretation']==ANSWER
        with core.connect() as db:original=tuple(db.execute('SELECT context_json,protocol_json FROM ai_session WHERE session_id=?',(old['session_id'],)).fetchone())
        response=client.post('/api/v1/ai/sessions/'+old['session_id']+'/messages',json={'text':'今天早上竞价如何？','consent':True,'request_key':'future-agent','allow_web':True})
        assert response.status_code==202,response.text
        assert drive(runtime,response.json()['task_id'])['state']=='completed'
        after=client.get('/api/v1/ai/sessions/'+old['session_id']).json()
        assert after['messages'][1]==before['messages'][1]
        assert after['messages'][-1]['response_type']=='agent_answer' and after['messages'][-1]['text']==answer
        assert after['messages'][-1]['source_context']['tool_results'][0]['date']=='2026-11-02'
        with core.connect() as db:assert tuple(db.execute('SELECT context_json,protocol_json FROM ai_session WHERE session_id=?',(old['session_id'],)).fetchone())==original
    finally:runtime.shutdown()


def test_network_is_planned_before_hosted_tool_and_fee_is_reserved_only_then(agent_client,monkeypatch):
    client=agent_client;runtime=AIRuntime(fake);calls=[]
    answer='交易所公告披露了公司经营变化，具体口径应以公告原文为准。'
    url='https://www.sse.com.cn/disclosure/fixture.pdf';annotated_url=url+'?utm_source=responses'
    async def handler(request):
        payload=json.loads(request.content);calls.append(payload)
        if len(calls)==1:
            assert all(tool['type']!='web_search' for tool in payload['tools'])
            return httpx.Response(200,content=complete([function('request_web_search',{'query':'公司最新交易所公告','reason':'需要官方披露','purpose':'stock_research','user_quote':'帮我查公司最新公告'},'web_plan')]),headers={'content-type':'text/event-stream'})
        assert payload['max_tool_calls']==1 and payload['tools'][0]['type']=='web_search'
        native=final(answer);native['content'][0]['annotations']=[{'type':'url_citation','start_index':0,'end_index':len(answer),'url':annotated_url,'title':'交易所公告'}]
        search={'type':'web_search_call','id':'ws_fixture','status':'completed','action':{'type':'search','sources':[{'type':'url','url':url,'title':'交易所公告'}]}}
        return httpx.Response(200,content=complete([search,native],answer),headers={'content-type':'text/event-stream'})
    mock_network(monkeypatch,handler)
    try:
        enable(client,runtime,'openai')
        result=create(client,text='帮我查公司最新公告').json();task=drive(runtime,result['task_id'])
        assert task['state']=='completed' and task['cost']['state']=='pending'
        with core.connect() as db:
            receipts=db.execute('SELECT * FROM ai_request_receipt WHERE task_id=? ORDER BY ordinal',(result['task_id'],)).fetchall()
            assert len(receipts)==2
            assert 'web_policy' not in json.loads(receipts[0]['price_json'])
            assert receipts[0]['cost_state']=='estimated' and receipts[0]['reservation_nusd']<330000000
            assert json.loads(receipts[1]['price_json'])['web_policy']['tool_nusd']==10000000
            assert receipts[1]['reservation_nusd']>=330000000 and receipts[1]['cost_state']=='pending'
        message=client.get('/api/v1/ai/sessions/'+result['session_id']).json()['messages'][-1]
        assert message['source_context']['web']['used'] is True and message['sources'][0]['url']==annotated_url
        assert message['sources'][0]['provenance']=='native_annotation' and message['sources'][0]['consulted_match'] is False
        assert message['sources'][0]['annotation_only'] is True and message['source_context']['web']['annotation_only_source_count']==1
        assert message['citations'][0]['start_index']==0 and message['text']==answer
        assert service.providers()['usage']['requests']==3
    finally:runtime.shutdown()


def test_chat_concept_without_tool_does_not_read_data_or_web(agent_client,monkeypatch):
    client=agent_client;runtime=AIRuntime(fake);calls=[]
    monkeypatch.setattr(ai_read_tools,'execute_read_tool',lambda *a,**kw:pytest.fail('concept question need not read business data'))
    answer='市盈率比较股价与每股盈利，解读时还要留意盈利是否稳定及行业差异。'
    async def handler(request):
        payload=json.loads(request.content);calls.append(payload)
        assert all(tool['type']!='web_search' for tool in payload['tools'])
        return httpx.Response(200,content=complete([final(answer)],answer),headers={'content-type':'text/event-stream'})
    mock_network(monkeypatch,handler)
    try:
        enable(client,runtime,'openai')
        response=create(client,text='PE是什么意思？',kind='stock',instrument_id='600001.SH')
        assert response.status_code==202,response.text
        assert drive(runtime,response.json()['task_id'])['state']=='completed'
        message=client.get('/api/v1/ai/sessions/'+response.json()['session_id']).json()['messages'][-1]
        assert message['text']==answer and message['source_context']['tool_results']==[] and len(calls)==1
    finally:runtime.shutdown()


def test_three_independent_local_reads_fit_one_model_round_and_control_runs_in_order(agent_client,monkeypatch):
    client=agent_client;runtime=AIRuntime(fake);calls=[];sections=[]
    def read(name,args,**kwargs):
        sections.append(args['section']);result=deepcopy(TOOL);result['summary']['section']=args['section'];return result
    monkeypatch.setattr(ai_read_tools,'execute_read_tool',read)
    answer='竞价、午间和收盘资料已分别查询，比较时需要关注各时点样本范围。'
    async def handler(request):
        payload=json.loads(request.content);calls.append(payload)
        if len(calls)==1:
            assert payload['parallel_tool_calls'] is True
            output=[function(args={'date':'today','section':section},call_id='call_'+section) for section in ('auction','noon','close')]
        else:
            assert len([item for item in payload['input'] if item.get('type')=='function_call_output'])==3
            output=[final(answer)]
        return httpx.Response(200,content=complete(output,answer if len(calls)>1 else None),headers={'content-type':'text/event-stream'})
    mock_network(monkeypatch,handler)
    try:
        enable(client,runtime,'openai');result=create(client,text='比较竞价到午间再到收盘的变化').json()
        assert drive(runtime,result['task_id'])['state']=='completed'
        assert sections==['auction','noon','close'] and len(calls)==2
        message=client.get('/api/v1/ai/sessions/'+result['session_id']).json()['messages'][-1]
        assert len(message['source_context']['tool_results'])==3
    finally:runtime.shutdown()


def test_execution_time_is_refreshed_and_report_anchor_is_retained(agent_client,monkeypatch):
    client=agent_client;runtime=AIRuntime(fake);sent=[]
    answer='我会以本次执行时间理解今天，并查询所选标的资料。'
    async def handler(request):
        payload=json.loads(request.content);sent.append(payload)
        assert '2000-01-01' not in payload['input'][1]['content']
        assert 'selected-report' in payload['input'][1]['content']
        return httpx.Response(200,content=complete([final(answer)],answer),headers={'content-type':'text/event-stream'})
    mock_network(monkeypatch,handler)
    try:
        enable(client,runtime,'openai')
        result=create(client,kind='stock',report_id='selected-report',instrument_id='600001.SH').json()
        with core.connect() as db:
            request=json.loads(db.execute('SELECT request_json FROM ai_task WHERE task_id=?',(result['task_id'],)).fetchone()[0])
            request['request_time']={'request_date_bj':'2000-01-01','request_time_bj':'2000-01-01T00:00:00+08:00'}
            request['seed_messages'][1]['content']='old 2000-01-01'
            db.execute('UPDATE ai_task SET request_json=? WHERE task_id=?',(core.dumps(request),result['task_id']))
        assert drive(runtime,result['task_id'])['state']=='completed'
        message=client.get('/api/v1/ai/sessions/'+result['session_id']).json()['messages'][-1]
        assert message['source_context']['request_time']['request_date_bj']!='2000-01-01'
        assert message['source_context']['submitted_request_time']['request_date_bj']=='2000-01-01'
        assert message['source_context']['page_anchor']['report_id']=='selected-report'
    finally:runtime.shutdown()


def test_mismatching_final_text_does_not_attach_citations_to_different_stream(agent_client,monkeypatch):
    client=agent_client;runtime=AIRuntime(fake)
    async def handler(request):
        events=[frame({'type':'response.output_text.delta','delta':'已经流出的正文'}),
                frame({'type':'response.completed','response':{'status':'completed','model':'gpt-6.1-sol',
                    'output':[final('另一个不同正文')],'usage':{'input_tokens':10,'output_tokens':10}}})]
        return httpx.Response(200,content=b''.join(events),headers={'content-type':'text/event-stream'})
    mock_network(monkeypatch,handler)
    try:
        enable(client,runtime,'openai');result=create(client).json();task=drive(runtime,result['task_id'])
        assert task['state']=='failed' and task['reason_code']=='invalid_response'
        message=client.get('/api/v1/ai/sessions/'+result['session_id']).json()['messages'][-1]
        assert message['text']=='已经流出的正文' and message['partial'] and not message['citations']
    finally:runtime.shutdown()


def test_stream_cleanup_advances_past_old_receipts_and_keeps_last_seq(agent_client):
    client=agent_client;runtime=AIRuntime(fake)
    try:
        with core.connect() as db:
            for n in range(60):
                db.execute("INSERT INTO ai_task(task_id,operation,provider_id,model,config_signature,credential_ref,request_key,request_digest,state,created_at_ms,finished_at_ms,request_json,result_json) VALUES(?,'message','openai','gpt-6.1-sol','sig','','key-'||?,'hash','completed',1,1,'{}','{}')",('old-'+str(n),str(n)))
            db.execute("INSERT INTO ai_task(task_id,operation,provider_id,model,config_signature,credential_ref,request_key,request_digest,state,created_at_ms,finished_at_ms,request_json,result_json) VALUES('with-events','message','openai','gpt-6.1-sol','sig','','event-key','hash','completed',2,2,'{}','{\"stream_last_seq\":1}')")
            db.execute("INSERT INTO ai_task_event VALUES('with-events',1,'delta','{\"text\":\"temporary\"}',2)")
            runtime._cleanup_agent_events(db)
            assert db.execute('SELECT count(*) FROM ai_task_event').fetchone()[0]==0
            assert json.loads(db.execute("SELECT result_json FROM ai_task WHERE task_id='with-events'").fetchone()[0])['stream_last_seq']==1
    finally:runtime.shutdown()


def test_native_source_failure_keeps_received_usage_and_uncertain_search_fee(agent_client,monkeypatch):
    client=agent_client;runtime=AIRuntime(fake);calls=[]
    answer='本轮生成过的文字仍可作为失败片段查看。'
    async def handler(request):
        calls.append(request)
        if len(calls)==1:output=[function('request_web_search',{'query':'公司公告','reason':'需要官方原文','purpose':'stock_research','user_quote':'查公司最新公告'},'plan_failure')]
        else:
            bad='https://evil.example/fake'
            message=final(answer);message['content'][0]['annotations']=[{'type':'url_citation','start_index':0,'end_index':len(answer),'url':bad,'title':'fake'}]
            output=[{'type':'web_search_call','status':'completed','action':{'type':'search','sources':[{'type':'url','url':bad}]}},message]
        return httpx.Response(200,content=complete(output,answer if len(calls)>1 else None),headers={'content-type':'text/event-stream'})
    mock_network(monkeypatch,handler)
    try:
        enable(client,runtime,'openai');result=create(client,text='查公司最新公告').json();task=drive(runtime,result['task_id'])
        assert task['state']=='failed' and task['reason_code']=='invalid_web_source' and task['cost']['state']=='pending'
        with core.connect() as db:
            receipts=db.execute('SELECT * FROM ai_request_receipt WHERE task_id=? ORDER BY ordinal',(result['task_id'],)).fetchall()
            assert len(receipts)==2 and json.loads(receipts[1]['usage_json'])['input_tokens']==100
            assert json.loads(receipts[1]['usage_json'])['output_tokens']==50 and receipts[1]['cost_state']=='pending'
            assert receipts[1]['reservation_nusd']>=330000000
        assert len(calls)==2
    finally:runtime.shutdown()


def test_terminal_output_is_authoritative_over_stale_done_items_and_indexes():
    answer='本轮按交易所原文回答。';url='https://www.sse.com.cn/fixture.pdf'
    source={'type':'url','url':url,'title':'交易所原文'}
    search={'type':'web_search_call','id':'ws_done','status':'completed','action':{'type':'search','sources':[source]}}
    attempt={'type':'web_search_call','id':'ws_ignored','status':'searching','action':{'type':'open_page','url':url}}
    message=final(answer);message['id']='msg_final'
    message['content'][0]['annotations']=[{'type':'url_citation','start_index':0,'end_index':len(answer),'url':url}]
    terminal={'status':'completed','output':[search,attempt,message],'usage':{'input_tokens':100,'output_tokens':50}}
    stale=deepcopy(search);stale.update(status='searching',action={'type':'search'})
    # Stale status at the original index plus a valid item at an obsolete index
    # must not replace completed sources or the final message.
    events=[frame({'type':'response.output_text.delta','delta':answer}),
            frame({'type':'response.output_item.done','output_index':0,'item':stale}),
            frame({'type':'response.output_item.done','output_index':2,'item':deepcopy(attempt)}),
            frame({'type':'response.completed','response':terminal})]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=b''.join(events)))) as client:
            return await ai_agent.stream_response(client,'https://api.openai.com/v1/responses',{},threading.Event(),ai_agent.AgentBridge())
    actual,text=asyncio.run(run())
    assert actual==terminal and text==answer
    native=ai_agent.ai_web.native_answer(actual,{'web_policy':ai_agent.ai_web.policy()})
    assert native['web']['ignored_attempt_count']==1 and native['citations'][0]['url']==url


def test_stream_reasoning_supplements_only_missing_encrypted_field_by_stable_id():
    terminal={'status':'completed','output':[
        {'type':'reasoning','id':'rs_missing','summary':[]},
        {'type':'reasoning','id':'rs_existing','summary':[],'encrypted_content':'authoritative'},
        {'type':'reasoning','id':'rs_unmatched','summary':[]},final('概念解释')],
        'usage':{'input_tokens':10,'output_tokens':10}}
    prior=[{'type':'reasoning','id':'rs_missing','summary':[{'text':'stale'}],'encrypted_content':'fill_missing'},
           {'type':'reasoning','id':'rs_existing','encrypted_content':'stale_encrypted'},
           {'type':'reasoning','id':'different_id','encrypted_content':'never_fill_by_index'}]
    events=[frame({'type':'response.output_item.done','output_index':index,'item':item}) for index,item in enumerate(prior)]
    events.append(frame({'type':'response.completed','response':terminal}))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=b''.join(events)))) as client:
            return await ai_agent.stream_response(client,'https://api.openai.com/v1/responses',{},threading.Event(),ai_agent.AgentBridge())
    actual,text=asyncio.run(run())
    assert actual['output'][0]=={'type':'reasoning','id':'rs_missing','summary':[],'encrypted_content':'fill_missing'}
    assert actual['output'][1]['encrypted_content']=='authoritative'
    assert 'encrypted_content' not in actual['output'][2] and text=='概念解释'


def test_unbound_new_chat_binds_verified_stock_for_next_empty_instrument_read(agent_client,monkeypatch):
    from test_ai_contexts import report,SYMBOL
    client=agent_client;runtime=AIRuntime(fake);calls=[];reads=[]
    value=report()
    with core.connect() as db:
        db.execute('INSERT INTO research_report(report_id,instrument_id,data_cutoff,created_at_ms,report_json,snapshot_refs_json,expires_at_ms,last_accessed_at_ms) VALUES(?,?,?,?,?,?,?,?)',
                   (value['report_id'],SYMBOL,value['data_cutoff'],1234,core.dumps(value),'{}',9999999999999,5678))
    def read(name,args,**kwargs):
        reads.append(deepcopy(kwargs['page_context']))
        # Exercise the actual read-only verifier and permission filter, rather
        # than binding from a model's guessed symbol or mocked prose.
        return actual_read_tool(name,args,**kwargs)
    monkeypatch.setattr(ai_read_tools,'execute_read_tool',read)
    async def handler(request):
        payload=json.loads(request.content);calls.append(payload)
        if len(calls) in (1,3):
            symbol=SYMBOL if len(calls)==1 else ''
            return httpx.Response(200,content=complete([function('read_stock_research',{'instrument_id':symbol})]))
        return httpx.Response(200,content=complete([final('研究资料已读取，估值需结合经营情况判断。')],'研究资料已读取，估值需结合经营情况判断。'))
    mock_network(monkeypatch,handler)
    try:
        enable(client,runtime,'openai');result=create(client,text='请研究这只股票').json()
        with core.connect() as db:
            old_protocol=db.execute('SELECT protocol_json FROM ai_session WHERE session_id=?',(result['session_id'],)).fetchone()[0]
        assert drive(runtime,result['task_id'])['state']=='completed'
        assert len(calls)==2  # Binding itself issues no additional model request.
        first=client.get('/api/v1/ai/sessions/'+result['session_id']).json()
        session=first['session']
        assert session['kind']=='stock' and session['instrument_id']==SYMBOL
        assert session['context']['kind']=='page_anchor' and session['context']['subject']==value['instrument']['name']
        assert session['context_digest']==core.digest(session['context'])
        assert 'instrument_id' not in first['messages'][-1]['source_context']['page_anchor']
        assert first['messages'][-1]['source_context']['tool_results'][0]['summary']['instrument_id']==SYMBOL
        follow=client.post('/api/v1/ai/sessions/'+result['session_id']+'/messages',json={'text':'它的估值如何？','consent':True,'request_key':'stock-pronoun'})
        assert follow.status_code==202,follow.text
        assert drive(runtime,follow.json()['task_id'])['state']=='completed' and len(calls)==4
        after=client.get('/api/v1/ai/sessions/'+result['session_id']).json()
        assert reads[0].get('instrument_id') is None and reads[1]['instrument_id']==SYMBOL
        assert after['messages'][:2]==first['messages'] and after['messages'][-1]['source_context']['tool_results'][0]['evidence']
        listed=client.get('/api/v1/ai/sessions',params={'kind':'stock','instrument_id':SYMBOL}).json()['items']
        assert [row['session_id'] for row in listed]==[result['session_id']]
        with core.connect() as db:
            assert db.execute('SELECT protocol_json FROM ai_session WHERE session_id=?',(result['session_id'],)).fetchone()[0]==old_protocol
            assert tuple(db.execute('SELECT expires_at_ms,last_accessed_at_ms FROM research_report').fetchone())==(9999999999999,5678)
    finally:runtime.shutdown()


@pytest.mark.parametrize('case',['old_protocol','old_context','already_bound','permission','empty_evidence','read_error','conflicting_identity'])
def test_stock_anchor_binding_preserves_old_bound_or_unverified_chats(agent_client,case):
    client=agent_client;runtime=AIRuntime(fake)
    result=deepcopy(TOOL)
    result.update(tool_name='read_stock_research',summary={'instrument_id':'600001.SH','instrument':{'instrument_id':'600001.SH','name':'合成个股'}})
    try:
        enable(client,runtime,'openai');created=create(client).json()
        with core.connect() as db:
            if case=='old_protocol':db.execute("UPDATE ai_session SET protocol_json='{}' WHERE session_id=?",(created['session_id'],))
            elif case=='old_context':db.execute("UPDATE ai_session SET context_json='{\"kind\":\"market_review\",\"evidence\":[]}' WHERE session_id=?",(created['session_id'],))
            elif case=='already_bound':db.execute("UPDATE ai_session SET instrument_id='600002.SH' WHERE session_id=?",(created['session_id'],))
            elif case=='permission':result['gaps']=[{'capability':'research_permission','reason':'不许外发'}]
            elif case=='empty_evidence':result['evidence']=[]
            elif case=='read_error':result.update(ok=False,state='error')
            elif case=='conflicting_identity':result['summary']['instrument']['instrument_id']='600002.SH'
            before=tuple(db.execute('SELECT * FROM ai_session WHERE session_id=?',(created['session_id'],)).fetchone())
            messages=[tuple(row) for row in db.execute('SELECT * FROM ai_message WHERE session_id=?',(created['session_id'],))]
            _bind_stock_anchor(db,created['session_id'],result)
            assert tuple(db.execute('SELECT * FROM ai_session WHERE session_id=?',(created['session_id'],)).fetchone())==before
            assert [tuple(row) for row in db.execute('SELECT * FROM ai_message WHERE session_id=?',(created['session_id'],))]==messages
    finally:runtime.shutdown()
