"""Administrative rebuilding retains exact versioned configuration in an empty root."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('strategy_reinitialization', ROOT / 'scripts/reinitialize_preserving_strategies.py')
rebuild = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rebuild)


@pytest.fixture
def stored(tmp_path, monkeypatch):
    from app import core
    for key in ('SHIXU_SQLITE_PATH','SHIXU_PUBLISHED_DIR','SHIXU_RAW_DIR','SHIXU_WORK_DIR','SHIXU_CREDENTIAL_DIR'):
        monkeypatch.delenv(key, raising=False)
    root=tmp_path/'live'
    monkeypatch.setenv('SHIXU_DATA_DIR', str(root))
    monkeypatch.setenv('SHIXU_CREDENTIAL_MASTER_KEY_FILE', str(tmp_path/'master'))
    from cryptography.fernet import Fernet
    (tmp_path/'master').write_bytes(Fernet.generate_key())
    core.ensure_ready()
    from app.services import task_service as tasks
    task=tasks.create_task('initialise', {'window':'near-three-years'})
    file=core.published_dir()/'calendar-fixture.json'; file.write_text('{}')
    snapshot=core.publish_snapshot(module='calendar',job_id=task['job_id'],start='2026-10-20',end='2026-10-20',artifacts=[{'object_key':file.name,'role':'calendar','sha256':core.hash_file(file),'byte_count':file.stat().st_size,'row_count':1}],quality='partial')
    with core.connect() as c:
        c.execute('INSERT INTO business_clock VALUES(1,?,?,?)',('2026-10-20',snapshot,core.now_ms()))
    tasks.finish(task['job_id'],'succeeded')
    from app.services.strategies import create_strategy, update_strategy
    original=create_strategy({'name':'用户自定义策略','definition':{'field':'volume_ratio','op':'>=','value':3},'trade_rules':{'entry':None,'exit':{'field':'macd_dead_cross','op':'==','value':True}},'policy':{'duration_sessions':50}})
    update_strategy(original['strategy_id'], {'definition':{'all':[{'field':'volume_ratio','op':'>=','value':4},{'field':'topic_ids','op':'contains','value':'manual-keep'}]},'policy':{'duration_sessions':40}})
    from app.infrastructure.credentials import CredentialStore
    CredentialStore().save('fuyao','isolated-rebuild-fixture')
    with core.connect() as c:
        c.execute("INSERT INTO platform_setting VALUES('active_source','\"fuyao\"',1)")
        c.execute("INSERT INTO platform_setting VALUES('worker_heartbeat','{}',1)")
        trigger=c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='topic_revision_current_day'").fetchone()[0]
        c.execute('DROP TRIGGER topic_revision_current_day')
        c.execute("INSERT INTO topic(topic_id,origin,created_at_ms) VALUES('manual-keep','manual',1)")
        c.execute("INSERT INTO topic_revision VALUES('manual-keep',1,'自定义题材','原说明','[]',0,'2026-10-20',1)")
        c.execute(trigger)
    return root, original['strategy_id']


def test_prepare_activate_preserves_versions_rules_and_credentials_without_old_results(stored, tmp_path):
    root, sid=stored
    before=rebuild.connect_sqlite(root/'business/app.sqlite3','ro')
    try: fingerprint=rebuild.configuration_hash(rebuild.configuration(before))
    finally: before.close()
    original_cipher=(root/'credentials/fuyao.enc').read_bytes()
    prepared=tmp_path/'prepared'; backup=tmp_path/'backup'; old=tmp_path/'recoverable'
    receipt=rebuild.prepare(root,prepared,backup)
    assert receipt['configuration_sha256']==fingerprint
    assert receipt['retained_counts']['strategy']==1
    assert receipt['retained_counts']['strategy_version']==2
    assert receipt['retained_counts']['tracking_policy_version']==2
    assert receipt['retained_counts']['topic']==1
    assert not receipt['master_key_copied']
    assert (root/'business/app.sqlite3').exists()  # Preparing does not clear live data.
    final=rebuild.activate(prepared,old)
    assert final['activation_complete'] and (old/'business/app.sqlite3').exists()
    assert (root/'credentials/fuyao.enc').read_bytes()==original_cipher
    assert not (root/'credential_master_key').exists()
    c=rebuild.connect_sqlite(root/'business/app.sqlite3','ro')
    try:
        assert rebuild.configuration_hash(rebuild.configuration(c))==fingerprint
        head=c.execute('SELECT * FROM strategy WHERE strategy_id=?',(sid,)).fetchone()
        assert head['current_version']==2 and head['current_policy_version']==2
        assert c.execute('SELECT trade_rules_json FROM strategy_version WHERE strategy_id=? AND version=1',(sid,)).fetchone()[0]
        assert all(c.execute('SELECT count(*) FROM '+name).fetchone()[0]==0 for name in rebuild.EMPTY_TABLES)
        assert c.execute("SELECT count(*) FROM sqlite_master WHERE name='topic_revision_current_day'").fetchone()[0]==1
        assert c.execute("SELECT count(*) FROM platform_setting WHERE key='worker_heartbeat'").fetchone()[0]==0
    finally: c.close()


def test_activation_refuses_configuration_changed_since_preparation(stored, tmp_path):
    root, sid=stored
    prepared=tmp_path/'prepared'
    rebuild.prepare(root,prepared,tmp_path/'backup')
    c=rebuild.connect_sqlite(root/'business/app.sqlite3')
    try: c.execute('UPDATE strategy SET name=? WHERE strategy_id=?',('后来修改的策略',sid)); c.commit()
    finally: c.close()
    with pytest.raises(ValueError, match='changed since preparation'): rebuild.activate(prepared,tmp_path/'recoverable')
    assert root.is_dir() and prepared.is_dir()


def test_preparation_refuses_active_task_and_does_not_touch_credentials(stored, tmp_path):
    root,_=stored
    from app.services.task_service import create_task
    create_task('initialise', {'window':'near-three-years'})
    ciphertext=(root/'credentials/fuyao.enc').read_bytes()
    with pytest.raises(ValueError, match='active tasks'): rebuild.prepare(root,tmp_path/'prepared',tmp_path/'backup')
    assert (root/'credentials/fuyao.enc').read_bytes()==ciphertext
    assert not (tmp_path/'backup').exists()


def test_activation_rechecks_prepared_database_is_empty(stored,tmp_path):
    root,_=stored; prepared=tmp_path/'prepared'
    rebuild.prepare(root,prepared,tmp_path/'backup')
    c=rebuild.connect_sqlite(prepared/'business/app.sqlite3')
    try:
        c.execute("INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,parameters_hash,status,created_at_ms) VALUES('unexpected','unexpected','unexpected','initialise','{}',?,'queued',1)",('a'*64,)); c.commit()
    finally:c.close()
    with pytest.raises(ValueError,match='contains old market'):rebuild.activate(prepared,tmp_path/'recoverable')
    assert root.is_dir() and prepared.is_dir()


def test_paths_must_be_independent_and_master_key_stays_outside_data(stored,tmp_path,monkeypatch):
    root,_=stored
    with pytest.raises(ValueError,match='independent'):rebuild.prepare(root,tmp_path/'new',tmp_path/'new/backup')
    monkeypatch.setenv('SHIXU_CREDENTIAL_MASTER_KEY_FILE',str(root/'credentials/master'))
    with pytest.raises(ValueError,match='outside the data root'):rebuild.prepare(root,tmp_path/'prepared',tmp_path/'backup')


def test_provider_topic_manual_override_is_configuration_even_without_strategy_reference(stored,tmp_path):
    root,_=stored
    c=rebuild.connect_sqlite(root/'business/app.sqlite3')
    try:
        c.execute("INSERT INTO topic(topic_id,origin,connection_id,provider_topic_key,created_at_ms) VALUES('provider-user-edited','provider','fuyao','source-topic',1)")
        c.execute("INSERT INTO topic_revision VALUES('provider-user-edited',1,'手动维护的来源题材','原说明','[]',0,'2026-10-20',1)")
        c.execute("INSERT INTO topic_override(override_id,topic_id,instrument_id,action,effective_business_date,recorded_at_ms,reason) VALUES('manual-override','provider-user-edited','600001.SH','include','2026-10-20',1,'用户配置')")
        c.commit()
    finally:c.close()
    receipt=rebuild.prepare(root,tmp_path/'prepared',tmp_path/'backup')
    assert receipt['retained_counts']['topic']==2 and receipt['retained_counts']['topic_override']==1
