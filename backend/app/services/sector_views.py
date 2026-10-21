"""Keep dated board prices independent of constituent-history coverage."""
from copy import deepcopy
import math

from app.services.market_topic_members import membership_link


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _archived_rows(local, membership, day, indexed):
    snapshots = {row.get('thscode'): row for row in local.get('sectors_snapshot', [])}
    output = []
    for topic in local.get('topics', []):
        ids = list(dict.fromkeys(topic.get('instrument_ids', [])))
        members = [indexed[symbol] for symbol in ids if symbol in indexed]
        returns = [row['change_pct'] for row in members if _number(row.get('change_pct'))]
        amounts = [row['amount'] for row in members if _number(row.get('amount'))]
        real = snapshots.get(topic.get('topic_code'), {}) if (
            local.get('sectors_snapshot_date_semantics') == 'verified_trade_date'
            and local.get('sectors_snapshot_trade_date') == day) else {}
        limits_known = bool(members) and all(isinstance(row.get('limit_up'), bool) for row in members)
        change = real.get('price_change_ratio_pct') if real else (sum(returns) / len(returns) if returns else None)
        amount = real.get('turnover') if real else (sum(amounts) if amounts else None)
        output.append({
            'topic_id': topic['topic_id'], 'topic_code': topic.get('topic_code'),
            'name': topic['name'], 'kind': topic.get('kind', 'concept'),
            'change_pct': change, 'amount': amount, 'member_count': len(ids),
            'observed_count': len(members), 'members_available': True,
            'members_complete': topic.get('members_complete', False),
            'member_quote_coverage_complete': len(members) == len(ids),
            'limit_up_count': sum(row.get('limit_up') is True for row in members) if limits_known else None,
            'limit_up_observed_count': sum(row.get('limit_up') is True for row in members),
            'up_count': sum(_number(row.get('change_pct')) and row['change_pct'] > 0 for row in members),
            'down_count': sum(_number(row.get('change_pct')) and row['change_pct'] < 0 for row in members),
            'calculation': '来源板块指数' if real else '存档成员样本等权涨幅',
            'provider_observed_change_pct': real.get('price_change_ratio_pct'),
            'membership_observed_date': topic.get('observed_date'),
            'membership_snapshot_day': membership.get('source_snapshot_day'),
            'source_snapshot_id': membership.get('source_snapshot_id'),
            'quote_available': _number(change) or _number(amount), 'quote_date': day,
            'quote_source': 'fuyao' if real else 'archived_members',
            'quote_basis': 'provider_index' if real else 'archived_member_sample',
            'catalog_historical': False,
        })
    return output


def sector_view(day, reference, indexed, local, membership, prices):
    """Compose one day's quotes; missing members never erase provider prices."""
    archived = _archived_rows(local, membership, day, indexed)
    by_code = {row.get('topic_code'): row for row in archived if row.get('topic_code')}
    rows = []
    supplied = set()
    for source in prices.get('items', []):
        if source.get('quote_date', source.get('date')) != day:
            continue
        code = source.get('topic_code', source.get('thscode'))
        if not code or code in supplied:
            continue
        supplied.add(code)
        members = by_code.get(code)
        if members:
            row = {**members, **deepcopy(source)}
        else:
            row = {**deepcopy(source), 'member_count': None, 'observed_count': None,
                   'up_count': None, 'down_count': None, 'limit_up_count': None,
                   'limit_up_observed_count': None, 'members_available': False,
                   'members_complete': False, 'member_quote_coverage_complete': False,
                   'membership_observed_date': None, 'membership_snapshot_day': None,
                   'source_snapshot_id': None}
        row.update(quote_available=True, quote_date=day, quote_source=source.get('source', 'fuyao'),
                   quote_basis='provider_index', catalog_historical=False,
                   calculation='来源板块指数',
                   catalog_observed_date=str(prices.get('coverage',{}).get('catalog_observed_at') or '')[:10] or None,
                   provider_observed_change_pct=source.get('change_pct'))
        rows.append(row)
    rows.extend(row for row in archived if row.get('topic_code') not in supplied)
    for row in rows:
        row.update(membership_link(row, local.get('topics', [])))
    rows.sort(key=lambda row: row['change_pct'] if _number(row.get('change_pct')) else -math.inf, reverse=True)
    coverage = prices.get('coverage', {})
    if supplied:
        # Price history and member history describe different data products.
        # Current catalog names do not claim a complete historical universe.
        records = reference.get('_supplement', {}).get('sector_index_history', {})
        dates = sorted({day, *(key for key, record in records.items()
                              if isinstance(record, dict) and record.get('date') == key and key <= day)})
        price_coverage = {
            'label': '板块指数日行情实际留存', 'start': dates[0], 'end': dates[-1],
            'available_dates': dates, 'selected_date_available': True, 'partial': True,
            'basis': '来源明确日期的行业与概念指数日行情；不依赖历史成分股快照',
            'notice': f'当前同花顺目录中已核验{len(supplied)}个板块的{day}日行情；'
                      '涨幅按相邻市场交易日指数收盘计算，成交额为来源指数成交额。'
                      '目录名称与分类是当前观察，不代表完整历史目录；成分股覆盖另行核验。',
        }
    else:
        price_coverage = deepcopy(membership)
    summary = {
        'concept_count': len(reference.get('concept_catalog', local.get('concept_catalog', []))),
        'industry_count': len(reference.get('industry_catalog', local.get('industry_catalog', []))),
        'members_synced': len(local.get('topics', [])), 'index_quote_count': len(supplied),
        'quoted_concepts': sum(row.get('quote_basis') == 'provider_index' and row.get('kind') == 'concept' for row in rows),
        'quoted_industries': sum(row.get('quote_basis') == 'provider_index' and row.get('kind') == 'industry' for row in rows),
        'rank_basis': '来源板块指数日行情；无指数时才使用当日已知存档成员样本，分别标注口径',
        'catalog_historical': False,
    }
    warnings = [price_coverage['notice']]
    if not membership.get('selected_date_available'):
        warnings.append('该日历史成分股尚未留存；不影响已取得的板块指数涨幅和成交额，成分家数及涨跌停家数保持未知。')
    result = {'items': rows, 'data_coverage': price_coverage, 'quote_coverage': coverage,
              'membership_coverage': membership, 'summary': summary, 'warnings': warnings}
    if not rows:
        result['unavailable_reason'] = '该日板块指数行情尚未补充，且没有可用的当日成员样本；请在数据中心运行盘面补充'
    return result
