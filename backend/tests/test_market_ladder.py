"""Closing-pool fixtures never access live providers."""
from copy import deepcopy
import pytest
from app.services import market_ladder as ladder, market_service, strategy_limits

A, B, C, X, OTHER = '600001.SH', '600002.SH', '300001.SZ', '688001.SH', '000001.SZ'
DAYS = ['2026-01-05', '2026-01-06', '2026-01-07', '2026-01-08', '2026-01-09', '2026-01-12', '2026-01-13']
TODAY = DAYS[-1]


def event(symbol, day, **extra):
    return {'thscode': symbol, 'ticker': symbol[:6], 'name': symbol, 'last_price': 10,
            'date': day, 'continue_day_cnt': 1, 'limit_up_time': '14:30:00', **extra}


def pool(day, symbols, **extra):
    return {'date': day, 'items': [event(symbol, day) for symbol in symbols],
            'complete': True, 'pagination_complete': True, 'ambiguous_code_rows': 0,
            'raw_codes': [symbol[:6] for symbol in symbols], 'rejected_codes': [],
            'source': 'fixture-source', 'pool_kind': 'limitup', **extra}


class Market:
    def __init__(self):
        self.ref = {'_supplement': {'annual_pools': {
            DAYS[-3]: pool(DAYS[-3], [A, OTHER]),
            DAYS[-2]: pool(DAYS[-2], [B, OTHER]),
            TODAY: pool(TODAY, [A, C, OTHER]),
        }}}
        self.master = [{'instrument_id': symbol, 'kind': 'stock', 'name': symbol, 'list_date': '2020-01-01'} for symbol in (A, B, C, X, OTHER)]
        self.history = {symbol: [{'instrument_id': symbol, 'date': day, 'close': 10, 'volume': 100, 'amount': 1000, 'change_pct': 1} for day in DAYS] for symbol in (A, B, C, X, OTHER)}
        self.history_calls = []
        self.local = {'topics': [{'topic_id': 'concept-one', 'name': '存档题材', 'kind': 'concept', 'observed_date': TODAY,
                                 'members_complete': True, 'instrument_ids': [A, B, C]}]}
        self.membership = {'selected_date_available': True, 'source_snapshot_day': TODAY, 'notice': 'fixture dated member archive'}

    def status(self): return {'snapshot_id': 'fixture', 'data_end': TODAY}
    def reference(self): return self.ref
    def calendar(self): return DAYS
    def instruments(self, limit=10000): return self.master
    def history_for_instruments(self, symbols, start=None, end=None):
        self.history_calls.append((symbols, start, end))
        return {symbol: [row for row in self.history.get(symbol, []) if start <= row['date'] <= end] for symbol in symbols}
    def rows(self): return [dict(rows[-1]) for rows in self.history.values() if rows and rows[-1]['date'] == TODAY]


@pytest.fixture
def market(monkeypatch):
    market = Market()
    monkeypatch.setattr(strategy_limits, 'load_supplement', lambda snapshot: deepcopy(market.ref['_supplement']))
    monkeypatch.setattr(ladder, 'resolve_reference', lambda *args: (deepcopy(market.local), deepcopy(market.membership)))
    return market


def build(market):
    return ladder.build_ladder(market, market.status(), TODAY, market.ref, market.rows(), {})


def test_confirmed_rebound_failed_and_twenty_cm_have_exact_session_proof(market):
    result = build(market)
    assert {row['instrument_id'] for row in result['items']} == {A, C, OTHER}
    assert [row['instrument_id'] for row in result['rebound_items']] == [A]
    assert [row['instrument_id'] for row in result['failed_items']] == [B]
    assert [row['instrument_id'] for row in result['twenty_cm_items']] == [C]
    failed = result['failed_items'][0]
    assert failed['previous_board_count'] == 1 and failed['limit_up_state'] is False
    assert failed['turnover_rate'] is None
    assert '候选' in result['rebound_items'][0]['rebound_reason']
    assert len(market.history_calls) == 1
    assert market.history_calls[0][1:] == (DAYS[-3], TODAY)


@pytest.mark.parametrize('mode', ['partial', 'empty', 'quarantined', 'missing_quote', 'zero_volume', 'duplicate_quote', 'price_conflict', 'future_event'])
def test_failed_promotion_never_treats_absence_or_bad_quote_as_no(market, mode):
    current = market.ref['_supplement']['annual_pools'][TODAY]
    if mode == 'partial': current.update(complete=False, pagination_complete=False)
    elif mode == 'empty': current['items'] = []
    elif mode == 'quarantined': current['rejected_codes'] = [B[:6]]
    elif mode == 'missing_quote': market.history[B] = market.history[B][:-1]
    elif mode == 'zero_volume': market.history[B][-1]['volume'] = 0
    elif mode == 'duplicate_quote': market.history[B].append(dict(market.history[B][-1]))
    elif mode == 'price_conflict': current['items'].append(event(B, TODAY, last_price=11))
    elif mode == 'future_event': current['items'].append(event(B, '2026-01-14'))
    result = build(market)
    assert not result['failed_items']
    assert result['classification_coverage']['failed']['unknown_count'] >= 1


def test_partial_public_pool_can_confirm_positive_but_never_negative(market):
    document = market.ref['_supplement']
    document['public_pools'] = {TODAY: pool(TODAY, [A, C, OTHER])}
    del document['annual_pools'][TODAY]
    result = build(market)
    assert {row['instrument_id'] for row in result['items']} == {A, C, OTHER}
    assert not result['failed_items']
    assert result['main_coverage']['source_pool_complete'] is False


def test_raw_price_conflict_is_retained_only_in_unverified_candidates(market):
    market.ref['_supplement']['annual_pools'][TODAY]['items'][0]['last_price'] = 99
    result = build(market)
    assert A not in {row['instrument_id'] for row in result['items']}
    assert [row['instrument_id'] for row in result['unverified_items']] == [A]
    assert result['unverified_items'][0]['limit_up_evidence']['status'] == 'conflict'
    assert result['main_coverage']['complete'] is False


@pytest.mark.parametrize('missing_day', [DAYS[-2], DAYS[-3]])
def test_rebound_missing_exact_prior_day_stays_unknown(market, missing_day):
    market.history[A] = [row for row in market.history[A] if row['date'] != missing_day]
    result = build(market)
    row = next(row for row in result['items'] if row['instrument_id'] == A)
    assert row['rebound_state'] is None
    assert not result['rebound_items']
    assert result['classification_coverage']['rebound']['unknown_count'] >= 1


@pytest.mark.parametrize('symbol,day,listing,calendar,expected', [
    (C, '2020-08-21', '2010-01-01', DAYS, False),
    (C, TODAY, None, DAYS, None),
    (C, TODAY, DAYS[-5], DAYS, None),
    (C, TODAY, DAYS[-6], DAYS, True),
    (X, TODAY, '2019-07-22', DAYS, True),
    (A, TODAY, '2000-01-01', DAYS, False),
])
def test_twenty_cm_uses_dated_regime_and_ipo_exception_not_return(symbol, day, listing, calendar, expected):
    value, reason = ladder.twenty_cm_state(symbol, day, {'kind': 'stock', 'list_date': listing, 'change_pct': 30}, calendar, True)
    assert value is expected and reason


def test_theme_counts_times_use_selected_archive_members_not_panel_topics(market):
    current = market.ref['_supplement']['annual_pools'][TODAY]
    current['items'][0].update(first_limit_time='09:31:00', last_limit_time='14:00:00')
    current['items'][1].update(first_limit_time='09:45:00', last_limit_time='14:50:00')
    market.history[A][-1]['topics'] = [{'topic_id': 'wrong-today', 'name': '不可回填'}]
    result = build(market)
    theme = result['theme_counts'][0]
    assert theme['limit_up_count'] == 2 and theme['limit_up_observed_count'] == 2
    assert theme['first_limit_time'] == '09:31:00' and theme['last_limit_time'] == '14:50:00'
    assert theme['member_lookup'] == {'topic_id': 'concept-one', 'kind': 'concept'}
    assert all(row['topics'][0]['topic_id'] == 'concept-one' for row in result['items'] if row['instrument_id'] in (A, C))


def test_partial_member_or_quote_coverage_reports_count_lower_bound(market):
    market.local['topics'][0]['members_complete'] = False
    theme = build(market)['theme_counts'][0]
    assert theme['limit_up_count'] is None and theme['limit_up_observed_count'] == 2
    market.local['topics'][0]['members_complete'] = True
    market.history[B].pop()
    theme = build(market)['theme_counts'][0]
    assert theme['limit_up_count'] is None and theme['unknown_member_count'] == 1


def test_missing_historical_membership_does_not_borrow_current_topics(market):
    market.membership['selected_date_available'] = False
    market.history[A][-1]['topics'] = [{'name': 'current-only'}]
    result = build(market)
    assert result['theme_counts'] == []
    assert all(row['topics'] == [] and row['topic_membership_reason'] for row in result['items'])


def test_market_endpoint_returns_all_ladder_rows_even_with_legacy_limit_and_page_size(market, monkeypatch):
    monkeypatch.setattr(market_service, 'panel', lambda day: (market, market.status(), day, market.ref, market.rows()))
    monkeypatch.setattr(market_service, 'prior_ladder_feedback', lambda *args: [])
    monkeypatch.setattr(market_service, 'data_coverage', lambda *args: {'notice': 'fixture'})
    result = market_service.market_page('ladder', TODAY, limit=1, page_size=1, page=3)
    assert result['total'] == 3 and len(result['items']) == 3
    assert result['pagination_mode'] == 'all' and result['pages'] == 1


def test_prior_day_rate_and_seal_fields_are_not_misdated_on_failed_rows(market):
    previous = market.ref['_supplement']['annual_pools'][DAYS[-2]]['items'][0]
    previous.update(turnover_ratio_pct=25, seal_money=100000, break_count=2)
    failed = build(market)['failed_items'][0]
    assert failed['turnover_rate'] is None and failed['seal_money'] is None and failed['break_count'] is None


def test_future_quotes_are_filtered_before_duplicate_check(market):
    for history in market.history.values(): history.append({'date': '2026-01-14', 'close': 99, 'volume': 100})
    rows = [dict(history[-2]) for history in market.history.values()]
    result = ladder.build_ladder(market, market.status(), TODAY, market.ref, rows, {})
    assert [row['instrument_id'] for row in result['rebound_items']] == [A]
    assert all(row['close'] == 10 for row in result['items'])


@pytest.mark.parametrize('target', [TODAY, DAYS[-2]])
@pytest.mark.parametrize('mode', ['quarantine', 'rejected', 'pagination', 'ambiguous',
                                  'raw_only', 'missing_raw', 'duplicate_raw', 'invalid_raw',
                                  'duplicate_accepted', 'unmapped', 'malformed'])
def test_scope_completeness_never_drops_unaccepted_or_unmapped_candidates(market, target, mode):
    record = market.ref['_supplement']['annual_pools'][target]
    if mode == 'quarantine': record['quarantine'] = [{'ticker': X[:6]}]
    elif mode == 'rejected': record['rejected_codes'] = [X[:6]]
    elif mode == 'pagination': record['pagination_complete'] = False
    elif mode == 'ambiguous': record['ambiguous_code_rows'] = 1
    elif mode == 'raw_only': record['raw_codes'].append(X[:6])
    elif mode == 'missing_raw': record['raw_codes'].pop()
    elif mode == 'duplicate_raw': record['raw_codes'].append(record['raw_codes'][0])
    elif mode == 'invalid_raw': record['raw_codes'].append('unknown')
    elif mode == 'duplicate_accepted': record['items'].append(dict(record['items'][0]))
    elif mode == 'unmapped': record['items'].append({'ticker': '999999', 'date': target, 'last_price': 10})
    elif mode == 'malformed': record['items'].append('unresolved-row')
    result = build(market)
    # Stronger range claims do not discard independently verified positives.
    assert {row['instrument_id'] for row in result['items']} == {A, C, OTHER}
    if target == TODAY:
        assert result['main_coverage']['source_pool_complete'] is False
        assert result['main_coverage']['complete'] is False
        assert result['classification_coverage']['twenty_cm']['source_scope_complete'] is False
    else:
        coverage = result['classification_coverage']['failed']
        assert coverage['source_scope_complete'] is False and coverage['complete'] is False


def test_legacy_complete_pool_still_requires_every_candidate_to_resolve(market):
    record = market.ref['_supplement']['annual_pools'].pop(TODAY)
    market.ref.update(pool_trade_date=TODAY, limitup=record['items'],
                      pool_coverage={'limitup': {'complete': True}})
    assert build(market)['main_coverage']['complete'] is True
    market.ref['limitup'].append({'ticker': '999999', 'date': TODAY, 'last_price': 10})
    result = build(market)
    assert result['main_coverage']['source_pool_complete'] is False
    assert {row['instrument_id'] for row in result['items']} == {A, C, OTHER}


def _shape_quote(market, *, one_price=False):
    market.history[A][-2].update(open=10, high=10, low=10, close=10, price_basis='none')
    market.history[A][-1].update(open=11 if one_price else 9.5, high=11, low=11 if one_price else 9,
                               close=11, price_basis='none', corporate_action=False,
                               corporate_action_coverage_known=True)
    market.ref['_supplement']['annual_pools'][TODAY]['items'][0]['last_price'] = 11


@pytest.mark.parametrize('one_price,kind', [(False, 'earth_to_sky'), (True, 'one_price_limit_up')])
def test_ladder_patterns_reuse_exact_chart_evaluator_and_existing_batch(market, one_price, kind):
    from app.services.chart_patterns import attach_chart_patterns
    _shape_quote(market, one_price=one_price)
    result = build(market)
    row = next(item for item in result['items'] if item['instrument_id'] == A)
    assert row['pattern_date'] == row['pattern_as_of'] == TODAY
    assert row['snapshot_id'] == 'fixture'
    assert [item['kind'] for item in row['pattern_events']] == [kind]
    assert row['pattern_status'][kind]['state'] is True
    chart = attach_chart_patterns({'instrument': market.master[0], 'snapshot_id': 'fixture',
                                  'as_of': TODAY, 'period': 'D', 'bars': [dict(market.history[A][-1])]},
                                 raw_rows=market.history[A][-3:], calendar=DAYS,
                                 closing_states={TODAY: {'up': row['limit_up_evidence']}}, evaluation_dates=[TODAY])
    assert row['pattern_events'] == chart['bars'][0]['pattern_events']
    assert len(market.history_calls) == 1
    assert market.history_calls[0][1:] == (DAYS[-3], TODAY)
    rebound = next(item for item in result['rebound_items'] if item['instrument_id'] == A)
    assert rebound['pattern_events'] == row['pattern_events']


@pytest.mark.parametrize('mode', ['previous_missing', 'listing_missing', 'ipo', 'action', 'action_unknown', 'one_cent_source', 'outside_band', 'snapshot_changed'])
def test_ladder_earth_shape_does_not_relax_chart_guards(market, mode, monkeypatch):
    _shape_quote(market)
    if mode == 'previous_missing': market.history[A] = [item for item in market.history[A] if item['date'] != DAYS[-2]]
    elif mode == 'listing_missing': market.master[0]['list_date'] = None
    elif mode == 'ipo': market.master[0]['list_date'] = DAYS[-4]
    elif mode == 'action': market.history[A][-1]['corporate_action'] = True
    elif mode == 'action_unknown': market.history[A][-1]['corporate_action_coverage_known'] = False
    elif mode == 'one_cent_source': market.ref['_supplement']['annual_pools'][TODAY]['items'][0]['last_price'] = 10.99
    elif mode == 'outside_band': market.history[A][-1]['low'] = 8.99
    elif mode == 'snapshot_changed': monkeypatch.setattr(market, 'status', lambda: {'snapshot_id': 'newer', 'data_end': TODAY})
    initial_status = {'snapshot_id': 'fixture', 'data_end': TODAY}
    result = ladder.build_ladder(market, initial_status, TODAY, market.ref, market.rows(), {})
    row = next(item for item in result['items'] if item['instrument_id'] == A)
    assert not row['pattern_events']
    assert row['pattern_status']['earth_to_sky']['state'] is None


def test_ladder_action_metadata_comes_only_from_matching_fixed_generation_panel(market):
    _shape_quote(market)
    market.history[A][-1].pop('corporate_action')
    market.history[A][-1].pop('corporate_action_coverage_known')
    rows = market.rows()
    current = next(item for item in rows if item['instrument_id'] == A)
    current.update(snapshot_id='fixture', corporate_action=False, corporate_action_coverage_known=True)
    result = ladder.build_ladder(market, market.status(), TODAY, market.ref, rows, {})
    row = next(item for item in result['items'] if item['instrument_id'] == A)
    assert row['pattern_status']['earth_to_sky']['state'] is True
    assert 'corporate_action' not in market.history[A][-1], 'display metadata must not modify retained source rows'
    current['snapshot_id'] = 'unrelated-generation'
    result = ladder.build_ladder(market, market.status(), TODAY, market.ref, rows, {})
    row = next(item for item in result['items'] if item['instrument_id'] == A)
    assert row['pattern_status']['earth_to_sky']['state'] is None
    current.update(snapshot_id='fixture', low=8.99)
    result = ladder.build_ladder(market, market.status(), TODAY, market.ref, rows, {})
    row = next(item for item in result['items'] if item['instrument_id'] == A)
    assert row['pattern_status']['earth_to_sky']['state'] is None


def test_outlook_can_skip_card_pattern_display_work_without_changing_ladder_facts(market, monkeypatch):
    _shape_quote(market)
    expected = build(market)
    def forbidden(*args, **kwargs): raise AssertionError('outlook requested no card pattern evaluation')
    monkeypatch.setattr(ladder, 'attach_chart_patterns', forbidden)
    result = ladder.build_ladder(market, market.status(), TODAY, market.ref, market.rows(), {}, include_patterns=False)
    for key in ('items', 'rebound_items', 'failed_items', 'twenty_cm_items'):
        without_patterns = lambda rows: [{name: value for name, value in item.items() if not name.startswith('pattern_') and name != 'board_presentation'} for item in rows]
        assert without_patterns(result[key]) == without_patterns(expected[key])
    assert result['theme_counts'] == expected['theme_counts']
    assert result['main_coverage'] == expected['main_coverage']
