"""Current multi-classification membership and atomic additive associations.

Market data is read once outside the SQLite write transaction. A business-date
and base-generation guard prevents silently mixing a just-published data day.
Source membership, manual overrides and missing quotes remain separate facts.
"""
from __future__ import annotations

import math

from app.core import connect, get_market
from app.services import topics
from app.services.market_supplement import load_supplement
from app.services.topic_member_metrics import member_metrics
from app.services.workspace_common import code_info, current, fail, obj, rows

SORT_FIELDS = {'change_pct', 'amount', 'turnover_rate', 'close', 'name', 'code', 'return_ytd_pct', 'change_30d_pct', 'limit_up_ytd', 'last_limit_up_date', 'sessions_since_limit_up'}


def _ids(values, label, maximum):
    if not isinstance(values, list) or not 1 <= len(values) <= maximum:
        fail(f'{label}需要1至{maximum}项')
    if any(not isinstance(value, str) or not value.strip() or len(value) > 128 for value in values):
        fail(label + '包含无效标识')
    return list(dict.fromkeys(value.strip() for value in values))


def _number(value):
    return float(value) if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) else None


def _head(connection):
    row = connection.execute("SELECT s.lineage_json FROM published_head h JOIN dataset_snapshot s ON s.snapshot_id=h.snapshot_id WHERE h.module='daily_raw' ORDER BY h.changed_at_ms DESC LIMIT 1").fetchone()
    return obj(row[0], {}).get('market_snapshot_id') if row else None


def _read_state(connection, day, catalog):
    definitions = rows(connection, '''SELECT t.*,r.name,r.description,r.aliases_json,r.archived,r.revision,r.effective_business_date FROM topic t JOIN topic_revision r ON r.topic_id=t.topic_id AND r.revision=(SELECT max(revision) FROM topic_revision WHERE topic_id=t.topic_id)''')
    overrides = rows(connection, 'SELECT *,rowid AS _order FROM topic_override WHERE effective_business_date<=? ORDER BY effective_business_date,recorded_at_ms,rowid', (day,))
    grouped = {}
    for override in overrides: grouped.setdefault(override['topic_id'], []).append(override)
    result = {}
    for definition in definitions:
        key = definition['topic_id']; meta = catalog.get(definition.get('provider_topic_key'), {})
        provider = topics._provider_members(definition, catalog)
        preloaded = grouped.get(key, [])
        members, _ = topics._members(connection, definition, day, catalog, overrides=preloaded)
        specific = {override['instrument_id']: override for override in preloaded}
        complete = definition['origin'] == 'manual' or meta.get('members_complete') is True
        public = {**{name: value for name, value in definition.items() if name != 'aliases_json'}, 'aliases': obj(definition.get('aliases_json'), []), 'archived': bool(definition['archived']), 'kind': 'manual' if definition['origin'] == 'manual' else meta.get('kind', 'concept'), 'members_complete': complete, 'historical_membership': False, 'source_observed_date': meta.get('observed_date'), 'observed_member_count': len(members), 'member_count': len(members) if complete else None}
        result[key] = {'public': public, 'members': members, 'provider': provider, 'overrides': specific}
    return result


def _select(state, selected, *, writable=False):
    missing = [key for key in selected if key not in state]
    if missing: fail('分类不存在：' + '、'.join(missing))
    if writable:
        archived = [state[key]['public']['name'] for key in selected if state[key]['public']['archived']]
        if archived: fail('归档分类不能写入，请先恢复：' + '、'.join(archived))
    return [state[key] for key in selected]


def _membership(item, ident):
    action = item['overrides'].get(ident, {}).get('action')
    manual = action if action in ('include', 'exclude') else None
    return {'origin': 'manual' if action == 'include' else 'provider', 'manual_override': manual, 'provider_member': ident in item['provider']}


def _market_context():
    with connect() as connection:
        day = current(connection); head = _head(connection)
    market = get_market()
    status = market.status() if hasattr(market, 'status') else {}
    snapshot = status.get('snapshot_id')
    if head and snapshot and snapshot != head: fail('行情发布已变化，请重新加载分类成员。', 409)
    catalog = topics._catalog_map(market)
    data = market.instruments(limit=10000)
    master = {str(item.get('instrument_id') or item.get('thscode') or item.get('code')).upper(): item for item in data}
    return market, day, head, snapshot, catalog, master


def _guard(connection, day, head):
    if current(connection) != day or _head(connection) != head:
        fail('业务日或行情发布已变化，请重新加载后操作。', 409)


def list_members(topic_ids, match='any', query='', sort_by='change_pct', sort_order='desc'):
    selected = _ids(topic_ids, 'topic_ids', 20)
    if match not in ('any', 'all'): fail('match仅支持any/all')
    if sort_by not in SORT_FIELDS or sort_order not in ('asc', 'desc'): fail('成员排序字段或方向不支持')
    if not isinstance(query, str) or len(query) > 200: fail('成员搜索最多200字')
    market, day, head, snapshot, catalog, master = _market_context()
    with connect() as connection:
        connection.execute('BEGIN')
        _guard(connection, day, head)
        state = _read_state(connection, day, catalog)
        chosen = _select(state, selected)
    observed = [item['members'] for item in chosen]
    candidates = set.union(*observed) if match == 'any' else set.intersection(*observed)
    # Exactly one panel read, with an exact observed-day check per security.
    panel = market.panel(day)
    if isinstance(panel, dict): panel = panel.get('items', panel.get('stocks', []))
    quotes = {row['instrument_id']: row for row in panel if str(row.get('date', row.get('trade_date', '')))[:10] == day and row.get('instrument_id')}
    supplement = load_supplement(snapshot) if snapshot else {}
    public = supplement.get('public_pools', {}).get(day, {})
    turnover = {row['instrument_id']: row for row in public.get('items', []) if row.get('date') == day and row.get('instrument_id')} if public.get('date') == day else {}
    historical = member_metrics(market, day, candidates, master, quotes, supplement)
    associated = {}
    for key, item in state.items():
        if item['public']['archived'] and key not in selected: continue
        for ident in item['members'] & candidates:
            associated.setdefault(ident, []).append(item['public'])
    output = []; search = query.strip().casefold()
    for ident in sorted(candidates):
        quote = quotes.get(ident); tags = sorted(associated.get(ident, []), key=lambda item: (item['kind'], item['name'], item['topic_id']))
        matches = [{**{name: item['public'][name] for name in ('topic_id', 'name', 'kind')}, **_membership(item, ident)} for item in chosen if ident in item['members']]
        rate = _number(quote.get('turnover_ratio_pct', quote.get('turnover_rate'))) if quote else None
        if rate is not None and rate < 0: rate = None
        if rate is None and quote:
            rate = _number(turnover.get(ident, {}).get('turnover_rate'))
        item = {**code_info(ident, master), 'close': _number(quote.get('close')) if quote else None, 'change_pct': _number(quote.get('change_pct', quote.get('pct_change'))) if quote else None, 'amount': _number(quote.get('amount')) if quote else None, 'turnover_rate': rate if rate is not None and rate >= 0 else None, 'quote_date': day if quote else None, 'quote_available': quote is not None, 'quote_reason': None if quote else '业务日缺少报价，不使用上一交易日报价替代', 'matched_topics': matches, 'topics': list(dict.fromkeys(tag['name'] for tag in tags if tag['kind'] != 'industry')), 'industries': list(dict.fromkeys(tag['name'] for tag in tags if tag['kind'] == 'industry')), 'price_basis': 'none', **(_membership(chosen[0], ident) if len(chosen) == 1 else {'origin': None, 'manual_override': None, 'provider_member': None})}
        item.update(historical.get(ident, {}))
        if not search or search in ' '.join([item['code'], item['name'], ident, *item['topics'], *item['industries']]).casefold(): output.append(item)
    present = [item for item in output if item.get(sort_by) is not None]
    missing = [item for item in output if item.get(sort_by) is None]
    present.sort(key=lambda item: item[sort_by].casefold() if isinstance(item[sort_by], str) else item[sort_by], reverse=sort_order == 'desc')
    complete = all(item['public']['members_complete'] for item in chosen)
    warnings = ['分类归属为当前研究成员，不代表历史题材或行业归属。']
    if not complete: warnings.append('所选分类尚未完整同步，只返回已知正成员；未出现的股票不能判为不属于该分类。')
    if any(item['public']['archived'] for item in chosen): warnings.append('包含归档分类，仅供查看；批量关联不能写入归档分类。')
    absent = sum(not item['quote_available'] for item in output)
    if absent: warnings.append(f'{absent}只股票缺少业务日报价，行情字段保留未知并排序在末尾。')
    with connect() as connection: _guard(connection, day, head)
    metric_coverage={'total':len(output),'return_ytd_complete':sum(item.get('return_ytd_pct') is not None for item in output),'change_30d_complete':sum(item.get('change_30d_pct') is not None for item in output),'limit_up_ytd_complete':sum(item.get('limit_up_ytd') is not None for item in output),'last_limit_up_complete':sum(item.get('last_limit_up_complete') is True for item in output),'price_scope':'本地公司行动事件连续价格；不是含分红到账账户收益','last_limit_scope':'仅已留存、逐原价核验的收盘涨停池；无记录不代表从未涨停'}
    return {'items': present + missing, 'total': len(output), 'business_date': day, 'quote_date': day, 'selected_topics': [item['public'] for item in chosen], 'match': match, 'members_complete': complete, 'warnings': warnings, 'sort_by': sort_by, 'sort_order': sort_order, 'snapshot_id': snapshot, 'metric_coverage':metric_coverage}


def associate(payload):
    if not isinstance(payload, dict): fail('关联参数必须是对象')
    selected = _ids(payload.get('topic_ids'), 'topic_ids', 20)
    securities = [ident.upper() for ident in _ids(payload.get('instrument_ids'), 'instrument_ids', 100)]
    securities = list(dict.fromkeys(securities))
    reason = payload.get('reason', '题材库批量人工关联')
    if not isinstance(reason, str) or len(reason) > 500: fail('关联原因最多500字')
    reason = reason.strip() or '题材库批量人工关联'
    _, day, head, _, catalog, master = _market_context()
    invalid = [ident for ident in securities if ident not in master or master[ident].get('kind', master[ident].get('asset_type', 'stock')) not in ('stock', 'equity', 'a_share', 'A')]
    if invalid: fail('股票不存在于当前证券范围：' + '、'.join(invalid))
    added = []; unchanged = []
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        _guard(connection, day, head)
        state = _read_state(connection, day, catalog)
        chosen = _select(state, selected, writable=True)
        for item in chosen:
            key = item['public']['topic_id']
            for ident in securities:
                detail = {'instrument_id': ident, 'topic_id': key, 'name': item['public']['name'], 'kind': item['public']['kind']}
                if ident in item['members']:
                    unchanged.append(detail)
                    continue
                topics._add_member(connection, key, ident, 'include', reason, day, known_ids=master)
                item['members'].add(ident); added.append(detail)
        result_topics = []
        for item in chosen:
            public = dict(item['public']); public['observed_member_count'] = len(item['members']); public['member_count'] = len(item['members']) if public['members_complete'] else None
            result_topics.append(public)
    return {'business_date': day, 'added': added, 'unchanged': unchanged, 'added_count': len(added), 'unchanged_count': len(unchanged), 'association_count': len(added) + len(unchanged), 'requested_association_count': len(selected) * len(securities), 'instrument_count': len(securities), 'topic_count': len(selected), 'selected_topics': result_topics, 'idempotency': 'effective-membership-state'}
