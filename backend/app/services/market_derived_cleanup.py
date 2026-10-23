"""Release only sealed, reconstructible import derivatives after publication.

Remote accepted responses and their hashes remain intact. This is a finite
writer-owned success step, never a directory/age sweep or a reader side effect.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time

from app.core import atomic_json, connect, digest, hash_file, now_ms, published_dir, raw_dir, work_dir
from app.analytics.market_storage import VERSION as STORAGE_VERSION, read_descriptor

VERSION = 'published-market-derivatives-v1'
_JOB = re.compile(r'[A-Za-z0-9_-]{1,120}\Z')
_GENERATION = re.compile(r'market-[a-f0-9]{24}\Z')
_CANONICAL = re.compile(r'(?:fuyao_(?:stock_master|calendar_year|index_year_[0-9]{6}_(?:SH|SZ)|bulk_adjustments|concept_catalog|industry_catalog|members_[A-Za-z0-9_-]+|limitup_latest|limitdown_latest|breakpool_latest|ladder_30d|abnormal_current|sectors_snapshot_sample|pool_history)|members-[A-Za-z0-9_.-]+)\.(?:json|parquet)\Z')


def _safe(root, relative):
    root = Path(root).resolve()
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('派生清理路径越界')
    path = root / relative
    if any(part.is_symlink() for part in (path, *path.parents) if part != root and root in part.parents):
        raise ValueError('派生清理拒绝符号链接')
    if not path.resolve().is_relative_to(root):
        raise ValueError('派生清理路径越界')
    return path


def _ready_proof(job_id, status, source_kind):
    generation = status.get('snapshot_id')
    if not isinstance(generation, str) or not _GENERATION.fullmatch(generation):
        raise ValueError('发布代际无效，不能清理派生文件')
    with connect() as connection:
        job = connection.execute('SELECT kind,status FROM job WHERE job_id=?', (job_id,)).fetchone()
        expected = ('initialise', 'increment') if source_kind == 'sync' else ('topic_init', 'topic_refresh')
        if not job or job['kind'] not in expected or job['status'] not in ('partial', 'succeeded', 'no_change'):
            return None
        snapshot = connection.execute("SELECT * FROM dataset_snapshot WHERE job_id=? AND module='daily_raw' AND state='published' AND json_extract(lineage_json,'$.market_snapshot_id')=?", (job_id, generation)).fetchone()
        if not snapshot or json.loads(snapshot['lineage_json']).get('market_snapshot_id') != generation:
            raise ValueError('任务未登记对应已发布行情，不能清理')
        artifact = connection.execute("SELECT * FROM snapshot_artifact WHERE snapshot_id=? AND role='raw-daily.parquet'", (snapshot['snapshot_id'],)).fetchone()
        if artifact is None:
            raise ValueError('缺少已登记原行情分片清单')
        artifact = dict(artifact)
    root = published_dir()
    manifest_path = _safe(root, generation + '/manifest.json')
    manifest = json.loads(manifest_path.read_text())
    head = manifest.get('summary', {})
    if head.get('snapshot_id') != generation or head.get('storage_layout') != STORAGE_VERSION:
        raise ValueError('仅新分片行情支持释放派生文件')
    descriptor = _safe(root, artifact['object_key'])
    if descriptor.name != 'raw-daily.manifest.json' or descriptor.parent.name != generation or hash_file(descriptor) != artifact['sha256']:
        raise ValueError('已登记原行情分片清单不匹配')
    # This checks every immutable raw object SHA/size once, with the shared
    # bounded verification cache. It does not rerun an all-history SQL EXCEPT.
    raw = read_descriptor(descriptor)
    if raw['rows'] != head.get('rows') or raw['data_begin'] != head.get('data_begin') or raw['data_end'] != head.get('data_end'):
        raise ValueError('原行情分片闭包与发布范围不匹配')
    return {'generation': generation, 'snapshot_id': snapshot['snapshot_id'],
            'generation_manifest_sha256': hash_file(manifest_path),
            'raw_descriptor': artifact['object_key'], 'raw_descriptor_sha256': artifact['sha256'],
            'rows': raw['rows'], 'source_sha256': manifest.get('source_sha256'),
            'supplemental_hashes': manifest.get('supplemental_hashes', {})}


def _accepted_sources(directory, plan, source_kind):
    expected = plan.get('files', {})
    if not isinstance(expected, dict) or not expected:
        raise ValueError('缺少原始响应清单，不能释放派生文件')
    sources = []
    for name, item in sorted(expected.items()):
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError('原始响应路径无效')
        relative = ('accepted/' + name) if source_kind == 'sync' else ('raw/' + name + '.json')
        wanted = item.get('sha256') if isinstance(item, dict) else item
        if not isinstance(wanted, str) or not re.fullmatch(r'[a-f0-9]{64}', wanted):
            raise ValueError('原始响应缺少有效SHA')
        path = _safe(directory, relative)
        if not path.is_file() or hash_file(path) != wanted:
            raise ValueError('原始响应缺失或SHA不符，不能释放派生副本')
        sources.append({'path': relative, 'sha256': wanted, 'bytes': path.stat().st_size})
    return sources


def cleanup_published_derivatives(job_id, directory, status, plan, *, source_kind='sync'):
    """Exact, resumable cleanup. The caller wraps warnings after publication."""
    if source_kind not in ('sync', 'topics') or not isinstance(job_id, str) or not _JOB.fullmatch(job_id):
        raise ValueError('派生清理上下文无效')
    frozen = plan.get('plan', {}) if isinstance(plan, dict) and source_kind == 'sync' else plan
    if not isinstance(frozen, dict) or frozen.get('storage_layout') != STORAGE_VERSION:
        return {'status': 'skipped', 'reason': '旧冻结任务不改变原始文件保留方式'}
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError('派生工作目录不能是符号链接')
    directory = directory.resolve()
    expected = (raw_dir() / 'sync' / job_id) if source_kind == 'sync' else (work_dir() / job_id)
    if directory != expected.resolve():
        raise ValueError('只允许清理本任务的明确派生目录')
    started = time.monotonic()
    proof = _ready_proof(job_id, status, source_kind)
    if proof is None:
        return {'status': 'skipped', 'reason': '暂停、取消、失败或尚未发布登记的任务保留全部恢复文件'}
    sources = _accepted_sources(directory, plan, source_kind)
    receipt_path = _safe(directory, 'derived-cleanup.json')
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        scope = receipt.get('scope', {})
        if (receipt.get('version') != VERSION or digest(scope) != receipt.get('scope_sha256')
                or scope.get('proof') != proof or scope.get('accepted_sources') != sources
                or scope.get('job_id') != job_id or scope.get('source_kind') != source_kind):
            raise ValueError('派生释放回执与当前来源不符，不能继续')
        expected_files = {item['path']: item for item in scope.get('files', [])}
        ledger = receipt.get('files', [])
        if len(ledger) != len(expected_files) or {item['path'] for item in ledger} != set(expected_files):
            raise ValueError('派生释放逐文件回执不完整')
        if any(item.get('status') not in ('pending', 'removed', 'absent') for item in ledger):
            raise ValueError('派生释放逐文件状态无效')
        for relative, item in expected_files.items():
            value = Path(relative)
            wanted = proof['source_sha256'] if relative == 'year.parquet' else proof['supplemental_hashes'].get(value.name)
            if ((relative != 'year.parquet' and not (len(value.parts) == 2 and value.parts[0] == 'raw' and _CANONICAL.fullmatch(value.name)))
                    or item.get('sha256') != wanted or not isinstance(item.get('bytes'), int) or item['bytes'] < 0):
                raise ValueError('派生释放回执与已发布来源SHA不符')
        if receipt.get('status') == 'completed':
            if any(item['status'] == 'pending' or item.get('removed_bytes') != (expected_files[item['path']]['bytes'] if item['status'] == 'removed' else 0) for item in ledger):
                raise ValueError('派生完成回执与逐文件结果不符')
            return {'status': 'completed', 'already_completed': True, 'released_logical_bytes': sum(item['bytes'] for item in scope['files']), 'files': len(scope['files'])}
    else:
        bundle = json.loads(_safe(directory, 'bundle.json').read_text())
        if bundle.get('daily_file') != 'year.parquet' or bundle.get('storage_layout') != STORAGE_VERSION:
            raise ValueError('只释放新任务明确的year.parquet派生文件')
        original = _safe(directory, 'year.parquet')
        if not original.is_file() or hash_file(original) != proof['source_sha256']:
            raise ValueError('合并派生行情与已发布来源SHA不匹配')
        files = [{'path': 'year.parquet', 'sha256': proof['source_sha256'], 'bytes': original.stat().st_size}]
        protected = {item['path'] for item in sources}
        for name, wanted in sorted(proof['supplemental_hashes'].items()):
            if not isinstance(name, str) or not _CANONICAL.fullmatch(name):
                continue
            relative = 'raw/' + name
            # Topic collection stores the actual fetched responses in raw/;
            # their names are explicit in topic-plan.files and remain protected.
            if relative in protected:
                continue
            path = _safe(directory, relative)
            if not path.is_file() or hash_file(path) != wanted:
                raise ValueError('规范派生文件与已发布来源SHA不匹配')
            files.append({'path': relative, 'sha256': wanted, 'bytes': path.stat().st_size})
        scope = {'job_id': job_id, 'source_kind': source_kind, 'proof': proof, 'accepted_sources': sources, 'files': files,
                 'basis': '新写入器已验证输入格式、唯一键及不可变原行情分片；保留原始响应和来源SHA，释放可重建合并与规范副本'}
        expected_files = {item['path']: item for item in files}
        ledger = [{'path': item['path'], 'status': 'pending', 'removed_bytes': 0} for item in files]
        receipt = {'version': VERSION, 'status': 'deleting', 'scope': scope, 'scope_sha256': digest(scope), 'created_at_ms': now_ms(), 'files': ledger}
        atomic_json(receipt_path, receipt)
    removed_this_attempt = 0
    for item in ledger:
        if item['status'] != 'pending':
            continue
        expected_file = expected_files[item['path']]
        # Revalidate the frozen whitelist on resume, not an arbitrary receipt path.
        relative = Path(item['path'])
        if str(relative) != 'year.parquet' and not (len(relative.parts) == 2 and relative.parts[0] == 'raw' and _CANONICAL.fullmatch(relative.name)):
            raise ValueError('回执包含非派生白名单路径')
        if item['path'] in {source['path'] for source in sources}:
            raise ValueError('回执不能释放真实原始响应')
        path = _safe(directory, relative)
        exists = path.exists()
        if exists:
            if not path.is_file() or path.stat().st_size != expected_file['bytes'] or hash_file(path) != expected_file['sha256']:
                raise ValueError('派生文件删除前发生变化，停止释放')
            path.unlink()
            removed_this_attempt += expected_file['bytes']
            descriptor = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
        item.update(status='removed' if exists else 'absent', removed_bytes=expected_file['bytes'] if exists else 0)
        atomic_json(receipt_path, receipt)
    receipt.update(status='completed', completed_at_ms=now_ms(), elapsed_seconds=round(time.monotonic() - started, 3))
    atomic_json(receipt_path, receipt)
    return {'status': 'completed', 'files': len(scope['files']), 'released_logical_bytes': sum(item['bytes'] for item in scope['files']),
            'removed_bytes_this_attempt': removed_this_attempt, 'elapsed_seconds': receipt['elapsed_seconds']}


def release_after_publication(job_id, directory, status, plan, *, source_kind='sync'):
    """A cleanup failure is visible, but cannot undo a successful publication."""
    try:
        result = cleanup_published_derivatives(job_id, directory, status, plan, source_kind=source_kind)
    except Exception as error:
        result = {'status': 'warning', 'reason': '派生副本释放未完成，来源响应及已发布行情保留，可按回执重试', 'error_type': type(error).__name__}
    if result['status'] in ('completed', 'warning'):
        try:
            from app.services.task_service import append_event
            with connect() as connection:
                append_event(connection, job_id, 'derived_storage', '已释放发布后的可重建副本' if result['status'] == 'completed' else result['reason'],
                             'info' if result['status'] == 'completed' else 'warning', result)
        except Exception:
            pass
    return result
