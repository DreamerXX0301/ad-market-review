"""Exercise official endpoint shaping and actual async cancellation with mock IO."""
import asyncio
import json
import threading
import time
import httpx
import pytest
from app.adapters import ai_responses as adapter
from app.services.ai_validation import PROBE_SCHEMA


def request(provider='openai',operation='verify'):
    return {'provider_id':provider,'model':'gpt-6.1-sol' if provider=='openai' else 'deepseek-flash',
            'operation':operation,'messages':[{'role':'user','content':'fixture'}],
            'schema':PROBE_SCHEMA,'timeout_seconds':30}


def mock_client(monkeypatch,handler):
    actual=httpx.AsyncClient
    def factory(**kwargs):return actual(**kwargs,transport=httpx.MockTransport(handler))
    monkeypatch.setattr(adapter.httpx,'AsyncClient',factory)


@pytest.mark.parametrize('provider,paths',[
    ('openai',['/v1/models','/v1/responses']),('deepseek',['/models','/responses'])])
def test_fixed_hosts_exact_schema_and_no_provider_body_leak(monkeypatch,provider,paths):
    calls=[]
    async def handler(req):
        calls.append(req)
        assert req.url.host==('api.openai.com' if provider=='openai' else 'api.deepseek.com')
        assert req.headers['authorization']=='Bearer fixture-private'
        if req.method=='GET':return httpx.Response(200,json={'data':[{'id':request(provider)['model']}]})
        body=json.loads(req.content)
        assert body['store'] is False and body['text']['format']['strict']
        assert body['reasoning']['effort']==('low' if provider=='openai' else 'none')
        return httpx.Response(200,json={'status':'completed','model':body['model'],
            'output':[{'type':'message','content':[{'type':'output_text','text':'{"probe":"ok","missing":null}'}]}],
            'usage':{'input_tokens':20,'output_tokens':10,'output_tokens_details':{'reasoning_tokens':4}}})
    mock_client(monkeypatch,handler)
    result=adapter.perform(request(provider),'fixture-private',threading.Event())
    assert [call.url.path for call in calls]==paths
    assert result['output']=={'probe':'ok','missing':None} and result['usage']['reasoning_tokens']==4
    assert 'fixture-private' not in json.dumps(result)


def test_cancel_closes_live_coroutine_and_is_bounded(monkeypatch):
    closed=threading.Event()
    async def handler(req):
        if req.method=='GET':return httpx.Response(200,json={'data':[]})
        try:await asyncio.sleep(10)
        finally:closed.set()
        return httpx.Response(200,json={})
    mock_client(monkeypatch,handler)
    cancel=threading.Event();timer=threading.Timer(.05,cancel.set);timer.start()
    started=time.monotonic()
    with pytest.raises(adapter.ProviderError,match='cancelled'):
        adapter.perform(request(),'fixture-private',cancel)
    timer.join()
    assert closed.is_set() and time.monotonic()-started<1


def test_total_deadline_includes_wait_before_first_byte(monkeypatch):
    closed=threading.Event()
    async def handler(req):
        try:await asyncio.sleep(10)
        finally:closed.set()
    mock_client(monkeypatch,handler)
    payload=request();payload['timeout_seconds']=.05
    started=time.monotonic()
    with pytest.raises(adapter.ProviderError,match='timeout'):
        adapter.perform(payload,'fixture-private',threading.Event())
    assert closed.is_set() and time.monotonic()-started<1


@pytest.mark.parametrize('body,status,expected',[
    ({'error':{'code':'insufficient_quota','message':'fixture-private'}},429,'quota_exhausted'),
    ({'status':'incomplete','usage':{'input_tokens':2,'output_tokens':1}},200,'incomplete_response'),
    ({'status':'completed','output':[{'content':[{'type':'refusal','refusal':'private'}]}]},200,'refusal'),
    ({'status':'completed','output':[{'content':[{'type':'output_text','text':'bad json'}]}]},200,'invalid_output'),
    ({'status':'completed','output':[],'padding':'x'*200000},200,'response_too_large'),
])
def test_failures_remain_classified_and_sanitized(monkeypatch,body,status,expected):
    async def handler(req):
        if req.method=='GET':return httpx.Response(200,json={'data':[]})
        return httpx.Response(status,json=body)
    mock_client(monkeypatch,handler)
    with pytest.raises(adapter.ProviderError) as error:
        adapter.perform(request(),'fixture-private',threading.Event())
    assert str(error.value)==expected
    assert 'private' not in str(error.value)


@pytest.mark.parametrize('opt_in,expected',[(None,False),('0',False),('true',False),('1',True)])
def test_system_proxy_requires_explicit_deployment_opt_in(monkeypatch,opt_in,expected):
    if opt_in is None:monkeypatch.delenv('SHIXU_AI_SYSTEM_PROXY_ENABLED',raising=False)
    else:monkeypatch.setenv('SHIXU_AI_SYSTEM_PROXY_ENABLED',opt_in)
    settings=[]
    actual=httpx.AsyncClient
    async def handler(req):
        assert req.url.host=='api.openai.com'
        if req.method=='GET':return httpx.Response(200,json={'data':[]})
        return httpx.Response(200,json={'status':'completed','output':[{'content':[{'type':'output_text','text':'{"probe":"ok","missing":null}'}]}]})
    def factory(**kwargs):
        settings.append(kwargs)
        return actual(**kwargs,transport=httpx.MockTransport(handler))
    monkeypatch.setattr(adapter.httpx,'AsyncClient',factory)
    adapter.perform(request(),'fixture-private',threading.Event())
    assert settings[0]['trust_env'] is expected
    assert settings[0]['follow_redirects'] is False
    assert settings[0]['base_url']=='https://api.openai.com'
    assert settings[0].get('verify',True) is True


def test_proxy_opt_in_flag_is_not_interpreted_as_proxy_address(monkeypatch):
    from urllib.request import getproxies_environment
    monkeypatch.delenv('SHIXU_AI_SYSTEM_PROXY_ENABLED',raising=False)
    before=getproxies_environment()
    monkeypatch.setenv('SHIXU_AI_SYSTEM_PROXY_ENABLED','1')
    assert getproxies_environment()==before
    assert 'shixu_ai_system' not in getproxies_environment()
