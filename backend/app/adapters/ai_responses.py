"""Fixed official Responses transports. Runs in one IO thread; never opens databases."""
from __future__ import annotations
import asyncio
import json
import os
import threading
import httpx
from app.services.ai_catalog import MAX_RESPONSE_BYTES, max_output
from app.services import ai_web

BASE_URLS = {'deepseek': 'https://api.deepseek.com', 'openai': 'https://api.openai.com'}

class ProviderError(Exception):
    def __init__(self, reason_code, *, usage=None, web_accounting=None):
        self.reason_code = reason_code
        self.usage = usage or {}
        self.web_accounting = web_accounting
        super().__init__(reason_code)


def error_code(status, body):
    code = (body.get('error') or {}).get('code') if isinstance(body, dict) and isinstance(body.get('error'), dict) else None
    if status == 401: return 'authentication_failed'
    if status == 402 or code == 'insufficient_quota': return 'quota_exhausted'
    if status == 429: return 'rate_limited'
    if status == 404: return 'model_unavailable'
    if status in (400, 422): return 'unsupported_parameters'
    if status >= 500: return 'provider_unavailable'
    return 'provider_error'


def usage_of(body):
    raw = body.get('usage') or {}
    def token(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10000000 else None
    inp = raw.get('input_tokens_details') or {}
    out = raw.get('output_tokens_details') or {}
    return {'input_tokens': token(raw.get('input_tokens')), 'cached_tokens': token(inp.get('cached_tokens')),
            'cache_write_tokens': token(inp.get('cache_write_tokens', inp.get('cache_creation_tokens'))),
            'output_tokens': token(raw.get('output_tokens')), 'reasoning_tokens': token(out.get('reasoning_tokens'))}


def payload_for(request):
    provider = request['provider_id']
    payload = {'model': request['model'], 'input': request['messages'],
               'reasoning': {'effort': 'none' if provider == 'deepseek' else 'low'},
               'max_output_tokens': max_output(provider, request['operation'] == 'verify'),
               'text': {'format': {'type': 'json_schema', 'name': 'ai_probe' if request['operation'] == 'verify' else 'ai_interpretation',
                                   'strict': True, 'schema': request.get('schema', {})}}, 'store': False}
    if request.get('allow_web'):
        if not ai_web.supported(provider, request['model']): raise ProviderError('web_search_unavailable')
        rules=request['web_policy']
        payload.pop('text')
        payload.update(tools=[{'type':'web_search','filters':{'allowed_domains':rules['allowed_domains']},
                               'search_context_size':rules['search_context_size'],'return_token_budget':rules['return_token_budget']}],
                       tool_choice='auto',max_tool_calls=rules['max_tool_calls'],include=['web_search_call.action.sources'])
    return payload


async def _read(client, method, path, **kwargs):
    async with client.stream(method, path, **kwargs) as response:
        chunks = bytearray()
        async for chunk in response.aiter_bytes():
            chunks.extend(chunk)
            if len(chunks) > MAX_RESPONSE_BYTES:
                raise ProviderError('response_too_large')
        try: body = json.loads(chunks)
        except (ValueError, UnicodeError): raise ProviderError('invalid_response') from None
        if not 200 <= response.status_code < 300:
            raise ProviderError(error_code(response.status_code, body))
        if not isinstance(body, dict): raise ProviderError('invalid_response')
        return body


async def _call(request, api_key, cancel):
    # Deployment-only opt-in for an operating-system network proxy. This never
    # changes the fixed official API host, TLS verification or redirect policy.
    async with httpx.AsyncClient(base_url=BASE_URLS[request['provider_id']],
                                headers={'Authorization': 'Bearer ' + api_key},
                                timeout=httpx.Timeout(request['timeout_seconds']),
                                trust_env=os.environ.get('SHIXU_AI_SYSTEM_PROXY_ENABLED') == '1',
                                follow_redirects=False) as client:
        async def operation():
            models = None
            if request['operation'] == 'verify':
                directory = await _read(client, 'GET', '/models' if request['provider_id'] == 'deepseek' else '/v1/models')
                models = [item['id'] for item in directory.get('data', [])[:300] if isinstance(item, dict)
                          and isinstance(item.get('id'), str) and len(item['id']) <= 100]
            body = await _read(client, 'POST', '/responses' if request['provider_id'] == 'deepseek' else '/v1/responses', json=payload_for(request))
            usage = usage_of(body)
            if body.get('status') != 'completed': raise ProviderError('incomplete_response', usage=usage)
            if request.get('allow_web'):
                for item in body.get('output', []):
                    if isinstance(item, dict):
                        for content in item.get('content') or []:
                            if content.get('type')=='refusal': raise ProviderError('refusal', usage=usage)
                try: result=ai_web.native_answer(body,request)
                except ValueError as error:
                    raise ProviderError(str(error),usage=usage) from None
                # The public usage schema does not separately establish whether
                # search-content charges are already in total input. Preserve
                # uncertainty rather than add them twice or assume them free.
                accounting={'search_call_count':result['web']['search_call_count'],'used':result['web']['used'],
                            'complete':True,'search_content_in_input':None}
                return {'output':result,'usage':usage,'response_model':str(body.get('model') or '')[:100],
                        'web_accounting':accounting,'models':None}
            texts = []
            for output in body.get('output', []):
                if not isinstance(output, dict): continue
                for content in output.get('content', []):
                    if content.get('type') == 'refusal': raise ProviderError('refusal', usage=usage)
                    if content.get('type') == 'output_text' and isinstance(content.get('text'), str): texts.append(content['text'])
            try: result = json.loads(''.join(texts))
            except ValueError: raise ProviderError('invalid_output', usage=usage) from None
            return {'output': result, 'usage': usage, 'response_model': str(body.get('model') or '')[:100], 'models': models}
        task = asyncio.create_task(operation())
        try:
            async with asyncio.timeout(request['timeout_seconds']):
                while not task.done():
                    if cancel.is_set(): raise ProviderError('cancelled')
                    await asyncio.wait({task}, timeout=0.2)
                return task.result()
        finally:
            if not task.done():
                task.cancel()
                try: await task
                except (asyncio.CancelledError, Exception): pass


def perform(request: dict, api_key: str, cancel: threading.Event):
    try:
        return asyncio.run(_call(request, api_key, cancel))
    except ProviderError: raise
    except (TimeoutError, httpx.TimeoutException): raise ProviderError('timeout') from None
    except (httpx.HTTPError, OSError): raise ProviderError('network_error') from None
    except Exception: raise ProviderError('invalid_response') from None
