"""topics application workflows; persistence and side effects live here."""
from __future__ import annotations
import hashlib,json,sqlite3
from decimal import Decimal,ROUND_HALF_UP
from fastapi import HTTPException
from app.core import connect,get_market,now_ms,new_id
from app.domain.conditions import validate,evaluate,metrics_for,names,specs
from app.services.workspace_common import *


def _provider_catalog(market=None):
    market=market if market is not None else get_market()
    for method in ('topics','topics_data','topic_catalog'):
        if hasattr(market,method):
            value=getattr(market,method)()
            if isinstance(value,dict):return value.get('items',value.get('topics',[]))
            return value or []
    return []

def _catalog_map(market=None):
    result={}
    for item in _provider_catalog(market):
        for key in ('topic_id','topic_code','code'):
            if item.get(key):result[str(item[key])]=item
    return result

def _provider_members(topic,catalog=None):
    if topic['origin']!='provider':return set()
    if catalog is not None:
        item=catalog.get(topic['provider_topic_key'],{})
        value=item.get('instrument_ids',item.get('members',[]))
        return {str(v.get('instrument_id') or v.get('thscode') or v.get('code')).upper() if isinstance(v,dict) else str(v).upper() for v in value}
    m=get_market();key=topic['provider_topic_key']
    if hasattr(m,'topic_members'):
        value=m.topic_members(key)
        if isinstance(value,dict):value=value.get('items',value.get('members',[]))
    else:
        item=next((x for x in _provider_catalog() if str(x.get('topic_id') or x.get('code') or x.get('topic_code'))==key),{})
        value=item.get('members',item.get('instrument_ids',[]))
    return {str(v.get('instrument_id') or v.get('thscode') or v.get('code')).upper() if isinstance(v,dict) else str(v).upper() for v in value or []}

def _topic(c,topic_id):
    return one(c,'''SELECT t.*,r.name,r.description,r.aliases_json,r.archived,r.revision,r.effective_business_date FROM topic t JOIN topic_revision r ON r.topic_id=t.topic_id WHERE t.topic_id=? ORDER BY r.revision DESC LIMIT 1''',(topic_id,),'题材不存在')

def _members(c,topic,day=None,catalog=None,overrides=None):
    day=day or current(c);provider=_provider_members(topic,catalog);members=set(provider);origins={x:'provider' for x in members}
    if overrides is None:overrides=rows(c,'''SELECT *,rowid AS _order FROM topic_override WHERE topic_id=? AND effective_business_date<=? ORDER BY effective_business_date,recorded_at_ms,rowid''',(topic['topic_id'],day))
    else:overrides=sorted((r for r in overrides if r['topic_id']==topic['topic_id'] and r['effective_business_date']<=day),key=lambda r:(r['effective_business_date'],r['recorded_at_ms'],r.get('_order',0)))
    latest={r['instrument_id']:r for r in overrides}
    for ident,r in latest.items():
        if r['action']=='include':members.add(ident);origins[ident]='manual'
        elif r['action']=='exclude':members.discard(ident);origins.pop(ident,None)
        elif ident in provider:members.add(ident);origins[ident]='provider'
        else:members.discard(ident);origins.pop(ident,None)
    return members,origins

def topic_view(c,r,with_members=False,catalog=None):
    catalog=_catalog_map() if catalog is None else catalog
    meta=catalog.get(r.get('provider_topic_key'),{})
    result={k:v for k,v in r.items() if k!='aliases_json'};result['aliases']=obj(r.get('aliases_json'),[])
    result.update(kind='manual' if r['origin']=='manual' else meta.get('kind','concept'),source_observed_date=meta.get('observed_date'),historical_membership=False,members_complete=True if r['origin']=='manual' else meta.get('members_complete',False))
    members,origin=_members(c,r,catalog=catalog)
    result['observed_member_count']=len(members)
    result['member_count']=len(members) if result['members_complete'] else None
    if with_members:
        day=current(c);master=identities(day);provider=_provider_members(r,catalog)
        latest={v['instrument_id']:v for v in rows(c,'SELECT * FROM topic_override WHERE topic_id=? AND effective_business_date<=? ORDER BY effective_business_date,recorded_at_ms,rowid',(r['topic_id'],day))}
        def member(ident,action):
            override=latest.get(ident,{}).get('action')
            return {**code_info(ident,master),'origin':origin.get(ident,'manual'),'action':action,'provider_member':ident in provider,'manual_override':override if override in ('include','exclude') else None}
        result['members']=[member(i,'include') for i in sorted(members)]
        result['excluded_members']=[member(i,'exclude') for i,v in latest.items() if v['action']=='exclude']
    return result

def list_topics(q='',include_archived=False,kind=None):
    if kind not in (None,'all','manual','concept','industry'):fail('不支持的题材类型')
    with connect() as c:
        catalog=_catalog_map()
        data=rows(c,'''SELECT t.*,r.name,r.description,r.aliases_json,r.archived,r.revision,r.effective_business_date FROM topic t JOIN topic_revision r ON r.topic_id=t.topic_id AND r.revision=(SELECT max(revision) FROM topic_revision WHERE topic_id=t.topic_id) ORDER BY r.name''')
        data=[topic_view(c,r,catalog=catalog) for r in data if (include_archived or not r['archived']) and (not q or q.lower() in (r['name']+r['description']+' '.join(obj(r['aliases_json'],[]))).lower())]
        if kind and kind!='all':data=[r for r in data if r['kind']==kind]
        return {'items':data,'total':len(data)}

def get_topic(topic_id):
    with connect() as c:return topic_view(c,_topic(c,topic_id),True)

def create_topic(payload):
    name=str(payload.get('name','')).strip()
    if not name or len(name)>100:fail('题材名称需要1至100字')
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        day=current(c);tid=new_id('topic');aliases=payload.get('aliases',[])
        if not isinstance(aliases,list) or any(not isinstance(x,str) for x in aliases):fail('aliases必须为字符串数组')
        insert(c,'topic',dict(topic_id=tid,origin='manual',created_at_ms=now_ms()))
        insert(c,'topic_revision',dict(topic_id=tid,revision=1,name=name,description=str(payload.get('description',''))[:2000],aliases_json=js(aliases),archived=0,effective_business_date=day,recorded_at_ms=now_ms()))
        for ident in payload.get('instrument_ids',[]):_add_member(c,tid,ident,'include','新建题材',day)
        return topic_view(c,_topic(c,tid),True)

def update_topic(topic_id,payload):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        old=_topic(c,topic_id);day=current(c);name=str(payload.get('name',old['name'])).strip()
        if not name or len(name)>100:fail('题材名称需要1至100字')
        aliases=payload.get('aliases',obj(old['aliases_json'],[]))
        if not isinstance(aliases,list) or any(not isinstance(x,str) for x in aliases):fail('aliases必须为字符串数组')
        insert(c,'topic_revision',dict(topic_id=topic_id,revision=old['revision']+1,name=name,description=str(payload.get('description',old['description']))[:2000],aliases_json=js(aliases),archived=int(bool(payload.get('archived',old['archived']))),effective_business_date=day,recorded_at_ms=now_ms()))
        return topic_view(c,_topic(c,topic_id),True)

def _add_member(c,topic_id,ident,action,reason,day,known_ids=None):
    if action not in ('include','exclude','reset_to_provider'):fail('不支持的题材成员操作')
    ident=str(ident).upper()
    if ident not in (known_ids if known_ids is not None else identities(day)):fail('股票代码不存在于当前已导入证券范围：'+ident)
    prev=c.execute('SELECT override_id FROM topic_override WHERE topic_id=? AND instrument_id=? ORDER BY effective_business_date DESC,recorded_at_ms DESC,rowid DESC LIMIT 1',(topic_id,ident)).fetchone()
    insert(c,'topic_override',dict(override_id=new_id('override'),topic_id=topic_id,instrument_id=ident,action=action,effective_business_date=day,recorded_at_ms=now_ms(),supersedes_override_id=prev[0] if prev else None,reason=str(reason or '用户手动修改')[:500]))

def add_member(topic_id,payload):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        topic=_topic(c,topic_id)
        if topic['archived']:fail('题材已归档，请先恢复')
        _add_member(c,topic_id,payload.get('instrument_id',''),payload.get('action','include'),payload.get('reason'),current(c))
        return topic_view(c,_topic(c,topic_id),True)

def topic_history(topic_id):
    with connect() as c:
        _topic(c,topic_id);data=[{**r,'kind':'revision'} for r in rows(c,'SELECT * FROM topic_revision WHERE topic_id=?',(topic_id,))]+[{**r,'kind':'override'} for r in rows(c,'SELECT * FROM topic_override WHERE topic_id=?',(topic_id,))]
        data.sort(key=lambda r:r['recorded_at_ms'],reverse=True);return {'items':data,'total':len(data)}

def topic_map(c,day):
    # This is a current business-day map. Historical backtests must use PIT, never this helper.
    result={};provider_catalog=_catalog_map()
    catalog=rows(c,'''SELECT t.*,r.name,r.archived FROM topic t JOIN topic_revision r ON r.topic_id=t.topic_id AND r.revision=(SELECT max(revision) FROM topic_revision WHERE topic_id=t.topic_id)''')
    for topic in catalog:
        if topic['archived']:continue
        members,_=_members(c,topic,day,catalog=provider_catalog)
        for ident in members:result.setdefault(ident,[]).append({'topic_id':topic['topic_id'],'name':topic['name']})
    return result

def complete_topic_ids(c,requested):
    if not requested:return []
    catalog=_catalog_map();complete=[]
    for topic in rows(c,'''SELECT t.*,r.archived FROM topic t JOIN topic_revision r ON r.topic_id=t.topic_id AND r.revision=(SELECT max(revision) FROM topic_revision WHERE topic_id=t.topic_id)'''):
        if topic['topic_id'] not in requested or topic['archived']:continue
        if topic['origin']=='manual' or catalog.get(topic['provider_topic_key'],{}).get('members_complete') is True:complete.append(topic['topic_id'])
    return sorted(complete)


def seed_topics():
    """Import current, already-local provider topics; does not fetch remote data or fabricate PIT."""
    with connect() as c:day=current(c)
    imported=0
    for item in _provider_catalog():
        key=str(item.get('topic_id') or item.get('code') or item.get('topic_code') or '')
        name=item.get('name') or item.get('topic_name')
        if not key or not name:continue
        with connect() as c:
            if c.execute('SELECT 1 FROM topic WHERE connection_id=? AND provider_topic_key=?',('import-fuyao',key)).fetchone():continue
            c.execute("INSERT INTO provider_connection(connection_id,provider_code,label,access_mode,created_at_ms) VALUES('import-fuyao','fuyao','已导入题材档案','public',?) ON CONFLICT(connection_id) DO NOTHING",(now_ms(),))
            tid='provider-'+hashlib.sha256(key.encode()).hexdigest()[:20]
            insert(c,'topic',dict(topic_id=tid,origin='provider',connection_id='import-fuyao',provider_topic_key=key,created_at_ms=now_ms()))
            insert(c,'topic_revision',dict(topic_id=tid,revision=1,name=str(name),description='已导入供应商当前题材快照；不是历史PIT成员',aliases_json='[]',archived=0,effective_business_date=day,recorded_at_ms=now_ms()))
            imported+=1
    return {'business_date':day,'imported_topics':imported}
