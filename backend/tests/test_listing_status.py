"""Listing status keeps source dates, user statements and quote evidence distinct."""
import json
from pathlib import Path
import sqlite3

from fastapi import HTTPException
import pandas as pd
import pytest

from test_workspace import ws
from app import core, worker
from app.routers.platform import router
from app.services import listing_status as listing, task_service as tasks


@pytest.fixture
def identities(ws,monkeypatch):
    market,client=ws;client.app.include_router(router)
    master={symbol:{'instrument_id':symbol,'kind':'stock','name':symbol,'list_date':market.days[0],'end_date':None} for symbol in market.data}
    for code in range(3,7):
        symbol=f'60000{code}.SH';master[symbol]={'instrument_id':symbol,'kind':'stock','name':'未核验'+symbol,'list_date':None,'end_date':None}
    master['600007.SH']={'instrument_id':'600007.SH','kind':'stock','name':'来源未来日期','list_date':market.days[71],'end_date':None}
    master['000001.SH']={'instrument_id':'000001.SH','kind':'index','name':'指数'}
    reference={'master':master,'calendar':market.days}
    monkeypatch.setattr(market,'reference',lambda:reference,raising=False)
    status={'ready':True,'snapshot_id':'raw','data_begin':market.days[0],'data_end':market.entry}
    monkeypatch.setattr(market,'status',lambda:status,raising=False)
    monkeypatch.setattr(listing,'get_market',lambda:market)
    directory=core.published_dir()/'raw';directory.mkdir(parents=True,exist_ok=True)
    bars=[{'instrument_id':symbol,**bar} for symbol,history in market.data.items() for bar in history if bar['date']<=market.entry]
    pd.DataFrame(bars).to_parquet(directory/'daily.parquet')
    pd.DataFrame([{'thscode':row['instrument_id'],'date_ms':1} for row in bars]).to_parquet(directory/'raw-daily.parquet')
    return market,client,reference,status


def confirmation(client,**changes):
    return client.post('/api/v1/data/listing-status/confirm',json={
        'instrument_ids':['600003.SH','600004.SH','600005.SH','600006.SH'],'status':'pending',
        'reason':'用户明确确认尚未上市','evidence':'用户说明；并非官方上市公告','request_key':'fixture-confirm',**changes})


def execute():
    job=tasks.claim_next('listing-fixture-worker');assert job
    worker.dispatch(job)
    return tasks.task_view(job['job_id'])


def advance_generation(reference,status,day,new_id='next-raw'):
    now=core.now_ms();h='a'*64
    with core.connect() as db:
        db.execute('''INSERT INTO dataset_snapshot(snapshot_id,module,scope_key,job_id,data_begin,data_end,knowledge_cutoff_ms,price_basis,algorithm_version,lineage_json,manifest_path,manifest_sha256,state,quality,created_at_ms) VALUES(?,'daily_raw','all','fixture-job',?,?,?,'none','fixture','{}',?,?,'staged','ready',?)''',
                   (new_id,reference['calendar'][0],day,now,new_id+'/manifest.json',h,now))
        db.execute('INSERT INTO snapshot_artifact VALUES(?,?,?,?,?,?)',(new_id,new_id+'/daily.parquet','daily',h,100,100))
        db.execute("INSERT INTO snapshot_coverage(snapshot_id,feature,scope_json,coverage_state,observed_rows,continuous_begin,continuous_end) VALUES(?,'daily_raw','{}','ready',100,?,?)",(new_id,reference['calendar'][0],day))
        db.execute("UPDATE dataset_snapshot SET state='validated' WHERE snapshot_id=?",(new_id,))
        db.execute("UPDATE dataset_snapshot SET state='published',published_at_ms=? WHERE snapshot_id=?",(now,new_id))
        db.execute("UPDATE published_head SET snapshot_id=?,changed_at_ms=? WHERE module='daily_raw'",(new_id,now))
        db.execute('UPDATE business_clock SET business_date=? WHERE singleton=1',(day,))
    old=core.published_dir()/status['snapshot_id']/'daily.parquet'
    directory=core.published_dir()/new_id;directory.mkdir()
    (directory/'daily.parquet').write_bytes(old.read_bytes())
    (directory/'raw-daily.parquet').write_bytes((old.parent/'raw-daily.parquet').read_bytes())
    status.update(snapshot_id=new_id,data_end=day)


def test_four_explicit_pending_and_source_future_partition_catalog(identities):
    market,client,reference,_=identities
    result=confirmation(client);assert result.status_code==200,result.text
    body=result.json();summary=body['identities']
    assert summary['catalog_count']==7 and summary['listed_count']==summary['known_count']==2
    assert summary['pending_listing_count']==5 and summary['unknown_count']==0
    assert summary['indices_excluded']==1 and body['summary_is_current'] is True
    manual=[row for row in summary['pending_listing'] if row['evidence_kind']=='manual_user_confirmation']
    assert len(manual)==4 and all(row['source']=='用户确认（非官方来源）' and row['source_list_date'] is None for row in manual)
    assert all(row['confirmed_date']==market.entry and isinstance(row['confirmed_at_ms'],int) and row['reference']['quote_presence']['valid_quote_count']==0 for row in manual)
    source=next(row for row in summary['pending_listing'] if row['instrument_id']=='600007.SH')
    assert source['evidence_kind']=='source_list_date' and source['source_list_date']==market.days[71]
    assert all(reference['master'][row['instrument_id']]['list_date'] is None for row in manual)
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM listing_status_confirmation').fetchone()[0]==4
        saved=db.execute('SELECT * FROM listing_status_confirmation LIMIT 1').fetchone()
        assert saved['source_kind']=='manual_user_confirmation' and saved['market_snapshot_id']=='raw'
        assert json.loads(saved['quote_presence_json'])['valid_quote_count']==0


def test_missing_quote_alone_never_means_pending(identities):
    market,client,reference,_=identities
    result=listing.resolve(reference,market.entry,{symbol:{'valid_quote_count':0} for symbol in reference['master']})
    assert result['pending_listing_count']==1 and result['unknown_count']==4
    unverified=client.get('/api/v1/data/listing-status').json()
    assert unverified['summary_is_current'] is False
    assert unverified['identities']['pending_listing_count']==0 and unverified['identities']['unknown_count']==5


def test_quoted_stock_cannot_be_confirmed_pending_and_does_not_create_fake_date(identities):
    market,client,reference,_=identities
    reference['master']['600001.SH']['list_date']=None
    failed=confirmation(client,instrument_ids=['600001.SH'])
    assert failed.status_code==409 and '报价' in failed.json()['detail']
    proof=listing.quote_presence(market,['600001.SH'],market.entry)
    resolved=listing.resolve(reference,market.entry,proof)['items']['600001.SH']
    assert resolved['listing_status']=='quoted_unknown_date' and resolved['source_list_date'] is None
    with core.connect() as db:assert db.execute('SELECT count(*) FROM listing_status_confirmation').fetchone()[0]==0


def test_source_known_date_wins_manual_and_new_head_never_reuses_absence(identities):
    market,client,reference,status=identities
    assert confirmation(client).status_code==200
    reference['master']['600003.SH']['list_date']=market.days[71]
    advance_generation(reference,status,market.days[71])
    old=client.get('/api/v1/data/listing-status').json()
    assert old['summary_is_current'] is False
    assert old['identities']['listed_count']==4 and old['identities']['pending_listing_count']==0
    assert old['identities']['unknown_count']==3
    with core.connect() as db:
        db.execute('BEGIN IMMEDIATE');identifier=listing.enqueue(db,'new-head-check')
    assert execute()['status']=='succeeded'
    current=client.get('/api/v1/data/listing-status').json()
    assert current['summary_is_current'] is True and current['identities']['listed_count']==4
    assert current['identities']['pending_listing_count']==3 and current['identities']['unknown_count']==0
    assert next(row for row in current['confirmations'] if row['instrument_id']=='600003.SH')['status']=='pending','historical user audit is retained despite source override'


def test_new_quotes_revoke_manual_pending_without_inventing_listing_date(identities):
    market,client,reference,status=identities
    assert confirmation(client).status_code==200
    advance_generation(reference,status,market.days[71])
    path=core.published_dir()/status['snapshot_id']/'daily.parquet';bars=pd.read_parquet(path)
    added={**bars.iloc[0].to_dict(),'instrument_id':'600003.SH','date':market.days[71]}
    pd.concat([bars,pd.DataFrame([added])]).to_parquet(path)
    with core.connect() as db:listing.enqueue(db,'after-first-quote')
    execute()
    result=client.get('/api/v1/data/listing-status').json()['identities']
    row=next(row for row in result['unknown'] if row['instrument_id']=='600003.SH')
    assert row['listing_status']=='quoted_unknown_date' and row['source_list_date'] is None
    assert result['pending_listing_count']==3 and result['listed_count']==3


def test_pending_correction_audit_idempotency_and_request_collision(identities,monkeypatch):
    market,client,_,_=identities
    first=confirmation(client).json();revision=first['revision']
    original=listing.quote_presence
    monkeypatch.setattr(listing,'quote_presence',lambda *args:pytest.fail('idempotent replay must not scan quotes'))
    same=confirmation(client).json();assert same['already_exists'] and same['confirmation_ids']==first['confirmation_ids'] and same['revision']==revision
    changed=confirmation(client,reason='不同说明');assert changed.status_code==409
    monkeypatch.setattr(listing,'quote_presence',original)
    result=confirmation(client,status='unknown',request_key='correct-statement',instrument_ids=['600003.SH'])
    assert result.status_code==200,result.text
    assert result.json()['identities']['unknown_count']==1 and result.json()['revision']>revision
    history=client.get('/api/v1/data/listing-status/confirmations',params={'instrument_id':'600003.SH'}).json()
    assert history['total']==2 and [row['status'] for row in history['items']]==['unknown','pending']
    assert all(row['source_kind']=='manual_user_confirmation' and row['quote_presence']['valid_quote_count']==0 for row in history['items'])
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM listing_status_confirmation').fetchone()[0]==5
        with pytest.raises(sqlite3.IntegrityError):db.execute("UPDATE listing_status_confirmation SET status='unknown'")
        with pytest.raises(sqlite3.IntegrityError):db.execute('DELETE FROM listing_status_confirmation')


def test_confirmation_read_is_sqlite_only_and_never_queues(identities,monkeypatch):
    _,client,_,_=identities
    assert confirmation(client).status_code==200
    before=tasks.list_tasks()['total']
    monkeypatch.setattr(listing,'quote_presence',lambda *args:pytest.fail('GET cannot scan quotes'))
    monkeypatch.setattr(listing.duckdb,'connect',lambda *args:pytest.fail('GET cannot open DuckDB'))
    result=client.get('/api/v1/data/listing-status')
    assert result.status_code==200 and result.json()['identities']['pending_listing_count']==5
    assert tasks.list_tasks()['total']==before


@pytest.mark.parametrize('changes',[
    {'list_date':'2026-01-01'}, {'status':'listed'}, {'status':[]}, {'instrument_ids':['600003']},
    {'instrument_ids':['000001.SH']}, {'instrument_ids':[]}, {'reason':''}, {'evidence':'x'*4001},
    {'request_key':42}, {'request_key':''}, {'observed_business_date':'2026-02-30'},
])
def test_invalid_manual_inputs_cannot_write(identities,changes):
    _,client,_,_=identities
    assert confirmation(client,**changes).status_code==422
    with core.connect() as db:assert db.execute('SELECT count(*) FROM listing_status_confirmation').fetchone()[0]==0


def test_confirmation_rejects_clock_change_after_quote_read(identities,monkeypatch):
    market,client,_,_=identities;original=listing.quote_presence
    def changed(*args):
        result=original(*args)
        with core.connect() as db:db.execute('UPDATE business_clock SET business_date=?',(market.days[71],))
        return result
    monkeypatch.setattr(listing,'quote_presence',changed)
    result=confirmation(client);assert result.status_code==409
    with core.connect() as db:assert db.execute('SELECT count(*) FROM listing_status_confirmation').fetchone()[0]==0


def test_snapshot_summary_requires_current_revision_and_preserves_source_quality(identities):
    market,client,reference,_=identities
    assert confirmation(client).status_code==200
    with pytest.raises(HTTPException) as error:listing.snapshot_summary(reference,market.entry,{},'raw',expected_revision=0)
    assert error.value.status_code==409
    with core.connect() as db:assert db.execute("SELECT quality FROM dataset_snapshot WHERE snapshot_id='raw'").fetchone()[0]=='ready'


def test_same_market_generation_with_new_primary_head_requires_new_receipt(identities):
    market,client,reference,_=identities
    assert confirmation(client).status_code==200
    with core.connect() as db:
        now=core.now_ms();h='b'*64
        db.execute("INSERT INTO dataset_snapshot(snapshot_id,module,scope_key,job_id,data_begin,data_end,knowledge_cutoff_ms,price_basis,algorithm_version,lineage_json,manifest_path,manifest_sha256,state,quality,created_at_ms) VALUES('metadata-new','daily_raw','all','fixture-job',?,?,?,'none','fixture',?,'metadata/manifest.json',?,'staged','ready',?)",(market.days[0],market.entry,now,core.dumps({'market_snapshot_id':'raw'}),h,now))
        db.execute("INSERT INTO snapshot_artifact VALUES('metadata-new','metadata/daily.parquet','daily',?,100,100)",(h,))
        db.execute("INSERT INTO snapshot_coverage(snapshot_id,feature,scope_json,coverage_state,observed_rows,continuous_begin,continuous_end) VALUES('metadata-new','daily_raw','{}','ready',100,?,?)",(market.days[0],market.entry))
        db.execute("UPDATE dataset_snapshot SET state='validated' WHERE snapshot_id='metadata-new'")
        db.execute("UPDATE dataset_snapshot SET state='published',published_at_ms=? WHERE snapshot_id='metadata-new'",(now,))
        db.execute("UPDATE published_head SET snapshot_id='metadata-new',changed_at_ms=? WHERE module='daily_raw'",(now,))
    assert listing.read_summary('raw',market.entry,reference) is None
    result=client.get('/api/v1/data/listing-status').json()
    assert result['summary_is_current'] is False and result['identities']['pending_listing_count']==0


def test_quote_presence_counts_unique_valid_ohlcv_and_zero_volume_blocks_pending(identities):
    market,client,_,_=identities
    path=core.published_dir()/'raw'/'daily.parquet';bars=pd.read_parquet(path);base=bars.iloc[0].to_dict()
    rows=[{**base,'instrument_id':'600003.SH','volume':0.},
          {**base,'instrument_id':'600004.SH','open':None},
          {**base,'instrument_id':'600005.SH'},{**base,'instrument_id':'600005.SH'},
          {**base,'instrument_id':'600006.SH','amount':-1.}]
    pd.concat([bars,pd.DataFrame(rows)]).to_parquet(path)
    proof=listing.quote_presence(market,['600003.SH','600004.SH','600005.SH','600006.SH','600001.SH'],market.entry)
    assert all(proof[symbol]['valid_quote_count']==0 for symbol in ('600004.SH','600005.SH','600006.SH'))
    assert proof['600003.SH']['valid_quote_count']==1
    assert proof['600001.SH']['valid_quote_count']==71
    assert confirmation(client,instrument_ids=['600003.SH']).status_code==409


@pytest.mark.parametrize('case',['duplicate','invalid','raw_only','bad_date','missing_raw'])
def test_any_ambiguous_or_raw_record_cannot_prove_pending(identities,case):
    market,client,reference,_=identities
    directory=core.published_dir()/'raw';path=directory/'daily.parquet';bars=pd.read_parquet(path)
    record={**bars.iloc[0].to_dict(),'instrument_id':'600003.SH'}
    if case=='duplicate':bars=pd.concat([bars,pd.DataFrame([record,record])])
    elif case=='invalid':bars=pd.concat([bars,pd.DataFrame([{**record,'open':None}])])
    elif case=='bad_date':bars=pd.concat([bars,pd.DataFrame([{**record,'date':'bad-date'}])])
    elif case=='raw_only':
        raw=pd.read_parquet(directory/'raw-daily.parquet')
        pd.concat([raw,pd.DataFrame([{'thscode':'600003.SH','date_ms':None}])]).to_parquet(directory/'raw-daily.parquet')
    else:(directory/'raw-daily.parquet').unlink()
    bars.to_parquet(path)
    proof=listing.quote_presence(market,['600003.SH'],market.entry)
    manual={'600003.SH':{'status':'pending','observed_business_date':market.entry,'reason':'用户确认'}}
    row=listing.resolve(reference,market.entry,proof,confirmations=manual)['items']['600003.SH']
    assert row['listing_status']=='unknown' and '不能当作无报价' in row['reason']
    assert confirmation(client,instrument_ids=['600003.SH']).status_code==409


def test_racing_identical_request_reuses_the_first_committed_audit(identities,monkeypatch):
    _,client,_,_=identities;original=listing.quote_presence;first=[]
    def race(*args):
        proof=original(*args);monkeypatch.setattr(listing,'quote_presence',original)
        first.append(confirmation(client).json());return proof
    monkeypatch.setattr(listing,'quote_presence',race)
    response=confirmation(client)
    assert response.status_code==200 and response.json()['already_exists'] is True
    assert response.json()['confirmation_ids']==first[0]['confirmation_ids']
    with core.connect() as db:assert db.execute('SELECT count(*) FROM listing_status_confirmation').fetchone()[0]==4


def test_only_new_sync_marker_enqueues_once_and_recovery_keeps_old_scope(identities):
    market,_,_,_=identities
    with core.connect() as db:
        old=dict(db.execute("SELECT * FROM job WHERE job_id='fixture-job'").fetchone())
        db.execute("INSERT INTO job(job_id,request_key,logical_key,kind,target_date,parameters_json,parameters_hash,status,created_at_ms) VALUES('new-sync','new-sync','new-sync','increment',?,?,?,'succeeded',?)",
                   (market.entry,core.dumps({'listing_status_check_version':listing.VERSION}),core.digest({}),core.now_ms()))
        new=dict(db.execute("SELECT * FROM job WHERE job_id='new-sync'").fetchone())
    assert listing.after_sync(old) is None
    identifier=listing.after_sync(new);assert identifier
    assert listing.after_sync(new) is None
    listing.recover_sync_followups()
    result=execute();assert result['job_id']==identifier and result['status']=='succeeded'
    with core.connect() as db:assert db.execute("SELECT count(*) FROM job WHERE json_extract(parameters_json,'$.operation')=?",(listing.OPERATION,)).fetchone()[0]==1


def test_old_attempt_and_new_head_cannot_commit_summary(identities,monkeypatch):
    market,_,reference,status=identities
    with core.connect() as db:identifier=listing.enqueue(db,'interrupted')
    job=tasks.claim_next('listing-worker')
    tasks.change_task(identifier,'pause');tasks.change_task(identifier,'resume')
    resumed=tasks.claim_next('listing-worker');assert resumed['lease_owner']!=job['lease_owner']
    with pytest.raises(tasks.TaskCancelled):listing.execute_check(job)
    original=listing.quote_presence
    def replace(*args):
        result=original(*args);advance_generation(reference,status,market.days[71]);return result
    monkeypatch.setattr(listing,'quote_presence',replace)
    listing.execute_check(resumed)
    assert tasks.task_view(identifier)['status']=='no_change'
    with core.connect() as db:assert db.execute('SELECT count(*) FROM listing_status_summary').fetchone()[0]==0


def test_migration_is_additive_empty_and_contains_no_seeded_security_ids():
    migrations=Path(__file__).parents[1]/'app'/'migrations';db=sqlite3.connect(':memory:')
    for file in sorted(migrations.glob('*.sql')):
        if file.name.startswith('016_'):break
        db.executescript(file.read_text())
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    body=(migrations/'016_listing_status.sql').read_text();db.executescript(body)
    assert tables<={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert db.execute('SELECT count(*) FROM listing_status_confirmation').fetchone()[0]==0
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert not db.execute('PRAGMA foreign_key_check').fetchall()
