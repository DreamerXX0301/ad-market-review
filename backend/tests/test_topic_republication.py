"""A topic-only generation must not upgrade unknown raw-data quality."""
import json
from datetime import datetime

import pytest
import pandas as pd

from test_sync import FixtureTransport,transport,start_job
from app.analytics.market import MarketData
from app.core import connect,get_market,work_dir
from app.services import sync_service,topic_collection,task_service as tasks
from app.services import market_service, market_supplement, sync_completion, topic_catalog_additions


def test_topic_republication_preserves_unknown_missing_count_and_calendar_lineage(transport,monkeypatch):
    for row in transport.master:row['list_date']=None
    sync_service.execute_sync(start_job())
    previous=get_market().status()
    previous_reference=get_market().reference()
    assert previous['unexplained_missing'] is None
    assert previous['coverage']['identity_unknown_count']==2
    assert previous['coverage']['confirmed_missing_lower_bound']==0
    monkeypatch.setattr(topic_collection,'client',lambda _:transport)
    task=tasks.create_task('topic_refresh',{'source_id':'fuyao'},target_date=previous['data_end'])
    claimed=tasks.claim_next('fixture-worker');assert claimed['job_id']==task['job_id']
    topic_collection.execute_topic_collection(task['job_id'])
    current=get_market().status()
    assert current['unexplained_missing'] is None
    assert current['coverage']['confirmed_missing_lower_bound']==0
    assert current['coverage']['identity_unknown_count']==2
    assert current['coverage']['calendar_provenance']==previous['coverage']['calendar_provenance']
    bundle=json.loads((work_dir()/task['job_id']/'bundle.json').read_text())
    assert bundle['unexplained_missing'] is None
    reference=get_market().reference()
    for key in ('abnormal','abnormal_observed_date','abnormal_fetched_at'):
        assert reference[key]==previous_reference[key]
    assert bundle['abnormal_observed_date']==previous_reference['abnormal_observed_date']
    assert bundle['abnormal_fetched_at']==previous_reference['abnormal_fetched_at']
    assert bundle['collected_at']!=bundle['abnormal_fetched_at']


@pytest.mark.parametrize('missing_fields',[
    ('abnormal_observed_date',),
    ('abnormal_fetched_at',),
    ('abnormal',),
])
def test_topic_republication_does_not_date_unknown_monitor_metadata(transport,monkeypatch,missing_fields):
    sync_service.execute_sync(start_job())
    previous=get_market().status()
    original_reference=MarketData.reference
    def inherited_reference(market):
        reference=original_reference(market)
        if market._fixed_snapshot==previous['snapshot_id']:
            for key in missing_fields:reference.pop(key,None)
        return reference
    monkeypatch.setattr(MarketData,'reference',inherited_reference)
    monkeypatch.setattr(topic_collection,'client',lambda _:transport)
    before_calls=len(transport.calls)
    task=tasks.create_task('topic_refresh',{'source_id':'fuyao'},target_date=previous['data_end'])
    assert tasks.claim_next('fixture-worker')['job_id']==task['job_id']
    topic_collection.execute_topic_collection(task['job_id'])
    bundle=json.loads((work_dir()/task['job_id']/'bundle.json').read_text())
    current=get_market().reference()
    for key in ('abnormal_observed_date','abnormal_fetched_at'):
        expected=None if key in missing_fields or 'abnormal' in missing_fields else original_reference(
            MarketData(get_market().data_dir,snapshot_id=previous['snapshot_id']))[key]
        assert bundle[key]==current[key]==expected
    assert not any(path.endswith('/abnormal-monitor-list') for path,_ in transport.calls[before_calls:])


def test_new_five_year_initialisation_collects_all_current_monitor_pages_before_topic_chain(transport,monkeypatch):
    replacement=FixtureTransport(transport.root,'2021-10-20')
    transport.__dict__.update(replacement.__dict__)
    original_request=transport.request
    def request(path,params=None):
        if not path.endswith('/abnormal-monitor-list'):return original_request(path,params)
        transport.calls.append((path,dict(params)))
        records=[{'thscode':'600000.SH','monitor_label':f'合成监控标签{index}'} for index in range(31)]
        begin=(params['page']-1)*params['size']
        return {'code':0,'data':{'item':records[begin:begin+params['size']],'pagination':{'total':31}}}
    monkeypatch.setattr(transport,'request',request)
    task=tasks.create_task('initialise',{'source_id':'fuyao','window':'near-five-years','data_chain_version':'sector-charts-v1'})
    assert tasks.claim_next('fixture-worker')['job_id']==task['job_id']
    sync_service.execute_sync(task['job_id'])
    requests=[params for path,params in transport.calls if path.endswith('/abnormal-monitor-list')]
    assert requests==[{'state':'current','page':1,'size':30},{'state':'current','page':2,'size':30}]
    reference=get_market().reference()
    assert len(reference['abnormal'])==31
    assert reference['abnormal'][-1]['monitor_label']=='合成监控标签30'
    assert reference['abnormal_observed_date']=='2026-10-20'
    assert reference['abnormal_fetched_at'].startswith('2026-10-20T')
    before_calls=len(transport.calls)
    monkeypatch.setattr(topic_collection,'client',lambda _:transport)
    refresh=tasks.create_task('topic_refresh',{'source_id':'fuyao'},target_date=get_market().status()['data_end'])
    assert tasks.claim_next('fixture-worker')['job_id']==refresh['job_id']
    topic_collection.execute_topic_collection(refresh['job_id'])
    republished=get_market().reference()
    for key in ('abnormal','abnormal_observed_date','abnormal_fetched_at'):
        assert republished[key]==reference[key]
    assert not any(path.endswith('/abnormal-monitor-list') for path,_ in transport.calls[before_calls:])


@pytest.mark.parametrize('collected_day',[23,24])
def test_first_initialisation_separates_friday_quotes_from_monitor_observation_and_topic_copy(transport,monkeypatch,collected_day):
    # 实际执行首次同步与题材重发；合成来源只在临时测试目录使用。
    replacement=FixtureTransport(transport.root,'2021-10-20')
    transport.__dict__.update(replacement.__dict__)
    extra_days=['2026-10-21','2026-10-22','2026-10-23']
    transport.calendar.extend(extra_days)
    transport.official_calendar.extend(extra_days)
    frame=pd.read_parquet(transport.root/'full.parquet')
    tail=frame[frame.date_ms==frame.date_ms.max()]
    additions=[]
    for day in extra_days:
        copy=tail.copy();copy['date_ms']=sync_service.midnight_ms(day);additions.append(copy)
    pd.concat([frame,*additions],ignore_index=True).to_parquet(transport.root/'full.parquet',index=False)
    monkeypatch.setattr(sync_service,'now_china',lambda:datetime(2026,10,collected_day,18,tzinfo=sync_service.CHINA))
    task=tasks.create_task('initialise',{'source_id':'fuyao','window':'near-five-years','data_chain_version':'sector-charts-v1'})
    assert tasks.claim_next('fixture-worker')['job_id']==task['job_id']
    sync_service.execute_sync(task['job_id'])
    current=get_market();reference=current.reference()
    observed=f'2026-10-{collected_day}'
    assert current.status()['data_end']=='2026-10-23'
    assert reference['abnormal_observed_date']==observed
    historical=market_service.market_page('abnormal','2026-10-23')
    latest=market_service.market_page('abnormal','2026-10-23',monitor_scope='latest')
    assert historical['data_coverage']['selected_date_available']==(collected_day==23)
    assert bool(historical['items'])==(collected_day==23)
    assert latest['items'] and latest['monitor_observed_date']==observed
    assert latest['quote_as_of']=='2026-10-23'
    assert latest['monitor_fetched_at'].startswith(observed+'T')
    monkeypatch.setattr(topic_collection,'client',lambda _:transport)
    before_calls=len(transport.calls)
    refresh=tasks.create_task('topic_refresh',{'source_id':'fuyao'},target_date='2026-10-23')
    assert tasks.claim_next('fixture-worker')['job_id']==refresh['job_id']
    topic_collection.execute_topic_collection(refresh['job_id'])
    for key in ('abnormal','abnormal_observed_date','abnormal_fetched_at'):
        assert get_market().reference()[key]==reference[key]
    after=market_service.market_page('abnormal','2026-10-23',monitor_scope='latest')
    assert after['monitor_observed_date']==observed and after['monitor_fetched_at']==latest['monitor_fetched_at']
    assert not any(path.endswith('/abnormal-monitor-list') for path,_ in transport.calls[before_calls:])


def test_topic_task_rejects_a_different_frozen_target_before_requesting_sources(transport,monkeypatch):
    sync_service.execute_sync(start_job())
    monkeypatch.setattr(topic_collection,'client',lambda _:(_ for _ in ()).throw(AssertionError('no source fetch before date validation')))
    task=tasks.create_task('topic_refresh',{'source_id':'fuyao'},target_date='2026-10-16')
    claimed=tasks.claim_next('fixture-worker');assert claimed['job_id']==task['job_id']
    with pytest.raises(ValueError,match='冻结日期'):topic_collection.execute_topic_collection(task['job_id'])


def test_catalog_additions_reuses_quotes_and_requests_only_unseen_members(transport, monkeypatch):
    sync_service.execute_sync(start_job())
    before = get_market()
    prior = before.status()
    members = before.topics_data()
    day = prior['data_end']
    supplement = tasks.create_task('source_check', {
        'operation': 'market_supplement', 'year_history': True, 'sector_history': True, 'recent_days': 1})
    tasks.finish(supplement['job_id'], 'succeeded')
    market_supplement.SupplementCache().publish({
        'version': market_supplement.VERSION, 'snapshot_id': prior['snapshot_id'],
        'target_date': day, 'job_id': supplement['job_id'], 'quality': 'complete',
        'public_pools': {day: {'date': day, 'complete': True, 'items': []}},
    })
    new_code = '885998.TI'
    original = transport.request

    def request(path, params):
        value = original(path, params)
        if path.endswith('/ths-index-list') and params['tag'] == 'cn_concept':
            value['data']['item'].append({'thscode': new_code, 'name': '合成新增概念'})
        return value

    monkeypatch.setattr(transport, 'request', request)
    monkeypatch.setattr(topic_catalog_additions, 'client', lambda _: transport)

    def run():
        start = len(transport.calls)
        task = tasks.create_task('topic_refresh', {'scope': topic_catalog_additions.SCOPE})
        job = tasks.claim_next('fixture-worker')
        assert job['job_id'] == task['job_id']
        result = topic_catalog_additions.execute(job, lambda *args: None)
        return result, transport.calls[start:]

    result, calls = run()
    assert result['status'] == 'succeeded'
    assert [path.rsplit('/', 1)[-1] for path, _ in calls] == ['ths-index-list', 'ths-index-list', 'ths-stock-list']
    assert calls[-1][1] == {'thscode': new_code}
    current = get_market()
    assert current.topics_data()[:-1] == members
    assert current.topics_data()[-1]['topic_code'] == new_code
    assert current.topics_data()[-1]['instrument_ids'] == ['600000.SH']
    price_hashes = lambda head: {f['role']: f['sha256'] for f in head['files']
                                 if f['role'].startswith(('raw-daily', 'daily', 'corporate-actions'))}
    assert price_hashes(current.status()) == price_hashes(prior)
    for key in ('limitup', 'limitdown', 'breakpool', 'pool_history', 'abnormal', 'indices', 'coverage'):
        assert current.reference()[key] == before.reference()[key]
    current_id = current.status()['snapshot_id']
    assert market_supplement.load_supplement(current_id)['public_pools'][day]['complete']
    assert sync_completion.read_view(current_id, day)['supplement']['completed']
    with connect() as connection:
        assert connection.execute('SELECT 1 FROM topic WHERE provider_topic_key=?', ('fuyao-' + new_code,)).fetchone()
    second, calls = run()
    assert second['status'] == 'no_change'
    assert [path.rsplit('/', 1)[-1] for path, _ in calls] == ['ths-index-list', 'ths-index-list']
    assert get_market().status()['snapshot_id'] == current.status()['snapshot_id']


def test_catalog_additions_retry_only_unaccepted_members(transport, monkeypatch):
    from app.adapters.providers import ProviderError
    sync_service.execute_sync(start_job())
    before = get_market().topics_data()
    original = transport.request
    accepted, failed = '885997.TI', '885998.TI'
    fail = True

    def request(path, params):
        value = original(path, params)
        if path.endswith('/ths-index-list') and params['tag'] == 'cn_concept':
            value['data']['item'] += [{'thscode': code, 'name': code} for code in (accepted, failed)]
        if path.endswith('/ths-stock-list') and params['thscode'] == failed and fail:
            raise ProviderError('合成成员来源暂不可用', 503)
        return value

    monkeypatch.setattr(transport, 'request', request)
    monkeypatch.setattr(topic_catalog_additions, 'client', lambda _: transport)

    def run():
        start = len(transport.calls)
        task = tasks.create_task('topic_refresh', {'scope': topic_catalog_additions.SCOPE})
        job = tasks.claim_next('fixture-worker')
        assert job['job_id'] == task['job_id']
        result = topic_catalog_additions.execute(job, lambda *args: None)
        requested = [params['thscode'] for path, params in transport.calls[start:] if path.endswith('/ths-stock-list')]
        return result['status'], requested

    assert run() == ('partial', [accepted, failed])
    assert get_market().topics_data()[:-1] == before
    assert get_market().topics_data()[-1]['topic_code'] == accepted
    fail = False
    assert run() == ('succeeded', [failed])
    assert get_market().topics_data()[-1]['topic_code'] == failed
