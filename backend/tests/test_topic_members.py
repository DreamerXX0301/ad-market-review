"""Current multi-topic members: isolated SQLite and controlled quotes, no network."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from app import core
from app.services import topic_members,topics
from test_workspace import ws

A='600001.SH';B='000001.SZ';C='300001.SZ';D='688001.SH';E='920001.BJ'


@pytest.fixture
def memberships(ws,monkeypatch):
    market,client=ws;day=market.entry;before=market.days[69]
    calls={'panel':0,'master':0}
    master=[{'instrument_id':symbol,'code':symbol[:6],'name':name,'kind':'stock'} for symbol,name in [(A,'甲股票'),(B,'乙股票'),(C,'丙股票'),(D,'丁股票'),(E,'戊股票')]]
    quotes=[{'instrument_id':A,'date':day,'close':10,'change_pct':-3,'amount':100,'turnover_ratio_pct':None},{'instrument_id':B,'date':day,'close':20,'change_pct':5,'amount':200,'turnover_ratio_pct':2},{'instrument_id':C,'date':day,'close':30,'change_pct':-1,'amount':50,'turnover_ratio_pct':None},{'instrument_id':D,'date':before,'close':40,'change_pct':8,'amount':500,'turnover_ratio_pct':99}]
    catalog=[{'topic_id':'concept-a','name':'概念甲','kind':'concept','members_complete':True,'observed_date':day,'instrument_ids':[A,B,D]},{'topic_id':'concept-b','name':'概念乙','kind':'concept','members_complete':True,'observed_date':day,'instrument_ids':[B,C,E]},{'topic_id':'industry-a','name':'行业甲','kind':'industry','members_complete':True,'observed_date':day,'instrument_ids':[A,C,D]},{'topic_id':'concept-partial','name':'未完整概念','kind':'concept','members_complete':False,'observed_date':day,'instrument_ids':[A]}]
    def instruments(query='',limit=10000):calls['master']+=1;return list(master)
    def panel(as_of):calls['panel']+=1;assert as_of==day;return list(quotes)
    monkeypatch.setattr(market,'instruments',instruments)
    monkeypatch.setattr(market,'panel',panel)
    monkeypatch.setattr(market,'topics',lambda:list(catalog))
    monkeypatch.setattr(market,'status',lambda:{'snapshot_id':'fixture-generation'},raising=False)
    monkeypatch.setattr(topic_members,'get_market',lambda:market)
    supplement={'public_pools':{day:{'date':day,'items':[{'instrument_id':A,'date':day,'turnover_rate':7},{'instrument_id':D,'date':day,'turnover_rate':77},{'instrument_id':C,'date':before,'turnover_rate':88}]}}}
    monkeypatch.setattr(topic_members,'load_supplement',lambda snapshot: supplement if snapshot=='fixture-generation' else {})
    seeded=client.post('/api/v1/topics/initialize',json={});assert seeded.status_code==200,seeded.text
    identifiers={row['name']:row['topic_id'] for row in seeded.json()['items']}
    return market,client,identifiers,calls,quotes


def query(client,*selected,**params):
    response=client.get('/api/v1/topic-members',params=[('topic_ids',key) for key in selected]+list(params.items()))
    assert response.status_code==200,response.text
    return response.json()


def overrides():
    with core.connect() as connection:return connection.execute('SELECT count(*) FROM topic_override').fetchone()[0]


def test_union_intersection_full_members_and_one_quote_join(memberships):
    market,client,ids,calls,_=memberships
    before=dict(calls)
    union=query(client,ids['概念甲'],ids['概念乙'])
    assert {row['instrument_id'] for row in union['items']}=={A,B,C,D,E}
    assert union['total']==5 and union['business_date']==union['quote_date']==market.entry
    assert calls['panel']-before['panel']==1 and calls['master']-before['master']==1
    common=query(client,ids['概念甲'],ids['概念乙'],match='all')
    assert [row['instrument_id'] for row in common['items']]==[B]
    assert {row['topic_id'] for row in common['items'][0]['matched_topics']}=={ids['概念甲'],ids['概念乙']}
    assert common['items'][0]['manual_override'] is None and common['items'][0]['origin'] is None
    assert common['members_complete'] is True


def test_negative_sort_null_last_and_exact_date_no_old_quote(memberships):
    _,client,ids,_,_=memberships
    selected=[ids['概念甲'],ids['概念乙']]
    descending=query(client,*selected,sort_by='change_pct',sort_order='desc')
    ascending=query(client,*selected,sort_by='change_pct',sort_order='asc')
    assert [row['instrument_id'] for row in descending['items']]==[B,C,A,D,E]
    assert [row['instrument_id'] for row in ascending['items']]==[A,C,B,D,E]
    stale=next(row for row in descending['items'] if row['instrument_id']==D)
    assert stale['name']=='丁股票' and stale['quote_available'] is False and stale['quote_date'] is None
    assert all(stale[key] is None for key in ('close','change_pct','amount','turnover_rate'))
    assert query(client,*selected,query='丁股票')['total']==1
    assert query(client,*selected,sort_by='turnover_rate',sort_order='desc')['items'][0]['turnover_rate']==7
    # C's public field is from yesterday and cannot fill today's unknown ratio.
    c=next(row for row in descending['items'] if row['instrument_id']==C)
    assert c['turnover_rate'] is None


def test_single_topic_compatibility_and_multiple_classification_tags(memberships):
    _,client,ids,_,_=memberships
    manual=client.post('/api/v1/topics',json={'name':'人工研究','instrument_ids':[A]}).json()
    data=query(client,ids['概念甲'])
    stock=next(row for row in data['items'] if row['instrument_id']==A)
    assert stock['origin']=='provider' and stock['provider_member'] is True and stock['manual_override'] is None
    assert stock['industries']==['行业甲']
    assert {'概念甲','未完整概念','人工研究'}.issubset(stock['topics'])
    assert '人工研究' not in stock['industries']
    assert manual['topic_id']


def test_manual_exclusion_and_additive_restore_are_state_idempotent(memberships):
    _,client,ids,_,_=memberships;topic=ids['概念甲']
    excluded=client.post('/api/v1/topics/'+topic+'/members',json={'instrument_id':A,'action':'exclude'});assert excluded.status_code==200
    assert A not in {row['instrument_id'] for row in query(client,topic)['items']}
    count=overrides()
    payload={'instrument_ids':[A,C],'topic_ids':[topic,ids['行业甲']],'reason':'多归属研究'}
    first=client.post('/api/v1/topic-members/associate',json=payload)
    assert first.status_code==200,first.text
    result=first.json();assert result['added_count']==2 and result['unchanged_count']==2 and result['association_count']==4
    assert overrides()==count+2
    again=client.post('/api/v1/topic-members/associate',json=payload).json()
    assert again['added_count']==0 and again['unchanged_count']==4 and overrides()==count+2
    assert again['idempotency']=='effective-membership-state'
    member=next(row for row in query(client,topic)['items'] if row['instrument_id']==A)
    assert member['origin']=='manual' and member['manual_override']=='include' and member['provider_member'] is True
    # Existing other concept and industry membership survives additive writes.
    c=next(row for row in query(client,topic)['items'] if row['instrument_id']==C)
    assert {'概念甲','概念乙'}.issubset(c['topics']) and c['industries']==['行业甲']


@pytest.mark.parametrize('invalid',[
    {'instrument_ids':[A,'999999.SH']},
    {'topic_ids':['missing-topic']},
    {'topic_ids':[]},
    {'instrument_ids':[]},
    {'instrument_ids':[A]*101},
    {'topic_ids':['same-topic']*21},
])
def test_batch_validation_failure_writes_nothing(memberships,invalid):
    _,client,ids,_,_=memberships
    before=overrides()
    response=client.post('/api/v1/topic-members/associate',json={'instrument_ids':[A,C],'topic_ids':[ids['概念乙'],ids['行业甲']],**invalid})
    assert response.status_code==422,response.text
    assert overrides()==before


def test_archived_read_only_and_partial_membership_warning(memberships):
    _,client,ids,_,_=memberships
    archived=ids['概念甲']
    assert client.patch('/api/v1/topics/'+archived,json={'archived':True}).status_code==200
    readonly=query(client,archived);assert readonly['selected_topics'][0]['archived'] is True
    assert readonly['total']==3 and any('归档' in message for message in readonly['warnings'])
    before=overrides()
    rejected=client.post('/api/v1/topic-members/associate',json={'instrument_ids':[C],'topic_ids':[ids['概念乙'],archived]})
    assert rejected.status_code==422 and overrides()==before
    partial=query(client,ids['未完整概念'],ids['概念乙'],match='all')
    assert partial['members_complete'] is False and partial['total']==0
    assert any('已知正成员' in message for message in partial['warnings'])


def test_invalid_get_scope_and_sort_are_422(memberships):
    _,client,ids,_,_=memberships
    assert client.get('/api/v1/topic-members').status_code==422
    for params in ({'topic_ids':'missing-topic'},{'topic_ids':ids['概念甲'],'sort_by':'unsupported'},{'topic_ids':ids['概念甲'],'sort_order':'sideways'},{'topic_ids':ids['概念甲'],'match':'none'}):
        assert client.get('/api/v1/topic-members',params=params).status_code==422


def test_equal_returns_have_stable_codes_and_no_quotes_keep_target_date(memberships):
    market,client,ids,_,quotes=memberships
    quotes[0]['change_pct']=quotes[1]['change_pct']=-2
    result=query(client,ids['概念甲'])
    assert [row['instrument_id'] for row in result['items'][:2]]==[B,A]
    quotes.clear()
    empty=query(client,ids['概念甲'])
    assert empty['quote_date']==market.entry and empty['total']==3
    assert all(row['quote_date'] is None and row['quote_available'] is False for row in empty['items'])


def test_concurrent_same_effective_association_writes_once(memberships):
    _,client,ids,_,_=memberships
    payload={'instrument_ids':[C],'topic_ids':[ids['概念甲']]}
    before=overrides()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(lambda _:client.post('/api/v1/topic-members/associate',json=payload),range(2)))
    assert all(response.status_code==200 for response in results)
    assert sorted(response.json()['added_count'] for response in results)==[0,1]
    assert overrides()==before+1


def test_same_millisecond_override_order_and_supersedes_are_consistent(memberships,monkeypatch):
    _,client,identifiers,_,_=memberships;topic=identifiers['概念甲']
    identifiers=iter(['override-z','override-y','override-x'])
    monkeypatch.setattr(topics,'new_id',lambda prefix:next(identifiers))
    monkeypatch.setattr(topics,'now_ms',lambda:1000000000000)
    for action in ('exclude','include','exclude'):
        response=client.post('/api/v1/topics/'+topic+'/members',json={'instrument_id':A,'action':action})
        assert response.status_code==200,response.text
    with core.connect() as connection:
        chain=[tuple(row) for row in connection.execute('SELECT override_id,supersedes_override_id FROM topic_override ORDER BY rowid')]
    assert chain==[('override-z',None),('override-y','override-z'),('override-x','override-y')]
    assert A not in {row['instrument_id'] for row in query(client,topic)['items']}


def test_storage_failure_rolls_back_already_written_associations(memberships,monkeypatch):
    _,client,identifiers,_,_=memberships;before=overrides();original=topics._add_member;written=[]
    def storage_failure(connection,topic_id,ident,*args,**kwargs):
        if written:raise sqlite3.IntegrityError('controlled second-write failure')
        original(connection,topic_id,ident,*args,**kwargs);written.append(ident)
    monkeypatch.setattr(topics,'_add_member',storage_failure)
    with pytest.raises(sqlite3.IntegrityError):
        client.post('/api/v1/topic-members/associate',json={'instrument_ids':[C,E],'topic_ids':[identifiers['概念甲']]})
    assert written==[C] and overrides()==before
    assert C not in {row['instrument_id'] for row in query(client,identifiers['概念甲'])['items']}


def test_business_day_changed_during_quote_join_returns_409(memberships,monkeypatch):
    market,client,identifiers,_,_=memberships;original=market.panel
    def changed_day(day):
        result=original(day)
        with core.connect() as connection:connection.execute('UPDATE business_clock SET business_date=? WHERE singleton=1',(market.days[71],))
        return result
    monkeypatch.setattr(market,'panel',changed_day)
    response=client.get('/api/v1/topic-members',params={'topic_ids':identifiers['概念甲']})
    assert response.status_code==409 and '业务日或行情发布已变化' in response.json()['detail']


def test_business_day_changed_before_atomic_association_writes_nothing(memberships,monkeypatch):
    market,client,identifiers,_,_=memberships;original=topic_members._market_context;before=overrides()
    def changed_context():
        result=original()
        with core.connect() as connection:connection.execute('UPDATE business_clock SET business_date=? WHERE singleton=1',(market.days[71],))
        return result
    monkeypatch.setattr(topic_members,'_market_context',changed_context)
    response=client.post('/api/v1/topic-members/associate',json={'instrument_ids':[C,E],'topic_ids':[identifiers['概念甲']]})
    assert response.status_code==409 and overrides()==before
