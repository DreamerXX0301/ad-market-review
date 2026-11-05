"""Point-in-time stock details for rows selected in the intraday workbench."""
from datetime import datetime, time
from math import isfinite
import re

from duckdb import Error as DuckDBError
from fastapi import HTTPException

from app.core import connect, get_market
from app.services import intraday_service as intraday
from app.services.market_supplement import load_supplement_with_fingerprint
from app.services.market_suspensions import suspension_status
from app.services.noon_context import _event_cohorts
from app.services.stock_summary import STOCK_KINDS, summary_fields
from app.services.strategy_limits import count_window, quote_index
from app.services.supplement_access import supplement_reader
from app.services.topic_member_metrics import member_metrics

MAX_SYMBOLS = 32
POOL_FIELDS = ('public_pools', 'annual_pools', 'annual_down_pools')
HISTORY_FIELDS = ('change_30d_pct', 'change_30d_reason', 'change_30d_coverage',
                  'return_ytd_pct', 'return_ytd_reason', 'return_ytd_coverage',
                  'last_limit_up_date', 'last_limit_up_complete', 'last_limit_up_reason',
                  'limit_up_1y', 'limit_up_1y_observed', 'limit_up_1y_complete', 'board_label')


def closing_for_symbols(day, requested, *, market=None):
    """A separate, published close comparison; it never replaces the selected slot."""
    result = {'date': day, 'closing_date': None, 'closing_snapshot_id': None, 'items': {}}
    try:
        market = market or get_market()
        symbols = list(dict.fromkeys(requested))
        if len(symbols) > 10000 or not all(isinstance(symbol, str) and intraday.STOCK_CODE.fullmatch(symbol) for symbol in symbols):
            return result
        status = market.status()
        generation, end = status.get('snapshot_id'), status.get('data_end')
        if not status.get('ready') or not generation or not end or day > end:
            return result
        if hasattr(market, 'data_dir'):
            from app.services.noon_collection import CHINA
            from app.services.noon_prior_events import _file, MAX_MANIFEST, MAX_REFERENCE

            manifest = status.get('manifest') or {}
            summary = manifest.get('summary') or {}
            published = datetime.fromisoformat(manifest['published_at'])
            close_at = datetime.combine(datetime.fromisoformat(day).date(), time(15), CHINA)
            if (summary.get('snapshot_id') != generation or summary.get('data_end') != end
                    or summary.get('source') != 'fuyao' or summary.get('price_basis') != 'none'
                    or published.tzinfo is None or published < close_at):
                return result
            root = market.data_dir / 'published'
            _file(root, generation, status, 'reference.json', ('reference.json',), MAX_REFERENCE)
            # Partitioned installations verify the small monthly descriptor;
            # the market reader verifies its selected immutable price objects.
            # A legacy monolithic source still needs its original file SHA.
            daily_files = [row for row in status.get('files', ()) if row.get('role') == 'daily.parquet']
            daily_budget = (MAX_MANIFEST if len(daily_files) == 1
                            and daily_files[0].get('path') == f'published/{generation}/daily.manifest.json'
                            else 4 * 1024 * 1024 * 1024)
            _file(root, generation, status, 'daily.parquet', ('daily.manifest.json', 'daily.parquet'), daily_budget)
        calendar = market.calendar()
        if (not isinstance(calendar, list) or len(calendar) > 15000
                or day not in calendar or calendar != sorted(set(calendar))):
            return result
        previous = max((value for value in calendar if value < day), default=None)
        from app.services.intraday_members import _closing_quotes

        # This is bounded by the caller's original member list (or 32-row detail
        # batch), and reads two explicit sessions rather than an annual panel.
        quotes = _closing_quotes(market, symbols, day, previous, strict=True)
        final = market.status()
        if (final.get('snapshot_id'), final.get('data_end')) != (generation, end):
            return {**result, 'reason': '行情发布代际变化，请刷新'}
        items = {}
        for symbol in symbols:
            quote = quotes.get(symbol, {})
            value = quote.get('change_pct')
            items[symbol] = {
                'closing_change_pct': value,
                'closing_quote_date': day if quote else None,
                'closing_snapshot_id': generation,
                'closing_reason': None if value is not None else '该日收盘或可比前收未能核验',
            }
        return {**result, 'closing_date': day, 'closing_snapshot_id': generation, 'items': items}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, DuckDBError):
        return result


@supplement_reader
def read_history(day, slot, symbols):
    day = intraday.day_value(day)
    slots = {item['key'] for item in (*intraday.SLOTS, *intraday.LEGACY_SLOTS)}
    if slot not in slots | {'auction', 'noon', 'close'}:
        raise HTTPException(422, '请选择盘中时点')
    requested = symbols.split(',')
    if len(requested) > MAX_SYMBOLS or not all(intraday.STOCK_CODE.fullmatch(symbol) for symbol in requested):
        raise HTTPException(422, '每次最多读取32个完整股票代码')
    return history_for_symbols(day, slot, requested)


def history_for_symbols(day, slot, requested, *, market=None, closing=None):
    """Read history for a validated, bounded set of stock codes."""
    market = market or get_market()
    status = market.status()
    def finish(result):
        current = market.status()
        if (current.get('snapshot_id'), current.get('data_end')) != (status.get('snapshot_id'), status.get('data_end')):
            return {'date': day, 'slot': slot, 'as_of': None, 'items': {},
                    'closing_date': None, 'closing_snapshot_id': None, 'reason': '行情发布代际变化，请刷新'}
        return result

    calendar = market.calendar() if status.get('ready') else []
    end = status.get('data_end') or ''
    # A replay at an early slot retains its previous close cutoff even after
    # that day's full candle and corporate action history have been published.
    previous_day = max((d for d in calendar if d <= end and d < day), default=None)
    as_of = day if slot == 'close' and day in calendar and day <= end else previous_day
    closing = closing if closing is not None else closing_for_symbols(day, requested, market=market)
    if (closing.get('reason') == '行情发布代际变化，请刷新'
            or closing.get('closing_snapshot_id') not in (None, status.get('snapshot_id'))
            or closing.get('closing_date') not in (None, day)):
        return {'date': day, 'slot': slot, 'as_of': None, 'items': {},
                'closing_date': None, 'closing_snapshot_id': None, 'reason': '行情发布代际变化，请刷新'}
    # A member close sort can supply its already verified whole-set comparison.
    # Copy only this page's rows so enrichment cannot mutate the sort inputs.
    closing_items = {symbol: dict(closing['items'][symbol]) for symbol in requested if symbol in closing['items']}
    result = {'date': day, 'slot': slot, 'as_of': as_of, 'items': dict(closing_items),
              'closing_date': closing['closing_date'], 'closing_snapshot_id': closing['closing_snapshot_id']}
    if not as_of or slot == 'close' and as_of != day:
        return finish({**result, 'as_of': None})
    wanted = set(requested)
    master = {row['instrument_id']: row for row in market.instruments(limit=10000)
              if row.get('kind', row.get('asset_type', 'stock')) in STOCK_KINDS
              and row['instrument_id'] in wanted}
    if not master:
        return finish(result)
    daily = market.history_for_instruments(list(master), start=as_of, end=as_of)
    quotes = {symbol: quote_index(daily.get(symbol, ())).get(as_of) for symbol in master}
    quotes = {symbol: row for symbol, row in quotes.items() if row}
    snapshot_id = status.get('snapshot_id')
    supplement, fingerprint = load_supplement_with_fingerprint(
        snapshot_id, dates=[d for d in calendar if d <= as_of], fields=POOL_FIELDS)
    previous_heights = {}
    if previous_day:
        previous_daily = (daily if as_of == previous_day else
                          market.history_for_instruments(list(master), start=previous_day, end=previous_day))
        previous_quotes = {symbol: quote_index(rows).get(previous_day) for symbol, rows in previous_daily.items()}
        prior_cohorts = _event_cohorts({**market.reference(), '_supplement': supplement}, previous_day, previous_quotes)
        previous_heights = {row['instrument_id']: row['previous_board_count'] for row in prior_cohorts['limit_up']['items']}
    metrics = member_metrics(market, as_of, list(master), master, quotes, supplement,
                             supplement_fingerprint=fingerprint, rolling_start=count_window(day)[0])
    for symbol, values in metrics.items():
        enriched = summary_fields(values, master[symbol], as_of)
        result['items'][symbol] = {**{key: enriched.get(key) for key in HISTORY_FIELDS},
                                   **closing_items.get(symbol, {}),
                                   'history_as_of': as_of, 'previous_board_count': previous_heights.get(symbol)}
    for symbol, values in closing_items.items():
        result['items'].setdefault(symbol, values)
    return finish(result)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def read_quote(snapshot_id, symbol):
    if not re.fullmatch(r'intraday_[0-9a-f]{32}', snapshot_id) or not intraday.STOCK_CODE.fullmatch(symbol):
        raise HTTPException(422, '盘中快照或股票代码无效')
    with connect() as connection:
        row = connection.execute('SELECT * FROM market_intraday_capture WHERE snapshot_id=?', (snapshot_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '盘中快照未留存或已清理')
    snapshot = intraday.decode_snapshot(row)
    day = snapshot['date']
    previous = max((d for d in get_market().calendar() if d < day), default=None)
    result = {'snapshot_id': snapshot_id, 'trade_date': day, 'previous_trade_date': previous,
              'collected_end_at': snapshot['collected_end_at'], 'slot_label': snapshot['slot_label'],
              'quote': None, 'reason': '该快照的开盘、最高、最低或最新价未能完整核验'}
    matches = [quote for quote in snapshot.get('comparison_input', {}).get('stocks', ())
               if quote.get('instrument_id') == symbol]
    evidence = suspension_status(symbol, day)
    if (evidence and len(matches) <= 1
            and not any(_number(quote.get(key)) and quote[key] > 0
                        for quote in matches for key in ('amount', 'volume'))):
        return {**result, 'suspension': evidence, 'reason': '该股票当日停牌，无该时点临时日 K'}
    if len(matches) != 1:
        return result
    quote = matches[0]
    required = ('open', 'high', 'low', 'latest_price', 'prev_close')
    if quote.get('price_basis') != 'none' or any(not _number(quote.get(key)) or quote[key] <= 0 for key in required):
        return result
    if not quote['low'] <= min(quote['open'], quote['latest_price']) <= max(quote['open'], quote['latest_price']) <= quote['high']:
        return result
    vetted = {key: quote[key] for key in ('instrument_id', *required)}
    vetted.update({key: quote.get(key) if _number(quote.get(key)) else None for key in ('amount', 'volume', 'change_pct')})
    from app.services.chart_patterns import attach_intraday_board_patterns
    from datetime import datetime
    from app.services.intraday_board_changes import BASELINES, build_changes, _pools, read_noon_row, noon_frame

    resealed = False
    if symbol in _pools(snapshot)['limit_up'][0] and (baseline := BASELINES.get(snapshot.get('slot'))) is not None:
        cutoff = int(datetime.fromisoformat(snapshot['collected_start_at']).timestamp() * 1000)
        with connect() as connection:
            tables = {value[0] for value in connection.execute('SELECT name FROM sqlite_master WHERE type=\'table\'')}
            if baseline == 'noon':
                before_row = read_noon_row(connection, day)
                if before_row is not None and before_row['recorded_at_ms'] > cutoff:
                    before_row = None
            else:
                before_row = connection.execute('''SELECT c.* FROM market_intraday_capture c JOIN job j USING(job_id)
                    WHERE c.trade_date=? AND c.slot=? AND j.status IN ('succeeded','partial')
                    AND j.finished_at_ms<=? AND length(CAST(c.payload_json AS BLOB))<=?''',
                    (day, baseline, cutoff, 2 * 1024 * 1024)).fetchone() if {'market_intraday_capture', 'job'} <= tables else None
        if before_row is not None:
            try:
                before = noon_frame(before_row) if baseline == 'noon' else intraday.decode_snapshot(before_row)
                changes = build_changes(snapshot, before, current_row=row, previous_row=before_row)
                resealed = any(item['instrument_id'] == symbol for item in changes['groups']['resealed']['items'])
            except (ValueError, TypeError, KeyError, HTTPException):
                pass
    attach_intraday_board_patterns(vetted, snapshot, content_sha256=row['content_sha256'], resealed=resealed)
    return {**result, 'quote': vetted, 'reason': None}
