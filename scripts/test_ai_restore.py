"""Backup copies retain conversations and charges while revoking AI egress."""
import json
from pathlib import Path
import pytest
from backup import connect_sqlite, perform_backup, sha
from restore import perform_restore, verify_backup
from test_deployment import source

ROOT=Path(__file__).resolve().parents[1]


def inventory(path):
    return {item.relative_to(path).as_posix():sha(item) for item in path.rglob('*') if item.is_file()}


def test_restore_revokes_ai_egress_without_erasing_sessions_or_charges(source,tmp_path):
    with connect_sqlite(source/'business/app.sqlite3') as db:
        db.executescript((ROOT/'backend/app/migrations/035_ai_sessions.sql').read_text())
        for provider in ('deepseek','openai'):
            db.execute("UPDATE ai_provider_config SET enabled=1,credential_ref=?,config_revision=7,verification_json=? WHERE provider_id=?",
                       ('ai-'+provider+'-fixture',json.dumps({'state':'passed','items':[{'capability':'structured_output','state':'passed'}],'checked_at_ms':1,'config_signature':'fixture'}),provider))
        db.execute("INSERT INTO ai_session VALUES('session','stock','deleted-parent-report','600001.SH','2026-10-30','deepseek','deepseek-flash','fixture-signature','fixture-context','{\"evidence\":[{\"text\":\"合成测试材料\"}]}','{}',1,1,2,9999999999999)")
        for identifier,state,sent,cost,cost_state in [('sent','running',10,None,'pending'),('unsent','queued',None,None,'not_sent'),('done','completed',10,123,'estimated')]:
            db.execute("""INSERT INTO ai_task(task_id,operation,session_id,provider_id,model,config_signature,credential_ref,
                request_key,request_digest,state,created_at_ms,request_json,day,reservation_nusd,sent_at_ms,cost_nusd,cost_state)
                VALUES(?, 'message','session','deepseek','deepseek-flash','fixture-signature','ai-deepseek-fixture',?, ?, ?,1,'{}','2026-10-30',999,?,?,?)""",
                (identifier,'key-'+identifier,'hash-'+identifier,state,sent,cost,cost_state))
        db.execute("INSERT INTO ai_message VALUES('message','session','done','assistant','合成测试回复','{}','completed',1)")
        original_session=tuple(db.execute('SELECT * FROM ai_session').fetchone())
        original_message=tuple(db.execute('SELECT * FROM ai_message').fetchone())
    bundle=tmp_path/'bundle';perform_backup(source,bundle)
    original=inventory(bundle);manifest=verify_backup(bundle)
    restored=tmp_path/'restored';report=perform_restore(bundle,restored)
    assert report['ai_disabled'] and report['ai_interrupted_tasks']==['sent'] and report['ai_cancelled_tasks']==['unsent']
    assert inventory(bundle)==original and verify_backup(bundle)==manifest
    assert not (restored/'credential_master_key').exists()
    with connect_sqlite(restored/'business/app.sqlite3','ro') as db:
        for provider in db.execute('SELECT * FROM ai_provider_config'):
            assert provider['enabled']==0 and provider['config_revision']==8
            verification=json.loads(provider['verification_json'])
            assert verification['state']=='stale' and verification['checked_at_ms']==1
        sent=db.execute("SELECT * FROM ai_task WHERE task_id='sent'").fetchone()
        assert sent['state']=='interrupted_manual_retry' and sent['reservation_nusd']==999 and sent['sent_at_ms']==10
        assert sent['cost_state']=='pending' and sent['credential_ref']=='' and sent['request_json']=='{}'
        unsent=db.execute("SELECT * FROM ai_task WHERE task_id='unsent'").fetchone()
        assert unsent['state']=='cancelled' and unsent['reservation_nusd']==0 and unsent['cost_state']=='not_sent'
        done=db.execute("SELECT * FROM ai_task WHERE task_id='done'").fetchone()
        assert done['state']=='completed' and done['cost_nusd']==123 and done['cost_state']=='estimated'
        assert tuple(db.execute('SELECT * FROM ai_session').fetchone())==original_session
        assert tuple(db.execute('SELECT * FROM ai_message').fetchone())==original_message
        assert db.execute("SELECT count(*) FROM ai_task WHERE state IN ('queued','running')").fetchone()[0]==0
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    with connect_sqlite(source/'business/app.sqlite3','ro') as db:
        assert all(row[0]==1 for row in db.execute('SELECT enabled FROM ai_provider_config'))
        assert db.execute("SELECT state FROM ai_task WHERE task_id='sent'").fetchone()[0]=='running'


def test_legacy_restore_does_not_create_ai_tables(source,tmp_path):
    bundle=tmp_path/'bundle';perform_backup(source,bundle)
    report=perform_restore(bundle,tmp_path/'restored')
    assert not report['ai_disabled'] and not report['ai_interrupted_tasks'] and not report['ai_cancelled_tasks']
    with connect_sqlite(tmp_path/'restored/business/app.sqlite3','ro') as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_provider_config'").fetchone() is None


def test_agent_restore_archives_received_text_and_keeps_child_charge(source,tmp_path):
    with connect_sqlite(source/'business/app.sqlite3') as db:
        db.executescript((ROOT/'backend/app/migrations/035_ai_sessions.sql').read_text())
        db.executescript((ROOT/'backend/app/migrations/036_ai_stream_events.sql').read_text())
        db.execute("INSERT INTO ai_session VALUES('agent-session','market',NULL,NULL,'2026-10-30','openai','gpt-6.1-sol','signature','context','{}','{}',1,1,2,9999999999999)")
        db.execute("""INSERT INTO ai_task(task_id,operation,session_id,provider_id,model,config_signature,credential_ref,
            request_key,request_digest,state,created_at_ms,request_json,day,reservation_nusd,sent_at_ms,cost_state,result_json)
            VALUES('agent-task','message','agent-session','openai','gpt-6.1-sol','signature','cipher','request','hash','running',1,?,
                   '2026-11-02',123456,10,'pending',?)""",
            (json.dumps({'page_anchor':{'as_of':'2026-10-30'},'agent_state':{'tool_results':[{'state':'available','date':'2026-11-02'}]}}),json.dumps({'agent':True,'stream_last_seq':1})))
        db.execute("INSERT INTO ai_request_receipt VALUES('child','agent-task',1,'2026-11-02','openai','gpt-6.1-sol','sent',123456,NULL,'pending','{}','{}',1,10,NULL,NULL)")
        db.execute("INSERT INTO ai_task_event VALUES('agent-task',1,'delta',?,1)",(json.dumps({'text':'恢复前已接收的真实片段'}),))
    bundle=tmp_path/'agent-bundle';perform_backup(source,bundle)
    original=inventory(bundle);target=tmp_path/'agent-restored';report=perform_restore(bundle,target)
    assert report['ai_interrupted_tasks']==['agent-task'] and inventory(bundle)==original
    with connect_sqlite(target/'business/app.sqlite3','ro') as db:
        receipt=db.execute('SELECT * FROM ai_request_receipt').fetchone()
        assert receipt['state']=='interrupted' and receipt['cost_state']=='pending' and receipt['reservation_nusd']==123456
        assert db.execute('SELECT count(*) FROM ai_task_event').fetchone()[0]==0
        message=db.execute("SELECT * FROM ai_message WHERE role='assistant'").fetchone()
        assert message['text']=='恢复前已接收的真实片段' and message['state']=='interrupted_manual_retry'
        saved=json.loads(message['interpretation_json'])
        assert saved['source_context']['tool_results'][0]['date']=='2026-11-02'
        task=db.execute('SELECT * FROM ai_task').fetchone()
        assert task['request_json']=='{}' and task['cost_state']=='pending' and task['reservation_nusd']==123456
        assert json.loads(task['result_json'])['stream_last_seq']==1
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
