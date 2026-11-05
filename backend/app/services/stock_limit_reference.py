"""Pure regular-price reference; the result cannot certify a source-pool event."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date
import re

from app.services.noon_analysis import number

TICK = Decimal('.01')
SYMBOL = re.compile(r'[0-9]{6}\.(?:SH|SZ|BJ)\Z')


def price_tick(value):
    value = number(value, positive=True)
    if value is None:
        return None
    try:
        exact = Decimal(str(value))
        rounded = exact.quantize(TICK, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    return rounded if rounded > 0 and abs(exact - rounded) <= Decimal('.0000001') else None


def standard_limit_rate(symbol, name):
    name = str(name or '').strip().upper()
    if (not SYMBOL.fullmatch(symbol) or not name
            or name.startswith(('ST', '*ST', 'SST', 'S*ST', 'N', 'C'))):
        return None
    code, exchange = symbol.split('.')
    if exchange == 'SH' and code.startswith(('688', '689')) or exchange == 'SZ' and code.startswith(('300', '301')):
        return 20
    if exchange == 'SH' and code.startswith(('600', '601', '603', '605')) or exchange == 'SZ' and code.startswith(('000', '001', '002', '003')):
        return 10
    if exchange == 'BJ' and code.startswith(('4', '8', '9')):
        return 30
    return None


def upper_limit_at_rate(previous, rate):
    upper = (previous * (1 + Decimal(rate) / 100)).quantize(TICK, rounding=ROUND_HALF_UP)
    return max(upper, previous + TICK)


def attach_intraday_references(snapshot, rows, master, calendar):
    """Label exact matches on already displayed stock rows with dated identity."""
    cutoff = sorted({value for value in calendar if value < snapshot['date']})[-5:]
    if len(cutoff) < 5:
        return
    sealed = {row['instrument_id'] for row in snapshot.get('sealed_pool_input', {}).get('items', ())}
    displayed = {}
    for row in rows:
        if (row.get('limit_up_state') not in ('sealed', 'broken')
                and row['instrument_id'] not in sealed
                and (number(row.get('change_pct')) or 0) > 0):
            displayed.setdefault(row['instrument_id'], []).append(row)
    if not displayed:
        return
    quotes = {}
    for quote in snapshot.get('comparison_input', {}).get('stocks', ()):
        symbol = quote['instrument_id']
        if symbol in displayed:
            quotes[symbol] = quote if symbol not in quotes else {}
    for symbol, quote in quotes.items():
        identity = master.get(symbol, {})
        listed = identity.get('list_date')
        try:
            known_listing = isinstance(listed, str) and date.fromisoformat(listed).isoformat() == listed
        except ValueError:
            known_listing = False
        rate = standard_limit_rate(symbol, identity.get('name'))
        if (rate is None or identity.get('kind') != 'stock' or not known_listing or listed > cutoff[0]
                or quote.get('price_basis') != 'none' or quote.get('currency') != 'CNY'
                or number(quote.get('amount'), positive=True) is None):
            continue
        price, previous = price_tick(quote.get('latest_price')), price_tick(quote.get('prev_close'))
        change = number(quote.get('change_pct'))
        if (price is None or previous is None or change is None or abs((float(price / previous) - 1) * 100 - change) > .001
                or price != upper_limit_at_rate(previous, rate)):
            continue
        for row in displayed[symbol]:
            if (row['change_pct'] == change and standard_limit_rate(symbol, row.get('name') or identity['name']) == rate):
                row['limit_up_reference_price'] = float(price)
