"""Offline deployment/backup checks over temporary records and new synthetic secrets only."""
import importlib.util,json,os,sqlite3,stat,subprocess,sys,time
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import backup as backup_module
from backup import perform_backup,sha
from restore import perform_restore,verify_backup
spec=importlib.util.spec_from_file_location('create_secret',ROOT/'deploy/create_secret.py');secret=importlib.util.module_from_spec(spec);spec.loader.exec_module(secret)

@pytest.fixture
def source(tmp_path,monkeypatch):
    for name in ('SHIXU_SQLITE_PATH','SHIXU_PUBLISHED_DIR','SHIXU_CREDENTIAL_DIR','SHIXU_CREDENTIAL_MASTER_KEY_FILE','SHIXU_MASTER_KEY_FILE'):monkeypatch.delenv(name,raising=False)
    data=tmp_path/'live';(data/'business').mkdir(parents=True);(data/'published/generation').mkdir(parents=True);(data/'credentials').mkdir()
    db=sqlite3.connect(data/'business/app.sqlite3');db.row_factory=sqlite3.Row
    db.executescript((ROOT/'backend/app/migrations/001_initial.sql').read_text())
    now=time.time_ns()//1000000;h='a'*64
    for jid,status in [('import','succeeded'),('active','running'),('queued','queued')]:
        db.execute('INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,parameters_hash,status,lease_owner,lease_expires_at_ms,created_at_ms) VALUES(?,?,?,?,?,?,?,?,?,?)',(jid,jid,jid,'initialise','{}',h,status,'old-process' if status=='running' else None,now+60000 if status=='running' else None,now))
    artifact=data/'published/generation/reference.json';artifact.write_text('{"fixture":true}')
    for sid,module in [('cal','calendar'),('bars','analysis_bars')]:
        manifest=data/'published/catalog'/sid/'manifest.json';manifest.parent.mkdir(parents=True);manifest.write_text(json.dumps({'snapshot_id':sid,'module':module}))
        db.execute('INSERT INTO dataset_snapshot(snapshot_id,module,scope_key,job_id,data_begin,data_end,knowledge_cutoff_ms,price_basis,algorithm_version,lineage_json,manifest_path,manifest_sha256,state,quality,created_at_ms) VALUES(?,?,?,\'import\',\'2026-10-19\',\'2026-10-19\',?,\'none\',\'fixture\',\'{}\',?,?,\'staged\',\'ready\',?)',(sid,module,'fixture',now,str(manifest.relative_to(data/'published')),sha(manifest),now))
        db.execute('INSERT INTO snapshot_artifact VALUES(?,?,?,?,?,?)',(sid,'generation/reference.json',module,sha(artifact),artifact.stat().st_size,1))
        db.execute('INSERT INTO snapshot_coverage(snapshot_id,feature,scope_json,coverage_state,observed_rows,continuous_begin,continuous_end) VALUES(?,?,\'{}\',\'ready\',1,\'2026-10-19\',\'2026-10-19\')',(sid,module))
        db.execute("UPDATE dataset_snapshot SET state='validated' WHERE snapshot_id=?",(sid,));db.execute("UPDATE dataset_snapshot SET state='published',published_at_ms=? WHERE snapshot_id=?",(now,sid));db.execute('INSERT INTO published_head VALUES(?,?,?,?)',(module,'fixture',sid,now))
    db.execute('INSERT INTO business_clock VALUES(1,\'2026-10-19\',\'cal\',?)',(now,))
    db.execute("INSERT INTO retention_pin VALUES('request-live','cal','request','browser-request',?,?)",(now,now+100000))
    db.execute('INSERT INTO sync_batch(job_id,capability,batch_key,attempt_no,status,fetched_at_ms) VALUES(\'active\',\'daily_k\',\'part\',1,\'running\',?)',(now,));db.commit();db.close()
    (data/'credentials/fuyao.enc').write_bytes(b'synthetic-ciphertext-only')
    (data/'master-key-never-back-up').write_bytes(b'synthetic-master-key')
    (data/'work').mkdir();(data/'work/active.duckdb').write_bytes(b'uncommitted-work')
    return data

def test_online_backup_restore_checksums_shared_refs_and_no_master(source,tmp_path):
    backup=tmp_path/'bundle';result=perform_backup(source,backup)
    assert result['master_key_included'] is False and result['snapshot_count']==2
    manifest=verify_backup(backup)
    assert [x['path'] for x in manifest['files']].count('published/generation/reference.json')==1
    assert (backup/'credentials/fuyao.enc').read_bytes()==b'synthetic-ciphertext-only'
    assert not (backup/'master-key-never-back-up').exists() and not (backup/'work').exists()
    target=tmp_path/'restored';restored=perform_restore(backup,target)
    assert set(restored['paused_jobs'])=={'active','queued'}
    db=sqlite3.connect(target/'business/app.sqlite3')
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert db.execute("SELECT status,lease_owner,lease_expires_at_ms FROM job WHERE job_id='active'").fetchone()==('paused',None,None)
    assert db.execute("SELECT status FROM sync_batch WHERE job_id='active'").fetchone()[0]=='cancelled'
    assert db.execute("SELECT count(*) FROM retention_pin WHERE owner_kind='request' OR owner_id=?",(manifest['backup_id'],)).fetchone()[0]==0
    assert db.execute('SELECT business_date FROM business_clock').fetchone()[0]=='2026-10-19'
    assert db.execute('SELECT count(*) FROM published_head').fetchone()[0]==2;db.close()
    original=sqlite3.connect(source/'business/app.sqlite3')
    assert original.execute("SELECT status FROM job WHERE job_id='active'").fetchone()[0]=='running'
    assert original.execute("SELECT count(*) FROM retention_pin WHERE owner_kind='backup'").fetchone()[0]==0;original.close()

def supplement_fixture(source):
    root=source/'published/market-supplement'
    raw=root/'raw/public-response.json';raw.parent.mkdir(parents=True);raw.write_text(json.dumps({'public_records':[{'instrument_id':'600825.SH','date':'2026-10-19'}]}))
    record={'complete':True,'date':'2026-10-19','items':[{'instrument_id':'600825.SH'}],'raw_artifact':'raw/public-response.json','raw_sha256':sha(raw)}
    artifact=root/'artifacts/accepted-result.json';artifact.parent.mkdir();artifact.write_text(json.dumps(record))
    pointer=root/'accepted/eastmoney/limitup/2026-10-19.json';pointer.parent.mkdir(parents=True);pointer.write_text(json.dumps({'version':'market-supplement-v1','signature':'fixture-quote-signature','artifact':'artifacts/accepted-result.json','sha256':sha(artifact)}))
    index=root/'indexes/public-index.json';index.parent.mkdir();index.write_text(json.dumps({'version':'market-supplement-v1','snapshot_id':'fixture-market','public_pools':{'2026-10-19':record},'annual_pools':{},'fund_flows':{}}))
    head=root/'head.json';head.write_text(json.dumps({'version':'market-supplement-v1','snapshot_id':'fixture-market','artifact':'indexes/public-index.json','sha256':sha(index),'published_at_ms':1}))
    (root/'head.json.writer.tmp').write_text('in-progress-public-write')
    (root/'master.key').write_bytes(b'synthetic-secret-must-not-be-exported')
    return root

def test_optional_public_supplement_roundtrip_preserves_pointer_chain_not_secrets(source,tmp_path):
    root=supplement_fixture(source);bundle=tmp_path/'bundle';perform_backup(source,bundle)
    manifest=verify_backup(bundle)
    names={row['path'] for row in manifest['files']}
    assert 'published/market-supplement/head.json' in names
    assert 'published/market-supplement/accepted/eastmoney/limitup/2026-10-19.json' in names
    assert not any('master.key' in name or name.endswith('.tmp') for name in names)
    restored=tmp_path/'restored';perform_restore(bundle,restored)
    frozen=restored/'published/market-supplement'
    head=json.loads((frozen/'head.json').read_text());index=frozen/head['artifact']
    assert sha(index)==head['sha256']
    entry=json.loads(index.read_text())['public_pools']['2026-10-19']
    assert sha(frozen/entry['raw_artifact'])==entry['raw_sha256']
    pointer=json.loads((frozen/'accepted/eastmoney/limitup/2026-10-19.json').read_text())
    assert sha(frozen/pointer['artifact'])==pointer['sha256']
    for key in names:
        if key.startswith('published/market-supplement/'):assert (restored/key).read_bytes()==(source/key).read_bytes()

def test_supplement_missing_referenced_raw_object_aborts_instead_of_partial_success(source,tmp_path):
    root=supplement_fixture(source);(root/'raw/public-response.json').unlink()
    with pytest.raises(ValueError,match='missing|checksum'):perform_backup(source,tmp_path/'bundle')
    assert not (tmp_path/'bundle').exists()

def test_supplement_head_publication_during_copy_aborts_and_releases_pins(source,tmp_path,monkeypatch):
    root=supplement_fixture(source);original_copy=backup_module.shutil.copyfile;changed=False
    def publish_while_copying(src,dst,*args,**kwargs):
        nonlocal changed
        result=original_copy(src,dst,*args,**kwargs)
        if Path(src)==root/'raw/public-response.json':
            head=json.loads((root/'head.json').read_text());head['published_at_ms']=2
            temporary=root/'next-head.tmp';temporary.write_text(json.dumps(head));os.replace(temporary,root/'head.json');changed=True
        return result
    monkeypatch.setattr(backup_module.shutil,'copyfile',publish_while_copying)
    with pytest.raises(ValueError,match='supplement changed'):perform_backup(source,tmp_path/'bundle')
    assert changed and not (tmp_path/'bundle').exists()
    db=sqlite3.connect(source/'business/app.sqlite3');assert db.execute("SELECT count(*) FROM retention_pin WHERE owner_kind='backup'").fetchone()[0]==0;db.close()

def test_supplement_index_hash_mismatch_aborts_before_restoreable_bundle(source,tmp_path):
    root=supplement_fixture(source);(root/'indexes/public-index.json').write_text('{}')
    with pytest.raises(ValueError,match='version|checksum'):perform_backup(source,tmp_path/'bundle')
    assert not (tmp_path/'bundle').exists()

def test_restore_rejects_checksum_tampering_before_target_creation(source,tmp_path):
    bundle=tmp_path/'bundle';perform_backup(source,bundle)
    (bundle/'published/generation/reference.json').write_text('modified')
    with pytest.raises(ValueError,match='checksum'):perform_restore(bundle,tmp_path/'target')
    assert not (tmp_path/'target').exists()

def test_finder_metadata_created_and_modified_during_copy_is_not_backup_evidence(source,tmp_path,monkeypatch):
    original_copy=backup_module.shutil.copyfile;original_sha=backup_module.sha;original_fsync=backup_module.os.fsync
    metadata=[];mutations=0
    def copy_with_finder(src,dst,*args,**kwargs):
        result=original_copy(src,dst,*args,**kwargs)
        stage=next((p for p in Path(dst).parents if p.name.startswith('.shixu-backup-')),None)
        if stage and not metadata:
            metadata.extend([stage/'.DS_Store',stage/'published/.DS_Store'])
            for path in metadata:path.write_bytes(b'finder-first-state')
        return result
    def hash_business_only(path):
        nonlocal mutations
        assert Path(path).name!='.DS_Store','Finder metadata must never enter the SHA manifest'
        for item in metadata:item.write_text('finder-updated-'+str(mutations))
        if metadata:mutations+=1
        return original_sha(path)
    def fsync_business_only(descriptor):
        info=os.fstat(descriptor)
        assert all((info.st_dev,info.st_ino)!=(p.stat().st_dev,p.stat().st_ino) for p in metadata),'Finder metadata must not be fsynced as backup evidence'
        return original_fsync(descriptor)
    monkeypatch.setattr(backup_module.shutil,'copyfile',copy_with_finder)
    monkeypatch.setattr(backup_module,'sha',hash_business_only)
    monkeypatch.setattr(backup_module.os,'fsync',fsync_business_only)
    bundle=tmp_path/'finder-bundle';perform_backup(source,bundle)
    assert mutations>1
    for relative in ('.DS_Store','published/.DS_Store'):(bundle/relative).write_bytes(b'finder-after-publication')
    manifest=verify_backup(bundle)
    assert not any(Path(item['path']).name=='.DS_Store' for item in manifest['files'])
    restored=tmp_path/'finder-restored';perform_restore(bundle,restored)
    assert (restored/'published/generation/reference.json').read_bytes()==(source/'published/generation/reference.json').read_bytes()
    assert not list(restored.rglob('.DS_Store'))

@pytest.mark.parametrize('relative',['extra-file','.DS_Store.extra','published/backup-manifest.json'])
def test_restore_still_rejects_unlisted_files_with_other_names(source,tmp_path,relative):
    bundle=tmp_path/'bundle';perform_backup(source,bundle)
    extra=bundle/relative;extra.parent.mkdir(parents=True,exist_ok=True);extra.write_text('unlisted')
    with pytest.raises(ValueError,match='inventory'):perform_restore(bundle,tmp_path/'restored')
    assert not (tmp_path/'restored').exists()

@pytest.mark.parametrize('relative',['.DS_Store','published/.DS_Store','extra-link'])
def test_restore_rejects_symlinks_including_finder_metadata(source,tmp_path,relative):
    bundle=tmp_path/'bundle';perform_backup(source,bundle)
    outside=tmp_path/'outside';outside.write_text('must-not-be-followed')
    link=bundle/relative;link.parent.mkdir(parents=True,exist_ok=True);link.symlink_to(outside)
    with pytest.raises(ValueError,match='Symlink'):perform_restore(bundle,tmp_path/'restored')
    assert not (tmp_path/'restored').exists()

def test_backup_rejects_finder_named_symlink_injected_into_stage(source,tmp_path,monkeypatch):
    original_copy=backup_module.shutil.copyfile;outside=tmp_path/'outside';outside.write_text('not-metadata')
    injected=False
    def copy_with_symlink(src,dst,*args,**kwargs):
        nonlocal injected
        result=original_copy(src,dst,*args,**kwargs)
        stage=next((p for p in Path(dst).parents if p.name.startswith('.shixu-backup-')),None)
        if stage and not injected:
            (stage/'.DS_Store').symlink_to(outside);injected=True
        return result
    monkeypatch.setattr(backup_module.shutil,'copyfile',copy_with_symlink)
    with pytest.raises(ValueError,match='Symlink'):perform_backup(source,tmp_path/'bundle')
    assert injected and not (tmp_path/'bundle').exists()

def test_old_manifest_listing_finder_metadata_requires_fresh_backup(source,tmp_path):
    bundle=tmp_path/'bundle';perform_backup(source,bundle)
    metadata=bundle/'published/.DS_Store';metadata.write_text('legacy-bad-manifest-entry')
    path=bundle/'backup-manifest.json';manifest=json.loads(path.read_text())
    manifest['files'].append({'path':'published/.DS_Store','bytes':metadata.stat().st_size,'sha256':sha(metadata)})
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='fresh backup'):perform_restore(bundle,tmp_path/'restored')
    assert not (tmp_path/'restored').exists()

def test_restore_refuses_existing_even_empty_target(source,tmp_path):
    bundle=tmp_path/'bundle';perform_backup(source,bundle);target=tmp_path/'target';target.mkdir()
    with pytest.raises(ValueError,match='must not exist'):perform_restore(bundle,target)

def test_missing_published_file_aborts_and_releases_pins(source,tmp_path):
    (source/'published/generation/reference.json').unlink()
    with pytest.raises(ValueError,match='missing'):perform_backup(source,tmp_path/'bundle')
    assert not (tmp_path/'bundle').exists()
    db=sqlite3.connect(source/'business/app.sqlite3');assert db.execute("SELECT count(*) FROM retention_pin WHERE owner_kind='backup'").fetchone()[0]==0;db.close()

def test_backup_cannot_be_nested_in_live_data(source):
    with pytest.raises(ValueError,match='inside live'):perform_backup(source,source/'backup')

def test_symlink_published_object_is_rejected(source,tmp_path):
    target=source/'published/generation/reference.json';target.unlink();outside=tmp_path/'outside';outside.write_text('private');target.symlink_to(outside)
    with pytest.raises(ValueError,match='Symlink'):perform_backup(source,tmp_path/'bundle')

def test_master_named_like_ciphertext_is_never_copied(source,tmp_path,monkeypatch):
    monkeypatch.setenv('SHIXU_MASTER_KEY_FILE',str(source/'credentials/fuyao.enc'))
    with pytest.raises(ValueError,match='master key'):perform_backup(source,tmp_path/'bundle')

def test_new_secret_private_folder_and_no_overwrite(tmp_path):
    path=tmp_path/'private-secret/master.key';secret.create_secret(path)
    assert stat.S_IMODE(path.parent.stat().st_mode)==0o700
    assert stat.S_IMODE(path.stat().st_mode)==0o444
    assert len(path.read_bytes())==44
    with pytest.raises(FileExistsError):secret.create_secret(path)

def test_secret_refuses_public_parent(tmp_path):
    directory=tmp_path/'public';directory.mkdir(mode=0o755)
    with pytest.raises(ValueError,match='private'):secret.create_secret(directory/'master.key')
    assert stat.S_IMODE(directory.stat().st_mode)==0o755

def test_compose_permissions_and_single_writer(tmp_path):
    env=os.environ|{'SHIXU_MASTER_KEY_FILE':str(tmp_path/'unused-secret')}
    result=subprocess.run(['docker','compose','-f',str(ROOT/'deploy/compose.yaml'),'config','--format','json'],env=env,text=True,capture_output=True)
    if result.returncode and 'not found' in result.stderr:pytest.skip('Docker Compose unavailable')
    assert result.returncode==0,result.stderr
    config=json.loads(result.stdout);services=config['services']
    assert services['api']['image']==services['worker']['image']
    mounts={v['target']:v for v in services['api']['volumes']}
    assert mounts['/var/lib/shixu/published']['read_only'] is True
    assert '/var/lib/shixu/work' not in mounts
    assert services['worker']['deploy']['replicas']==1
    assert services['api']['ports'][0]['host_ip']=='127.0.0.1'
    assert services['api']['depends_on']['init']['condition']=='service_completed_successfully'
    assert 'shixu_master' in config['secrets']

def test_docker_archive_project_names_are_not_shell_input():
    from docker_archive import project_name
    assert project_name('shixu-restored-20261020')=='shixu-restored-20261020'
    for invalid in ('Uppercase','../../live','x;rm -rf /',''):
        with pytest.raises(ValueError):project_name(invalid)

def test_docker_archive_rejects_mismatched_volume_labels(monkeypatch):
    import docker_archive
    monkeypatch.setattr(docker_archive,'inspect_volume',lambda name:{'Labels':{'com.docker.compose.project':'different','com.docker.compose.volume':'business'}})
    with pytest.raises(ValueError,match='identity mismatch'):docker_archive.require_source_volumes('shixu')

def test_docker_restore_refuses_original_project_before_creating_volumes(monkeypatch):
    import docker_archive
    monkeypatch.setattr(docker_archive,'verify_backup',lambda bundle:{'source_compose_project':'live'})
    with pytest.raises(ValueError,match='differ'):docker_archive.restore_project('/unused','live','unused-image')


def test_annual_down_index_raw_chain_roundtrips_and_detects_tampering(source,tmp_path):
    root=supplement_fixture(source)
    raw=root/'raw/down-response.json'
    raw.write_text(json.dumps({'down_records':[{'instrument_id':'603999.SH','date':'2026-10-19'}]}))
    index=root/'indexes/public-index.json';document=json.loads(index.read_text())
    document['annual_down_pools']={'2026-10-19':{'date':'2026-10-19','pool_kind':'limitdown','items':[{'instrument_id':'603999.SH'}],'raw_artifact':'raw/down-response.json','raw_sha256':sha(raw)}}
    index.write_text(json.dumps(document))
    head=root/'head.json';pointer=json.loads(head.read_text());pointer['sha256']=sha(index);head.write_text(json.dumps(pointer))
    bundle=tmp_path/'down-bundle';perform_backup(source,bundle)
    restored=tmp_path/'down-restored';perform_restore(bundle,restored)
    record=json.loads((restored/'published/market-supplement/indexes/public-index.json').read_text())['annual_down_pools']['2026-10-19']
    assert sha(restored/'published/market-supplement'/record['raw_artifact'])==record['raw_sha256']
    raw.write_text('{"tampered":true}')
    with pytest.raises(ValueError,match='checksum mismatch'):perform_backup(source,tmp_path/'bad-down-bundle')
    assert not (tmp_path/'bad-down-bundle').exists()
