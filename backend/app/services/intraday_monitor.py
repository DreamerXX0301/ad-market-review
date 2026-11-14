"""Opt-in ten-minute monitoring of a deduplicated personal stock universe.

HTTP reads never collect. Only the exclusively locked market worker may enqueue
and execute this operation. Intraday observations never publish a daily candle,
change a tracking round, or start the existing closing-alert expiry clock.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import re
import time

from fastapi import HTTPException
from app.core import connect, digest, dumps, get_market, new_id, now_ms
from app.services import task_service as tasks
from app.services.increment_access import require_business_unlocked
from app.services.noon_collection import CHINA
from app.services.noon_service import _configured
from app.services.search_matching import phonetic_keys
from app.services.intraday_monitor_capture import collect_quotes
from app.services.intraday_monitor_rules import SLOTS, TITLES, evaluate_snapshot, merge_observations
from app.services.intraday_monitor_levels import automatic_baseline, manual_levels, comparable_quote, annotate_action_coverage
from app.services import intraday_monitor_storage as storage

VERSION = 'intraday-monitor-v1'
OPERATION = 'intraday_monitor'
CONFIG_KEY = 'intraday-monitor-config-v1'
CALENDAR_KEY = 'intraday-monitor-calendar-v1'
STATUS_KEY = 'intraday-monitor-status-v1'
MAX_STOCKS = 2000
CAPTURE_SECONDS = 120
CREATION_SECONDS = 60
SYMBOL = re.compile(r'\d{6}\.(?:SH|SZ|BJ)\Z')
SCOPES = ('tracking', 'watchlist', 'manual_lines', 'gainers_30d')
_next_check = 0.0
_next_maintenance = 0.0


def now_china():
    return datetime.now(CHINA)


def _day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _json(connection, key, default):
    row = connection.execute('SELECT value_json FROM platform_setting WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _save(connection, key, value):
    connection.execute('''INSERT INTO platform_setting(key,value_json,updated_at_ms) VALUES(?,?,?)
        ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at_ms=excluded.updated_at_ms''',
        (key, dumps(value), now_ms()))


def _config(connection):
    value = _json(connection, CONFIG_KEY, {
        'enabled': False, 'revision': 0, 'interval_minutes': 10,
        'scopes': {key: True for key in SCOPES}, 'manual_enabled': True, 'automatic_enabled': True,
    })
    if (not isinstance(value, dict) or type(value.get('revision')) is not int or value['revision'] < 0
            or any(type(value.get(key)) is not bool for key in ('enabled', 'manual_enabled', 'automatic_enabled'))
            or value.get('interval_minutes') != 10 or not isinstance(value.get('scopes'), dict)
            or set(value['scopes']) != set(SCOPES)
            or any(type(value['scopes'][key]) is not bool for key in SCOPES)):
        raise ValueError('盘中监控设置无效，请核验恢复副本或重新配置')
    return value


def _head(connection):
    row = connection.execute('''SELECT s.snapshot_id,s.lineage_json,s.data_end FROM published_head h
        JOIN dataset_snapshot s USING(snapshot_id) WHERE h.module='daily_raw'
        ORDER BY h.changed_at_ms DESC LIMIT 1''').fetchone()
    if not row:
        return None
    return {'primary_snapshot_id': row['snapshot_id'],
            'market_snapshot_id': json.loads(row['lineage_json']).get('market_snapshot_id') or row['snapshot_id'],
            'business_date': row['data_end']}


def _universe(connection, config):
    result = {}
    counts = {}
    pools = {
        'tracking': connection.execute("SELECT instrument_id,count(DISTINCT strategy_id) n FROM tracking_round WHERE status='tracking' GROUP BY instrument_id").fetchall(),
        'watchlist': connection.execute('SELECT instrument_id,1 n FROM watchlist_item').fetchall(),
        'manual_lines': connection.execute("SELECT instrument_id,count(*) n FROM chart_annotation WHERE instrument_kind='stock' AND line_type!='reference' AND json_array_length(alert_modes_json)>0 GROUP BY instrument_id").fetchall(),
    }
    from .intraday_monitor_metadata import read_gainers_pool
    gainers = read_gainers_pool(connection, _head(connection))
    gainers_by_id = {row['instrument_id']: row for row in gainers.get('items', [])}
    pools['gainers_30d'] = [{'instrument_id': row['instrument_id'], 'n': 1} for row in gainers.get('items', [])]
    for kind, rows in pools.items():
        counts[kind] = (None if kind == 'gainers_30d' and gainers.get('state') != 'ready' else len(rows))
        if not config['scopes'][kind]:
            continue
        for row in rows:
            symbol = row['instrument_id']
            if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
                continue
            entry = result.setdefault(symbol, {'instrument_id': symbol, 'code': symbol.split('.')[0],
                                               'name': symbol.split('.')[0], 'origins': [],
                                               'strategy_count': 0, 'manual_count': 0})
            entry['origins'].append(kind)
            if kind == 'tracking':
                entry['strategy_count'] = row['n']
            elif kind == 'manual_lines':
                entry['manual_count'] = row['n']
            elif kind == 'gainers_30d':
                entry.update(gainers_rank=gainers_by_id[symbol]['rank'],
                             gainers_return_pct=gainers_by_id[symbol].get('change_30d_pct'),
                             gainers_basis_date=gainers.get('basis_date'))
    values = [result[key] for key in sorted(result)]
    counts.update(stocks=len(values), eligible=min(MAX_STOCKS, len(values)), excluded=max(0, len(values) - MAX_STOCKS))
    counts['gainers_reason'] = gainers.get('reason')
    return values, counts


def _lines(connection):
    from .line_alerts import active_rules
    return active_rules(connection)


def _auto_rules(connection, universe):
    ids = {row['instrument_id'] for row in universe}
    return {row['instrument_id']: dict(row) for row in connection.execute('SELECT * FROM automatic_price_alert_rule')
            if row['instrument_id'] in ids}


def _signature(universe, lines, rules=None):
    return digest({'ids': [row['instrument_id'] for row in universe],
                   'lines': [{key: line.get(key) for key in ('annotation_id', 'revision', 'instrument_id', 'price',
                              'price_basis', 'anchor_date', 'line_type', 'alert_modes')} for line in lines],
                   'automatic_rules': rules or {}})


def _worker_alive(connection, at):
    value = _json(connection, 'worker_heartbeat', {})
    stamp = value.get('at_ms')
    return isinstance(stamp, (int, float)) and 0 <= int(at.timestamp() * 1000) - stamp <= 90_000


def _preconditions(connection, at):
    if not _head(connection):
        return False, '请先完成真实行情初始化'
    if not _configured(connection):
        return False, '请先配置并启用扶摇数据源'
    if not _worker_alive(connection, at):
        return False, '唯一行情worker未在线；独立预览不承担自动采集'
    return True, None


def save_settings(payload):
    expected = {'expected_revision', 'enabled', 'scopes', 'manual_enabled', 'automatic_enabled'}
    if (not isinstance(payload, dict) or set(payload) != expected
            or type(payload.get('expected_revision')) is not int or payload['expected_revision'] < 0
            or any(type(payload.get(key)) is not bool for key in ('enabled', 'manual_enabled', 'automatic_enabled'))
            or not isinstance(payload.get('scopes'), dict) or set(payload['scopes']) != set(SCOPES)
            or any(type(payload['scopes'][key]) is not bool for key in SCOPES)):
        raise HTTPException(422, '盘中监控需要完整开关、范围及当前设置版本；采集间隔固定10分钟')
    if not any(payload['scopes'].values()) or not (payload['manual_enabled'] or payload['automatic_enabled']):
        raise HTTPException(422, '至少选择一个监控范围和一种价位来源')
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        require_business_unlocked(connection)
        old = _config(connection)
        if old['revision'] != payload['expected_revision']:
            raise HTTPException(409, '盘中监控设置已变化，请重新读取后保存')
        if payload['enabled']:
            allowed, reason = _preconditions(connection, now_china())
            if not allowed:
                raise HTTPException(409, reason)
            _, counts = _universe(connection, {**old, **payload})
            if not counts['stocks']:
                raise HTTPException(409, '当前选择范围内没有可监控的股票')
            if counts['excluded']:
                raise HTTPException(409, f'第一版最多监控{MAX_STOCKS}只，请缩小范围；不会静默漏掉超额股票')
            if payload['scopes']['gainers_30d'] and counts['gainers_30d'] is None:
                raise HTTPException(409, '30日榜前1000范围尚未完成本地准备，请等待唯一worker就绪')
        new = {key: payload[key] for key in ('enabled', 'scopes', 'manual_enabled', 'automatic_enabled')}
        new.update(interval_minutes=10, revision=old['revision'])
        if new != old:
            new['revision'] += 1
            _save(connection, CONFIG_KEY, new)
        return new


def _names(universe, market):
    identities = {row['instrument_id']: row for row in market.instruments(limit=10000)}
    return [{**row, 'name': identities.get(row['instrument_id'], {}).get('name') or row['code']} for row in universe]


def _decorate(items, head):
    """Current classifications are reread; fixed-generation numeric facts reuse a small worker projection."""
    if not items:
        return items
    named_items = []
    for item in items:
        name = item.get('name')
        pinyin, initials = phonetic_keys(name) if isinstance(name, str) and not name.isdecimal() else ('', '')
        named_items.append({**item, 'search_pinyin': pinyin, 'search_initials': initials})
    if not head:
        return named_items
    from .intraday_monitor_metadata import read_metadata
    from .stock_memberships import current_memberships
    with connect() as connection:
        metrics = read_metadata(connection, head)
    memberships = current_memberships(get_market(), [row['instrument_id'] for row in items],
                                       expected_day=head['business_date'], include_unregistered_provider=True)
    return [{**_public_metrics(metrics.get(row['instrument_id'], {})), **row,
             'current_topics': [{key: topic[key] for key in (
                 'topic_id', 'name', 'kind', 'source', 'source_code', 'source_observed_date', 'classification_level')
                 if topic.get(key) is not None}
                 for topic in memberships['memberships'].get(row['instrument_id'], [])],
             'topic_date': memberships['business_date'], 'metrics_date': head['business_date'],
             'topics_scope': memberships['scope']} for row in named_items]


def _public_metrics(value):
    """One canonical field per metric; source facts and gaps remain explicit."""
    fields = ('change_30d_pct', 'return_ytd_pct', 'limit_up_30d', 'limit_up_1y',
              'limit_up_ytd', 'last_limit_up_date', 'sessions_since_limit_up')
    prefixes = ('change_30d', 'return_ytd', 'limit_up_30d', 'limit_up_1y', 'limit_up_ytd', 'last_limit_up')
    keys = {'board', 'board_label', 'board_reason', 'board_scope', 'basis_date', 'input_sha256', *fields}
    keys.update(prefix + suffix for prefix in prefixes
                for suffix in ('_coverage', '_complete', '_observed', '_start', '_end'))
    result = {key: value[key] for key in keys if key in value}
    result['metrics_reasons'] = {prefix: value[prefix + '_reason'] for prefix in prefixes
                                 if isinstance(value.get(prefix + '_reason'), str)}
    result['coverage_unknown'] = any(value.get(key) is None for key in fields) or any(
        value.get(prefix + '_complete') is False for prefix in prefixes)
    return result


def _event_view(connection, row):
    item = dict(row)
    item['code'] = item['instrument_id'].split('.')[0]
    item['title'] = TITLES.get(item['type'], item['type'])
    item['unread'] = item['last_seen_at_ms'] > item.pop('read_at_ms')
    item['sampling_only'] = True
    receipts = connection.execute('''SELECT evidence_json FROM intraday_monitor_evidence
        WHERE event_id=? ORDER BY observed_at_ms,receipt_id''', (item['event_id'],)).fetchall()
    # A source's first sighting and holding receipt are both retained, while
    # one visible source entry keeps its own original time and later phase.
    sources = {}
    for receipt in receipts:
        source = json.loads(receipt[0])
        key = source['rule_key']
        if key not in sources:
            sources[key] = source
        else:
            first = sources[key]
            sources[key] = {**source, 'observed_at_ms': first['observed_at_ms'],
                            'source_time_ms': first.get('source_time_ms'),
                            'confirmed_at_ms': source['observed_at_ms'] if source['phase'] == 'holding' else first.get('confirmed_at_ms'),
                            'receipts': [*first.get('receipts', [first]), source]}
    item['sources'] = list(sources.values())
    return item


def _events(connection, day, limit):
    total, unread = connection.execute('''SELECT count(*),coalesce(sum(last_seen_at_ms>read_at_ms),0)
        FROM intraday_monitor_event WHERE trade_date=?''', (day,)).fetchone()
    rows = connection.execute('''SELECT * FROM intraday_monitor_event WHERE trade_date=?
        ORDER BY last_seen_at_ms DESC,event_id LIMIT ?''', (day, limit)).fetchall()
    return {'date': day, 'items': [_event_view(connection, row) for row in rows], 'total': total, 'unread_count': unread}


def read_events(day=None, limit=200):
    day = day or now_china().date().isoformat()
    if not _day(day) or type(limit) is not int or not 1 <= limit <= 500:
        raise HTTPException(422, '提醒日期或数量范围无效')
    with connect() as connection:
        connection.execute('BEGIN')
        result = _events(connection, day, limit)
        head = _head(connection)
    return {**result, 'items': _decorate(result['items'], head),
            'chart_as_of': head['business_date'] if head else None}


def mark_read(payload):
    ids = payload.get('event_ids') if isinstance(payload, dict) and set(payload) == {'event_ids'} else None
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 200
            or any(not isinstance(value, str) or not 1 <= len(value) <= 100 for value in ids)):
        raise HTTPException(422, '请明确指定1至200条提醒为已读')
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        require_business_unlocked(connection)
        count = 0
        for identifier in set(ids):
            count += connection.execute('''UPDATE intraday_monitor_event SET read_at_ms=last_seen_at_ms
                WHERE event_id=? AND read_at_ms<last_seen_at_ms''', (identifier,)).rowcount
    return {'updated': count}


def read_summary():
    at = now_china()
    day = at.date().isoformat()
    with connect() as connection:
        connection.execute('BEGIN')
        config = _config(connection)
        head = _head(connection)
        result = _events(connection, day, 5)
    return {**result, 'enabled': config['enabled'], 'server_now_ms': int(at.timestamp() * 1000),
            'chart_as_of': head['business_date'] if head else None,
            'notice': '盘中采样提醒，与盘后收盘确认分别保存'}


def slot_at(at, *, creation=False):
    at = at.astimezone(CHINA)
    for key in reversed(SLOTS):
        scheduled = datetime.fromisoformat(at.date().isoformat() + 'T' + key).replace(tzinfo=CHINA)
        elapsed = (at - scheduled).total_seconds()
        if 0 <= elapsed < (CREATION_SECONDS if creation else CAPTURE_SECONDS):
            return {'key': key, 'scheduled_at': scheduled, 'deadline_at': scheduled + timedelta(seconds=CAPTURE_SECONDS)}
    return None


def _view_revision(connection, at, config, head, latest, worker_alive, scheduler, busy):
    from .intraday_monitor_metadata import METADATA_KEY, POOL_KEY

    projections = [tuple(row) for row in connection.execute('''SELECT key,updated_at_ms FROM platform_setting
        WHERE key IN (?,?) ORDER BY key''', (METADATA_KEY, POOL_KEY))]
    batch = [latest[key] for key in ('batch_id', 'status', 'completed_at_ms')] if latest else None
    return digest([at.date().isoformat(), config['revision'], head, batch, worker_alive,
                   scheduler, bool(busy), projections])


def read_revision():
    """Probe the date and latest persisted changes without decoding quotes or metadata."""
    at = now_china()
    day = at.date().isoformat()
    with connect() as connection:
        connection.execute('BEGIN')
        config = _config(connection)
        head = _head(connection)
        latest = connection.execute('''SELECT batch_id,status,completed_at_ms FROM intraday_monitor_batch
            WHERE trade_date=? ORDER BY slot_key DESC LIMIT 1''', (day,)).fetchone()
        scheduler = _json(connection, STATUS_KEY, {})
        busy = connection.execute("SELECT 1 FROM job WHERE status='running' LIMIT 1").fetchone()
        return {'date': day, 'revision': _view_revision(
            connection, at, config, head, latest, _worker_alive(connection, at), scheduler, busy)}


def read_view():
    at = now_china()
    day = at.date().isoformat()
    with connect() as connection:
        connection.execute('BEGIN')
        config = _config(connection)
        universe, counts = _universe(connection, config)
        head = _head(connection)
        allowed, reason = _preconditions(connection, at)
        worker_alive = _worker_alive(connection, at)
        latest = connection.execute('''SELECT * FROM intraday_monitor_batch WHERE trade_date=?
            ORDER BY slot_key DESC LIMIT 1''', (day,)).fetchone()
        states = {row['instrument_id']: dict(row) for row in connection.execute(
            'SELECT * FROM intraday_monitor_state WHERE trade_date=?', (day,))}
        events = _events(connection, day, 100)
        alert_counts = {row[0]: row[1] for row in connection.execute(
            'SELECT instrument_id,count(*) FROM intraday_monitor_event WHERE trade_date=? GROUP BY instrument_id', (day,))}
        scheduler = _json(connection, STATUS_KEY, {})
        busy = connection.execute("SELECT job_id,kind FROM job WHERE status='running' LIMIT 1").fetchone()
        revision = _view_revision(connection, at, config, head, latest, worker_alive, scheduler, busy)
    if head:
        universe = _names(universe, get_market())
    stocks = []
    for row in universe:
        saved = states.get(row['instrument_id'])
        quote = json.loads(saved['quote_json']) if saved else {}
        levels = json.loads(saved['levels_json']) if saved else []
        valid = quote.get('valid') is True
        public_levels = [{key: level.get(key) for key in (
            'key', 'kind', 'source_kind', 'source_id', 'revision', 'label', 'lower', 'upper', 'baseline_date', 'price_basis')}
            for level in levels]
        stocks.append({**row, 'today_alert_count': alert_counts.get(row['instrument_id'], 0),
                       'latest_price': quote.get('latest_price') if valid else None,
                       'change_pct': quote.get('change_pct') if valid else None,
                       'last_observed_at_ms': quote.get('received_at_ms'),
                       'source_time_ms': quote.get('source_time_ms'), 'quote_time_ms': None,
                       'state': 'ready' if valid and levels and not quote.get('monitoring_reason') else 'unknown' if saved else 'pending',
                       'reason': (quote.get('reason') or quote.get('monitoring_reason') or ('暂无可用提醒价位' if not levels else None)) if saved else '尚未取得本交易日盘中采样',
                       'level_count': len(levels), 'levels': public_levels})
    state = 'disabled' if not config['enabled'] else 'blocked' if not allowed else 'waiting'
    if not config['enabled']:
        reason = '盘中监控未启用；查看或刷新页面不会采集行情'
    elif busy:
        state, reason = 'delayed', '唯一worker正在执行任务；本轮不会叠加排队或补造错过的报价'
    elif latest and latest['status'] in ('succeeded', 'partial'):
        state, reason = 'ready', '最近采样已保存；仅在真实交易日和指定时段继续'
    elif scheduler.get('date') == day:
        reason = scheduler.get('reason') or reason
    if counts['excluded']:
        state, reason = 'blocked', f'当前范围超过第一版{MAX_STOCKS}只上限，请缩小范围'
        allowed = False
    if config['scopes']['gainers_30d'] and counts.get('gainers_30d') is None:
        reason = counts.get('gainers_reason') or '30日榜前1000范围尚待唯一worker完成本地准备'
        allowed = False
        if config['enabled']:
            state = 'blocked'
    batch = {key: latest[key] for key in ('batch_id', 'trade_date', 'slot_key', 'status', 'started_at_ms',
             'completed_at_ms', 'requested_count', 'valid_count', 'unknown_count', 'level_unknown_count', 'event_count', 'reason')} if latest else None
    return {'date': day, 'revision': revision, 'chart_as_of': head['business_date'] if head else None, 'settings': config,
            'status': {'state': state, 'reason': reason, 'worker_alive': worker_alive, 'can_enable': allowed},
            'counts': counts, 'last_batch': batch, 'stocks': _decorate(stocks, head),
            'events': {**events, 'items': _decorate(events['items'], head)},
            'retention': {'samples_sessions': storage.SAMPLE_SESSIONS, 'events_sessions': storage.EVENT_SESSIONS},
            'notice': '每10分钟采样；单股报价时间未知，采集时间不等于成交时间。盘中观察不等于收盘确认。'}


def _set_status(connection, day, state, reason):
    value = {'date': day, 'state': state, 'reason': reason}
    if _json(connection, STATUS_KEY, {}) != value:
        _save(connection, STATUS_KEY, value)


def local_maintenance(*, at=None, monotonic=None):
    """Only the idle worker prepares local projections and resumes bounded cleanup."""
    global _next_maintenance
    clock = time.monotonic() if monotonic is None else monotonic
    if clock < _next_maintenance:
        return
    _next_maintenance = clock + 60
    observed = at or now_china()
    with connect() as connection:
        head = _head(connection)
        config = _config(connection)
        if not head or connection.execute("SELECT 1 FROM job WHERE status IN ('queued','running') LIMIT 1").fetchone():
            return
        require_business_unlocked(connection)
        calendar = _json(connection, CALENDAR_KEY, {}).get('days', [])
    def guard():
        with connect() as connection:
            if (_head(connection) != head or _config(connection) != config
                    or connection.execute("SELECT 1 FROM job WHERE status IN ('queued','running') LIMIT 1").fetchone()):
                raise ValueError('本地监控摘要准备期间行情、范围或任务状态已变化')
            require_business_unlocked(connection)
    from .intraday_monitor_metadata import ensure_gainers_pool, ensure_metadata
    market = get_market()
    if config['scopes']['gainers_30d']:
        ensure_gainers_pool(market, head, guard=guard)
    with connect() as connection:
        universe, counts = _universe(connection, config)
    if not counts['excluded']:
        ensure_metadata(market, head, [row['instrument_id'] for row in universe], guard=guard)
    actual = [value for value in calendar if _day(value) and value <= observed.date().isoformat()]
    if actual:
        receipt = storage.cleanup(calendar, max(actual))
        with connect() as connection:
            previous = _json(connection, 'intraday-monitor-cleanup-v1', {})
            if receipt != previous:
                _save(connection, 'intraday-monitor-cleanup-v1', receipt)


def tick_automatic(*, at=None, monotonic=None):
    """Called in the unique worker after prior authorized schedules, at most /30s."""
    global _next_check
    clock = time.monotonic() if monotonic is None else monotonic
    if clock < _next_check:
        return None
    _next_check = clock + 30
    observed = at or now_china()
    try:
        local_maintenance(at=observed, monotonic=clock)
    except (OSError, ValueError, HTTPException):
        # Derived display metrics are replaceable. Their failure cannot block
        # valid manual/watchlist monitoring; an unprepared selected rank pool
        # is still rejected explicitly below, never treated as an empty pool.
        with connect() as connection:
            _set_status(connection, observed.date().isoformat(), 'preparing',
                        '部分本地范围或指标摘要尚待准备；缺口保留未知，空闲时继续核验')
    day = observed.astimezone(CHINA).date().isoformat()
    with connect() as connection:
        config = _config(connection)
    if not config['enabled']:
        return None
    slot = slot_at(observed, creation=True)
    if not slot or observed.weekday() >= 5:
        return None
    with connect() as connection:
        allowed, reason = _preconditions(connection, observed)
        if (not allowed or connection.execute("SELECT 1 FROM job WHERE status IN ('queued','running') LIMIT 1").fetchone()
                or connection.execute('SELECT 1 FROM intraday_monitor_batch WHERE trade_date=? AND slot_key=?', (day, slot['key'])).fetchone()):
            if not allowed:
                _set_status(connection, day, 'blocked', reason)
            return None
        require_business_unlocked(connection)
    from .automatic_tasks import _calendar
    calendar = _calendar('intraday_monitor', day, observed, setting_key=CALENDAR_KEY)
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        current = now_china()
        if (_config(connection) != config or slot_at(current, creation=True) != slot
                or not _preconditions(connection, current)[0]
                or connection.execute("SELECT 1 FROM job WHERE status IN ('queued','running') LIMIT 1").fetchone()):
            return None
        require_business_unlocked(connection)
        if (calendar.get('state') != 'ready' or calendar.get('source_id') != 'fuyao'
                or day not in calendar.get('days', []) or not re.fullmatch(r'[a-f0-9]{64}', calendar.get('source_sha256', ''))):
            _set_status(connection, day, 'calendar_unknown', '当天交易日历未核验或不是交易日，不采集')
            return None
        universe, counts = _universe(connection, config)
        if not universe or counts['excluded'] or config['scopes']['gainers_30d'] and counts['gainers_30d'] is None:
            _set_status(connection, day, 'blocked', '监控股票为空或超过2000只，请调整范围')
            return None
        lines = _lines(connection) if config['manual_enabled'] else []
        rules = _auto_rules(connection, universe) if config['automatic_enabled'] else {}
        head = _head(connection)
        key = f'intraday-monitor:{day}:{slot["key"]}'
        if connection.execute('SELECT 1 FROM job WHERE request_key=?', (key,)).fetchone():
            return None
        batch_id, job_id = new_id('monitor'), new_id('job')
        params = {'operation': OPERATION, 'version': VERSION, 'batch_id': batch_id, 'date': day,
                  'slot_key': slot['key'], 'scheduled_at': slot['scheduled_at'].isoformat(),
                  'deadline_at': slot['deadline_at'].isoformat(), 'config': config, 'head': head,
                  'calendar_sha256': calendar['source_sha256'], 'universe': universe, 'lines': lines,
                  'automatic_rules': rules, 'universe_signature': _signature(universe, lines, rules)}
        connection.execute('''INSERT INTO job(job_id,request_key,logical_key,kind,target_date,requested_by,
            parameters_json,parameters_hash,status,planned_batches,created_at_ms)
            VALUES(?,?,?,'source_check',?,'automatic',?,?,'queued',100,?)''',
            (job_id, key, digest(params), day, dumps(params), digest(params), now_ms()))
        connection.execute('''INSERT INTO intraday_monitor_batch(batch_id,job_id,trade_date,slot_key,config_revision,status,requested_count)
            VALUES(?,?,?,?,?,'queued',?)''', (batch_id, job_id, day, slot['key'], config['revision'], len(universe)))
        tasks.append_event(connection, job_id, 'queued', '已冻结关注池盘中采样；不修改正式日线与跟踪轮次')
    return job_id


def _guard(connection, job, parameters, *, window=True):
    active = connection.execute('SELECT status,lease_owner FROM job WHERE job_id=?', (job['job_id'],)).fetchone()
    if not active or active['status'] != 'running' or active['lease_owner'] != job['lease_owner']:
        raise tasks.TaskCancelled()
    config = _config(connection)
    if config != parameters['config'] or not config['enabled'] or _head(connection) != parameters['head']:
        raise ValueError('盘中监控设置或已发布行情已变化，本轮停止且不发布旧结果')
    universe, _ = _universe(connection, config)
    lines = _lines(connection) if config['manual_enabled'] else []
    rules = _auto_rules(connection, universe) if config['automatic_enabled'] else {}
    if _signature(universe, lines, rules) != parameters['universe_signature']:
        raise ValueError('关注范围或手动画线已变化，本轮停止且不使用旧规则')
    require_business_unlocked(connection)
    calendar = _json(connection, CALENDAR_KEY, {})
    if (calendar.get('date') != parameters['date'] or calendar.get('state') != 'ready'
            or calendar.get('source_sha256') != parameters['calendar_sha256'] or parameters['date'] not in calendar.get('days', [])):
        raise ValueError('盘中监控交易日历回执已变化')
    if window:
        observed = now_china()
        if (observed.date().isoformat() != parameters['date']
                or not datetime.fromisoformat(parameters['scheduled_at']) <= observed < datetime.fromisoformat(parameters['deadline_at'])):
            raise ValueError('盘中采样窗口已过期，不补造历史报价')
    return calendar


def _persist_observation(connection, observation, batch_id, stock, quote, current_state=None):
    """Assign each immutable source to its own episode before merging cards.

    A wide zone can participate in several price intersections over time. Its
    existing episode must never drag another card into a disjoint intersection.
    """
    changed = set()
    for original in observation.get('sources', [observation]):
        source = {**original, 'episode': f'{observation["trade_date"]}:{original["episode"]}',
                  'sampling_only': True, 'batch_id': batch_id, 'change_pct': quote.get('change_pct')}
        old_phase = connection.execute(
            'SELECT 1 FROM intraday_monitor_evidence WHERE rule_key=? AND episode=? AND type=? AND phase=?',
            (source['rule_key'], source['episode'], source['type'], source['phase'])).fetchone()
        if old_phase:
            continue
        found = connection.execute(
            'SELECT event_id FROM intraday_monitor_evidence WHERE rule_key=? AND episode=? AND type=? LIMIT 1',
            (source['rule_key'], source['episode'], source['type'])).fetchone()
        identifier = found[0] if found else None
        stamp = source['observed_at_ms']
        if identifier is None:
            candidates = connection.execute("""SELECT * FROM intraday_monitor_event
                WHERE trade_date=? AND instrument_id=? AND type=? AND lower_price<=? AND upper_price>=?
                AND last_seen_at_ms>=? ORDER BY last_seen_at_ms DESC,event_id""",
                (observation['trade_date'], stock['instrument_id'], source['type'], source['upper'],
                 source['lower'], stamp - 20 * 60_000)).fetchall()
            for candidate in candidates:
                if candidate['last_seen_at_ms'] != stamp:
                    active = current_state.get('levels', {}) if isinstance(current_state, dict) else {}
                    receipts = connection.execute('''SELECT rule_key,episode,type FROM intraday_monitor_evidence
                        WHERE event_id=?''', (candidate['event_id'],)).fetchall()
                    if not any(
                        isinstance(active.get(row['rule_key']), dict)
                        and f'{observation["trade_date"]}:{active[row["rule_key"]].get("episode")}' == row['episode']
                        and active[row['rule_key']].get('last_type') == row['type'] == candidate['type']
                        for row in receipts
                    ):
                        continue
                # A recross by this rule belongs to a new card, even seconds later.
                if not connection.execute(
                    'SELECT 1 FROM intraday_monitor_evidence WHERE event_id=? AND rule_key=?',
                    (candidate['event_id'], source['rule_key'])).fetchone():
                    identifier = candidate['event_id']
                    break
        if identifier is None:
            identifier = new_id('monitor-event')
            connection.execute("""INSERT INTO intraday_monitor_event(event_id,instrument_id,name,trade_date,type,phase,
                lower_price,upper_price,first_seen_at_ms,last_seen_at_ms,confirmed_at_ms,latest_price,change_pct)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (identifier, stock['instrument_id'], stock['name'], observation['trade_date'], source['type'], source['phase'],
                 source['lower'], source['upper'], stamp, stamp, stamp if source['phase'] == 'holding' else None,
                 quote['latest_price'], quote.get('change_pct')))
        connection.execute("""INSERT INTO intraday_monitor_evidence(receipt_id,event_id,batch_id,rule_key,
            episode,type,phase,observed_at_ms,evidence_json) VALUES(?,?,?,?,?,?,?,?,?)""",
            (new_id('monitor-proof'), identifier, batch_id, source['rule_key'], source['episode'], source['type'],
             source['phase'], stamp, dumps(source)))
        connection.execute("""UPDATE intraday_monitor_event SET last_seen_at_ms=?,latest_price=?,change_pct=?,
            lower_price=max(lower_price,?),upper_price=min(upper_price,?),
            phase=CASE WHEN ?='holding' THEN 'holding' ELSE phase END,
            confirmed_at_ms=CASE WHEN ?='holding' THEN coalesce(confirmed_at_ms,?) ELSE confirmed_at_ms END
            WHERE event_id=?""",
            (stamp, quote['latest_price'], quote.get('change_pct'), source['lower'], source['upper'],
             source['phase'], source['phase'], stamp, identifier))
        changed.add(identifier)
    return changed


def execute_capture(job):
    parameters = json.loads(job['parameters_json'])
    if parameters.get('operation') != OPERATION or parameters.get('version') != VERSION:
        raise ValueError('盘中监控任务操作或冻结版本不受支持')
    batch_id, day = parameters['batch_id'], parameters['date']
    try:
        completed_status = None
        with connect() as connection:
            leased = connection.execute('SELECT status,lease_owner FROM job WHERE job_id=?', (job['job_id'],)).fetchone()
            if not leased or leased['status'] != 'running' or leased['lease_owner'] != job['lease_owner']:
                raise tasks.TaskCancelled()
            batch = connection.execute('SELECT * FROM intraday_monitor_batch WHERE batch_id=? AND job_id=?', (batch_id, job['job_id'])).fetchone()
            if not batch or (batch['trade_date'], batch['slot_key'], batch['config_revision']) != (day, parameters['slot_key'], parameters['config']['revision']):
                raise ValueError('盘中监控批次不存在')
            if batch['status'] in ('succeeded', 'partial', 'no_change', 'failed', 'missed'):
                completed_status = batch['status'] if batch['status'] != 'missed' else 'no_change'
            else:
                calendar_receipt = _guard(connection, job, parameters)
                connection.execute("UPDATE intraday_monitor_batch SET status='running',started_at_ms=coalesce(started_at_ms,?) WHERE batch_id=?", (now_ms(), batch_id))
        if completed_status is not None:
            tasks.finish(job['job_id'], completed_status, '沿用该批次已提交的结果，不重新采集或重新判断',
                         lease_owner=job['lease_owner'])
            return
        def guard():
            with connect() as connection:
                _guard(connection, job, parameters)
        market = get_market()
        if market.status().get('snapshot_id') != parameters['head']['market_snapshot_id']:
            raise ValueError('盘中监控使用的已发布行情代际不一致')
        calendar = calendar_receipt['days']
        position = calendar.index(day)
        previous = calendar[position - 1] if position else None
        if previous is None or parameters['head']['business_date'] != previous:
            raise ValueError('前一市场交易日行情尚未发布，本轮不能准备可靠价位')
        universe = _names(parameters['universe'], market)
        symbols = [row['instrument_id'] for row in universe]
        with connect() as connection:
            cached = {row['instrument_id']: json.loads(row['baseline_json']) for row in connection.execute(
                'SELECT * FROM intraday_monitor_baseline WHERE trade_date=? AND market_snapshot_id=?', (day, parameters['head']['market_snapshot_id']))}
            old_states = {row['instrument_id']: dict(row) for row in connection.execute('SELECT * FROM intraday_monitor_state WHERE trade_date=?', (day,))}
            earlier = {row['instrument_id']: json.loads(row['baseline_json']) for row in connection.execute('''
                SELECT b.instrument_id,b.baseline_json FROM intraday_monitor_baseline b
                JOIN (SELECT instrument_id,max(trade_date) day FROM intraday_monitor_baseline WHERE trade_date<? GROUP BY instrument_id) p
                ON b.instrument_id=p.instrument_id AND b.trade_date=p.day''', (day,))}
            official = {}
            for row in connection.execute('''SELECT r.instrument_id,b.* FROM automatic_price_alert_baseline b
                    JOIN automatic_price_alert_rule r USING(rule_id) WHERE b.rule_revision=r.revision'''):
                official[row['instrument_id']] = {**json.loads(row['baseline_json']), 'rule_id': row['rule_id'],
                                                   'rule_revision': row['rule_revision']}
        rules = parameters.get('automatic_rules', {})
        cached = {symbol: value for symbol, value in cached.items()
                  if value.get('configuration_signature') == digest(rules.get(symbol))}
        missing = [symbol for symbol in symbols if symbol not in cached]
        start = calendar[max(0, position - 130)]
        histories = market.history_for_instruments(missing, start=start, end=previous) if missing else {}
        actions = market.actions_for_instruments(symbols)
        histories = annotate_action_coverage(histories, actions)
        for index, symbol in enumerate(missing):
            if index % 64 == 0:
                guard()
            cached[symbol] = automatic_baseline(symbol, histories.get(symbol, []), previous_day=previous,
                calendar=calendar, snapshot=parameters['head']['market_snapshot_id'], rule=rules.get(symbol),
                saved_baseline=official.get(symbol) or earlier.get(symbol),
                primary_snapshot=parameters['head']['primary_snapshot_id'])
            cached[symbol]['configuration_signature'] = digest(rules.get(symbol))
        guard()
        prepared, level_unknowns = {}, {}
        for stock in universe:
            symbol = stock['instrument_id']
            baseline = cached[symbol]
            levels = list(baseline['levels']) if parameters['config']['automatic_enabled'] else []
            reasons = [baseline.get(key) for key in ('reason', 'risk_reason') if baseline.get(key)] if parameters['config']['automatic_enabled'] else []
            if parameters['config']['manual_enabled']:
                selected = [line for line in parameters['lines'] if line['instrument_id'] == symbol]
                manual, unknowns = manual_levels(selected, previous_day=previous,
                                           actions=actions.get(symbol) if isinstance(actions, dict) else None)
                levels.extend(manual)
                reasons.extend(item['reason'] for item in unknowns)
            prepared[symbol] = levels
            level_unknowns[symbol] = list(dict.fromkeys(reasons or ([] if levels else ['暂无可用的手动线或已确认自动价位'])))
        from .source_service import client
        def register_manifest(value):
            with connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                _guard(connection, job, parameters)
                connection.execute('UPDATE intraday_monitor_batch SET manifest_json=? WHERE batch_id=?', (dumps(value), batch_id))
                from .workspace_common import pin
                from .automatic_price_alerts import _baseline_sources
                snapshots = {parameters['head']['primary_snapshot_id']}
                for baseline in cached.values():
                    monitoring = baseline.get('monitoring_baseline')
                    if isinstance(monitoring, dict):
                        snapshots.update(_baseline_sources(monitoring))
                for snapshot in snapshots:
                    pin(connection, snapshot, 'manual', 'intraday-monitor:' + batch_id)
        saved = storage.recover_prepared(batch_id, day, json.loads(batch['manifest_json']), register_manifest=register_manifest)
        if saved:
            result = storage.load_capture(saved, batch_id, day)
            manifest = saved
        else:
            result = collect_quotes(symbols, day=day, scheduled_at=datetime.fromisoformat(parameters['scheduled_at']),
                                    deadline_at=datetime.fromisoformat(parameters['deadline_at']), adapter=client('fuyao'),
                                    guard=guard, now=now_china)
            guard()
            manifest = storage.save_capture(batch_id, day, result, calendar=calendar,
                                            generation=parameters['head']['market_snapshot_id'],
                                            primary_snapshot_id=parameters['head']['primary_snapshot_id'],
                                            register_manifest=register_manifest)
        evaluations = {}
        valid = 0
        for stock in universe:
            symbol = stock['instrument_id']
            quote = comparable_quote(result['quotes_by_id'][symbol], cached[symbol])
            quote = {**quote, 'monitoring_reason': '；'.join(level_unknowns[symbol]) or None,
                     'level_unknowns': level_unknowns[symbol]}
            valid += int(quote.get('valid') is True)
            old = old_states.get(symbol)
            state = json.loads(old['state_json']) if old and old['config_revision'] == parameters['config']['revision'] else None
            evaluated = evaluate_snapshot(prepared[symbol], quote, state, trade_date=day, slot_key=parameters['slot_key'])
            if evaluated['unknowns']:
                reasons = [item.get('reason', '盘中规则状态待核验') for item in evaluated['unknowns']]
                level_unknowns[symbol] = list(dict.fromkeys([*level_unknowns[symbol], *reasons]))
                quote['monitoring_reason'] = '；'.join(level_unknowns[symbol])
                quote['level_unknowns'] = level_unknowns[symbol]
            evaluations[symbol] = (quote, evaluated)
        new_events = set()
        with connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            _guard(connection, job, parameters)
            for symbol in missing:
                connection.execute('''INSERT INTO intraday_monitor_baseline VALUES(?,?,?,?)
                    ON CONFLICT(trade_date,instrument_id) DO UPDATE SET market_snapshot_id=excluded.market_snapshot_id,
                    baseline_json=excluded.baseline_json''',
                    (day, symbol, parameters['head']['market_snapshot_id'], dumps(cached[symbol])))
                from .automatic_price_alerts import _baseline_sources
                from .workspace_common import pin
                owner = 'intraday-monitor-baseline:' + symbol
                connection.execute("DELETE FROM retention_pin WHERE owner_kind='manual' AND owner_id=?", (owner,))
                monitoring = cached[symbol].get('monitoring_baseline')
                snapshots = _baseline_sources(monitoring) if isinstance(monitoring, dict) else set()
                for snapshot in snapshots:
                    pin(connection, snapshot, 'manual', owner)
            for stock in universe:
                symbol = stock['instrument_id']
                quote, evaluated = evaluations[symbol]
                connection.execute('''INSERT INTO intraday_monitor_state VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(instrument_id) DO UPDATE SET trade_date=excluded.trade_date,slot_key=excluded.slot_key,
                    config_revision=excluded.config_revision,state_json=excluded.state_json,quote_json=excluded.quote_json,
                    levels_json=excluded.levels_json,updated_at_ms=excluded.updated_at_ms''',
                    (symbol, day, parameters['slot_key'], parameters['config']['revision'], dumps(evaluated['state']),
                     dumps(quote), dumps(prepared[symbol]), now_ms()))
                for observation in merge_observations(evaluated['observations']):
                    new_events.update(_persist_observation(connection, observation, batch_id, stock, quote, evaluated['state']))
            unknown = len(universe) - valid
            status = 'succeeded' if unknown == 0 else 'partial'
            connection.execute('''UPDATE intraday_monitor_batch SET status=?,valid_count=?,unknown_count=?,event_count=?,
                level_unknown_count=?,completed_at_ms=?,reason=? WHERE batch_id=?''',
                (status, valid, unknown, len(new_events), sum(bool(value) for value in level_unknowns.values()), now_ms(),
                 '部分报价或提醒价位待核验' if unknown or any(level_unknowns.values()) else None, batch_id))
        tasks.finish(job['job_id'], status, f'盘中采样完成：{valid}/{len(universe)}只报价可用，{len(new_events)}条提醒更新', lease_owner=job['lease_owner'])
        try:
            storage.cleanup(calendar, day)
        except (OSError, ValueError):
            with connect() as connection:
                tasks.append_event(connection, job['job_id'], 'monitor_cleanup_pending',
                                   '盘中采样已发布；清理保留检查点，由唯一worker空闲时继续', 'warning')
    except (tasks.TaskPaused, tasks.TaskCancelled):
        raise
    except Exception as error:
        with connect() as connection:
            connection.execute("UPDATE intraday_monitor_batch SET status='failed',completed_at_ms=?,reason=? WHERE batch_id=? AND status IN ('queued','running')",
                               (now_ms(), str(error) if isinstance(error, ValueError) else '本轮采样未完成，等待下一时段', batch_id))
        raise
