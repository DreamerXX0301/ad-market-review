"""Mock actual streamed function calls: denied scope never reaches a resource."""
from copy import deepcopy
import json
import socket
import httpx
import pytest
from app import core
from app.services import ai_service as service,ai_read_tools
from app.services.ai_runtime import AIRuntime
from test_ai_agent_round import agent_client,create,complete,function,final,mock_network
from test_ai_service import enable,fake,drive


@pytest.fixture(autouse=True)
def block_scope_test_ip_network(monkeypatch):
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    def guarded(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError('Scope regression must not reach an IP network')
        return connect(sock, address)
    def guarded_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError('Scope regression must not reach an IP network')
        return connect_ex(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', guarded)
    monkeypatch.setattr(socket.socket, 'connect_ex', guarded_ex)
    monkeypatch.delenv('SHIXU_AI_SYSTEM_PROXY_ENABLED', raising=False)
    monkeypatch.delenv('SHIXU_AI_READ_SQLITE_PATH', raising=False)

@pytest.mark.parametrize('question',['最近房价跌了吗','北京天气怎么样','旅游攻略','购物买手机推荐','帮我写代码',
                                    '聊聊人生闲聊','先看茅台股票，然后聊北京天气对生活的影响','股票先放一边，聊北京房价',
                                    '贵州茅台好喝吗','贵州茅台哪里可以买','600519.SH请写首生日歌','今天有什么电影推荐','现在帮我改简历'])
def test_forged_plan_without_exposed_tools_has_no_web_fee_or_local_reads(agent_client,monkeypatch,question):
    client=agent_client;runtime=AIRuntime(fake);calls=[]
    monkeypatch.setattr('app.services.ai_scope_focus.resolve_scope_focus',lambda text:{'instrument_id':'600519.SH','name':'贵州茅台'})
    monkeypatch.setattr(ai_read_tools,'execute_read_tool',lambda *a,**kw:pytest.fail('outside scope must not read data'))
    async def handler(request):
        payload=json.loads(request.content);calls.append(payload)
        assert payload['tools']==[] and payload['max_output_tokens']<=256
        if len(calls)==1:
            output=[function('request_web_search',{'query':'北京房价','reason':'财经股票研究','purpose':'stock_research','user_quote':question})]
        else:
            result=next(item for item in payload['input'] if item.get('type')=='function_call_output')
            assert json.loads(result['output'])['state']=='scope_denied'
            # The output guard must also withhold a provider's off-topic facts.
            output=[final('北京房价最近跌了，你想查哪个城市？')]
        return httpx.Response(200,content=complete(output,'北京房价最近跌了，你想查哪个城市？' if len(calls)>1 else None))
    mock_network(monkeypatch,handler)
    try:
        enable(client,runtime,'openai');created=create(client,text=question,kind='stock',instrument_id='600519.SH').json()
        with core.connect() as db:
            row=db.execute('SELECT request_json FROM ai_task WHERE task_id=?',(created['task_id'],)).fetchone()
            stored=json.loads(row[0]);stored['scope_input']['recent_user_texts']=['研究贵州茅台股票']
            db.execute('UPDATE ai_task SET request_json=? WHERE task_id=?',(core.dumps(stored),created['task_id']))
        assert drive(runtime,created['task_id'])['state']=='completed' and len(calls)==2
        message=client.get('/api/v1/ai/sessions/'+created['session_id']).json()['messages'][-1]
        assert '只能' in message['text'] and '北京' not in message['text'] and not message['source_context']['web']['used']
        with core.connect() as db:
            receipts=db.execute('SELECT price_json,usage_json,cost_state FROM ai_request_receipt WHERE task_id=?',(created['task_id'],)).fetchall()
            assert len(receipts)==2 and all('web_policy' not in json.loads(row['price_json']) for row in receipts)
            assert all(json.loads(row['usage_json'])['input_tokens']==100 and row['cost_state']=='estimated' for row in receipts)
            deltas=''.join(json.loads(row[0]).get('text','') for row in db.execute("SELECT data_json FROM ai_task_event WHERE task_id=? AND kind='delta'",(created['task_id'],)))
            assert '北京房价' not in deltas
    finally:runtime.shutdown()

def test_beijing_followup_cannot_borrow_old_assistant_drift_or_stock_anchor(agent_client,monkeypatch):
    client=agent_client;runtime=AIRuntime(fake);calls=[]
    async def handler(request):
        payload=json.loads(request.content);calls.append(payload)
        assert payload['tools']==[]
        if len(calls)==1:return httpx.Response(200,content=complete([final('只能讨论股票研究。')],'只能讨论股票研究。'))
        if len(calls)==2:return httpx.Response(200,content=complete([function('request_web_search',{'query':'北京房价','reason':'此前已聊财经','purpose':'stock_impact','user_quote':'北京的'})]))
        return httpx.Response(200,content=complete([final('我只能讨论股票研究。')],'我只能讨论股票研究。'))
    mock_network(monkeypatch,handler)
    try:
        enable(client,runtime,'openai');created=create(client,text='最近房价跌了吗',kind='stock',instrument_id='600519.SH').json()
        assert drive(runtime,created['task_id'])['state']=='completed'
        with core.connect() as db:
            db.execute("UPDATE ai_message SET text='最近房价下跌，请问哪个城市？' WHERE session_id=? AND role='assistant'",(created['session_id'],))
        response=client.post('/api/v1/ai/sessions/'+created['session_id']+'/messages',json={'text':'北京的','consent':True,'request_key':'beijing-after-drift'})
        assert drive(runtime,response.json()['task_id'])['state']=='completed' and len(calls)==3
        with core.connect() as db:
            assert all('web_policy' not in json.loads(row[0]) for row in db.execute('SELECT price_json FROM ai_request_receipt'))
    finally:runtime.shutdown()

def test_explicit_property_data_impact_on_stocks_can_use_hosted_search(agent_client,monkeypatch):
    client=agent_client;runtime=AIRuntime(fake);calls=[];question='地产数据对地产股有什么影响'
    answer='地产股需要结合销售与盈利变化评估，不能把住宅价格直接当成股票价值。';url='https://www.sse.com.cn/fixture.pdf'
    async def handler(request):
        payload=json.loads(request.content);calls.append(payload)
        if len(calls)==1:
            return httpx.Response(200,content=complete([function('request_web_search',{'query':'房地产销售数据','reason':'用于评估地产股影响','purpose':'stock_impact','user_quote':question})]))
        assert payload['tools'][0]['type']=='web_search'
        message=final(answer);message['content'][0]['annotations']=[{'type':'url_citation','start_index':0,'end_index':len(answer),'url':url}]
        output=[{'type':'web_search_call','status':'completed','action':{'type':'search','sources':[{'url':url}]}},message]
        return httpx.Response(200,content=complete(output,answer))
    mock_network(monkeypatch,handler)
    try:
        enable(client,runtime,'openai');created=create(client,text=question).json()
        assert drive(runtime,created['task_id'])['state']=='completed' and len(calls)==2
        with core.connect() as db:
            receipts=[json.loads(row[0]) for row in db.execute('SELECT price_json FROM ai_request_receipt WHERE task_id=?',(created['task_id'],))]
            assert sum('web_policy' in row for row in receipts)==1
    finally:runtime.shutdown()

def test_control_thread_rechecks_hosted_and_local_before_any_resource_or_receipt(agent_client,monkeypatch):
    client=agent_client;runtime=AIRuntime(fake)
    monkeypatch.setattr(ai_read_tools,'execute_read_tool',lambda *a,**kw:pytest.fail('read must be denied'))
    try:
        enable(client,runtime,'openai');created=create(client,text='北京的房价如何').json()
        with core.connect() as db:task=dict(db.execute('SELECT * FROM ai_task WHERE task_id=?',(created['task_id'],)).fetchone())
        assert runtime._agent_authorize(task,{'ordinal':1,'input_bytes':100,'hosted_web':True})['_error']=='stock_scope_required'
        assert runtime._agent_tool(task,{'name':'read_market_data','args':{'date':'today','section':'overview'},'call_id':'forged'})['state']=='scope_denied'
        with core.connect() as db:assert db.execute('SELECT count(*) FROM ai_request_receipt WHERE task_id=?',(created['task_id'],)).fetchone()[0]==0
    finally:runtime.shutdown()


def test_natural_cross_day_followup_reaches_local_tools_from_actual_user_history(agent_client,monkeypatch):
    client=agent_client;runtime=AIRuntime(fake);calls=[];reads=[]
    first_question='今天收盘行情数据有了吗？'
    question='那对比周五的行情，今天的变化是什么？上周五强的方向今天表现怎么样？'
    first_answer='可以核对当前已保存的时点记录，尚未取得的收盘资料应保留未知。'
    answer='已分别核对周五和今天的合成盘面资料；方向变化按各自日期比较，指数涨幅不能替代板块涨停家数。'
    def read(name,args,**kwargs):
        reads.append((name,deepcopy(args)))
        day='2026-11-02' if args['date']=='today' else args['date']
        return {'ok':True,'state':'available','tool_name':name,'date':day,'source':['fixture'],
                'summary':{'fixture':'合成盘面资料，非全市场涨停家数榜'},
                'evidence':[] if name=='check_data_availability' else [{'evidence_id':'fixture:'+day,'data':{'change_pct':1.2}}],
                'metrics':[],'gaps':[],'source_bindings':{'snapshot_id':'fixture-'+day}}
    monkeypatch.setattr(ai_read_tools,'execute_read_tool',read)
    async def handler(request):
        payload=json.loads(request.content);calls.append(payload)
        assert any(tool.get('name')=='read_market_data' for tool in payload['tools']) or len(calls)%2==0
        if len(calls)==1:
            output=[function('check_data_availability',{'date':'today'},'first_availability')];text=None
        elif len(calls)==2:output=[final(first_answer)];text=first_answer
        elif len(calls)==3:
            output=[function('read_market_data',{'date':'2026-10-30','section':'overview'},'friday'),
                    function('read_market_data',{'date':'today','section':'noon'},'today')];text=None
        else:
            results={item['call_id']:json.loads(item['output']) for item in payload['input'] if item.get('type')=='function_call_output'}
            assert results['friday']['date']=='2026-10-30' and results['today']['date']=='2026-11-02'
            output=[final(answer)];text=answer
        return httpx.Response(200,content=complete(output,text))
    mock_network(monkeypatch,handler)
    try:
        enable(client,runtime,'openai');created=create(client,text=first_question).json()
        assert drive(runtime,created['task_id'])['state']=='completed'
        response=client.post('/api/v1/ai/sessions/'+created['session_id']+'/messages',
                             json={'text':question,'consent':True,'request_key':'natural-market-compare'})
        assert response.status_code==202,response.text
        identifier=response.json()['task_id']
        with core.connect() as db:
            request=json.loads(db.execute('SELECT request_json FROM ai_task WHERE task_id=?',(identifier,)).fetchone()[0])
            assert request['scope_input']['recent_user_texts']==[first_question]
            assert request['scope']['version']=='ai-stock-scope-v3' and request['scope']['resources_allowed']
        assert drive(runtime,identifier)['state']=='completed'
        message=client.get('/api/v1/ai/sessions/'+created['session_id']).json()['messages'][-1]
        assert message['text']==answer and message['source_context']['web']['used'] is False
        assert len(calls)==4 and len(reads)==3
        with core.connect() as db:
            receipts=db.execute('SELECT price_json,cost_state FROM ai_request_receipt WHERE task_id=?',(identifier,)).fetchall()
            assert len(receipts)==2 and all('web_policy' not in json.loads(row['price_json']) for row in receipts)
            assert all(row['cost_state']=='estimated' for row in receipts)
    finally:runtime.shutdown()


@pytest.mark.parametrize('other_question',['黄金周五行情怎么样','北京房价今天如何'])
def test_implicit_cross_day_market_followup_cannot_reuse_older_stock_chain_after_topic_change(agent_client,monkeypatch,other_question):
    client=agent_client;runtime=AIRuntime(fake);calls=[]
    question='那对比周五的行情，今天的变化是什么？上周五强的方向今天表现怎么样？'
    monkeypatch.setattr(ai_read_tools,'execute_read_tool',lambda *a,**kw:pytest.fail('changed topic must not read equity data'))
    async def handler(request):
        payload=json.loads(request.content);calls.append(payload)
        if len(calls)==1:
            output=[final('可以讨论A股盘面。')];text='可以讨论A股盘面。'
        elif len(calls)==2:
            assert payload['tools']==[];output=[final('我仅提供A股行情复盘与个股研究。')];text='我仅提供A股行情复盘与个股研究。'
        elif len(calls)==3:
            assert payload['tools']==[]
            output=[function('read_market_data',{'date':'today','section':'overview'},'forged_equity_read')];text=None
        else:
            assert payload['tools']==[]
            result=next(item for item in payload['input'] if item.get('type')=='function_call_output')
            assert json.loads(result['output'])['state']=='scope_denied'
            output=[final('我仅提供A股行情复盘与个股研究。')];text='我仅提供A股行情复盘与个股研究。'
        return httpx.Response(200,content=complete(output,text))
    mock_network(monkeypatch,handler)
    try:
        enable(client,runtime,'openai');created=create(client,text='今天收盘行情数据有了吗？').json()
        assert drive(runtime,created['task_id'])['state']=='completed'
        switched=client.post('/api/v1/ai/sessions/'+created['session_id']+'/messages',
                             json={'text':other_question,'consent':True,'request_key':'other-domain-switch'})
        assert drive(runtime,switched.json()['task_id'])['state']=='completed'
        with core.connect() as db:
            db.execute("UPDATE ai_message SET text='接下来可以对比周五与今天的盘面。' WHERE session_id=? AND role='assistant'",(created['session_id'],))
        response=client.post('/api/v1/ai/sessions/'+created['session_id']+'/messages',
                             json={'text':question,'consent':True,'request_key':'implicit-after-other-domain'})
        assert drive(runtime,response.json()['task_id'])['state']=='completed'
        assert len(calls)==4
        with core.connect() as db:
            assert all('web_policy' not in json.loads(row[0]) for row in db.execute('SELECT price_json FROM ai_request_receipt'))
    finally:runtime.shutdown()


@pytest.mark.parametrize('question', ['今天的资金主要去了哪些方向？', '今天下午资金轮转是什么情况？',
                                      '上周五资金方向今天怎么样？', '那今天呢？', '那上周五呢？'])
def test_natural_flow_or_date_followup_reaches_read_from_actual_market_chat(agent_client,monkeypatch,question):
    client=agent_client;runtime=AIRuntime(fake);calls=[];reads=[]
    answer='已读取对应日期的合成盘面资料；缺少真实净流入时不能把成交额当作资金流向。'
    def read(name,args,**kwargs):
        reads.append((name,deepcopy(args)))
        return {'ok':True,'state':'partial','tool_name':name,'date':args['date'],
                'source':['fixture'],'summary':{'fixture':'合成盘面，非真实净流入'},
                'evidence':[],'metrics':[],'gaps':[]}
    monkeypatch.setattr(ai_read_tools,'execute_read_tool',read)
    async def handler(request):
        payload=json.loads(request.content);calls.append(payload)
        if len(calls)==1:
            return httpx.Response(200,content=complete([final('可以核对A股盘面。')],'可以核对A股盘面。'))
        if len(calls)==2:
            assert any(tool.get('name')=='read_market_data' for tool in payload['tools'])
            return httpx.Response(200,content=complete([function('read_market_data',{'date':'2026-10-30' if '周五' in question else 'today','section':'overview'},'flow')]))
        assert len(calls)==3
        item=next(item for item in payload['input'] if item.get('type')=='function_call_output')
        assert json.loads(item['output'])['tool_name']=='read_market_data'
        return httpx.Response(200,content=complete([final(answer)],answer))
    mock_network(monkeypatch,handler)
    try:
        enable(client,runtime,'openai');created=create(client,text='今天收盘行情数据有了吗？').json()
        assert drive(runtime,created['task_id'])['state']=='completed'
        response=client.post('/api/v1/ai/sessions/'+created['session_id']+'/messages',
                             json={'text':question,'consent':True,'request_key':'natural-flow-date'})
        assert response.status_code==202,response.text
        identifier=response.json()['task_id']
        assert drive(runtime,identifier)['state']=='completed' and len(reads)==1
        message=client.get('/api/v1/ai/sessions/'+created['session_id']).json()['messages'][-1]
        assert message['text']==answer and message['source_context']['scope']['purposes']==['market_review']
        assert not message['source_context']['web']['used']
        with core.connect() as db:
            assert all('web_policy' not in json.loads(row[0]) for row in db.execute('SELECT price_json FROM ai_request_receipt WHERE task_id=?',(identifier,)))
    finally:runtime.shutdown()


@pytest.mark.parametrize('question', ['今天银行转账的资金主要去了哪些方向？', '今天诈骗资金轮转是什么情况？',
                                      '今天政治献金资金流向如何？'])
def test_dated_non_equity_money_cannot_read_despite_prior_market_chat(agent_client,monkeypatch,question):
    client=agent_client;runtime=AIRuntime(fake);calls=[]
    monkeypatch.setattr(ai_read_tools,'execute_read_tool',lambda *a,**kw:pytest.fail('non-equity money cannot read'))
    async def handler(request):
        payload=json.loads(request.content);calls.append(payload)
        assert payload['tools']==[]
        if len(calls)==1:
            return httpx.Response(200,content=complete([function('read_market_data',{'date':'today','section':'fund_flow'},'forged-flow')]))
        item=next(item for item in payload['input'] if item.get('type')=='function_call_output')
        assert json.loads(item['output'])['state']=='scope_denied'
        return httpx.Response(200,content=complete([final('我仅提供A股行情复盘和个股研究。')],'我仅提供A股行情复盘和个股研究。'))
    mock_network(monkeypatch,handler)
    try:
        enable(client,runtime,'openai');created=create(client,text=question).json()
        with core.connect() as db:
            saved=json.loads(db.execute('SELECT request_json FROM ai_task WHERE task_id=?',(created['task_id'],)).fetchone()[0])
            saved['scope_input']['recent_user_texts']=['今天A股盘面复盘']
            db.execute('UPDATE ai_task SET request_json=? WHERE task_id=?',(core.dumps(saved),created['task_id']))
        assert drive(runtime,created['task_id'])['state']=='completed' and len(calls)==2
        with core.connect() as db:
            assert all('web_policy' not in json.loads(row[0]) for row in db.execute('SELECT price_json FROM ai_request_receipt WHERE task_id=?',(created['task_id'],)))
    finally:runtime.shutdown()
