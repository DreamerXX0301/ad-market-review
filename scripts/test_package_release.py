"""Reproducible source exports use only synthetic local Git repositories."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('package_release',ROOT/'scripts/package_release.py')
package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)


def git(repo,*args):
    done=subprocess.run(['git','-C',str(repo),*args],text=True,capture_output=True,
        env=os.environ|{'GIT_AUTHOR_DATE':'2026-10-21T00:00:00+00:00',
                       'GIT_COMMITTER_DATE':'2026-10-21T00:00:00+00:00'})
    assert done.returncode==0,done.stderr
    return done.stdout.strip()


def commit(repo):
    git(repo,'add','--all');git(repo,'commit','--quiet','-m','synthetic source fixture')
    return git(repo,'rev-parse','HEAD')


@pytest.fixture
def repository(tmp_path):
    repo=tmp_path/'source';repo.mkdir();git(repo,'init','--quiet')
    git(repo,'config','user.email','fixture@example.invalid');git(repo,'config','user.name','Fixture')
    (repo/'VERSION').write_text('0.1.0\n')
    (repo/'README.md').write_text('# Synthetic source\n')
    (repo/'backend/app/migrations').mkdir(parents=True)
    (repo/'backend/app/migrations/001_initial.sql').write_text('CREATE TABLE sample(id INTEGER);\n')
    (repo/'backend/pyproject.toml').write_text('[project]\nname="fixture"\nversion="0.1.0"\n')
    (repo/'frontend/src/features/data').mkdir(parents=True)
    (repo/'frontend/package.json').write_text('{"version":"0.1.0"}\n')
    (repo/'frontend/src/features/data/DataCenter.tsx').write_text('export const name = "DataCenter";\n')
    (repo/'scripts').mkdir();script=repo/'scripts/dev.sh';script.write_text('#!/bin/sh\nexit 0\n');script.chmod(0o755)
    return repo,commit(repo)


def test_reproducible_package_and_manifest_bind_commit_versions_migrations(repository,tmp_path):
    repo,sha=repository
    first=package.package_release(repo,sha,tmp_path/'first')
    second=package.package_release(repo,sha,tmp_path/'second')
    assert first['sha256']==second['sha256']
    left=tmp_path/'first';right=tmp_path/'second'
    assert (left/first['archive']).read_bytes()==(right/second['archive']).read_bytes()
    assert (left/'source-manifest.json').read_bytes()==(right/'source-manifest.json').read_bytes()
    manifest=json.loads((left/'source-manifest.json').read_text())
    assert manifest['git_commit']==sha and manifest['version']=='0.1.0'
    assert manifest['working_tree_used'] is False and manifest['source_only'] is True
    assert manifest['image_published'] is False and manifest['test_results_included'] is False
    migration='backend/app/migrations/001_initial.sql'
    assert manifest['migration_sha256'][migration]==package.sha256((repo/migration).read_bytes()).hexdigest()
    with tarfile.open(left/first['archive']) as archive:
        members=archive.getmembers();names=[m.name for m in members]
        assert names==sorted(names)
        assert all(m.isfile() and m.uid==m.gid==0 and m.uname==m.gname=='' for m in members)
        assert all(m.mtime==manifest['commit_timestamp'] for m in members)
        assert archive.getmember('ad-market-review-0.1.0/scripts/dev.sh').mode==0o755
        assert archive.extractfile('ad-market-review-0.1.0/frontend/src/features/data/DataCenter.tsx')


def test_uncommitted_worktree_and_untracked_env_cannot_enter_commit_export(repository,tmp_path):
    repo,sha=repository
    first=package.package_release(repo,sha,tmp_path/'before')
    (repo/'README.md').write_text('Uncommitted content must not be exported')
    (repo/'.env').write_text('UNTRACKED_PRIVATE=synthetic-only')
    second=package.package_release(repo,sha,tmp_path/'after')
    assert first['sha256']==second['sha256']
    with tarfile.open(tmp_path/'after'/second['archive']) as archive:
        assert not any(m.name.endswith('/.env') for m in archive)
        assert archive.extractfile('ad-market-review-0.1.0/README.md').read()==b'# Synthetic source\n'


@pytest.mark.parametrize('unsafe',['data/business/app.sqlite3','secrets/master.key','backups/backup.json',
    'backend/.venv/bin/python','frontend/node_modules/dependency/index.js','frontend/dist/index.html',
    'docs/validation/private-evidence.json','docs/screenshots/private.jpg','deploy/container-checks.json',
    'frontend/api-contract-checks.json','frontend/.env.production','backend/database.duckdb',
    '.idea/workspace.xml','frontend/src/leak.enc'])
def test_dangerous_tracked_tree_aborts_before_output_creation(repository,tmp_path,unsafe):
    repo,_=repository;path=repo/unsafe;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('synthetic')
    sha=commit(repo)
    with pytest.raises(ValueError):package.package_release(repo,sha,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


@pytest.mark.parametrize('name',['/absolute.py','../outside.py','scripts/../../outside.py',
    'scripts\\windows.py','scripts//double.py','scripts/./dot.py','scripts/new\nline.py'])
def test_unsafe_archive_paths_are_rejected(name):
    with pytest.raises(ValueError):package.validate_path(name)


def test_symlink_and_submodule_are_not_release_source(repository,tmp_path):
    repo,_=repository;(repo/'scripts/linked.py').symlink_to('../README.md')
    sha=commit(repo)
    with pytest.raises(ValueError,match='符号链接'):package.package_release(repo,sha,tmp_path/'linked')
    with pytest.raises(ValueError):package.validate_path('backend/external','160000')


@pytest.mark.parametrize('kind',['home','windows','token','private_key','binary'])
def test_private_path_secret_or_unreviewed_binary_content_is_rejected(repository,tmp_path,kind):
    repo,_=repository
    values={'home':('/Us'+'ers/alice/private/file').encode(),
        'windows':('C:\\Us'+'ers\\alice\\private').encode(),
        'token':('sk-fu'+'yao-'+'A'*24).encode(),
        'private_key':('-----BEGIN '+'PRIVATE KEY-----').encode(),
        'binary':b'\x80\x00unreviewed'}
    (repo/'scripts/sensitive.py').write_bytes(values[kind]);sha=commit(repo)
    with pytest.raises(ValueError):package.package_release(repo,sha,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


def test_safe_env_example_with_placeholders_is_not_runtime_environment(repository,tmp_path):
    repo,_=repository;(repo/'deploy').mkdir()
    (repo/'deploy/.env.example').write_text('SHIXU_MASTER_KEY_FILE=/absolute/separate/master.key\n')
    sha=commit(repo);package.package_release(repo,sha,tmp_path/'allowed')


def test_public_changelog_is_included(repository,tmp_path):
    repo,_=repository;(repo/'CHANGELOG.md').write_text('# 0.1.0\nPublic release notes.\n')
    sha=commit(repo);result=package.package_release(repo,sha,tmp_path/'package')
    with tarfile.open(tmp_path/'package'/result['archive']) as archive:
        assert archive.extractfile('ad-market-review-0.1.0/CHANGELOG.md').read().startswith(b'# 0.1.0')


@pytest.mark.parametrize('bad',['HEAD','main','-option','a'*39,'A'*40])
def test_only_exact_commit_sha_is_accepted(repository,tmp_path,bad):
    repo,_=repository
    with pytest.raises(ValueError,match='40位'):package.package_release(repo,bad,tmp_path/'rejected')


@pytest.mark.parametrize('changed',['missing','invalid','frontend','backend'])
def test_project_version_must_exist_and_match_app_metadata(repository,tmp_path,changed):
    repo,_=repository
    if changed=='missing':(repo/'VERSION').unlink()
    elif changed=='invalid':(repo/'VERSION').write_text('v0.1.0\n')
    elif changed=='frontend':(repo/'frontend/package.json').write_text('{"version":"0.2.0"}')
    else:(repo/'backend/pyproject.toml').write_text('[project]\nversion="0.2.0"')
    sha=commit(repo)
    with pytest.raises(ValueError):package.package_release(repo,sha,tmp_path/'rejected')


def test_existing_bundle_is_never_overwritten(repository,tmp_path):
    repo,sha=repository;folder=tmp_path/'existing';folder.mkdir();(folder/'keep').write_text('keep')
    with pytest.raises(ValueError,match='不存在'):package.package_release(repo,sha,folder)
    assert (folder/'keep').read_text()=='keep'


def test_size_budget_is_enforced_before_export(repository,tmp_path,monkeypatch):
    repo,sha=repository;monkeypatch.setattr(package,'MAX_SOURCE_BYTES',5)
    with pytest.raises(ValueError,match='保护上限'):package.package_release(repo,sha,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


def test_ci_is_readonly_and_has_no_deployment_or_docker_socket():
    workflow=(ROOT/'.github/workflows/ci.yml').read_text()
    assert 'contents: read' in workflow and 'persist-credentials: false' in workflow
    assert 'pull_request:' in workflow and 'pull_request_target:' not in workflow
    assert 'branches: [main, develop]' in workflow
    assert 'docker.sock' not in workflow and 'docker login' not in workflow and 'docker push' not in workflow
    assert 'npm ci' in workflow and 'npm run format:check' in workflow and "*.test.mjs" in workflow
    assert 'deploy/Dockerfile.test' in workflow and '--network none' in workflow
    for line in workflow.splitlines():
        if 'uses:' in line:
            import re
            assert re.search(r'@[0-9a-f]{40}(?:\s|$)',line),line
    testing=(ROOT/'deploy/Dockerfile.test').read_text()
    for test in ('backend/tests','test_deployment.py','test_upgrade.py','test_package_release.py'):
        assert test in testing
    assert 'export_schema.py --check' in testing and 'COPY contracts /app/contracts' in testing
