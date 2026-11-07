"""Controlled transport fixtures only; no authentication, source requests or user DB writes."""
from datetime import datetime
from pathlib import Path
import json
import shutil
import duckdb
import pandas as pd
import pytest

from app.analytics.market import MarketData,INDEX_NAMES,MEMBERS
from app.services import sync_service as sync
from app.services import task_service as tasks
from app.core import connect,get_market,clear_market_cache


class FixtureTransport:
    def __init__(self,root,begin='2023-10-20'):
        self.root=root;self.calls=[];self.downloads=[];self.fail=None
        self.calendar=[d.strftime('%Y-%m-%d') for d in pd.bdate_range(begin,'2026-10-20')]
        self.official_calendar=[day for day in self.calendar if day>='2025-10-20']
        self.master=[{'thscode':s,'ticker':s[:6],'name':n,'exchange':s[-2:],'asset_type':'a-share','list_date':'2000-01-01'} for s,n in [('600000.SH','测试甲'),('000001.SZ','测试乙')]]
        records=[]
        for i,day in enumerate(self.calendar):
            for symbol in ('600000.SH','000001.SZ'):
                records.append({'thscode':symbol,'currency':'CNY','interval':'1d','adjusted':'none','date_ms':sync.midnight_ms(day),'open_price':10+i*.01,'high_price':10.2+i*.01,'low_price':9.8+i*.01,'close_price':10.1+i*.01,'volume':100000.0,'turnover':1000000.0})
        frame=pd.DataFrame(records);frame.to_parquet(root/'full.parquet',index=False)
        frame.tail(20).to_parquet(root/'ten.parquet',index=False)
        pd.DataFrame([{'thscode':'600000.SH','ticker':'600000','ex_date_ms':sync.midnight_ms('2020-01-02'),'dividend_per_share':.1,'per_share_bonus':0.0,'allotment_ratio':0.0,'allotment_price':0.0,'currency':'CNY'}]).to_parquet(root/'actions.parquet',index=False)

    def request(self,path,params=None):
        params=params or {};self.calls.append((path,dict(params)))
        if self.fail==path:raise ValueError('controlled transport failure')
        if path.endswith('/trading-days'):items=[{'date_ms':sync.midnight_ms(d),'date':d.replace('-','')} for d in self.official_calendar]
        elif path.endswith('/tickers/list'):items=self.master
        elif path.endswith('/historical'):
            items=[{'date_ms':sync.midnight_ms(d),'open_price':3000.0,'high_price':3020.0,'low_price':2980.0,'close_price':3010.0,'volume':100000.0,'turnover':10000000.0} for d in self.calendar if params['start']<=sync.midnight_ms(d)<=params['end']]
        elif path.endswith('/ths-index-list'):items=[{'thscode':code,'name':name} for code,name in MEMBERS.values()]
        elif path.endswith('/ths-stock-list'):items=[{'thscode':'600000.SH','name':'测试甲'}]
        elif path.endswith('/limit-up-ladder'):items=[{'date':d,'boards':{}} for d in self.calendar[-30:]]
        elif path.endswith('/snapshot'):items=[{'thscode':code,'name':code,'last_price':3000.0} for code in params['thscodes'].split(',')]
        elif path.endswith('/abnormal-monitor-list'):items=[{'thscode':'600000.SH','monitor_label':'监控标签样本','start_date_ms':sync.midnight_ms('2026-10-19'),'end_date_ms':sync.midnight_ms('2026-10-21')}]
        else:return {'code':0,'data':{'item':[],'pagination':{'total':0,'pages':1}}}
        return {'code':0,'data':{'item':items,'timestamp':sync.midnight_ms('2026-10-20')}}

    def download(self,kind,target,progress=None):
        self.downloads.append(kind)
        file={'daily-k':'full.parquet','daily-k-10d':'ten.parquet','adjustment-factors':'actions.parquet'}[kind]
        shutil.copyfile(self.root/file,target)
        if progress:progress(target.stat().st_size,target.stat().st_size)
        return {'bytes':target.stat().st_size,'host':'fixture.invalid'}


@pytest.fixture
def transport(tmp_path,monkeypatch):
    input_dir=tmp_path/'fixtures';input_dir.mkdir();adapter=FixtureTransport(input_dir)
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path/'runtime'))
    monkeypatch.delenv('SHIXU_SQLITE_PATH',raising=False);monkeypatch.delenv('SHIXU_PUBLISHED_DIR',raising=False)
    monkeypatch.setattr(sync.source_service,'client',lambda identifier:adapter)
    monkeypatch.setattr(sync,'now_china',lambda:datetime(2026,10,20,12,tzinfo=sync.CHINA))
    clear_market_cache()
    return adapter


def start_job(kind='initialise',target=None,window='near-three-years'):
    task=tasks.create_task(kind,{'source_id':'fuyao',**({'window':window} if window else {})},target)
    claimed=tasks.claim_next('fixture-worker');assert claimed['job_id']==task['job_id']
    return task['job_id']


@pytest.mark.parametrize('window,begin,years',[('near-five-years','2021-10-20',5),('near-ten-years','2016-10-20',10)])
def test_long_initialisation_uses_real_sized_fixture_calendar_and_preserves_window_on_increment(transport,monkeypatch,window,begin,years):
    replacement=FixtureTransport(transport.root,begin)
    transport.__dict__.update(replacement.__dict__)
    job=start_job(window=window)
    if window=='near-ten-years':
        # Frozen pre-change plans remain interpretable; new ten-year jobs are rejected.
        checkpoint=sync.Collector(job,None)
        wanted=sync.window_start('2026-10-19',window)
        checkpoint.state['plan']={'target':'2026-10-19','begin':wanted,'wanted':wanted,'window':window,'kind':'initialise','previous_generation':None,'previous_end':None,'provider':'fuyao'}
        sync.atomic_json(checkpoint.state_path,checkpoint.state)
    result=sync.execute_sync(job)
    status=get_market().status()
    assert status['data_begin']==begin and result['coverage']['initial_window']==window
    proof=result['coverage']['calendar_provenance']
    assert proof['years']==years and proof['official_history'] is False
    assert proof['indices']['000688.SH']['full_window']==(years==5)
    monkeypatch.setattr(sync,'now_china',lambda:datetime(2026,10,20,18,tzinfo=sync.CHINA))
    result=sync.execute_sync(start_job('increment'))
    assert result['coverage']['initial_window']==window
    assert get_market().status()['data_begin']==begin


def test_first_three_year_sync_and_before_close_does_not_publish_today(transport):
    job=start_job();result=sync.execute_sync(job)
    status=get_market().status()
    assert result['status']=='partial' and status['data_end']=='2026-10-19'
    assert status['rows']==(len(transport.calendar)-1)*2
    assert status['data_begin']=='2023-10-20'
    assert result['coverage']['initial_window']=='near-three-years'
    proof=result['coverage']['calendar_provenance']
    assert proof['official_history'] is False and proof['count']==len(transport.calendar)-1
    assert proof['official_recent']['count']==len(transport.official_calendar)-1
    assert len(get_market().indices())==4 and len(get_market().topics_data())==3
    assert tasks.task_view(job)['status']=='partial'
    assert tasks.task_view(job)['progress']==100
    report=get_market().chart('000001.SH')
    assert report['breadth_scope'] and report['bars'][-1]['advancers']==2
    assert report['bars'][0]['advancers'] is None
    assert len(get_market().reference()['abnormal'])==1
    assert get_market().reference()['abnormal_observed_date']=='2026-10-20'
    assert all(r['limit_up'] is False for r in get_market().panel())
    assert get_market().bars('600000.SH')[-1]['limit_up'] is False
    raw=Path(result['data_status']['path'])/'manifest.json'
    assert 'fixture.invalid' not in raw.read_text()


def test_same_day_is_no_change_and_new_day_merges_ten_day_increment(transport,monkeypatch):
    sync.execute_sync(start_job());first=get_market().status()['snapshot_id']
    monkeypatch.setattr(sync,'now_china',lambda:datetime(2026,10,20,18,tzinfo=sync.CHINA))
    repeat=start_job('increment',target='2026-10-19');result=sync.execute_sync(repeat)
    assert result['status']=='no_change' and get_market().status()['snapshot_id']==first
    assert tasks.task_view(repeat)['status']=='no_change'
    assert result['coverage']['derived_file_written'] is False
    repeated_root=sync.raw_dir()/'sync'/repeat
    assert not (repeated_root/'year.parquet').exists()
    assert not (repeated_root/'raw/fuyao_stock_master.json').exists()
    assert not (repeated_root/'raw/fuyao_calendar_year.json').exists()
    assert (repeated_root/'accepted/daily-k-10d.parquet').is_file()
    monkeypatch.setattr(sync,'now_china',lambda:datetime(2026,10,20,18,tzinfo=sync.CHINA))
    result=sync.execute_sync(start_job('increment'))
    assert get_market().status()['data_end']=='2026-10-20'
    assert get_market().status()['rows']==len(transport.calendar)*2
    assert result['coverage']['overlap_rows']>0 and result['coverage']['revised_overlap_rows']==0
    assert get_market().status()['data_begin']=='2023-10-20'
    assert result['coverage']['calendar_provenance']['recent_extension']['appended_dates']==['2026-10-20']
    assert 'daily-k-10d' in transport.downloads
    history=get_market().reference()['pool_history']
    assert {'2026-10-19','2026-10-20'}<=set(history)
    assert all(k in history['2026-10-19'] for k in ('limitup','limitdown','breakpool'))
    assert 'abnormal' not in history['2026-10-19']


def test_lost_more_than_ten_days_falls_back_to_full(transport,monkeypatch):
    monkeypatch.setattr(sync,'now_china',lambda:datetime(2026,9,15,18,tzinfo=sync.CHINA))
    sync.execute_sync(start_job(window='near-one-year'))
    monkeypatch.setattr(sync,'now_china',lambda:datetime(2026,10,20,18,tzinfo=sync.CHINA))
    result=sync.execute_sync(start_job('increment'))
    assert transport.downloads.count('daily-k')==2
    assert result['data_status']['data_end']=='2026-10-20'
    assert result['data_status']['data_begin']=='2025-10-20'
    assert 'calendar_provenance' not in result['coverage']
    assert result['coverage']['intermediate_event_gap_dates']
    assert result['coverage']['event_supplement_target_dates']==['2026-10-20']
    assert '2026-10-20' not in result['coverage']['intermediate_event_gap_dates']


def test_explicit_one_year_initial_remains_one_year(transport):
    result=sync.execute_sync(start_job(window='near-one-year'))
    assert result['data_status']['data_begin']=='2025-10-20'
    assert result['coverage']['initial_window']=='near-one-year'
    assert 'calendar_provenance' not in result['coverage']


def test_old_checkpoint_without_window_is_not_reinterpreted_as_three_years(transport):
    job=start_job(window='near-one-year')
    def pause(stage,pct):
        if stage=='下载daily-k':tasks.change_task(job,'pause')
    with pytest.raises(tasks.TaskPaused):sync.execute_sync(job,pause)
    checkpoint=sync.raw_dir()/'sync'/job/'checkpoint.json'
    state=json.loads(checkpoint.read_text())
    state['plan'].pop('window');state['plan'].pop('wanted')
    checkpoint.write_text(json.dumps(state))
    # Simulate an old task with no explicit window, including after a software upgrade.
    with connect() as c:c.execute('UPDATE job SET parameters_json=? WHERE job_id=?',(json.dumps({'source_id':'fuyao'}),job))
    tasks.change_task(job,'resume');tasks.claim_next('fixture-worker')
    result=sync.execute_sync(job)
    assert result['data_status']['data_begin']=='2025-10-20'
    assert result['coverage']['initial_window']=='near-one-year'
    assert 'calendar_provenance' not in result['coverage']


def test_three_year_resume_keeps_request_signatures_and_original_fetch_times(transport,monkeypatch):
    job=start_job();paused=False
    def pause(stage,pct):
        nonlocal paused
        if stage=='校验、合并历史行情与重叠修订' and not paused:
            paused=True;tasks.change_task(job,'pause')
    with pytest.raises(tasks.TaskPaused):sync.execute_sync(job,pause)
    checkpoint=sync.raw_dir()/'sync'/job/'checkpoint.json'
    before=json.loads(checkpoint.read_text())
    assert before['plan']['calendar_verified'] is True
    assert before['plan']['wanted']=='2023-10-19' and before['plan']['begin']=='2023-10-20'
    indices=[name for name in before['files'] if name.startswith('index-')]
    assert len(indices)==4
    calls=len([path for path,_ in transport.calls if path.endswith('/historical')])
    # A newer configured default must not reinterpret an already frozen job.
    from app.services import history_window
    monkeypatch.setattr(history_window,'DEFAULT_WINDOW','near-five-years')
    tasks.change_task(job,'resume');tasks.claim_next('fixture-worker')
    result=sync.execute_sync(job)
    after=json.loads(checkpoint.read_text())
    assert result['data_status']['data_begin']=='2023-10-20'
    assert result['coverage']['initial_window']=='near-three-years'
    assert len([path for path,_ in transport.calls if path.endswith('/historical')])==calls
    assert all(before['files'][name]==after['files'][name] for name in indices)


def test_missing_three_year_index_day_does_not_publish_one_year_fallback(transport,monkeypatch):
    original=transport.request
    def missing_date(path,params=None):
        payload=original(path,params)
        if path.endswith('/historical') and params['thscode']=='000688.SH':payload['data']['item'].pop(20)
        return payload
    monkeypatch.setattr(transport,'request',missing_date)
    with pytest.raises(ValueError,match='交易日期不一致'):sync.execute_sync(start_job())
    assert get_market().status()['ready'] is False


def test_duplicate_input_fails_without_publication(transport):
    con=duckdb.connect(':memory:')
    original=con.execute('SELECT * FROM read_parquet(?)',[str(transport.root/'full.parquet')]).fetchdf();con.close()
    pd.concat([original,original.head(1)],ignore_index=True).to_parquet(transport.root/'full.parquet',index=False)
    with pytest.raises(ValueError,match='重复键'):sync.execute_sync(start_job())
    assert get_market().status()['ready'] is False


def test_pause_resume_reuses_verified_request_batches(transport):
    job=start_job();paused=False
    def progress(stage,pct):
        nonlocal paused
        if stage=='下载daily-k' and not paused:
            paused=True;tasks.change_task(job,'pause')
    with pytest.raises(tasks.TaskPaused):sync.execute_sync(job,progress)
    before=len([x for x in transport.calls if x[0].endswith('/tickers/list')])
    assert before==1
    tasks.change_task(job,'resume');tasks.claim_next('fixture-worker')
    result=sync.execute_sync(job)
    assert result['status']=='partial'
    assert len([x for x in transport.calls if x[0].endswith('/tickers/list')])==1
    with connect() as c:
        batches=c.execute("SELECT batch_key,count(*) FROM sync_batch WHERE job_id=? AND status='accepted' GROUP BY batch_key",(job,)).fetchall()
    assert all(row[1]==1 for row in batches)


def test_cancel_during_catalog_registration_keeps_old_sqlite_generation(transport,monkeypatch):
    sync.execute_sync(start_job());old=get_market().status()['snapshot_id']
    monkeypatch.setattr(sync,'now_china',lambda:datetime(2026,10,20,18,tzinfo=sync.CHINA))
    job=start_job('increment')
    def cancel(stage,pct):
        if stage=='登记daily_raw':tasks.change_task(job,'cancel')
    with pytest.raises(tasks.TaskCancelled):sync.execute_sync(job,cancel)
    assert get_market().status()['snapshot_id']==old
    assert get_market().status()['data_end']=='2026-10-19'
    with connect() as c:assert c.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone()[0]=='2026-10-19'


def test_bundle_external_members_kind_and_unknown_are_not_empty_complete(transport,tmp_path,monkeypatch):
    # A real sync output supplies schemas; this fixture tests only bundle metadata semantics.
    # Keep this explicit offline import fixture; post-publication derivative
    # release is exercised separately, including original-response protection.
    from app.services import market_derived_cleanup
    monkeypatch.setattr(market_derived_cleanup,'release_after_publication',lambda *args,**kwargs:{'status':'fixture-retained'})
    sync.execute_sync(start_job())
    # Locate the immutable bundle from the completed manual job.
    folders=list((sync.raw_dir()/'sync').iterdir());folder=folders[0]
    bundle=json.loads((folder/'bundle.json').read_text())
    bundle['topic_files']=[{'file':'missing-members.json','code':'885999.TI','name':'未采集题材','kind':'concept','observed_date':'2026-10-19','complete':False}]
    (folder/'bundle.json').write_text(json.dumps(bundle))
    imported=MarketData(sync.data_dir()).import_audit(folder)
    topic=MarketData(sync.data_dir(),imported['snapshot_id']).topics_data()[0]
    assert topic['kind']=='concept' and topic['members_complete'] is False and topic['members_state']=='unknown'
    assert topic['instrument_ids']==[]


def test_pool_unknown_identity_is_quarantined_and_history_preserves_it(transport,monkeypatch):
    original=transport.request
    def unknown_identity(path,params=None):
        if path.endswith('/limit-up-pool') and params['date_ms']==sync.midnight_ms('2026-10-19'):
            return {'code':0,'data':{'item':[{'thscode':None,'name':'历史旧简称','last_price':12.0}],'pagination':{'total':1,'pages':1}}}
        return original(path,params)
    monkeypatch.setattr(transport,'request',unknown_identity)
    result=sync.execute_sync(start_job())
    reference=get_market().reference()
    assert result['status']=='partial'
    assert reference['limitup']==[] and len(reference['pool_quarantine']['limitup'])==1
    assert reference['pool_coverage']['limitup']['complete'] is False
    assert reference['pool_coverage']['limitup']['declared_total']==1
    assert reference['pool_coverage']['limitup']['quarantined_rows']==1
    assert all(r['limit_up'] is None for r in get_market().panel())
    assert get_market().bars('600000.SH')[-1]['limit_up'] is None
    monkeypatch.setattr(sync,'now_china',lambda:datetime(2026,10,20,18,tzinfo=sync.CHINA))
    sync.execute_sync(start_job('increment'))
    history=get_market().reference()['pool_history']['2026-10-19']
    assert history['coverage']['limitup']['complete'] is False
    assert len(history['quarantine']['limitup'])==1
    assert all(r['limit_up'] is None for r in get_market().panel('2026-10-19'))


def test_pool_verified_identity_wrong_trade_date_price_blocks_publication(transport,monkeypatch):
    original=transport.request
    def wrong_price(path,params=None):
        if path.endswith('/limit-up-pool'):
            return {'code':0,'data':{'item':[{'thscode':'600000.SH','last_price':999.0}],'pagination':{'total':1,'pages':1}}}
        return original(path,params)
    monkeypatch.setattr(transport,'request',wrong_price)
    with pytest.raises(ValueError,match='请求交易日收盘不符'):sync.execute_sync(start_job())
    assert get_market().status()['ready'] is False


def test_import_flushes_artifacts_before_rename_and_rejects_corrupt_reuse(transport,monkeypatch):
    from app.analytics import market as market_module
    from app.services import market_derived_cleanup
    monkeypatch.setattr(market_derived_cleanup,'release_after_publication',lambda *args,**kwargs:{'status':'fixture-retained'})
    events=[];original_file=market_module.fsync_file;original_directory=market_module.fsync_directory;original_replace=market_module.os.replace
    def file_sync(path):events.append(('file',Path(path)));return original_file(path)
    def directory_sync(path):events.append(('directory',Path(path)));return original_directory(path)
    def rename(source,target):
        if Path(target).name.startswith('market-'):events.append(('rename',Path(source)))
        return original_replace(source,target)
    monkeypatch.setattr(market_module,'fsync_file',file_sync)
    monkeypatch.setattr(market_module,'fsync_directory',directory_sync)
    monkeypatch.setattr(market_module.os,'replace',rename)
    sync.execute_sync(start_job())
    status=get_market().status();final=Path(status['path']);stage=final.parent/('.staging-'+final.name)
    rename_index=events.index(('rename',stage))
    assert {p.name for kind,p in events[:rename_index] if kind=='file' and p.parent==stage}=={'raw-daily.manifest.json','daily.manifest.json','reference.json','corporate-actions.parquet','manifest.json'}
    assert events[rename_index-1]==('directory',stage)
    assert events[rename_index+1]==('directory',final.parent)
    head_index=events.index(('file',final.parent/'market-head.json.tmp'))
    assert head_index>rename_index and events[head_index+2]==('directory',final.parent)
    bundle=next((sync.raw_dir()/'sync').iterdir())
    assert MarketData(sync.data_dir()).import_audit(bundle)['snapshot_id']==status['snapshot_id']
    original=(final/'reference.json').read_bytes()
    (final/'reference.json').write_bytes(original+b' ')
    with pytest.raises(ValueError,match='校验和不符'):MarketData(sync.data_dir()).import_audit(bundle)


def test_new_ten_year_worker_job_rejects_before_source_calls(transport):
    with pytest.raises(ValueError,match='5年'):
        sync.execute_sync(start_job(window='near-ten-years'))
    assert transport.calls==[] and transport.downloads==[]


def test_five_year_increment_keeps_old_indices_and_respects_rolling_source_boundary(transport,monkeypatch):
    replacement=FixtureTransport(transport.root,'2021-10-20')
    transport.__dict__.update(replacement.__dict__)
    original=transport.request
    def bounded_source(path,params=None):
        payload=original(path,params)
        if path.endswith('/historical'):
            earliest=sync.midnight_ms(sync.window_start(sync.now_china().date().isoformat(),'near-five-years'))
            if params['start']<earliest:return {'code':1003}
        return payload
    monkeypatch.setattr(transport,'request',bounded_source)
    initial=sync.execute_sync(start_job(window='near-five-years'))
    assert initial['coverage']['index_request_plan']==sync.INDEX_REQUEST_PLAN
    for path,params in transport.calls:
        if path.endswith('/historical'):
            assert params['start']==sync.midnight_ms('2021-10-20')
    before=get_market().reference()['indices']
    assert all(rows[0]['date']=='2021-10-20' for rows in before.values())
    calls=len(transport.calls)
    monkeypatch.setattr(sync,'now_china',lambda:datetime(2026,10,20,18,tzinfo=sync.CHINA))
    result=sync.execute_sync(start_job('increment'))
    assert result['data_status']['data_begin']=='2021-10-20'
    assert result['data_status']['data_end']=='2026-10-20'
    assert result['coverage']['index_request_plan']==sync.INDEX_REQUEST_PLAN
    after=get_market().reference()['indices']
    assert all(rows[0]['date']=='2021-10-20' and rows[-1]['date']=='2026-10-20' for rows in after.values())
    assert all(rows[0]==before[symbol][0] for symbol,rows in after.items())
    index_requests=[params for path,params in transport.calls[calls:] if path.endswith('/historical')]
    assert len(index_requests)==4
    assert all(params['start']==sync.midnight_ms('2025-10-20') for params in index_requests)


def test_five_year_initialisation_clamps_weekend_index_request_without_losing_trading_days(transport,monkeypatch):
    transport.__dict__.update(FixtureTransport(transport.root,'2021-10-18').__dict__)
    monkeypatch.setattr(sync,'now_china',lambda:datetime(2026,10,18,12,tzinfo=sync.CHINA))
    result=sync.execute_sync(start_job(window='near-five-years'))

    proof=result['coverage']['calendar_provenance']
    assert proof['wanted']=='2021-10-16'
    assert result['data_status']['data_begin']=='2021-10-18'
    assert result['data_status']['data_end']=='2026-10-16'
    index_requests=[params for path,params in transport.calls if path.endswith('/historical')]
    assert len(index_requests)==4
    assert all(params['start']==sync.midnight_ms('2021-10-18') for params in index_requests)
    assert all(index['full_window'] and index['requested_start']=='2021-10-18' for index in proof['indices'].values())


def test_five_year_initialisation_refuses_to_drop_required_early_trading_day(transport,monkeypatch):
    transport.__dict__.update(FixtureTransport(transport.root,'2021-10-15').__dict__)
    monkeypatch.setattr(sync,'now_china',lambda:datetime(2026,10,17,12,tzinfo=sync.CHINA))
    job=start_job(target='2026-10-15',window='near-five-years')

    with pytest.raises(ValueError,match='2021-10-17 晚于所需首个交易日 2021-10-15'):
        sync.execute_sync(job)

    assert not get_market().status()['ready']


@pytest.mark.parametrize('fault',['missing_retained','missing_old_day','missing_new_day','empty_new','wrong_basis','duplicate_old'])
def test_bounded_index_merge_rejects_unknown_or_incomplete_raw_history(fault):
    days=['2021-10-20','2025-10-20','2026-10-20']
    retained=[{'date':day,'open':10.,'high':11.,'low':9.,'close':10.,'volume':100.,'amount':1000.,'source':'fuyao','price_basis':'none'} for day in days[:-1]]
    raw=lambda day:{'date_ms':sync.midnight_ms(day),'open_price':10.,'high_price':11.,'low_price':9.,'close_price':10.,'volume':100.,'turnover':1000.}
    incoming={'data':{'thscode':'000001.SH','interval':'1d','item':[raw(day) for day in days[1:]]}}
    if fault=='missing_retained':retained=[]
    elif fault=='missing_old_day':retained.pop(0)
    elif fault=='missing_new_day':incoming['data']['item'].pop(0)
    elif fault=='empty_new':incoming['data']['item']=[]
    elif fault=='wrong_basis':retained[0]['price_basis']='forward'
    elif fault=='duplicate_old':retained.append(dict(retained[0]))
    with pytest.raises(ValueError):
        sync.merge_retained_index(incoming,'000001.SH',retained,days[0],days[-1],days,days[1])


def test_bounded_index_merge_accepts_real_suffix_revision_without_losing_early_history():
    days=['2021-10-20','2025-10-20','2026-10-20']
    retained=[{'date':day,'open':10.,'high':11.,'low':9.,'close':10.,'volume':100.,'amount':1000.,'source':'fuyao','price_basis':'none'} for day in days[:-1]]
    raw=lambda day:{'date_ms':sync.midnight_ms(day),'open_price':10.,'high_price':12.,'low_price':9.,'close_price':11.,'volume':100.,'turnover':1000.}
    incoming={'data':{'thscode':'000001.SH','interval':'1d','item':[raw(day) for day in days[1:]]}}
    result=sync.merge_retained_index(incoming,'000001.SH',retained,days[0],days[-1],days,days[1])
    assert len(result['data']['item'])==3
    assert result['data']['item'][0]['close_price']==10.
    assert result['data']['item'][1]['close_price']==11.
    assert result['lineage']['merge']=='incoming_daily_revision_wins'


@pytest.mark.parametrize('bounded',[False,True])
def test_older_five_year_checkpoint_preserves_frozen_index_request_signatures(transport,monkeypatch,bounded):
    replacement=FixtureTransport(transport.root,'2021-10-20')
    transport.__dict__.update(replacement.__dict__)
    job=start_job(window='near-five-years')
    def pause_before_indices(stage,pct):
        if stage=='下载daily-k':tasks.change_task(job,'pause')
    with pytest.raises(tasks.TaskPaused):sync.execute_sync(job,pause_before_indices)
    checkpoint=sync.raw_dir()/'sync'/job/'checkpoint.json'
    state=json.loads(checkpoint.read_text())
    # Emulate both older plan shapes. Neither may be clamped again on resume.
    if bounded:state['plan']['index_request_begin']=state['plan']['wanted']
    else:
        state['plan'].pop('index_request_plan');state['plan'].pop('index_request_begin')
    checkpoint.write_text(json.dumps(state))
    monkeypatch.setattr(sync,'now_china',lambda:datetime(2026,10,21,12,tzinfo=sync.CHINA))
    tasks.change_task(job,'resume');tasks.claim_next('fixture-worker')
    def pause_after_indices(stage,pct):
        if stage=='校验、合并历史行情与重叠修订':tasks.change_task(job,'pause')
    with pytest.raises(tasks.TaskPaused):sync.execute_sync(job,pause_after_indices)
    before=json.loads(checkpoint.read_text())
    requests=[params for path,params in transport.calls if path.endswith('/historical')]
    sse=next(params for params in requests if params['thscode']=='000001.SH')
    assert sse['start']==sync.midnight_ms('2021-10-19' if bounded else '2021-06-01')
    assert before['plan']['calendar_provenance']['version']==f'source-calendar-history-window-v{2 if bounded else 1}'
    calls=len(requests)
    tasks.change_task(job,'resume');tasks.claim_next('fixture-worker')
    result=sync.execute_sync(job)
    after=json.loads(checkpoint.read_text())
    assert len([path for path,_ in transport.calls if path.endswith('/historical')])==calls
    assert all(before['files'][name]==after['files'][name] for name in before['files'] if name.startswith('index-'))
    assert ('index_request_plan' in result['coverage'])==bounded
