"""Hosted web mode uses native bounded citations and conservative shared billing."""
from copy import deepcopy
import json
import threading
import pytest
import httpx
from app import core
from app.adapters import ai_responses as adapter
from app.services import ai_web, ai_service as service
from app.services.ai_runtime import AIRuntime
from test_ai_service import isolated, enable, drive, fake

URL='https://www.sse.com.cn/disclosure/listedinfo/announcement/c/new/fixture.htm'
TEXT='今天🙂指数上涨1%，后续应关注量能和板块分化。'


def body(*,used=True):
    start=TEXT.index('指数');end=start+len('指数上涨1%')
    output=[]
    if used:
        output.append({'type':'web_search_call','id':'ws_fixture','status':'completed',
                       'action':{'type':'search','query':'fixture','sources':[{'type':'url','url':URL,'title':'测试交易所来源'}]}})
    output.append({'type':'message','content':[{'type':'output_text','text':TEXT,
                   'annotations':[{'type':'url_citation','start_index':start,'end_index':end,'url':URL,'title':'测试交易所来源'}] if used else []}]})
    return {'status':'completed','model':'gpt-6.1-sol','output':output,
            'usage':{'input_tokens':100,'output_tokens':50,'input_tokens_details':{'cached_tokens':0}}}


def request():
    return {'provider_id':'openai','model':'gpt-6.1-sol','operation':'message','messages':[{'role':'user','content':'fixture'}],
            'allow_web':True,'web_policy':ai_web.policy(),'requested_as_of':'2026-10-30','timeout_seconds':30}


def create(client,key='web-first',**changes):
    payload={'kind':'market','as_of':'2026-10-30','provider_id':'openai','context_digest':'fixture-sha',
             'text':'请查交易所最新公告，解释今天的市场分化。','consent':True,'allow_web':True,'request_key':key,**changes}
    return client.post('/api/v1/ai/sessions',json=payload)


def web_fake(request,key,cancel):
    if request.get('allow_web'):
        raw=body()
        return {'output':ai_web.native_answer(raw,request),'usage':adapter.usage_of(raw),'response_model':'gpt-6.1-sol',
                'web_accounting':{'search_call_count':1,'used':True,'complete':True,'search_content_in_input':None}}
    return fake(request,key,cancel)


def test_native_unicode_offsets_sources_and_original_numbers_are_preserved():
    result=ai_web.native_answer(body(),request())
    cite=result['citations'][0]
    assert result['text'][cite['start_index']:cite['end_index']]=='指数上涨1%'
    assert cite['start_index']==3  # Python Unicode code points; UTF-16 offset would be 4.
    assert result['sources'][0]['published_at'] is None
    assert result['sources'][0]['url']==cite['url'] and result['sources'][0]['source_id']==cite['source_id']
    assert result['web']['validation']=='native_citations_only' and result['web']['requested_as_of']=='2026-10-30'


@pytest.mark.parametrize('url',[
    'javascript:alert(1)','data:text/html,evil','https://sse.com.cn.evil.example/a',
    'https://evil.example/sse.com.cn','https://user:secret@sse.com.cn/a','https://sse.com.cn:999/a',
    'http://127.0.0.1/a','https://sse.com.cn/a\nInjected','https://%73se.com.cn/a',
])
def test_malicious_or_out_of_policy_native_citation_urls_are_rejected(url):
    raw=body();raw['output'][0]['action']['sources'][0]['url']=url
    raw['output'][1]['content'][0]['annotations'][0]['url']=url
    with pytest.raises(ValueError,match='invalid_web_source'):ai_web.native_answer(raw,request())


@pytest.mark.parametrize('start,end',[(True,8),(-1,2),(4,999),(8,4)])
def test_invalid_native_offsets_are_rejected(start,end):
    raw=body();raw['output'][1]['content'][0]['annotations'][0].update(start_index=start,end_index=end)
    with pytest.raises(ValueError,match='invalid_web_citation'):ai_web.native_answer(raw,request())


def test_native_annotation_provenance_is_independent_of_extra_consulted_list():
    raw=body();raw['output'][0]['action']['sources']=[]
    result=ai_web.native_answer(raw,request())
    assert result['sources'][0]['provenance']=='native_annotation'
    assert result['sources'][0]['annotation_only'] is True and result['sources'][0]['consulted_match'] is False
    assert result['web']['annotation_only_source_count']==1
    raw=body();raw['output'][1]['content'][0]['annotations']=[]
    with pytest.raises(ValueError,match='web_citations_missing'):ai_web.native_answer(raw,request())
    raw=body();raw['output'].insert(0,deepcopy(raw['output'][0]))
    with pytest.raises(ValueError,match='web_limits_exceeded'):ai_web.native_answer(raw,request())


def test_hosted_tool_payload_has_fixed_domains_one_auto_call_and_no_json_schema():
    payload=adapter.payload_for(request())
    assert payload['max_tool_calls']==1 and payload['tool_choice']=='auto'
    assert payload['tools'][0]=={'type':'web_search','filters':{'allowed_domains':list(ai_web.DOMAINS)},
                                 'search_context_size':'low','return_token_budget':'default'}
    assert payload['include']==['web_search_call.action.sources'] and 'text' not in payload
    assert payload['store'] is False and payload['reasoning']=={'effort':'low'}
    assert ai_web.extra_reservation_nusd({'input':'2','cache_write':'2.5'},ai_web.policy())==330000000


def test_web_lifecycle_keeps_cost_pending_sources_local_and_mode_frozen(isolated):
    client=isolated;runtime=AIRuntime(web_fake)
    try:
        enable(client,runtime,'openai')
        response=create(client);assert response.status_code==202,response.text
        result=response.json();task=drive(runtime,result['task_id'])
        assert task['state']=='completed' and task['cost']['state']=='pending' and task['cost']['amount'] is None
        detail=client.get('/api/v1/ai/sessions/'+result['session_id']).json()
        assert detail['session']['mode']=='web' and detail['session']['allow_web'] is True
        message=detail['messages'][-1]
        assert message['response_type']=='web_answer' and message['interpretation'] is None
        assert message['text']==TEXT and message['citations'][0]['url']==URL and message['sources'][0]['published_at'] is None
        with core.connect() as db:
            row=db.execute('SELECT * FROM ai_task WHERE task_id=?',(result['task_id'],)).fetchone()
            receipt=json.loads(row['result_json'])
            assert row['reservation_nusd']>=330000000 and receipt['web_policy']['tool_nusd']==10000000
            assert receipt['web_accounting']['search_content_in_input'] is None
            assert row['request_json']=='{}'
        wrong=client.post('/api/v1/ai/sessions/'+result['session_id']+'/messages',json={'text':'继续解释','consent':True,'request_key':'wrong-mode','allow_web':False})
        assert wrong.status_code==409 and wrong.json()['detail']['reason_code']=='mode_conflict'
        # GETs never create another outbound attempt or renew retention.
        for path in ('/providers','/sessions','/sessions/'+result['session_id'],'/tasks/'+result['task_id']):
            assert client.get('/api/v1/ai'+path).status_code==200
        assert service.providers()['usage']['requests']==2
        assert client.delete('/api/v1/ai/sessions/'+result['session_id']).status_code==200
        assert service.get_task(result['task_id'])['cost']['state']=='pending'
        with core.connect() as db:
            receipt=json.loads(db.execute('SELECT result_json FROM ai_task WHERE task_id=?',(result['task_id'],)).fetchone()[0])
            assert receipt['web_policy']['price_version']==ai_web.PRICE_VERSION
            assert 'sources' not in receipt and 'text' not in receipt
    finally:runtime.shutdown()


def test_web_rejects_deepseek_and_other_models_without_auto_switch(isolated):
    client=isolated;runtime=AIRuntime(fake)
    try:
        enable(client,runtime,'deepseek')
        response=create(client,provider_id='deepseek')
        assert response.status_code==422 and response.json()['detail']['reason_code']=='web_search_unavailable'
        assert service.providers()['usage']['requests']==1
    finally:runtime.shutdown()


def test_web_reservation_uses_shared_budget_before_send(isolated):
    client=isolated;runtime=AIRuntime(web_fake)
    try:
        enable(client,runtime,'openai')
        with core.connect() as db:db.execute('UPDATE ai_instance_limits SET daily_budget_nusd=330000000 WHERE singleton=1')
        response=create(client)
        assert response.status_code==429 and response.json()['detail']['reason_code']=='budget_exceeded'
        with core.connect() as db:assert db.execute('SELECT count(*) FROM ai_task').fetchone()[0]==1
    finally:runtime.shutdown()


def test_web_cancellation_releases_network_but_not_uncertain_fee(isolated):
    client=isolated;runtime=AIRuntime(web_fake);started=threading.Event();closed=threading.Event()
    try:
        enable(client,runtime,'openai')
        def blocked(request,key,cancel):
            started.set()
            try:
                cancel.wait(2);raise adapter.ProviderError('cancelled')
            finally:closed.set()
        runtime.transport=blocked
        result=create(client).json();runtime.tick('fixture');assert started.wait(1)
        client.post('/api/v1/ai/tasks/'+result['task_id']+'/cancel')
        task=drive(runtime,result['task_id'])
        assert closed.is_set() and task['state']=='cancelled' and task['cost']['state']=='pending'
        with core.connect() as db:assert db.execute('SELECT reservation_nusd FROM ai_task WHERE task_id=?',(result['task_id'],)).fetchone()[0]>=330000000
    finally:runtime.shutdown()


def test_auto_without_search_releases_extra_reservation_after_known_usage(isolated):
    client=isolated
    def no_search(request,key,cancel):
        if request.get('allow_web'):
            raw=body(used=False)
            return {'output':ai_web.native_answer(raw,request),'usage':adapter.usage_of(raw),'response_model':'gpt-6.1-sol',
                    'web_accounting':{'search_call_count':0,'used':False,'complete':True,'search_content_in_input':None}}
        return fake(request,key,cancel)
    runtime=AIRuntime(no_search)
    try:
        enable(client,runtime,'openai')
        result=create(client).json();task=drive(runtime,result['task_id'])
        assert task['state']=='completed' and task['cost']['state']=='estimated'
        assert client.get('/api/v1/ai/sessions/'+result['session_id']).json()['messages'][-1]['web']['used'] is False
    finally:runtime.shutdown()


def test_adapter_reads_plain_text_native_sources_without_freeform_json_parse(monkeypatch):
    actual=httpx.AsyncClient
    async def handler(req):
        payload=json.loads(req.content)
        assert payload['max_tool_calls']==1 and req.url.host=='api.openai.com'
        return httpx.Response(200,json=body())
    monkeypatch.setattr(adapter.httpx,'AsyncClient',lambda **kwargs:actual(**kwargs,transport=httpx.MockTransport(handler)))
    result=adapter.perform(request(),'fixture-private',threading.Event())
    assert result['output']['text']==TEXT and result['web_accounting']['search_content_in_input'] is None
    assert 'fixture-private' not in json.dumps(result)


def test_proven_total_input_bills_search_once_without_content_double_count(isolated):
    client=isolated
    def known(request,key,cancel):
        result=web_fake(request,key,cancel)
        if request.get('allow_web'):result['web_accounting']['search_content_in_input']=True
        return result
    runtime=AIRuntime(known)
    try:
        enable(client,runtime,'openai')
        result=create(client).json();task=drive(runtime,result['task_id'])
        assert task['cost']['state']=='estimated' and task['cost']['amount']=='0.0107'
    finally:runtime.shutdown()


@pytest.mark.parametrize('model',['gpt-6-luna','gpt-6-astra'])
def test_web_does_not_silently_upgrade_another_openai_model(isolated,model):
    client=isolated;runtime=AIRuntime(fake)
    try:
        provider=enable(client,runtime,'openai')
        saved=client.put('/api/v1/ai/providers/openai',json={'expected_revision':provider['config_revision'],'model':model,'enabled':False}).json()
        probe=client.post('/api/v1/ai/providers/openai/verify',json={'expected_revision':saved['config_revision'],'request_key':'verify-other-model'}).json()
        assert drive(runtime,probe['task_id'])['state']=='completed'
        enabled=client.put('/api/v1/ai/providers/openai',json={'expected_revision':saved['config_revision'],'model':model,'enabled':True})
        assert enabled.status_code==200
        response=create(client)
        assert response.status_code==422 and response.json()['detail']['reason_code']=='web_search_unavailable'
        assert next(x for x in service.providers()['items'] if x['provider_id']=='openai')['model']==model
    finally:runtime.shutdown()


def test_web_v3_honors_current_question_and_preserves_frozen_v1_v2_prompts():
    from pathlib import Path
    from test_ai_service import CONTEXT
    root=Path(__file__).resolve().parents[2]
    old_text=(root/'contracts/prompts/ai-web-chat-v1.md').read_text().split('```text\n',1)[1].split('\n```',1)[0]
    new_text=(root/'contracts/prompts/ai-web-chat-v3.md').read_text().split('```text\n',1)[1].split('\n```',1)[0]
    assert ai_web.PROMPT_VERSION=='ai-web-chat-v3' and ai_web.SYSTEM_PROMPT==new_text
    assert '不替用户把问题改成历史复盘' in new_text and '本地历史快照与现在网上补查' in new_text
    old=service._web_protocol()
    old.update(prompt_version='ai-web-chat-v1',system_prompt=old_text)
    old=json.loads(json.dumps(old))  # Simulate a previously saved protocol.
    request=service._outbound(CONTEXT,[{'role':'user','text':'查一下今天最新的公告'}],old)
    assert request['prompt_version']=='ai-web-chat-v1' and request['messages'][0]['content']==old_text
    current=service._outbound(CONTEXT,[{'role':'user','text':'查一下今天最新的公告'}],service._web_protocol())
    assert current['prompt_version']=='ai-web-chat-v3' and current['messages'][0]['content']==new_text
    assert current['messages'][-1]['content']=='查一下今天最新的公告'
    v2_text=(root/'contracts/prompts/ai-web-chat-v2.md').read_text().split('```text\n',1)[1].split('\n```',1)[0]
    old_v2=service._web_protocol()
    old_v2.update(prompt_version='ai-web-chat-v2',system_prompt=v2_text)
    previous=service._outbound(CONTEXT,[{'role':'user','text':'继续原会话'}],old_v2)
    assert previous['prompt_version']=='ai-web-chat-v2' and previous['messages'][0]['content']==v2_text


@pytest.mark.parametrize('url',[
    'https://gubaposthk.eastmoney.com/stock/fixture.html',
    'https://guba.eastmoney.com/news,fixture.html',
    'https://caifuhao.eastmoney.com/news/fixture',
    'https://eastmoney.com/fixture',
    'https://10jqka.com.cn/fixture',
    'https://bbs.10jqka.com.cn/fixture',
])
def test_new_policy_excludes_forums_self_media_and_generic_roots(url):
    assert ai_web.POLICY_VERSION=='ai-web-official-domains-v2'
    with pytest.raises(ValueError,match='invalid_web_source'):
        ai_web.safe_url(url,ai_web.policy()['allowed_domains'])


@pytest.mark.parametrize('host',[
    'data.eastmoney.com','finance.eastmoney.com','emweb.securities.eastmoney.com','quote.eastmoney.com',
    'basic.10jqka.com.cn','news.10jqka.com.cn','www.cninfo.com.cn','www.sse.com.cn','www.szse.cn','www.bse.cn',
])
def test_new_policy_retains_named_data_and_official_domains(host):
    url='https://'+host+'/fixture'
    assert ai_web.safe_url(url,ai_web.policy()['allowed_domains'])==url


def test_historical_policy_and_native_sources_are_not_reinterpreted_by_new_policy():
    old=request()
    old['web_policy']={**old['web_policy'],'version':'ai-web-official-domains-v1',
                       'allowed_domains':['cninfo.com.cn','sse.com.cn','szse.cn','bse.cn','eastmoney.com','10jqka.com.cn']}
    forum='https://gubaposthk.eastmoney.com/fixture.html'
    raw=body()
    raw['output'][0]['action']['sources'][0]['url']=forum
    raw['output'][1]['content'][0]['annotations'][0]['url']=forum
    historical=ai_web.native_answer(raw,old)
    assert historical['sources'][0]['url']==forum
    with pytest.raises(ValueError,match='invalid_web_source'):
        ai_web.native_answer(raw,request())
    assert '官方原文' in ai_web.SYSTEM_PROMPT and '股吧论坛' in ai_web.SYSTEM_PROMPT


def test_many_unreferenced_native_candidates_do_not_reject_cited_answer():
    raw=body()
    raw['output'][0]['action']['sources'].extend(
        {'type':'url','url':'https://www.sse.com.cn/unused/'+str(n)+'.pdf','title':'未引用候选'+str(n)}
        for n in range(100))
    result=ai_web.native_answer(raw,request())
    assert result['text']==TEXT and len(result['citations'])==1 and len(result['sources'])==1
    assert result['sources'][0]['url']==URL and result['citations'][0]['source_id']==result['sources'][0]['source_id']
    assert result['web']['consulted_source_count']==101
    assert result['web']['retained_source_count']==1
    assert result['web']['omitted_unreferenced_source_count']==100
    assert len(json.dumps(result,ensure_ascii=False).encode())<64*1024


def test_unreferenced_unsafe_candidate_is_omitted_but_cited_unsafe_url_still_fails():
    raw=body()
    raw['output'][0]['action']['sources'].append({'type':'url','url':'https://evil.example/unreferenced','title':'bad'})
    result=ai_web.native_answer(raw,request())
    assert result['web']['invalid_unreferenced_source_count']==1 and result['sources'][0]['url']==URL
    raw['output'][1]['content'][0]['annotations'][0]['url']='https://evil.example/unreferenced'
    with pytest.raises(ValueError,match='invalid_web_source'):ai_web.native_answer(raw,request())


def test_native_citation_url_alias_does_not_claim_an_exact_consulted_match():
    raw=body();raw['output'][0]['action']['sources']=[{'type':'url','url':'https://www.sse.com.cn/unused/'+str(n)} for n in range(100)]
    result=ai_web.native_answer(raw,request())
    assert result['citations'][0]['url']==URL and result['sources'][0]['url']==URL
    assert result['sources'][0]['annotation_only'] and not result['sources'][0]['consulted_match']
    assert result['web']['omitted_unreferenced_source_count']==100 and result['web']['annotation_only_source_count']==1


def ignored_attempt():
    # The attempted open may target the same cited PDF. Its URL never supplies
    # provenance: only the final response's native annotations create links.
    return {'type':'web_search_call','id':'ws_ignored','status':'searching',
            'action':{'type':'open_page','url':URL,'sources':[]}}


def test_completed_search_can_keep_citations_when_empty_trailing_attempt_is_ignored():
    raw=body();raw['output'].insert(1,ignored_attempt())
    result=ai_web.native_answer(raw,request())
    assert result['text']==TEXT and result['web']['search_call_count']==1
    assert result['web']['ignored_attempt_count']==1 and result['web']['consulted_source_count']==1
    assert result['sources'][0]['url']==URL and result['citations'][0]['source_id']==result['sources'][0]['source_id']
    assert ai_web.native_answer(body(),request())['web']['ignored_attempt_count']==0


@pytest.mark.parametrize('fault',['only_searching','response_incomplete','response_status_missing',
                                 'new_sources','before_completed','failed','two_completed'])
def test_incomplete_attempt_is_not_a_general_tool_limit_or_completion_bypass(fault):
    raw=body();extra=ignored_attempt();raw['output'].insert(1,extra)
    expected='incomplete_response'
    if fault=='only_searching':raw['output'].pop(0)
    elif fault=='response_incomplete':raw['status']='incomplete'
    elif fault=='response_status_missing':raw.pop('status')
    elif fault=='new_sources':extra['action']['sources']=[{'type':'url','url':URL}]
    elif fault=='before_completed':raw['output'][0],raw['output'][1]=raw['output'][1],raw['output'][0]
    elif fault=='failed':extra['status']='failed'
    elif fault=='two_completed':extra['status']='completed';expected='web_limits_exceeded'
    with pytest.raises(ValueError,match=expected):ai_web.native_answer(raw,request())


def test_ignored_open_url_and_model_body_urls_never_substitute_for_native_annotations():
    raw=body();raw['output'].insert(1,ignored_attempt());raw['output'][0]['action']['sources']=[]
    raw['output'][2]['content'][0].update(text='请看此链接 '+URL,annotations=[])
    with pytest.raises(ValueError,match='web_citations_missing'):ai_web.native_answer(raw,request())
    raw=body();extra=ignored_attempt();extra['action']['url']='https://evil.example/fake';raw['output'].insert(1,extra)
    with pytest.raises(ValueError,match='invalid_web_source'):ai_web.native_answer(raw,request())


@pytest.mark.parametrize('annotated_url',[URL+'?utm_source=api','https://big5.sse.com.cn/disclosure/fixture.pdf'])
def test_safe_native_aliases_are_preserved_with_honest_provenance(annotated_url):
    raw=body();raw['output'][1]['content'][0]['annotations'][0]['url']=annotated_url
    result=ai_web.native_answer(raw,request())
    assert result['sources'][0]['url']==annotated_url and result['sources'][0]['published_at'] is None
    assert result['sources'][0]['provenance']=='native_annotation'
    assert result['sources'][0]['consulted_match'] is False and result['web']['annotation_only_source_count']==1


def test_exact_native_consulted_match_is_marked_without_claiming_fact_verification():
    result=ai_web.native_answer(body(),request())
    assert result['sources'][0]['consulted_match'] is True and result['sources'][0]['annotation_only'] is False
    assert result['web']['annotation_only_source_count']==0 and result['web']['validation']=='native_citations_only'


def test_agent_hosted_search_requires_a_completed_tool_even_for_safe_native_annotations():
    raw=body();raw['output'].pop(0)
    with pytest.raises(ValueError,match='incomplete_response'):ai_web.native_answer(raw,{**request(),'agent':True})
    raw=body();raw['output'][0]['status']='searching'
    with pytest.raises(ValueError,match='incomplete_response'):ai_web.native_answer(raw,{**request(),'agent':True})
