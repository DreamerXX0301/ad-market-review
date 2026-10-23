"""Explicit stock lines: causal local receipts, no networking or live data."""
from copy import deepcopy
import json

import pytest
from app import core, worker
from app.services import line_alerts as alerts, chart_annotations as marks, task_service as tasks
from app.routers.chart_annotations import router as chart_router
from test_workspace import ws, advance
from test_watchlist import personal


@pytest.fixture
def lines(personal,monkeypatch):
    market,client=personal
    client.app.include_router(chart_router)
    monkeypatch.setattr(marks,'get_market',lambda:market)
    monkeypatch.setattr(alerts,'get_market',lambda:market)
    monkeypatch.setattr(market,'status',lambda:{'ready':True,'snapshot_id':'raw','data_end':market.entry},raising=False)
    return market,client


def quote(market,index,open=100,high=110,low=90,close=105,symbol='600001.SH'):
    market.data[symbol][index].update(open=open,high=high,low=low,close=close,volume=100,corporate_action=False)


def create(client,price=100,kind='support',modes=None,auto=True,**extra):
    response=client.post('/api/v1/instruments/600001.SH/annotations',json={'price':price,'line_type':kind,
        'alert_modes':modes if modes is not None else ['support_reclaim'],'auto_watchlist':auto,**extra})
    assert response.status_code==200,response.text
    return response.json()


def drain():
    for _ in range(100):
        job=tasks.claim_next('fixture-lines')
        if job is None:return
        worker.dispatch(job)
    pytest.fail('unexpected unbounded queue')


def result(client,**params):return client.get('/api/v1/watchlist/line-alerts',params=params).json()


def test_non_watchlist_line_triggers_atomically_then_repeat_never_readds(lines):
    market,client=lines;quote(market,69,close=105);quote(market,70,low=100,close=108)
    line=create(client);drain()
    event=result(client)['items'][0]
    assert event['state']=='triggered' and event['rule']=='support_reclaim'
    assert event['added_to_watchlist'] is True and event['watchlist_add_state']=='added'
    assert event['annotation_revision']==1 and event['market_snapshot_id']=='raw' and event['input_snapshots']['daily_raw']=='raw'
    own=client.get('/api/v1/watchlist').json()['items'][0]
    assert own['watchlist_entry_price']==108 and own['added_business_date']==market.entry
    assert own['event_close'] is None and own['tracking_round_id'] is None
    assert own['latest_line_alert']['event_id']==event['event_id']
    client.delete('/api/v1/watchlist/600001.SH')
    job=client.post('/api/v1/watchlist/line-alerts/evaluate',json={'request_key':'repeat'}).json();drain()
    assert client.get('/api/v1/watchlist/ids').json()['total']==0
    assert result(client)['total']==1
    assert client.get('/api/v1/instruments/600001.SH/annotations').json()['items'][0]['alert_modes']==['support_reclaim']
    assert client.post('/api/v1/watchlist/line-alerts/evaluate',json={'request_key':'repeat'}).json()['job_id']==job['job_id']


def test_support_breakdown_is_risk_only_and_never_auto_collects(lines):
    market,client=lines;quote(market,69,close=105);quote(market,70,open=101,low=95,close=99)
    create(client,modes=['support_breakdown','support_reclaim']);drain()
    event=result(client)['items'][0]
    assert result(client)['total']==1 and event['state']=='invalidated'
    assert event['watchlist_add_state']=='not_eligible' and event['added_to_watchlist'] is False
    assert '不视为回踩收回' in event['reason']
    assert client.get('/api/v1/watchlist/ids').json()['total']==0


def test_pressure_equality_touch_and_strict_cross_are_separate(lines):
    market,client=lines;quote(market,69,close=100);quote(market,70,low=100,close=100)
    create(client,kind='resistance',modes=['pressure_touch','pressure_close_breakout']);drain()
    assert [row['rule'] for row in result(client)['items']]==['pressure_touch']
    advance(market,71);quote(market,71,low=99,close=106)
    client.post('/api/v1/watchlist/line-alerts/evaluate',json={});drain()
    assert any(row['rule']=='pressure_close_breakout' and row['trade_date']==market.days[71] for row in result(client)['items'])
    advance(market,72);quote(market,72,low=101,close=108)
    client.post('/api/v1/watchlist/line-alerts/evaluate',json={});drain()
    assert not any(row['rule']=='pressure_close_breakout' and row['trade_date']==market.days[72] for row in result(client)['items'])


def test_missing_strict_previous_quote_unknown_but_touch_still_works(lines):
    market,client=lines;quote(market,70,low=100,close=105)
    market.data['600001.SH']=[row for row in market.data['600001.SH'] if row['date']!=market.days[69]]
    create(client,kind='resistance',modes=['pressure_touch','pressure_close_breakout']);drain()
    view=result(client)
    assert len(view['items'])==1 and view['items'][0]['rule']=='pressure_touch'
    assert view['latest_check']['unknown_count']==1 and '前一市场交易日' in view['unknowns'][0]['reason']


def test_raw_action_day_cannot_invent_cross_but_touch_compares_current_prices(lines,monkeypatch):
    market,client=lines;quote(market,69,close=105);quote(market,70,low=95,close=99)
    monkeypatch.setattr(market,'actions_for_instruments',lambda ids:{s:[{'date':market.entry,'dividend_per_share':1,'per_share_bonus':0,'allotment_ratio':0,'allotment_price':0}] for s in ids})
    create(client,modes=['support_breakdown','support_reclaim']);drain()
    assert result(client)['total']==0 and result(client)['latest_check']['unknown_count']==2


def test_fixed_anchor_line_and_previous_are_converted_with_action_evidence(lines,monkeypatch):
    market,client=lines;quote(market,69,open=100,high=110,low=100,close=105);quote(market,70,open=51,high=54,low=50,close=53)
    monkeypatch.setattr(market,'actions_for_instruments',lambda ids:{s:[{'date':market.entry,'dividend_per_share':0,'per_share_bonus':1,'allotment_ratio':0,'allotment_price':0}] for s in ids})
    create(client,price=100,price_basis='forward_event_affine',anchor_date=market.days[69]);drain()
    event=result(client)['items'][0]
    assert event['line_price']==100 and event['comparison_line_price']==50 and event['previous_close']==52.5
    assert event['comparison_evidence']['model']=='event_affine'
    assert event['close']==53 and event['added_to_watchlist'] is True


def test_missing_action_evidence_is_unknown_for_adjusted_anchor(lines,monkeypatch):
    market,client=lines;quote(market,69,close=105);quote(market,70,low=100,close=108)
    monkeypatch.setattr(market,'actions_for_instruments',lambda ids:None)
    create(client,price_basis='forward_event_affine',anchor_date=market.days[69]);drain()
    assert result(client)['total']==0 and result(client)['unknowns']


def test_material_revisions_queue_but_style_changes_do_not_rearm(lines):
    _,client=lines;line=create(client,auto=False)
    path='/api/v1/instruments/600001.SH/annotations/'+line['annotation_id']
    with core.connect() as c:before=c.execute('SELECT count(*) FROM job').fetchone()[0]
    styled=client.patch(path,json={'label':'支撑','color':'amber'}).json()
    assert styled['revision']==1
    with core.connect() as c:assert c.execute('SELECT count(*) FROM job').fetchone()[0]==before
    changed=client.patch(path,json={'price':101}).json()
    assert changed['revision']==2
    client.patch(path,json={'alert_modes':[]});drain()
    assert result(client)['total']==0 and result(client)['rules_total']==0


def test_deleted_or_changed_rule_cannot_submit_from_earlier_queued_copy(lines):
    _,client=lines;line=create(client)
    client.delete('/api/v1/instruments/600001.SH/annotations/'+line['annotation_id']);drain()
    assert result(client)['total']==0 and client.get('/api/v1/watchlist/ids').json()['total']==0


def test_offline_sessions_are_backfilled_but_added_date_is_actual_today(lines):
    market,client=lines;quote(market,69,close=90);quote(market,70,open=90,high=95,low=85,close=90)
    create(client,kind='resistance',modes=['pressure_close_breakout']);drain()
    quote(market,71,open=95,high=115,low=95,close=110);quote(market,72,open=110,high=120,low=105,close=112)
    advance(market,72)
    client.post('/api/v1/watchlist/line-alerts/evaluate',json={});drain()
    event=result(client)['items'][0]
    assert event['trade_date']==market.days[71] and event['watchlist_added_business_date']==market.days[72]
    own=client.get('/api/v1/watchlist').json()['items'][0]
    assert own['added_business_date']==market.days[72] and own['watchlist_entry_price']==112
    assert result(client,as_of=market.days[70])['total']==0


def test_event_capacity_failure_still_leaves_visible_evidence(lines,monkeypatch):
    market,client=lines;quote(market,69,close=105);quote(market,70,low=100,close=108)
    from app.services import watchlist
    monkeypatch.setattr(watchlist,'MAX_ITEMS',0)
    create(client);drain();event=result(client)['items'][0]
    assert event['watchlist_add_state']=='capacity_reached' and '容量已满' in event['reason']


def test_existing_personal_entry_is_never_replaced(lines):
    market,client=lines
    client.post('/api/v1/watchlist',json={'instrument_id':'600001.SH'})
    with core.connect() as c:old=dict(c.execute('SELECT * FROM watchlist_item').fetchone())
    quote(market,69,close=105);quote(market,70,low=100,close=108)
    create(client);drain()
    assert result(client)['items'][0]['watchlist_add_state']=='already_present'
    with core.connect() as c:assert dict(c.execute('SELECT * FROM watchlist_item').fetchone())==old


def test_reads_do_not_read_quotes_or_queue_jobs(lines,monkeypatch):
    market,client=lines;create(client,auto=False);drain()
    with core.connect() as c:before=c.execute('SELECT count(*) FROM job').fetchone()[0]
    monkeypatch.setattr(market,'history_for_instruments',lambda *a,**k:pytest.fail('GET cannot scan quotes'))
    client.get('/api/v1/watchlist/line-alerts');client.get('/api/v1/instruments/600001.SH/annotations')
    with core.connect() as c:assert c.execute('SELECT count(*) FROM job').fetchone()[0]==before


@pytest.mark.parametrize('payload',[{'line_type':'bad'},{'alert_modes':['pressure_touch']},{'auto_watchlist':1},{'alert_modes':['support_reclaim','support_reclaim']}])
def test_invalid_rule_config_rejected(lines,payload):
    _,client=lines
    body={'price':100,'line_type':'support','alert_modes':['support_reclaim'],'auto_watchlist':False,**payload}
    assert client.post('/api/v1/instruments/600001.SH/annotations',json=body).status_code==422


class SimulatedCrash(BaseException):pass


def test_event_and_auto_add_rollback_together_and_resume_once(lines,monkeypatch):
    market,client=lines;quote(market,69,close=105);quote(market,70,low=100,close=108)
    create(client);job=tasks.claim_next('same-worker');original=alerts._auto_add
    def crash(*args,**kwargs):
        original(*args,**kwargs)
        raise SimulatedCrash()
    with monkeypatch.context() as scoped:
        scoped.setattr(alerts,'_auto_add',crash)
        with pytest.raises(SimulatedCrash):worker.dispatch(job)
    with core.connect() as c:
        assert c.execute('SELECT count(*) FROM watchlist_item').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM line_alert_event').fetchone()[0]==0
        c.execute('UPDATE job SET lease_expires_at_ms=? WHERE job_id=?',(core.now_ms()-1,job['job_id']))
    newer=tasks.claim_next('same-worker')
    assert job['lease_owner']!=newer['lease_owner']
    with pytest.raises(tasks.TaskCancelled):worker.dispatch(job)
    worker.dispatch(newer)
    assert result(client)['total']==1 and client.get('/api/v1/watchlist/ids').json()['total']==1


def test_receipted_event_does_not_readd_after_crash_and_personal_removal(lines,monkeypatch):
    market,client=lines;quote(market,69,close=105);quote(market,70,low=100,close=108)
    create(client);job=tasks.claim_next('fixture');original=tasks.finish
    def crash(identifier,*args,**kwargs):
        if identifier==job['job_id']:raise SimulatedCrash()
        return original(identifier,*args,**kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(tasks,'finish',crash)
        with pytest.raises(SimulatedCrash):worker.dispatch(job)
    client.delete('/api/v1/watchlist/600001.SH')
    with core.connect() as c:c.execute('UPDATE job SET lease_expires_at_ms=? WHERE job_id=?',(core.now_ms()-1,job['job_id']))
    worker.dispatch(tasks.claim_next('fixture'))
    assert result(client)['total']==1 and client.get('/api/v1/watchlist/ids').json()['total']==0


def test_pause_resume_and_rule_change_during_quote_read_fence_old_attempt(lines,monkeypatch):
    market,client=lines;quote(market,69,close=105);quote(market,70,low=100,close=108)
    line=create(client);job=tasks.claim_next('fixture');original=market.history_for_instruments
    changed=False
    def change(*args,**kwargs):
        nonlocal changed
        if not changed:
            changed=True
            client.patch('/api/v1/instruments/600001.SH/annotations/'+line['annotation_id'],json={'alert_modes':[]})
        return original(*args,**kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(market,'history_for_instruments',change)
        worker.dispatch(job)
    assert tasks.task_view(job['job_id'])['status']=='no_change'
    assert result(client)['total']==0 and client.get('/api/v1/watchlist/ids').json()['total']==0


def test_save_and_enqueue_are_one_transaction(lines,monkeypatch):
    _,client=lines
    def reject(*args,**kwargs):raise ValueError('fixture queue unavailable')
    with monkeypatch.context() as scoped:
        scoped.setattr(alerts,'enqueue',reject)
        with pytest.raises(ValueError):marks.create_annotation('600001.SH',{'price':100,'line_type':'support','alert_modes':['support_reclaim']})
    assert client.get('/api/v1/instruments/600001.SH/annotations').json()['total']==0


def test_no_history_before_new_rule_and_initial_pending_check_catches_offline_days(lines):
    market,client=lines;quote(market,69,close=105);quote(market,70,low=100,close=108)
    line=create(client);advance(market,72)
    quote(market,71,low=100,close=108);quote(market,72,low=100,close=108)
    # Original queued target is superseded, a new check includes all days
    # since activation, not only the latest day and never dates before it.
    client.post('/api/v1/watchlist/line-alerts/evaluate',json={});drain()
    dates={event['trade_date'] for event in result(client)['items']}
    assert dates==set(market.days[70:73])
    assert min(dates)==line['effective_business_date']


def test_new_sync_followup_is_idempotent_and_old_completed_jobs_are_not_backfilled(lines):
    market,client=lines;create(client,auto=False);drain()
    old=tasks.create_task('increment',{'source_id':'fuyao'},target_date=market.entry,request_key='old-sync')
    tasks.finish(old['job_id'])
    with core.connect() as c:before=c.execute('SELECT count(*) FROM job').fetchone()[0]
    alerts.recover_sync_followups()
    with core.connect() as c:assert c.execute('SELECT count(*) FROM job').fetchone()[0]==before
    fresh=tasks.create_task('increment',{'source_id':'fuyao','line_alert_check_version':alerts.VERSION},target_date=market.entry,request_key='new-sync')
    tasks.finish(fresh['job_id'])
    alerts.recover_sync_followups();alerts.recover_sync_followups()
    with core.connect() as c:
        children=c.execute("SELECT count(*) FROM job WHERE request_key=?",('line-alert-after-sync:'+fresh['job_id'],)).fetchone()[0]
        assert children==1
    drain()


def test_latest_stock_summaries_are_independent_of_public_list_limit(lines):
    market,client=lines;quote(market,69,close=105);quote(market,70,low=100,close=108)
    create(client,modes=['support_reclaim']);drain()
    create(client,price=101,modes=['support_reclaim']);drain()
    assert result(client,limit=1)['total']==2 and len(result(client,limit=1)['items'])==1
    with core.connect() as c:summary=alerts.latest_by_stock(c,['600001.SH','000001.SZ'],market.entry)
    assert summary['600001.SH']['event_id']==result(client,limit=1)['items'][0]['event_id']


@pytest.mark.parametrize('payload',[None,[],False,0,''])
def test_service_does_not_turn_nonobjects_into_check_requests(lines,payload):
    from fastapi import HTTPException
    with pytest.raises(HTTPException):alerts.create_check(payload)


def test_latest_check_marks_rule_and_business_date_changes_stale(lines):
    market,client=lines;line=create(client,auto=False);drain()
    assert result(client)['latest_check']['is_current'] is True
    client.patch('/api/v1/instruments/600001.SH/annotations/'+line['annotation_id'],json={'label':'只改外观'})
    assert result(client)['latest_check']['is_current'] is True
    client.patch('/api/v1/instruments/600001.SH/annotations/'+line['annotation_id'],json={'price':101})
    assert result(client)['latest_check']['stale'] is True and '规则' in result(client)['latest_check']['stale_reason']
    drain();advance(market,71)
    assert result(client)['latest_check']['stale'] is True and '行情日' in result(client)['latest_check']['stale_reason']


def test_unknown_old_date_is_rechecked_after_quote_evidence_becomes_available(lines,monkeypatch):
    market,client=lines;quote(market,69,close=105);quote(market,70,low=100,close=108)
    saved=deepcopy(market.data['600001.SH'][69]);market.data['600001.SH']=[row for row in market.data['600001.SH'] if row['date']!=market.days[69]]
    create(client);drain();assert result(client)['latest_check']['unknown_count']==1
    market.data['600001.SH'].append(saved);market.data['600001.SH'].sort(key=lambda row:row['date'])
    published=core.published_dir()/'fixture-corrected.json';core.atomic_json(published,{'fixture':True})
    publisher=tasks.create_task('source_check',{'fixture':'corrected-input'})
    core.publish_snapshot(module='daily_raw',job_id=publisher['job_id'],start=market.days[0],end=market.entry,lineage={'market_snapshot_id':'raw-correction'},artifacts=[{'object_key':'fixture-corrected.json','sha256':core.hash_file(published),'byte_count':published.stat().st_size,'row_count':2}])
    tasks.finish(publisher['job_id'])
    monkeypatch.setattr(market,'status',lambda:{'ready':True,'snapshot_id':'raw-correction','data_end':market.entry})
    assert result(client)['latest_check']['stale'] is True
    client.post('/api/v1/watchlist/line-alerts/evaluate',json={});drain()
    assert result(client)['total']==1 and result(client)['latest_check']['unknown_count']==0


def test_015_upgrades_legacy_marks_without_arming_or_rewriting_them():
    import sqlite3
    from pathlib import Path
    folder=Path(__file__).resolve().parents[1]/'app/migrations'
    connection=sqlite3.connect(':memory:');connection.row_factory=sqlite3.Row
    for path in sorted(folder.glob('[0-9]*.sql')):
        if path.name.startswith('015_'):break
        connection.executescript(path.read_text())
    connection.execute("INSERT INTO chart_annotation VALUES('legacy','600001.SH',100,'支撑(旧名称)','amber','none',NULL,1,1)")
    connection.commit();connection.executescript((folder/'015_line_alerts.sql').read_text())
    row=dict(connection.execute('SELECT * FROM chart_annotation').fetchone())
    assert row['price']==100 and row['label']=='支撑(旧名称)'
    assert row['line_type']=='reference' and row['alert_modes_json']=='[]' and row['auto_watchlist']==0
    assert row['revision']==1 and row['effective_business_date'] is None
    assert connection.execute('SELECT count(*) FROM line_alert_event').fetchone()[0]==0
    assert connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert not connection.execute('PRAGMA foreign_key_check').fetchall()
    connection.close()


def test_pause_after_committed_evaluations_resumes_without_recollecting(lines,monkeypatch):
    market,client=lines;quote(market,69,close=105);quote(market,70,low=100,close=108)
    create(client);job=tasks.claim_next('fixture');original=tasks.finish
    def pause_before_finish(identifier,*args,**kwargs):
        if identifier==job['job_id']:tasks.change_task(identifier,'pause')
        return original(identifier,*args,**kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(tasks,'finish',pause_before_finish)
        worker.dispatch(job)
    assert tasks.task_view(job['job_id'])['status']=='paused'
    assert result(client)['latest_check']['is_current'] is False
    client.delete('/api/v1/watchlist/600001.SH')
    tasks.change_task(job['job_id'],'resume');newer=tasks.claim_next('fixture')
    assert newer['lease_owner']!=job['lease_owner']
    worker.dispatch(newer)
    assert result(client)['total']==1 and client.get('/api/v1/watchlist/ids').json()['total']==0


def test_long_offline_range_uses_bounded_windows_and_strict_pair_semantics(lines,monkeypatch):
    market,client=lines;create(client,auto=False);drain();advance(market,120)
    original=market.history_for_instruments;reads=[]
    def read(symbols,start=None,end=None):
        reads.append((symbols,start,end));return original(symbols,start=start,end=end)
    monkeypatch.setattr(market,'history_for_instruments',read)
    client.post('/api/v1/watchlist/line-alerts/evaluate',json={});drain()
    assert len(reads)<=4  # one actual-add-date quote + three 20-session windows
    assert max(sum(start<=day<=end for day in market.days) for _,start,end in reads)<=alerts.READ_SESSION_WINDOW+1
    assert result(client)['latest_check']['checked_rules']==51
