"""Offline release checks: synthetic paths only, no Docker daemon or live data."""
import importlib.util
import json
from pathlib import Path
import stat
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('prepare_upgrade',ROOT/'scripts/prepare_upgrade.py')
upgrade=importlib.util.module_from_spec(spec);spec.loader.exec_module(upgrade)
OLD='example.invalid/shixu@sha256:'+'a'*64
NEW='example.invalid/shixu:v0.1.0@sha256:'+'b'*64


def arguments(tmp_path):
    return {'current_project':'existing', 'candidate_project':'preview-v010',
        'target_project':'release-v010','rollback_project':'rollback-v009',
        'current_image':OLD,'candidate_image':NEW,'backup_root':tmp_path/'backup',
        'master_key_file':tmp_path/'separate-secret'/'master.key',
        'output':tmp_path/'plan','candidate_commit':'c'*40}


def test_plan_is_offline_and_only_contains_digest_images_distinct_projects(tmp_path,monkeypatch):
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:pytest.fail('plan cannot run a command'))
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('plan cannot start a subprocess'))
    values=arguments(tmp_path);master=values['master_key_file'];master.parent.mkdir()
    master.write_text('synthetic-master-text-never-export')
    original=Path.read_bytes
    def read_bytes(path):
        if path==master: pytest.fail('master key content must not be read')
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',read_bytes)
    plan=upgrade.make_plan(**values);result=upgrade.write_plan(plan)
    assert result['executed'] is False
    assert plan['images']=={'current':OLD,'candidate':NEW,'candidate_commit':'c'*40}
    assert len(set(plan['projects'].values()))==4
    assert not values['backup_root'].exists()
    assert stat.S_IMODE(values['output'].stat().st_mode)==0o700
    files=list(values['output'].iterdir());assert len(files)==6
    assert all(stat.S_IMODE(f.stat().st_mode)==0o600 for f in files)
    for file in files: assert 'synthetic-master-text-never-export' not in file.read_text()
    assert 'SHIXU_IMAGE_REF='+NEW in (values['output']/'candidate.env').read_text()
    assert 'master.key' in (values['output']/'candidate.env').read_text()


@pytest.mark.parametrize('reference',['shixu:latest','shixu:v0.1.0','shixu@sha256:x',
    'https://example.invalid/shixu@sha256:'+'a'*64,'bad;command@sha256:'+'a'*64,
    'UPPER@sha256:'+'a'*64,'x//y@sha256:'+'a'*64,'x::::@sha256:'+'a'*64])
def test_mutable_or_invalid_image_ref_is_rejected(tmp_path,reference):
    args=arguments(tmp_path);args['candidate_image']=reference
    with pytest.raises(ValueError,match='镜像'): upgrade.make_plan(**args)
    assert not args['output'].exists()


@pytest.mark.parametrize('projects',[('existing','existing','release','rollback'),
    ('live','preview','release','release'),('live','../preview','release','rollback')])
def test_cannot_plan_shared_project_volumes_or_shell_names(tmp_path,projects):
    args=arguments(tmp_path)
    args.update(dict(zip(('current_project','candidate_project','target_project','rollback_project'),projects)))
    with pytest.raises(ValueError): upgrade.make_plan(**args)


@pytest.mark.parametrize('field,value',[('candidate_port',8788),('candidate_port',True),
    ('candidate_port',65536),('live_port',80),('candidate_commit','short')])
def test_invalid_port_and_commit_refuse_plan(tmp_path,field,value):
    args=arguments(tmp_path);args[field]=value
    with pytest.raises(ValueError): upgrade.make_plan(**args)


def test_paths_key_and_backup_must_be_separate_and_noninterpolating(tmp_path):
    for change in ({'backup_root':tmp_path/'plan'/'nested'},
                   {'master_key_file':tmp_path/'plan'/'master.key'},
                   {'master_key_file':tmp_path/'backup'/'master.key'},
                   {'master_key_file':'relative/key'},
                   {'master_key_file':str(tmp_path/'$EXPANDED'/'key')}):
        with pytest.raises(ValueError): upgrade.make_plan(**(arguments(tmp_path)|change))


def test_existing_plan_never_overwritten(tmp_path):
    args=arguments(tmp_path);args['output'].mkdir();marker=args['output']/'keep'
    marker.write_text('keep')
    with pytest.raises(ValueError,match='不存在'): upgrade.write_plan(upgrade.make_plan(**args))
    assert marker.read_text()=='keep' and list(args['output'].iterdir())==[marker]


def test_candidate_has_no_worker_and_cutover_rollback_explicitly_ordered(tmp_path):
    plan=upgrade.make_plan(**arguments(tmp_path));commands=plan['commands']
    assert all(command['requires_manual_gate'] for command in commands)
    assert all('down' not in c['argv'] and '-v' not in c['argv'] for c in commands)
    preview=[c for c in commands if c['phase']=='preview']
    preview_up=next(c['argv'] for c in preview if 'up' in c['argv'])
    assert preview_up[-1]=='api' and 'worker' not in preview_up
    assert preview_up[:3]==['env','-u','SHIXU_IMAGE_REF']
    cutover=[c['argv'] for c in commands if c['phase']=='cutover']
    assert cutover[0][-3:]==['stop','api','worker']
    assert cutover[-1][-1]=='worker' and '--profile' in cutover[-1]
    rollback=[c['argv'] for c in commands if c['phase']=='rollback']
    assert rollback[0][-3:]==['stop','api','worker']
    restore=next(c for c in rollback if 'restore' in c)
    assert OLD in restore and restore[-1]=='rollback-v009'
    assert plan['rollback']['post_cutover_writes_preserved_automatically'] is False


def test_release_compose_expands_without_daemon_and_has_no_build_or_shared_worker_work(tmp_path):
    # `compose config` parses locally; it does not pull/start/migrate services.
    env=__import__('os').environ|{'SHIXU_IMAGE_REF':NEW,'SHIXU_HTTP_PORT':'18789',
                                'SHIXU_MASTER_KEY_FILE':str(tmp_path/'not-read.key')}
    command=['docker','compose','-p','offline-release-check','-f',str(ROOT/'deploy/compose.release.yaml'),'config','--format','json']
    completed=subprocess.run(command,env=env,capture_output=True,text=True)
    if completed.returncode and ('not found' in completed.stderr or 'unknown command' in completed.stderr):
        pytest.skip('Docker Compose unavailable')
    assert completed.returncode==0,completed.stderr
    services=json.loads(completed.stdout)['services']
    for service in services.values(): assert service['image']==NEW and 'build' not in service
    api=services['api'];assert api['read_only'] is True
    assert api['ports'][0]['host_ip']=='127.0.0.1'
    mounts={v['target']:v for v in api['volumes']}
    assert mounts['/var/lib/shixu/published']['read_only'] is True
    assert '/var/lib/shixu/work' not in mounts
    assert any('/var/lib/shixu/work:' in value for value in api['tmpfs'])
    # Disabled profiles can be omitted from config output. Read with --profile.
    enabled=subprocess.run(command[:2]+['--profile','worker']+command[2:],env=env,capture_output=True,text=True)
    assert enabled.returncode==0,enabled.stderr
    worker=json.loads(enabled.stdout)['services']['worker']
    assert worker['profiles']==['worker'] and worker['deploy']['replicas']==1
    assert any(v['target']=='/var/lib/shixu/work' and v['type']=='volume' for v in worker['volumes'])


def test_release_compose_missing_required_image_fails_config_without_docker_calls(tmp_path):
    env=__import__('os').environ.copy();env.pop('SHIXU_IMAGE_REF',None)
    env.update(SHIXU_HTTP_PORT='18789',SHIXU_MASTER_KEY_FILE=str(tmp_path/'unused'))
    result=subprocess.run(['docker','compose','-f',str(ROOT/'deploy/compose.release.yaml'),'config'],
        env=env,capture_output=True,text=True)
    assert result.returncode!=0 and 'SHIXU_IMAGE_REF' in result.stderr
