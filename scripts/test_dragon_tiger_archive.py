"""Optional LHB graph is self-contained in standard backup/restore; fixtures only."""
from pathlib import Path
import hashlib,json
import pytest
from test_deployment import source
import backup as backup_module
from backup import perform_backup,sha,dragon_tiger_files
from restore import perform_restore,verify_backup

DAY='2026-10-19';VERSION='dragon-tiger-v1'


def fixture_tree(source):
    root=source/'published/dragon-tiger'
    def save(value):
        content=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode();name=hashlib.sha256(content).hexdigest()+'.json'
        path=root/'artifacts'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content+b'\n')
        return {'record_ref':'artifacts/'+name,'sha256':sha(path)}
    raw=save({'version':VERSION,'date':DAY,'capability':'fuyao-all','payload':{'code':0,'data':{'fixture':True}}})
    record=save({'version':VERSION,'accepted':True,'date':DAY,'snapshot_id':'fixture','source_objects':[raw],'boards':{'fixture':True},'seats':{'fixture':True}})
    index=save({'version':VERSION,'anchor_date':DAY,'days':{DAY:record},'failures':{}})
    (root/'head.json').write_text(json.dumps({'version':VERSION,**index}))
    part=root/'parts/fuyao-all'/f'{DAY}.json';part.parent.mkdir(parents=True);part.write_text(json.dumps(raw))
    orphan=save({'fixture':'unreferenced object not in graph'})
    (root/'private.key').write_text('fixture-secret-not-eligible')
    return root,raw,record,index,orphan


def test_standard_backup_restore_keeps_lhb_day_and_source_closure(source,tmp_path):
    root,raw,record,index,orphan=fixture_tree(source)
    bundle=tmp_path/'bundle';perform_backup(source,bundle);manifest=verify_backup(bundle)
    names={item['path'] for item in manifest['files']}
    assert 'published/dragon-tiger/head.json' in names
    assert 'published/dragon-tiger/'+raw['record_ref'] in names
    assert 'published/dragon-tiger/'+record['record_ref'] in names
    assert 'published/dragon-tiger/'+index['record_ref'] in names
    assert 'published/dragon-tiger/'+orphan['record_ref'] not in names
    assert not any(name.endswith('private.key') for name in names)
    restored=tmp_path/'restored';perform_restore(bundle,restored)
    assert dragon_tiger_files(restored/'published')==dragon_tiger_files(source/'published')
    for item in manifest['files']:
        if item['path'].startswith('published/dragon-tiger/'):
            assert (restored/item['path']).read_bytes()==(source/item['path']).read_bytes()


@pytest.mark.parametrize('mode',['missing','corrupt'])
def test_backup_refuses_missing_or_corrupt_lhb_source(source,tmp_path,mode):
    root,raw,*_=fixture_tree(source);path=root/raw['record_ref']
    if mode=='missing':path.unlink()
    else:path.write_text('{"fixture":"corrupted source"}')
    with pytest.raises(ValueError,match='Dragon-tiger|dragon-tiger'):perform_backup(source,tmp_path/'must-not-exist')
    assert not (tmp_path/'must-not-exist').exists()


def test_restore_validates_source_closure_even_when_manifest_omits_dependency(source,tmp_path):
    root,raw,*_=fixture_tree(source);bundle=tmp_path/'bundle';perform_backup(source,bundle)
    relative='published/dragon-tiger/'+raw['record_ref'];(bundle/relative).unlink()
    path=bundle/'backup-manifest.json';manifest=json.loads(path.read_text());manifest['files']=[item for item in manifest['files'] if item['path']!=relative];path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='Dragon-tiger|dragon-tiger'):verify_backup(bundle)


def test_lhb_inventory_follows_small_index_without_scanning_cas_history(source,monkeypatch):
    fixture_tree(source)
    original=Path.iterdir
    def no_artifact_scan(path):
        if path.name=='artifacts' and path.parent.name=='dragon-tiger':pytest.fail('backup must follow references, not scan historical CAS')
        return original(path)
    monkeypatch.setattr(Path,'iterdir',no_artifact_scan)
    assert len(dragon_tiger_files(source/'published'))==5


def test_lhb_component_publication_during_copy_rejects_mixed_backup(source,tmp_path,monkeypatch):
    root,raw,*_=fixture_tree(source);original=backup_module.shutil.copyfile;changed=False
    def publish_during_copy(src,dst,*args,**kwargs):
        nonlocal changed
        result=original(src,dst,*args,**kwargs)
        if Path(src)==root/raw['record_ref']:
            part=root/'parts/fuyao-all'/f'{DAY}.json';value=json.loads(part.read_text());value['diagnostic']='fixture concurrent accepted-pointer revision';part.write_text(json.dumps(value));changed=True
        return result
    monkeypatch.setattr(backup_module.shutil,'copyfile',publish_during_copy)
    with pytest.raises(ValueError,match='changed|differs'):perform_backup(source,tmp_path/'must-not-exist')
    assert changed and not (tmp_path/'must-not-exist').exists()
