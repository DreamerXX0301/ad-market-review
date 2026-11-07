"""Disposable disk-backed supplement reference graph.

Only a bounded node batch and the selected unlink receipts enter Python memory.
The graph is derived evidence, never business authority: interruption discards it
and the next explicit worker collection rebuilds it from verified source files.
A scan is still O(total files); this module does not claim incremental DAG cost.
"""
from __future__ import annotations

from collections.abc import Mapping, Set
from datetime import date
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import tempfile
import time

from app.infrastructure.file_operations import is_link_or_reparse, path_is_link_or_reparse
from app.infrastructure.storage import digest, dumps

_CAS = re.compile(r'(artifacts|raw|indexes)/[0-9a-f]{64}\.json\Z')
_DAY = re.compile(r'\d{4}-\d{2}-\d{2}\Z')
_BATCH_NODES = 256
_BATCH_EDGES = 4096
_SQL_CACHE_KIB = 8192
_MAX_GRAPH_PAGES = 524288  # 2 GiB disposable disk cap, independent of worker RAM.


def _day(value):
    try:
        return isinstance(value, str) and bool(_DAY.fullmatch(value)) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _files(root):
    """Stream flat CAS directories; never sort/materialize their million names."""
    if any(path_is_link_or_reparse(path) for path in (root, *root.parents)):
        raise ValueError('补充目录不允许符号链接')
    directories = 0
    def walk(folder, depth):
        nonlocal directories
        directories += 1
        if depth > 16 or directories > 100000:
            raise ValueError('补充目录层级或目录数超过有界扫描预算')
        with os.scandir(folder) as entries:
            for entry in entries:
                path = Path(entry.path)
                key = path.relative_to(root).as_posix()
                if entry.is_symlink() or (os.name == "nt" and is_link_or_reparse(entry.stat(follow_symlinks=False))):
                    raise ValueError('补充树含符号链接，拒绝清理')
                if entry.is_dir(follow_symlinks=False):
                    if PurePosixPath(key).parts[0] not in ('accepted', 'artifacts', 'raw', 'indexes'):
                        raise ValueError('发现未知补充目录，拒绝清理')
                    yield from walk(path, depth + 1)
                    continue
                if entry.name == '.DS_Store' and entry.is_file(follow_symlinks=False):
                    continue
                accepted = key.startswith('accepted/') and key.endswith('.json')
                if key != 'head.json' and not _CAS.fullmatch(key) and not accepted:
                    raise ValueError('发现未知补充文件，拒绝清理：'+key)
                yield key
    yield from walk(root, 0)


def _private_directory(directory):
    if directory is None:
        return None
    path = Path(directory).absolute()
    for candidate in (path, *path.parents):
        if path_is_link_or_reparse(candidate):
            raise ValueError('补充派生图目录不允许符号链接')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    # A fresh TemporaryDirectory below this parent is created with mode 0700.
    # On Python 3.13+ that sets a private Windows ACL, while st_mode reports
    # only read-only/writable bits. POSIX also verifies the existing parent.
    if not stat.S_ISDIR(info.st_mode) or (os.name != 'nt' and stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('补充派生图目录必须为0700私有目录')
    return path


class _Graph:
    def __init__(self, graph_directory=None):
        self.directory = tempfile.TemporaryDirectory(prefix='supplement-retention-graph-', dir=_private_directory(graph_directory))
        self.path = Path(self.directory.name) / 'graph.sqlite3'
        self.connection = sqlite3.connect(self.path, isolation_level=None)
        self.closed = False
        for pragma in ('foreign_keys=ON', 'recursive_triggers=ON', 'busy_timeout=5000',
                       f'cache_size=-{_SQL_CACHE_KIB}', 'temp_store=FILE', 'mmap_size=0',
                       'journal_mode=OFF', 'synchronous=OFF', f'max_page_count={_MAX_GRAPH_PAGES}'):
            self.connection.execute('PRAGMA ' + pragma)
        self.connection.executescript('''
          CREATE TABLE object(
            object_key TEXT PRIMARY KEY, sha256 TEXT NOT NULL, byte_count INTEGER NOT NULL,
            metadata_json TEXT NOT NULL, root INTEGER NOT NULL DEFAULT 0,
            protected INTEGER NOT NULL DEFAULT 0, incoming INTEGER NOT NULL DEFAULT 0,
            visited INTEGER NOT NULL DEFAULT 0, blocked INTEGER NOT NULL DEFAULT 0,
            index_created INTEGER, index_snapshot TEXT
          ) WITHOUT ROWID;
          CREATE TABLE edge(
            parent_key TEXT NOT NULL REFERENCES object(object_key),
            child_key TEXT NOT NULL, expected_sha256 TEXT NOT NULL,
            PRIMARY KEY(parent_key,child_key,expected_sha256)
          ) WITHOUT ROWID;
        ''')

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.connection.close()
        self.directory.cleanup()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def insert(self, nodes):
        connection = self.connection
        connection.execute('BEGIN')
        try:
            connection.executemany('INSERT INTO object(object_key,sha256,byte_count,metadata_json,root,index_created,index_snapshot) VALUES(?,?,?,?,?,?,?)',
                                   [(key, node['sha256'], node['byte_count'], dumps({name: value for name, value in node.items() if name != 'references'}), root, created, snapshot)
                                    for key, node, root, created, snapshot in nodes])
            connection.executemany('INSERT INTO edge(parent_key,child_key,expected_sha256) VALUES(?,?,?)',
                                   ((key, relative, expected) for key, node, *_ in nodes for relative, expected in node['references']))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


class DiskNodes(Mapping):
    """Compatibility mapping for existing collector checks; reads one node."""
    def __init__(self, graph):
        self.graph = graph

    def __len__(self):
        return self.graph.connection.execute('SELECT count(*) FROM object').fetchone()[0]

    def __iter__(self):
        yield from (row[0] for row in self.graph.connection.execute('SELECT object_key FROM object ORDER BY object_key'))

    def __getitem__(self, key):
        row = self.graph.connection.execute('SELECT metadata_json FROM object WHERE object_key=?', (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        value = json.loads(row[0])
        value['identity'] = tuple(value['identity'])
        value['references'] = tuple(self.graph.connection.execute('SELECT child_key,expected_sha256 FROM edge WHERE parent_key=? ORDER BY child_key,expected_sha256', (key,)))
        return value

    def items(self):
        for key in self:
            yield key, self[key]


class DiskProtected(Set):
    def __init__(self, graph):
        self.graph = graph

    def __len__(self):
        return self.graph.connection.execute('SELECT count(*) FROM object WHERE protected=1').fetchone()[0]

    def __iter__(self):
        yield from (row[0] for row in self.graph.connection.execute('SELECT object_key FROM object WHERE protected=1 ORDER BY object_key'))

    def __contains__(self, key):
        return self.graph.connection.execute('SELECT 1 FROM object WHERE object_key=? AND protected=1', (key,)).fetchone() is not None


class GraphPlan(dict):
    def __init__(self, graph, **values):
        super().__init__(values)
        self.graph = graph

    def close(self):
        self.graph.close()

    def __enter__(self):
        return self

    def __exit__(self, *_error):
        self.close()


def _root_kind(key, node, window, protected_generations):
    root, created, snapshot = key == 'head.json', None, None
    if key.startswith('accepted/'):
        parts = PurePosixPath(key).parts
        day = Path(key).stem[:10]
        sector = any(part in ('sector-chart', 'sector-chart-tail') for part in parts)
        annual = _day(day) and day[:4] == window['anchor_date'][:4] and any(part in ('limitup', 'limitdown') for part in parts)
        root = sector or not _day(day) or day >= window['cutoff_date'] or annual
    elif key.startswith('indexes/'):
        day, created, snapshot = node.get('target_date'), node.get('created_at_ms'), node.get('snapshot_id')
        if not _day(day) or not isinstance(snapshot, str) or not isinstance(created, int) or isinstance(created, bool) or created <= 0:
            root, created, snapshot = True, None, None
        else:
            root = snapshot in protected_generations
    return bool(root), created, snapshot


def _roots_digest(connection):
    result = hashlib.sha256(b'[')
    for number, row in enumerate(connection.execute('SELECT object_key,sha256 FROM object WHERE root=1 ORDER BY object_key')):
        if number:
            result.update(b',')
        result.update(dumps(list(row)).encode())
    result.update(b']')
    return result.hexdigest()


def _select(connection, max_files, max_bytes, guard=None):
    """Disk Kahn ordering; parent exclusion propagates before children dequeue."""
    connection.execute('CREATE INDEX edge_child ON edge(child_key,parent_key)')
    connection.execute('CREATE INDEX object_pending ON object(protected,visited,incoming,object_key)')
    connection.execute('''UPDATE object SET incoming=(
      SELECT count(*) FROM edge JOIN object parent ON parent.object_key=edge.parent_key
      WHERE edge.child_key=object.object_key AND parent.protected=0
    ) WHERE protected=0''')
    connection.execute('CREATE TABLE ready_batch(object_key TEXT PRIMARY KEY,blocked INTEGER NOT NULL) WITHOUT ROWID')
    chosen, used, visited = [], 0, 0
    while True:
        if guard:
            guard()
        rows = connection.execute('SELECT object_key,sha256,byte_count,blocked FROM object WHERE protected=0 AND visited=0 AND incoming=0 ORDER BY object_key LIMIT ?', (_BATCH_NODES,)).fetchall()
        if not rows:
            break
        decisions = []
        for key, sha, byte_count, blocked in rows:
            skip = bool(blocked or len(chosen) >= max_files or used + byte_count > max_bytes)
            decisions.append((key, int(skip)))
            if not skip:
                chosen.append({'object_key': key, 'sha256': sha, 'byte_count': byte_count})
                used += byte_count
        connection.execute('BEGIN')
        try:
            connection.execute('DELETE FROM ready_batch')
            connection.executemany('INSERT INTO ready_batch VALUES(?,?)', decisions)
            # The explicit primary-key path matters: SQLite may otherwise pick
            # the protected-state index and scan every eligible object for each
            # frontier. That becomes quadratic on a large mostly-leaf CAS tree.
            connection.execute('''UPDATE object INDEXED BY sqlite_autoindex_object_1 SET blocked=1
              WHERE object_key IN (SELECT edge.child_key FROM ready_batch CROSS JOIN edge ON edge.parent_key=ready_batch.object_key WHERE ready_batch.blocked=1)
              AND protected=0''')
            connection.execute('''UPDATE object INDEXED BY sqlite_autoindex_object_1 SET visited=1
              WHERE object_key IN (SELECT object_key FROM ready_batch)''')
            connection.execute('''UPDATE object INDEXED BY sqlite_autoindex_object_1 SET incoming=incoming-(
              SELECT count(*) FROM edge JOIN ready_batch ON ready_batch.object_key=edge.parent_key
              WHERE edge.child_key=object.object_key
            ) WHERE object_key IN (SELECT edge.child_key FROM ready_batch CROSS JOIN edge ON edge.parent_key=ready_batch.object_key)
              AND protected=0''')
            connection.commit()
            visited += len(rows)
        except BaseException:
            connection.rollback()
            raise
    eligible = connection.execute('SELECT count(*) FROM object WHERE protected=0').fetchone()[0]
    if visited != eligible:
        raise ValueError('补充孤立引用图存在循环，拒绝清理')
    return chosen, used, eligible


def build_plan(root, window, *, protected_generations=(), max_files, max_bytes, node_loader,
               max_inventory_files=None, max_edges=None, guard=None, graph_directory=None):
    """Read and verify every object into a disposable bounded-cache SQLite DAG."""
    started = time.perf_counter()
    root = Path(root).absolute()
    if graph_directory is not None and Path(graph_directory).absolute().is_relative_to(root):
        raise ValueError('补充派生图不能写入只读来源目录')
    graph = _Graph(graph_directory)
    connection = graph.connection
    try:
        protected_generations = set(protected_generations)
        batch, batch_edges, total, edges = [], 0, 0, 0
        for key in _files(root):
            if guard and total % 128 == 0:
                guard()
            node = node_loader(root, key)
            total += 1
            edges += len(node['references'])
            if max_inventory_files is not None and total > max_inventory_files or max_edges is not None and edges > max_edges:
                raise ValueError('补充树超过本轮有界磁盘扫描预算')
            is_root, created, snapshot = _root_kind(key, node, window, protected_generations)
            batch.append((key, node, is_root, created, snapshot))
            batch_edges += len(node['references'])
            if len(batch) >= _BATCH_NODES or batch_edges >= _BATCH_EDGES:
                graph.insert(batch)
                batch, batch_edges = [], 0
        if batch:
            graph.insert(batch)
        if guard:
            guard()
        if connection.execute("SELECT 1 FROM object WHERE object_key='head.json'").fetchone() is None:
            raise ValueError('补充索引尚未发布，不能清理')
        broken = connection.execute('SELECT edge.child_key FROM edge LEFT JOIN object ON object.object_key=edge.child_key WHERE object.object_key IS NULL OR object.sha256<>edge.expected_sha256 LIMIT 1').fetchone()
        if broken:
            raise ValueError('补充依赖缺失或SHA不一致：' + broken[0])
        # Only current head closure, the two newest rollback indexes, actual
        # business generations and conservative unknown metadata are index roots.
        connection.execute('UPDATE object SET root=1 WHERE object_key IN (SELECT object_key FROM object WHERE index_created IS NOT NULL ORDER BY index_created DESC,object_key DESC LIMIT 2)')
        connection.execute('''WITH RECURSIVE closure(object_key) AS (
          SELECT object_key FROM object WHERE root=1
          UNION SELECT edge.child_key FROM edge JOIN closure ON edge.parent_key=closure.object_key
        ) UPDATE object SET protected=1 WHERE object_key IN (SELECT object_key FROM closure)''')
        roots_sha = _roots_digest(connection)
        chosen, used, eligible = _select(connection, max_files, max_bytes, guard)
        count, byte_count, protected = connection.execute('SELECT count(*),COALESCE(sum(byte_count),0),COALESCE(sum(protected),0) FROM object').fetchone()
        head_sha = connection.execute("SELECT sha256 FROM object WHERE object_key='head.json'").fetchone()[0]
        scope = {'version': 'supplement-retention-250-v1', **window, 'files': chosen,
                 'head_sha256': head_sha, 'protected_generations': sorted(protected_generations),
                 'protected_roots_sha256': roots_sha, 'inventory_files': count,
                 'inventory_bytes': byte_count, 'protected_files': protected,
                 'eligible_files': eligible, 'selected_bytes': used,
                 'backlog_files': eligible - len(chosen), 'max_files': max_files, 'max_bytes': max_bytes,
                 'graph_storage': 'disposable-sqlite-v1', 'graph_bytes': graph.path.stat().st_size,
                 'graph_cache_kib': _SQL_CACHE_KIB}
        return GraphPlan(graph, scope=scope, scope_sha256=digest(scope),
                         protected_keys=DiskProtected(graph), nodes=DiskNodes(graph),
                         elapsed_ms=round((time.perf_counter() - started) * 1000, 3))
    except BaseException:
        graph.close()
        raise
