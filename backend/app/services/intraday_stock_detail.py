"""Point-in-time stock details for rows selected in the intraday workbench."""
from math import isfinite
import re

from fastapi import HTTPException

from app.core import connect, get_market
from app.services import intraday_service as intraday
from app.services.market_supplement import load_supplement_with_fingerprint
from app.services.noon_context import _event_cohorts
from app.services.stock_summary import STOCK_KINDS, summary_fields
from app.services.strategy_limits import count_window, quote_index
from app.services.supplement_access import supplement_reader
from app.services.topic_member_metrics import member_metrics

MAX_SYMBOLS = 32
POOL_FIELDS = ('public_pools', 'annual_pools', 'annual_down_pools')
HISTORY_FIELDS = ('change_30d_pct', 'change_30d_reason', 'return_ytd_pct', 'return_ytd_reason',
                  'last_limit_up_date', 'last_limit_up_complete', 'last_limit_up_reason',
                  'limit_up_1y', 'limit_up_1y_observed', 'limit_up_1y_complete', 'board_label')


@supplement_reader
def read_history(day, slot, symbols):
    day = intraday.day_value(day)
    slots = {item['key'] for item in (*intraday.SLOTS, *intraday.LEGACY_SLOTS)}
    if slot not in slots | {'auction', 'noon', 'close'}:
        raise HTTPException(422, '请选择盘中时点')
    requested = symbols.split(',')
    if len(requested) > MAX_SYMBOLS or not all(intraday.STOCK_CODE.fullmatch(symbol) for symbol in requested):
        raise HTTPException(422, '每次最多读取32个完整股票代码')
    market = get_market()
    status = market.status()
    calendar = market.calendar() if status.get('ready') else []
    end = status.get('data_end') or ''
    # A replay at an early slot retains its previous close cutoff even after
    # that day's full candle and corporate action history have been published.
    previous_day = max((d for d in calendar if d <= end and d < day), default=None)
    as_of = day if slot == 'close' and day in calendar and day <= end else previous_day
    result = {'date': day, 'slot': slot, 'as_of': as_of, 'items': {}}
    if not as_of or slot == 'close' and as_of != day:
        return {**result, 'as_of': None}
    master = {row['instrument_id']: row for row in market.instruments(limit=10000)
              if row.get('kind', row.get('asset_type', 'stock')) in STOCK_KINDS
              and row['instrument_id'] in requested}
    if not master:
        return result
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
                                   'history_as_of': as_of, 'previous_board_count': previous_heights.get(symbol)}
    return result


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
    return {**result, 'quote': vetted, 'reason': None}
