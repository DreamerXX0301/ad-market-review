from app.services.intraday_analysis import build_intraday_rankings, split_recent_listings


def quote(code, price, amount=100, **extra):
    return {'instrument_id': code, 'prev_close': 100, 'open': 101,
            'latest_price': price, 'change_pct': price - 100,
            'amount': amount, 'price_basis': 'none', 'currency': 'CNY', **extra}


def test_compare_only_comparable_same_day_quote_and_keep_unknowns():
    prior = [quote('A', 101, 50), quote('B', 103), quote('C', 104)]
    latest = [quote('A', 105), quote('B', 104, 80), quote('C', 101), quote('D', 102)]
    latest[2]['prev_close'] = 101
    latest[2]['change_pct'] = 0
    result = build_intraday_rankings(latest, [], previous_stocks=prior)
    a, b, d, c = result['stocks']
    assert a['rank'] == 1 and a['previous_rank'] is None and a['rank_delta'] is None
    assert a['amount_delta'] == 50 and a['change_pp'] == 4
    assert b['change_pp'] == 1 and b['amount_delta'] is None
    assert d['previous_rank'] is None and c['previous_rank'] is None
    assert result['coverage']['stocks'] == {'observed': 4, 'rankable': 4, 'compared': 2}

    same_scope = build_intraday_rankings(latest[:-1], [], previous_stocks=prior)
    first = same_scope['stocks'][0]
    assert first['previous_rank'] == 3 and first['rank_delta'] == 2


def test_bounded_ranking_orders_by_pct_then_open_then_amount_and_drops_unverified_pct():
    stocks = [quote(str(i), 102, i) for i in range(40)]
    stocks.append({**quote('bad', 120), 'change_pct': 50})
    stocks.append({**quote('no_open', 102, 200), 'open': None})
    sectors = [quote(str(i), 101, i, kind='industry') for i in range(25)]
    result = build_intraday_rankings(stocks, sectors)
    assert len(result['stocks']) == 30 and len(result['sectors']) == 20
    assert result['stocks'][0]['instrument_id'] == '39'
    assert result['stocks'][0]['open_change_pct'] == .9901
    assert result['coverage']['stocks'] == {'observed': 42, 'rankable': 41, 'compared': 0}
    assert all(row['change_pp'] is None for row in result['stocks'])


def test_confirmed_recent_listings_do_not_distort_regular_stock_ranks():
    calendar = {'2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29',
                '2026-10-30', '2026-11-02', '2026-11-03', '2026-11-04'}
    stocks = [quote('FIRST', 300), quote('FIFTH', 110), quote('SIXTH', 108), quote('UNKNOWN', 109)]
    regular, recent = split_recent_listings(stocks, {
        'FIRST': {'list_date': '2026-11-03'}, 'FIFTH': {'list_date': '2026-10-28'},
        'SIXTH': {'list_date': '2026-10-27'}, 'UNKNOWN': {'list_date': '2026-11-04'},
    }, calendar, '2026-11-03')
    assert [row['instrument_id'] for row in regular] == ['SIXTH', 'UNKNOWN']
    assert [row['instrument_id'] for row in recent] == ['FIRST', 'FIFTH']


def test_board_strength_requires_breadth_and_pool_confirmation_for_candidates():
    from app.services.intraday_analysis import build_intraday_strength

    stocks = [quote(str(i), 108 - i, name=f'stock {i}') for i in range(7)]
    sectors = [quote('verified', 104, name='verified'), quote('unmapped', 109, name='unmapped')]
    membership = {'verified': {'instrument_ids': [str(i) for i in range(7)],
                               'date': '2026-10-29', 'complete': True}}
    result = build_intraday_strength(stocks, sectors, members=membership,
                                     sealed_ids=['0', '1'], pool_complete=True,
                                     board_heights={'0': 1, '1': 2}, excluded_candidate_ids=['2'])
    board = result['strong_sectors'][0]
    assert board['instrument_id'] == 'verified' and board['up_ratio'] == 100
    assert board['limit_up_count'] == 2 and board['limit_up_count_state'] == 'observed'
    assert board['first_board_count'] == board['multi_board_count'] == 1
    assert result['limit_up_sectors'][0]['instrument_id'] == 'verified'
    assert result['index_only_sectors'][0]['instrument_id'] == 'unmapped'
    assert result['index_only_sectors'][0]['limit_up_count'] is None
    assert [r['instrument_id'] for r in result['candidates']] == ['3', '4', '5', '6']
    assert all(r['limit_up_state'] == 'not_in_verified_pool' for r in result['candidates'])
    assert [r['instrument_id'] for r in result['sealed_stocks']] == ['0', '1']

    partial = build_intraday_strength(stocks, sectors, members=membership,
                                      sealed_ids=['0'], pool_complete=False)
    assert partial['limit_up_sectors'][0]['limit_up_count_state'] == 'partial'
    assert partial['candidates'] == [] and partial['strong_sectors'][0]['candidates'] == []
    assert partial['sealed_stocks'][0]['limit_up_state'] == 'sealed'


def test_wide_concepts_do_not_displace_narrow_limit_up_clusters():
    from app.services.intraday_analysis import build_intraday_strength

    pool = ['1', '2']
    stocks = [quote(code, 110) for code in pool]
    sectors = [quote('wide', 105, kind='concept'), quote('industry', 103, kind='industry')]
    members = {
        'wide': {'instrument_ids': [str(i) for i in range(201)], 'complete': True},
        'industry': {'instrument_ids': ['1', 'other'], 'complete': True},
    }
    result = build_intraday_strength(stocks, sectors, members=members, sealed_ids=pool, pool_complete=True)
    assert [r['instrument_id'] for r in result['limit_up_sectors']] == ['industry']
    wide = result['wide_limit_up_sectors'][0]
    assert wide['limit_up_count'] == 2 and wide['limit_up_ratio'] < 1
    assert result['limit_up_sectors'][0]['limit_up_ratio'] == 50


def test_strength_shows_verified_breaks_but_never_mixes_them_with_candidates_or_seals():
    from app.services.intraday_analysis import build_intraday_strength

    stocks = [quote(f'00000{i}.SZ', 110 - i) for i in range(1, 7)]
    ids = [row['instrument_id'] for row in stocks]
    sectors = [quote('industry', 106, kind='industry', name='行业'),
               {'instrument_id': 'concept', 'name': '题材', 'kind': 'concept'},
               {'instrument_id': 'wide', 'name': '过宽题材', 'kind': 'concept'}]
    members = {code: {'instrument_ids': group, 'date': '2026-11-02', 'complete': True}
               for code, group in {'industry': ids, 'concept': ids[:4],
                                   'wide': ids + [str(i) for i in range(200)]}.items()}
    breaks = {'state': 'observed', 'items': [{'instrument_id': ids[1]},
                                             {'instrument_id': ids[2], 'open_times': 1}]}
    result = build_intraday_strength(stocks, sectors, members=members, sealed_ids=ids[:2],
                                     pool_complete=True, break_pool=breaks,
                                     sealed_rows=[{'instrument_id': ids[0], 'limit_up_reason': '来源理由'}])
    board = result['strong_sectors'][0]
    assert board['broken_count'] == 1 and board['broken_count_state'] == 'partial'
    assert board['broken'] == result['broken_stocks'] and board['broken'][0]['instrument_id'] == ids[2]
    assert [row['instrument_id'] for row in result['candidates']] == ids[3:]
    assert [row['instrument_id'] for row in result['sealed_stocks']] == [ids[0]]
    assert result['sealed_stocks'][0]['source_reason'] == '来源理由'
    assert [tag['name'] for tag in board['broken'][0]['themes']] == ['行业', '题材']
    assert all(tag['member_date'] == '2026-11-02' for tag in result['candidates'][0]['themes'])
