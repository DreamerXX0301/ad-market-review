"""Worker-prepared, fixed-generation stock facts; HTTP readers use SQLite only.

These caches contain no current topic memberships and never create tracking
rounds. A missing cache is unknown, rather than an empty successful ranking.
"""
from copy import deepcopy
from datetime import date
import json
import re

from app.core import connect, digest, dumps, now_ms
from app.services import topic_member_metrics as metrics
from app.services.limit_pool_scope import rolling_start
from app.services.market_rank_cache import read_rank_block
from app.services.market_service import _gainers_price_rank
from app.services.market_supplement import load_supplement_with_fingerprint
from app.services.market_suspensions import suspension_revision
from app.services.stock_summary import STOCK_KINDS, _daily_change, summary_fields

POOL_KEY = 'intraday-monitor-gainers-v1'
METADATA_KEY = 'intraday-monitor-metadata-v1'
VERSION = 'intraday-monitor-metadata-v1'
MAX_STOCKS = 2000
MAX_BYTES = 16 * 1024 * 1024
SYMBOL = re.compile(r'[0-9]{6}\.(?:SH|SZ|BJ)\Z')
POOL_FIELDS = ('public_pools', 'annual_pools', 'annual_down_pools')
REFERENCE_FIELDS = ('pool_history', 'pool_trade_date', 'limitup', 'limitdown', 'pool_coverage', 'pool_quarantine')


def _head(head):
    if not isinstance(head, dict):
        raise ValueError('尚未发布可准备监控指标的行情')
    day = head.get('business_date')
    if (not isinstance(day, str) or date.fromisoformat(day).isoformat() != day
            or any(not isinstance(head.get(key), str) or not head[key]
                   for key in ('market_snapshot_id', 'primary_snapshot_id'))):
        raise ValueError('监控指标的已发布行情身份无效')
    return {key: head[key] for key in ('business_date', 'market_snapshot_id', 'primary_snapshot_id')}


def _sql_inputs(connection, head):
    row = connection.execute('SELECT manifest_sha256 FROM dataset_snapshot WHERE snapshot_id=?',
                             (head['primary_snapshot_id'],)).fetchone()
    receipt = connection.execute('SELECT value_json,updated_at_ms FROM platform_setting WHERE key=?',
        ('sync-supplement-completion:' + head['market_snapshot_id'] + ':' + head['business_date'],)).fetchone()
    return digest({'primary_manifest_sha256': row[0] if row else None,
                   'supplement_publication': list(receipt) if receipt else None})


def _read(connection, key, head):
    try:
        selected = _head(head)
        row = connection.execute('SELECT value_json FROM platform_setting WHERE key=?', (key,)).fetchone()
        if row is None or len(row[0].encode()) > MAX_BYTES:
            return None
        value = json.loads(row[0])
        if (not isinstance(value, dict) or value.get('version') != VERSION or value.get('head') != selected
                or value.get('sql_inputs_sha256') != _sql_inputs(connection, selected)
                or not isinstance(value.get('input_sha256'), str)
                or value.get('content_sha256') != digest({k: v for k, v in value.items() if k != 'content_sha256'})):
            return None
        return value
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def _store(key, value, sql_inputs, guard):
    guard()
    value = {**value, 'version': VERSION, 'sql_inputs_sha256': sql_inputs, 'prepared_at_ms': now_ms()}
    value['content_sha256'] = digest(value)
    encoded = dumps(value)
    if len(encoded.encode()) > MAX_BYTES:
        raise ValueError('监控指标摘要超过有界缓存容量，未写入截断结果')
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if _sql_inputs(connection, value['head']) != sql_inputs:
            raise ValueError('指标准备期间来源发布回执已变化，未保存混合版本')
        connection.execute('''INSERT INTO platform_setting(key,value_json,updated_at_ms) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at_ms=excluded.updated_at_ms''',
            (key, encoded, now_ms()))
    return value


def read_gainers_pool(connection, head):
    """No market object, filesystem, provider or historical computation here."""
    value = _read(connection, POOL_KEY, head)
    if (value is not None and value.get('state') in ('ready', 'unknown')
            and isinstance(value.get('items'), list) and len(value['items']) <= 1000
            and all(isinstance(item, dict) and isinstance(item.get('instrument_id'), str)
                    and SYMBOL.fullmatch(item['instrument_id']) and type(item.get('rank')) is int
                    and item['rank'] == index and metrics._number(item.get('change_30d_pct')) is not None
                    for index, item in enumerate(value['items'], 1))
            and (value['state'] != 'ready' or value['items'])):
        return value
    return {'state': 'pending', 'basis_date': head.get('business_date') if isinstance(head, dict) else None,
            'market_snapshot_id': head.get('market_snapshot_id') if isinstance(head, dict) else None,
            'items': [], 'reason': '前日已发布30日涨幅前1000池尚未由唯一worker准备，数量未知'}


def read_metadata(connection, head):
    value = _read(connection, METADATA_KEY, head)
    items = value.get('items') if value else None
    if (not isinstance(items, dict) or len(items) > MAX_STOCKS
            or any(not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol)
                   or not isinstance(item, dict) for symbol, item in items.items())):
        return {}
    return items


def _context(market, head, guard, *, with_supplement=False):
    guard()
    selected = _head(head)
    day = selected['business_date']
    status = market.status()
    if status.get('snapshot_id') != selected['market_snapshot_id'] or status.get('data_end') != day:
        raise ValueError('监控指标必须绑定当前已发布观察日和固定行情代际')
    calendar = market.calendar()
    if (not isinstance(calendar, list) or calendar != sorted(set(calendar)) or day not in calendar
            or any(not isinstance(value, str) or date.fromisoformat(value).isoformat() != value for value in calendar)):
        raise ValueError('监控指标缺少完整、无重复的市场交易日历')
    verifier = getattr(market, 'verify_condition_sources', None)
    proof = verifier() if callable(verifier) else None
    if proof is None:
        raise ValueError('已发布来源缺少可核验SHA闭包，监控派生缓存保持待准备')
    calendar = [value for value in calendar if value <= day]
    inputs = {'head': selected, 'price_sources_sha256': digest(proof),
              'suspensions_sha256': digest(suspension_revision())}
    supplement, fingerprint = {}, None
    if with_supplement:
        supplement, fingerprint = load_supplement_with_fingerprint(selected['market_snapshot_id'],
            dates=[value for value in calendar if value >= rolling_start(day)], fields=POOL_FIELDS)
        inputs['closing_pool_sources_sha256'] = digest(fingerprint if fingerprint is not None else supplement)
    with connect() as connection:
        sql_inputs = _sql_inputs(connection, selected)
    guard()
    return selected, calendar, inputs, sql_inputs, supplement, fingerprint


def _master(market, day):
    return {row['instrument_id']: row for row in market.instruments(limit=10000)
            if isinstance(row, dict) and row.get('kind', row.get('asset_type', 'stock')) in STOCK_KINDS
            and isinstance(row.get('instrument_id'), str) and SYMBOL.fullmatch(row['instrument_id'])
            and (not row.get('list_date') or row['list_date'] <= day)
            and (not row.get('end_date') or row['end_date'] >= day)}


def _histories(market, symbols, start, end, guard, *, closes=False):
    reader = getattr(market, 'closing_prices_for_instruments', None) if closes else None
    if not callable(reader):
        reader = getattr(market, 'history_for_instruments', None)
    if not callable(reader):
        raise ValueError('来源没有限定证券批量历史读取能力，未扫描全市场替代')
    result = {}
    for offset in range(0, len(symbols), metrics.BATCH_SIZE):
        guard()
        batch = symbols[offset:offset + metrics.BATCH_SIZE]
        result.update(reader(batch, start=start, end=end))
    guard()
    return result


def _quotes(histories, day):
    result = {}
    for symbol, rows in histories.items():
        exact = [row for row in rows if isinstance(row, dict) and row.get('date') == day]
        if len(exact) == 1:
            result[symbol] = exact[0]
    return result


def ensure_gainers_pool(market, head, guard=lambda: None):
    selected, calendar, inputs, sql_inputs, _, _ = _context(market, head, guard)
    input_sha = digest(inputs)
    with connect() as connection:
        cached = _read(connection, POOL_KEY, selected)
    if cached and cached.get('input_sha256') == input_sha:
        return cached
    day = selected['business_date']
    master = _master(market, day)
    histories = _histories(market, sorted(master), day, day, guard)
    quoted = _quotes(histories, day)
    rows = list(quoted.values())
    unquoted = [row for symbol, row in master.items() if symbol not in quoted]
    baseline = calendar[-31] if len(calendar) >= 31 else None
    # The pure price block is shared with the existing dated 30-day ranking;
    # no annual-limit or topic decoration is requested for the full universe.
    ranked, known = read_rank_block('gainers-price-rank-v1', day,
        (inputs['price_sources_sha256'], inputs['suspensions_sha256']), market,
        lambda: _gainers_price_rank(market, day, rows, unquoted, calendar, baseline))
    guard()
    if market.status().get('snapshot_id') != selected['market_snapshot_id']:
        raise ValueError('排名准备期间行情代际已变化，未保存旧结果')
    items = [{'instrument_id': row['instrument_id'], 'rank': row['rank'],
              'change_30d_pct': row['change_30d_pct']} for row in ranked
             if type(row.get('rank')) is int and 1 <= row['rank'] <= 1000
             and metrics._number(row.get('change_30d_pct')) is not None]
    items.sort(key=lambda row: (row['rank'], row['instrument_id']))
    if len({row['instrument_id'] for row in items}) != len(items):
        raise ValueError('30日涨幅排名存在重复证券，未任取记录')
    value = dict(head=selected, state='ready' if items else 'unknown', basis_date=day,
                 market_snapshot_id=selected['market_snapshot_id'], input_sha256=input_sha, items=items,
                 known_count=known, universe_count=len(master),
                 reason=(f'仅有{len(items)}只证券具备可排名的T/T-30端点，未补足1000只' if items and len(items) < 1000
                         else None if items else '未取得可排名的完整T/T-30端点，前1000池数量未知'),
                 scope='已发布观察日T/T-30市场交易日价格排名；不创建策略轮次')
    return _store(POOL_KEY, value, sql_inputs, guard)


class _MetricView:
    """Project only dated closing pools and reuse one action batch."""
    def __init__(self, market, reference, actions, guard):
        self.market, self.reference_data, self.action_data, self.guard = market, reference, actions, guard

    def __getattr__(self, name):
        return getattr(self.market, name)

    def reference(self):
        return self.reference_data

    def actions_for_instruments(self, symbols):
        return {symbol: self.action_data.get(symbol) for symbol in symbols} if isinstance(self.action_data, dict) else None

    def history_for_instruments(self, symbols, start=None, end=None):
        self.guard()
        value = self.market.history_for_instruments(symbols, start=start, end=end)
        self.guard()
        return value


def _thirty_limits(market, day, symbols, calendar, supplement, reference, guard):
    window = calendar[-30:]
    sources = metrics._sources(reference, supplement, window)
    histories = _histories(market, symbols, window[0], day, guard, closes=True)
    result = {}
    for symbol in symbols:
        closes, duplicates = {}, set()
        for row in histories.get(symbol, []):
            observed = row.get('date')
            if observed not in sources:
                continue
            if observed in closes:
                duplicates.add(observed)
            value = metrics._number(row.get('close'))
            closes[observed] = value if value is not None and value > 0 else None
        for observed in duplicates:
            closes[observed] = None
        daily = metrics._daily_states(symbol, closes, window, sources)
        observed = sum(state['state'] is True for state in daily.values())
        covered = sum(state['state'] is not None for state in daily.values())
        complete = len(window) == 30 and covered == 30
        reason = None if complete else '近30交易日的日历、原始收盘或涨跌停来源覆盖不足；已确认次数仅为下界'
        result[symbol] = dict(limit_up_30d=observed if complete else None,
            limit_up_30d_observed=observed, limit_up_30d_complete=complete, limit_up_30d_reason=reason,
            limit_up_30d_coverage=dict(start=window[0], end=day, expected_days=30, retained_days=len(window),
                                       covered_days=covered, complete=complete))
    return result


def ensure_metadata(market, head, symbols, guard=lambda: None):
    selected, calendar, inputs, sql_inputs, supplement, fingerprint = _context(market, head, guard, with_supplement=True)
    if (not isinstance(symbols, (list, tuple)) or len(symbols) > MAX_STOCKS
            or any(not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) for symbol in symbols)):
        raise ValueError('监控指标仅准备最多2000只明确A股证券')
    symbols = sorted(set(symbols))
    input_sha = digest({**inputs, 'symbols': symbols})
    with connect() as connection:
        cached = _read(connection, METADATA_KEY, selected)
    if cached and cached.get('input_sha256') == input_sha:
        return cached['items']
    day = selected['business_date']
    master = _master(market, day)
    previous = calendar[-2] if len(calendar) >= 2 else None
    histories = _histories(market, symbols, previous or day, day, guard)
    quotes = _quotes(histories, day)
    actions = market.actions_for_instruments(symbols) if callable(getattr(market, 'actions_for_instruments', None)) else None
    original_reference = market.reference()
    reference = {key: original_reference[key] for key in REFERENCE_FIELDS if key in original_reference}
    view = _MetricView(market, reference, actions, guard)
    statistics = metrics.member_metrics(view, day, symbols, master, quotes, supplement,
                                        supplement_fingerprint=fingerprint, rolling_start=rolling_start(day))
    thirty = _thirty_limits(market, day, symbols, calendar, supplement, reference, guard) if symbols else {}
    items = {}
    for symbol in symbols:
        value = statistics.get(symbol, {})
        own_quote = None if value.get('suspension') else quotes.get(symbol)
        change, reason = _daily_change(view, symbol, day, previous, calendar, histories.get(symbol, []), own_quote)
        value = summary_fields({**value, **thirty[symbol], 'instrument_id': symbol, 'code': symbol[:6],
            'name': master.get(symbol, {}).get('name') or symbol[:6], 'basis_date': day,
            'data_date': day, 'snapshot_id': selected['market_snapshot_id'], 'input_sha256': input_sha,
            'change_pct': change, 'change_pct_reason': reason,
            'limit_up_1y_reason': None if value.get('limit_up_1y_complete') else '近一年滚动范围的日历、收盘或来源池覆盖不足；已确认次数仅为下界',
            'limit_up_1y_start': rolling_start(day), 'limit_up_1y_end': day}, master.get(symbol), day)
        for field in ('limit_up_1y', 'limit_up_1y_observed'):
            value.setdefault(field, None)
        value.setdefault('limit_up_1y_complete', False)
        # Keep display facts and compact coverage, not full per-day raw proof
        # maps or current classifications inside this fixed-generation cache.
        value.pop('suspension', None)
        for key in tuple(value):
            if key.endswith('_coverage') and isinstance(value[key], dict):
                value[key] = {name: field for name, field in value[key].items()
                              if name not in ('missing_dates', 'unknown_dates', 'counts', 'instrument_coverage')}
        items[symbol] = value
    guard()
    if market.status().get('snapshot_id') != selected['market_snapshot_id']:
        raise ValueError('指标准备期间行情代际已变化，未保存旧结果')
    _store(METADATA_KEY, dict(head=selected, state='ready', basis_date=day, input_sha256=input_sha, items=items), sql_inputs, guard)
    return items
