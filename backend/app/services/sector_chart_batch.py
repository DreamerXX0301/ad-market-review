"""Bounded, resumable all-catalog sector chart collection for the sole worker.

Every board retains its existing independently SHA-verified cache. This report
contains artifact summaries, never710 full histories held in one head JSON.
"""
from __future__ import annotations

import json
import time

from app.adapters.providers import (ProviderError, SECTOR_TAIL_PACING_VERSION,
    validate_sector_tail_pacing, sector_tail_request_scope)
from app.core import digest
from app.services.sector_charts import (collect_sector_chart, collect_sector_chart_incremental,
    _plan_from_inputs, _root, _verified_json)
from app.services.sector_history import _catalog, _day
from app.services.sector_read_context import planning_scope, consume_verified

NORMALIZATION_VERSION = 'fuyao-sector-chart-batch-v1'
MAX_CODES = 800
REQUEST_INTERVAL = 1.2


def plan_sector_chart_batch(market, as_of, mode='initial', *, request_policy_version=None):
    """Bind the current catalog identity, published generation and date scope."""
    day = _day(as_of)
    if mode not in ('initial', 'increment'): raise ValueError('板块K线仅支持首次补齐或尾部增量模式')
    validate_sector_tail_pacing(request_policy_version)
    if request_policy_version is not None and mode != 'increment':
        raise ValueError('新请求节奏只适用于明确冻结的板块尾部增量')
    catalog, invalid, duplicates, ambiguous = _catalog(market.reference())
    if not catalog:
        raise ValueError('行业概念目录为空，不能将空目录视为全量K线完成')
    if len(catalog) > MAX_CODES:
        raise ValueError('当前行业概念目录超过800个保护上限，未创建无界板块K线任务')
    status = market.status(); calendar = set(market.calendar())
    boards = [_plan_from_inputs(status, calendar, catalog, code, day) for code in sorted(catalog)]
    frozen = {'source': 'fuyao', 'as_of': day, 'start': boards[0]['start'], 'end': day,
              'snapshot_id': boards[0]['snapshot_id'], 'boards': boards,
              'catalog_signature': digest([board['instrument'] for board in boards]),
              'catalog_failures': invalid, 'duplicate_catalog_rows': duplicates,
              'ambiguous_catalog_count': ambiguous,
              'normalization_version': NORMALIZATION_VERSION,
              'scope': '冻结当前目录的行业与概念历史指数K线；不代表完整历史目录或历史成分股'}
    if mode == 'increment': frozen['mode'] = mode
    if request_policy_version is not None:
        frozen['sector_tail_request_policy_version'] = request_policy_version
    frozen['request_signature'] = digest(frozen)
    return frozen


def _validate_plan(market, plan):
    if not isinstance(plan, dict): raise ValueError('全量板块K线缺少冻结计划')
    current = plan_sector_chart_batch(market, plan.get('as_of'), plan.get('mode', 'initial'),
                                     request_policy_version=plan.get('sector_tail_request_policy_version'))
    if any(plan.get(key) != value for key, value in current.items()):
        raise ValueError('全量板块K线冻结目录、身份、日期或行情代际已不符，请重新创建任务')
    return current


class _GuardInterrupted(Exception):
    """Do not misclassify a guard's ValueError as an individual source failure."""


class _SourceStopped(Exception):
    pass


class _BoundedAdapter:
    def __init__(self, adapter, guard, pacing=None):
        self.adapter = adapter; self.guard = guard
        self.pacing = pacing
        self.interval = pacing.interval if pacing is not None else REQUEST_INTERVAL
        self.requests = 0; self.last_request = None; self.blocked = None
        self.request_kind = 'full'; self.full_requests = 0; self.tail_requests = 0

    def set_chart_request_kind(self, kind):
        self.request_kind = kind

    def request(self, path, params):
        self.guard()
        if self.blocked: raise _SourceStopped(self.blocked)
        if self.requests >= MAX_CODES: raise _SourceStopped('达到本次800个逻辑请求保护上限')
        if self.last_request is not None:
            delay = self.interval-(time.monotonic()-self.last_request)
            if delay > 0:
                (self.pacing.wait if self.pacing is not None else time.sleep)(delay)
        self.guard()
        self.requests += 1; self.last_request = time.monotonic()
        if self.request_kind == 'tail': self.tail_requests += 1
        else: self.full_requests += 1
        try:
            return self.adapter.request(path, params)
        except ProviderError as error:
            if error.status in (401, 403, 429) or error.code == 2001 or self.pacing is not None and error.code in (2003, 4001):
                self.blocked = '来源认证、权限或限流拒绝，停止新增请求；缓存仍逐项复验：'+str(error)
            raise


def _item_summary(plan, result):
    coverage = result['coverage']
    keys = ('requested_start', 'requested_end', 'start', 'end', 'expected_count',
            'observed_count', 'missing_count', 'amount_unknown_count',
            'volume_unknown_count', 'complete', 'source_request')
    return {'topic_code': plan['topic_code'], 'topic_id': plan['instrument']['topic_id'],
            'name': plan['instrument']['name'], 'kind': plan['instrument']['sector_kind'],
            'available': True, 'cache_hit': result['cache_hit'], 'quality': result['quality'],
            'artifact': result['artifact'], 'sha256': result['sha256'],
            'fetched_at': result['record'].get('fetched_at'),
            'request_kind': result.get('request_kind', 'cache' if result['cache_hit'] else 'full'),
            'reused_rows': result.get('reused_rows', len(result['record']['bars']) if result['cache_hit'] else 0),
            'downloaded_rows': result.get('downloaded_rows', 0 if result['cache_hit'] else len(result['record']['bars'])),
            'raw_artifact': result['record'].get('raw_artifact'),
            'raw_sha256': result['record'].get('raw_sha256'),
            'coverage': {key: coverage[key] for key in keys if key in coverage}}


def _save_report(plan, report, guard):
    from app.services.market_supplement import SupplementCache
    guard()
    cache = SupplementCache(_root())
    capability = 'sector-chart-increment-batch' if plan.get('mode') == 'increment' else 'sector-chart-batch'
    saved = cache.accept('fuyao', capability, plan['as_of'],
                         plan['request_signature'], report,
                         {'contract': NORMALIZATION_VERSION, 'report': report})
    guard()
    pointer = json.loads((cache.root/'accepted/fuyao'/capability/(plan['as_of']+'.json')).read_text())
    return {**saved, 'artifact': pointer['artifact'], 'sha256': pointer['sha256']}


def read_frozen_sector_chart_batch_report(plan, mode='initial'):
    """Read one small SHA-verified manifest, not every board's full history.

    This reports validation performed at collection time; each actual chart GET
    continues to verify that chart's own raw artifact independently.
    """
    if mode not in ('initial', 'increment') or not isinstance(plan, dict):
        raise ValueError('板块K线摘要冻结计划或模式无效')
    # Preserve old frozen initial signatures exactly; never reinterpret them
    # against the current generation or add a new field to their digest.
    expected = {key: plan[key] for key in ('source','as_of','snapshot_id','catalog_signature','request_signature')}
    capability = 'sector-chart-increment-batch' if mode == 'increment' else 'sector-chart-batch'
    root = _root().resolve()
    pointer = root/'accepted/fuyao'/capability/(expected['as_of']+'.json')
    if not pointer.is_file(): return None
    if not pointer.resolve().is_relative_to(root) or pointer.stat().st_size > 64*1024:
        raise ValueError('板块K线全库摘要指针路径或大小异常')
    saved = json.loads(pointer.read_text())
    if not isinstance(saved, dict) or saved.get('version') != 'market-supplement-v1':
        raise ValueError('板块K线全库摘要指针版本不符')
    if saved.get('signature') != expected['request_signature']: return None
    record = _verified_json(saved.get('artifact'), saved.get('sha256'))
    if not isinstance(record, dict):
        raise ValueError('板块K线全库摘要结构不符')
    raw = _verified_json(record.get('raw_artifact'), record.get('raw_sha256'))
    if not isinstance(raw, dict) or raw.get('contract') != NORMALIZATION_VERSION:
        raise ValueError('板块K线全库摘要原始依据不符')
    proof = raw.get('report')
    keys = ('date', 'source', 'snapshot_id', 'catalog_signature', 'request_signature',
            'normalization_version', 'items', 'coverage', 'failures', 'complete', 'quality', 'message', 'mode',
            'sector_tail_request_policy_version')
    if (not isinstance(proof, dict) or any(record.get(key) != proof.get(key) for key in keys)
            or record.get('date') != expected['as_of'] or record.get('source') != expected['source']
            or record.get('snapshot_id') != expected['snapshot_id']
            or record.get('catalog_signature') != expected['catalog_signature']
            or record.get('request_signature') != expected['request_signature']
            or record.get('sector_tail_request_policy_version') != plan.get('sector_tail_request_policy_version')
            or record.get('mode', 'initial') != mode
            or record.get('normalization_version') != NORMALIZATION_VERSION):
        raise ValueError('板块K线全库摘要代际、目录、日期或请求证明不符')
    return {**record, 'artifact': saved['artifact'], 'sha256': saved['sha256'],
            'verification_basis': '仅摘要及其原始清单SHA复验；完整K线在逐板块采集及图表读取时另行复验'}


def read_sector_chart_batch_report(market, as_of, mode='initial'):
    if mode == 'increment':
        current = plan_sector_chart_batch(market, as_of, mode, request_policy_version=SECTOR_TAIL_PACING_VERSION)
        report = read_frozen_sector_chart_batch_report(current, mode)
        if report is not None:
            return report
    plan = plan_sector_chart_batch(market, as_of, mode)
    return read_frozen_sector_chart_batch_report(plan, mode)


def collect_sector_chart_batch(market, plan, adapter, guard=None, progress=None):
    """Collect one frozen directory once; a retry remotely fetches only misses.

    Initial mode reuses exact-day caches; increment mode appends only new dates
    to a verified immutable base. New catalog codes may require one full call.
    ProviderClient owns at most3 transport attempts per logical request.
    """
    if not isinstance(plan, dict): raise ValueError('全量板块K线缺少冻结计划')
    def checked():
        try:
            if guard: guard()
            if market.status().get('snapshot_id') != plan.get('snapshot_id'):
                raise ValueError('主行情代际已变化，全量板块K线停止；已验收缓存保留')
        except Exception as error:
            raise _GuardInterrupted() from error
    try:
        checked()
        frozen = _validate_plan(market, plan)
        items = []; failures = list(frozen['catalog_failures'])
        failed = 0; unrequested = 0; cache_hits = 0; total = len(frozen['boards'])
        with sector_tail_request_scope(frozen.get('sector_tail_request_policy_version'), checked) as pacing, planning_scope(market, frozen['as_of'], plans=frozen['boards']):
            bounded = _BoundedAdapter(adapter, checked, pacing)
            for done, board in enumerate(frozen['boards'], 1):
                checked()
                prior_requests = bounded.requests; result = None
                try:
                    collector = collect_sector_chart_incremental if frozen.get('mode') == 'increment' else collect_sector_chart
                    result = collector(market, board, bounded, guard=checked)
                    items.append(_item_summary(board, result))
                    cache_hits += bool(result['cache_hit'])
                except _SourceStopped as error:
                    result = None
                    unrequested += 1
                    failures.append({'topic_code': board['topic_code'], 'stage': 'source_blocked',
                                     'reason': str(error), 'requested': False})
                except (ProviderError, ValueError, TypeError, OSError) as error:
                    result = None
                    failed += 1
                    failures.append({'topic_code': board['topic_code'], 'stage': 'chart_collection',
                                     'reason': str(error), 'requested': bounded.requests > prior_requests})
                if result is not None:
                    try:
                        consume_verified(board, result)
                    except Exception:
                        checked()
                        raise
                checked()
                if progress:
                    progress('同步行业概念历史K线', round(done/total*99), board['instrument']['name'],
                             {'completed_count': done, 'total_count': total,
                              'available_count': len(items), 'request_count': bounded.requests,
                              'full_request_count': bounded.full_requests, 'tail_request_count': bounded.tail_requests,
                              'cache_hit_count': cache_hits, 'failed_count': failed,
                              'unrequested_count': unrequested})
        checked()
        complete_count = sum(item['quality'] == 'complete' for item in items)
        collection_complete = len(items) == total and not failures
        complete = collection_complete and complete_count == total
        coverage = {'catalog_count': total+frozen['ambiguous_catalog_count'], 'total_count': total,
                    'available_count': len(items), 'complete_count': complete_count,
                    'partial_count': len(items)-complete_count, 'failed_count': failed,
                    'catalog_failure_count': len(frozen['catalog_failures']),
                    'unrequested_count': unrequested, 'request_count': bounded.requests,
                    'full_request_count': bounded.full_requests, 'tail_request_count': bounded.tail_requests,
                    'reused_rows': sum(item['reused_rows'] for item in items),
                    'downloaded_rows': sum(item['downloaded_rows'] for item in items),
                    'cache_hit_count': cache_hits, 'max_codes': MAX_CODES,
                    'max_provider_transport_attempts': bounded.requests*3,
                    'collection_complete': collection_complete, 'complete': complete,
                    'current_catalog': True, 'historical_catalog_complete': False,
                    'membership_independent': True,
                    'kind_counts': {kind: sum(board['instrument']['sector_kind'] == kind
                                             for board in frozen['boards'])
                                    for kind in ('industry', 'concept')},
                    'notice': '按冻结当前目录逐板块核验；增量仅追加已核验缓存之后的新日线，新板块首次下载全窗口；来源滚动范围、板块新设或旧内部缺口仍保持未知。'}
        report = {'date': frozen['as_of'], 'source': 'fuyao', 'snapshot_id': frozen['snapshot_id'],
                'catalog_signature': frozen['catalog_signature'],
                'mode': frozen.get('mode', 'initial'),
                'request_signature': frozen['request_signature'], 'items': items,
                'coverage': coverage, 'failures': failures, 'complete': complete,
                'quality': 'complete' if complete else 'partial',
                'normalization_version': NORMALIZATION_VERSION,
                'message': f'行业概念历史K线已核验 {len(items)}/{total} 个；失败 {failed}、未请求 {unrequested}，报价缺口见覆盖说明'}
        if pacing is not None:
            report['sector_tail_request_policy_version'] = frozen['sector_tail_request_policy_version']
            coverage.update(request_policy_version=frozen['sector_tail_request_policy_version'],
                            request_interval_seconds=pacing.interval, request_interval_anchor='transport-start',
                            maximum_inflight=1, provider_transport_attempts=pacing.attempts)
        return _save_report(frozen, report, checked)
    except _GuardInterrupted as error:
        raise error.__cause__
