"""A topic-only generation must not upgrade unknown raw-data quality."""
import json

import pytest

from test_sync import transport,start_job
from app.core import get_market,work_dir
from app.services import sync_service,topic_collection,task_service as tasks


def test_topic_republication_preserves_unknown_missing_count_and_calendar_lineage(transport,monkeypatch):
    for row in transport.master:row['list_date']=None
    sync_service.execute_sync(start_job())
    previous=get_market().status()
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


def test_topic_task_rejects_a_different_frozen_target_before_requesting_sources(transport,monkeypatch):
    sync_service.execute_sync(start_job())
    monkeypatch.setattr(topic_collection,'client',lambda _:(_ for _ in ()).throw(AssertionError('no source fetch before date validation')))
    task=tasks.create_task('topic_refresh',{'source_id':'fuyao'},target_date='2026-10-16')
    claimed=tasks.claim_next('fixture-worker');assert claimed['job_id']==task['job_id']
    with pytest.raises(ValueError,match='冻结日期'):topic_collection.execute_topic_collection(task['job_id'])
