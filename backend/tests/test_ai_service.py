"""AI tests use only temporary SQLite, temporary encryption keys and fake HTTP."""
import json
import threading
import time
from concurrent.futures import Future
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet
from app import core
from app.services import ai_service as service
from app.services.ai_runtime import AIRuntime
from app.services.ai_validation import validate_interpretation
from app.services.ai_catalog import estimated_nusd
from app.adapters.ai_responses import ProviderError, payload_for, error_code, usage_of
from app.routers.ai import router

CONTEXT = {'kind':'stock_research','instrument_id':'600001.SH','as_of':'2026-10-30','report_id':'report-fixture',
           'claims':[{'claim_id':'c1','text':'已有来源陈述','kind':'source_statement','evidence_ids':['e1']}],
           'evidence':[{'evidence_id':'e1','title':'测试资料','source':'fixture','kind':'test','data':{'text':'仅测试资料'}}],
           'metrics':[{'metric_id':'m1','value':None,'unit':'%','evidence_ids':['e1']}], 'gaps':[{'reason':'测试缺口'}],
           'allowed_ids':{'claim_ids':['c1'],'evidence_ids':['e1'],'metric_ids':['m1']}}
ANSWER = {'answer':[{'heading':None,'text':'当前表现呈现分化，需要结合后续量能变化判断持续性。','evidence_ids':['e1'],'metric_refs':[]}],'follow_up_questions':[]}
OUTPUT = {'summary':'已有材料需要结合相反证据核查','selected_claim_ids':['c1'],
          'hypotheses':[{'text':'可能存在关联但尚未证实','supporting_evidence_ids':['e1'],
                         'counter_evidence_ids':[],'metric_refs':['m1'],'open_question':'还需哪些材料验证'}],
          'questions':[{'text':'来源是否覆盖所需范围','evidence_ids':['e1']}]}

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path/'data'))
    monkeypatch.setenv('SHIXU_CREDENTIAL_DIR',str(tmp_path/'credentials'))
    master=tmp_path/'master'; master.write_bytes(Fernet.generate_key())
    monkeypatch.setenv('SHIXU_CREDENTIAL_MASTER_KEY_FILE',str(master))
    for key in ('SHIXU_SQLITE_PATH','SHIXU_PUBLISHED_DIR','SHIXU_RAW_DIR','SHIXU_WORK_DIR'):
        monkeypatch.delenv(key,raising=False)
    core.ensure_ready()
    # These fixtures deliberately replay pre-agent frozen records. Current
    # every-round behavior is exercised in test_ai_agent_round.py.
    monkeypatch.setattr(service,'create_session',service._legacy_create_session)
    monkeypatch.setattr(service,'send_message',service._legacy_send_message)
    monkeypatch.setattr(service,'context_preview',lambda *args,**kwargs: {'eligible':True,'context_digest':'fixture-sha','context':CONTEXT})
    app=FastAPI();app.include_router(router)
    return TestClient(app)


def configure(client,provider='deepseek',model=None):
    result=client.put('/api/v1/ai/providers/'+provider,json={
        'expected_revision':0,'model':model or ('deepseek-flash' if provider=='deepseek' else 'gpt-6.1-sol'),
        'enabled':False,'api_key':'fixture-secret-'+provider})
    assert result.status_code==200,result.text
    return result.json()


def fake(request,key,cancel):
    assert key.startswith('fixture-secret-')
    return {'output': {'probe':'ok','missing':None} if request['operation']=='verify' else ANSWER if 'answer' in request['schema']['properties'] else OUTPUT,
            'usage':{'input_tokens':50,'cached_tokens':5,'output_tokens':30,'reasoning_tokens':10},
            'response_model':request['model'],'models':[request['model']]}


def drive(runtime,task_id):
    for _ in range(100):
        runtime.tick('fixture-worker')
        result=service.get_task(task_id)
        if result['state'] not in ('queued','running'):return result
        time.sleep(.005)
    pytest.fail('fake transport did not finish')


def enable(client,runtime,provider='deepseek'):
    row=configure(client,provider)
    response=client.post('/api/v1/ai/providers/'+provider+'/verify',json={'expected_revision':row['config_revision'],'request_key':'verify-'+provider})
    assert response.status_code==202,response.text
    assert drive(runtime,response.json()['task_id'])['state']=='completed'
    row=next(x for x in client.get('/api/v1/ai/providers').json()['items'] if x['provider_id']==provider)
    assert row['verification']['state']=='passed'
    result=client.put('/api/v1/ai/providers/'+provider,json={'expected_revision':row['config_revision'],'model':row['model'],'enabled':True})
    assert result.status_code==200,result.text
    return result.json()


def session(client,key='first',provider='deepseek',kind='stock'):
    payload={'kind':kind,'provider_id':provider,'context_digest':'fixture-sha','consent':True,'request_key':key}
    payload.update({'report_id':'report-fixture'} if kind=='stock' else {'as_of':'2026-10-30'})
    return client.post('/api/v1/ai/sessions',json=payload)


def test_disabled_defaults_and_write_only_keys(isolated):
    client=isolated
    rows=client.get('/api/v1/ai/providers').json()['items']
    assert len(rows)==2 and all(not x['enabled'] and not x['configured'] for x in rows)
    result=configure(client)
    assert result['configured'] and not result['enabled']
    assert 'fixture-secret' not in json.dumps(client.get('/api/v1/ai/providers').json())
    assert session(client).status_code==409
    response=client.put('/api/v1/ai/providers/deepseek',json={'expected_revision':0,'model':'deepseek-flash','enabled':False})
    assert response.status_code==409
    with core.connect() as db:
        # The only stored credential is an immutable file reference.
        assert 'fixture-secret' not in db.execute('SELECT credential_ref FROM ai_provider_config WHERE provider_id="deepseek"').fetchone()[0]
        assert db.execute('PRAGMA foreign_keys').fetchone()[0]==1
        assert db.execute('PRAGMA recursive_triggers').fetchone()[0]==1


def test_conversation_frozen_history_idempotency_and_usage(isolated):
    client=isolated;runtime=AIRuntime(fake)
    try:
        enable(client,runtime)
        first=session(client);assert first.status_code==202,first.text
        result=first.json();assert session(client).json()==result
        assert drive(runtime,result['task_id'])['state']=='completed'
        detail=client.get('/api/v1/ai/sessions/'+result['session_id']).json()
        assert detail['session']['context']==CONTEXT
        assert detail['messages'][-1]['interpretation']==ANSWER
        assert detail['messages'][-1]['cost']['state']=='estimated'
        stamp=detail['session']['expires_at_ms']
        assert client.get('/api/v1/ai/sessions/'+result['session_id']).json()['session']['expires_at_ms']==stamp
        payload={'text':'还有哪些相反证据需要核查','consent':True,'request_key':'follow-up'}
        response=client.post('/api/v1/ai/sessions/'+result['session_id']+'/messages',json=payload)
        assert response.status_code==202,response.text
        assert client.post('/api/v1/ai/sessions/'+result['session_id']+'/messages',json=payload).json()==response.json()
        assert client.post('/api/v1/ai/sessions/'+result['session_id']+'/messages',json={**payload,'text':'不同参数'}).status_code==409
        assert drive(runtime,response.json()['task_id'])['state']=='completed'
        assert len(client.get('/api/v1/ai/sessions/'+result['session_id']).json()['messages'])==4
        assert client.patch('/api/v1/ai/sessions/'+result['session_id'],json={'keep':True}).json()['keep']
        assert client.get('/api/v1/ai/sessions',params={'kind':'market'}).json()['items']==[]
        listing=client.get('/api/v1/ai/sessions',params={'kind':'stock'}).json()['items']
        assert len(listing)==1 and 'context' not in listing[0]
        assert client.delete('/api/v1/ai/sessions/'+result['session_id']).status_code==200
        assert client.get('/api/v1/ai/sessions/'+result['session_id']).status_code==404
        # Deleting a conversation must not erase today's accounting.
        with core.connect() as db:
            assert db.execute('SELECT count(*) FROM ai_task WHERE sent_at_ms IS NOT NULL').fetchone()[0]==3
            assert all(row[0]=='{}' for row in db.execute('SELECT request_json FROM ai_task'))
    finally:runtime.shutdown()


def test_configuration_changed_probe_does_not_activate_new_key(isolated):
    client=isolated;started=threading.Event();release=threading.Event()
    def delayed(request,key,cancel):
        started.set();release.wait(2);return fake(request,key,cancel)
    runtime=AIRuntime(delayed)
    try:
        row=configure(client)
        task=client.post('/api/v1/ai/providers/deepseek/verify',json={'expected_revision':row['config_revision'],'request_key':'old-probe'}).json()['task_id']
        runtime.tick('fixture');assert started.wait(1)
        response=client.put('/api/v1/ai/providers/deepseek',json={'expected_revision':1,'model':'deepseek-flash','enabled':False,'api_key':'fixture-secret-new'})
        assert response.status_code==200
        release.set()
        assert drive(runtime,task)['reason_code']=='configuration_changed'
        provider=client.get('/api/v1/ai/providers').json()['items'][0]
        assert provider['verification']['state']!='passed' and not provider['enabled']
    finally:release.set();runtime.shutdown()


def test_restart_does_not_repeat_sent_and_preserves_budget(isolated):
    client=isolated;runtime=AIRuntime(fake)
    try:
        enable(client,runtime)
        result=session(client).json()
        with core.connect() as db:
            db.execute("UPDATE ai_task SET state='running',sent_at_ms=?,day=?,reservation_nusd=123456 WHERE task_id=?",(core.now_ms(),service.day_key(),result['task_id']))
        runtime.recover_interrupted()
        task=service.get_task(result['task_id'])
        assert task['state']=='interrupted_manual_retry' and task['cost']['state']=='pending'
        runtime.tick('fixture')
        with core.connect() as db:
            assert db.execute('SELECT reservation_nusd FROM ai_task WHERE task_id=?',(result['task_id'],)).fetchone()[0]==123456
        assert service.providers()['usage']['requests']==2
    finally:runtime.shutdown()


def test_budget_combines_providers_and_cancellation_never_resends(isolated):
    client=isolated;runtime=AIRuntime(fake)
    try:
        enable(client,runtime,'deepseek');enable(client,runtime,'openai')
        with core.connect() as db:db.execute('UPDATE ai_instance_limits SET daily_request_limit=2 WHERE singleton=1')
        response=session(client,'over-budget','openai')
        assert response.status_code==429 and response.json()['detail']['reason_code']=='budget_exceeded'
        with core.connect() as db:db.execute('UPDATE ai_instance_limits SET daily_request_limit=3 WHERE singleton=1')
        cancelled=session(client,'cancel-before-sent').json()
        assert client.post('/api/v1/ai/tasks/'+cancelled['task_id']+'/cancel').json()['state']=='cancelled'
        runtime.tick('fixture')
        assert service.get_task(cancelled['task_id'])['sent_at_ms'] is None
        assert service.providers()['usage']['requests']==2
    finally:runtime.shutdown()


def test_no_free_retry_unknown_usage_and_provider_error_scrub(isolated):
    client=isolated
    def failure(*args):raise ProviderError('quota_exhausted')
    runtime=AIRuntime(fake)
    try:
        enable(client,runtime)
        runtime.transport=failure
        result=session(client).json()
        task=drive(runtime,result['task_id'])
        assert task['reason_code']=='quota_exhausted' and task['cost']['state']=='pending'
        runtime.tick('fixture')
        with core.connect() as db:
            assert db.execute('SELECT count(*) FROM ai_task').fetchone()[0]==2
        assert service.providers()['usage']['requests']==2
    finally:runtime.shutdown()


def test_expiry_and_keep_are_independent_of_research(isolated):
    client=isolated;runtime=AIRuntime(fake)
    try:
        enable(client,runtime)
        a=session(client,'expires').json();drive(runtime,a['task_id'])
        b=session(client,'kept',kind='market').json();drive(runtime,b['task_id'])
        client.patch('/api/v1/ai/sessions/'+b['session_id'],json={'keep':True})
        with core.connect() as db:db.execute('UPDATE ai_session SET expires_at_ms=0')
        assert client.get('/api/v1/ai/sessions/'+a['session_id']).status_code==404
        assert client.get('/api/v1/ai/sessions/'+b['session_id']).status_code==200
        with core.connect() as db: assert service._purge_expired(db)==1
        assert len(client.get('/api/v1/ai/sessions').json()['items'])==1
    finally:runtime.shutdown()


def test_unknown_model_prices_and_fixed_urls(isolated):
    client=isolated
    row=configure(client,model='future-model')
    response=client.post('/api/v1/ai/providers/deepseek/verify',json={'expected_revision':row['config_revision'],'request_key':'unknown-price'})
    assert response.status_code==409 and response.json()['detail']['reason_code']=='unknown_price'
    for provider in ('deepseek','openai'):
        payload=payload_for({'provider_id':provider,'model':'fixture','operation':'verify','messages':[],'schema':{}})
        assert payload['reasoning']['effort']==('none' if provider=='deepseek' else 'low')
        assert payload['text']['format']['strict'] and payload['store'] is False
    assert error_code(429,{'error':{'code':'insufficient_quota'}})=='quota_exhausted'
    assert error_code(429,{'error':{'code':'rate_limit_exceeded'}})=='rate_limited'
    assert estimated_nusd('openai','gpt-6.1-sol',100,50,20,10)==667000
    usage=usage_of({'usage':{'input_tokens':100,'output_tokens':50,'output_tokens_details':{'reasoning_tokens':20}}})
    assert usage['output_tokens']==50 and usage['reasoning_tokens']==20

@pytest.mark.parametrize('change,reason',[
    ({'summary':'增长10%需要关注'},'ungrounded_number'),
    ({'selected_claim_ids':['foreign']},'invalid_reference'),
    ({'selected_claim_ids':['c1','c1']},'invalid_reference'),
])
def test_strict_references_and_new_numbers(change,reason):
    with pytest.raises(ValueError,match=reason):validate_interpretation({**OUTPUT,**change},CONTEXT)


def test_drain_completes_even_when_general_work_blocks_new_starts(isolated):
    client=isolated;runtime=AIRuntime(fake)
    try:
        enable(client,runtime)
        result=session(client).json()
        runtime.tick('fixture')
        runtime.active[1].result(timeout=1)
        other=session(client,'wait-for-business').json()
        runtime.tick('fixture',allow_start=False)
        assert service.get_task(result['task_id'])['state']=='completed'
        assert service.get_task(other['task_id'])['state']=='queued'
        runtime.tick('fixture',allow_start=True)
        assert drive(runtime,other['task_id'])['state']=='completed'
    finally:runtime.shutdown()


def test_running_cancel_waits_for_network_close_and_keeps_uncertain_charge(isolated):
    client=isolated;runtime=AIRuntime(fake)
    started=threading.Event();closed=threading.Event()
    try:
        enable(client,runtime)
        def cancellable(request,key,cancel):
            started.set()
            try:
                if cancel.wait(2):raise ProviderError('cancelled')
                return fake(request,key,cancel)
            finally:closed.set()
        runtime.transport=cancellable
        result=session(client).json()
        runtime.tick('fixture');assert started.wait(1)
        response=client.post('/api/v1/ai/tasks/'+result['task_id']+'/cancel')
        assert response.json()['state']=='running'  # Await actual connection release.
        task=drive(runtime,result['task_id'])
        assert closed.is_set() and task['state']=='cancelled' and task['cost']['state']=='pending'
        assert service.providers()['usage']['requests']==2
    finally:runtime.shutdown()


def test_context_changed_missing_consent_and_input_cap_never_send(isolated,monkeypatch):
    client=isolated;runtime=AIRuntime(fake)
    try:
        enable(client,runtime)
        original=service.context_preview
        monkeypatch.setattr(service,'context_preview',lambda *a,**kw:{'eligible':True,'context_digest':'new-sha','context':CONTEXT})
        assert session(client).json()['detail']['reason_code']=='context_changed'
        monkeypatch.setattr(service,'context_preview',original)
        payload={'kind':'stock','report_id':'report-fixture','provider_id':'deepseek','context_digest':'fixture-sha','consent':False,'request_key':'no-consent'}
        assert client.post('/api/v1/ai/sessions',json=payload).status_code==422
        huge={**CONTEXT,'evidence':[{'evidence_id':'e1','data':{'text':'测'*50000}}]}
        monkeypatch.setattr(service,'context_preview',lambda *a,**kw:{'eligible':True,'context_digest':'fixture-sha','context':huge})
        assert session(client).json()['detail']['reason_code']=='input_too_large'
        with core.connect() as db:assert db.execute('SELECT count(*) FROM ai_task').fetchone()[0]==1
    finally:runtime.shutdown()


def test_concurrent_same_request_key_creates_one_local_attempt(isolated):
    from concurrent.futures import ThreadPoolExecutor
    client=isolated;runtime=AIRuntime(fake)
    try:
        enable(client,runtime)
        payload={'kind':'stock','report_id':'report-fixture','provider_id':'deepseek','context_digest':'fixture-sha','consent':True,'request_key':'concurrent-key'}
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:service.create_session(payload),range(8)))
        assert all(result==results[0] for result in results)
        with core.connect() as db:
            assert db.execute("SELECT count(*) FROM ai_task WHERE operation='message'").fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM ai_session').fetchone()[0]==1
    finally:runtime.shutdown()


def test_pending_provider_usage_cannot_be_understated_by_broken_details(isolated):
    client=isolated;runtime=AIRuntime(fake)
    try:
        enable(client,runtime)
        def malformed(*args):
            return {**fake(*args),'usage':{'input_tokens':10,'cached_tokens':20,'output_tokens':5,'reasoning_tokens':10}}
        runtime.transport=malformed
        result=session(client).json();task=drive(runtime,result['task_id'])
        assert task['state']=='completed' and task['cost']['state']=='pending'
        with core.connect() as db:
            assert db.execute('SELECT reservation_nusd FROM ai_task WHERE task_id=?',(result['task_id'],)).fetchone()[0]>0
    finally:runtime.shutdown()


def test_sqlite_parent_delete_cannot_erase_daily_receipts_or_send_orphan(isolated):
    client=isolated;runtime=AIRuntime(fake)
    try:
        enable(client,runtime)
        complete=session(client,'completed-to-delete').json();drive(runtime,complete['task_id'])
        queued=session(client,'queued-to-delete').json()
        with core.connect() as db:
            db.execute('DELETE FROM ai_session')
            assert db.execute('SELECT count(*) FROM ai_task').fetchone()[0]==3
            assert db.execute('SELECT count(*) FROM ai_task WHERE session_id IS NOT NULL').fetchone()[0]==0
        assert drive(runtime,queued['task_id'])['reason_code']=='session_deleted'
        assert service.get_task(queued['task_id'])['sent_at_ms'] is None
        assert service.providers()['usage']['requests']==2
        assert session(client,'completed-to-delete').status_code==409
    finally:runtime.shutdown()


def test_interrupted_odd_message_history_stays_within_forty_message_bound(isolated):
    client=isolated;runtime=AIRuntime(fake)
    try:
        enable(client,runtime)
        result=session(client).json();drive(runtime,result['task_id'])
        with core.connect() as db:
            for number in range(37):
                db.execute('INSERT INTO ai_message VALUES(?,?,?,?,?,?,?,?)',('extra-'+str(number),result['session_id'],None,'user','测试中断记录',None,'completed',number+1))
        response=client.post('/api/v1/ai/sessions/'+result['session_id']+'/messages',json={'text':'测试追问','consent':True,'request_key':'too-many-messages'})
        assert response.status_code==409 and response.json()['detail']['reason_code']=='message_capacity'
    finally:runtime.shutdown()


def test_quick_disable_reenable_cannot_revive_inflight_result(isolated):
    client=isolated;runtime=AIRuntime(fake);started=threading.Event();release=threading.Event()
    try:
        provider=enable(client,runtime)
        def delayed(*args):
            started.set();release.wait(2);return fake(*args)
        runtime.transport=delayed
        result=session(client).json();runtime.tick('fixture');assert started.wait(1)
        disabled=client.put('/api/v1/ai/providers/deepseek',json={'expected_revision':provider['config_revision'],'model':provider['model'],'enabled':False})
        assert disabled.status_code==200
        enabled=client.put('/api/v1/ai/providers/deepseek',json={'expected_revision':disabled.json()['config_revision'],'model':provider['model'],'enabled':True})
        assert enabled.status_code==200
        release.set();task=drive(runtime,result['task_id'])
        assert task['state']=='failed' and task['reason_code']=='configuration_changed'
        detail=client.get('/api/v1/ai/sessions/'+result['session_id']).json()
        assert detail['messages'][-1]['interpretation'] is None
    finally:release.set();runtime.shutdown()


def test_attempt_uses_frozen_prices_even_if_catalog_changes_before_finish(isolated,monkeypatch):
    from app.services import ai_catalog
    client=isolated;runtime=AIRuntime(fake)
    try:
        enable(client,runtime)
        result=session(client).json()
        rates=next(item for item in ai_catalog.MODELS['deepseek'] if item['id']=='deepseek-flash')
        old=dict(rates)
        monkeypatch.setitem(rates,'output','999')
        task=drive(runtime,result['task_id'])
        expected=estimated_nusd('deepseek','deepseek-flash',50,30,5,rates=old)
        assert task['cost']['amount']==ai_catalog.usd(expected)
        assert task['cost']['price_version']=='pricing-2026-11-02'
        client.delete('/api/v1/ai/sessions/'+result['session_id'])
        with core.connect() as db:
            receipt=json.loads(db.execute('SELECT result_json FROM ai_task WHERE task_id=?',(result['task_id'],)).fetchone()[0])
            assert receipt['price_rates']==old and 'output' not in receipt
    finally:runtime.shutdown()


def test_v3_prompt_limits_match_published_prompt_artifact():
    from pathlib import Path
    published=(Path(__file__).resolve().parents[2]/'contracts/prompts/ai-evidence-chat-v3.md').read_text()
    frozen=published.split('```text\n',1)[1].split('\n```',1)[0]
    assert service.SYSTEM_PROMPT==frozen and service.PROMPT_VERSION=='ai-evidence-chat-v3'
    for rule in ('1至5个回答块','最多40字符','最多900字符','合计最多2600字符',
                 'evidence_ids为1至8个','metric_refs为0至8个','0至3条','最多150字符',
                 '{{metric:完整metric_id}}','用户最后的问题'):
        assert rule in service.SYSTEM_PROMPT


def test_v3_new_sessions_preserve_old_v1_schema_and_prompt(isolated,monkeypatch):
    from app.services.ai_validation import output_schema
    client=isolated;requests=[]
    def capture(request,key,cancel):
        requests.append(request)
        return fake(request,key,cancel)
    runtime=AIRuntime(capture)
    try:
        enable(client,runtime)
        current_protocol=service._protocol
        old_protocol={'prompt_version':'ai-evidence-chat-v2','output_schema_version':'ai-research-interpret-v1',
                      'system_prompt':'旧会话冻结提示词，仅测试','schema':output_schema()}
        monkeypatch.setattr(service,'_protocol',lambda:dict(old_protocol))
        old=session(client,'old-frozen-v1').json();drive(runtime,old['task_id'])
        assert client.get('/api/v1/ai/sessions/'+old['session_id']).json()['messages'][-1]['interpretation']==OUTPUT
        monkeypatch.setattr(service,'_protocol',current_protocol)
        follow=client.post('/api/v1/ai/sessions/'+old['session_id']+'/messages',json={'text':'继续核查原材料','consent':True,'request_key':'old-follow-after-v3'}).json()
        drive(runtime,follow['task_id'])
        assert requests[-1]['prompt_version']=='ai-evidence-chat-v2'
        assert requests[-1]['messages'][0]['content']=='旧会话冻结提示词，仅测试'
        new=session(client,'new-v3').json();drive(runtime,new['task_id'])
        assert requests[-1]['prompt_version']=='ai-evidence-chat-v3'
        assert 'answer' in requests[-1]['schema']['properties']
        with core.connect() as db:
            old_saved=json.loads(db.execute('SELECT protocol_json FROM ai_session WHERE session_id=?',(old['session_id'],)).fetchone()[0])
            new_saved=json.loads(db.execute('SELECT protocol_json FROM ai_session WHERE session_id=?',(new['session_id'],)).fetchone()[0])
            assert old_saved==old_protocol
            assert new_saved['schema_version']==2 and new_saved['output_schema_version']=='ai-evidence-answer-v2'
    finally:runtime.shutdown()


def test_first_message_uses_user_question_and_binds_seen_configuration(isolated):
    client=isolated;captured=[]
    def capture(request,key,cancel):
        captured.append(request);return fake(request,key,cancel)
    runtime=AIRuntime(capture)
    try:
        provider=enable(client,runtime)
        payload={'kind':'market','as_of':'2026-10-30','provider_id':'deepseek','context_digest':'fixture-sha',
                 'text':'今天的主要分化在哪里？','consent':True,'request_key':'actual-first-question',
                 'expected_config_revision':provider['config_revision'],'expected_model':provider['model']}
        response=client.post('/api/v1/ai/sessions',json=payload)
        assert response.status_code==202,response.text
        result=response.json();assert drive(runtime,result['task_id'])['state']=='completed'
        assert captured[-1]['messages'][-1]['content']==payload['text']
        detail=client.get('/api/v1/ai/sessions/'+result['session_id']).json()
        assert detail['messages'][0]['text']==payload['text']
        assert client.post('/api/v1/ai/sessions',json={**payload,'text':'不同问题'}).status_code==409
        wrong=client.post('/api/v1/ai/sessions',json={**payload,'request_key':'wrong-config','expected_model':'deepseek-v4-pro'})
        assert wrong.status_code==409 and wrong.json()['detail']['reason_code']=='configuration_changed'
        wrong=client.post('/api/v1/ai/sessions',json={**payload,'request_key':'wrong-revision','expected_config_revision':0})
        assert wrong.status_code==409
        missing=session(client,'natural-market-default',kind='market').json();drive(runtime,missing['task_id'])
        assert captured[-1]['messages'][-1]['content']=='请复盘所选交易日，说明整体表现、主要分化及后续需要观察的信号。'
    finally:runtime.shutdown()


def test_new_session_model_projection_is_frozen_for_followups(isolated,monkeypatch):
    from app.services import ai_contexts
    client=isolated;captured=[]
    def capture(request,key,cancel):
        captured.append(request);return fake(request,key,cancel)
    runtime=AIRuntime(capture)
    try:
        enable(client,runtime)
        result=session(client,'frozen-projection').json();drive(runtime,result['task_id'])
        first_input=captured[-1]['messages'][1]['content']
        with core.connect() as db:
            protocol=json.loads(db.execute('SELECT protocol_json FROM ai_session WHERE session_id=?',(result['session_id'],)).fetchone()[0])
            assert protocol['model_input_sha256']==core.digest(protocol['model_context'])
            assert protocol['model_context']['allowed_ids']==CONTEXT['allowed_ids']
        monkeypatch.setattr(ai_contexts,'model_input',lambda _:pytest.fail('an existing session must not rerun its projection'))
        follow=client.post('/api/v1/ai/sessions/'+result['session_id']+'/messages',json={'text':'后续该看什么？','consent':True,'request_key':'frozen-projection-follow'}).json()
        assert drive(runtime,follow['task_id'])['state']=='completed'
        assert captured[-1]['messages'][1]['content']==first_input
    finally:runtime.shutdown()


def test_request_date_meta_is_dynamic_only_for_new_opted_protocols(monkeypatch):
    protocol=service._protocol()
    stamps=iter([1793577600000,1793664000000])
    monkeypatch.setattr(service,'now_ms',lambda:next(stamps))
    a=service._outbound(CONTEXT,[{'role':'user','text':'今天怎么样？'}],protocol)
    b=service._outbound(CONTEXT,[{'role':'user','text':'今天怎么样？'}],protocol)
    assert a['request_time']['selected_as_of']=='2026-10-30'
    assert a['request_time']['request_date_bj']!=b['request_time']['request_date_bj']
    assert a['messages'][1]==b['messages'][1]  # Frozen material itself never changes.
    assert '历史交易日' in protocol['system_prompt']
    legacy={key:value for key,value in protocol.items() if key!='request_time_context'}
    unchanged=service._outbound(CONTEXT,[{'role':'user','text':'今天怎么样？'}],legacy)
    assert 'request_time' not in unchanged and len(unchanged['messages'])==3


def test_history_titles_use_first_local_question_without_renewing_or_writing(isolated):
    client=isolated;runtime=AIRuntime(fake)
    try:
        enable(client,runtime)
        question='这是我想找回的首条问题'+('内容'*40)
        response=client.post('/api/v1/ai/sessions',json={'kind':'market','as_of':'2026-10-30','provider_id':'deepseek',
            'context_digest':'fixture-sha','text':question,'consent':True,'request_key':'history-title'})
        result=response.json();drive(runtime,result['task_id'])
        detail=client.get('/api/v1/ai/sessions/'+result['session_id']).json()
        expiry=detail['session']['expires_at_ms']
        assert detail['session']['title']==question[:60]
        summary=client.get('/api/v1/ai/sessions',params={'kind':'market','limit':1}).json()['items'][0]
        assert summary['title']==question[:60] and summary['expires_at_ms']==expiry and 'context' not in summary
        with core.connect() as db:
            assert db.execute("SELECT text FROM ai_message WHERE session_id=? AND role='user'",(result['session_id'],)).fetchone()[0]==question
    finally:runtime.shutdown()


def test_new_protocol_accepts_complete_context_above_48k_legacy_stays_bounded():
    from copy import deepcopy
    from fastapi import HTTPException
    from app.services.ai_catalog import MAX_INPUT_BYTES, MAX_RESPONSE_BYTES
    context=deepcopy(CONTEXT)
    context['evidence']=[{'evidence_id':'e1' if number==0 else 'e'+str(number+1),
                          'source':'fixture','kind':'test','data':{'text':'a'*3000}} for number in range(20)]
    context['allowed_ids']['evidence_ids']=[row['evidence_id'] for row in context['evidence']]
    assert len(core.dumps(context).encode())<64*1024
    protocol=service._protocol()
    request=service._outbound(context,[{'role':'user','text':'请解释已留存资料的变化'}],protocol)
    size=service._input_size({'provider_id':'openai','model':'gpt-6.1-sol'},request,'message')
    assert 48*1024<size<96*1024
    assert protocol['max_input_bytes']==request['max_input_bytes']==MAX_INPUT_BYTES==96*1024
    assert MAX_RESPONSE_BYTES==192*1024
    legacy={key:value for key,value in protocol.items() if key!='max_input_bytes'}
    with pytest.raises(HTTPException) as error:
        service._outbound(context,[{'role':'user','text':'请解释已留存资料的变化'}],legacy)
    assert error.value.status_code==422 and error.value.detail['reason_code']=='input_too_large'
    assert service._web_protocol()['max_input_bytes']==96*1024


def test_budget_reserves_actual_payload_not_larger_input_ceiling(isolated):
    from copy import deepcopy
    client=isolated;runtime=AIRuntime(fake)
    try:
        enable(client,runtime)
        request=service._outbound(CONTEXT,[{'role':'user','text':'核对实际输入费用'}],service._protocol())
        old=deepcopy(request);old['max_input_bytes']=48*1024
        assert service._input_size({'provider_id':'deepseek','model':'deepseek-flash'},request,'message')==service._input_size({'provider_id':'deepseek','model':'deepseek-flash'},old,'message')
        with core.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row,limits=service._eligible_provider(db,'deepseek')
            a=service._enqueue(db,row,limits,'message','cap96','digest96',request)
            b=service._enqueue(db,row,limits,'message','cap48','digest48',old)
            reservations=[db.execute('SELECT reservation_nusd FROM ai_task WHERE task_id=?',(item['task_id'],)).fetchone()[0] for item in (a,b)]
            assert reservations[0]==reservations[1]
            assert limits['daily_budget_nusd']==500000000
    finally:runtime.shutdown()


def test_full_provider_schema_counts_against_frozen_input_limit(isolated):
    client=isolated;runtime=AIRuntime(fake)
    try:
        enable(client,runtime)
        request={'messages':[{'role':'user','content':'a'*(48*1024-200)}],
                 'schema':{'type':'object','description':'schema-overhead'*300},'max_input_bytes':48*1024}
        assert len(core.dumps(request['messages']).encode())<48*1024
        with core.connect() as db:
            row,limits=service._eligible_provider(db,'deepseek')
            with pytest.raises(HTTPException) as error:
                service._enqueue(db,row,limits,'message','full-overhead','full-overhead',request)
            assert error.value.detail['reason_code']=='input_too_large'
    finally:runtime.shutdown()
