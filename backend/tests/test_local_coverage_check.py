"""Explicit worker checks use isolated immutable files and never provider I/O."""
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient
import pandas as pd
import pytest

from test_platform import isolated
from app import core, worker
from app.services import local_coverage_check as check, source_service, task_service as tasks
from app.services.data_coverage import dataset_progress

A='600825.SH';B='000001.SZ';C='688827.SH';INDEX='000001.SH'
DAYS=['2026-01-05','2026-01-06','2026-01-07']


@pytest.fixture
def local_market(isolated,monkeypatch):
    directory=core.published_dir()/'market-fixture';directory.mkdir()
    master={symbol:{'instrument_id':symbol,'code':symbol[:6],'name':symbol,'kind':'stock','list_date':listed,'end_date':None}
            for symbol,listed in ((A,DAYS[0]),(B,DAYS[1]),(C,None))}
    master[INDEX]={'instrument_id':INDEX,'name':'index','kind':'index'}
    reference={'master':master,'calendar':DAYS}
    core.atomic_json(directory/'reference.json',reference)
    bars=[{'instrument_id':symbol,'date':day,'open':10.,'high':11.,'low':9.,'close':10.,'volume':100.,'amount':1000.}
          for symbol,dates in ((A,[DAYS[0],DAYS[2]]),(B,DAYS[1:]),(C,[DAYS[2]])) for day in dates]
    pd.DataFrame(bars).to_parquet(directory/'daily.parquet')
    raw=[{'thscode':row['instrument_id'],'date_ms':int(datetime.fromisoformat(row['date']).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()*1000),
          'open_price':row['open'],'high_price':row['high'],'low_price':row['low'],'close_price':row['close'],'volume':row['volume'],'turnover':row['amount']} for row in bars]
    pd.DataFrame(raw).to_parquet(directory/'raw-daily.parquet')
    actions=[{'thscode':A,'ticker':A[:6],'ex_date_ms':int(datetime(2026,1,6,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()*1000),
              'dividend_per_share':None,'per_share_bonus':0.,'allotment_ratio':0.,'allotment_price':0.,'currency':'CNY'}]
    pd.DataFrame(actions).to_parquet(directory/'corporate-actions.parquet')
    class Market:
        def status(self):return {'ready':True,'snapshot_id':'market-fixture','data_begin':DAYS[0],'data_end':DAYS[-1],
            'files':[{'role':path.name,'path':'published/market-fixture/'+path.name,'bytes':path.stat().st_size,'sha256':core.hash_file(path)} for path in sorted(directory.iterdir())]}
        def reference(self):return reference
        def calendar(self):return DAYS
    market=Market();frozen=market.status();market.status=lambda:frozen
    monkeypatch.setattr(check,'get_market',lambda:market)
    # Real isolated publication heads keep summary primary-snapshot FKs and
    # generation fencing active instead of bypassing them with a fake head.
    publisher=tasks.create_task('source_check',{'operation':'fixture_publish'})
    tasks.finish(publisher['job_id'])
    for module,identifier,name in [('calendar','calendar-fixture','reference.json'),('daily_raw','raw-fixture','raw-daily.parquet')]:
        path=directory/name
        core.publish_snapshot(module=module,snapshot_id=identifier,job_id=publisher['job_id'],start=DAYS[0],end=DAYS[-1],
            artifacts=[{'object_key':'market-fixture/'+name,'sha256':core.hash_file(path),'byte_count':path.stat().st_size,'row_count':5}],
            lineage={'market_snapshot_id':'market-fixture'},quality='partial')
    with core.connect() as connection:connection.execute('INSERT INTO business_clock VALUES(1,?,?,?)',(DAYS[-1],'calendar-fixture',core.now_ms()))
    monkeypatch.setattr(source_service,'client',lambda *a,**k:pytest.fail('local check must not inspect credentials or fetch'))
    return market,directory


def test_local_verification_counts_stock_opportunities_and_keeps_no_quote_unknown(local_market):
    market,_=local_market;events=[]
    report=check.verify(market,progress=lambda *args:events.append(args))
    assert report['identities']['stock_count']==3 and report['identities']['known_count']==2
    assert report['identities']['indices_excluded']==1
    daily=report['daily']
    assert daily['observed_count']==4 and daily['expected_count']==5
    assert daily['unquoted_sessions']==1 and daily['gap_stock_count']==1
    assert daily['gaps'][0]['instrument_id']==A and daily['gaps'][0]['dates']==[DAYS[1]]
    assert daily['gaps'][0]['classification']=='unquoted_unknown'
    assert daily['unknown_identities'][0]['instrument_id']==C
    assert daily['quote_rows']==5, 'unknown stock quote is retained but excluded from the supported denominator'
    assert report['corporate_actions']['schema_verified'] is True
    assert report['corporate_actions']['source_expected_count'] is None
    assert report['quality']=='partial'
    assert [event[3]['actual_completed_batches'] for event in events]==list(range(8))
    assert all(event[3]['actual_total_batches']==7 for event in events)


def test_listing_denominator_excludes_indices_and_quote_ratio_has_explicit_scope(local_market):
    market,_=local_market
    row={'module':'security_identity','observed_rows':4,'lineage_json':json.dumps({'market_snapshot_id':'market-fixture'})}
    progress=dataset_progress(row,market.reference(),'market-fixture')
    assert (progress['observed_count'],progress['expected_count'])==(2,3)
    report=check.verify(market)
    row['module']='daily_raw'
    progress=dataset_progress(row,market.reference(),'market-fixture',local_report=report)
    assert progress['percent']==80 and '出现率' in progress['label']
    assert '排除1只' in progress['basis'] and '无有效报价' in progress['reason']
    row['module']='corporate_actions'
    progress=dataset_progress(row,market.reference(),'market-fixture',local_report=report)
    assert progress['percent'] is None and progress['expected_count'] is None
    assert progress['coverage_label']=='来源总量未提供'


def test_worker_persists_report_only_getters_are_read_only_and_stale_files_hide_it(local_market,monkeypatch):
    market,directory=local_market
    before=tasks.list_tasks()['total']
    empty=check.read_view();assert empty['report'] is None
    assert not check._root().exists() and tasks.list_tasks()['total']==before
    task=check.create_check({'plan_id':empty['plan_id'],'request_key':'coverage-fixture'})
    same=check.create_check({'plan_id':empty['plan_id'],'request_key':'another-key'})
    assert same['job_id']==task['job_id']
    worker.dispatch(tasks.claim_next('coverage-test-worker'))
    assert tasks.task_view(task['job_id'])['status']=='partial'
    assert tasks.task_activity()['items'][0]['title']=='本地数据核验'
    small=check.read_view();full=check.read_view(full=True)
    assert 'gaps' not in small['report']['daily'] and len(full['report']['daily']['gaps'])==1
    assert 'stocks' not in small['report']['daily']['gap_diagnosis']
    diagnosis=full['report']['daily']['gap_diagnosis']
    assert diagnosis['episode_count']==diagnosis['isolated_episode_count']==1
    assert diagnosis['stocks'][0]['episodes'][0]['diagnosis_type']=='source_unquoted_unknown'
    assert check.listing_status.read_summary('market-fixture',DAYS[-1],market.reference())['listed_count']==2
    monkeypatch.setattr(check.duckdb,'connect',lambda *a,**k:pytest.fail('GET must not scan history'))
    assert check.read_view()['report']['daily']['expected_count']==5
    assert tasks.list_tasks()['total']==before+1
    with (directory/'daily.parquet').open('ab') as writer:writer.write(b'changed')
    stale=check.read_view();assert stale['report'] is None and stale['stale'] is True


def test_manual_listing_revision_invalidates_reports_without_any_quote_scan(local_market,monkeypatch):
    market,_=local_market;task=check.create_check({});check.execute_check(task['job_id'])
    assert check.read_view()['report']
    with core.connect() as connection:connection.execute('INSERT INTO listing_status_revision VALUES(1,1)')
    monkeypatch.setattr(check.duckdb,'connect',lambda *a,**k:pytest.fail('getter must not scan quotes'))
    view=check.read_view()
    assert view['stale'] and view['report'] is None


def test_raw_and_analysis_share_the_same_diagnosis_and_detect_local_conversion_difference(local_market):
    market,directory=local_market;path=directory/'daily.parquet'
    frame=pd.read_parquet(path);frame=frame[~((frame.instrument_id==B)&(frame.date==DAYS[1]))];frame.to_parquet(path)
    item=next(row for row in market.status()['files'] if row['role']==path.name)
    item.update(bytes=path.stat().st_size,sha256=core.hash_file(path))
    report=check.verify(market);diagnosis=report['daily']['gap_diagnosis']
    stock=next(row for row in diagnosis['stocks'] if row['instrument_id']==B)
    assert stock['episodes'][0]['diagnosis_type']=='local_normalization_difference'
    for module in ('daily_raw','analysis_bars'):
        progress=dataset_progress({'module':module,'lineage_json':json.dumps({'market_snapshot_id':'market-fixture'})},market.reference(),'market-fixture',local_report=report)
        assert progress['shared_diagnosis_id']==diagnosis['diagnosis_id']


def test_full_get_overlays_saved_source_evidence_without_rewriting_local_report(local_market,monkeypatch):
    from app.services import quote_gap_source_check as remote
    market,_=local_market;task=check.create_check({});check.execute_check(task['job_id'])
    context=check._context(market);report,_=check._read_report(context)
    episode=report['daily']['gap_diagnosis']['stocks'][0]['episodes'][0]
    evidence={'snapshot_id':report['snapshot_id'],'source_signature':report['source_signature'],
              'diagnosis_id':report['daily']['gap_diagnosis']['diagnosis_id'],'source':'fixture',
              'receipt_id':'receipt','receipt_sha256':'a'*64,'gaps':{episode['gap_id']:{'status':'source_no_quote','missing_dates':episode['dates']}}}
    pointer=(check._root()/'latest.json').read_bytes()
    monkeypatch.setattr(remote,'_read_evidence',lambda:evidence)
    monkeypatch.setattr(remote,'read_view',lambda **kwargs:pytest.fail('overlay must not recurse through remote view'))
    monkeypatch.setattr(check.duckdb,'connect',lambda *a,**k:pytest.fail('GET never rescans quote history'))
    view=check.read_view(full=True)
    assert view['report']['daily']['gap_diagnosis']['stocks'][0]['episodes'][0]['source_verification']['status']=='source_no_quote'
    assert (check._root()/'latest.json').read_bytes()==pointer
    assert check._read_report(context)[0]['daily']['gap_diagnosis']['stocks'][0]['episodes'][0]['source_verification'] is None


def test_pending_dates_use_current_quote_evidence_and_unknowns_still_occupy_listing_denominator(local_market,monkeypatch):
    market,directory=local_market;reference=market.reference()
    pending='688999.SH';future='301999.SZ'
    reference['master'][pending]={'instrument_id':pending,'kind':'stock','name':'pending','list_date':None}
    reference['master'][future]={'instrument_id':future,'kind':'stock','name':'future','list_date':'2026-01-08'}
    manual={symbol:{'status':'pending','observed_business_date':DAYS[0],'reason':'explicit fixture user evidence'} for symbol in (pending,C)}
    monkeypatch.setattr(check.listing_status,'confirmations',lambda connection=None:manual)
    report=check.verify(market);identities=report['identities']
    assert identities['catalog_count']==5 and identities['listed_count']==2
    assert identities['pending_listing_count']==2 and identities['unknown_count']==1
    assert identities['unknown'][0]['instrument_id']==C, 'real quote conflicts with a manual pending claim'
    assert report['daily']['expected_count']==5, 'pending stocks add no historical quote opportunities'
    progress=dataset_progress({'module':'security_identity','data_end':DAYS[-1],'lineage_json':json.dumps({'market_snapshot_id':'market-fixture'})},reference,'market-fixture',local_report=report)
    assert progress['observed_count']==2 and progress['expected_count']==3
    assert progress['catalog_count']==5 and progress['pending_listing_count']==2
    assert progress['percent']<100, 'an actually unknown date cannot be silently removed from the denominator'


@pytest.mark.parametrize('record_kind',['raw_only','duplicate','missing_raw'])
def test_pending_absence_requires_both_raw_and_analysis_without_ambiguous_records(local_market,monkeypatch,record_kind):
    market,directory=local_market;symbol='688999.SH'
    market.reference()['master'][symbol]={'instrument_id':symbol,'kind':'stock','name':'pending candidate','list_date':None}
    manual={symbol:{'status':'pending','observed_business_date':DAYS[0],'reason':'explicit fixture user statement'}}
    monkeypatch.setattr(check.listing_status,'confirmations',lambda connection=None:manual)
    name='raw-daily.parquet' if record_kind!='duplicate' else 'daily.parquet';path=directory/name
    if record_kind=='missing_raw':path.write_bytes(b'not the accepted file')
    else:
        data=pd.read_parquet(path);row=data.iloc[0].to_dict()
        row['thscode' if record_kind=='raw_only' else 'instrument_id']=symbol
        pd.concat([data,pd.DataFrame([row] if record_kind=='raw_only' else [row,row])],ignore_index=True).to_parquet(path)
        item=next(item for item in market.status()['files'] if item['role']==name)
        item.update(bytes=path.stat().st_size,sha256=core.hash_file(path))
    collected={}
    report=check.verify(market,listing_callback=lambda value:collected.update(value))
    proof=collected[symbol]
    assert proof['valid_quote_count']==0
    assert report['identities']['pending_listing_count']==0
    assert symbol in {row['instrument_id'] for row in report['identities']['unknown']}
    if record_kind=='missing_raw':assert proof['absence_verified'] is False
    else:assert proof['observed_quote_rows']>0
    if record_kind=='duplicate':assert proof['ambiguous_quote_keys']==1 and proof['analysis_observed_rows']==2


def test_hash_failure_never_promotes_a_quote_measure_to_complete(local_market):
    market,directory=local_market
    (directory/'daily.parquet').write_bytes(b'broken')
    result=check.verify(market)
    assert result['daily']['ready'] is False and result['failures']
    assert not next(row for row in result['file_checks'] if row['role']=='daily.parquet')['sha256_verified']


def test_cancellation_or_generation_change_cannot_publish_a_new_report(local_market):
    market,_=local_market
    initial=check.read_view();task=check.create_check({'plan_id':initial['plan_id']})
    def progress(*args):tasks.change_task(task['job_id'],'cancel')
    with pytest.raises(tasks.TaskCancelled):check.execute_check(task['job_id'],progress)
    assert not (check._root()/'latest.json').exists()


def test_routes_expose_explicit_check_without_mutating_get(local_market):
    from app.routers import platform
    app=FastAPI();app.include_router(platform.router)
    before=tasks.list_tasks()['total']
    with TestClient(app) as client:
        plan=client.get('/api/v1/data/coverage/check').json()
        assert plan['network'] is False and tasks.list_tasks()['total']==before
        assert client.post('/api/v1/data/coverage/check',json={'plan_id':'stale'}).status_code==409
        assert client.post('/api/v1/data/coverage/check',json={'remote':True}).status_code==422
        response=client.post('/api/v1/data/coverage/check',json={'plan_id':plan['plan_id'],'request_key':'explicit-local-check'})
    assert response.status_code==200 and response.json()['parameters']['operation']=='local_coverage_check'


def test_listing_window_bounds_exclude_prelisting_delisted_and_index_opportunities():
    reference={'calendar':DAYS,'master':{
        A:{'kind':'stock','list_date':DAYS[1],'end_date':DAYS[-1]},
        B:{'kind':'stock','list_date':'2020-01-01','end_date':DAYS[0]},
        C:{'kind':'stock','list_date':None},INDEX:{'kind':'index','list_date':'1990-01-01'}}}
    _,stocks,ranges,unknown=check.stock_scope(reference,DAYS[0],DAYS[-1])
    assert len(stocks)==3 and {row[0]:row[3] for row in ranges}=={A:2,B:1}
    assert [row['instrument_id'] for row in unknown]==[C]


def test_duplicate_or_invalid_quotes_are_not_blessed_as_covered(local_market):
    market,directory=local_market
    rows=pd.read_parquet(directory/'daily.parquet')
    pd.concat([rows,rows.iloc[:1]],ignore_index=True).to_parquet(directory/'daily.parquet')
    status=market.status();changed=next(row for row in status['files'] if row['role']=='daily.parquet')
    changed.update(bytes=(directory/'daily.parquet').stat().st_size,sha256=core.hash_file(directory/'daily.parquet'))
    report=check.verify(market)
    assert report['daily']['duplicate_rows']==1
    assert report['daily']['observed_count']==3 and report['daily']['unquoted_sessions']==2
    assert report['daily']['gaps'][0]['dates']==DAYS[:2]


def test_repair_plan_freezes_reviewed_scope_and_rejects_changes_before_credentials(local_market,monkeypatch):
    from app.routers import platform
    from app.services import market_supplement
    market,_=local_market
    task=check.create_check({});check.execute_check(task['job_id']);tasks.finish(task['job_id'],'partial')
    plan=check.read_view(full=True);calls=[]
    monkeypatch.setattr(source_service,'client',lambda *_:(calls.append('credentials') or type('Client',(),{'key':'fixture'})()))
    monkeypatch.setattr(core,'get_market',lambda:market)
    app=FastAPI();app.include_router(platform.router)
    with TestClient(app) as client:
        payload={**plan['report']['repair_plan']['payload'],'coverage_plan_id':plan['plan_id']}
        assert client.post('/api/v1/sync/market-supplement',json={**payload,'coverage_plan_id':'stale'}).status_code==409
        assert client.post('/api/v1/sync/market-supplement',json={**payload,'sector_history':True}).status_code==422
        assert calls==[]
        accepted=client.post('/api/v1/sync/market-supplement',json=payload)
    assert accepted.status_code==200
    parameters=accepted.json()['parameters']
    assert parameters['market_snapshot_id']=='market-fixture' and parameters['coverage_plan_id']==plan['plan_id']
    assert parameters['sector_history'] is False
    monkeypatch.setattr(market_supplement,'get_market',lambda:type('Changed',(),{'status':lambda self:{'snapshot_id':'changed','data_end':DAYS[-1]}})())
    with pytest.raises(ValueError,match='代际已变化'):market_supplement.execute_supplement(accepted.json()['job_id'])
    assert calls==['credentials'], 'generation guard rejects before worker/provider access'


@pytest.mark.parametrize('payload',[[],True,None,{'request_key':{}},{'request_key':True},{'request_key':''},{'request_key':' '*10},{'request_key':'x'*201},{'plan_id':12}])
def test_invalid_check_payload_is_422_before_planning(payload,local_market):
    before=tasks.list_tasks()['total']
    with pytest.raises(HTTPException) as caught:check.create_check(payload)
    assert caught.value.status_code==422 and tasks.list_tasks()['total']==before


@pytest.mark.parametrize('payload',[None,[],True,'invalid',{'request_key':[]},{'plan_id':False}])
def test_http_invalid_request_body_is_a_clean_422(payload,local_market):
    from app.routers import platform
    app=FastAPI();app.include_router(platform.router)
    before=tasks.list_tasks()['total']
    with TestClient(app) as client:response=client.post('/api/v1/data/coverage/check',json=payload)
    assert response.status_code==422 and tasks.list_tasks()['total']==before


def test_outside_scope_invalid_quotes_and_action_errors_never_report_complete(local_market,monkeypatch):
    market,directory=local_market
    original_daily=check._daily;original_actions=check._actions
    market.reference()['master'][C]['list_date']=DAYS[-1]
    core.atomic_json(directory/'reference.json',market.reference())
    source=next(item for item in market.status()['files'] if item['role']=='reference.json')
    source.update(bytes=(directory/'reference.json').stat().st_size,sha256=core.hash_file(directory/'reference.json'))
    def daily(*args,**kwargs):
        result=original_daily(*args,**kwargs)
        result.update(unknown_identities=[],unquoted_sessions=0,duplicate_rows=1,invalid_price_keys=1)
        return result
    monkeypatch.setattr(check,'_daily',daily)
    # Even if all source pool batches are known, schema/price failures remain
    # partial independently of narrow opportunity counts or identities.
    monkeypatch.setattr('app.services.market_supplement.load_supplement',lambda *_:{'annual_pools':{day:{} for day in DAYS},'annual_down_pools':{day:{} for day in DAYS}})
    assert check.verify(market)['quality']=='partial'
    monkeypatch.setattr(check,'_daily',lambda *a,**k:{'ready':False})
    assert check.verify(market)['quality']=='partial'
    monkeypatch.setattr(check,'_daily',lambda *a,**k:{'ready':True,'unquoted_sessions':0})
    monkeypatch.setattr(check,'_actions',lambda *a,**k:{**original_actions(*a,**k),'ready':False,'invalid_rows':1})
    assert check.verify(market)['quality']=='partial'


@pytest.mark.parametrize('inside',[False,True])
def test_special_negative_actions_keep_window_and_whole_file_scopes_separate(local_market,inside):
    market,directory=local_market;path=directory/'corporate-actions.parquet'
    source=pd.read_parquet(path);special=source.iloc[0].to_dict()
    special.update(per_share_bonus=-.67335,ex_date_ms=int(datetime(2026 if inside else 2007,1,6,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()*1000))
    pd.concat([source,pd.DataFrame([special])],ignore_index=True).to_parquet(path)
    item=next(row for row in market.status()['files'] if row['role']==path.name)
    item.update(bytes=path.stat().st_size,sha256=core.hash_file(path))
    report=check.verify(market);actions=report['corporate_actions']
    assert actions['ready'] is False and actions['file_ready'] is False
    assert actions['window_ready'] is (not inside)
    assert actions['invalid_rows']==actions['file_invalid_rows']==actions['file_special_rows']==1
    assert actions['file_structural_invalid_rows']==0
    assert actions['window_invalid_rows']==int(inside)
    assert actions['outside_window_special_rows']==int(not inside)
    assert actions['special_items'][0]['code']==A[:6]
    assert actions['special_items'][0]['scope']==('window' if inside else 'outside_window')
    assert report['quality']=='partial' and report['failures']==[] and report['notices']
    assert pd.read_parquet(path).iloc[-1]['per_share_bonus']==-.67335


def test_action_problem_items_are_bounded_and_unknown_date_cannot_pass_window(local_market):
    market,directory=local_market;path=directory/'corporate-actions.parquet'
    original=pd.read_parquet(path).iloc[0].to_dict()
    values=[{**original,'ex_date_ms':None,'per_share_bonus':-.1} for _ in range(25)]
    pd.DataFrame(values).to_parquet(path)
    with check.duckdb.connect(':memory:') as connection:
        result=check._actions(connection,path,market.reference(),DAYS[0],DAYS[-1])
    assert result['window_ready'] is False and result['unscoped_date_rows']==25
    assert result['file_invalid_rows']==25 and result['items_truncated'] is True
    assert len(result['special_items'])+len(result['invalid_items'])==20
