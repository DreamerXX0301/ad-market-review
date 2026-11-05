"""Local fixtures only: bounded uploads, immutable history and mocked vision SSE."""
import base64
from copy import deepcopy
from io import BytesIO
import json
import socket
import zipfile

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
import pytest

from app import core
from app.services import ai_attachments as files, ai_service as service
from app.services.ai_runtime import AIRuntime
from test_ai_agent_round import modern_agent_client as client, complete, function, final, mock_network
from test_ai_service import enable, fake, drive
from test_ai_semantic_round import planned


def file(name='evidence.txt', data=b'Company revenue 123, period 2025', media_type='text/plain'):
    return {'name': name, 'media_type': media_type, 'data_base64': base64.b64encode(data).decode()}


def image(kind='PNG', size=(24, 18)):
    stream=BytesIO();Image.new('RGB', size, (12, 34, 56)).save(stream,format=kind)
    return file('chart.'+{'PNG':'png','JPEG':'jpg','WEBP':'webp'}[kind],stream.getvalue(),{'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}[kind])


def pdf(*, text='Revenue 123', pages=1, encrypted=False):
    writer=PdfWriter()
    for _ in range(pages):
        page=writer.add_blank_page(width=200,height=100)
        if text:
            font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
            page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
            content=DecodedStreamObject();content.set_data(('BT /F1 12 Tf 10 40 Td ('+text+') Tj ET').encode())
            page[NameObject('/Contents')]=writer._add_object(content)
    if encrypted:writer.encrypt('test-only')
    stream=BytesIO();writer.write(stream)
    return file('report.pdf',stream.getvalue(),'application/pdf')


def docx(text='Revenue 123', extra=None):
    stream=BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml','<Types/>')
        archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'+text+'</w:t></w:r></w:p></w:body></w:document>')
        for name,value in (extra or {}).items():archive.writestr(name,value)
    return file('report.docx',stream.getvalue(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document')


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    actual=socket.socket.connect
    def deny(sock,address):
        if sock.family in (socket.AF_INET,socket.AF_INET6):raise AssertionError('attachment tests cannot reach model or IP network')
        return actual(sock,address)
    monkeypatch.setattr(socket.socket,'connect',deny)
    for key in ('SHIXU_AI_READ_SQLITE_PATH','SHIXU_AI_SYSTEM_PROXY_ENABLED'):monkeypatch.delenv(key,raising=False)


@pytest.mark.parametrize('kind',['PNG','JPEG','WEBP'])
def test_real_image_decoder_and_mime(kind):
    uploaded=image(kind);prepared=files.prepare([uploaded])[0]
    assert prepared['width']==24 and prepared['height']==18 and prepared['kind']=='image'
    assert prepared['data_base64']==uploaded['data_base64']
    bad={**uploaded,'media_type':'image/png' if kind!='PNG' else 'image/jpeg'}
    with pytest.raises(HTTPException):files.prepare([bad])


def test_image_magic_and_normalization_preserve_original():
    with pytest.raises(HTTPException):files.prepare([file('chart.png',b'not an image','image/png')])
    original=files.prepare([image(size=(3000,2000))])[0]
    model=files._model_image(original)
    with Image.open(BytesIO(base64.b64decode(model['image_url'].split(',',1)[1]))) as decoded:
        assert max(decoded.size)<=2048 and decoded.width*decoded.height<=4_000_000
    assert original['width']==3000 and original['height']==2000 and model['detail']=='high'


def test_pdf_text_only_and_scanned_encrypted_limits():
    item=files.prepare([pdf()])[0]
    assert item['kind']=='text' and item['text_only'] and item['page_count']==1 and 'Revenue 123' in item['text']
    for value,code in [(pdf(text=''),'attachment_pdf_scanned'),(pdf(encrypted=True),'attachment_pdf_encrypted'),(pdf(pages=21),'attachment_pdf_limit')]:
        with pytest.raises(HTTPException) as error:files.prepare([value])
        assert error.value.detail['reason_code']==code


def test_docx_extracts_plain_text_and_rejects_unsafe_archives():
    assert files.prepare([docx()])[0]['text']=='Revenue 123'
    for extra in [{'../escape':b'x'},{'word/vbaProject.bin':b'x'},{'word/bomb':b'x'*100000}]:
        with pytest.raises(HTTPException):files.prepare([docx(extra=extra)])
    with pytest.raises(HTTPException):files.prepare([docx(text='<!DOCTYPE foo [<!ENTITY a "unsafe">]>')])


@pytest.mark.parametrize('value',[
    file('../secret.txt'),file('wrong.exe'),file('bad\ud800.txt'),file(data=b'\xff\xfe'),file(data=b'a\x00b'),
    {'name':'x.txt','media_type':'text/plain','data_base64':'%%%bad'},
    file('bad.json',b'{invalid','application/json'),file('nan.json',b'NaN','application/json'),
    file(data=b'x'*(files.MAX_TEXT_BYTES+1)),
])
def test_invalid_input_never_reaches_storage(value):
    with pytest.raises(HTTPException):files.prepare([value])


def create(client,uploads,key='attachments-first',provider='openai',question='分析这份公司资料'):
    return client.post('/api/v1/ai/sessions',json={'kind':'stock','provider_id':provider,'text':question,'attachments':uploads,'consent':True,'request_key':key})


def test_metadata_only_history_and_controlled_content_idempotence(client):
    runtime=AIRuntime(fake)
    try:
        enable(client,runtime,'openai');uploaded=file()
        response=create(client,[uploaded]);assert response.status_code==202,response.text
        result=response.json();again=create(client,[uploaded]);assert again.json()==result
        history=client.get('/api/v1/ai/sessions/'+result['session_id']).json()
        user=history['messages'][0];metadata=user['attachments'][0]
        assert user['interpretation'] is None and 'data_base64' not in json.dumps(history)
        assert uploaded['data_base64'] not in json.dumps(history)
        content=client.get(metadata['content_url']);assert content.content==base64.b64decode(uploaded['data_base64'])
        assert content.headers['content-disposition'].startswith('attachment')
        assert content.headers['cache-control']=='private, no-store'
        changed=create(client,[file(data=b'changed')]);assert changed.status_code==409
        assert client.get(metadata['content_url'].replace(result['session_id'],'wrong-session')).status_code==404
        client.delete('/api/v1/ai/sessions/'+result['session_id'])
        assert client.get(metadata['content_url']).status_code==404
    finally:runtime.shutdown()


def test_deepseek_rejects_images_but_accepts_pdf_text_before_paid_enqueue(client):
    runtime=AIRuntime(fake)
    try:
        enable(client,runtime,'deepseek')
        before=service.providers()['usage']['requests']
        response=create(client,[image()],provider='deepseek');assert response.status_code==422
        assert response.json()['detail']['reason_code']=='attachment_unsupported_model'
        assert service.providers()['usage']['requests']==before
        response=create(client,[pdf()],provider='deepseek');assert response.status_code==202,response.text
        with core.connect() as db:
            request=json.loads(db.execute('SELECT request_json FROM ai_task WHERE task_id=?',(response.json()['task_id'],)).fetchone()[0])
        assert 'Revenue 123' in str(request['seed_messages']) and request['attachment_input_tokens']==0
        assert 'data_base64' not in json.dumps(request)
    finally:runtime.shutdown()


def test_vision_reaches_planner_and_final_with_separate_reservation_and_history_reuse(client,monkeypatch):
    runtime=AIRuntime(fake);calls=[];question='这张股票K线图里有什么特点？';answer='这是用户上传的图，先核对图中日期再判断走势。'
    async def handler(request):
        payload=json.loads(request.content);calls.append(payload)
        images=[part for item in payload['input'] if isinstance(item.get('content'),list) for part in item['content'] if part.get('type')=='input_image']
        assert len(images)==1 and images[0]['detail']=='high'
        assert images[0]['image_url'].startswith('data:image/jpeg;base64,')
        if len(calls)%2:
            current=question if len(calls)==1 else '再说一下图里应该核对什么。'
            return httpx.Response(200,content=complete([function('plan_stock_task',planned(current,purpose='concept'),'image-plan-'+str(len(calls)))]))
        return httpx.Response(200,content=complete([final(answer)],answer))
    mock_network(monkeypatch,handler)
    try:
        enable(client,runtime,'openai');uploaded=image()
        response=create(client,[uploaded],question=question);assert response.status_code==202,response.text
        result=response.json()
        with core.connect() as db:
            task=dict(db.execute('SELECT * FROM ai_task WHERE task_id=?',(result['task_id'],)).fetchone())
            frozen=json.loads(task['request_json'])
            assert frozen['attachment_input_tokens']==32768 and 'data_base64' not in task['request_json']
            base=files.estimated_reservation('openai','gpt-6.1-sol',service._input_size({'provider_id':'openai','model':'gpt-6.1-sol'},frozen,'message'),6144,0)
            assert task['reservation_nusd']>base
        assert drive(runtime,result['task_id'])['state']=='completed'
        history=client.get('/api/v1/ai/sessions/'+result['session_id']).json()
        assert history['messages'][-1]['text']==answer
        with core.connect() as db:
            reply=db.execute("SELECT interpretation_json FROM ai_message WHERE role='assistant' AND task_id=?",(result['task_id'],)).fetchone()[0]
            assert 'data:image/jpeg;base64,' not in reply
            assert db.execute('SELECT count(*) FROM ai_request_receipt WHERE task_id=?',(result['task_id'],)).fetchone()[0]==2
        response=client.post('/api/v1/ai/sessions/'+result['session_id']+'/messages',json={'text':'再说一下图里应该核对什么。','consent':True,'request_key':'image-follow'})
        assert response.status_code==202,response.text
        assert drive(runtime,response.json()['task_id'])['state']=='completed'
        assert len(calls)==4
    finally:runtime.shutdown()


def test_header_and_chunked_body_limits_run_before_route():
    app=FastAPI();called=[]
    @app.post('/api/v1/ai/sessions')
    async def endpoint(body: dict):called.append(True);return {'ok':True}
    app.add_middleware(files.AIRequestSizeMiddleware)
    client=TestClient(app)
    response=client.post('/api/v1/ai/sessions',content=b'{}',headers={'content-length':str(files.MAX_REQUEST_BYTES+1)})
    assert response.status_code==413 and not called


def test_chunked_upload_is_bounded_without_content_length():
    app=FastAPI();called=[]
    @app.post('/api/v1/ai/sessions')
    async def endpoint(body:dict):called.append(True);return {'ok':True}
    app.add_middleware(files.AIRequestSizeMiddleware)
    response=TestClient(app).post('/api/v1/ai/sessions',content=(b'x'*1024*1024 for _ in range(15)))
    assert response.status_code==413 and not called
    assert response.json()['detail']['reason_code']=='attachment_too_large'


@pytest.mark.parametrize('name,mime', [('evidence.markdown','text/markdown'),('evidence.tsv','text/tab-separated-values')])
def test_plain_text_aliases_match_the_picker(name,mime):
    assert files.prepare([file(name,b'Value\t123',mime)])[0]['kind']=='text'


def test_session_attachment_limit_and_ttl_reclaim_keep_accounting(client,monkeypatch):
    runtime=AIRuntime(fake)
    try:
        enable(client,runtime,'openai');monkeypatch.setattr(files,'MAX_SESSION_BYTES',45)
        response=create(client,[file(data=b'x'*30)]);assert response.status_code==202
        ids=response.json();client.post('/api/v1/ai/tasks/'+ids['task_id']+'/cancel')
        response=client.post('/api/v1/ai/sessions/'+ids['session_id']+'/messages',json={'text':'分析另一个文件','attachments':[file(data=b'y'*20)],'consent':True,'request_key':'over-session'})
        assert response.status_code==422 and response.json()['detail']['reason_code']=='attachment_session_limit'
        with core.connect() as db:
            db.execute('UPDATE ai_session SET expires_at_ms=0 WHERE session_id=?',(ids['session_id'],))
            assert service._purge_expired(db)==1
            assert db.execute('SELECT count(*) FROM ai_message WHERE session_id=?',(ids['session_id'],)).fetchone()[0]==0
            assert db.execute('SELECT state FROM ai_task WHERE task_id=?',(ids['task_id'],)).fetchone()[0]=='cancelled'
    finally:runtime.shutdown()


def test_image_reservation_does_not_depend_on_base64_transport_length():
    a=files.estimated_reservation('openai','gpt-6.1-sol',1000,100,32768)
    b=files.estimated_reservation('openai','gpt-6.1-sol',1000,100,0)
    assert a-b==81920000  # 32K engineering tokens at the higher cache-write rate.
    media={'model':'gpt-6.1-sol','input':[{'role':'user','content':[{'type':'input_image','image_url':'data:image/jpeg;base64,'+'A'*200000,'detail':'high'}]}]}
    small=deepcopy(media);small['input'][0]['content'][0]['image_url']='data:image/jpeg;base64,AA=='
    assert files.text_payload_bytes(media)==files.text_payload_bytes(small)


def compressed_pdf(content):
    writer=PdfWriter();page=writer.add_blank_page(width=200,height=100)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject();stream.set_data(content)
    page[NameObject('/Contents')]=writer._add_object(stream.flate_encode())
    output=BytesIO();writer.write(output)
    return file('compressed.pdf',output.getvalue(),'application/pdf')


@pytest.mark.parametrize('content', [
    b' '*(3*1024*1024)+b'BT /F1 12 Tf (SyntheticPdfText) Tj ET',
    b'1 0 0 1 0 0 cm\n'*20000+b'BT /F1 12 Tf (SyntheticPdfText) Tj ET',
])
def test_pdf_decompression_and_parser_work_are_limited_before_text_length(content,caplog):
    from pypdf import get_configuration
    before=get_configuration()
    uploaded=compressed_pdf(content)
    assert len(base64.b64decode(uploaded['data_base64']))<20000
    with pytest.raises(HTTPException) as error:files.prepare([uploaded])
    assert error.value.detail['reason_code']=='attachment_invalid'
    assert get_configuration()==before
    assert 'SyntheticPdfText' not in caplog.text
