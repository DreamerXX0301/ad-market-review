"""Keep observed provider-monitor evidence distinct from official regulation.

This is a bounded projection of an already-read, dated market page. It neither
reads a current monitor list nor acquires data to fill a missing historical day.
"""


STOCK_FIELDS = ('instrument_id', 'code', 'name', 'date', 'close', 'change_pct', 'amount')
EVENT_FIELDS = ('event_type', 'start_date', 'end_date', 'observed_date', 'fetched_at',
                'evidence_type', 'official_document', 'official_regulation_end_date',
                'source', 'source_snapshot_id', 'quote_as_of', 'reason')


def _pick(value, fields):
    return {key: value[key] for key in fields if key in value}


def project_data(data, query='', *, maximum=30, stock_projector=None):
    """Filter all loaded monitor rows before cutting the model-visible list."""
    if type(maximum) is not int or not 1 <= maximum <= 30:
        raise ValueError('监管投影名单上限须为1至30')
    query = query.strip().casefold()
    rows = [row for row in data.get('items', []) if isinstance(row, dict)]
    loaded = len(rows)
    # A provider label or its interval is not a stock-name match. Keeping query
    # semantics narrow also avoids silently changing a company's event scope.
    if query:
        rows = [row for row in rows if query in ' '.join(
            str(row.get(key) or '') for key in ('instrument_id', 'code', 'name')).casefold()]
    projected = [{**(stock_projector(row) if stock_projector is not None else _pick(row, STOCK_FIELDS)),
                  **_pick(row, EVENT_FIELDS)} for row in rows[:maximum]]
    total = data.get('total')
    source_complete = type(total) is int and total == loaded
    return {
        **_pick(data, ('monitor_scope', 'requested_date', 'monitor_observed_date',
                      'monitor_fetched_at', 'quote_as_of', 'source', 'unavailable_reason')),
        'stocks': {'items': projected, 'observed_count': len(rows),
                   'returned_count': len(projected), 'truncated': len(rows) > maximum,
                   'scope': '原指定观察日已留存监控名单；按名称或代码筛选后有界返回'},
        'query': query, 'query_source_count': loaded, 'query_source_complete': source_complete,
        'scope': '供应商监控标签与区间不等于交易所正式监管函件或官方解除期限；'
                 '原证据类型、观察日和来源分别保留，官方期限未知时不推断。'
                 '未命中仅说明本次已读取名单未观察到，不证明没有监管事件。',
        'units': {'change_pct': '%', 'amount': 'CNY'},
    }
