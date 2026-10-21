"""Offline publication guards. Fake registry commands never reach Docker/GHCR."""
import json
import os
from pathlib import Path
import re
import subprocess
import textwrap

import pytest

ROOT=Path(__file__).resolve().parents[1]
WORKFLOW=ROOT/'.github/workflows/publish-image.yml'
IMAGE='ghcr.io/dreamerxx0301/ad-market-review'
REVISION='a'*40
AMD='sha256:'+'b'*64
ARM='sha256:'+'c'*64
INDEX='sha256:'+'d'*64


def run_blocks():
    lines=WORKFLOW.read_text().splitlines();blocks=[]
    for position,line in enumerate(lines):
        if line.strip()!='run: |':continue
        indentation=len(line)-len(line.lstrip());body=[]
        for candidate in lines[position+1:]:
            if candidate.strip() and len(candidate)-len(candidate.lstrip())<=indentation:break
            body.append(candidate)
        blocks.append(textwrap.dedent('\n'.join(body)).rstrip()+'\n')
    return blocks


def matching_block(needle):
    matches=[block for block in run_blocks() if needle in block]
    assert len(matches)==1
    return matches[0]


def test_publication_is_manual_main_only_and_permissions_are_minimal():
    workflow=WORKFLOW.read_text()
    triggers=workflow.split('\npermissions:',1)[0]
    assert 'workflow_dispatch:' in triggers and 'push:' not in triggers and 'pull_request:' not in triggers
    assert 'default: false' in triggers and 'inputs.confirm' in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert 'uses: ./.github/workflows/ci.yml' in workflow and 'needs: checks' in workflow
    assert 'needs: [checks, build]' in workflow
    assert workflow.count('packages: write')==2
    assert 'contents: write' not in workflow and 'id-token: write' not in workflow
    assert 'secrets.GITHUB_TOKEN' in workflow and 'secrets.GHCR' not in workflow
    assert 'cancel-in-progress: false' in workflow
    assert 'workflow_call:' in (ROOT/'.github/workflows/ci.yml').read_text()


def test_native_matrix_verifies_both_architectures_before_version_promotion():
    workflow=WORKFLOW.read_text()
    assert 'architecture: amd64' in workflow and 'runner: ubuntu-24.04' in workflow
    assert 'architecture: arm64' in workflow and 'runner: ubuntu-24.04-arm' in workflow
    assert 'setup-qemu' not in workflow
    assert 'load: true' in workflow and 'push: false' in workflow
    assert workflow.index('Verify image identity')<workflow.index('docker push "$tag"')
    assert "matrix.architecture == 'amd64'" in workflow
    assert "report['passed'] == report['total'] == 14" in workflow
    assert '--network none --read-only' in workflow and '/health' in workflow
    assert "health['app_name'] == '冒险的梦想家'" in workflow
    assert "health['version'] == os.environ['EXPECTED_VERSION']" in workflow
    assert '<title>冒险的梦想家 · A股研究工作台</title>' in workflow
    assert 'worker_started' in workflow and 'source_credentials_mounted' in workflow
    assert 'v$VERSION' in workflow and ':latest' not in workflow
    assert 'pull_reference' in workflow and 'release_digest' in workflow


def test_actions_are_pinned_and_all_inline_shell_and_python_parse():
    for line in WORKFLOW.read_text().splitlines():
        if 'uses:' in line and './.github/' not in line:
            assert re.search(r'@[0-9a-f]{40}(?:\s|$)',line),line
    for block in run_blocks():
        checked=subprocess.run(['bash','-n'],input=block,capture_output=True,text=True)
        assert checked.returncode==0,checked.stderr
        for code in re.findall(r"(?:python3 -|cat .*?) <<'PY'\n(.*?)^PY$",block,re.MULTILINE|re.DOTALL):
            compile(code,'publication-inline.py','exec')


@pytest.fixture
def registry(tmp_path):
    folder=tmp_path/'release-evidence';folder.mkdir()
    for architecture,digest in [('amd64',AMD),('arm64',ARM)]:
        (folder/f'platform-{architecture}.json').write_text(json.dumps({
            'architecture':architecture,'digest':digest,'git_commit':REVISION,'version':'0.1.1','image':IMAGE}))
    bin_dir=tmp_path/'bin';bin_dir.mkdir()
    docker=bin_dir/'docker'
    docker.write_text('#!'+__import__('sys').executable+'\n'+textwrap.dedent('''
        import json, os, pathlib, sys
        arguments=sys.argv[1:]
        with open('docker-command-log.jsonl','a') as stream:stream.write(json.dumps(arguments)+'\\n')
        if 'create' in arguments:sys.exit(0)
        if '--raw' in arguments:
            data={'manifests':[{'platform':{'os':'linux','architecture':architecture},'digest':os.environ[key]}
                  for architecture,key in [('amd64','AMD64_DIGEST'),('arm64','ARM64_DIGEST')]]}
            print(json.dumps(data));sys.exit(0)
        if os.environ.get('REGISTRY_STATE')=='present':print('Digest: '+os.environ['INDEX_DIGEST']);sys.exit(0)
        if os.environ.get('REGISTRY_STATE')=='forbidden':print('403 Forbidden',file=sys.stderr);sys.exit(1)
        if os.environ.get('REGISTRY_STATE')=='missing':print('manifest unknown: not found',file=sys.stderr);sys.exit(1)
        print('Name: example\\nDigest: '+os.environ['INDEX_DIGEST'])
    '''));docker.chmod(0o600)
    # Read the fake through Python, so noexec /tmp mounts cannot fall through
    # PATH to a real Docker CLI. BASH_ENV applies only to these child shells.
    shell_env=tmp_path/'fake-registry-shell.sh'
    shell_env.write_text('docker() { "$FAKE_REGISTRY_PYTHON" "$FAKE_REGISTRY_SCRIPT" "$@"; }\n')
    env=os.environ|{'BASH_ENV':str(shell_env),
        'FAKE_REGISTRY_PYTHON':__import__('sys').executable,'FAKE_REGISTRY_SCRIPT':str(docker),
        'IMAGE':IMAGE,'GITHUB_SHA':REVISION,'GITHUB_OUTPUT':str(tmp_path/'output'),
        'GITHUB_STEP_SUMMARY':str(tmp_path/'summary'),'VERSION':'0.1.1',
        'AMD64_DIGEST':AMD,'ARM64_DIGEST':ARM,'INDEX_DIGEST':INDEX}
    return tmp_path,env


@pytest.mark.parametrize('state,allowed',[('present',False),('forbidden',False),('missing',True)])
def test_tag_guard_requires_positive_absence_and_never_overwrites(registry,state,allowed):
    folder,env=registry
    result=subprocess.run(['bash','-c',matching_block('Version absence could not be established')],
        cwd=folder,env=env|{'REGISTRY_STATE':state},capture_output=True,text=True)
    assert (result.returncode==0)==allowed
    commands=[json.loads(line) for line in (folder/'docker-command-log.jsonl').read_text().splitlines()]
    assert all('create' not in args for args in commands)


@pytest.mark.parametrize('broken',['git_commit','version','image','digest'])
def test_untrusted_or_mixed_platform_receipt_blocks_registry_promotion(registry,broken):
    folder,env=registry;path=folder/'release-evidence/platform-arm64.json';data=json.loads(path.read_text())
    data[broken]={'git_commit':'e'*40,'version':'0.2.0','image':'example.invalid/elsewhere','digest':'not-sha'}[broken]
    path.write_text(json.dumps(data))
    result=subprocess.run(['bash','-c',matching_block('Version absence could not be established')],
        cwd=folder,env=env|{'REGISTRY_STATE':'missing'},capture_output=True,text=True)
    assert result.returncode!=0 and not (folder/'docker-command-log.jsonl').exists()


def test_manifest_uses_exact_verified_digests_and_records_version_commit_and_pull_reference(registry):
    folder,env=registry
    result=subprocess.run(['bash','-c',matching_block('platform_digests')],cwd=folder,env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    data=json.loads((folder/'release-evidence/release-image.json').read_text())
    assert data['version']=='0.1.1' and data['git_commit']==REVISION
    assert data['release_digest']==INDEX and data['pull_reference']==IMAGE+'@'+INDEX
    assert data['platform_digests']=={'linux/amd64':AMD,'linux/arm64':ARM}
    assert data['deployed'] is False and data['anonymous_pull_verified'] is False
    commands=[json.loads(line) for line in (folder/'docker-command-log.jsonl').read_text().splitlines()]
    create=next(command for command in commands if 'create' in command)
    assert IMAGE+'@'+AMD in create and IMAGE+'@'+ARM in create
    assert IMAGE+':v0.1.1' in create and IMAGE+':sha-'+REVISION in create


def test_runtime_has_traceable_oci_labels_without_changing_legacy_data_paths():
    dockerfile=(ROOT/'deploy/Dockerfile').read_text()
    for field in ('title','source','version','revision','created'):
        assert 'org.opencontainers.image.'+field in dockerfile
    assert 'https://github.com/DreamerXX0301/ad-market-review' in dockerfile
    assert 'AS runtime' in dockerfile and 'USER 10001:10001' in dockerfile
    assert 'SHIXU_DATA_DIR=/var/lib/shixu' in dockerfile
    assert 'COPY deploy/create_secret.py /app/deploy/create_secret.py' in dockerfile


def test_fresh_install_compose_is_standalone_pulls_version_and_requires_explicit_worker(tmp_path):
    path=ROOT/'deploy/docker-compose.yml';text=path.read_text()
    assert 'include:' not in text and 'extends:' not in text
    env=os.environ.copy();env.pop('SHIXU_IMAGE_REF',None)
    env.update(SHIXU_MASTER_KEY_FILE=str(tmp_path/'unused-private-key'),SHIXU_HTTP_PORT='18789')
    command=['docker','compose','-p','brand-install-fixture','-f',str(path),'config','--format','json']
    run=subprocess.run(command,env=env,text=True,capture_output=True)
    assert run.returncode==0,run.stderr
    config=json.loads(run.stdout)
    assert all(service['image']==IMAGE+':v0.1.1' and 'build' not in service for service in config['services'].values())
    assert 'worker' not in config['services']
    api=config['services']['api'];assert api['ports'][0]['host_ip']=='127.0.0.1'
    assert any(m['target']=='/var/lib/shixu/published' and m['read_only'] for m in api['volumes'])
    run=subprocess.run(command[:2]+['--profile','worker']+command[2:],env=env,text=True,capture_output=True)
    assert run.returncode==0,run.stderr
    worker=json.loads(run.stdout)['services']['worker']
    assert worker['profiles']==['worker'] and worker['deploy']['replicas']==1


def test_ci_test_image_includes_publication_checks():
    text=(ROOT/'deploy/Dockerfile.test').read_text()
    assert 'scripts/test_publish_image.py' in text and 'deploy/docker-compose.yml' in text
    assert '.github/workflows/publish-image.yml' in text
