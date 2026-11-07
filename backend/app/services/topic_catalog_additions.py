"""Append newly observed Fuyao classifications to the accepted market reference."""
from datetime import datetime
import json
import re
from zoneinfo import ZoneInfo

from app.adapters.providers import ProviderError
from app.core import atomic_json, get_market, hash_file, work_dir
from app.services.bootstrap import register_market
from app.services.source_service import client

SCOPE = 'catalog-additions-v1'
CATALOGS = {'concept': 'cn_concept', 'industry': 'industry'}
CODE = re.compile(r'[A-Z0-9]{1,16}\.[A-Z]{1,6}')
SYMBOL = re.compile(r'[0-9]{6}\.(SH|SZ|BJ)')
CHINA = ZoneInfo('Asia/Shanghai')


def _items(response):
    data = response.get('data') if isinstance(response, dict) else None
    rows = data.get('item') if isinstance(data, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ProviderError('题材来源响应格式不完整')
    pagination = data.get('pagination') or {}
    if pagination and (pagination.get('has_next') is True or pagination.get('total', len(rows)) != len(rows)):
        raise ProviderError('题材来源存在未返回的分页')
    return rows


def execute(job, progress):
    market = get_market()
    status = market.status()
    if not status.get('ready') or (job['target_date'] and job['target_date'] != status['data_end']):
        raise ValueError('题材增量的冻结行情日已变化，请重新提交')
    job_id = job['job_id']
    directory = work_dir() / job_id
    plan_path = directory / 'catalog-additions-plan.json'
    plan = (json.loads(plan_path.read_text()) if plan_path.exists() else
            {'scope': SCOPE, 'generation': status['snapshot_id'], 'files': {}})
    if plan['scope'] != SCOPE or plan['generation'] != status['snapshot_id']:
        raise ValueError('题材增量的冻结代际已变化，请重新提交')
    atomic_json(plan_path, plan)
    adapter = client('fuyao')

    def response(name, path, params):
        file = directory / 'raw' / (name + '.json')
        accepted = plan['files'].get(name)
        if accepted and (not file.is_file() or hash_file(file) != accepted):
            raise ValueError('题材增量原始响应缺失或校验不符')
        if not file.is_file():
            body = adapter.request(path, params)
            atomic_json(file, {'observed_date': datetime.now(CHINA).date().isoformat(), 'response': body})
        plan['files'][name] = hash_file(file)
        atomic_json(plan_path, plan)
        return json.loads(file.read_text())

    progress('题材目录', 3, '核对当前行业与概念目录，只采集本机尚未留存的分类成员')
    reference = market.reference()
    existing = {row['topic_code']: row for row in reference.get('topics', []) if row.get('topic_code')}
    catalog = {}
    for kind, tag in CATALOGS.items():
        receipt = response(kind, '/api/a-share-index/catalog/ths-index-list', {'tag': tag})
        rows = _items(receipt['response'])
        if not rows:
            raise ProviderError('来源题材目录为空，保留已发布题材')
        for row in rows:
            code, name = row.get('thscode'), row.get('name')
            if not isinstance(code, str) or not CODE.fullmatch(code) or not isinstance(name, str) or not name.strip():
                raise ProviderError('来源题材目录包含无效代码或名称')
            if code not in existing and code in catalog and catalog[code] != (kind, name):
                raise ProviderError('新增题材代码对应多个不同分类，待来源核验')
            catalog[code] = (kind, name)
    pending = sorted(catalog.keys() - existing.keys())
    if not pending:
        return {'status': 'no_change', 'message': '来源目录暂无新增分类，已保留现有成员档案'}

    additions, failures = [], []
    total = len(pending)
    for index, code in enumerate(pending):
        kind, name = catalog[code]
        progress('新增题材成员', 8 + 68 * index // total, f'核验 {index + 1}/{total} · {name}',
                 {'actual_completed_batches': index, 'actual_total_batches': total})
        try:
            receipt = response('members-' + code, '/api/a-share-index/constituents/ths-stock-list', {'thscode': code})
            symbols = [row.get('thscode') for row in _items(receipt['response'])]
            if any(not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol.upper()) for symbol in symbols):
                raise ProviderError('题材成员含未核验证券代码')
        except ProviderError as error:
            if error.status in (401, 403):
                raise
            failures.append(code)
            continue
        additions.append({'topic_id': 'fuyao-' + code, 'topic_code': code, 'code': code,
                          'name': name, 'kind': kind, 'source': 'fuyao',
                          'observed_date': receipt['observed_date'], 'historical_membership': False,
                          'instrument_ids': sorted({symbol.upper() for symbol in symbols}),
                          'members_complete': True, 'members_state': 'complete'})

    progress('新增题材验收', 79, f'已验收 {len(additions)}/{total} 组新增分类',
             {'actual_completed_batches': total, 'actual_total_batches': total,
              'verified_count': len(additions), 'failed_count': len(failures)})
    if not additions:
        raise ValueError(f'{total} 组新增分类的成员均未通过来源核验，原目录保留')
    # Use the current source directory only for coverage. The original
    # definitions, member lists and collection dates carry over verbatim.
    reference['topics'] = [*reference.get('topics', []), *additions]
    reference['topic_coverage'] = f'已留存 {len(reference["topics"])} 组来源成员；本次新增 {len(additions)}/{total} 组'
    for kind in CATALOGS:
        old = {row['thscode']: row for row in reference.get(kind + '_catalog', []) if row.get('thscode')}
        old.update({code: {'thscode': code, 'name': name} for code, (category, name) in catalog.items()
                    if category == kind and code not in old})
        reference[kind + '_catalog'] = list(old.values())
    from app.services.market_supplement import retained_supplement_basis
    basis = retained_supplement_basis(status)
    proof = {'scope': SCOPE, 'job_id': job_id, 'base_generation': status['snapshot_id'],
             'supplement_snapshot_id': basis, 'added_codes': [row['topic_code'] for row in additions],
             'failed_codes': failures, 'source_hashes': plan['files']}
    updated = market.publish_topic_reference(reference, proof, directory / 'raw')
    completion = {'status': 'partial' if failures else 'succeeded',
                  'message': f'已同步 {len(additions)}/{total} 组新增分类' +
                             (f'；{len(failures)} 组成员未通过核验，可重试' if failures else '；原有成员档案继续有效'),
                  'details': {'verified_count': len(additions), 'failed_count': len(failures), 'total_count': total}}
    result = register_market(updated, job_id, progress, completion=completion)
    market._publish_head(market.data_dir / 'published', updated['manifest']['summary'])
    return {**result, **completion}
