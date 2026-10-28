#!/usr/bin/env python3
"""Start an already accepted native release; never install, migrate or fetch data.

The private deployment control selects the release and data, rather than this
checkout's current application code. Runtime paths and credentials stay private.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import time
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener


class ServiceError(RuntimeError):
    """A startup prerequisite failed; unrelated processes must stay untouched."""


def require(condition, message):
    if not condition:
        raise ServiceError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def ordinary(path, *, directory=False):
    path = Path(path).expanduser().absolute()
    require('..' not in path.parts, '部署路径不允许包含上级目录跳转')
    require(not path.is_symlink() and (path.is_dir() if directory else path.is_file()),
            '缺少既有部署文件或目录，请先完成版本部署')
    return path.resolve()


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.native-service-' + str(os.getpid()))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def startup_lock(control, timeout=30):
    # Share the deployment lock, so a shortcut cannot race a version cutover.
    with (control.parent / 'cutover.lock').open('a+b') as stream:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                require(time.monotonic() < deadline, '另一个启动或部署操作尚未结束')
                time.sleep(.05)
        yield


@dataclass
class Process:
    pid: int
    argv: list[str]
    cwd: Path
    environment: dict[str, str]
    token: str


def linux_lock_owners(lines, identity):
    """Kernel FLOCK owners, not processes that merely have the file open."""
    owners = set()
    for line in lines.splitlines():
        fields = line.split()
        if len(fields) < 8 or fields[1] == '->':
            continue  # A blocked waiter does not own the lock.
        device = fields[5].split(':')
        if len(device) != 3:
            continue
        try:
            actual = (int(device[0], 16), int(device[1], 16), int(device[2]))
        except ValueError:
            continue
        if actual != identity:
            continue
        require(fields[1:4] == ['FLOCK', 'ADVISORY', 'WRITE'] and fields[6:8] == ['0', 'EOF'],
                'worker 数据锁不是可核验的完整文件 FLOCK 写锁')
        require(fields[4].isdigit() and int(fields[4]) > 0, 'worker 数据锁缺少可核验的进程所有者')
        owners.add(int(fields[4]))
    return sorted(owners)


class Inspector:
    """Read process identity internally; never print commands or environments."""

    @staticmethod
    def run(arguments):
        result = subprocess.run(arguments, capture_output=True, text=True, check=False)
        require(result.returncode in (0, 1), '无法核验本机进程，请检查 ps/lsof 工具')
        return result.stdout

    def dead(self, pid):
        state = self.run(['ps', '-p', str(pid), '-o', 'stat=']).strip()
        return not state or state.startswith('Z')

    def read(self, pid):
        if type(pid) is not int or pid <= 0:
            return None
        if self.dead(pid):
            return None
        token = self.run(['ps', '-p', str(pid), '-o', 'lstart=']).strip()
        try:
            if sys.platform == 'darwin':
                library = ctypes.CDLL(None, use_errno=True)
                mib = (ctypes.c_int * 3)(1, 49, pid)  # KERN_PROCARGS2
                size = ctypes.c_size_t()
                first = library.sysctl(mib, 3, None, ctypes.byref(size), None, 0)
                if first != 0 and self.dead(pid):
                    return None
                require(first == 0, '无法核验既有进程的运行路径')
                buffer = ctypes.create_string_buffer(size.value)
                second = library.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0)
                if second != 0 and self.dead(pid):
                    return None
                require(second == 0, '进程在身份核验期间发生变化')
                raw = buffer.raw[:size.value]
                argc = struct.unpack_from('i', raw)[0]
                offset = raw.index(b'\0', 4) + 1
                while offset < len(raw) and raw[offset] == 0:
                    offset += 1
                parts = raw[offset:].split(b'\0')
                argv = [item.decode() for item in parts[:argc]]
                pairs = parts[argc:]
                names = [line[1:] for line in self.run(['lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn']).splitlines()
                         if line.startswith('n')]
                if len(names) != 1 and self.dead(pid):
                    return None
                require(len(names) == 1, '无法核验既有进程的工作目录')
                cwd = Path(names[0])
            elif sys.platform.startswith('linux'):
                proc = Path('/proc') / str(pid)
                argv = [item.decode() for item in (proc / 'cmdline').read_bytes().split(b'\0') if item]
                pairs = (proc / 'environ').read_bytes().split(b'\0')
                cwd = (proc / 'cwd').resolve()
            else:
                raise ServiceError('原生快捷启动仅支持 macOS/Linux')
            # Discard every unrelated variable, including possible provider keys.
            allowed = {'PYTHONPATH', 'SHIXU_DATA_DIR', 'SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR',
                       'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR', 'SHIXU_CREDENTIAL_DIR',
                       'SHIXU_CREDENTIAL_MASTER_KEY_FILE', 'SHIXU_SELECTION_CPU_WORKERS'}
            environment = {}
            for item in pairs:
                key, separator, value = item.partition(b'=')
                if separator and key.decode(errors='replace') in allowed:
                    environment[key.decode()] = value.decode()
            return Process(pid, argv, cwd, environment, token)
        except (FileNotFoundError, ProcessLookupError):
            return None

    def listeners(self, port):
        return sorted({int(line[1:]) for line in self.run(
            ['lsof', '-nP', '-iTCP:' + str(port), '-sTCP:LISTEN', '-Fp']).splitlines()
                       if line.startswith('p')})

    def workers(self, workspace):
        result = []
        # Linux otherwise truncates long release paths at the terminal width,
        # hiding the trailing module name and missing an existing writer.
        for line in self.run(['ps', '-ww', '-axo', 'pid=,command=']).splitlines():
            if '-m app.worker' not in line:
                continue
            process = self.read(int(line.strip().split(None, 1)[0]))
            worker_arguments = process and any(process.argv[index:index + 2] == ['-m', 'app.worker']
                                               for index in range(len(process.argv) - 1))
            if worker_arguments and process.cwd.is_relative_to(workspace):
                result.append(process)
        return result

    def lock_holders(self, path):
        if sys.platform.startswith('linux'):
            metadata = Path(path).stat()
            identity = (os.major(metadata.st_dev), os.minor(metadata.st_dev), metadata.st_ino)
            # Docker/PID namespaces can make lsof's fd list differ from lock
            # ownership. Missing or unreadable kernel evidence fails closed.
            return linux_lock_owners(Path('/proc/locks').read_text(), identity)
        return sorted({int(line[1:]) for line in self.run(['lsof', '-nP', '-Fp', str(path)]).splitlines()
                       if line.startswith('p') and int(line[1:]) != os.getpid()})


class NativeService:
    def __init__(self, control, *, workspace=None, inspector=None, timeout=30):
        self.control = ordinary(control)
        self.workspace = Path(workspace or Path(__file__).resolve().parents[1]).resolve()
        self.inspector = inspector or Inspector()
        self.timeout = timeout
        self.children = {}

    def load(self, *, full=False):
        self.control_sha = sha(self.control)
        self.value = json.loads(self.control.read_text())
        value = self.value
        require(re.fullmatch(r'[0-9a-f]{40}', value['source_git_commit'] or '') is not None
                and type(value['schema']) is int and value['schema'] > 0, '发行提交或 schema 配置不合法')
        self.backend = ordinary(value['api_source'], directory=True)
        require(self.backend == Path(value['worker_source']).resolve(), 'API 和 worker 必须属于同一已验收版本')
        self.release = self.backend.parent.parent
        require(self.backend.is_relative_to(self.workspace) and self.backend.name == 'backend', '部署源码超出当前工作区')
        manifest_path = ordinary(value['candidate_manifest'])
        require(sha(manifest_path) == value['candidate_manifest_sha256'], '冻结源码清单已变化，拒绝启动旧版或混合版本')
        manifest = json.loads(manifest_path.read_text())
        require(manifest['version'] == value['version'] and manifest.get('candidate_commit') == value['source_git_commit']
                and manifest.get('dirty') is False and manifest.get('source_matches_merged_master') is True,
                '控制配置与已验收发行版本不一致')
        self.code = self.backend.parent
        files = manifest['files']
        if full:
            actual = {str(path.relative_to(self.code)) for path in self.code.rglob('*') if path.is_file()}
            require(actual == set(files), '冻结源码或前端文件集合已变化')
            for name, item in files.items():
                relative = Path(name)
                require(not relative.is_absolute() and '..' not in relative.parts, '冻结源码包含越界路径')
                path = ordinary(self.code / relative)
                require(path.is_relative_to(self.code) and
                        all(not parent.is_symlink() for parent in (self.code / relative).parents if parent.is_relative_to(self.code)),
                        '冻结源码不允许符号链接')
                require(path.stat().st_size == item['bytes'] and sha(path) == item['sha256'], '冻结源码或前端 SHA 校验失败')
        self.python = Path(value['python_environment']).absolute() / 'bin/python'
        require(self.python.is_file() and os.access(self.python, os.X_OK)
                and (self.python.parent.parent / 'pyvenv.cfg').is_file(), '已部署的 Python 虚拟环境不存在')
        self.data = ordinary(value['data_dir'], directory=True)
        require(self.data.is_relative_to(self.workspace) and not self.data.is_relative_to(self.code), '运行数据路径不属于当前部署')
        roots = {name: ordinary(value['storage_roots'][name], directory=True)
                 for name in ('business', 'published', 'raw', 'work', 'credentials')}
        require(all(path == self.data / name for name, path in roots.items()), 'SHIXU 存储路径与部署配置不一致')
        self.database = ordinary(value['sqlite_path'])
        require(self.database == roots['business'] / 'app.sqlite3', '业务库路径与部署配置不一致')
        self.key = ordinary(value.get('master_key_path') or Path.home() / '.local/share/shixu/credential_master_key')
        require(not self.key.is_relative_to(self.workspace), '原外置主密钥必须与项目数据分开保留')
        parsed = urlparse(value['url'])
        require(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and parsed.port
                and not parsed.username and parsed.path in ('', '/'), '快捷启动仅监听本机既有 HTTP 端口')
        self.port = parsed.port
        budget = value.get('selection_cpu_workers', 4)
        require(type(budget) is int and 0 <= budget <= 4, '选股 CPU 预算不合法')
        self.environment = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'SSL_CERT_FILE')
                            if key in os.environ}
        self.environment.update(PYTHONPATH=str(self.backend), PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
                                SHIXU_DATA_DIR=str(self.data), SHIXU_SQLITE_PATH=str(self.database),
                                SHIXU_CREDENTIAL_MASTER_KEY_FILE=str(self.key), SHIXU_SELECTION_CPU_WORKERS=str(budget))
        for name in ('published', 'raw', 'work', 'credentials'):
            self.environment['SHIXU_' + ('CREDENTIAL' if name == 'credentials' else name.upper()) + '_DIR'] = str(roots[name])
        self.worker_lock = roots['work'] / 'worker.lock'
        # Only small metadata; never integrity_check, full table SHA, migrate or validate.
        connection = sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA recursive_triggers=ON')
            connection.execute('PRAGMA busy_timeout=2000')
            require(connection.execute('PRAGMA user_version').fetchone()[0] == value['schema'], '业务库尚未完成本版本迁移，快捷启动不会自动迁移')
            actual = dict(connection.execute('SELECT name,sha256 FROM _schema_migration'))
            expected = {path.name: sha(path) for path in (self.backend / 'app/migrations').glob('[0-9]*.sql')}
            require(expected and actual == expected and max(int(name.split('_')[0]) for name in expected) == value['schema'],
                    '已执行迁移与发行源码不匹配，请先走独立升级流程')
        finally:
            connection.close()

    def arguments(self, role):
        return ['-B', '-m', 'app.worker'] if role == 'worker' else [
            '-B', '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(self.port), '--workers', '1']

    def matches(self, process, role):
        expected = {key: value for key, value in self.environment.items() if key.startswith('SHIXU_') or key == 'PYTHONPATH'}
        executable = self.python.resolve()
        executables = {executable, executable.parent.parent / 'Resources/Python.app/Contents/MacOS/Python'}
        return (bool(process.argv) and process.cwd == self.backend and process.argv[1:] == self.arguments(role)
                and Path(process.argv[0]).resolve() in executables
                and all(process.environment.get(key) == value for key, value in expected.items()))

    def inspect(self):
        found = {}
        for role in ('api', 'worker'):
            pid = self.value.get('processes', {}).get(role)
            process = self.inspector.read(pid)
            if process:
                require(self.matches(process, role), '记录的 PID 已被其他实例使用，拒绝操作且不会终止该进程')
                token = self.value.get('native_process_tokens', {}).get(role)
                require(not token or token == process.token, '记录的 PID 启动时间已变化，请核对部署配置')
                found[role] = process
        listeners = self.inspector.listeners(self.port)
        if listeners:
            require(len(listeners) == 1, '本机端口存在多个监听者，拒绝重复启动')
            process = self.inspector.read(listeners[0])
            require(process and self.matches(process, 'api'), '端口被其他服务占用，拒绝启动且不会终止占用者')
            require('api' not in found or found['api'].pid == process.pid, 'API PID 与端口监听者不一致')
            found['api'] = process
        else:
            require('api' not in found, '记录的 API 仍存活但未监听端口，请稍后查看日志')
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    probe.bind(('127.0.0.1', self.port))
                except OSError:
                    raise ServiceError('端口无法绑定，拒绝启动') from None
        workers = self.inspector.workers(self.workspace)
        require(len(workers) <= 1, '工作区中存在多个 worker，拒绝操作')
        if workers:
            require(self.matches(workers[0], 'worker'), '已有 worker 使用其他版本或数据路径，拒绝启动')
            require('worker' not in found or found['worker'].pid == workers[0].pid, 'worker PID 不一致')
            found['worker'] = workers[0]
        if self.worker_lock.exists():
            with self.worker_lock.open('rb') as stream:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    require('worker' in found and self.inspector.lock_holders(self.worker_lock) == [found['worker'].pid],
                            'worker 数据锁由未知进程占用，拒绝启动')
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                    require('worker' not in found, 'worker 未持有当前数据独占锁，请稍后查看日志')
        else:
            require('worker' not in found, 'worker 缺少当前数据锁')
        return found

    def health(self):
        try:
            with build_opener(ProxyHandler({})).open('http://127.0.0.1:' + str(self.port) + '/health', timeout=2) as response:
                value = json.load(response)
            return value.get('status') == 'ok' and value.get('application') == 'ad-market-review' and value.get('version') == self.value['version']
        except (OSError, ValueError):
            return False

    def spawn(self, role):
        logs = self.control.parent / 'native-service-logs'
        require(not logs.is_symlink(), '日志目录不允许使用符号链接')
        logs.mkdir(exist_ok=True, mode=0o700)
        path = logs / (role + '.log')
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'ab') as stream:
            os.fchmod(stream.fileno(), 0o600)
            child = subprocess.Popen([str(self.python), *self.arguments(role)], cwd=self.backend,
                                     env=self.environment, stdin=subprocess.DEVNULL, stdout=stream,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        self.children[role] = child
        return child

    def clean_own_failed_start(self):
        # No stop command: failure cleanup only owns children spawned by this call.
        for role, child in reversed(list(self.children.items())):
            if child.poll() is None:
                process = self.inspector.read(child.pid)
                if process and self.matches(process, role):
                    child.send_signal(signal.SIGTERM)
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass  # Never kill a process group or an unrelated/reused PID.

    def execute(self, action):
        with startup_lock(self.control):
            self.load(full=action == 'start')
            found = self.inspect()
            if action == 'status':
                return self.result(found, healthy='api' in found and self.health())
            try:
                require('api' not in found or self.health(), '既有 API 尚未健康，不启动新的 worker，请先查看日志')
                for role in ('api', 'worker'):
                    if role in found:
                        continue
                    require(sha(self.control) == self.control_sha, '部署配置在启动期间变化')
                    child = self.spawn(role)
                    deadline = time.monotonic() + self.timeout
                    while True:
                        require(child.poll() is None, '新进程启动失败，请查看 native-service-logs')
                        process = self.inspector.read(child.pid)
                        ready = process and self.matches(process, role)
                        if role == 'api':
                            ready = ready and self.inspector.listeners(self.port) == [child.pid] and self.health()
                        else:
                            ready = ready and self.worker_lock.exists() and self.inspector.lock_holders(self.worker_lock) == [child.pid]
                        if ready:
                            break
                        require(time.monotonic() < deadline, '新进程启动超时，请查看 native-service-logs')
                        time.sleep(.1)
                    found[role] = process
                found = self.inspect()
                require(set(found) == {'api', 'worker'} and self.health(), '启动后身份或健康核验失败')
                require(sha(self.control) == self.control_sha, '部署配置在启动期间变化')
                value = {**self.value, 'processes': {role: process.pid for role, process in found.items()},
                         'native_process_tokens': {role: process.token for role, process in found.items()},
                         'native_last_start_at': dt.datetime.now().astimezone().isoformat()}
                atomic_json(self.control, value)
                return self.result(found, healthy=True, started=list(self.children))
            except BaseException:
                self.clean_own_failed_start()
                raise

    def result(self, found, *, healthy, started=None):
        return {'status': 'running' if set(found) == {'api', 'worker'} and healthy else 'stopped' if not found else 'partial',
                'version': self.value['version'], 'url': self.value['url'],
                'processes': {role: process.pid for role, process in found.items()},
                'started': started or [], 'new_jobs_created': 0, 'migration_executed': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'status'))
    parser.add_argument('--control', type=Path, default=Path(__file__).resolve().parents[1] / 'data/work/runtime/local-service.json')
    args = parser.parse_args(argv)
    try:
        result = NativeService(args.control).execute(args.action)
    except (ServiceError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        # Do not print foreign process commands, environments or credential values.
        message = str(error) if isinstance(error, ServiceError) else '部署配置或运行环境不可用，请检查既有部署'
        print(json.dumps({'status': 'refused', 'message': message}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
