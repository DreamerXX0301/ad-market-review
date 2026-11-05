"""Read-only, bounded rotation quotes, separate from the displayed strength ranks."""
from collections import Counter
import re
import sqlite3

MAX_ROTATION_SECTORS = 1500
CODE = re.compile(r'\d{6}\.TI')
STOCK = re.compile(r'\d{6}\.(?:SH|SZ|BJ)')
METRIC_FIELDS = ('member_count', 'member_date', 'member_list_complete', 'rankable_members',
                 'advancers', 'decliners', 'up_ratio', 'member_coverage_pct', 'breadth_qualified',
                 'median_change_pct', 'limit_up_count', 'limit_up_ratio', 'limit_up_count_state',
                 'first_board_count', 'multi_board_count', 'board_height_count_state',
                 'broken_count', 'broken_count_state', 'breadth_state', 'breadth_reason',
                 'broken_count_reason', 'member_metrics_scope')


def _metric_inputs(snapshot, *, quote_index=None):
    """Index the original same-window prices and event receipts once per frame."""
    from app.services.intraday_board_changes import _quotes, _pools

    quotes = _quotes(snapshot) if quote_index is None else quote_index
    pools = _pools(snapshot)
    heights, claims = {}, {}
    def claim(row, height=None):
        symbol = row.get('instrument_id')
        height = row.get('continue_day_cnt', row.get('board_count')) if height is None else height
        if symbol in pools['limit_up'][0] and type(height) is int and height > 0:
            claims.setdefault(symbol, set()).add(height)
    for group in snapshot.get('ladder', {}).get('groups', ()):
        height = group.get('board_count')
        if type(height) is int and height > 0:
            for row in group.get('items', ()):
                if isinstance(row, dict):
                    claim(row, height)
    for row in pools['limit_up'][0].values():
        claim(row)
    for row in snapshot.get('sealed_stocks', ()):
        claim(row)
    for board in snapshot.get('board_stocks', {}).values():
        for row in board.get('items', ()):
            if row.get('limit_up_state') == 'sealed':
                claim(row)
    heights.update({symbol: next(iter(values)) for symbol, values in claims.items() if len(values) == 1})
    return quotes, pools, heights


def _saved_height_metrics(row, original):
    """An exact same-body height aggregate is stronger than its truncated sample rows."""
    count = row.get('limit_up_count')
    first, multiple = original.get('first_board_count'), original.get('multi_board_count')
    if (original.get('board_height_count_state') == 'observed'
            and row.get('limit_up_count_state') == original.get('limit_up_count_state') == 'observed'
            and type(count) is int and original.get('limit_up_count') == count
            and type(first) is int and first >= 0 and type(multiple) is int and multiple >= 0
            and first + multiple == count
            and all(original.get(key) == row.get(key) for key in ('instrument_id', 'kind', 'change_pct',
                        'member_count', 'member_date', 'member_list_complete'))):
        row.update(first_board_count=first, multi_board_count=multiple, board_height_count_state='observed')


def _member_metric_projection(symbols, observed, complete, context):
    """Breadth counts only price-checked quoted members; unknown pools never become zero."""
    from app.services.intraday_analysis import _member_metrics

    quotes, pools, heights = context
    sealed, sealed_complete = pools['limit_up']
    broken, broken_complete = pools['limit_break']
    ids, metrics = _member_metrics({'instrument_ids': symbols, 'date': observed, 'complete': complete},
                                  quotes, set(sealed), sealed_complete, heights)
    hits = ids & broken.keys()
    exact = bool(ids and complete and broken_complete)
    metrics.update(broken_count=len(hits) if exact or hits else None,
                   broken_count_state='observed' if exact else 'partial' if hits else 'unknown',
                   breadth_state='observed' if complete and metrics['rankable_members'] == len(ids)
                   else 'partial' if metrics['rankable_members'] else 'unknown',
                   breadth_reason=None if complete and metrics['rankable_members'] == len(ids) else
                   '按该时点可核验报价成员计算，缺报价或名单不完整；不以缺档成员为下跌或平盘',
                   broken_count_reason=None if exact else
                   '成员名单或同档炸板池不完整，仅能列已确认下界' if hits else
                   '成员名单或同档炸板池缺少完整证据，未匹配不代表零炸板',
                   member_metrics_scope='同档原成员与逐股报价交集；上涨占比分母为可比成员，概念可重叠')
    return metrics


def attach_sector_metrics(snapshot):
    """Enrich bounded UI lists in memory, retaining their rankings and stock arrays."""
    from app.services.intraday_discovery import SECTOR_LISTS

    indexed = {row['instrument_id']: row for row in snapshot.get('rotation_sectors', ())}
    for key in SECTOR_LISTS:
        for row in snapshot.get(key, ()):
            source = indexed.get(row.get('instrument_id'))
            if source and all(row.get(field) == source.get(field) for field in ('kind', 'change_pct')):
                row.update({field: source[field] for field in METRIC_FIELDS if field in source})


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
           'limit_up_count': None, 'limit_up_count_state': 'unknown',
           'breadth_state': 'unknown', 'breadth_reason': '原时点成员名单未通过核验，仅展示独立指数报价',
           'broken_count_state': 'unknown', 'broken_count_reason': '缺少原时点成员与炸板池交集证据'}
    if previous is not None:
        row['prev_close'] = previous
    row.update(sector_comparison_metadata(row, source='fuyao'))
    return row


def live_rotation_sectors(snapshot, *, reference=None, identity=None, member_cache=None, input_cache=None,
                          quote_index=None):
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
    cache_key = (id(reference), identity, snapshot['date'])
    prepared = member_cache.get(cache_key) if member_cache is not None else None
    if prepared is None:
        topics = [row for row in (reference or {}).get('topics', ()) if isinstance(row, dict)]
        identities = Counter(row.get('topic_code') for row in topics)
        original = {row['topic_code']: row for row in topics
                    if isinstance(row.get('topic_code'), str) and identities[row['topic_code']] == 1}
        proofs = {}
        for code, topic in original.items():
            symbols, observed = topic.get('instrument_ids'), topic.get('observed_date')
            if (identity and topic.get('source') == 'fuyao' and topic.get('kind') in ('industry', 'concept')
                    and _day(observed) and observed <= identity[1] < snapshot['date']
                    and isinstance(symbols, list) and 0 < len(symbols) <= 10000
                    and all(isinstance(symbol, str) and STOCK.fullmatch(symbol) for symbol in symbols)
                    and len(symbols) == len(set(symbols))):
                complete = topic.get('members_complete') is True
                fingerprint = (sector_membership_fingerprint(*identity, code, topic['kind'], observed, symbols)
                               if complete else None)
                proofs[code] = symbols, observed, complete, fingerprint
        prepared = original, proofs
        if member_cache is not None:
            member_cache[cache_key] = prepared
    original, proofs = prepared
    quote_ids = Counter(row.get('instrument_id') for row in quotes if isinstance(row, dict))
    context = _metric_inputs(snapshot) if quote_index is None else _metric_inputs(snapshot, quote_index=quote_index)
    if input_cache is not None and snapshot.get('id'):
        input_cache[snapshot['id']] = (snapshot['date'], snapshot.get('slot'), snapshot.get('collected_end_at'),
                                       snapshot.get('comparison_input'), context)
    from app.services.intraday_discovery import SECTOR_LISTS
    saved = {}
    for key in SECTOR_LISTS:
        for board in snapshot.get(key, ()):
            if board.get('board_height_count_state') == 'observed':
                saved.setdefault(board.get('instrument_id'), board)
    result = []
    for quote in quotes:
        if not isinstance(quote, dict):
            continue
        code = quote.get('instrument_id')
        topic = original.get(code, {})
        row = _quote_projection(quote, topic.get('name'))
        if row is None or quote_ids[code] != 1:
            continue
        proof = proofs.get(code)
        if proof and topic.get('kind') == quote['kind']:
            symbols, observed, complete, fingerprint = proof
            row.update(_member_metric_projection(symbols, observed, complete, context))
            _saved_height_metrics(row, saved.get(code, {}))
            if fingerprint:
                row['member_reference_sha256'] = fingerprint
        result.append(row)
    return result


def noon_rotation_sectors(payload):
    """Full saved noon prices, without copying member arrays or stock samples into the response."""
    from app.services.intraday_brief import sector_membership_fingerprint
    from app.services import noon_membership_context as membership
    from app.services.noon_sector_views import _members

    sectors = payload.get('sectors') or []
    if not isinstance(sectors, list) or len(sectors) > MAX_ROTATION_SECTORS:
        return []
    counts = Counter(row.get('topic_code') or row.get('instrument_id') for row in sectors if isinstance(row, dict))
    quotes = membership._quotes(payload)
    context = _metric_inputs({**payload, 'slot': 'noon',
                              'comparison_input': {'stocks': payload.get('stocks', [])}})
    generation = membership._source_id(payload)
    legacy_candidates = None
    if 'sector_memberships' not in payload and generation:
        try:
            topics, _ = membership._load_topics(generation, payload['previous_review']['date'])
            legacy_candidates = {}
            for topic in topics:
                legacy_candidates.setdefault(topic.get('topic_code'), []).append(topic)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            legacy_candidates = {}
    result = []
    for sector in sectors:
        if (not isinstance(sector, dict)
                or counts[sector.get('topic_code') or sector.get('instrument_id')] != 1):
            continue
        row = _quote_projection(sector)
        if row is None:
            continue
        try:
            if legacy_candidates is not None:
                proof, error = membership._verified_members(sector, legacy_candidates, quotes, payload['date'])
            else:
                proof, _, error = _members(payload, sector, quotes)
            if proof and not error and generation:
                observed = proof['topic']['observed_date']
                complete = proof['topic'].get('members_complete') is True
                row.update(_member_metric_projection(proof['members'], observed, complete, context))
                if complete:
                    row['member_reference_sha256'] = sector_membership_fingerprint(
                        generation, payload['previous_review']['date'], row['instrument_id'],
                        row['kind'], observed, proof['members'])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # A missing member proof never clears an independently verified index quote.
            row.update(limit_up_count=None, limit_up_count_state='unknown')
        result.append(row)
    return result


def _close_source(day, generation, *, closing_context=None):
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
    if closing_context is None:
        try:
            from app.services.intraday_close import closing_data
            closing_context = closing_data(day, generation)
        except (ImportError, OSError, ValueError, KeyError, TypeError, AttributeError):
            closing_context = None
    if (isinstance(closing_context, dict) and closing_context.get('date') == day
            and closing_context.get('generation') == generation):
        context = _closing_metric_inputs(closing_context)
        return prices, closing_context.get('reference', {}), set(context[1]['limit_up'][0]), context[1]['limit_up'][1], context
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


def _closing_metric_inputs(context):
    from app.services.intraday_board_changes import _quotes

    values = context.get('quotes')
    values = values if isinstance(values, dict) and len(values) <= 10000 else {}
    quotes = _quotes({'comparison_input': {'stocks': list(values.values())}})
    sources = context.get('pools')
    sources = sources if isinstance(sources, dict) else {}
    pools, heights = {}, {}
    for key in ('limit_up', 'limit_break', 'limit_down'):
        pool = sources.get(key, {})
        pool = pool if isinstance(pool, dict) else {}
        items = pool.get('items', ()) if pool.get('state') in ('observed', 'partial') else ()
        invalid = not isinstance(items, (list, tuple)) or len(items) > 2000
        if invalid:
            items = ()
        counts = Counter(row.get('instrument_id') for row in items if isinstance(row, dict))
        rows = {row['instrument_id']: row for row in items if isinstance(row, dict)
                and STOCK.fullmatch(str(row.get('instrument_id'))) and counts[row['instrument_id']] == 1}
        exact = (not invalid and pool.get('state') == 'observed' and len(rows) == len(items)
                 and type(pool.get('count')) is int and pool['count'] == len(rows))
        pools[key] = rows, exact
    conflicts = set()
    for left, right in (('limit_up', 'limit_break'), ('limit_up', 'limit_down'), ('limit_break', 'limit_down')):
        conflicts |= pools[left][0].keys() & pools[right][0].keys()
    for key, (rows, exact) in pools.items():
        pools[key] = {symbol: row for symbol, row in rows.items() if symbol not in conflicts}, exact and not conflicts
    for symbol, row in pools['limit_up'][0].items():
        height = row.get('continue_day_cnt', row.get('board_count', row.get('previous_board_count')))
        if type(height) is int and height > 0:
            heights[symbol] = height
    return quotes, pools, heights


def close_rotation_sectors(day, generation, *, closing_context=None):
    """Reopen the exact closing generation, without seven-day rankings or full stock panels."""
    from app.services.intraday_brief import sector_comparison_metadata, sector_membership_fingerprint
    from app.services.noon_membership_context import _day
    from app.services.noon_analysis import number

    source = (_close_source(day, generation, closing_context=closing_context)
              if closing_context is not None else _close_source(day, generation))
    sectors, reference, sealed, pool_complete, *contexts = source
    context = contexts[0] if contexts else None
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
               'limit_up_count': None, 'limit_up_count_state': 'unknown',
               'breadth_state': 'unknown', 'breadth_reason': '原收盘成员或同日行情未通过核验，仅展示独立指数报价',
               'broken_count_state': 'unknown', 'broken_count_reason': '缺少原收盘成员与炸板池交集证据'}
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
            if context is not None:
                row.update(_member_metric_projection(symbols, observed, complete, context))
            if complete:
                row['member_reference_sha256'] = sector_membership_fingerprint(
                    generation, day, code, row['kind'], observed, symbols)
        result.append(row)
    return result
