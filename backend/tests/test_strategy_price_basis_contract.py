"""The strategy price convention is versioned; old prices and runs stay frozen."""
import json
import shutil

import pytest

from app import core
from app.infrastructure import database
from app.services import strategies
from test_workspace import ws, make_strategy


@pytest.mark.parametrize('basis',[None,'qfq','forward_event_affine',False,{},[]])
def test_invalid_price_basis_is_rejected_without_creating_strategy(ws,basis):
    _,client=ws
    response=client.post('/api/v1/strategies',json={'name':'invalid','definition':{'field':'close','op':'>','value':0},'price_basis':basis})
    assert response.status_code==422
    assert client.get('/api/v1/strategies').json()['items']==[]


def test_legacy_default_and_forward_opt_in_make_distinct_immutable_versions(ws):
    _,client=ws
    original=make_strategy(client,{'field':'close','op':'>','value':0})
    assert original['price_basis']=='none'
    sid=original['strategy_id']
    with core.connect() as connection:
        before=dict(connection.execute('SELECT * FROM strategy_version WHERE strategy_id=? AND version=1',(sid,)).fetchone())
    updated=client.patch('/api/v1/strategies/'+sid,json={'price_basis':'forward'}).json()
    assert updated['version']==2 and updated['price_basis']=='forward'
    assert updated['price_basis_contract']=='strategy-local-forward-v1'
    with core.connect() as connection:
        after=dict(connection.execute('SELECT * FROM strategy_version WHERE strategy_id=? AND version=1',(sid,)).fetchone())
        current=dict(connection.execute('SELECT * FROM strategy_version WHERE strategy_id=? AND version=2',(sid,)).fetchone())
    assert before==after
    assert current['definition_json']==before['definition_json']
    assert current['definition_sha256']!=before['definition_sha256']
    assert current['evaluator_version']==strategies.FORWARD_ENGINE_VERSION
    renamed=client.patch('/api/v1/strategies/'+sid,json={'name':'仅改名'}).json()
    assert renamed['version']==2 and renamed['price_basis']=='forward'
    raw=client.patch('/api/v1/strategies/'+sid,json={'price_basis':'none'}).json()
    assert raw['version']==3 and raw['price_basis']=='none'


def test_old_run_reads_its_frozen_basis_after_current_strategy_switch(ws):
    _,client=ws
    original=make_strategy(client,{'field':'close','op':'>','value':0})
    sid=original['strategy_id']
    run=client.post('/api/v1/strategies/'+sid+'/runs',json={}).json()
    assert run['requested_price_basis']=='none'
    changed=client.patch('/api/v1/strategies/'+sid,json={'price_basis':'forward'})
    assert changed.status_code==200
    old=client.get('/api/v1/strategy-runs/'+run['run_id']).json()
    listing=client.get('/api/v1/strategy-runs',params={'strategy_id':sid}).json()
    assert old['requested_price_basis']=='none' and old['strategy_version']==1
    assert listing['items'][0]['requested_price_basis']=='none'
    assert old['items']==run['items']


def test_explicit_forward_default_templates_do_not_rewrite_existing_versions(ws):
    _,client=ws
    response=client.post('/api/v1/strategies/bootstrap',json={'price_basis':'forward'})
    assert response.status_code==200,response.text
    items=client.get('/api/v1/strategies').json()['items']
    assert len(items)==3 and all(item['price_basis']=='forward' for item in items)
    versions={item['strategy_id']:item['version'] for item in items}
    assert client.post('/api/v1/strategies/bootstrap',json={}).status_code==200
    assert {item['strategy_id']:item['version'] for item in client.get('/api/v1/strategies').json()['items']}==versions


def test_024_migrates_a_recovery_copy_without_rewriting_prior_strategy(tmp_path,monkeypatch):
    source=database.MIGRATIONS_DIR
    legacy=tmp_path/'legacy-migrations';legacy.mkdir()
    for file in source.glob('*.sql'):
        if int(file.name.split('_',1)[0])<24:shutil.copyfile(file,legacy/file.name)
    data=tmp_path/'restored-copy'
    monkeypatch.setenv('SHIXU_DATA_DIR',str(data))
    for key in ('SHIXU_SQLITE_PATH','SHIXU_PUBLISHED_DIR','SHIXU_RAW_DIR','SHIXU_WORK_DIR'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setattr(database,'MIGRATIONS_DIR',legacy)
    core.ensure_ready()
    with core.connect() as connection:
        connection.execute("INSERT INTO strategy(strategy_id,name,created_at_ms) VALUES('old','旧策略',1)")
        connection.execute("INSERT INTO strategy_version(strategy_id,version,period,definition_json,definition_sha256,evaluator_version,recorded_at_ms) VALUES('old',1,'D','{}',?,'old-engine',1)",('a'*64,))
        before=dict(connection.execute("SELECT * FROM strategy_version WHERE strategy_id='old'").fetchone())
        assert connection.execute('PRAGMA user_version').fetchone()[0]==23
    # Exercise exactly 023 -> 024; later independent migrations have their own
    # recovery-copy tests and must not change this migration's expected version.
    shutil.copyfile(source/'024_strategy_price_basis.sql',legacy/'024_strategy_price_basis.sql')
    core.ensure_ready()
    with core.connect() as connection:
        row=dict(connection.execute("SELECT * FROM strategy_version WHERE strategy_id='old'").fetchone())
        assert row.pop('price_basis')=='none'
        assert row==before
        assert connection.execute('PRAGMA user_version').fetchone()[0]==24
        assert connection.execute("SELECT count(*) FROM _schema_migration WHERE name='024_strategy_price_basis.sql'").fetchone()[0]==1
        assert connection.execute('PRAGMA foreign_key_check').fetchall()==[]
