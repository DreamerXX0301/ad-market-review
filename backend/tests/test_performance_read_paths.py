"""Optimizations preserve identities, unknowns, policies and actual SHA checks."""
from contextlib import contextmanager
from datetime import date, datetime
import json
import math
import os

import numpy as np

from app import core
from app.services import strategies, sector_charts as charts
from app.services.chart_transport import chart_payload
from test_workspace import ws, make_strategy, add_one
from test_sector_charts import local, collect, CODE, DAY, pointer
from test_instrument_detail_projection import detail_client


def test_json_scalar_fast_path_preserves_canonical_types_and_unknowns():
    value={3:(np.int64(9),np.bool_(True),np.float64('nan'),-0.0),
           'date':date(2026,10,29),'stamp':datetime(2026,10,29,9,25),
           'values':[float('inf'),float('-inf'),None,False,'中文']}
    expected={'3':[9,True,None,-0.0],'date':'2026-10-29','stamp':'2026-10-29T09:25:00',
              'values':[None,None,None,False,'中文']}
    assert core.json_clean(value)==expected
    assert core.dumps(value)==json.dumps(expected,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    assert math.copysign(1,core.json_clean(value)['3'][3])==-1


def expand(value):
    encoded=value['bars_columnar']; rows=[{} for _ in range(encoded['count'])]
    for field,column in zip(encoded['fields'],encoded['columns']):
        absent=set(encoded['missing'].get(field,[]))
        for i,cell in enumerate(column):
            if i not in absent:rows[i][field]=cell
    return {**{key:item for key,item in value.items() if key!='bars_columnar'},'bars':rows}


def test_chart_transport_keeps_every_field_null_absence_and_negative_zero():
    source={'as_of':DAY,'bars':[{'date':DAY,'close':1.25,'volume':None,'proof':{'known':False}},
                              {'date':DAY,'close':-0.0,'extra':[1,'中文']},{'date':DAY,'close':None}],
            'coverage':{'unknown':None},'indicator_gaps':['原始缺口']}
    old=core.dumps(source)
    result=expand(json.loads(json.dumps(chart_payload(source,'columns-v1'))))
    assert core.dumps(result)==old
    assert 'volume' not in result['bars'][1] and result['bars'][0]['volume'] is None
    assert chart_payload(source)==source and core.dumps(source)==old


def test_chart_http_opt_in_preserves_legacy_rows_and_every_field(detail_client):
    market,_,_,client=detail_client
    url='/api/v1/instruments/'+market.symbol+'/chart'
    legacy=client.get(url)
    packed=client.get(url,params={'encoding':'columns-v1'})
    assert legacy.status_code==packed.status_code==200
    assert 'bars' in legacy.json() and 'bars_columnar' not in legacy.json()
    assert core.dumps(legacy.json())==core.dumps(expand(packed.json()))
    assert client.get(url,params={'encoding':'invalid'}).status_code==422


def test_batch_list_matches_individual_views_and_current_policy(ws, monkeypatch):
    _,client=ws
    first=make_strategy(client); add_one(client,first)
    for _ in range(20):make_strategy(client)
    def compare():
        actual=strategies.list_strategies()
        with core.connect() as connection:
            ids=[row['strategy_id'] for row in actual['items']]
            context=strategies.stock_daily_once.read_context(connection,strategy_ids=ids)
            expected=[strategies.strategy_view(connection,sid,run_context=context) for sid in ids]
        assert actual['items']==expected
        return actual
    before=compare()
    strategies.update_strategy(first['strategy_id'],{'name':'已更新名称','policy':{'duration_sessions':17}})
    after=compare()
    assert before['items'][0]['policy']['duration_sessions']!=after['items'][0]['policy']['duration_sessions']
    statements=[]
    @contextmanager
    def observed():
        with core.connect() as connection:
            connection.set_trace_callback(statements.append)
            yield connection
    monkeypatch.setattr(strategies,'connect',observed)
    strategies.list_strategies()
    assert sum(sql.lstrip().upper().startswith('SELECT') for sql in statements)<35
    assert strategies.list_strategies(limit=3,offset=2)['items']==after['items'][2:5]


def test_chart_cache_revalidates_bytes_and_cannot_share_mutable_rows(local, monkeypatch):
    market,_,_=local; collect(local)
    original=charts.read_sector_chart(market,CODE,DAY)
    expected=core.dumps(original)
    original['bars'][-1]['close']=999999
    normalize=charts.normalize_sector_chart
    monkeypatch.setattr(charts,'normalize_sector_chart',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('warm chart must reuse calculations')))
    assert core.dumps(charts.read_sector_chart(market,CODE,DAY))==expected
    monkeypatch.setattr(charts,'normalize_sector_chart',normalize)
    saved=json.loads(pointer(local).read_text())
    record=json.loads((charts._root()/saved['artifact']).read_text())
    raw=charts._root()/record['raw_artifact']; info=raw.stat();body=raw.read_bytes()
    raw.write_bytes(body.replace(b'100.0',b'101.0',1) if b'100.0' in body else body[:-1]+b'!')
    os.utime(raw,ns=(info.st_atime_ns,info.st_mtime_ns))
    assert charts.read_sector_chart(market,CODE,DAY)['available'] is False


def test_calendar_cache_observes_date_and_calendar_changes():
    from app.analytics.period_dates import latest_closed_date, _latest_closed_date
    calendar=['2026-10-28','2026-10-29','2026-10-30','2026-11-02']
    assert latest_closed_date(calendar,'2026-10-29','W') is None
    assert latest_closed_date(calendar,'2026-10-30','M')=='2026-10-30'
    calendar.pop()
    assert latest_closed_date(calendar,'2026-10-30','M') is None
    assert _latest_closed_date.cache_info().maxsize==32


def test_sector_strategy_list_matches_individual_views_with_three_queries(ws,monkeypatch):
    from app.services import sector_strategies as sectors
    ids=[sectors.create_strategy({'name':'板块样本'+str(i),'definition':{'field':'close','op':'>','value':0}})['strategy_id'] for i in range(20)]
    expected=[sectors.get_strategy(sid) for sid in ids]
    statements=[]
    @contextmanager
    def observed():
        with core.connect() as connection:
            connection.set_trace_callback(statements.append)
            yield connection
    monkeypatch.setattr(sectors,'connect',observed)
    actual=sectors.list_strategies()
    assert actual['items']==sorted(expected,key=lambda row:(row['created_at_ms'],row['strategy_id']))
    assert sum(sql.lstrip().upper().startswith('SELECT') for sql in statements)==3
