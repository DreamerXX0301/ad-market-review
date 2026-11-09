"""Local AI management and bounded evidence conversations; GET never uses network."""
from typing import Annotated, Literal
from fastapi import APIRouter, Query
from fastapi.responses import Response
from urllib.parse import quote
from pydantic import BaseModel, ConfigDict, Field
from app.services import ai_service as service

router = APIRouter(prefix='/api/v1/ai', tags=['ai'])

class Request(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class Limits(Request):
    daily_budget_usd: str = '0.50'
    daily_request_limit: Annotated[int, Field(ge=1, le=200)] = 20
    timeout_seconds: Literal[30,45,60] = 45

class ProviderUpdate(Request):
    expected_revision: Annotated[int, Field(ge=0)]
    model: Annotated[str, Field(min_length=1,max_length=100)]
    enabled: bool
    api_key: Annotated[str | None, Field(max_length=1000)] = None
    limits: Limits | None = None

class Verification(Request):
    expected_revision: Annotated[int, Field(ge=0)]
    request_key: Annotated[str, Field(min_length=1,max_length=200)]

class Attachment(Request):
    name: Annotated[str, Field(min_length=1,max_length=160)]
    media_type: Annotated[str, Field(min_length=1,max_length=120)]
    data_base64: Annotated[str, Field(min_length=1,max_length=6990508)]

class NewsDayContext(Request):
    kind: Literal['day']

class NewsItemContext(Request):
    kind: Literal['news']
    source: Annotated[str, Field(min_length=1,max_length=120)]
    id: Annotated[str, Field(min_length=1,max_length=200)]

class NewsEventContext(Request):
    kind: Literal['event']
    source: Annotated[str, Field(min_length=1,max_length=120)]
    id: Annotated[str, Field(min_length=1,max_length=200)]
    days: Literal[7,30]

class SessionCreate(Request):
    attachments: Annotated[list[Attachment], Field(max_length=3)] = []
    allow_web: bool = False
    expected_config_revision: Annotated[int | None, Field(ge=0)] = None
    expected_model: Annotated[str | None, Field(min_length=1,max_length=100)] = None
    text: Annotated[str | None, Field(max_length=2000)] = None
    kind: Literal['stock','market']
    report_id: Annotated[str | None, Field(max_length=200)] = None
    as_of: Annotated[str | None, Field(max_length=10)] = None
    market_scope: Literal['full_review'] | None = None
    news_context: Annotated[NewsDayContext | NewsItemContext | NewsEventContext, Field(discriminator='kind')] | None = None
    provider_id: Literal['deepseek','openai']
    instrument_id: Annotated[str | None, Field(max_length=30)] = None
    context_digest: Annotated[str | None, Field(max_length=100)] = None
    consent: bool
    request_key: Annotated[str, Field(min_length=1,max_length=200)]

class MessageCreate(Request):
    attachments: Annotated[list[Attachment], Field(max_length=3)] = []
    allow_web: bool | None = None
    text: Annotated[str, Field(max_length=2000)] = ''
    consent: bool
    request_key: Annotated[str, Field(min_length=1,max_length=200)]

class SessionUpdate(Request):
    keep: bool

@router.get('/providers')
def providers(): return service.providers()

@router.put('/providers/{provider_id}')
def save_provider(provider_id: Literal['deepseek','openai'], body: ProviderUpdate):
    return service.save_provider(provider_id, body.model_dump(exclude_none=True, exclude_unset=True))

@router.delete('/providers/{provider_id}/credential')
def delete_credential(provider_id: Literal['deepseek','openai'], expected_revision: int = Query(ge=0)):
    return service.delete_credential(provider_id, expected_revision)

@router.post('/providers/{provider_id}/verify', status_code=202)
def verify(provider_id: Literal['deepseek','openai'], body: Verification):
    return service.verify_provider(provider_id, body.model_dump())

@router.get('/context-preview')
def context_preview(kind: Literal['stock','market'], report_id: str | None=None, as_of: str | None=None):
    return service.context_preview(kind, report_id, as_of)

@router.post('/sessions', status_code=202)
def create_session(body: SessionCreate): return service.create_session(body.model_dump(exclude_none=True))

@router.get('/sessions')
def sessions(kind: Literal['stock','market'] | None=None, instrument_id: str | None=None, as_of: str | None=None,
             limit: int=Query(20, ge=1, le=50), cursor: str | None=None):
    return service.list_sessions(kind, instrument_id, as_of, limit, cursor)

@router.get('/sessions/{session_id}')
def session(session_id: str): return service.get_session(session_id)

@router.post('/sessions/{session_id}/messages', status_code=202)
def message(session_id: str, body: MessageCreate): return service.send_message(session_id, body.model_dump(exclude_none=True))

@router.patch('/sessions/{session_id}')
def update_session(session_id: str, body: SessionUpdate): return service.change_session(session_id, body.keep)

@router.delete('/sessions/{session_id}')
def delete_session(session_id: str): return service.delete_session(session_id)

@router.get('/tasks/{task_id}')
def task(task_id: str): return service.get_task(task_id)

@router.post('/tasks/{task_id}/cancel')
def cancel(task_id: str): return service.cancel_task(task_id)


@router.get('/sessions/{session_id}/messages/{message_id}/attachments/{attachment_id}')
def attachment_content(session_id: str, message_id: str, attachment_id: str):
    raw, metadata = service.attachment_content(session_id, message_id, attachment_id)
    inline = metadata['kind'] == 'image'
    return Response(raw, media_type=metadata['media_type'] if inline else 'application/octet-stream', headers={
        'Content-Disposition': ('inline' if inline else 'attachment') + "; filename*=UTF-8''" + quote(metadata['name'], safe=''),
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; sandbox"})
