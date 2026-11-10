"""Synthetic native startup checks: no real deployment, data or provider access."""
import concurrent.futures
import ctypes
import errno
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import time

import pytest


SPEC = importlib.util.spec_from_file_location('native_service_under_test', Path(__file__).with_name('native_service.py'))
native = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = native
SPEC.loader.exec_module(native)

WORKER = '''import fcntl,os,signal,time
from pathlib import Path
with (Path(os.environ['SHIXU_WORK_DIR'])/'worker.lock').open('a+b') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 with (Path(os.environ['SHIXU_WORK_DIR'])/'test-starts').open('a') as f:f.write('worker\\n')
 signal.signal(signal.SIGTERM,lambda *_:exit(0))
 while True:time.sleep(.05)
'''
API = '''import json,os,signal,sys
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  body=json.dumps({'status':'ok','application':'ad-market-review','version':'0.1.13'}).encode()
  self.send_response(200);self.end_headers();self.wfile.write(body)
 def log_message(self,*args):pass
server=HTTPServer(('127.0.0.1',int(sys.argv[sys.argv.index('--port')+1])),Handler)
with (Path(os.environ['SHIXU_WORK_DIR'])/'test-starts').open('a') as f:f.write('api\\n')
signal.signal(signal.SIGTERM,lambda *_:os._exit(0))
server.serve_forever()
'''


@pytest.fixture
def installation(tmp_path):
    root = (tmp_path / 'workspace').resolve()
    backend = root / 'release/code/backend'
    (backend / 'app/migrations').mkdir(parents=True)
    (backend / 'app/__init__.py').write_text('')
    (backend / 'app/worker.py').write_text(WORKER)
    (backend / 'uvicorn.py').write_text(API)
    migration = backend / 'app/migrations/029_test.sql'
    migration.write_text('-- synthetic migration; never executed by launcher\n')
    (backend.parent / 'VERSION').write_text('0.1.13\n')
    environment = root / 'release/python-environment'
    (environment / 'bin').mkdir(parents=True)
    (environment / 'bin/python').symlink_to(sys.executable)
    (environment / 'pyvenv.cfg').write_text('home = ' + str(Path(sys.executable).resolve().parent) + '\ninclude-system-site-packages = true\n')
    data = root / 'release/final-data'
    roots = {name: data / name for name in ('business', 'published', 'raw', 'work', 'credentials')}
    for path in roots.values():
        path.mkdir(parents=True)
    database = roots['business'] / 'app.sqlite3'
    with sqlite3.connect(database) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA recursive_triggers=ON')
        connection.execute('PRAGMA busy_timeout=2000')
        connection.execute('PRAGMA user_version=29')
        connection.execute('CREATE TABLE _schema_migration(name TEXT PRIMARY KEY,sha256 TEXT)')
        connection.execute('INSERT INTO _schema_migration VALUES(?,?)', (migration.name, native.sha(migration)))
        connection.execute('CREATE TABLE preserved_fact(value TEXT)')
        connection.execute("INSERT INTO preserved_fact VALUES('unchanged')")
    key = tmp_path / 'external-original-key'
    key.write_text('synthetic-file-not-a-real-provider-key')
    for _ in range(20):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
            # An IPv6-only listener can share an IPv4 ephemeral candidate;
            # production deliberately rejects any listener on its chosen port.
            if not native.Inspector().listeners(port):
                break
    else:
        pytest.fail('未找到合成测试专用的空闲端口')
    manifest_path = root / 'release/candidate-manifest.json'
    control = root / 'data/work/runtime/local-service.json'
    control.parent.mkdir(parents=True)
    value = {'version': '0.1.13', 'schema': 29, 'api_source': str(backend), 'worker_source': str(backend),
             'python_environment': str(environment), 'data_dir': str(data), 'storage_roots': {k: str(v) for k, v in roots.items()},
             'sqlite_path': str(database), 'master_key_path': str(key), 'url': 'http://127.0.0.1:' + str(port),
             'source_git_commit': '1' * 40, 'candidate_manifest': str(manifest_path), 'selection_cpu_workers': 0,
             'processes': {'api': None, 'worker': None}}
    instances = []

    def freeze():
        files = {str(path.relative_to(backend.parent)): {'bytes': path.stat().st_size, 'sha256': native.sha(path)}
                 for path in backend.parent.rglob('*') if path.is_file()}
        manifest_path.write_text(json.dumps({'version': value['version'], 'candidate_commit': value['source_git_commit'],
                                            'dirty': False, 'source_matches_merged_master': True, 'files': files}))
        value['candidate_manifest_sha256'] = native.sha(manifest_path)
        native.atomic_json(control, value)

    def service():
        instance = native.NativeService(control, workspace=root, timeout=8)
        instances.append(instance)
        return instance

    freeze()
    yield {'root': root, 'backend': backend, 'data': data, 'database': database, 'value': value,
           'control': control, 'freeze': freeze, 'service': service, 'key': key, 'port': port}
    for instance in instances:
        for child in instance.children.values():
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=4)
                except native.subprocess.TimeoutExpired:
                    child.kill()  # Test-owned synthetic processes only.
                    child.wait(timeout=4)


def test_cold_start_status_and_idempotent_repeat_preserve_database(installation, monkeypatch):
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:9')
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:9')
    monkeypatch.setenv('NO_PROXY', '')
    monkeypatch.setenv('no_proxy', '')
    monkeypatch.setenv('COLUMNS', '40')
    item = installation
    before = native.sha(item['database'])
    first = item['service']().execute('start')
    assert first['status'] == 'running' and first['started'] == ['api', 'worker']
    second = item['service']().execute('start')
    assert second['processes'] == first['processes'] and second['started'] == []
    assert item['service']().execute('status')['status'] == 'running'
    assert native.sha(item['database']) == before
    assert (item['data'] / 'work/test-starts').read_text().splitlines() == ['api', 'worker']
    assert first['new_jobs_created'] == 0 and first['migration_executed'] is False


def test_concurrent_starts_create_one_api_and_one_worker(installation):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: installation['service']().execute('start'), range(2)))
    assert results[0]['processes'] == results[1]['processes']
    assert sorted(len(result['started']) for result in results) == [0, 2]
    assert (installation['data'] / 'work/test-starts').read_text().splitlines() == ['api', 'worker']


def test_stop_repeat_and_restart_preserve_database(installation):
    item = installation
    before = native.sha(item['database'])
    item['service']().execute('start')
    stopped = item['service']().execute('stop')
    assert stopped['status'] == 'stopped' and stopped['stopped'] == ['api', 'worker']
    assert item['service']().execute('status')['status'] == 'stopped'
    assert item['service']().execute('stop')['stopped'] == []
    assert json.loads(item['control'].read_text())['processes'] == {}
    assert item['service']().execute('start')['status'] == 'running'
    assert native.sha(item['database']) == before


def test_stop_stale_dead_pids_never_sends_signals(installation, monkeypatch):
    installation['value']['processes'] = {'api': 99999991, 'worker': 99999992}
    installation['freeze']()
    monkeypatch.setattr(native.os, 'kill', lambda *_: pytest.fail('不得向过期 PID 发送信号'))
    assert installation['service']().execute('stop')['status'] == 'stopped'


@pytest.mark.parametrize('role', ['api', 'worker'])
def test_stop_partial_service(installation, role):
    service = installation['service']()
    service.execute('start')
    other = 'worker' if role == 'api' else 'api'
    service.children[other].terminate()
    service.children[other].wait(timeout=4)
    assert installation['service']().execute('stop')['stopped'] == [role]


def test_stop_foreign_listener_preserves_worker(installation):
    service = installation['service']()
    service.execute('start')
    service.children['api'].terminate()
    service.children['api'].wait(timeout=4)
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', installation['port']))
        listener.listen()
        with pytest.raises(native.ServiceError, match='端口'):
            installation['service']().execute('stop')
        assert service.children['worker'].poll() is None


def test_stop_foreign_lock_preserves_api(installation):
    service = installation['service']()
    service.execute('start')
    service.children['worker'].terminate()
    service.children['worker'].wait(timeout=4)
    with (installation['data'] / 'work/worker.lock').open('a+b') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(native.ServiceError, match='锁'):
            installation['service']().execute('stop')
        assert service.children['api'].poll() is None


def test_stop_reused_recorded_token_preserves_both_processes(installation):
    service = installation['service']()
    service.execute('start')
    value = json.loads(installation['control'].read_text())
    value['native_process_tokens']['worker'] = 'reused-token'
    native.atomic_json(installation['control'], value)
    with pytest.raises(native.ServiceError, match='启动时间'):
        installation['service']().execute('stop')
    assert all(child.poll() is None for child in service.children.values())


@pytest.mark.parametrize('change', ['token', 'control'])
def test_stop_rechecks_identity_and_control_before_signalling(installation, monkeypatch, change):
    service = installation['service']()
    service.execute('start')
    service.load()
    found = service.inspect()
    original = service.inspector.read

    def changed(pid):
        process = original(pid)
        if change == 'token':
            process.token = 'reused-token'
        else:
            value = json.loads(installation['control'].read_text())
            native.atomic_json(installation['control'], {**value, 'changed': True})
        return process

    monkeypatch.setattr(service.inspector, 'read', changed)
    with pytest.raises(native.ServiceError, match='变化'):
        service.stop(found)
    assert all(child.poll() is None for child in service.children.values())


def test_stop_timeout_never_kills_or_claims_stopped(installation):
    item = installation
    (item['backend'] / 'app/worker.py').write_text(
        WORKER.replace('lambda *_:exit(0)', 'signal.SIG_IGN'))
    item['freeze']()
    service = item['service']()
    running = service.execute('start')
    stopping = item['service']()
    stopping.timeout = .2
    with pytest.raises(native.ServiceError, match='等待停止超时.*未强制终止'):
        stopping.execute('stop')
    assert service.children['worker'].poll() is None
    assert json.loads(item['control'].read_text())['processes'] == running['processes']


def test_process_identity_is_independent_of_terminal_language(monkeypatch):
    monkeypatch.setenv('LC_ALL', 'zh_CN.UTF-8')
    chinese = native.Inspector().read(os.getpid())
    monkeypatch.setenv('LC_ALL', 'C')
    english = native.Inspector().read(os.getpid())
    assert chinese.token == english.token and chinese.argv == english.argv


def test_stale_dead_pids_can_start_without_signalling_any_old_pid(installation):
    installation['value']['processes'] = {'api': 99999991, 'worker': 99999992}
    installation['freeze']()
    assert installation['service']().execute('start')['status'] == 'running'


def test_live_foreign_pid_refused_without_terminating_it(installation):
    installation['value']['processes']['api'] = os.getpid()
    installation['freeze']()
    with pytest.raises(native.ServiceError, match='PID'):
        installation['service']().execute('start')
    assert native.Inspector().read(os.getpid())


def test_occupied_port_refused_without_signalling_listener(installation):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', installation['port']))
        listener.listen()
        with pytest.raises(native.ServiceError, match='端口'):
            installation['service']().execute('start')
        assert listener.fileno() >= 0


def test_foreign_worker_lock_refused(installation):
    with (installation['data'] / 'work/worker.lock').open('a+b') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(native.ServiceError, match='锁'):
            installation['service']().execute('start')


@pytest.mark.parametrize('change', ['schema', 'checksum', 'missing'])
def test_unaccepted_migrations_refused_without_running_sql(installation, change):
    item = installation
    with sqlite3.connect(item['database']) as connection:
        if change == 'schema':
            connection.execute('PRAGMA user_version=28')
        elif change == 'checksum':
            connection.execute("UPDATE _schema_migration SET sha256='wrong'")
        else:
            connection.execute('DELETE FROM _schema_migration')
    before = native.sha(item['database'])
    with pytest.raises(native.ServiceError, match='迁移'):
        item['service']().execute('start')
    assert native.sha(item['database']) == before
    assert not (item['data'] / 'work/test-starts').exists()


def test_changed_source_sha_refused(installation):
    (installation['backend'] / 'app/worker.py').write_text('changed')
    with pytest.raises(native.ServiceError, match='SHA'):
        installation['service']().execute('start')


def test_wrong_data_paths_refused(installation):
    installation['value']['storage_roots']['work'] = str(installation['data'] / 'raw')
    installation['freeze']()
    with pytest.raises(native.ServiceError, match='存储路径'):
        installation['service']().execute('start')


def test_missing_original_key_does_not_create_new_key(installation):
    installation['key'].unlink()
    with pytest.raises(native.ServiceError):
        installation['service']().execute('start')
    assert not installation['key'].exists()


def test_child_worker_failure_cleans_only_api_started_in_same_call(installation):
    (installation['backend'] / 'app/worker.py').write_text('raise SystemExit(1)\n')
    installation['freeze']()
    service = installation['service']()
    with pytest.raises(native.ServiceError, match='启动失败'):
        service.execute('start')
    assert all(child.poll() is not None for child in service.children.values())
    assert native.Inspector().listeners(installation['port']) == []


def test_existing_api_survives_new_worker_failure(installation):
    item = installation
    (item['backend'] / 'app/worker.py').write_text('raise SystemExit(1)\n')
    item['freeze']()
    existing = item['service']()
    existing.load(full=True)
    child = existing.spawn('api')
    deadline = time.monotonic() + 8
    while not existing.health():
        assert child.poll() is None and time.monotonic() < deadline
        time.sleep(.1)
    item['value']['processes']['api'] = child.pid
    item['freeze']()
    new = item['service']()
    with pytest.raises(native.ServiceError, match='启动失败'):
        new.execute('start')
    assert child.poll() is None and existing.health()
    assert list(new.children) == ['worker']


@pytest.mark.parametrize('exited', [True, False])
def test_owned_child_inspection_error_only_normalized_after_confirmed_exit(installation, monkeypatch, exited):
    service = installation['service']()

    class Child:
        pid = 123
        checks = 0

        def poll(self):
            self.checks += 1
            return 1 if exited and self.checks > 1 else None

    child = Child()
    monkeypatch.setattr(service, 'inspect', lambda: {})
    monkeypatch.setattr(service, 'spawn', lambda _role: child)
    monkeypatch.setattr(service, 'clean_own_failed_start', lambda: None)

    def refuse(_pid):
        raise native.ServiceError('synthetic-unverifiable-identity')

    monkeypatch.setattr(service.inspector, 'read', refuse)
    expected = '启动失败' if exited else 'synthetic-unverifiable-identity'
    with pytest.raises(native.ServiceError, match=expected):
        service.execute('start')
    assert child.checks == 2


def test_identity_requires_data_key_and_start_token(installation):
    service = installation['service']()
    service.load()
    environment = {key: value for key, value in service.environment.items() if key.startswith('SHIXU_') or key == 'PYTHONPATH'}
    process = native.Process(55, [str(service.python), *service.arguments('worker')], service.backend, environment, 'token')
    assert service.matches(process, 'worker')
    process.environment = {**environment, 'SHIXU_DATA_DIR': '/different-data'}
    assert not service.matches(process, 'worker')
    process.environment = {**environment, 'SHIXU_CREDENTIAL_MASTER_KEY_FILE': '/different-key'}
    assert not service.matches(process, 'worker')


def test_environment_discards_unrelated_provider_credentials(installation, monkeypatch):
    monkeypatch.setenv('FUYAO_API_KEY', 'synthetic-secret-not-for-child')
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-secret-not-for-child')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic-secret-not-for-child')
    monkeypatch.setenv('SHIXU_AI_READ_SQLITE_PATH', '/wrong-inherited-ai-database')
    monkeypatch.setenv('SHIXU_AI_SYSTEM_PROXY_ENABLED', '1')
    monkeypatch.setenv('SHIXU_SQLITE_PATH', '/wrong-inherited-database')
    service = installation['service']()
    service.load()
    assert not {'FUYAO_API_KEY', 'OPENAI_API_KEY', 'DEEPSEEK_API_KEY',
                'SHIXU_AI_READ_SQLITE_PATH', 'SHIXU_AI_SYSTEM_PROXY_ENABLED'} & service.environment.keys()
    assert service.environment['SHIXU_SQLITE_PATH'] == str(installation['database'])


def test_native_calendar_control_explicitly_disables_the_new_install_default(installation):
    with sqlite3.connect(installation['database']) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA recursive_triggers=ON')
        connection.execute('PRAGMA busy_timeout=2000')
        connection.execute('CREATE TABLE platform_setting (key TEXT PRIMARY KEY, value_json TEXT)')
        connection.execute("INSERT INTO platform_setting VALUES ('official-event-calendar-install-default-v1','true')")
    flag = 'SHIXU_EVENT_CALENDAR_ENABLED'
    service = installation['service']()
    service.load()
    assert flag not in service.process_environment('worker')
    installation['value']['event_calendar_enabled'] = False
    installation['freeze']()
    service = installation['service']()
    service.load()
    environment = service.process_environment('worker')
    assert environment[flag] == '0' and flag not in service.process_environment('api')
    process = native.Process(55, [str(service.python), *service.arguments('worker')], service.backend, environment.copy(), 'token')
    assert service.matches(process, 'worker')
    del process.environment[flag]
    assert not service.matches(process, 'worker')


@pytest.mark.parametrize('enabled', [False, True])
def test_ai_proxy_control_applies_to_child_identity(installation, monkeypatch, enabled):
    item = installation
    item['value']['ai_system_proxy_enabled'] = enabled
    item['freeze']()
    monkeypatch.setenv('SHIXU_AI_SYSTEM_PROXY_ENABLED', '0' if enabled else '1')
    service = item['service']()
    result = service.execute('start')
    assert service.environment.get('SHIXU_AI_SYSTEM_PROXY_ENABLED') == ('1' if enabled else None)
    for role, pid in result['processes'].items():
        process = service.inspector.read(pid)
        assert process.environment.get('SHIXU_AI_SYSTEM_PROXY_ENABLED') == ('1' if enabled else None)
        assert service.matches(process, role)
        process.environment['SHIXU_AI_SYSTEM_PROXY_ENABLED'] = '0' if enabled else '1'
        assert not service.matches(process, role)
        if enabled:
            process.environment.pop('SHIXU_AI_SYSTEM_PROXY_ENABLED')
            assert not service.matches(process, role)
    assert item['service']().execute('status')['status'] == 'running'


def test_default_ai_proxy_identity_accepts_disabled_legacy_process(installation):
    service = installation['service']()
    service.load()
    environment = {key: value for key, value in service.environment.items() if key.startswith('SHIXU_') or key == 'PYTHONPATH'}
    assert 'SHIXU_AI_SYSTEM_PROXY_ENABLED' not in environment
    process = native.Process(55, [str(service.python), *service.arguments('worker')], service.backend, environment, 'token')
    assert service.matches(process, 'worker')
    process.environment = {**environment, 'SHIXU_AI_SYSTEM_PROXY_ENABLED': '0'}
    assert service.matches(process, 'worker')
    process.environment = {**environment, 'SHIXU_AI_SYSTEM_PROXY_ENABLED': '1'}
    assert not service.matches(process, 'worker')


@pytest.mark.parametrize('enabled', [None, 'true', 'false', 0, 1, [], {}])
def test_invalid_ai_proxy_control_refused_without_starting(installation, enabled):
    installation['value']['ai_system_proxy_enabled'] = enabled
    installation['freeze']()
    service = installation['service']()
    with pytest.raises(native.ServiceError, match='AI 系统代理开关必须为布尔值'):
        service.execute('start')
    assert service.children == {}


@pytest.mark.parametrize('enabled_field', native.WORKER_FEATURE_FLAGS)
def test_news_control_only_reaches_worker_and_is_verified(installation, monkeypatch, enabled_field):
    item = installation
    item['value'][enabled_field] = True
    item['freeze']()
    for variable in native.WORKER_FEATURE_FLAGS.values():
        monkeypatch.setenv(variable, '1')

    service = item['service']()
    result = service.execute('start')
    for role, pid in result['processes'].items():
        process = service.inspector.read(pid)
        for field, variable in native.WORKER_FEATURE_FLAGS.items():
            expected = '1' if role == 'worker' and field == enabled_field else None
            assert process.environment.get(variable) == expected
        assert service.matches(process, role)
        variable = native.WORKER_FEATURE_FLAGS[enabled_field]
        if role == 'api':
            process.environment[variable] = '1'
        else:
            process.environment.pop(variable)
        assert not service.matches(process, role)
    assert item['service']().execute('status')['status'] == 'running'


@pytest.mark.parametrize('field', native.WORKER_FEATURE_FLAGS)
def test_invalid_news_control_refused_without_starting(installation, field):
    installation['value'][field] = 'true'
    installation['freeze']()
    service = installation['service']()
    with pytest.raises(native.ServiceError, match=field + ' 必须为布尔值'):
        service.execute('start')
    assert service.children == {}


def test_reused_start_token_refused_without_stopping_existing_children(installation):
    service = installation['service']()
    service.execute('start')
    value = json.loads(installation['control'].read_text())
    value['native_process_tokens']['api'] = 'different-start-time'
    native.atomic_json(installation['control'], value)
    with pytest.raises(native.ServiceError, match='启动时间'):
        installation['service']().execute('start')
    assert all(child.poll() is None for child in service.children.values())


def test_old_release_manifest_rejected(installation):
    installation['value']['source_git_commit'] = '2' * 40
    native.atomic_json(installation['control'], installation['value'])
    with pytest.raises(native.ServiceError, match='发行版本'):
        installation['service']().execute('start')


def test_missing_data_directory_never_creates_empty_database(installation):
    missing = installation['root'] / 'uninitialized-data'
    installation['value']['data_dir'] = str(missing)
    native.atomic_json(installation['control'], installation['value'])
    with pytest.raises(native.ServiceError):
        installation['service']().execute('start')
    assert not missing.exists()


@pytest.mark.parametrize('directory', [True, False])
def test_symlink_logs_never_overwrite_external_target(installation, directory):
    item = installation
    target = item['key'].parent / 'unrelated-file'
    target.write_text('preserve-me')
    logs = item['control'].parent / 'native-service-logs'
    if directory:
        logs.symlink_to(target.parent, target_is_directory=True)
    else:
        logs.mkdir()
        (logs / 'api.log').symlink_to(target)
    with pytest.raises((native.ServiceError, OSError)):
        item['service']().execute('start')
    assert target.read_text() == 'preserve-me'


def test_process_exiting_between_ps_and_cwd_is_not_a_foreign_process(monkeypatch):
    if sys.platform != 'darwin':
        pytest.skip('Darwin procargs/cwd race boundary')
    inspector = native.Inspector()
    original = inspector.run
    missing_cwd = False

    def run(arguments):
        nonlocal missing_cwd
        if arguments[0] == 'lsof':
            missing_cwd = True
            return ''
        if missing_cwd and 'stat=' in arguments:
            return ''
        return original(arguments)

    monkeypatch.setattr(inspector, 'run', run)
    assert inspector.read(os.getpid()) is None


@pytest.mark.parametrize('boundary', ['cmdline', 'environ', 'cwd'])
@pytest.mark.parametrize('outcome', ['zombie', 'gone', 'live', 'reused'])
def test_linux_proc_permission_race_only_accepts_confirmed_exit(monkeypatch, boundary, outcome):
    inspector = native.Inspector()
    monkeypatch.setattr(native.sys, 'platform', 'linux')
    denied = False
    probes = []
    process_root = Path('/proc/123')

    def run(arguments):
        if 'stat=' in arguments:
            return 'R' if not denied else {'zombie': 'Z', 'gone': '', 'live': 'S', 'reused': 'R'}[outcome]
        assert 'lstart=' in arguments
        return 'new-start-token' if denied and outcome == 'reused' else 'original-start-token'

    def read_bytes(path):
        nonlocal denied
        assert path.parent == process_root
        if path.name == boundary:
            denied = True
            raise PermissionError(errno.EACCES, 'synthetic exiting /proc access')
        return b'python\0-m\0app.worker\0' if path.name == 'cmdline' else b'SHIXU_DATA_DIR=/synthetic-only\0'

    original_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        nonlocal denied
        if path == process_root / 'cwd':
            if boundary == 'cwd':
                denied = True
                raise PermissionError(errno.EACCES, 'synthetic exiting cwd access')
            return Path('/synthetic-only')
        return original_resolve(path, *args, **kwargs)

    def probe(pid, sig):
        probes.append((pid, sig))
        assert (pid, sig) == (123, 0), 'Exit inspection must never send a terminating signal'
        assert outcome == 'gone'
        raise ProcessLookupError(errno.ESRCH, 'synthetic PID already reaped')

    monkeypatch.setattr(inspector, 'run', run)
    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    monkeypatch.setattr(Path, 'resolve', resolve)
    monkeypatch.setattr(native.os, 'kill', probe)
    if outcome in ('zombie', 'gone'):
        assert inspector.read(123) is None
    else:
        with pytest.raises(PermissionError):
            inspector.read(123)
    assert probes == ([(123, 0)] if outcome == 'gone' else [])


@pytest.mark.parametrize('probe_state', ['alive', 'denied', 'gone'])
def test_linux_empty_ps_output_does_not_prove_exit(monkeypatch, probe_state):
    inspector = native.Inspector()
    monkeypatch.setattr(inspector, 'run', lambda _arguments: '')

    def probe(pid, sig):
        assert (pid, sig) == (123, 0)
        if probe_state == 'denied':
            raise PermissionError(errno.EPERM, 'synthetic uninspectable live process')
        if probe_state == 'gone':
            raise ProcessLookupError(errno.ESRCH, 'synthetic absent process')

    monkeypatch.setattr(native.os, 'kill', probe)
    assert inspector.linux_exited_after_error(123) is (probe_state == 'gone')


def test_darwin_permission_error_is_not_normalized_by_linux_exit_guard(monkeypatch):
    inspector = native.Inspector()
    monkeypatch.setattr(native.sys, 'platform', 'darwin')
    monkeypatch.setattr(inspector, 'run', lambda arguments: 'S' if 'stat=' in arguments else 'same-token')
    monkeypatch.setattr(inspector, 'linux_exited_after_error', lambda _pid: pytest.fail('Linux-only guard'))

    def denied(*_args, **_kwargs):
        raise PermissionError(errno.EACCES, 'synthetic Darwin identity failure')

    monkeypatch.setattr(native, 'darwin_process_args', denied)
    with pytest.raises(PermissionError):
        inspector.read(123)


def test_stop_unverifiable_live_process_never_signals_or_changes_control(installation, monkeypatch):
    service = installation['service']()
    service.load()
    original = installation['control'].read_bytes()

    def denied(_pid):
        raise PermissionError(errno.EACCES, 'synthetic live identity failure')

    monkeypatch.setattr(service.inspector, 'read', denied)
    monkeypatch.setattr(native.os, 'kill', lambda *_args: pytest.fail('Unverified live PID must not be signalled'))
    previous = native.Process(123, [], Path('/synthetic-only'), {}, 'original-start-token')
    with pytest.raises(PermissionError):
        service.stop({'worker': previous})
    assert installation['control'].read_bytes() == original


class ProcargsSysctl:
    """Only synthetic bytes and errno; never read a real process environment."""

    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    def sysctl(self, mib, count, buffer, size, _new, _new_size):
        stage = 'size' if buffer is None else 'value'
        self.calls.append((stage, mib[2]))
        expected, result, length, error = next(self.outcomes)
        assert stage == expected and count == 3
        size._obj.value = length
        ctypes.set_errno(error)
        if result == 0 and buffer is not None:
            ctypes.memmove(buffer, b'x' * length, length)
        return result


def procargs_call(library, *, tokens=None, dead=False):
    values = iter(tokens or ['same-token'] * 4)
    return native.darwin_process_args(123, 'same-token', read_token=lambda: next(values),
                                      is_dead=lambda: dead, library=library)


@pytest.mark.parametrize('error', [errno.ENOMEM, errno.EIO])
def test_darwin_procargs_transient_error_requeries_size_before_success(error):
    library = ProcargsSysctl([('size', 0, 4, 0), ('value', -1, 8, error),
                             ('size', 0, 8, 0), ('value', 0, 8, 0)])
    assert procargs_call(library) == b'x' * 8
    assert library.calls == [('size', 123), ('value', 123)] * 2


def test_darwin_procargs_size_enomem_is_also_bounded_to_fresh_query():
    library = ProcargsSysctl([('size', -1, 0, errno.ENOMEM),
                             ('size', 0, 4, 0), ('value', 0, 4, 0)])
    assert procargs_call(library) == b'x' * 4
    assert library.calls == [('size', 123), ('size', 123), ('value', 123)]


@pytest.mark.parametrize('error', [errno.ENOMEM, errno.EIO])
def test_darwin_procargs_persistent_error_is_bounded_and_fails_closed(error):
    library = ProcargsSysctl([('size', 0, 4, 0), ('value', -1, 8, error)] * 3)
    with pytest.raises(native.ServiceError, match='身份核验'):
        procargs_call(library)
    assert len(library.calls) == 6


@pytest.mark.parametrize('error', [errno.EPERM, errno.EACCES, errno.EINVAL])
def test_darwin_procargs_other_errors_are_not_retried(error):
    library = ProcargsSysctl([('size', 0, 4, 0), ('value', -1, 4, error)])
    with pytest.raises(native.ServiceError, match='身份核验'):
        procargs_call(library)
    assert len(library.calls) == 2


@pytest.mark.parametrize('error', [errno.ENOMEM, errno.EIO])
def test_darwin_procargs_reused_pid_is_refused_before_retry(error):
    library = ProcargsSysctl([('size', 0, 4, 0), ('value', -1, 8, error)])
    with pytest.raises(native.ServiceError, match='身份核验'):
        procargs_call(library, tokens=['different-token'])
    assert len(library.calls) == 2


def test_darwin_procargs_start_token_is_rechecked_after_success():
    library = ProcargsSysctl([('size', 0, 4, 0), ('value', 0, 4, 0)])
    with pytest.raises(native.ServiceError, match='身份核验'):
        procargs_call(library, tokens=['different-token'])


def test_darwin_procargs_exited_process_still_returns_none():
    library = ProcargsSysctl([('size', 0, 4, 0), ('value', -1, 4, errno.ESRCH)])
    assert procargs_call(library, dead=True) is None
    assert len(library.calls) == 2


def test_linux_kernel_lock_owner_is_not_a_file_opener():
    lines = '1: FLOCK ADVISORY WRITE 42 00:af:99 0 EOF\n2: -> FLOCK ADVISORY WRITE 88 00:af:99 0 EOF\n'
    assert native.linux_lock_owners(lines, (0, 175, 99)) == [42]
    assert native.linux_lock_owners(lines, (0, 175, 100)) == []


@pytest.mark.parametrize('record', ['OFDLCK ADVISORY WRITE -1', 'POSIX ADVISORY WRITE 42', 'FLOCK ADVISORY READ 42'])
def test_linux_unsupported_or_ownerless_lock_refused(record):
    with pytest.raises(native.ServiceError, match='锁'):
        native.linux_lock_owners('1: ' + record + ' 00:af:99 0 EOF', (0, 175, 99))


def test_linux_pid_one_is_inspected_instead_of_treated_as_dead(monkeypatch):
    if not sys.platform.startswith('linux'):
        pytest.skip('Linux PID namespace boundary')
    process = native.Inspector().read(1)
    assert process and process.pid == 1


def test_linux_extra_open_file_descriptor_is_not_a_second_lock_owner(installation):
    if not sys.platform.startswith('linux'):
        pytest.skip('Linux kernel FLOCK ownership boundary')
    item = installation
    first = item['service']().execute('start')
    lock = item['data'] / 'work/worker.lock'
    opener = native.subprocess.Popen([sys.executable, '-c',
        "import sys,time; f=open(sys.argv[1],'rb'); print('ready',flush=True); time.sleep(60)", str(lock)],
        stdout=native.subprocess.PIPE, text=True)
    try:
        assert opener.stdout.readline().strip() == 'ready'
        again = item['service']().execute('start')
        assert opener.poll() is None
    finally:
        opener.terminate()
        opener.wait(timeout=4)
    assert again['processes'] == first['processes'] and again['started'] == []


def test_foreign_true_lock_owner_is_never_stopped(installation):
    path = installation['data'] / 'work/worker.lock'
    holder = native.subprocess.Popen([sys.executable, '-c',
        "import fcntl,sys,time; f=open(sys.argv[1],'a+b'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); time.sleep(60)", str(path)],
        stdout=native.subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == 'ready'
        with pytest.raises(native.ServiceError, match='锁'):
            installation['service']().execute('start')
        assert holder.poll() is None
    finally:
        holder.terminate()
        holder.wait(timeout=4)


def test_linux_unreadable_lock_evidence_fails_closed(installation, monkeypatch):
    path = installation['data'] / 'work/worker.lock'
    path.touch()
    original = Path.read_text

    def read_text(self, *args, **kwargs):
        if str(self) == '/proc/locks':
            raise PermissionError('synthetic namespace restriction')
        return original(self, *args, **kwargs)

    monkeypatch.setattr(native.sys, 'platform', 'linux')
    monkeypatch.setattr(Path, 'read_text', read_text)
    with pytest.raises(PermissionError):
        native.Inspector().lock_holders(path)
