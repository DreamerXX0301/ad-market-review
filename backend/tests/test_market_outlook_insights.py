"""Independent opportunity-route and bounded candidate replay specifications.

Every quote below is synthetic. No running application, provider, SQLite or
DuckDB is opened by these tests.
"""
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import socket

import pytest

from app.services import market_ladder, market_outlook, market_outlook_insights as insights, market_sentiment
from test_market_outlook_scenarios import (
    DAY, FUTURE, INDICES, PREVIOUS, _without_clock,
    reading, scenario, turnover_rows,
)


def route_map(result):
    return {row['id']: row for row in result['insights']['routes']}


@pytest.mark.parametrize('mode', ['low_coverage', 'identity_unknown', 'duplicate_group', 'short_unknown'])
def test_trend_review_does_not_borrow_or_require_ordinary_group_promotion(scenario, mode):
    def damage_relay(data):
        short = data['sentiment']['short_term']
        group = short['groups'][0]
        if mode == 'low_coverage': group['coverage_pct'] = 36.67
        elif mode == 'identity_unknown': group['identity_complete'] = False
        elif mode == 'duplicate_group': short['groups'].append(deepcopy(group))
        else: short['cohort_source_complete'] = False

    result = scenario(reading(3200), index='strong', short='strong',
                      turnover_history=turnover_rows(), mutate=damage_relay)
    routes = route_map(result)
    assert result['stage']['code'] == 'broad_rise'
    assert routes['trend']['status'] == 'conditional'
    assert routes['relay']['status'] != 'conditional'
    assert routes['relay']['missing_conditions']
    assert result['entry_gate']['status'] == 'conditional'
    assert result['entry_gate']['routes'] == result['insights']['routes']
    assert {row['id']: row['status'] for row in result['plan']['actions']} == {
        'open': 'conditional', 'add': 'conditional', 'bottom': 'avoid'}
    assert all(row['trigger_state'] == 'unobserved' for row in routes.values())
    assert result['insights']['headline']['execution_state'] == 'unobserved'


@pytest.mark.parametrize('mode', ['ice', 'ebb', 'unknown_breadth', 'unknown_turnover'])
def test_global_hard_blocks_cannot_be_bypassed_by_a_strong_trend_route(scenario, mode):
    current, previous, amounts = reading(3200), None, turnover_rows()
    if mode == 'ice': current = reading(400)
    elif mode == 'ebb': current, previous = reading(1600), reading(3200, day=PREVIOUS)
    elif mode == 'unknown_breadth': current = reading(3200, expected_current_universe=6000)
    else: amounts = []
    result = scenario(current, previous, index='strong', short='strong', turnover_history=amounts)
    assert result['entry_gate']['status'] == 'blocked'
    assert result['plan']['actions'][0]['status'] == 'blocked'
    assert all(row['status'] != 'conditional' for row in route_map(result).values())


def test_ice_volume_repair_is_visible_without_becoming_a_confirmed_bottom(scenario):
    result = scenario(reading(3200), reading(400, day=PREVIOUS),
                      index='weak', short='unknown', turnover_history=turnover_rows())
    routes = route_map(result)
    assert result['stage']['code'] == 'repair'
    assert result['bottom_watch']['status'] == 'candidate'
    assert result['bottom_watch']['confirmed'] is False
    assert routes['repair']['status'] == 'conditional'
    assert routes['trend']['status'] != 'conditional'
    assert routes['relay']['status'] != 'conditional'
    assert result['insights']['candidate_tracking']['status'] == 'candidate'
    assert result['insights']['candidate_tracking']['candidate_date'] == DAY
    assert {row['id']: row['status'] for row in result['plan']['actions']} == {
        'open': 'conditional', 'add': 'wait', 'bottom': 'wait'}
    assert result['insights']['opportunities']
    assert result['insights']['risks']


def test_repair_trend_route_with_unchanged_turnover_has_path_specific_release_text(scenario):
    result = scenario(reading(3200), reading(400, day=PREVIOUS),
                      index='strong', short='unknown',
                      turnover_history=turnover_rows(current=120, previous=120))
    assert result['stage']['code'] == 'repair'
    assert result['turnover']['change_pct'] == 0
    assert result['turnover']['expanded'] is False
    assert result['bottom_watch']['status'] != 'candidate'
    assert route_map(result)['trend']['status'] == 'conditional'
    assert result['entry_gate']['status'] == 'conditional'
    releases = result['entry_gate']['release_conditions']
    trend = next(line for line in releases if line.startswith('趋势路径：'))
    assert '不要求' in trend and '20%' in trend
    assert '前日冰点' not in trend
    positive_expansion_requirements = [line for line in releases if '20%' in line and '不要求' not in line]
    assert positive_expansion_requirements
    assert all(line.startswith('冰点修复路径：') for line in positive_expansion_requirements)
    assert all(line.startswith('冰点修复路径：') for line in releases if '前日冰点' in line)


def test_conflicting_today_breadth_cannot_create_a_candidate_from_history_alone(scenario):
    previous = reading(400, day=PREVIOUS)
    result = scenario(reading(3200), previous, history=[previous, reading(2800)],
                      index='strong', short='strong', turnover_history=turnover_rows())
    assert result['temperature']['evidence_consistent'] is False
    assert result['stage']['code'] == 'unknown'
    assert result['entry_gate']['status'] == 'blocked'
    tracking = result['insights']['candidate_tracking']
    assert tracking['status'] == 'unknown'
    assert tracking['candidate_date'] is None
    assert not any(row['date'] == DAY and row['state'] == 'candidate' for row in tracking['observations'])


def test_future_inputs_cannot_change_observed_insights_or_today_execution(scenario):
    amounts = turnover_rows()
    before = scenario(reading(3200), reading(400, day=PREVIOUS),
                      index='weak', short='strong', turnover_history=deepcopy(amounts))

    def inject_future(data):
        data['history'].append(reading(0, day=FUTURE))
        data['turnover_history'].append({**amounts[-1], 'date': FUTURE, 'amount': 999999})
        data['rows'].extend({**row, 'date': FUTURE, 'close': 1} for row in list(data['rows']))
        for quotes in data['reference']['indices'].values():
            quotes.append({'date': FUTURE, 'close': 1, 'low': .5,
                           'source': 'synthetic-source', 'price_basis': 'none'})

    after = scenario(reading(3200), reading(400, day=PREVIOUS),
                     index='weak', short='strong', turnover_history=deepcopy(amounts), mutate=inject_future)
    assert _without_clock(before) == _without_clock(after)


def test_build_reuses_one_turnover_read_without_panel_quote_or_network_io(scenario, monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        raise AssertionError('An already-read outlook must not perform extra panel, price, sentiment or network I/O')

    def bind_market(data):
        def turnover_history(day):
            calls.append(day)
            return deepcopy(turnover_rows())
        monkeypatch.setattr(data['market'], 'turnover_history', turnover_history, raising=False)
        monkeypatch.setattr(data['market'], 'panel', forbidden, raising=False)
        monkeypatch.setattr(data['market'], 'history_for_instruments', forbidden, raising=False)

    monkeypatch.setattr(market_sentiment, 'describe_sentiment', forbidden)
    monkeypatch.setattr(market_ladder, 'build_ladder', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    result = scenario(reading(3200), reading(400, day=PREVIOUS), index='weak', short='strong',
                      turnover_history=None, ladder={}, mutate=bind_market)
    assert calls == [DAY]
    assert result['turnover']['expanded'] is True
    assert result['bottom_watch']['status'] == 'candidate'
    assert result['insights']['candidate_tracking']['status'] == 'candidate'


def replay_inputs(*, candidate_index=21, end_index=24):
    """Thirty explicit synthetic sessions, with one isolated ice/repair pair."""
    first = date(2025, 11, 3)
    calendar = [(first + timedelta(days=index)).isoformat() for index in range(45)
                if (first + timedelta(days=index)).weekday() < 5][:30]
    candidate = calendar[candidate_index]
    history = [reading(2200, day=day) for day in calendar]
    history[candidate_index - 1] = reading(400, day=calendar[candidate_index - 1])
    history[candidate_index] = reading(2800, day=candidate)
    for index in range(candidate_index + 1, len(calendar)):
        history[index] = reading(2800, day=calendar[index])
    amounts = [{'date': day, 'amount': 120 if index >= candidate_index else 100,
                'scope': 'synthetic-SH-SZ-BJ-turnover', 'source': 'synthetic-source'}
               for index, day in enumerate(calendar)]
    reference = {'indices': {symbol: [
        {'date': day, 'instrument_id': symbol, 'low': 98, 'close': 101,
         'price_basis': 'none', 'source': 'synthetic-source'} for day in calendar]
        for symbol in INDICES}}
    return {'day': calendar[end_index], 'calendar': calendar, 'history': history,
            'turnover_history': amounts, 'reference': reference}, candidate


def test_candidate_replay_distinguishes_candidate_and_later_continuation():
    options, candidate = replay_inputs()
    at_candidate = insights.candidate_tracking(**{**options, 'day': candidate})
    after = insights.candidate_tracking(**options)
    assert at_candidate['status'] == 'candidate'
    assert after['status'] == 'continuing'
    assert after['candidate_date'] == candidate
    assert after['last_observed_date'] == options['day']
    assert after['confirmed'] is False
    assert after['as_of'] == options['day']
    assert len(after['index_references']) == 4
    assert {row['date'] for row in after['index_references']} == {candidate}
    assert all(row['date'] <= options['day'] for row in after['observations'])
    assert all('executed' not in row for row in after['observations'])


def test_high_heat_priority_does_not_hide_the_same_ice_volume_repair_evidence():
    options, candidate = replay_inputs(end_index=21)
    options['history'] = [reading(3600, day=candidate) if row['date'] == candidate else row
                          for row in options['history']]
    result = insights.candidate_tracking(**options)
    assert result['status'] == 'candidate'
    assert result['candidate_date'] == candidate
    assert result['confirmed'] is False


@pytest.mark.parametrize('amount,expected', [(119.999, False), (120, True)])
def test_replayed_repair_uses_the_same_exact_twenty_percent_volume_boundary(amount, expected):
    options, candidate = replay_inputs(end_index=21)
    for row in options['turnover_history']:
        if row['date'] == candidate: row['amount'] = amount
    result = insights.candidate_tracking(**options)
    assert (result['status'] == 'candidate') is expected
    assert result['confirmed'] is False


@pytest.mark.parametrize('mode,expected', [('weak_breadth', 'diverging'),
                                          ('broken_low', 'failed'),
                                          ('missing_index', 'unknown'),
                                          ('missing_breadth', 'unknown')])
def test_later_candidate_observations_never_default_to_valid(mode, expected):
    options, candidate = replay_inputs()
    day = options['day']
    if mode == 'weak_breadth':
        options['history'] = [reading(1800, day=day) if row['date'] == day else row
                              for row in options['history']]
    elif mode == 'broken_low':
        for row in options['reference']['indices'][INDICES[0]]:
            if row['date'] == day: row.update(close=97, low=96)
    elif mode == 'missing_index':
        options['reference']['indices'][INDICES[0]] = [
            row for row in options['reference']['indices'][INDICES[0]] if row['date'] != day]
    else:
        options['history'] = [row for row in options['history'] if row['date'] != day]
    result = insights.candidate_tracking(**options)
    assert result['candidate_date'] == candidate
    assert result['status'] == expected
    assert result['confirmed'] is False
    assert result['conditions']
    if mode == 'broken_low':
        assert INDICES[0] in result['observations'][-1]['broken_indices']
    if expected == 'unknown':
        assert result['last_observed_date'] < day


def test_failed_candidate_does_not_become_continuing_when_price_recovers():
    options, candidate = replay_inputs()
    broken = options['calendar'][22]
    for row in options['reference']['indices'][INDICES[0]]:
        if row['date'] == broken: row.update(close=97, low=96)
    result = insights.candidate_tracking(**options)
    assert result['candidate_date'] == candidate
    assert result['status'] == 'failed'
    assert result['confirmed'] is False


@pytest.mark.parametrize('gap_kind', ['index', 'breadth', 'index_source'])
def test_recovered_quotes_cannot_bridge_an_unobserved_candidate_session(gap_kind):
    options, candidate = replay_inputs()
    gap = options['calendar'][22]
    if gap_kind == 'breadth':
        options['history'] = [row for row in options['history'] if row['date'] != gap]
    else:
        quotes = options['reference']['indices'][INDICES[0]]
        if gap_kind == 'index':
            options['reference']['indices'][INDICES[0]] = [row for row in quotes if row['date'] != gap]
        else:
            next(row for row in quotes if row['date'] == gap)['source'] = 'unverified-different-source'
    result = insights.candidate_tracking(**options)
    assert result['candidate_date'] == candidate
    assert result['status'] == 'unknown'
    assert result['last_observed_date'] == candidate
    states = {row['date']: row['state'] for row in result['observations']}
    assert states[gap] == states[options['calendar'][23]] == states[options['day']] == 'unknown'
    assert result['confirmed'] is False


def test_a_new_independent_candidate_resets_a_preceding_evidence_gap():
    options, _ = replay_inputs(end_index=25)
    calendar = options['calendar']
    gap, cold, new_candidate = calendar[22], calendar[23], calendar[24]
    options['reference']['indices'][INDICES[0]] = [
        row for row in options['reference']['indices'][INDICES[0]] if row['date'] != gap]
    options['history'] = [reading(400, day=cold) if row['date'] == cold else row
                          for row in options['history']]
    for row in options['turnover_history']:
        if row['date'] == new_candidate: row['amount'] = 144
    at_new_candidate = insights.candidate_tracking(**{**options, 'day': new_candidate})
    assert at_new_candidate['status'] == 'candidate'
    assert at_new_candidate['candidate_date'] == new_candidate
    after = insights.candidate_tracking(**options)
    assert after['status'] == 'continuing'
    assert after['candidate_date'] == new_candidate
    assert after['last_observed_date'] == calendar[25]
    assert [row['date'] for row in after['observations']] == calendar[24:26]
    assert after['confirmed'] is False


@pytest.mark.parametrize('earlier_gap', [False, True])
def test_confirmed_index_break_is_not_hidden_by_a_different_missing_index(earlier_gap):
    options, candidate = replay_inputs()
    day = options['day']
    for row in options['reference']['indices'][INDICES[0]]:
        if row['date'] == day: row.update(close=97, low=96)
    options['reference']['indices'][INDICES[1]] = [
        row for row in options['reference']['indices'][INDICES[1]] if row['date'] != day]
    if earlier_gap:
        gap = options['calendar'][22]
        options['history'] = [row for row in options['history'] if row['date'] != gap]
    result = insights.candidate_tracking(**options)
    assert result['candidate_date'] == candidate
    assert result['status'] == 'failed'
    assert result['last_observed_date'] == day
    assert result['observations'][-1]['broken_indices'] == [INDICES[0]]
    assert result['history_complete'] is False
    assert result['confirmed'] is False


@pytest.mark.parametrize('mode', ['duplicate_index', 'duplicate_breadth', 'changed_source',
                                  'missing_source', 'adjusted_basis'])
def test_duplicate_source_and_price_basis_gaps_do_not_preserve_a_valid_candidate(mode):
    options, candidate = replay_inputs()
    day = options['day']
    quotes = options['reference']['indices'][INDICES[0]]
    current = next(row for row in quotes if row['date'] == day)
    if mode == 'duplicate_index': quotes.append(deepcopy(current))
    elif mode == 'duplicate_breadth':
        options['history'].append(deepcopy(next(row for row in options['history'] if row['date'] == day)))
    elif mode == 'changed_source': current['source'] = 'another-synthetic-source'
    elif mode == 'missing_source': current.pop('source')
    else: current['price_basis'] = 'forward'
    result = insights.candidate_tracking(**options)
    assert result['candidate_date'] == candidate
    assert result['status'] == 'unknown'
    assert result['last_observed_date'] < day
    assert result['confirmed'] is False


def test_today_breadth_source_change_preserves_the_original_candidate_but_marks_unknown():
    options, candidate = replay_inputs()
    for row in options['history']:
        row['source'] = 'synthetic-original-breadth-source'
    before = insights.candidate_tracking(**options)
    assert before['candidate_date'] == candidate
    assert before['status'] == 'continuing'
    current = next(row for row in options['history'] if row['date'] == options['day'])
    current['source'] = 'synthetic-replacement-breadth-source'
    after = insights.candidate_tracking(**options)
    assert after['candidate_date'] == candidate
    assert after['status'] == 'unknown'
    assert after['last_observed_date'] == options['calendar'][23]
    assert after['observations'][0]['date'] == candidate
    assert after['observations'][0]['state'] == 'candidate'
    assert after['history_complete'] is False
    assert after['confirmed'] is False


@pytest.mark.parametrize('mode', ['duplicate_amount', 'amount_source_change', 'amount_scope_change',
                                  'duplicate_candidate_index', 'candidate_adjusted_basis'])
def test_candidate_creation_rejects_incomplete_comparison_or_risk_references(mode):
    options, candidate = replay_inputs(end_index=21)
    amount = next(row for row in options['turnover_history'] if row['date'] == candidate)
    quotes = options['reference']['indices'][INDICES[0]]
    current = next(row for row in quotes if row['date'] == candidate)
    if mode == 'duplicate_amount': options['turnover_history'].append(deepcopy(amount))
    elif mode == 'amount_source_change': amount['source'] = 'another-synthetic-source'
    elif mode == 'amount_scope_change': amount['scope'] = 'another-synthetic-universe'
    elif mode == 'duplicate_candidate_index': quotes.append(deepcopy(current))
    else: current['price_basis'] = 'forward'
    result = insights.candidate_tracking(**options)
    assert result['status'] != 'candidate'
    assert result['candidate_date'] is None
    assert result['confirmed'] is False


def test_replay_is_bounded_and_cannot_resurrect_a_candidate_before_the_window():
    options, _ = replay_inputs(candidate_index=4, end_index=29)
    result = insights.candidate_tracking(**options)
    assert result['lookback_sessions'] == 20
    assert result['window_end'] == options['day']
    assert result['window_begin'] == options['calendar'][10]
    assert result['status'] == 'none'
    assert result['candidate_date'] is None
    assert len(result['observations']) <= 20
    assert all(result['window_begin'] <= row['date'] <= result['window_end']
               for row in result['observations'])


@pytest.mark.parametrize('missing', ['breadth', 'amount'])
def test_missing_predecessor_of_the_twenty_day_window_cannot_prove_no_candidate(missing):
    options, _ = replay_inputs(candidate_index=4, end_index=29)
    before = insights.candidate_tracking(**options)
    assert before['status'] == 'none'
    assert before['history_complete'] is True
    predecessor = options['calendar'][9]
    key = 'history' if missing == 'breadth' else 'turnover_history'
    options[key] = [row for row in options[key] if row['date'] != predecessor]
    after = insights.candidate_tracking(**options)
    assert after['window_begin'] == options['calendar'][10]
    assert after['status'] == 'unknown'
    assert after['history_complete'] is False
    assert after['candidate_date'] is None
    assert after['observations'] == []


def test_large_retained_series_are_indexed_once_and_only_twenty_one_sessions_are_replayed(monkeypatch):
    options, _ = replay_inputs(end_index=29)
    expected = insights.candidate_tracking(**options)
    calendar = options['calendar']
    predecessor, end = calendar[9], options['day']

    class DateOnlyOutsideWindow(dict):
        """Outside the window even OHLC/amount/source fields must be ignored."""
        def get(self, key, default=None):
            if key != 'date':
                raise AssertionError('Out-of-window row was used for a predicate')
            return super().get(key, default)

    class OnceIterable(list):
        def __init__(self, rows):
            super().__init__(rows)
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            assert self.iterations == 1, 'Repeated scans of a full retained series are forbidden'
            return super().__iter__()

    first_old = date(2000, 1, 1)
    outside = [DateOnlyOutsideWindow(date=(first_old + timedelta(days=index)).isoformat(),
                                   source='outside-window-synthetic-marker') for index in range(4000)]
    outside.append(DateOnlyOutsideWindow(date='2099-01-01', source='future-synthetic-marker'))
    options['history'] = OnceIterable([*outside, *options['history']])
    options['turnover_history'] = OnceIterable([*outside, *options['turnover_history']])
    for symbol, rows in options['reference']['indices'].items():
        options['reference']['indices'][symbol] = OnceIterable([*outside, *rows])
    original = market_outlook._index_references
    replayed = []

    def bounded_references(reference, target):
        replayed.append(target)
        for rows in reference['indices'].values():
            assert len(rows) <= 21
            assert all(predecessor <= row['date'] <= end for row in rows)
        return original(reference, target)

    monkeypatch.setattr(market_outlook, '_index_references', bounded_references)
    result = insights.candidate_tracking(**options)
    assert result == expected
    assert replayed == calendar[9:30]
    assert options['history'].iterations == options['turnover_history'].iterations == 1
    assert all(rows.iterations == 1 for rows in options['reference']['indices'].values())
    assert 'outside-window-synthetic-marker' not in json.dumps(result)
    assert 'future-synthetic-marker' not in json.dumps(result)


def test_replay_ignores_future_breadth_amount_and_index_rows():
    options, _ = replay_inputs()
    before = insights.candidate_tracking(**options)
    later = deepcopy(options)
    day = later['day']
    for row in later['history']:
        if row['date'] > day: row.update(up=0, down=4000)
    for row in later['turnover_history']:
        if row['date'] > day: row['amount'] = 999999999
    for rows in later['reference']['indices'].values():
        for row in rows:
            if row['date'] > day: row.update(close=.1, low=.01)
    assert insights.candidate_tracking(**later) == before


def test_twenty_dates_without_a_calendar_predecessor_cannot_prove_no_candidate():
    options, _ = replay_inputs(candidate_index=10, end_index=29)
    options['calendar'] = options['calendar'][10:]
    result = insights.candidate_tracking(**options)
    assert result['status'] == 'unknown'
    assert result['history_complete'] is False
    assert result['candidate_date'] is None


def test_equivalent_retained_breadth_labels_keep_current_and_replayed_candidate_consistent(scenario):
    current = reading(3200, scope='沪深已留存可比A股样本', source='synthetic-source')
    previous = reading(400, day=PREVIOUS, scope=current['scope'], source=current['source'])
    history = [{**previous, 'scope': '沪深已留存可比标的；除权未知剔除'}, current]
    result = scenario(current, previous, history=history, index='weak', short='unknown',
                      turnover_history=turnover_rows())
    assert result['stage']['code'] == 'repair'
    assert result['bottom_watch']['status'] == 'candidate'
    assert result['insights']['candidate_tracking']['status'] == 'candidate'


def research_module():
    path = Path(__file__).resolve().parents[2] / 'scripts' / 'research_market_outlook.py'
    spec = importlib.util.spec_from_file_location('synthetic_market_outlook_http_research', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def http_samples():
    first = date(2026, 1, 5)
    days = [(first + timedelta(days=index)).isoformat() for index in range(16)
            if (first + timedelta(days=index)).weekday() < 5][:11]
    payloads = {}
    for index, day in enumerate(days):
        outlook = {'version': 'dated-market-outlook-v1', 'date': day, 'snapshot_id': 'synthetic-http',
                   'next_session': {'date': days[index + 1] if index < len(days) - 1 else None},
                   'temperature': {'value': 70, 'coverage_pct': 100, 'calendar_valid': True,
                                   'evidence_consistent': True},
                   'stage': {'code': 'broad_rise', 'status': 'observation'},
                   'entry_gate': {'status': 'conditional'},
                   'insights': {'version': 'market-opportunity-insights-v1', 'date': day,
                                'snapshot_id': 'synthetic-http', 'routes': [],
                                'candidate_tracking': {'as_of': day, 'status': 'none', 'confirmed': False}}}
        payloads[day] = {'date': day, 'snapshot_id': 'synthetic-http', 'market_outlook': outlook}
    return days, payloads


def test_http_research_uses_fixed_complete_windows_and_never_claims_trade_success():
    module = research_module()
    days, payloads = http_samples()
    calls = []
    def fetch(day):
        calls.append(day)
        return payloads[day]
    result = module.research([days[0]], fetch)
    assert result['horizons'] == [1, 3, 5, 10]
    assert calls == days
    for horizon in (1, 3, 5, 10):
        observed = result['samples'][0]['horizons'][str(horizon)]
        assert observed['state'] == 'observed'
        assert observed['observed_dates'] == days[:horizon + 1]
        assert observed['endpoint']['date'] == days[horizon]
        assert observed['strong_breadth_sessions'] == horizon
        assert observed['blocked_sessions'] == 0
        assert 'win_rate' not in observed and 'return_pct' not in observed


@pytest.mark.parametrize('mode,reason', [
    ('fallback_date', 'endpoint_date_mismatch'),
    ('generation_changed', 'market_generation_changed'),
    ('missing_next_date', 'next_market_session_unknown'),
    ('low_coverage', 'breadth_evidence_incomplete'),
    ('missing_insights', 'endpoint_insights_unverified'),
    ('request_budget', 'request_budget_exhausted'),
])
def test_http_research_cannot_skip_a_missing_or_incomparable_session(mode, reason):
    module = research_module()
    days, payloads = http_samples()
    broken = payloads[days[2]]
    if mode == 'fallback_date': broken['date'] = days[1]
    elif mode == 'generation_changed':
        broken['snapshot_id'] = broken['market_outlook']['snapshot_id'] = 'synthetic-new-generation'
        broken['market_outlook']['insights']['snapshot_id'] = 'synthetic-new-generation'
    elif mode == 'missing_next_date': payloads[days[1]]['market_outlook']['next_session']['date'] = None
    elif mode == 'low_coverage': broken['market_outlook']['temperature']['coverage_pct'] = 10
    elif mode == 'missing_insights': broken['market_outlook'].pop('insights')
    calls = []
    def fetch(day):
        calls.append(day)
        return payloads[day]
    result = module.research([days[0]], fetch, max_requests=2 if mode == 'request_budget' else 120)
    windows = result['samples'][0]['horizons']
    assert windows['1']['state'] == 'observed'
    for horizon in (3, 5, 10):
        assert windows[str(horizon)]['state'] == 'unknown'
        assert windows[str(horizon)]['reason'] == reason
        assert windows[str(horizon)]['endpoint'] is None
        assert windows[str(horizon)]['temperature_change'] is None
    if mode != 'low_coverage':
        assert len(calls) <= 3, 'do not search beyond the broken exact-session chain'
