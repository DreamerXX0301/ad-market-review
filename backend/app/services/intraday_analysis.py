"""盘中快照强弱排序。价格表现和累计成交额变化分别保留。"""
from statistics import median

from app.services.intraday_feedback import source_board_reason, stock_themes, verified_board_pools
from app.services.noon_analysis import number


def split_recent_listings(stocks, master, calendar, day):
    """Keep verified recent listings separate from the regular ranking."""
    recent_days = set(sorted({date for date in calendar if date <= day})[-5:])
    regular, recent = [], []
    for row in stocks:
        listed = master.get(row['instrument_id'], {}).get('list_date')
        if isinstance(listed, str) and listed in recent_days:
            recent.append(row)
        else:
            regular.append(row)
    return regular, _rank(recent)


def _rank(rows):
    rankable = []
    for row in rows:
        price = number(row.get('latest_price'), positive=True)
        previous = number(row.get('prev_close'), positive=True)
        change = number(row.get('change_pct'))
        if price is None or previous is None or change is None:
            continue
        if abs((price / previous - 1) * 100 - change) > .001:
            continue
        opening = number(row.get('open'), positive=True)
        amount = number(row.get('amount'), nonnegative=True)
        rankable.append({**row, 'amount': amount,
                         'open_change_pct': round((price / opening - 1) * 100, 4) if opening else None})

    def rank_key(row):
        opening = row['open_change_pct']
        amount = row['amount']
        return (-row['change_pct'], opening is None, -(opening or 0),
                amount is None, -(amount or 0), row['instrument_id'])

    rankable.sort(key=rank_key)
    return [{**row, 'rank': index} for index, row in enumerate(rankable, 1)]


def _comparison(current, previous, same_universe):
    unavailable = {'previous_rank': None, 'rank_delta': None, 'change_pp': None, 'amount_delta': None}
    if not previous or any(current.get(key) != previous.get(key)
                           for key in ('prev_close', 'price_basis', 'currency', 'kind')):
        return unavailable
    amount, prior_amount = current['amount'], previous['amount']
    return {
        'previous_rank': previous['rank'] if same_universe else None,
        'rank_delta': previous['rank'] - current['rank'] if same_universe else None,
        'change_pp': round(current['change_pct'] - previous['change_pct'], 4),
        'amount_delta': amount - prior_amount
        if (current.get('currency') == 'CNY' and amount is not None
            and prior_amount is not None and amount >= prior_amount) else None,
    }


def _rank_with_comparison(rows, earlier, limit):
    ranked = _rank(rows)
    baseline = {row['instrument_id']: row for row in _rank(earlier)}
    same_universe = baseline.keys() == {row['instrument_id'] for row in ranked}
    compared = [
        {**row, **_comparison(row, baseline.get(row['instrument_id']), same_universe)}
        for row in ranked
    ]
    return compared[:limit], {
        'observed': len(rows),
        'rankable': len(ranked),
        'compared': sum(row['change_pp'] is not None for row in compared),
    }


def build_intraday_rankings(stocks, sectors, *, previous_stocks=(), previous_sectors=()):
    """比较同交易日较早快照；调用者先核验交易日、顺序和来源。"""
    stock_rows, stock_coverage = _rank_with_comparison(stocks, previous_stocks, 30)
    sector_rows, sector_coverage = _rank_with_comparison(sectors, previous_sectors, 20)
    return {'stocks': stock_rows, 'sectors': sector_rows,
            'coverage': {'stocks': stock_coverage, 'sectors': sector_coverage}}


def _member_metrics(proof, quotes, sealed, pool_complete, heights):
    ids = set(proof.get('instrument_ids') or ())
    observed = [quotes[code]['change_pct'] for code in ids if code in quotes]
    rising = sum(value > 0 for value in observed)
    falling = sum(value < 0 for value in observed)
    hits = ids & sealed
    complete_count = bool(ids and proof.get('complete') is True and pool_complete)
    known_heights = [heights[code] for code in hits if type(heights.get(code)) is int and heights[code] > 0]
    first = sum(height == 1 for height in known_heights)
    multiple = len(known_heights) - first
    complete_heights = complete_count and len(known_heights) == len(hits)
    coverage = 100 * len(observed) / len(ids) if ids else None
    up_ratio = 100 * rising / len(observed) if observed else None
    return ids, dict(
        member_count=len(ids) or None, member_date=proof.get('date'),
        member_list_complete=proof.get('complete') is True if ids else None,
        rankable_members=len(observed), advancers=rising if observed else None,
        decliners=falling if observed else None, up_ratio=up_ratio, member_coverage_pct=coverage,
        breadth_qualified=bool(len(observed) >= 5 and coverage >= 60 and proof.get('complete') is True),
        median_change_pct=median(observed) if observed else None,
        limit_up_count=len(hits) if complete_count or hits else None,
        limit_up_ratio=100 * len(hits) / len(ids) if complete_count else None,
        limit_up_count_state='observed' if complete_count else 'partial' if hits else 'unknown',
        first_board_count=first if complete_heights or first else None,
        multi_board_count=multiple if complete_heights or multiple else None,
        board_height_count_state='observed' if complete_heights else 'partial' if known_heights else 'unknown',
    )


def _strength_stock(row, state, heights, board_names=(), proof=None):
    fields = ('instrument_id', 'name', 'latest_price', 'change_pct', 'open_change_pct', 'amount',
              'change_pp', 'amount_delta')
    result = {key: row.get(key) for key in fields}
    result.update(limit_up_state=state, board_names=list(board_names),
                  continue_day_cnt=heights.get(row['instrument_id']) if state == 'sealed' else None)
    signals = ['高于开盘'] if (row.get('open_change_pct') or 0) > 0 else []
    high = number(row.get('high'), positive=True)
    if high and 0 <= 1 - row['latest_price'] / high <= .02:
        signals.append('距盘中高点不超过2%')
    result['signals'] = signals
    if proof and (reason := source_board_reason(proof)):
        result['source_reason'] = reason
    return result


def build_intraday_strength(stocks, sectors, *, members, sealed_ids, pool_complete,
                            board_heights=None, excluded_candidate_ids=(),
                            previous_stocks=(), previous_sectors=(), break_pool=None, sealed_rows=(),
                            observed_sectors=()):
    """Rank board breadth and event concentration; pool absence is only an observation."""
    heights = board_heights or {}
    # Candidates require a complete seal enumeration. Ambiguous events cannot be candidates.
    candidates_allowed = pool_complete
    sealed, pool_complete, broken, break_complete, conflicts = verified_board_pools(sealed_ids, pool_complete, break_pool)
    excluded = sealed | broken.keys() | conflicts | set(excluded_candidate_ids)
    sealed_proofs = {row['instrument_id']: row for row in sealed_rows if row['instrument_id'] in sealed}
    earlier_stocks = {row['instrument_id']: row for row in _rank(previous_stocks)}
    earlier_sectors = {row['instrument_id']: row for row in _rank(previous_sectors)}
    ranked_stocks = [{**row, **_comparison(row, earlier_stocks.get(row['instrument_id']), False)}
                     for row in _rank(stocks)]
    quotes = {row['instrument_id']: row for row in ranked_stocks}
    boards, identities = [], {}
    for row in _rank(sectors):
        code = row['instrument_id']
        ids, metrics = _member_metrics(members.get(code) or {}, quotes, sealed, pool_complete, heights)
        if break_pool is not None:
            count = len(ids & broken.keys())
            exact = bool(ids and metrics['member_list_complete'] and break_complete)
            metrics.update(broken_count=count if exact or count else None,
                           broken_count_state='observed' if exact else 'partial' if count else 'unknown')
        identities[code] = ids
        boards.append({**row, **metrics, **_comparison(row, earlier_sectors.get(code), False)})

    strong = sorted((row for row in boards if row['breadth_qualified'] and row['change_pct'] > 0
                     and row['up_ratio'] > 50 and row['median_change_pct'] > 0),
                    key=lambda row: (-row['median_change_pct'], -row['change_pct'],
                                     -row['up_ratio'], row['instrument_id']))[:20]
    by_limit_ups = sorted((row for row in boards if (row['limit_up_count'] or 0) > 0),
                          key=lambda row: (-row['limit_up_count'], row['limit_up_count_state'] != 'observed',
                                           -row['change_pct'], row['instrument_id']))
    wide = [row for row in by_limit_ups if row.get('kind') == 'concept' and (row['member_count'] or 0) > 200]
    wide_ids = {row['instrument_id'] for row in wide}
    clustered = [row for row in by_limit_ups if row['instrument_id'] not in wide_ids][:20]
    index_only = [row for row in boards if not row['breadth_qualified']][:20]
    eligible = [row for row in ranked_stocks if row['instrument_id'] not in excluded
                and row['change_pct'] > 0 and (row['open_change_pct'] or 0) > 0] if candidates_allowed else []
    ranked_sealed = [row for row in ranked_stocks if row['instrument_id'] in sealed]
    ranked_broken = [row for row in ranked_stocks if row['instrument_id'] in broken]
    eligible_by_code, sealed_by_code, broken_by_code = (
        {row['instrument_id']: row for row in rows} for rows in (eligible, ranked_sealed, ranked_broken)
    )

    def best_in_board(codes, rows):
        return sorted((rows[code] for code in codes & rows.keys()), key=lambda row: row['rank'])[:6]

    def sealed_stock(row, labels):
        return _strength_stock(row, 'sealed', heights, labels, sealed_proofs.get(row['instrument_id']))

    def broken_stock(row, labels):
        proof = broken[row['instrument_id']]
        item = _strength_stock(row, 'broken', heights, labels, proof)
        opened = proof.get('open_times')
        item['open_times'] = opened if type(opened) is int and opened >= 0 else None
        return item

    def related(row, board_rows):
        return [board.get('name') or board['instrument_id'] for board in board_rows
                if row['instrument_id'] in identities[board['instrument_id']]][:4]

    def view(board, rank):
        codes = identities[board['instrument_id']]
        name = board.get('name') or board['instrument_id']
        result = {**board, 'rank': rank,
                'candidates': [_strength_stock(row, 'not_in_verified_pool', heights, [name])
                               for row in best_in_board(codes, eligible_by_code)]
                              if board['breadth_qualified'] and board['change_pct'] > 0 else [],
                'sealed_stocks': [sealed_stock(row, [name]) for row in best_in_board(codes, sealed_by_code)]}
        if break_pool is not None:
            result['broken'] = [broken_stock(row, [name]) for row in best_in_board(codes, broken_by_code)]
        return result

    names = [(row, labels) for row in eligible if (labels := related(row, strong))]
    result = {
        'strong_sectors': [view(row, index) for index, row in enumerate(strong, 1)],
        'limit_up_sectors': [view(row, index) for index, row in enumerate(clustered, 1)],
        'wide_limit_up_sectors': [view(row, index) for index, row in enumerate(wide[:10], 1)],
        'index_only_sectors': [view(row, index) for index, row in enumerate(index_only, 1)],
        'candidates': [_strength_stock(row, 'not_in_verified_pool', heights, labels)
                       for row, labels in names[:30]],
        'sealed_stocks': [sealed_stock(row, related(row, clustered)) for row in ranked_sealed[:20]],
        'strength_coverage': {'pool_complete': pool_complete, 'sealed_count': len(sealed),
                              'board_count': len(boards),
                              'breadth_board_count': sum(row['breadth_qualified'] for row in boards)},
    }
    if break_pool is not None:
        result['broken_stocks'] = [broken_stock(row, related(row, strong)) for row in ranked_broken[:20]]
        display_rows = [row for key in ('candidates', 'sealed_stocks', 'broken_stocks') for row in result[key]]
        for key in ('strong_sectors', 'limit_up_sectors', 'wide_limit_up_sectors', 'index_only_sectors'):
            display_rows.extend(row for board in result[key] for group in ('candidates', 'sealed_stocks', 'broken')
                                for row in board[group])
        themes = stock_themes(sectors, members, [row['instrument_id'] for row in display_rows])
        for row in display_rows:
            row['themes'] = themes.get(row['instrument_id'], [])
    if observed_sectors:
        shown = {row['instrument_id'] for row in observed_sectors}
        for key in ('strong_sectors', 'limit_up_sectors', 'wide_limit_up_sectors', 'index_only_sectors'):
            shown.update(row['instrument_id'] for row in result[key])
        board_stocks = {}
        for code in sorted(shown):
            proof = members.get(code)
            if not proof:
                continue
            ids = set(proof['instrument_ids'])
            samples = best_in_board(ids, quotes)
            items = []
            for row in samples:
                symbol = row['instrument_id']
                item = {key: row[key] for key in ('instrument_id', 'name', 'change_pct', 'amount') if key in row}
                if symbol in sealed:
                    item.update(limit_up_state='sealed', continue_day_cnt=heights.get(symbol))
                elif symbol in broken:
                    item.update(limit_up_state='broken', open_times=broken[symbol].get('open_times'))
                items.append(item)
            board_stocks[code] = dict(items=items, sample_count=len(items), matched_count=len(ids & quotes.keys()),
                                      member_date=proof['date'])
        result['board_stocks'] = board_stocks
    return result
