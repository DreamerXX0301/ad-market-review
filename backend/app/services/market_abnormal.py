"""监管名单只读视图：当前观察与指定日留存分别披露，不回填历史。"""
from datetime import datetime
from zoneinfo import ZoneInfo
from app.services.market_history import resolve_reference


def _source_day(value):
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, ZoneInfo('Asia/Shanghai')).date().isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _monitor_labels(events):
    return [{'symbol': event.get('thscode'), 'name': event.get('name'),
             'event_type': event.get('monitor_label'), 'start_date': _source_day(event.get('start_date_ms')),
             'end_date': _source_day(event.get('end_date_ms'))}
            for event in events if isinstance(event, dict)]


def monitor_view(day, status, reference, indexed, *, scope='historical', requested_date=None, market=None):
    local, coverage = resolve_reference('abnormal', day, status, reference, latest=scope == 'latest')
    observed = coverage.get('source_observed_date')
    fetched = coverage.get('source_fetched_at')
    result = {'items': [], 'monitor_scope': scope, 'requested_date': requested_date or day,
              'monitor_observed_date': observed, 'monitor_fetched_at': fetched,
              'quote_as_of': day, 'source': 'fuyao', 'data_coverage': coverage,
              'warnings': [coverage['notice'],
                           '供应商监控标签不等于交易所正式函件；供应商区间起止不是官方监管期限，官方解除日期仍未知。']}
    if not coverage['selected_date_available']:
        result['unavailable_reason'] = ('尚无观察日可核验的已发布监控名单，当前监管状态未知'
                                        if scope == 'latest' else '该日监管监控名单未留存，不能用最新名单回填历史')
        return result
    from app.infrastructure.storage import digest
    from app.services.pane_read_cache import read_block

    events = local.get('abnormal', [])
    proof = (coverage.get('source_snapshot_id'), observed, fetched, digest(events)) if market is not None else None
    labels = read_block('monitor-' + scope, day, proof, lambda: _monitor_labels(events), market=market)
    for event in labels:
        symbol = event['symbol']
        symbol = symbol if isinstance(symbol, str) and symbol else None
        quote = indexed.get(symbol, {})
        result['items'].append({**quote, 'instrument_id': symbol,
            'code': symbol[:6] if symbol else None, 'name': event.get('name') or quote.get('name') or symbol,
            'event_type': event['event_type'], 'start_date': event['start_date'],
            'end_date': event['end_date'], 'reason': '供应商监控标签',
            'source': 'fuyao', 'evidence_type': 'provider_monitor', 'official_document': None,
            'official_regulation_end_date': None, 'observed_date': observed, 'fetched_at': fetched,
            'quote_as_of': day if quote else None, 'source_snapshot_id': coverage.get('source_snapshot_id')})
    return result
