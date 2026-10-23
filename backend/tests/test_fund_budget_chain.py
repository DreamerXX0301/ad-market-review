"""New request policy is frozen along new chains; old task retries stay exact."""
import json
from types import SimpleNamespace

import pytest

from app import core,worker
from app.adapters.public_fund_budget import FUND_REQUEST_BUDGET_VERSION as VERSION
from app.routers import platform
from app.services import task_service as tasks,sector_chart_batch_jobs,source_service,sync_clock
from test_platform import isolated


def finish(item):
    job=tasks.claim_next('fixture-worker');assert job['job_id']==item['job_id']
    worker._authorize_chain(job);tasks.finish(job['job_id'],'succeeded')
    return job


@pytest.mark.parametrize('new',[True,False])
def test_initial_topic_chart_supplement_keeps_parent_frozen_policy(isolated,monkeypatch,new):
    policy={'fund_request_budget_version':VERSION} if new else {}
    root=tasks.create_task('initialise',{'data_chain_version':'sector-charts-v1',**policy},'2026-10-22')
    finish(root)
    topic=worker.recover_followups()[0]
    assert topic['parameters'].get('fund_request_budget_version')==(VERSION if new else None)
    finish(topic)
    monkeypatch.setattr(core,'get_market',lambda:object())
    def batch(market,day,request_key,chain_parameters,**kw):
        return tasks.create_task('source_check',{'operation':'sector_charts',**chain_parameters},day,request_key)
    monkeypatch.setattr(sector_chart_batch_jobs,'create_batch_job',batch)
    chart=worker.recover_followups()[0]
    assert chart['parameters'].get('fund_request_budget_version')==(VERSION if new else None)
    finish(chart)
    supplement=worker.recover_followups()[0]
    assert supplement['parameters'].get('fund_request_budget_version')==(VERSION if new else None)
    assert supplement['parameters']['year_history'] is True
    assert worker.recover_followups()==[]


def test_new_increment_route_freezes_budget_but_retry_does_not_upgrade_legacy(isolated,monkeypatch):
    monkeypatch.setattr(sync_clock,'require_new_increment',lambda:None)
    monkeypatch.setattr(source_service,'client',lambda *a:SimpleNamespace(source_id='fuyao',key='fixture'))
    new=platform.sync({'mode':'increment','request_key':'new-budget'})
    assert new['parameters']['fund_request_budget_version']==VERSION
    tasks.change_task(new['job_id'],'cancel')
    old=tasks.create_task('increment',{'source_id':'fuyao'},'2026-10-21')
    tasks.change_task(old['job_id'],'cancel')
    retry=tasks.change_task(old['job_id'],'retry')
    assert retry['parameters']=={'source_id':'fuyao'}


def test_new_supplement_route_has_budget_offline_revalidation_does_not(isolated,monkeypatch):
    monkeypatch.setattr(core,'get_market',lambda:SimpleNamespace(status=lambda:{'ready':True,'data_end':'2026-10-22'}))
    online=platform.market_supplement({'year_history':False,'sector_history':False,'request_key':'fund-budget'})
    offline=platform.market_supplement({'revalidate_only':True,'request_key':'offline'})
    assert online['parameters']['fund_request_budget_version']==VERSION
    assert 'fund_request_budget_version' not in offline['parameters']


def test_unknown_frozen_budget_blocks_chain_without_creating_child(isolated):
    root=tasks.create_task('increment',{'fund_request_budget_version':'unsupported'},'2026-10-22')
    finish(root)
    assert worker.recover_followups()==[]
    assert len(tasks.list_tasks()['items'])==1
    assert tasks.task_view(root['job_id'])['logs'][-1]['stage']=='chain_blocked'
