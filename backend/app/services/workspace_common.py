"""workspace_common application workflows; persistence and side effects live here."""
from __future__ import annotations
import hashlib,json,sqlite3
import os,threading
from decimal import Decimal,ROUND_HALF_UP
from fastapi import HTTPException
from app.core import connect,get_market,now_ms,new_id
from app.domain.conditions import validate,evaluate,metrics_for,names,specs


def js(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)

def obj(value,default=None):
    if value is None:return default
    try:return json.loads(value)
    except (ValueError,TypeError):return default

def digest(value):return hashlib.sha256(js(value).encode()).hexdigest()

def fail(message,status=422):raise HTTPException(status_code=status,detail=message)

def one(c,sql,args=(),message='记录不存在'):
    r=c.execute(sql,args).fetchone()
    if r is None:fail(message,404)
    return dict(r)

def rows(c,sql,args=()):return [dict(r) for r in c.execute(sql,args)]

def insert(c,table,r):
    ks=list(r);c.execute('INSERT INTO '+table+' ('+','.join(ks)+') VALUES ('+','.join('?' for _ in ks)+')',[r[k] for k in ks])

def current(c):
    r=c.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone()
    if not r:fail('请先到数据中心手动初始化真实行情，再使用自定义研究。',409)
    return r[0]

def freeze_inputs(c):
    data={r['module']:r['snapshot_id'] for r in c.execute('SELECT * FROM published_head ORDER BY module,scope_key')}
    primary=data.get('analysis_bars') or data.get('daily_raw')
    if not primary:fail('没有已发布行情快照，请先初始化数据。',409)
    return data,primary

def pin(c,snapshot,kind,owner):
    c.execute('INSERT INTO retention_pin(pin_id,snapshot_id,owner_kind,owner_id,created_at_ms) VALUES(?,?,?,?,?) ON CONFLICT(snapshot_id,owner_kind,owner_id) DO NOTHING',(new_id('pin'),snapshot,kind,owner,now_ms()))

def log(c,job_id,level,stage,message,details=None):
    seq=c.execute('SELECT coalesce(max(seq),0)+1 FROM job_event WHERE job_id=?',(job_id,)).fetchone()[0]
    insert(c,'job_event',dict(job_id=job_id,seq=seq,occurred_at_ms=now_ms(),level=level,stage=stage,message=message,details_json=js(details or {})))
def api_lease_owner():
    return 'api:'+str(os.getpid())+':'+new_id('lease')
def check_api_task(job_id,connection=None):
    def check(c):
        row=c.execute('SELECT status FROM job WHERE job_id=?',(job_id,)).fetchone()
        if not row or row['status']!='running':fail('任务已取消或停止，未继续提交结果。',409)
    if connection is not None:check(connection)
    else:
        with connect() as c:check(c)
def start_api_heartbeat(job_id,owner):
    """Only an explicitly started task gets a runtime heartbeat; imports start no threads."""
    stopped=threading.Event()
    def heartbeat():
        while not stopped.wait(20):
            try:
                with connect() as c:c.execute("UPDATE job SET lease_expires_at_ms=? WHERE job_id=? AND status='running' AND lease_owner=?",(now_ms()+120000,job_id,owner))
            except (sqlite3.Error,RuntimeError):
                # Keep the original expiry; a dead/unreachable process must not acquire an eternal lease.
                continue
    thread=threading.Thread(target=heartbeat,name='shixu-task-heartbeat',daemon=True);thread.start()
    def stop():
        stopped.set();thread.join(timeout=1)
        with connect() as c:c.execute("UPDATE job SET lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=? AND status<>'running' AND lease_owner=?",(job_id,owner))
    return stop

def e8(price):return int((Decimal(str(price))*100000000).quantize(Decimal('1'),rounding=ROUND_HALF_UP))

def money(value):return value/100000000 if value is not None else None

def market_panel(day):
    result=get_market().panel(day)
    if isinstance(result,dict):return result.get('items',result.get('stocks',[]))
    return result

def identities(day):
    market=get_market()
    data=market.instruments(limit=10000) if hasattr(market,'instruments') else market_panel(day)
    return {str(x.get('instrument_id') or x.get('thscode') or x.get('code')).upper():x for x in data}

def histories(day):
    m=get_market()
    if hasattr(m,'history_panel'):return m.history_panel(as_of=day)
    return {k:m.bars(k,as_of=day) for k in identities(day)}

def code_info(item_id,master):
    r=master.get(item_id,{})
    return {'instrument_id':item_id,'code':r.get('code') or item_id.split('.')[0],'name':r.get('name') or r.get('display_name') or item_id}
