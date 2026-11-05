"""Read-only, bounded rotation quotes, separate from the displayed strength ranks."""
from collections import Counter
import re
import sqlite3

MAX_ROTATION_SECTORS = 1500
CODE = re.compile(r'\d{6}\.TI')
STOCK = re.compile(r'\d{6}\.(?:SH|SZ|BJ)')


def _quote_projection(quote, name=None):
    from app.services.intraday_brief import sector_comparison_metadata
    from app.services.noon_analysis import number

    code = quote.get('topic_code') or quote.get('instrument_id')
    price = number(quote.get('latest_price'), positive=True)
    if (not CODE.fullmatch(str(code)) or quote.get('kind') not in ('industry', 'concept')
            or quote.get('price_basis', 'none') != 'none' or quote.get('currency', 'CNY') != 'CNY'
            or price is None):
        return None
    previous = number(quote.get('prev_close'), positive=True)
    change = number(quote.get('change_pct'))
    if previous is not None and change is not None and abs((price / previous - 1) * 100 - change) > .001:
        change = None
    row = {'instrument_id': code, 'name': quote.get('name') or name or code, 'kind': quote['kind'],
           'latest_price': price, 'change_pct': change, 'amount': quote.get('amount'),
           'quote_source': 'fuyao', 'quote_basis': 'provider_index', 'price_basis': 'none', 'currency': 'CNY',
           'limit_up_count': None, 'limit_up_count_state': 'unknown'}
    if previous is not None:
        row['prev_close'] = previous
    row.update(sector_comparison_metadata(row, source='fuyao'))
    return row


def live_rotation_sectors(snapshot, *, reference=None, identity=None):
    """Use every retained quote; only the capture's original member generation is eligible."""
    from app.services.intraday_brief import sector_membership_fingerprint
    from app.services.noon_membership_context import MAX_REFERENCE_BYTES, _day

    quotes = snapshot.get('comparison_input', {}).get('sectors', ())
    if not isinstance(quotes, (list, tuple)) or len(quotes) > MAX_ROTATION_SECTORS:
        return []
    if identity is None:
        try:
            from app.services.intraday_members import _member_provenance
            from app.services.auction_topic_summary import _load_original_topics

            generation, end, reference_sha = _member_provenance(snapshot)
            if reference_sha is not None:
                from app.analytics.market_storage import safe_object, verify_immutable_file
                from app.core import published_dir

                path = safe_object(published_dir(), generation + '/reference.json')
                size = path.stat().st_size
                if not 0 < size <= MAX_REFERENCE_BYTES:
                    raise ValueError('原成员参考文件超出读取边界')
                verify_immutable_file(path, reference_sha, size)
            # The loader verifies the immutable, original generation; no latest-name fallback.
            topics, _ = _load_original_topics(generation, end, end if reference_sha is None else None)
            reference, identity = {'topics': topics}, (generation, end)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error):
            reference, identity = {}, None
    topics = [row for row in (reference or {}).get('topics', ()) if isinstance(row, dict)]
    identities = Counter(row.get('topic_code') for row in topics)
    original = {row['topic_code']: row for row in topics
                if isinstance(row.get('topic_code'), str) and identities[row['topic_code']] == 1}
    quote_ids = Counter(row.get('instrument_id') for row in quotes if isinstance(row, dict))
    pool = snapshot.get('sealed_pool_input') or {}
    pool_rows = pool.get('items') or []
    sealed = {row['instrument_id'] for row in pool_rows if isinstance(row, dict)
              and STOCK.fullmatch(str(row.get('instrument_id')))}
    pool_complete = (pool.get('state') == 'observed' and len(pool_rows) == len(sealed))
    result = []
    for quote in quotes:
        if not isinstance(quote, dict):
            continue
        code = quote.get('instrument_id')
        topic = original.get(code, {})
        row = _quote_projection(quote, topic.get('name'))
        if row is None or quote_ids[code] != 1:
            continue
        symbols, observed = topic.get('instrument_ids'), topic.get('observed_date')
        if (identity and topic.get('source') == 'fuyao' and topic.get('kind') == quote['kind']
                and _day(observed) and observed <= identity[1] < snapshot['date']
                and isinstance(symbols, list) and 0 < len(symbols) <= 10000
                and all(isinstance(symbol, str) and STOCK.fullmatch(symbol) for symbol in symbols)
                and len(symbols) == len(set(symbols))):
            complete = topic.get('members_complete') is True
            count = len(set(symbols) & sealed)
            exact = complete and pool_complete
            row.update(member_count=len(symbols), member_date=observed, member_list_complete=complete,
                       limit_up_count=count if exact or count else None,
                       limit_up_count_state='observed' if exact else 'partial' if count else 'unknown')
            if complete:
                row['member_reference_sha256'] = sector_membership_fingerprint(
                    *identity, code, quote['kind'], observed, symbols)
        result.append(row)
    return result


def noon_rotation_sectors(payload):
    """Full saved noon prices, without copying member arrays or stock samples into the response."""
    from app.services.intraday_brief import _noon_member_metadata

    sectors = payload.get('sectors') or []
    if not isinstance(sectors, list) or len(sectors) > MAX_ROTATION_SECTORS:
        return []
    counts = Counter(row.get('topic_code') or row.get('instrument_id') for row in sectors if isinstance(row, dict))
    quotes = {row['instrument_id']: row for row in payload.get('stocks', [])}
    result = []
    for sector in sectors:
        if (not isinstance(sector, dict)
                or counts[sector.get('topic_code') or sector.get('instrument_id')] != 1):
            continue
        row = _quote_projection(sector)
        if row is None:
            continue
        try:
            proof = _noon_member_metadata(payload, sector, quotes)
            if proof:
                row.update(proof, limit_up_count=sector.get('member_limit_up_count'),
                           limit_up_count_state=sector.get('member_limit_up_state', 'unknown'))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # A missing member proof never clears an independently verified index quote.
            row.update(limit_up_count=None, limit_up_count_state='unknown')
        result.append(row)
    return result


def _close_source(day, generation):
    """Read only the selected-day index summary and the named generation's verified pools."""
    from app.core import published_dir
    from app.services.intraday_members import _bound_market
    from app.services.market_supplement import load_supplement
    from app.services.sector_history import sector_prices_for_date
    from app.services.noon_prior_events import _reference, _prices
    from app.services.noon_context import _event_cohorts

    market = _bound_market(generation)
    status = market.status()
    if status.get('snapshot_id') != generation or status.get('data_end') != day:
        raise ValueError('轮动收盘报价与原正式代际不符')
    document = load_supplement(generation, dates=(day,), fields=('sector_index_history',))
    prices = sector_prices_for_date(document, day).get('items', [])
    try:
        reference = market.reference()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        reference = {}
    sealed, complete = set(), False
    try:
        original, record, summary, _ = _reference(published_dir().resolve(), generation, day)
        symbols = {row.get('thscode') or row.get('instrument_id')
                   for key in ('limitup', 'limitdown', 'breakpool') for row in record.get(key, ())
                   if isinstance(row, dict) and STOCK.fullmatch(str(row.get('thscode') or row.get('instrument_id')))}
        quotes, _ = _prices(published_dir().resolve(), generation, summary, day, symbols)
        pools = _event_cohorts({**original, '_supplement': {}}, day, quotes)
        up = pools['limit_up']
        sealed = {row['instrument_id'] for row in up['items']}
        conflicts = sealed & ({row['instrument_id'] for row in pools['limit_down']['items']}
                              | {row['instrument_id'] for row in pools['limit_break']['items']})
        sealed -= conflicts
        coverage = record.get('pool_coverage') if record is original else record.get('coverage')
        proof = coverage.get('limitup') if isinstance(coverage, dict) else None
        complete = (not conflicts and isinstance(proof, dict) and proof.get('complete') is True
                    and type(proof.get('declared_total')) is int
                    and proof['declared_total'] == up['source_count'] == len(record.get('limitup', ()))
                    and proof.get('state') in (None, 'complete', 'observed', 'ready')
                    and proof.get('quarantined_rows') in (0, None) and up['unverified_count'] == 0)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return prices, reference, sealed, complete


def close_rotation_sectors(day, generation):
    """Reopen the exact closing generation, without seven-day rankings or full stock panels."""
    from app.services.intraday_brief import sector_comparison_metadata, sector_membership_fingerprint
    from app.services.noon_membership_context import _day
    from app.services.noon_analysis import number

    sectors, reference, sealed, pool_complete = _close_source(day, generation)
    if len(sectors) > MAX_ROTATION_SECTORS:
        return []
    topics = [row for row in reference.get('topics', ()) if isinstance(row, dict)]
    topic_counts = Counter(row.get('topic_code') for row in topics)
    topics = {row['topic_code']: row for row in topics
              if isinstance(row.get('topic_code'), str) and topic_counts[row['topic_code']] == 1}
    counts = Counter(row.get('topic_code') or row.get('thscode') for row in sectors)
    result = []
    for sector in sectors:
        code = sector.get('topic_code') or sector.get('thscode')
        price = number(sector.get('close'), positive=True)
        if (not CODE.fullmatch(str(code)) or counts[code] != 1 or sector.get('kind') not in ('industry', 'concept')
                or sector.get('source') != 'fuyao' or sector.get('quote_date', sector.get('date')) != day
                or sector.get('price_basis') != 'none' or price is None):
            continue
        previous = number(sector.get('previous_close'), positive=True)
        change = number(sector.get('change_pct'))
        if previous is not None and change is not None and abs((price / previous - 1) * 100 - change) > .001:
            change = None
        row = {'instrument_id': code, 'name': sector.get('name') or code, 'kind': sector['kind'],
               'change_pct': change, 'latest_price': price, 'amount': sector.get('amount'),
               'quote_source': 'fuyao', 'quote_basis': 'provider_index', 'price_basis': 'none', 'currency': 'CNY',
               'limit_up_count': None, 'limit_up_count_state': 'unknown'}
        if previous is not None:
            row['prev_close'] = previous
        # Price and declared dated change remain available if the independent previous price is absent.
        row.update(sector_comparison_metadata(row, source='fuyao'))
        topic = topics.get(code, {})
        symbols, observed = topic.get('instrument_ids'), topic.get('observed_date')
        if (topic.get('source') == 'fuyao' and topic.get('kind') == row['kind']
                and _day(observed) and observed <= day and isinstance(symbols, list) and 0 < len(symbols) <= 10000
                and len(symbols) == len(set(symbols))
                and all(isinstance(symbol, str) and STOCK.fullmatch(symbol) for symbol in symbols)):
            count = len(set(symbols) & sealed)
            complete = topic.get('members_complete') is True
            exact = complete and pool_complete
            row.update(member_count=len(symbols), member_date=observed, member_list_complete=complete,
                       limit_up_count=count if exact or count else None,
                       limit_up_count_state='observed' if exact else 'partial' if count else 'unknown')
            if complete:
                row['member_reference_sha256'] = sector_membership_fingerprint(
                    generation, day, code, row['kind'], observed, symbols)
        result.append(row)
    return result
