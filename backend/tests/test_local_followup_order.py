"""Local checks get FIFO positions before long sector work; no network/data reads."""
import json

import pytest

from app import core,worker
from app.services import task_service as tasks,line_alerts,listing_status,workspace_automation,sync_service
from test_platform import isolated


def parent(target='2026-10-22'):
    item=tasks.create_task('increment',{'source_id':'fuyao','data_chain_version':'sector-charts-increment-v1',
        'line_alert_check_version':line_alerts.VERSION,'listing_status_check_version':listing_status.VERSION},target)
    return tasks.claim_next('fixture-worker')


def hooks(monkeypatch,order):
    def queue(operation):
        def create(job,*args):
            order.append(operation)
            return tasks.create_task('source_check',{'operation':operation},'2026-10-22',request_key=operation+job['job_id'])
        return create
    monkeypatch.setattr(line_alerts,'after_sync',queue('line_alert_check'))
    monkeypatch.setattr(listing_status,'after_sync',queue('listing_status_check'))
    monkeypatch.setattr(worker,'_queue_followup',queue('sector_charts'))
    monkeypatch.setattr(workspace_automation,'run_after_sync',lambda job:order.append('workspace'))


@pytest.mark.parametrize('no_change',[False,True])
def test_new_sync_local_fifo_precedes_heavy_chain_and_keeps_frozen_date(isolated,monkeypatch,no_change):
    job=parent(target=None);order=[];hooks(monkeypatch,order)
    monkeypatch.setattr(sync_service,'execute_sync',lambda *a:{'business_date':'2026-10-22','no_change':no_change})
    monkeypatch.setattr(core,'get_market',lambda:(_ for _ in ()).throw(AssertionError('no global date')))
    worker.dispatch(job)
    assert order==['line_alert_check','listing_status_check','workspace','sector_charts']
    with core.connect() as c:
        frozen=c.execute("SELECT details_json FROM job_event WHERE job_id=? AND stage='chain_target_frozen'",(job['job_id'],)).fetchone()
        timing=c.execute("SELECT details_json FROM job_event WHERE job_id=? AND stage='workspace_followup_timing'",(job['job_id'],)).fetchone()
    assert json.loads(frozen[0])=={'target_date':'2026-10-22'}
    assert json.loads(timing[0])['elapsed_ms']>=0
    seen=[]
    for _ in range(3):
        child=tasks.claim_next('fixture-worker');seen.append(json.loads(child['parameters_json'])['operation']);tasks.finish(child['job_id'],'succeeded')
    assert seen==['line_alert_check','listing_status_check','sector_charts']


def test_terminal_dispatch_repairs_light_children_before_heavy_without_rerun(isolated,monkeypatch):
    job=parent();tasks.finish(job['job_id'],'succeeded');order=[];hooks(monkeypatch,order)
    monkeypatch.setattr(sync_service,'execute_sync',lambda *a:pytest.fail('terminal parent must not rerun'))
    worker.dispatch(job);worker.dispatch(job)
    assert order==['line_alert_check','listing_status_check','sector_charts']*2
    assert len(tasks.list_tasks()['items'])==4


def test_local_queue_failure_does_not_suppress_other_check_or_chain(isolated,monkeypatch):
    job=parent();order=[];hooks(monkeypatch,order)
    monkeypatch.setattr(sync_service,'execute_sync',lambda *a:{'business_date':'2026-10-22'})
    monkeypatch.setattr(line_alerts,'after_sync',lambda job:(_ for _ in ()).throw(ValueError('fixture queue failure')))
    worker.dispatch(job)
    assert order==['listing_status_check','workspace','sector_charts']
    assert any(e['stage']=='local_check_queue_error:line_alerts' for e in tasks.task_view(job['job_id'])['logs'])


def test_legacy_or_stopped_parent_does_not_gain_local_checks(isolated,monkeypatch):
    order=[];hooks(monkeypatch,order)
    legacy=tasks.create_task('increment',{'source_id':'fuyao'},'2026-10-22')
    old=tasks.claim_next('fixture-worker');tasks.finish(old['job_id'],'succeeded')
    worker._queue_local_sync_checks(old)
    assert order==[]
    job=parent();tasks.change_task(job['job_id'],'pause')
    with pytest.raises(tasks.TaskPaused):worker.dispatch(job)
    assert order==[] and tasks.task_view(job['job_id'])['status']=='paused'


def test_existing_ordinary_work_still_keeps_fifo_priority(isolated,monkeypatch):
    job=parent();order=[];hooks(monkeypatch,order)
    ordinary=tasks.create_task('source_check',{'operation':'ordinary'},'2026-10-22')
    monkeypatch.setattr(sync_service,'execute_sync',lambda *a:{'business_date':'2026-10-22'})
    worker.dispatch(job)
    assert tasks.claim_next('fixture-worker')['job_id']==ordinary['job_id']


@pytest.mark.parametrize('broken',[False,True])
def test_restart_recovers_local_checks_before_sector_child_creation(isolated,monkeypatch,broken):
    order=[]
    monkeypatch.setattr(worker,'ensure_ready',lambda:None)
    monkeypatch.setattr(worker,'set_setting',lambda *a:None)
    monkeypatch.setattr(worker.signal,'signal',lambda *a:None)
    from app.services import research_service
    monkeypatch.setattr(research_service,'cleanup_expired',lambda **kw:None)
    def recover_lines():
        order.append('lines')
        if broken:raise ValueError('fixture broken recovery read')
    monkeypatch.setattr(line_alerts,'recover_sync_followups',recover_lines)
    monkeypatch.setattr(listing_status,'recover_sync_followups',lambda:order.append('listing'))
    monkeypatch.setattr(worker,'recover_followups',lambda:order.append('sector'))
    worker.run(once=True)
    assert order==['lines','listing','sector']


@pytest.mark.parametrize('service,flag',[(line_alerts,'line_alert_check_version'),(listing_status,'listing_status_check_version')])
def test_one_bad_parent_does_not_hide_other_local_followups(isolated,monkeypatch,service,flag):
    parents=[]
    for day in ('2026-10-21','2026-10-22'):
        child=tasks.create_task('increment',{flag:service.VERSION},day)
        claimed=tasks.claim_next('fixture-worker');tasks.finish(claimed['job_id'],'succeeded');parents.append(child['job_id'])
    calls=[]
    def check(job):
        calls.append(job['job_id'])
        if job['job_id']==parents[0]:raise ValueError('fixture corrupt prior job')
    monkeypatch.setattr(service,'after_sync',check)
    service.recover_sync_followups()
    assert calls==parents


def test_no_change_crash_before_date_freeze_still_queues_light_before_heavy(isolated,monkeypatch):
    job=parent(target=None);worker._authorize_chain(job);tasks.finish(job['job_id'],'no_change')
    checkpoint=core.raw_dir()/'sync'/job['job_id']/'checkpoint.json'
    checkpoint.parent.mkdir(parents=True);core.atomic_json(checkpoint,{'plan':{'kind':'increment','target':'2026-10-22'}})
    order=[]
    def queue(name):
        def create(*a,**kw):
            order.append(name)
            return tasks.create_task('source_check',{'operation':name},'2026-10-22',request_key=name+job['job_id'])
        return create
    monkeypatch.setattr(line_alerts,'after_sync',queue('line_alert_check'))
    monkeypatch.setattr(listing_status,'after_sync',queue('listing_status_check'))
    monkeypatch.setattr(core,'get_market',lambda:object())
    from app.services import sector_chart_batch_jobs
    monkeypatch.setattr(sector_chart_batch_jobs,'create_batch_job',queue('sector_charts'))
    assert worker.recover_followups()[0]['target_date']=='2026-10-22'
    assert order==['line_alert_check','listing_status_check','sector_charts']
