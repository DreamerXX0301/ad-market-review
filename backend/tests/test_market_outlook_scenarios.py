"""Independent adversarial scenarios for the dated market outlook.

All quotes, pools and counts below are deliberately synthetic. The table is a
behavioral specification: evidence gaps and tomorrow's data must never turn an
observation into a confirmed entry, bottom, or completed next-session trigger.
"""
from copy import deepcopy

import pytest

from app.services import market_outlook as outlook, strategy_limits


CALENDAR = ['2026-01-05', '2026-01-06', '2026-01-07', '2026-01-08',
            '2026-01-09', '2026-01-12', '2026-01-13', '2026-01-14']
DAY, PREVIOUS, FUTURE = CALENDAR[-2], CALENDAR[-3], CALENDAR[-1]
SNAPSHOT = 'synthetic-outlook-scenario-generation'
SCOPE = 'synthetic-fixed-Hu-Shen-universe'
INDICES = ('000001.SH', '399001.SZ', '399006.SZ', '000688.SH')


def reading(up, total=4000, *, day=DAY, **changes):
    return {'date': day, 'up': up, 'down': total - up, 'flat': 0,
            'observed': total, 'total': total, 'expected_current_universe': total,
            'unknown': 0, 'missing': 0, 'scope': SCOPE,
            'universe_expected_known': True, **changes}


def axes(index='unknown', short='unknown'):
    return {'index_environment': {'indices': [
        {'instrument_id': symbol, 'name': symbol, 'date': DAY,
         'state': index, 'state_label': index, 'coverage_pct': 100,
         'metrics': [{'key': 'index_20d_return_pct', 'value': 2 if index == 'strong' else -2},
                     {'key': 'index_ma20_distance_pct', 'value': 1 if index == 'strong' else -1},
                     {'key': 'index_daily_return_pct', 'value': 1}]}
        for symbol in INDICES]},
        'short_term': {'state': short, 'sample_count': 20, 'coverage_pct': 100,
                       'cohort_source_complete': True,
                       'metrics': [{'key': 'previous_limit_up_median_return_pct', 'value': 1},
                                   {'key': 'positive_ratio_pct', 'value': 70}],
                       'groups': [{'id': 'ordinary_10', 'observed_count': 20,
                                   'identity_complete': True, 'cohort_source_complete': True,
                                   'coverage_pct': 100}]},
        'provenance': {'input_fingerprint': 'synthetic-dated-source',
                       'known_time_complete': False}}


def pool(day, symbols, kind):
    return {'date': day, 'pool_kind': kind, 'source': 'synthetic-source',
            'items': [{'instrument_id': symbol, 'ticker': symbol[:6], 'date': day,
                       'last_price': 10} for symbol in symbols],
            'complete': True, 'pagination_complete': True,
            'ambiguous_code_rows': 0, 'raw_codes': [symbol[:6] for symbol in symbols],
            'rejected_codes': []}


class ScenarioMarket:
    """Only already-supplied immutable facts; no provider, SQL, or history I/O."""
    def __init__(self, reference, calendar):
        self._reference, self._calendar = reference, calendar

    def reference(self): return self._reference
    def calendar(self): return self._calendar
    def status(self): return {'snapshot_id': SNAPSHOT, 'data_end': DAY}


@pytest.fixture
def scenario(monkeypatch):
    monkeypatch.setattr(strategy_limits, 'load_supplement', lambda _: {})

    def run(current=None, previous=None, *, history=None, index='unknown', short='unknown',
            up_events=1, down_events=1, calendar=None, mutate=None, **options):
        up_symbols = [f'600{100 + index:03d}.SH' for index in range(up_events)]
        down_symbols = [f'002{100 + index:03d}.SZ' for index in range(down_events)]
        reference = {'_supplement': {
            'annual_pools': {DAY: pool(DAY, up_symbols, 'limitup')},
            'annual_down_pools': {DAY: pool(DAY, down_symbols, 'limitdown')}},
            'indices': {symbol: [{'date': DAY, 'open': 100, 'high': 102, 'low': 98,
                                  'close': 101, 'source': 'synthetic-source', 'price_basis': 'none'}]
                        for symbol in INDICES}}
        rows = [{'instrument_id': symbol, 'date': DAY, 'close': 10, 'volume': 100,
                 'amount': 60, 'price_basis': 'none'} for symbol in up_symbols + down_symbols]
        current = reading(1800) if current is None else deepcopy(current)
        prior = deepcopy(previous)
        data = {'market': ScenarioMarket(reference, list(CALENDAR if calendar is None else calendar)),
                'status': {'snapshot_id': SNAPSHOT, 'data_end': DAY}, 'day': DAY,
                'reference': reference, 'rows': rows, 'breadth': current,
                'emotion': {'model': 'synthetic-legacy-emotion'}, 'sentiment': axes(index, short),
                'history': deepcopy(history) if history is not None else
                           ([prior, current] if prior is not None else [current]),
                'previous_breadth': prior, 'ladder': {}}
        data.update(options)
        if mutate:
            mutate(data)
        return outlook.build_market_outlook(**data)
    return run


@pytest.mark.parametrize('name,current,previous,index,short,ups,downs,expected', [
    ('cold-with-conflicting-strong-indices', reading(300), reading(3500, day=PREVIOUS), 'strong', 'strong', 1, 1, 'ice'),
    ('low-to-high-repair', reading(2800), reading(800, day=PREVIOUS), 'weak', 'strong', 1, 1, 'repair'),
    ('high-to-low-ebb', reading(1600), reading(3200, day=PREVIOUS), 'strong', 'strong', 1, 1, 'ebb'),
    ('verified-event-climax', reading(3600), None, 'strong', 'strong', 80, 5, 'climax'),
    ('aligned-broad-rise', reading(3200), None, 'strong', 'strong', 1, 1, 'broad_rise'),
    ('known-direction-conflict', reading(1200), None, 'strong', 'strong', 1, 1, 'mixed'),
    ('neutral-with-unknown-axes', reading(1800), None, 'unknown', 'unknown', 1, 1, 'divergence'),
    ('coverage-gap-is-not-chaos', reading(3200, expected_current_universe=6000), None, 'weak', 'weak', 80, 5, 'unknown'),
])
def test_all_market_stages_have_distinct_observations_and_untriggered_plans(scenario, name, current, previous, index, short, ups, downs, expected):
    result = scenario(current, previous, index=index, short=short, up_events=ups, down_events=downs)
    assert result['stage']['code'] == expected, name
    assert result['stage']['status'] == ('unknown' if expected == 'unknown' else 'observation')
    assert result['stage']['label'] != '混沌' or expected == 'mixed'
    assert result['plan']['trigger_state'] == 'unobserved'
    assert all(action['trigger_state'] == 'unobserved' and action['triggers'] and action['invalidations']
               for action in result['plan']['actions'])
    assert all(item['trigger_state'] == 'unobserved' for item in result['plan']['scenarios'])
    assert result['provenance']['first_known_at'] is None
    assert result['provenance']['known_time_complete'] is False


@pytest.mark.parametrize('current,previous,events,expected', [
    (reading(449, 3000), None, (1, 1), 'ice'),
    (reading(450, 3000), None, (1, 1), 'ice'),
    (reading(451, 3000), None, (1, 1), 'divergence'),
    (reading(499), None, (1, 1), 'ice'),
    (reading(500), None, (1, 1), 'divergence'),
    (reading(1400), reading(800, day=PREVIOUS), (1, 1), 'divergence'),
    (reading(1401), reading(800, day=PREVIOUS), (1, 1), 'repair'),
    (reading(2000), reading(1400, day=PREVIOUS), (1, 1), 'repair'),
    (reading(2001), reading(1401, day=PREVIOUS), (1, 1), 'divergence'),
    (reading(2999, 5000), reading(3750, 5000, day=PREVIOUS), (1, 1), 'ebb'),
    (reading(3000, 5000), reading(3750, 5000, day=PREVIOUS), (1, 1), 'broad_rise'),
    (reading(1800), reading(2400, day=PREVIOUS), (1, 1), 'ebb'),
    (reading(1799), reading(2399, day=PREVIOUS), (1, 1), 'divergence'),
    (reading(3399), None, (80, 5), 'broad_rise'),
    (reading(3400), None, (80, 5), 'climax'),
    (reading(2999, 3500), None, (80, 5), 'divergence'),
    (reading(3000, 3500), None, (80, 5), 'climax'),
    (reading(2999, 4998), None, (1, 1), 'divergence'),
    (reading(3000, 5000), None, (1, 1), 'broad_rise'),
    (reading(3600), None, (79, 5), 'broad_rise'),
    (reading(3600), None, (80, 5), 'climax'),
    (reading(3600), None, (80, 6), 'broad_rise'),
    (reading(3200, expected_current_universe=5000), None, (1, 1), 'broad_rise'),
    (reading(3200, expected_current_universe=5001), None, (1, 1), 'unknown'),
])
def test_threshold_edges_do_not_silently_change_stage_definitions(scenario, current, previous, events, expected):
    result = scenario(current, previous, up_events=events[0], down_events=events[1])
    assert result['stage']['code'] == expected


@pytest.mark.parametrize('current,previous,events,index,expected', [
    (reading(300), reading(3600, day=PREVIOUS), (80, 5), 'strong', 'ice'),
    (reading(3600), reading(400, day=PREVIOUS), (80, 5), 'weak', 'climax'),
    (reading(3200), reading(400, day=PREVIOUS), (1, 1), 'weak', 'repair'),
    (reading(1600), reading(3600, day=PREVIOUS), (1, 1), 'strong', 'ebb'),
    (reading(3200), None, (1, 1), 'weak', 'broad_rise'),
    (reading(300, expected_current_universe=6000), reading(3600, day=PREVIOUS), (80, 5), 'strong', 'unknown'),
])
def test_stage_priority_survives_simultaneous_conflicting_axes(scenario, current, previous, events, index, expected):
    result = scenario(current, previous, index=index, short='strong', up_events=events[0], down_events=events[1])
    assert result['stage']['code'] == expected
    if expected == 'unknown':
        assert result['stage']['label'] != '混沌'


@pytest.mark.parametrize('change', [{'up': None}, {'down': None}, {'flat': None},
                                   {'up': True}, {'down': -1}, {'up': float('inf')},
                                   {'date': FUTURE}])
def test_unknown_breadth_never_becomes_zero_ice_or_chaos(scenario, change):
    current = reading(300)
    current.update(change)
    result = scenario(current, reading(3600, day=PREVIOUS), index='strong')
    assert result['temperature']['value'] is None
    assert result['stage']['code'] == 'unknown'
    assert result['stage']['label'] == '待核验'
    assert all(action['status'] != 'conditional' for action in result['plan']['actions'])


@pytest.mark.parametrize('mode', ['missing', 'wrong_date', 'history_gap', 'different_scope', 'low_coverage', 'duplicate'])
def test_adjacent_day_gaps_never_fabricate_repair_or_ebb(scenario, mode):
    current, prior = reading(2800), reading(800, day=PREVIOUS)
    history = [deepcopy(prior), current]
    if mode == 'missing': prior = None
    elif mode == 'wrong_date': prior['date'] = CALENDAR[-4]
    elif mode == 'history_gap': history = [current]
    elif mode == 'different_scope': prior['scope'] = 'unrelated-universe'
    elif mode == 'low_coverage': prior['expected_current_universe'] = 6000
    elif mode == 'duplicate': history.append(deepcopy(prior))
    result = scenario(current, prior, history=history)
    assert result['temperature']['previous_value'] is None
    assert result['temperature']['delta'] is None
    assert result['stage']['code'] not in ('repair', 'ebb')


@pytest.mark.parametrize('mode', ['both_history_scopes_differ', 'previous_reading_conflicts', 'current_reading_conflicts'])
def test_inconsistent_history_cannot_be_relabelled_a_comparable_repair(scenario, mode):
    current, previous = reading(2800), reading(400, day=PREVIOUS)
    history = [deepcopy(previous), deepcopy(current)]
    if mode == 'both_history_scopes_differ':
        for item in history: item['scope'] = 'another-retained-universe'
    elif mode == 'previous_reading_conflicts': history[0] = reading(3200, day=PREVIOUS)
    elif mode == 'current_reading_conflicts': history[1] = reading(1800)
    result = scenario(current, previous, history=history, turnover_history=turnover_rows())
    assert result['temperature']['previous_value'] is None
    assert result['temperature']['delta'] is None
    assert result['stage']['code'] != 'repair'


@pytest.mark.parametrize('calendar', [[], CALENDAR[:-2], [*CALENDAR, DAY],
                                     list(reversed(CALENDAR)), [*CALENDAR[:-1], 'invalid-date']])
def test_broken_calendar_does_not_yield_an_executable_cycle(scenario, calendar):
    result = scenario(reading(3200), reading(400, day=PREVIOUS), calendar=calendar, index='strong', short='strong')
    assert result['stage']['code'] == 'unknown'
    assert result['next_session']['date'] is None
    assert all(action['status'] != 'conditional' for action in result['plan']['actions'])


def _watch_row(day=DAY):
    symbol = '600100.SH'
    return {'instrument_id': symbol, 'date': day, 'name': '合成候选', 'close': 10,
            'volume': 100, 'amount': 60, 'board_count': 2, 'limit_up_state': True,
            'topics': [], 'limit_up_evidence': {'state': True, 'evidence': [
                {'state': True, 'status': 'confirmed', 'source': 'synthetic-source',
                 'observed_prices': [10], 'pattern_validation': {
                    'source_identity_valid': True, 'source_dates_valid': True,
                    'instrument_id': symbol, 'trade_date': day}}]}}


def _without_clock(value):
    result = deepcopy(value)
    result['provenance'].pop('computed_at', None)
    return result


@pytest.mark.parametrize('future_kind', ['quotes', 'breadth', 'source_pools', 'members_and_candidates', 'all'])
def test_future_injection_cannot_rewrite_today_stage_candidates_or_plan(scenario, future_kind):
    ladder = {'items': [_watch_row()], 'main_coverage': {'complete': True, 'source_pool_complete': True}}
    before = scenario(reading(3600), index='strong', short='strong', up_events=80, down_events=5,
                      ladder=deepcopy(ladder))
    def inject(data):
        if future_kind in ('quotes', 'all'):
            data['rows'].extend({**row, 'date': FUTURE, 'close': 999999, 'amount': 999999999}
                                for row in list(data['rows']))
        if future_kind in ('breadth', 'all'):
            data['history'].append(reading(4000, day=FUTURE))
        if future_kind in ('source_pools', 'all'):
            data['reference']['_supplement']['annual_pools'][FUTURE] = pool(FUTURE, ['600999.SH'], 'limitup')
            data['reference']['indices'][INDICES[0]].append({'date': FUTURE, 'low': 1, 'close': 999999})
        if future_kind in ('members_and_candidates', 'all'):
            data['ladder']['items'].append(_watch_row(FUTURE))
            data['ladder']['theme_counts'] = [{'topic_id': 'future-theme', 'name': '未来合成题材',
                'kind': 'concept', 'membership_observed_date': FUTURE, 'limit_up_observed_count': 999}]
    after = scenario(reading(3600), index='strong', short='strong', up_events=80, down_events=5,
                     ladder=deepcopy(ladder), mutate=inject)
    assert _without_clock(after) == _without_clock(before)


def turnover_rows(current=120, previous=100):
    return [{'date': day, 'amount': current if day == DAY else previous,
             'scope': 'synthetic-SH-SZ-BJ-turnover', 'source': 'synthetic-source'}
            for day in CALENDAR[:-1]]


def cold_history(count, current):
    previous_days = CALENDAR[-7:-2]
    return [reading(400 if index >= len(previous_days) - count else 2000, day=day)
            for index, day in enumerate(previous_days)] + [current]


@pytest.mark.parametrize('cold_days', [1, 2, 3, 4, 5])
def test_ice_streaks_remain_blocked_despite_strong_axes_and_large_turnover(scenario, cold_days):
    current = reading(400)
    history = cold_history(cold_days, current)
    result = scenario(current, history[-2], history=history, index='strong', short='strong',
                      turnover_history=turnover_rows(previous=10))
    assert result['stage']['code'] == 'ice'
    assert result['bottom_watch']['prior_ice_streak'] == cold_days
    assert result['bottom_watch']['confirmed'] is False
    assert result['bottom_watch']['status'] != 'candidate'
    assert result['entry_gate']['status'] == 'blocked'
    actions = {item['id']: item['status'] for item in result['plan']['actions']}
    assert actions == {'open': 'blocked', 'add': 'avoid', 'bottom': 'avoid'}


def test_ice_history_keeps_the_missing_session_instead_of_splicing_cold_days(scenario):
    current = reading(3200)
    complete = cold_history(3, current)
    gap_day = CALENDAR[-4]
    history = [item for item in complete if item['date'] != gap_day]
    result = scenario(current, history[-2], history=history,
                      turnover_history=turnover_rows(), index='weak', short='strong')
    bottom = result['bottom_watch']
    assert bottom['prior_ice_streak'] == 1
    assert bottom['prior_ice_streak_complete'] is False
    assert bottom['prior_ice_streak_at_least'] is True
    missing = next(item for item in bottom['observations'] if item['date'] == gap_day)
    assert missing['state'] == 'unknown' and missing['is_ice'] is None
    assert bottom['recent_ice_count_complete'] is False
    assert bottom['recent_ice_count'] == 2, 'only two actually observed cold sessions survive the gap'


@pytest.mark.parametrize('current,expected_expanded,expected_gate', [
    (119.999, False, 'blocked'), (120, True, 'conditional'), (120.001, True, 'conditional'),
])
def test_turnover_twenty_percent_boundary_controls_repair_release_exactly(scenario, current, expected_expanded, expected_gate):
    def quote_amounts(data):
        for row in data['rows']:
            row['amount'] = current / len(data['rows'])
    result = scenario(reading(3200), reading(400, day=PREVIOUS),
                      index='weak', short='strong', turnover_history=turnover_rows(current=current), mutate=quote_amounts)
    assert result['stage']['code'] == 'repair'
    assert result['turnover']['expanded'] is expected_expanded
    assert result['entry_gate']['status'] == expected_gate
    assert result['bottom_watch']['confirmed'] is False
    assert result['bottom_watch']['status'] == ('candidate' if expected_expanded else 'unconfirmed')
    assert result['plan']['actions'][0]['status'] == expected_gate


@pytest.mark.parametrize('mode', ['missing_previous', 'duplicate_previous', 'wrong_scope', 'wrong_source',
                                  'zero_previous', 'unknown_current', 'future_only', 'invalid_amount'])
def test_unverified_turnover_never_releases_a_repair_entry(scenario, mode):
    history = turnover_rows()
    if mode == 'missing_previous': history = [item for item in history if item['date'] != PREVIOUS]
    elif mode == 'duplicate_previous': history.append(deepcopy(history[-2]))
    elif mode == 'wrong_scope': history[-2]['scope'] = 'unrelated-market'
    elif mode == 'wrong_source': history[-2]['source'] = 'unrelated-source'
    elif mode == 'zero_previous': history[-2]['amount'] = 0
    elif mode == 'unknown_current': history[-1]['amount'] = None
    elif mode == 'future_only': history = [{'date': FUTURE, 'amount': 999999, 'scope': 'synthetic-SH-SZ-BJ-turnover', 'source': 'synthetic-source'}]
    elif mode == 'invalid_amount': history[-2]['amount'] = float('nan')
    result = scenario(reading(3200), reading(400, day=PREVIOUS),
                      index='strong', short='strong', turnover_history=history)
    assert result['stage']['code'] == 'repair'
    assert result['turnover']['state'] == 'unknown'
    assert result['turnover']['expanded'] is None
    assert result['bottom_watch']['status'] == 'unknown'
    assert result['entry_gate']['status'] == 'blocked'
    assert result['plan']['actions'][0]['status'] == 'blocked'


@pytest.mark.parametrize('up,events,stage', [(3200, (1, 1), 'broad_rise'), (3600, (80, 5), 'climax')])
@pytest.mark.parametrize('mode', ['no_history', 'different_scope', 'unknown_quote_amount'])
def test_aligned_hot_markets_still_cannot_bypass_unknown_turnover(scenario, up, events, stage, mode):
    turnover = turnover_rows(current=60 * sum(events))
    if mode == 'no_history': turnover = []
    elif mode == 'different_scope': turnover[-2]['scope'] = 'unrelated-scope'
    def damage_amount(data):
        if mode == 'unknown_quote_amount': data['rows'][0]['amount'] = None
    result = scenario(reading(up), index='strong', short='strong',
                      up_events=events[0], down_events=events[1],
                      turnover_history=turnover, mutate=damage_amount)
    assert result['stage']['code'] == stage
    assert result['turnover']['state'] == 'unknown'
    assert result['entry_gate']['status'] == 'blocked'
    assert result['plan']['stance'] == 'blocked'
    assert result['plan']['actions'][0]['status'] == 'blocked'


@pytest.mark.parametrize('mode', ['duplicate_index', 'foreign_index', 'future_index_date', 'duplicate_ordinary_group'])
def test_ambiguous_axis_identity_cannot_masquerade_as_a_four_index_entry_alignment(scenario, mode):
    def damage_axis(data):
        if mode == 'duplicate_index':
            data['sentiment']['index_environment']['indices'][1] = deepcopy(data['sentiment']['index_environment']['indices'][0])
        elif mode == 'foreign_index':
            data['sentiment']['index_environment']['indices'][1]['instrument_id'] = '600100.SH'
        elif mode == 'future_index_date':
            data['sentiment']['index_environment']['indices'][1]['date'] = FUTURE
        elif mode == 'duplicate_ordinary_group':
            data['sentiment']['short_term']['groups'].append({
                'id': 'ordinary_10', 'observed_count': 20, 'coverage_pct': 0,
                'identity_complete': False, 'cohort_source_complete': False})
    result = scenario(reading(3200), index='strong', short='strong',
                      turnover_history=turnover_rows(), mutate=damage_axis)
    assert result['stage']['code'] == 'broad_rise'
    by_id = {item['id']: item for item in result['axes']}
    if mode == 'duplicate_ordinary_group':
        assert by_id['short_term']['ordinary_group_ready'] is False
        routes = {route['id']: route for route in result['entry_gate']['routes']}
        assert routes['trend']['status'] == 'conditional'
        assert routes['relay']['status'] == 'wait'
        return
    else:
        assert by_id['index']['state'] == 'unknown'
    assert result['entry_gate']['status'] != 'conditional'
    assert result['plan']['actions'][0]['status'] != 'conditional'


def test_one_repair_day_is_only_a_candidate_and_low_is_not_a_stop_loss_commitment(scenario):
    result = scenario(reading(3200), reading(400, day=PREVIOUS),
                      index='weak', short='strong', turnover_history=turnover_rows())
    bottom = result['bottom_watch']
    assert bottom['status'] == 'candidate'
    assert bottom['candidate_date'] == DAY and bottom['confirmed'] is False
    assert len(bottom['index_references']) == 4
    assert {item['low'] for item in bottom['index_references']} == {98}
    assert all(item['date'] == DAY and item['price_basis'] == 'none'
               and '风险参照' in item['role'] and '不是已确认支撑位' in item['role']
               for item in bottom['index_references'])
    assert bottom['trigger_state'] == result['entry_gate']['trigger_state'] == 'unobserved'
    assert bottom['confirmation_conditions'] and bottom['invalidations']
    assert {item['id']: item['status'] for item in result['plan']['actions']} == {
        'open': 'conditional', 'add': 'wait', 'bottom': 'wait'}
    assert 'stop_loss' not in bottom and 'automatic_order' not in bottom


@pytest.mark.parametrize('mode', ['missing', 'duplicate', 'future_low', 'negative_low', 'above_close', 'adjusted',
                                  'wrong_instrument_id', 'conflicting_thscode', 'conflicting_trade_date'])
def test_bottom_candidate_requires_all_four_actual_unadjusted_index_low_references(scenario, mode):
    def damage_reference(data):
        quotes = data['reference']['indices'][INDICES[0]]
        if mode == 'missing': quotes.clear()
        elif mode == 'duplicate': quotes.append(deepcopy(quotes[0]))
        elif mode == 'future_low': quotes[0]['date'] = FUTURE
        elif mode == 'negative_low': quotes[0]['low'] = -1
        elif mode == 'above_close': quotes[0]['low'] = 102
        elif mode == 'adjusted': quotes[0]['price_basis'] = 'forward'
        elif mode == 'wrong_instrument_id': quotes[0]['instrument_id'] = '600100.SH'
        elif mode == 'conflicting_thscode': quotes[0].update(instrument_id=INDICES[0], thscode='399001.SZ')
        elif mode == 'conflicting_trade_date': quotes[0]['trade_date'] = FUTURE
    result = scenario(reading(3200), reading(400, day=PREVIOUS),
                      turnover_history=turnover_rows(), mutate=damage_reference)
    assert result['stage']['code'] == 'repair'
    assert result['turnover']['expanded'] is True
    assert result['bottom_watch']['index_references_complete'] is False
    assert result['bottom_watch']['status'] == 'unknown'
    assert result['entry_gate']['status'] == 'blocked'


def test_repair_failure_returns_to_defensive_ebb_without_remembering_a_bottom(scenario):
    current, previous = reading(1400), reading(3200, day=PREVIOUS)
    history = [reading(400, day=CALENDAR[-4]), previous, current]
    result = scenario(current, previous, history=history, index='strong', short='strong',
                      turnover_history=turnover_rows(previous=50))
    assert result['stage']['code'] == 'ebb'
    assert result['turnover']['expanded'] is True
    assert result['bottom_watch']['prior_ice_streak'] == 0
    assert result['bottom_watch']['status'] != 'candidate'
    assert result['bottom_watch']['candidate_date'] is None
    assert result['bottom_watch']['confirmed'] is False
    assert result['entry_gate']['status'] == 'blocked'
    assert result['plan']['actions'][0]['status'] == 'blocked'
    assert result['plan']['actions'][1]['status'] == 'avoid'


def test_future_prices_and_turnover_cannot_confirm_a_single_day_bottom_early(scenario):
    turnover = turnover_rows()
    before = scenario(reading(3200), reading(400, day=PREVIOUS),
                      turnover_history=turnover, index='weak', short='strong')
    def inject_future(data):
        data['turnover_history'].append({'date': FUTURE, 'amount': 999999999,
            'scope': 'synthetic-SH-SZ-BJ-turnover', 'source': 'synthetic-source'})
        data['history'].append(reading(4000, day=FUTURE))
        for quotes in data['reference']['indices'].values():
            quotes.append({'date': FUTURE, 'low': 1000, 'close': 999999,
                           'price_basis': 'none', 'source': 'synthetic-source'})
    after = scenario(reading(3200), reading(400, day=PREVIOUS),
                     turnover_history=deepcopy(turnover), index='weak', short='strong', mutate=inject_future)
    assert before['bottom_watch']['status'] == after['bottom_watch']['status'] == 'candidate'
    assert after['bottom_watch']['confirmed'] is False
    assert _without_clock(before) == _without_clock(after)


@pytest.mark.parametrize('mode', ['missing', 'duplicate', 'invalid_amount', 'different_scope'])
def test_five_day_turnover_context_keeps_its_strict_window_and_does_not_repair_gaps(scenario, mode):
    history = turnover_rows()
    # This older observation is outside the exact prior-five-session window;
    # it must never be substituted for a missing member of that window.
    history[0]['amount'] = 999999
    target = CALENDAR[-5]
    if mode == 'missing': history = [item for item in history if item['date'] != target]
    elif mode == 'duplicate': history.append(deepcopy(next(item for item in history if item['date'] == target)))
    elif mode == 'invalid_amount': next(item for item in history if item['date'] == target)['amount'] = None
    elif mode == 'different_scope': next(item for item in history if item['date'] == target)['scope'] = 'other-scope'
    result = scenario(reading(3200), reading(400, day=PREVIOUS), turnover_history=history)
    assert result['turnover']['expanded'] is True, 'the separate exact T/P comparison remains available'
    assert result['turnover']['previous_5d_mean_complete'] is False
    assert result['turnover']['previous_5d_mean'] is None
    assert result['turnover']['vs_previous_5d_mean_pct'] is None


@pytest.mark.parametrize('up,observed,upper_bound,expected_stage,expected_basis', [
    (30, 40, None, 'unknown', 'unbounded_observed_sample'),
    (30, 40, 5176, 'unknown', 'retained_master_upper_bound'),
    (4000, 5124, 5176, 'broad_rise', 'retained_master_upper_bound'),
])
def test_unknown_expected_universe_needs_a_real_bound_before_sample_observation_is_actionable(
        scenario, up, observed, upper_bound, expected_stage, expected_basis):
    current = reading(up, observed, expected_current_universe=None,
                      universe_expected_known=False, missing=None)
    result = scenario(current, index='strong', short='strong',
                      turnover_history=turnover_rows(), universe_upper_bound=upper_bound)
    temperature = result['temperature']
    assert temperature['value'] == pytest.approx(up / observed * 100), 'the observed sample temperature remains visible'
    assert temperature['universe_expected_known'] is False, 'a conservative upper bound never invents a complete historical universe'
    assert temperature['coverage_basis'] == expected_basis
    assert result['stage']['code'] == expected_stage
    if upper_bound is None:
        assert temperature['coverage_pct'] is None
        assert temperature['universe_bound_known'] is False
    else:
        assert temperature['coverage_pct'] == pytest.approx(observed / upper_bound * 100)
        assert temperature['universe_upper_bound'] == upper_bound
        assert temperature['universe_bound_known'] is True
    if expected_stage == 'unknown':
        assert result['entry_gate']['status'] == 'blocked'
        assert result['plan']['actions'][0]['status'] == 'blocked'
    else:
        assert result['stage']['status'] == 'observation'
        assert result['entry_gate']['status'] == 'conditional'
