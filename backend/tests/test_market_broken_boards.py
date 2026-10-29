"""Synthetic event fixtures are never product data or provider requests."""
from copy import deepcopy

import pytest

from app.services.market_broken_boards import build_analysis, decorate_sectors

DAYS = ['2026-10-15', '2026-10-16', '2026-10-19', '2026-10-20', '2026-10-21', '2026-10-22', '2026-10-23', '2026-10-26']
DAY, PREVIOUS = DAYS[-1], DAYS[-2]
A, B, C, X = '600001.SH', '600002.SH', '300001.SZ', '688001.SH'


class Market:
    def __init__(self):
        self.history = {symbol: [dict(instrument_id=symbol, date=day, close=10., volume=100,
                                     high=11., low=9., open=10., corporate_action=False,
                                     corporate_action_coverage_known=True, price_continuity=True,
                                     change_pct=0., name='测试' + symbol[:6]) for day in DAYS]
                        for symbol in (A, B, C, X)}
        for symbol, close in ((A, 11.), (B, 9.), (C, 11.), (X, 10.3)):
            self.history[symbol][-1].update(close=close, change_pct=(close/10-1)*100)
        self.ref = {'pool_history': {day: self.container(day) for day in DAYS}}
        self.ref['pool_history'][PREVIOUS] = self.container(PREVIOUS, broken=[B, C])
        self.ref['pool_history'][DAY] = self.container(DAY, up=[A, C], broken=[X], down=[B])
        self.calls = []

    def container(self, day, up=(), broken=(), down=()):
        result = {'date': day, 'source': 'fuyao', 'coverage': {}, 'quarantine': {}}
        for key, symbols in (('limitup', up), ('breakpool', broken), ('limitdown', down)):
            result[key] = [dict(thscode=symbol, ticker=symbol[:6], name='测试'+symbol[:6],
                                date=day, last_price=next(row['close'] for row in self.history[symbol] if row['date'] == day),
                                open_times=3) for symbol in symbols]
            result['coverage'][key] = {'complete': True, 'declared_total': len(result[key]), 'quarantined_rows': 0}
        return result

    def calendar(self): return DAYS
    def status(self): return {'snapshot_id': 'market-fixture', 'data_end': DAY}
    def rows(self): return [deepcopy(values[-1]) for values in self.history.values()]
    def history_for_instruments(self, symbols, start=None, end=None):
        self.calls.append((symbols, start, end))
        return {symbol: [deepcopy(row) for row in self.history[symbol] if start <= row['date'] <= end] for symbol in symbols}
    def bars(self, *args, **kwargs): raise AssertionError('must not read individual chart histories')
    def history_panel(self, *args, **kwargs): raise AssertionError('must not scan whole market history')


def build(market, **kwargs):
    return build_analysis(market, market.status(), DAY, market.ref, market.rows(), **kwargs)


def test_distinct_current_failures_complete_history_and_strict_previous_feedback_use_one_batch():
    market = Market(); before = deepcopy(market.ref)
    result = build(market); current = result['current']; feedback = result['previous_feedback']
    assert current['state'] == 'complete'
    assert (current['limit_up_count'], current['broken_count'], current['touch_count']) == (2, 1, 3)
    assert current['broken_rate_pct'] == pytest.approx(100/3)
    assert current['drawdown_valid_count'] == current['large_drawdown_count'] == 1
    assert current['avg_drawdown_from_high_pct'] == pytest.approx((10.3/11-1)*100)
    assert [item['date'] for item in result['history']] == DAYS[-7:]
    assert result['comparison']['broken_rate_delta_pp'] == pytest.approx(100/3-100)
    assert feedback['previous_date'] == PREVIOUS and feedback['source_count'] == 2
    assert feedback['valid_count'] == 2 and feedback['unknown_count'] == 0
    assert feedback['avg_change_pct'] == feedback['median_change_pct'] == pytest.approx(0)
    assert feedback['limit_up_count'] == feedback['limit_down_count'] == 1
    assert feedback['return_limit_rate_pct'] == 50
    assert feedback['large_drop_count'] == 1
    assert len(market.calls) == 1 and market.calls[0][1:] == (DAYS[-7], DAY)
    assert market.ref == before


@pytest.mark.parametrize('mode', ['missing', 'not_callable', 'none', 'empty', 'invalid'])
def test_missing_calendar_capability_stays_unknown_without_source_or_history_reads(mode):
    class NoCalendar:
        def history_for_instruments(self, *_args, **_kwargs):
            raise AssertionError('cannot choose an event history window without a calendar')
    market = NoCalendar()
    if mode == 'not_callable':
        market.calendar = None
    elif mode != 'missing':
        market.calendar = lambda: {'none': None, 'empty': [], 'invalid': 42}[mode]
    report = build_analysis(market, {'snapshot_id': 'missing-calendar'}, DAY,
                            {'pool_trade_date': DAY, 'source': 'fuyao', 'breakpool': []}, [],
                            with_memberships=True)
    current = report['current']
    assert current['state'] == 'unknown'
    for key in ('limit_up_count', 'broken_count', 'touch_count', 'broken_rate_pct',
                'observed_limit_up_count', 'observed_broken_count', 'observed_touch_count'):
        assert current[key] is None
    assert current['items'] == [] and '交易日历' in current['reason']
    assert report['history'] == []
    assert report['previous_feedback']['previous_date'] is None
    assert '交易日历' in report['previous_feedback']['reason']
    assert report['comparison'] == {'previous_date': None, 'broken_rate_delta_pp': None}


def test_real_calendar_or_history_read_errors_are_not_hidden_as_missing_coverage():
    market = Market()
    def unavailable():
        raise RuntimeError('calendar read failed')
    market.calendar = unavailable
    with pytest.raises(RuntimeError, match='calendar read failed'):
        build(market)
    market.calendar = lambda: DAYS
    def bad_history(*_args, **_kwargs):
        raise RuntimeError('history read failed')
    market.history_for_instruments = bad_history
    with pytest.raises(RuntimeError, match='history read failed'):
        build(market)


def test_current_only_sector_analysis_reads_today_without_building_prior_feedback(monkeypatch):
    from app.services import market_broken_boards
    market = Market()
    # A prior-only candidate must not expand the current-sector read batch.
    market.ref['pool_history'][DAY] = market.container(DAY, up=[A], broken=[X])
    expected = build(market)['current']
    market.calls.clear()
    def no_feedback(*args, **kwargs):
        raise AssertionError('sector decoration does not consume previous feedback')
    monkeypatch.setattr(market_broken_boards, '_feedback', no_feedback)
    monkeypatch.setattr(market_broken_boards, 'resolve_reference', lambda *_args, **_kwargs: pytest.fail('sector context already resolved; do not look up members again'))
    result = build_analysis(market, market.status(), DAY, market.ref, market.rows(),
                            history_days=1, include_feedback=False)
    assert result['current'] == expected
    assert result['previous_feedback'] == {}
    assert result['comparison'] == {'previous_date': None, 'broken_rate_delta_pp': None}
    assert [item['date'] for item in result['history']] == [DAY]
    assert market.calls == [([A, X], DAY, DAY)]


def test_sector_service_requests_only_current_broken_board_structure(monkeypatch):
    from app.services import market_service, market_broken_boards, sector_activity
    market = Market(); calls = []
    def build_current(*args, **kwargs):
        calls.append(kwargs)
        return {'date': DAY, 'snapshot_id': 'market-fixture', 'current': {'items': [], 'state': 'unknown'}}
    monkeypatch.setattr(market_service, 'resolve_reference', lambda *args: ({'topics': []}, {}))
    monkeypatch.setattr(market_service, 'sector_prices_for_date', lambda *args: {})
    monkeypatch.setattr(market_service, 'sector_view', lambda *args: {'items': []})
    monkeypatch.setattr(market_broken_boards, 'build_analysis', build_current)
    monkeypatch.setattr(sector_activity, 'decorate_sector_activity', lambda *args: (args[3], {}))
    assert market_service._sectors_from_panel(market, market.status(), DAY, market.ref, {})['items'] == []
    assert calls == [{'history_days': 1, 'include_feedback': False}]


def test_open_times_are_not_households_and_identical_source_duplicates_are_deduplicated():
    market = Market(); source = market.ref['pool_history'][DAY]
    source['breakpool'].append(deepcopy(source['breakpool'][0]))
    source['coverage']['breakpool']['declared_total'] += 1
    source['breakpool'][0]['open_times'] = 100
    current = build(market)['current']
    assert current['state'] == 'complete' and current['broken_count'] == 1


@pytest.mark.parametrize('mode', ['missing', 'partial', 'pagination', 'quarantine', 'bad_total', 'wrong_date', 'bad_semantics', 'missing_source', 'scope', 'source', 'ambiguous', 'invalid_rows', 'raw_codes', 'raw_codes_invalid', 'wrong_kind', 'bad_state', 'bad_identified', 'bool_total'])
def test_missing_coverage_or_wrong_scope_never_produces_an_exact_denominator(mode):
    market = Market(); source = market.ref['pool_history'][DAY]
    if mode == 'missing': del source['breakpool']
    elif mode == 'partial': source['coverage']['breakpool']['complete'] = False
    elif mode == 'pagination': source['coverage']['breakpool']['pagination_complete'] = False
    elif mode == 'quarantine': source['quarantine']['breakpool'] = [{'thscode': X}]
    elif mode == 'bad_total': source['coverage']['breakpool']['declared_total'] = 100
    elif mode == 'wrong_date': source['date'] = PREVIOUS
    elif mode == 'bad_semantics': source['date_semantics'] = 'current_unverified_trade_date'
    elif mode == 'missing_source': del source['source']
    elif mode == 'scope': source['coverage']['breakpool']['scope'] = 'another-universe'
    elif mode == 'source': source['coverage']['breakpool']['source'] = 'other-provider'
    elif mode == 'ambiguous': source['coverage']['breakpool']['ambiguous_code_rows'] = 1
    elif mode == 'invalid_rows': source['coverage']['breakpool']['invalid_rows'] = 1
    elif mode == 'raw_codes': source['coverage']['breakpool']['raw_codes'] = [A[:6]]
    elif mode == 'raw_codes_invalid': source['coverage']['breakpool']['raw_codes'] = [{'bad': 'code'}]
    elif mode == 'wrong_kind': source['coverage']['breakpool']['pool_kind'] = 'limitup'
    elif mode == 'bad_state': source['coverage']['breakpool']['state'] = 'unknown'
    elif mode == 'bad_identified': source['coverage']['breakpool']['identified_rows'] = 0
    elif mode == 'bool_total': source['coverage']['breakpool']['declared_total'] = True
    current = build(market)['current']
    assert current['state'] != 'complete'
    assert current['broken_rate_pct'] is current['touch_count'] is None and current['reason']


@pytest.mark.parametrize('mode', ['overlap', 'down_overlap', 'row_source', 'row_date', 'identity', 'price', 'duplicate_price', 'missing_quote', 'zero_volume', 'duplicate_quote'])
def test_conflicting_or_unverifiable_event_is_not_counted_as_failure(mode):
    market = Market(); source = market.ref['pool_history'][DAY]
    if mode == 'overlap': source['limitup'].append(deepcopy(source['breakpool'][0])); source['coverage']['limitup']['declared_total'] += 1
    elif mode == 'down_overlap': source['limitdown'].append(deepcopy(source['breakpool'][0])); source['coverage']['limitdown']['declared_total'] += 1
    elif mode == 'row_source': source['breakpool'][0]['source'] = 'other'
    elif mode == 'row_date': source['breakpool'][0]['date'] = PREVIOUS
    elif mode == 'identity': source['breakpool'][0]['ticker'] = A[:6]
    elif mode == 'price': source['breakpool'][0]['last_price'] = 99
    elif mode == 'duplicate_price': source['breakpool'].append({**source['breakpool'][0], 'last_price': 99}); source['coverage']['breakpool']['declared_total'] += 1
    elif mode == 'missing_quote': market.history[X].pop()
    elif mode == 'zero_volume': market.history[X][-1]['volume'] = 0
    elif mode == 'duplicate_quote': market.history[X].append(deepcopy(market.history[X][-1]))
    current = build(market)['current']
    assert current['observed_broken_count'] == 0 and current['broken_rate_pct'] is None
    assert not any(row['instrument_id'] == X for row in current['items'])


def test_empty_complete_pools_are_valid_zero_but_no_touch_has_no_rate():
    market = Market(); market.ref['pool_history'][DAY] = market.container(DAY)
    current = build(market)['current']
    assert current['state'] == 'complete'
    assert current['limit_up_count'] == current['broken_count'] == current['touch_count'] == 0
    assert current['broken_rate_pct'] is None


def test_no_actual_high_does_not_invent_limit_price_or_retracement():
    market = Market(); del market.history[X][-1]['high']
    current = build(market)['current']
    assert current['broken_count'] == 1
    assert current['drawdown_valid_count'] == 0 and current['avg_drawdown_from_high_pct'] is None


def test_action_breaks_return_comparability_without_erasing_independent_pool_fact():
    market = Market(); market.history[C][-1]['corporate_action'] = True
    feedback = build(market)['previous_feedback']
    assert feedback['valid_count'] == feedback['unknown_count'] == 1
    assert feedback['avg_change_pct'] == pytest.approx(-10)
    assert next(row for row in feedback['items'] if row['instrument_id'] == C)['change_pct'] is None
    assert feedback['return_limit_rate_pct'] == 50


def test_missing_previous_pool_does_not_fallback_to_older_or_future_events():
    market = Market(); del market.ref['pool_history'][PREVIOUS]
    market.ref['pool_history'][DAYS[-3]] = market.container(DAYS[-3], broken=[A])
    market.ref['pool_history']['2099-01-01'] = {'source': 'fuyao', 'breakpool': [{'thscode': X, 'last_price': 999}]}
    feedback = build(market)['previous_feedback']
    assert feedback['previous_date'] == PREVIOUS and feedback['source_count'] is None
    assert feedback['items'] == [] and feedback['avg_change_pct'] is None


def test_future_quotes_never_change_observation():
    market = Market(); before = build(market)
    for values in market.history.values(): values.append({**values[-1], 'date': '2099-01-01', 'close': 999})
    # rows are explicit selected-date panel facts rather than the newly appended tail.
    rows = [deepcopy(values[-2]) for values in market.history.values()]
    assert build_analysis(market, market.status(), DAY, market.ref, rows) == before


def test_topic_intersection_uses_selected_archive_and_partial_members_are_lower_bounds():
    market = Market(); analysis = build(market)
    topic = {'topic_id': 'concept-1', 'topic_code': '885001.TI', 'kind': 'concept', 'source': 'fuyao',
             'observed_date': PREVIOUS, 'members_complete': True, 'instrument_ids': [A, X]}
    row = {'topic_id': 'concept-1', 'topic_code': '885001.TI', 'kind': 'concept', 'quote_source': 'fuyao', 'name': '测试题材'}
    coverage = {'selected_date_available': True, 'source_snapshot_day': DAY}
    result = decorate_sectors([row], {'topics': [topic]}, coverage, analysis)[0]['broken_board']
    assert result['date'] == DAY and result['snapshot_id'] == 'market-fixture'
    assert result['state'] == 'complete' and result['broken_rate_pct'] == 50
    assert {item['instrument_id'] for item in result['details']} == {A, X}
    topic['members_complete'] = False
    partial = decorate_sectors([row], {'topics': [topic]}, coverage, analysis)[0]['broken_board']
    assert partial['broken_count'] is None and partial['observed_broken_count'] == 1
    assert partial['broken_rate_pct'] is None
    coverage['selected_date_available'] = False
    missing = decorate_sectors([row], {'topics': [topic]}, coverage, analysis)[0]['broken_board']
    assert missing['state'] == 'unknown' and missing['details'] == []
    assert missing['observed_broken_count'] is None


@pytest.mark.parametrize('mode', ['future', 'future_archive', 'missing_archive', 'different_source', 'missing_source', 'missing_row_source', 'duplicate_identity', 'invalid_member', 'industry_level', 'inconsistent_code', 'row_source_conflict', 'row_level_only', 'topic_level_only'])
def test_topic_wrong_date_source_or_ambiguous_identity_never_produces_precise_rate(mode):
    market = Market(); analysis = build(market)
    topic = {'topic_id': 'concept-1', 'topic_code': '885001.TI', 'kind': 'concept', 'source': 'fuyao',
             'observed_date': DAY, 'members_complete': True, 'instrument_ids': [A, X]}
    row = {'topic_id': 'concept-1', 'topic_code': '885001.TI', 'kind': 'concept', 'quote_source': 'fuyao'}
    topics = [topic]
    coverage = {'selected_date_available': True, 'source_snapshot_day': DAY}
    if mode == 'future': topic['observed_date'] = '2099-01-01'
    elif mode == 'future_archive': coverage['source_snapshot_day'] = '2099-01-01'
    elif mode == 'missing_archive': del coverage['source_snapshot_day']
    elif mode == 'different_source': topic['source'] = 'other-provider'
    elif mode == 'missing_source': del topic['source']
    elif mode == 'missing_row_source': del row['quote_source']
    elif mode == 'duplicate_identity': topics.append(deepcopy(topic))
    elif mode == 'industry_level': topic.update(kind='industry', level=2); row.update(kind='industry', level=1)
    elif mode == 'row_level_only': topic.update(kind='industry'); row.update(kind='industry', level=1)
    elif mode == 'topic_level_only': topic.update(kind='industry', level=1); row.update(kind='industry')
    elif mode == 'row_source_conflict': row.update(source='fuyao', quote_source='other')
    elif mode == 'inconsistent_code': row['topic_code'] = '885002.TI'
    else: topic['instrument_ids'].append('bad')
    result = decorate_sectors([row], {'topics': topics}, coverage, analysis)[0]['broken_board']
    assert result['broken_rate_pct'] is None and result['state'] != 'complete'


def test_selected_day_snapshot_generation_change_suppresses_mixed_analysis():
    market = Market(); status = market.status()
    market.status = lambda: {**status, 'snapshot_id': 'new-generation'}
    result = build_analysis(market, status, DAY, market.ref, market.rows())
    assert result['current']['state'] == 'unknown'
    assert result['current']['broken_rate_pct'] is None and result['current']['items'] == []
    assert result['history'] == []


@pytest.mark.parametrize('mode', ['source', 'scope', 'complete', 'rows'])
def test_same_day_current_and_historical_declarations_must_agree(mode):
    market = Market(); own = deepcopy(market.ref['pool_history'][DAY])
    market.ref.update(pool_trade_date=DAY, source=own['source'],
                      limitup=own['limitup'], breakpool=own['breakpool'], limitdown=own['limitdown'],
                      pool_coverage=own['coverage'])
    historical = market.ref['pool_history'][DAY]
    if mode == 'source': historical['source'] = 'another-provider'
    elif mode == 'scope': historical['scope'] = 'another-scope'
    elif mode == 'complete': historical['coverage']['breakpool']['complete'] = False
    else: historical['breakpool'][0]['last_price'] = 99
    current = build(market)['current']
    assert current['broken_rate_pct'] is None and current['state'] != 'complete'


def test_source_row_order_alone_is_not_a_conflict_and_annual_pool_cannot_fill_break_pool():
    market = Market(); own = deepcopy(market.ref['pool_history'][DAY])
    market.ref.update(pool_trade_date=DAY, source=own['source'],
                      limitup=list(reversed(own['limitup'])), breakpool=own['breakpool'], limitdown=own['limitdown'],
                      pool_coverage=own['coverage'])
    assert build(market)['current']['state'] == 'complete'
    del market.ref['breakpool']; del market.ref['pool_history'][DAY]['breakpool']
    market.ref['_supplement'] = {'annual_pools': {DAY: {'date': DAY, 'source': 'fuyao', 'complete': True, 'items': own['limitup']}}}
    assert build(market)['current']['broken_rate_pct'] is None


def test_current_import_reference_source_uses_same_generation_published_source_evidence():
    market = Market(); own = deepcopy(market.ref['pool_history'][DAY])
    market.ref.update(pool_trade_date=DAY, limitup=own['limitup'], breakpool=own['breakpool'],
                      limitdown=own['limitdown'], pool_coverage=own['coverage'])
    del market.ref['pool_history'][DAY]
    status = {**market.status(), 'source': 'fuyao'}
    market.status = lambda: status
    result = build_analysis(market, status, DAY, market.ref, market.rows())
    assert result['current']['state'] == 'complete'
    assert result['current']['coverage']['breakpool']['source_evidence'] == 'published-market-source'


def test_current_missing_source_can_use_equivalent_explicit_historical_pair():
    market = Market(); own = deepcopy(market.ref['pool_history'][DAY])
    market.ref.update(pool_trade_date=DAY, limitup=list(reversed(own['limitup'])),
                      breakpool=own['breakpool'], limitdown=own['limitdown'], pool_coverage=own['coverage'])
    assert build(market)['current']['state'] == 'complete'
    market.ref['breakpool'] = [{**own['breakpool'][0], 'last_price': 99}]
    assert build(market)['current']['state'] == 'unknown'


def test_explicit_mismatched_published_source_is_not_a_fuyao_denominator():
    market = Market(); status = {**market.status(), 'source': 'another-provider'}
    market.status = lambda: status
    result = build_analysis(market, status, DAY, market.ref, market.rows())
    assert result['current']['state'] == 'unknown'
    assert result['current']['observed_touch_count'] == 0 and result['current']['broken_rate_pct'] is None


def test_published_source_change_during_read_suppresses_same_id_mixed_context():
    market = Market(); status = {**market.status(), 'source': 'fuyao'}
    market.status = lambda: {**status, 'source': 'another-provider'}
    result = build_analysis(market, status, DAY, market.ref, market.rows())
    assert result['current']['state'] == 'unknown'
    assert result['current']['observed_broken_count'] is None and result['history'] == []


def test_narrowed_today_scope_cannot_prove_prior_cohort_absent():
    market = Market()
    market.ref['pool_history'][DAY] = market.container(DAY)
    market.ref['pool_history'][DAY]['scope'] = 'only-one-board-source-universe'
    feedback = build(market)['previous_feedback']
    assert feedback['valid_count'] == 2
    assert all(row['current_state'] == 'unknown' for row in feedback['items'])
    assert feedback['return_limit_rate_pct'] is None


@pytest.mark.parametrize('missing_pool', ['limitdown', 'limitup'])
def test_missing_one_direction_does_not_erase_independent_other_direction(missing_pool):
    market = Market()
    del market.ref['pool_history'][DAY][missing_pool]
    feedback = build(market)['previous_feedback']
    assert feedback['source_count'] == feedback['verified_count'] == feedback['valid_count'] == 2
    assert feedback['unknown_count'] == 0
    assert feedback['state'] == 'partial'
    if missing_pool == 'limitdown':
        assert feedback['limit_up_count'] == 1 and feedback['return_limit_rate_pct'] == 50
        assert feedback['limit_down_count'] is None
        assert feedback['event_up_unknown_count'] == 0 and feedback['event_down_unknown_count'] == 2
        assert next(row for row in feedback['items'] if row['instrument_id'] == B)['current_state'] == 'not_limit_up'
    else:
        assert feedback['limit_down_count'] == 1
        assert feedback['limit_up_count'] is feedback['return_limit_rate_pct'] is None
        assert feedback['event_down_unknown_count'] == 0 and feedback['event_up_unknown_count'] == 2


def test_price_partial_and_missing_down_pool_keep_complete_up_event_proof():
    market = Market(); del market.ref['pool_history'][DAY]['limitdown']
    market.history[C][-1]['corporate_action'] = True
    market.history[C][-1]['price_continuity'] = False
    feedback = build(market)['previous_feedback']
    assert feedback['state'] == 'partial'
    assert feedback['valid_count'] == feedback['unknown_count'] == 1
    assert feedback['avg_change_pct'] == pytest.approx(-10)
    assert feedback['limit_up_count'] == 1 and feedback['return_limit_rate_pct'] == 50
    assert feedback['event_up_unknown_count'] == 0 and feedback['event_down_unknown_count'] == 2


def test_bad_up_record_cannot_become_negative_through_other_direction_absence():
    market = Market(); del market.ref['pool_history'][DAY]['limitdown']
    next(row for row in market.ref['pool_history'][DAY]['limitup'] if row['thscode'] == C)['last_price'] = 999
    feedback = build(market)['previous_feedback']
    assert feedback['return_limit_rate_pct'] is feedback['limit_up_count'] is None
    assert feedback['event_up_unknown_count'] == 2
    assert next(row for row in feedback['items'] if row['instrument_id'] == C)['limit_up_state'] is None


def test_narrowed_up_scope_does_not_hide_complete_down_direction():
    market = Market()
    market.ref['pool_history'][DAY]['coverage']['limitup']['scope'] = 'narrowed-up-source'
    feedback = build(market)['previous_feedback']
    assert feedback['limit_up_count'] is feedback['return_limit_rate_pct'] is None
    assert feedback['limit_down_count'] == 1 and feedback['event_down_unknown_count'] == 0


def test_current_change_uses_verified_panel_when_history_projection_has_no_percent(monkeypatch):
    market = Market(); original = market.history_for_instruments
    def prices_only(*args, **kwargs):
        result = original(*args, **kwargs)
        for values in result.values():
            for row in values:row.pop('change_pct', None)
        return result
    monkeypatch.setattr(market, 'history_for_instruments', prices_only)
    for options in ({}, {'history_days': 1, 'include_feedback': False}):
        result = build(market, **options)
        current = next(row for row in result['current']['items'] if row['instrument_id'] == X)
        assert current['change_pct'] == pytest.approx(3)
        assert current['drawdown_from_high_pct'] == pytest.approx((10.3/11-1)*100)


@pytest.mark.parametrize('mode', ['action', 'unknown_action', 'discontinuous', 'wrong_panel_date', 'wrong_close', 'wrong_volume', 'bad_percent'])
def test_current_price_comparability_gaps_do_not_erase_independent_event(mode):
    market = Market(); rows = market.rows(); row = next(row for row in rows if row['instrument_id'] == X)
    if mode == 'action':row['corporate_action'] = True
    elif mode == 'unknown_action':row['corporate_action_coverage_known'] = False
    elif mode == 'discontinuous':row['price_continuity'] = False
    elif mode == 'wrong_panel_date':row['date'] = PREVIOUS
    elif mode == 'wrong_close':row['close'] = 99
    elif mode == 'wrong_volume':row['volume'] = 99
    else:row['change_pct'] = 999
    result = build_analysis(market, market.status(), DAY, market.ref, rows)
    current = next(row for row in result['current']['items'] if row['instrument_id'] == X)
    assert current['change_pct'] is None
    assert result['current']['broken_count'] == 1


def topic(day, *, name='概念甲', kind='concept', members=None, code='885001.TI', **extra):
    return {'topic_id': 'fuyao-'+code, 'topic_code': code, 'name': name, 'kind': kind,
            'source': 'fuyao', 'observed_date': day, 'members_complete': True,
            'instrument_ids': list(members or []), **extra}


def member_archive(day, topics):
    return {'topics': topics}, {'selected_date_available': True, 'source_snapshot_day': day}


def test_memberships_are_frozen_by_event_date_and_aggregated_once_per_cohort(monkeypatch):
    from app.services import market_broken_boards
    market = Market(); calls = []
    current_topics = [topic(DAY, name='今日概念', members=[X, X]),
                      topic(DAY, name='今日行业', kind='industry', members=[X], code='881001.TI', level=1),
                      topic(DAY, name='次概念', members=[X, A], code='885002.TI')]
    previous_topics = [topic(PREVIOUS, name='昨日概念', members=[B, C]),
                       topic(PREVIOUS, name='昨日行业', kind='industry', members=[B], code='881001.TI', level=1)]
    def resolve(kind, day, *_args):
        calls.append((kind, day))
        return member_archive(day, deepcopy(current_topics if day == DAY else previous_topics))
    monkeypatch.setattr(market_broken_boards, 'resolve_reference', resolve)
    before = deepcopy(market.ref)
    result = build(market, with_memberships=True)
    own = next(row for row in result['current']['items'] if row['instrument_id'] == X)
    assert {row['name'] for row in own['topics']} == {'今日概念', '次概念'}
    assert [row['name'] for row in own['industries']] == ['今日行业']
    assert own['topics_date'] == DAY
    feedback = result['previous_feedback']
    assert all([tag['name'] for tag in row['topics']] == ['昨日概念'] for row in feedback['items'])
    assert all(row['topics_date'] == PREVIOUS for row in feedback['items'])
    assert [row['name'] for row in feedback['items'][0]['industries']] == ['昨日行业']
    assert all(row['observed_count'] == 1 and row['cohort_count'] == 1 and row['sample_share_pct'] == 100
               for row in result['current']['concept_distribution'])
    assert feedback['concept_distribution'][0]['observed_count'] == 2
    assert feedback['industry_distribution'][0]['sample_share_pct'] == 50
    assert feedback['membership_summary'] == {
        'cohort_count': 2, 'industry_count': 1, 'concept_count': 1,
        'stocks_with_industry': 1, 'stocks_with_concept': 2, 'unclassified_count': 0,
        'topics_date': PREVIOUS, 'reason': None,
        'scope': '当前完整返回的已核验炸板队列关联统计；不受排序、筛选、分页或前10标签截断影响'}
    assert calls == [('sectors', DAY), ('sectors', PREVIOUS)]
    assert len(market.calls) == 1 and market.ref == before


def test_overview_skips_membership_archives_and_details_without_changing_summary_facts(monkeypatch):
    from app.services import market_service, market_broken_boards, market_feedback, market_top_amount, stock_board_presentation
    market = Market(); member_reads = []
    def members(_kind, day, *_args):
        member_reads.append(day)
        return member_archive(day, [topic(day, members=[A, B, C, X])])
    monkeypatch.setattr(market_broken_boards, 'resolve_reference', members)
    complete = build(market, with_memberships=True)
    assert member_reads == [DAY, PREVIOUS]
    member_reads.clear(); market.calls.clear()
    monkeypatch.setattr(market_broken_boards, '_cohort_memberships',
                        lambda *_args, **_kwargs: pytest.fail('overview must not parse either cohort membership archive'))
    monkeypatch.setattr(market_service, 'panel', lambda *_args, **_kwargs:
                        (market, {**market.status(), 'quality': {}}, DAY, market.ref, market.rows()))
    monkeypatch.setattr(market_service, 'cached_gainers', lambda *_args: ([], {}))
    monkeypatch.setattr(market_service, 'data_coverage', lambda *_args: {})
    monkeypatch.setattr(market_service, '_breadth', lambda *_args: {})
    monkeypatch.setattr(market_service, 'amount_change_metadata', lambda *_args: {})
    monkeypatch.setattr(market_feedback, 'feedback_view', lambda *_args: {})
    monkeypatch.setattr(market_top_amount, 'decorate_top_amount', lambda *_args: ([], {}))
    monkeypatch.setattr(stock_board_presentation, 'decorate_board_collections', lambda *_args: _args[-1])
    monkeypatch.setattr(market, 'indices', lambda *_args: [], raising=False)
    report = market_service.overview(DAY)['broken_board_analysis']
    membership_fields = {'items', 'concept_distribution', 'industry_distribution', 'topics_date',
                         'membership_reason', 'membership_scope', 'membership_snapshot_day', 'membership_summary'}
    expected = deepcopy(complete)
    for key in ('current', 'previous_feedback'):
        expected[key] = {field: value for field, value in expected[key].items() if field not in membership_fields}
        assert 'items' not in report[key]
    expected['history'] = [{field: value for field, value in row.items() if field not in membership_fields}
                           for row in expected['history']]
    assert report == expected
    assert report['comparison']['broken_rate_delta_pp'] == pytest.approx(100/3-100)
    assert report['observations'] == complete['observations']
    assert member_reads == []
    assert len(market.calls) == 1 and market.calls[0][1:] == (DAYS[-7], DAY)


def test_emotion_route_keeps_both_required_cohorts_and_current_non_cohort_facts(monkeypatch):
    from app.services import market_service, market_broken_boards, market_sentiment, market_outlook, market_feedback
    market = Market(); member_reads = []
    monkeypatch.setattr(market_service, 'panel', lambda *_args, **_kwargs:
                        (market, market.status(), DAY, market.ref, market.rows()))
    monkeypatch.setattr(market, 'breadth_history', lambda *_args: [], raising=False)
    monkeypatch.setattr(market_service, '_breadth', lambda *_args: {'up': 0, 'down': 0, 'flat': 0, 'unknown': 4})
    monkeypatch.setattr(market_service, 'data_coverage', lambda *_args: {})
    monkeypatch.setattr(market_sentiment, 'describe_sentiment', lambda *_args: {})
    monkeypatch.setattr(market_outlook, 'build_market_outlook', lambda *_args, **_kwargs: {})
    monkeypatch.setattr(market_feedback, 'feedback_view', lambda *_args: {})
    def members(_kind, day, *_args):
        member_reads.append(day)
        return member_archive(day, [topic(day, members=[A, B, C, X])])
    monkeypatch.setattr(market_broken_boards, 'resolve_reference', members)
    report = market_service.market_page('emotion', DAY)['broken_board_analysis']
    assert member_reads == [DAY, PREVIOUS]
    assert len(report['current']['items']) == 3
    own = next(row for row in report['current']['items'] if row['outcome'] == 'broken')
    assert own['topics'] and own['topics_date'] == DAY
    for row in report['current']['items']:
        if row['outcome'] == 'limit_up':
            assert row['close'] == 11. and row['date'] == DAY and 'topics' not in row
    assert len(report['previous_feedback']['items']) == 2
    assert all(row['topics'] and row['topics_date'] == PREVIOUS for row in report['previous_feedback']['items'])
    assert report['current']['concept_distribution'][0]['sample_avg_change_pct'] == pytest.approx(3.)
    assert report['previous_feedback']['concept_distribution'][0]['sample_avg_change_pct'] == pytest.approx(0.)


@pytest.mark.parametrize('mode', ['missing', 'future_archive', 'future_topic', 'unknown_source', 'conflicting_duplicate'])
def test_missing_or_future_member_evidence_never_fills_prior_cohort_from_today(monkeypatch, mode):
    from app.services import market_broken_boards
    market = Market()
    def resolve(_kind, day, *_args):
        tags = [topic(day, members=[X, B, C])]
        local, coverage = member_archive(day, tags)
        if mode == 'missing':coverage['selected_date_available'] = False
        elif mode == 'future_archive':coverage['source_snapshot_day'] = '2099-01-01'
        elif mode == 'future_topic':tags[0]['observed_date'] = '2099-01-01'
        elif mode == 'unknown_source':tags[0]['source'] = None
        else:tags.append({**tags[0], 'instrument_ids': [A]})
        return local, coverage
    monkeypatch.setattr(market_broken_boards, 'resolve_reference', resolve)
    result = build(market, with_memberships=True)
    for cohort in (result['current'], result['previous_feedback']):
        assert cohort['concept_distribution'] == cohort['industry_distribution'] == []
        assert all(row['topics'] == row['industries'] == [] for row in cohort['items']
                   if cohort is not result['current'] or row['outcome'] == 'broken')
    assert result['current']['broken_count'] == 1


def test_more_than_ten_topics_do_not_truncate_membership_summary(monkeypatch):
    from app.services import market_broken_boards
    market = Market()
    topics = [topic(DAY, name=f'概念{i}', members=[X], code=f'885{i:03d}.TI') for i in range(12)]
    monkeypatch.setattr(market_broken_boards, 'resolve_reference', lambda _kind, day, *_: member_archive(day, topics))
    result = build(market, history_days=1, include_feedback=False, with_memberships=True)['current']
    assert len(result['concept_distribution']) == 10
    assert result['membership_summary']['concept_count'] == 12
    assert result['membership_summary']['stocks_with_concept'] == result['membership_summary']['cohort_count'] == 1


def test_single_day_detail_route_does_not_build_emotion_or_previous_feedback(monkeypatch):
    from app.services import market_service, market_broken_boards, market_outlook
    market = Market(); calls = []
    monkeypatch.setattr(market_service, 'panel', lambda *_args, **_kwargs: (market, market.status(), DAY, market.ref, market.rows()))
    def build_current(*args, **kwargs):
        calls.append(kwargs)
        return {'date': DAY, 'snapshot_id': 'market-fixture', 'current': {'items': [], 'state': 'unknown'}}
    monkeypatch.setattr(market_broken_boards, 'build_analysis', build_current)
    monkeypatch.setattr(market_outlook, 'build_market_outlook', lambda *_args, **_kwargs: pytest.fail('single-day detail must not compute outlook'))
    result = market_service.market_page('broken-boards', DAY)
    assert result['broken_board_analysis']['date'] == DAY
    assert 'market_outlook' not in result and 'sentiment' not in result
    assert calls == [{'history_days': 1, 'include_feedback': False, 'with_memberships': True}]
    assert result['data_coverage']['basis'] == '逐日已留存来源事件池'


def test_single_day_detail_http_route_uses_real_event_coverage_and_one_history_batch(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers.platform import router
    from app.services import market_service, market_broken_boards, market_outlook
    market = Market(); member_reads = []
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(market_service, 'panel', lambda *_args, **_kwargs: (market, market.status(), DAY, market.ref, market.rows()))
    def members(kind, day, *_args):
        member_reads.append((kind, day))
        return member_archive(day, [topic(day, members=[X])])
    monkeypatch.setattr(market_broken_boards, 'resolve_reference', members)
    monkeypatch.setattr(market_broken_boards, '_feedback', lambda *_args, **_kwargs: pytest.fail('single-day HTTP detail must not compute previous feedback'))
    monkeypatch.setattr(market_outlook, 'build_market_outlook', lambda *_args, **_kwargs: pytest.fail('single-day HTTP detail must not compute outlook'))
    app = FastAPI(); app.include_router(router)
    response = TestClient(app).get('/api/v1/market/broken-boards', params={'date': DAY})
    assert response.status_code == 200, response.text
    result = response.json(); report = result['broken_board_analysis']
    assert report['date'] == DAY and report['previous_feedback'] == {}
    assert [row['date'] for row in report['history']] == [DAY]
    assert report['current']['broken_count'] == 1
    assert next(row for row in report['current']['items'] if row['instrument_id'] == X)['change_pct'] == pytest.approx(3.)
    assert report['current']['concept_distribution'][0]['observed_count'] == 1
    assert result['data_coverage']['label'] == '炸板研究来源事件存档'
    assert result['data_coverage']['basis'] == '逐日已留存来源事件池'
    assert result['data_coverage']['selected_date_available'] is True
    assert DAY in result['data_coverage']['available_dates']
    assert member_reads == [('sectors', DAY)]
    assert market.calls == [(sorted([A, B, C, X]), PREVIOUS, DAY)]
    assert report['current']['previous_event_date'] == PREVIOUS


@pytest.mark.parametrize('value,code', [(None, 'unknown'), (-5, 'large_drop'), (-4.999, 'negative'), (0, 'flat'), (.001, 'positive'), (-13.29, 'large_drop')])
def test_price_feedback_threshold_is_independent_of_closing_limit_state(value, code):
    from app.services.market_broken_boards import _price_feedback
    result = _price_feedback(value)
    assert result['code'] == code and result['change_pct'] == value
    assert result['threshold_pct'] == -5
    assert '跌停' not in result['label']


def test_large_negative_return_never_invents_a_down_limit_event():
    market = Market()
    market.history[B][-1].update(close=8.671, change_pct=-13.29)
    current = market.ref['pool_history'][DAY]
    current['limitdown'] = []
    current['coverage']['limitdown']['declared_total'] = 0
    row = next(row for row in build(market)['previous_feedback']['items'] if row['instrument_id'] == B)
    assert row['change_pct'] == pytest.approx(-13.29)
    assert row['price_feedback']['code'] == 'large_drop'
    assert row['limit_down_state'] is False and row['current_state'] == 'not_listed'


@pytest.mark.parametrize('symbol,board,label', [
    ('600001.SH', 'main', '主板'), ('000001.SZ', 'main', '主板'),
    ('300001.SZ', 'chinext', '创业板'), ('301001.SZ', 'chinext', '创业板'),
    ('688001.SH', 'star', '科创板'), ('689001.SH', 'star', '科创板'),
    ('920001.BJ', 'beijing', '北交所'), ('430001.BJ', 'beijing', '北交所'),
    ('830001.BJ', 'beijing', '北交所'), ('123456.SH', 'unknown', '未知'),
    ('600001.SZ', 'unknown', '未知'), ('300001.SH', 'unknown', '未知'),
    ('688001.SZ', 'unknown', '未知'), ('920001.SH', 'unknown', '未知'),
    ('600001.BJ', 'unknown', '未知'), ('123456.BJ', 'unknown', '未知'),
    ('600001.XX', 'unknown', '未知'), ('600001', 'unknown', '未知'),
    (None, 'unknown', '未知'),
])
def test_board_labels_require_matching_retained_code_and_exchange(symbol, board, label):
    from app.services.market_broken_boards import _board_fields
    result = _board_fields(symbol)
    assert result['board'] == board and result['board_label'] == label
    assert ('不是历史身份档' in result['board_scope'] and '涨跌停制度' in result['board_scope'])
    assert bool(result['board_reason']) is (board == 'unknown')


def test_board_labels_cover_both_cohorts_without_changing_memberships_or_statistics(monkeypatch):
    from app.services import market_broken_boards
    market = Market()
    symbols = [A, C, X, '920001.BJ', '123456.SH']
    for symbol in symbols:
        market.history[symbol] = [dict(row, instrument_id=symbol, name='测试'+symbol[:6])
                                  for row in market.history[A]]
    market.ref['pool_history'][PREVIOUS] = market.container(PREVIOUS, broken=symbols)
    market.ref['pool_history'][DAY] = market.container(DAY, broken=symbols)
    monkeypatch.setattr(market_broken_boards, 'resolve_reference',
                        lambda _kind, day, *_: member_archive(day, [topic(day, members=symbols)]))
    actual = build(market, with_memberships=True)
    monkeypatch.setattr(market_broken_boards, '_board_fields', lambda _symbol: {})
    without_labels = build(market, with_memberships=True)
    for key in ('current', 'previous_feedback'):
        cohort = actual[key]
        assert {row['board'] for row in cohort['items']} == {'main', 'chinext', 'star', 'beijing', 'unknown'}
        assert cohort['concept_distribution'][0]['observed_count'] == len(symbols)
        for row in cohort['items']:
            for field in ('board', 'board_label', 'board_scope', 'board_reason'):
                row.pop(field)
        assert cohort == without_labels[key]
    assert len(market.calls) == 2  # One bounded history batch per full report.


def test_distribution_averages_only_unique_associated_broken_samples_at_full_precision(monkeypatch):
    from app.services import market_broken_boards
    values = [9.04572564612327, 9.28362573099415]
    rows = [dict(instrument_id=B, date=DAY, outcome='broken', change_pct=values[0]),
            dict(instrument_id=C, date=DAY, outcome='broken', change_pct=values[1]),
            dict(instrument_id=B, date=DAY, outcome='broken', change_pct=values[0]),
            dict(instrument_id=A, date=DAY, outcome='limit_up', change_pct=99.)]
    non_cohort = deepcopy(rows[-1])
    tags = [topic(DAY, members=[B, B, C, A]),
            topic(DAY, kind='industry', code='881001.TI', members=[B, B, C, A])]
    monkeypatch.setattr(market_broken_boards, 'resolve_reference', lambda *_args: member_archive(DAY, tags))
    result = market_broken_boards._cohort_memberships(rows, membership_day=DAY, sample_day=DAY,
                                                     status=Market().status(), reference={}, broken_only=True)
    for group in [*result['concept_distribution'], *result['industry_distribution']]:
        assert group['sample_avg_change_pct'] == 9.16467568855871
        assert group['sample_valid_count'] == group['observed_count'] == 2
        assert group['sample_unknown_count'] == 0
        assert group['sample_date'] == DAY and group['sample_basis'] == 'cohort_equal_weight'
        assert group['sample_reason'] is None
        assert 'quote_date' not in group and 'change_pct' not in group
    assert rows[-1] == non_cohort, 'non-cohort limit-up facts must remain without unused membership tags'
    assert all(row['topics'] and row['industries'] for row in rows[:-1])
    assert result['membership_summary']['cohort_count'] == result['membership_summary']['stocks_with_concept'] == 2


@pytest.mark.parametrize('mode', ['one_unknown', 'all_unknown', 'zero'])
def test_distribution_unknown_feedback_is_not_zero_and_zero_remains_valid(monkeypatch, mode):
    from app.services import market_broken_boards
    market = Market()
    if mode == 'zero':
        market.history[B][-1].update(close=10., change_pct=0.)
        market.history[C][-1].update(close=10., change_pct=0.)
        market.ref['pool_history'][DAY] = market.container(DAY, up=[A], broken=[X])
    else:
        market.history[B][-1]['corporate_action'] = True
        if mode == 'all_unknown':market.history[C][-1]['corporate_action_coverage_known'] = False
    monkeypatch.setattr(market_broken_boards, 'resolve_reference', lambda _kind, day, *_: member_archive(day, [topic(day, members=[X, B, C])]))
    result = build(market, with_memberships=True)
    group = result['previous_feedback']['concept_distribution'][0]
    assert group['observed_count'] == 2
    assert group['sample_valid_count'] + group['sample_unknown_count'] == 2
    if mode == 'one_unknown':
        assert group['sample_valid_count'] == group['sample_unknown_count'] == 1
        assert group['sample_avg_change_pct'] == pytest.approx(10.)
        assert '1/2' in group['sample_reason']
    elif mode == 'all_unknown':
        assert group['sample_valid_count'] == 0 and group['sample_unknown_count'] == 2
        assert group['sample_avg_change_pct'] is None and group['sample_reason']
    else:
        assert group['sample_valid_count'] == 2 and group['sample_unknown_count'] == 0
        assert group['sample_avg_change_pct'] == 0. and group['sample_reason'] is None


def test_prior_distribution_uses_prior_membership_and_today_feedback_without_index_reads(monkeypatch):
    from app.services import market_broken_boards, market_supplement, sector_history
    market = Market(); reads = []
    def resolve(_kind, day, *_):
        reads.append(day)
        return member_archive(day, [topic(day, name='今日关联' if day == DAY else '昨日关联', members=[X] if day == DAY else [B])])
    monkeypatch.setattr(market_broken_boards, 'resolve_reference', resolve)
    monkeypatch.setattr(market_supplement, 'load_supplement', lambda *_args, **_kwargs: pytest.fail('sample groups must not add supplemental reads'))
    monkeypatch.setattr(sector_history, 'sector_prices_for_date', lambda *_args, **_kwargs: pytest.fail('sample groups must not read industry indices'))
    result = build(market, with_memberships=True)
    current = result['current']['concept_distribution'][0]
    prior = result['previous_feedback']['concept_distribution'][0]
    assert current['name'] == '今日关联' and current['topics_date'] == DAY
    assert current['sample_avg_change_pct'] == pytest.approx(3.)
    assert prior['name'] == '昨日关联' and prior['topics_date'] == PREVIOUS
    assert prior['sample_date'] == DAY and prior['sample_avg_change_pct'] == pytest.approx(-10.)
    assert prior['sample_valid_count'] == prior['observed_count'] == 1
    assert reads == [DAY, PREVIOUS] and len(market.calls) == 1


@pytest.mark.parametrize('change,date', [(None, DAY), (float('nan'), DAY), (float('inf'), DAY), (True, DAY), (9., PREVIOUS)])
def test_invalid_or_wrong_day_sample_does_not_enter_group_mean(monkeypatch, change, date):
    from app.services import market_broken_boards
    rows = [dict(instrument_id=B, date=date, change_pct=change), dict(instrument_id=C, date=DAY, change_pct=0.)]
    monkeypatch.setattr(market_broken_boards, 'resolve_reference', lambda *_args: member_archive(PREVIOUS, [topic(PREVIOUS, members=[B, C])]))
    result = market_broken_boards._cohort_memberships(rows, membership_day=PREVIOUS, sample_day=DAY,
                                                     status=Market().status(), reference={})
    group = result['concept_distribution'][0]
    assert group['sample_avg_change_pct'] == 0.
    assert group['observed_count'] == 2 and group['sample_valid_count'] == group['sample_unknown_count'] == 1
    assert group['topics_date'] == PREVIOUS and group['sample_date'] == DAY


def test_distribution_is_not_changed_by_list_search_or_pagination(monkeypatch):
    from app.services import market_service, market_broken_boards
    market = Market()
    monkeypatch.setattr(market_service, 'panel', lambda *_args, **_kwargs: (market, market.status(), DAY, market.ref, market.rows()))
    monkeypatch.setattr(market_broken_boards, 'resolve_reference', lambda _kind, day, *_: member_archive(day, [topic(day, members=[X, B, C])]))
    original = market_service.market_page('broken-boards', DAY)
    filtered = market_service.market_page('broken-boards', DAY, query='不存在', page=3, page_size=1)
    assert original['broken_board_analysis']['current']['concept_distribution'] == filtered['broken_board_analysis']['current']['concept_distribution']


def test_historical_single_day_sample_date_is_selected_day_not_latest_day(monkeypatch):
    from app.services import market_broken_boards
    market = Market()
    monkeypatch.setattr(market_broken_boards, 'resolve_reference', lambda _kind, day, *_: member_archive(day, [topic(day, members=[B, C])]))
    rows = [next(row for row in values if row['date'] == PREVIOUS) for values in market.history.values()]
    result = build_analysis(market, market.status(), PREVIOUS, market.ref, rows,
                            history_days=1, include_feedback=False, with_memberships=True)
    group = result['current']['concept_distribution'][0]
    assert group['topics_date'] == group['sample_date'] == PREVIOUS
    assert group['sample_avg_change_pct'] == 0. and group['sample_valid_count'] == 2


def test_prior_event_badges_use_strict_previous_price_verified_pool_and_one_batch(monkeypatch):
    market = Market()
    prior = market.container(PREVIOUS, up=[A, B], broken=[C])
    prior['limitup'][0]['continue_day_cnt'] = 1
    prior['limitup'][1]['continue_day_cnt'] = 8
    market.ref['pool_history'][PREVIOUS] = prior
    market.ref['pool_history'][DAY] = market.container(DAY, broken=[A, B, C, X])
    monkeypatch.setattr('app.services.market_broken_boards.resolve_reference', lambda *_: ({}, {}))
    before = deepcopy(market.ref)
    result = build(market, with_memberships=True)
    by_id = {row['instrument_id']: row for row in result['current']['items']}
    assert [(by_id[symbol]['previous_state'], by_id[symbol]['previous_board_count']) for symbol in (A, B, C, X)] == [
        ('limit_up', 1), ('limit_up', 8), ('limit_break', None), ('none', None)]
    assert all(row['previous_event_date'] == PREVIOUS for row in by_id.values())
    feedback = result['previous_feedback']['items']
    assert [row['instrument_id'] for row in feedback] == [C]
    assert feedback[0]['previous_state'] == 'limit_break'
    assert feedback[0]['previous_board_count'] is None
    assert len(market.calls) == 1
    assert market.calls[0][1:] == (DAYS[-7], DAY)
    assert market.ref == before


@pytest.mark.parametrize('mode', ['no_height', 'invalid_height', 'duplicate_height', 'field_conflict', 'pool_conflict', 'bad_price', 'bad_date', 'missing_prior_pool'])
def test_prior_missing_conflicting_or_unpriced_evidence_never_fabricates_height(mode, monkeypatch):
    market = Market()
    prior = market.container(PREVIOUS, up=[X])
    prior['limitup'][0]['continue_day_cnt'] = 3
    if mode == 'no_height': prior['limitup'][0].pop('continue_day_cnt')
    elif mode == 'invalid_height': prior['limitup'][0]['continue_day_cnt'] = True
    elif mode == 'duplicate_height':
        duplicate = {**prior['limitup'][0], 'continue_day_cnt': 5}
        prior['limitup'].append(duplicate); prior['coverage']['limitup']['declared_total'] = 2
    elif mode == 'field_conflict': prior['limitup'][0]['board_count'] = 1
    elif mode == 'pool_conflict':
        prior['breakpool'] = deepcopy(prior['limitup']); prior['coverage']['breakpool']['declared_total'] = 1
    elif mode == 'bad_price': prior['limitup'][0]['last_price'] = 99
    elif mode == 'bad_date': prior['limitup'][0]['date'] = DAY
    elif mode == 'missing_prior_pool': prior.pop('limitup')
    market.ref['pool_history'][PREVIOUS] = prior
    monkeypatch.setattr('app.services.market_broken_boards.resolve_reference', lambda *_: ({}, {}))
    row = next(row for row in build(market, with_memberships=True)['current']['items'] if row['instrument_id'] == X)
    assert row['previous_board_count'] is None
    assert row['previous_state'] == ('conflict' if mode == 'pool_conflict' else 'limit_up' if mode in ('no_height', 'invalid_height', 'duplicate_height', 'field_conflict') else 'unknown')
    assert row['previous_event_reason']


def test_single_day_broken_detail_adds_only_strict_previous_day_to_same_batch(monkeypatch):
    market = Market()
    monkeypatch.setattr('app.services.market_broken_boards.resolve_reference', lambda *_: ({}, {}))
    result = build(market, history_days=1, include_feedback=False, with_memberships=True)
    assert result['previous_feedback'] == {}
    assert [row['date'] for row in result['history']] == [DAY]
    assert result['comparison']['previous_date'] is None
    assert len(market.calls) == 1
    assert market.calls[0][1:] == (PREVIOUS, DAY)
    assert all(row['previous_event_date'] == PREVIOUS for row in result['current']['items'])


def test_prior_event_is_not_borrowed_from_older_day_or_without_prior_quote(monkeypatch):
    market = Market()
    market.ref['pool_history'][DAYS[-3]] = market.container(DAYS[-3], up=[X])
    market.ref['pool_history'][DAYS[-3]]['limitup'][0]['continue_day_cnt'] = 5
    market.ref['pool_history'].pop(PREVIOUS)
    monkeypatch.setattr('app.services.market_broken_boards.resolve_reference', lambda *_: ({}, {}))
    row = next(row for row in build(market, with_memberships=True)['current']['items'] if row['instrument_id'] == X)
    assert (row['previous_state'], row['previous_board_count']) == ('unknown', None)
    market.ref['pool_history'][PREVIOUS] = market.container(PREVIOUS, up=[X])
    market.ref['pool_history'][PREVIOUS]['limitup'][0]['continue_day_cnt'] = 2
    market.history[X] = [row for row in market.history[X] if row['date'] != PREVIOUS]
    row = next(row for row in build(market, with_memberships=True)['current']['items'] if row['instrument_id'] == X)
    assert (row['previous_state'], row['previous_board_count']) == ('unknown', None)
