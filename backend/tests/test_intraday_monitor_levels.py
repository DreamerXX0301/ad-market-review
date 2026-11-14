"""Real reference algorithms on synthetic bars; no collection or DB writes."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from app.analytics import chart_trendlines
from app.services import automatic_price_alerts as closing
from app.services import intraday_monitor_levels as levels


SYMBOL = '002187.SZ'


def history():
    days, day = [], date(2026, 7, 1)
    while len(days) < 48:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    lows = [12, 11, 10, 12, 13, 14, 13, 10.1, 14, 15, 16, 17] * 4
    highs = [19, 20, 21, 23, 21, 19, 18, 19, 21, 19, 18, 18] * 4
    rows = [{'date': day, 'open': (low + high) / 2, 'high': high, 'low': low,
             'close': (low + high) / 2, 'volume': 1000, 'complete': True,
             'price_basis': 'none', 'corporate_action': False,
             'corporate_action_coverage_known': True}
            for day, low, high in zip(days, lows, highs)]
    rows[-1].update(open=15, close=15, low=14, high=16)
    return rows, days


def freeze(rows, days, **extra):
    return levels.automatic_baseline(SYMBOL, rows, previous_day=days[-1],
                                     calendar=days, snapshot='market-old',
                                     primary_snapshot='daily-old', **extra)


def candidates(result, kind):
    return [item for item in result['levels'] if item['kind'] == kind]


def following(rows, days, close):
    day = date.fromisoformat(days[-1]) + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return [*deepcopy(rows), {**rows[-1], 'date': day.isoformat(),
            'open': close, 'close': close, 'low': close - .2, 'high': close + .2}], [*days, day.isoformat()]


def test_actual_builder_price_shape_and_true_structure_are_distinct_from_risk_floor():
    rows, days = history()
    result = freeze(rows, days)
    direct = closing.build_baseline(SYMBOL, rows, days[-1], days, 'market-old', 'daily-old',
                                    profile='short_week_v1', max_risk_pct=5)
    expected = direct['levels']['stop']['price']
    risk = candidates(result, 'risk')
    assert len(risk) == 1 and risk[0]['lower'] == expected
    assert risk[0]['upper'] == expected and risk[0]['label'] == '短线风险线'
    assert risk[0]['evidence']['candidate']['source'] == 'short_risk_boundary'
    assert result['previous_close'] == rows[-1]['close'] == 15
    assert direct['levels']['support'] is None  # Real support is farther than 5%.
    genuine = [item for item in candidates(result, 'support') if item['source_id'] == 'level:display_support']
    assert len(genuine) == 1 and genuine[0]['lower'] == pytest.approx(10.1)
    assert genuine[0]['evidence']['candidate']['source'] == 'confirmed_pivot_low'
    assert risk[0]['lower'] != genuine[0]['lower']
    assert all(item['lower'] != expected for item in candidates(result, 'support'))
    assert any(item['source_kind'] == 'automatic_zone' for item in result['levels'])


def test_today_and_future_quotes_cannot_change_prior_day_reference_or_confirmation():
    rows, days = history()
    expected = freeze(rows, days)
    later_rows, later_days = following(rows, days, 90)
    later_rows, later_days = following(later_rows, later_days, 1)
    actual = levels.automatic_baseline(SYMBOL, later_rows, previous_day=days[-1],
                                       calendar=later_days, snapshot='market-old', primary_snapshot='daily-old')
    assert actual == expected
    for item in actual['levels']:
        assert item['baseline_date'] == days[-1]
        evidence = item['evidence']
        if item['source_kind'] == 'automatic_zone':
            assert evidence['zone']['end_date'] == days[-1]
            assert all(pivot['confirmed_on'] <= days[-1] for pivot in evidence['pivot_confirmations'])
        elif item['kind'] == 'support':
            assert evidence['candidate']['confirmed_on'] <= days[-1]


def test_zones_skip_expensive_sloped_trendline_search(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('intraday level preparation must not compute chart sloped lines/events')
    monkeypatch.setattr(chart_trendlines, '_line_groups', unexpected)
    monkeypatch.setattr(chart_trendlines, '_recent_crossings', unexpected)
    rows, days = history()
    assert any(item['source_kind'] == 'automatic_zone' for item in freeze(rows, days)['levels'])


@pytest.mark.parametrize('change', ['missing', 'coverage', 'action', 'forming', 'noon', 'volume', 'basis', 'bad_ohlc'])
def test_missing_or_unusable_exact_previous_day_never_falls_back(change):
    rows, days = history()
    if change == 'missing':
        rows.pop()
    else:
        patch = {'coverage': {'corporate_action_coverage_known': False},
                 'action': {'corporate_action': True}, 'forming': {'complete': False},
                 'noon': {'noon_provisional': True}, 'volume': {'volume': 0},
                 'basis': {'price_basis': 'forward_event_affine'}, 'bad_ohlc': {'high': 1}}[change]
        rows[-1].update(patch)
    result = freeze(rows, days)
    assert result['levels'] == [] and result['previous_close'] is None and result['reason']


@pytest.mark.parametrize('change', ['coverage', 'missing', 'zero_volume', 'provisional'])
def test_recent_gap_does_not_bridge_old_pivots_or_atr(change):
    rows, days = history()
    index = len(rows) - 4
    if change == 'missing':
        rows.pop(index)
    else:
        rows[index].update({'coverage': {'corporate_action_coverage_known': False},
                            'zero_volume': {'volume': 0}, 'provisional': {'noon_provisional': True}}[change])
    result = freeze(rows, days)
    assert result['previous_close'] == 15
    assert result['levels'] == []
    assert result['risk_reason'] and result['reason']


@pytest.mark.parametrize('change', ['duplicate', 'unsorted', 'calendar_missing', 'calendar_duplicate', 'snapshot'])
def test_ambiguous_history_calendar_or_snapshot_is_unknown(change):
    rows, days = history()
    if change == 'duplicate':
        rows.append(deepcopy(rows[-1]))
    elif change == 'unsorted':
        rows[-2], rows[-1] = rows[-1], rows[-2]
    elif change == 'calendar_missing':
        result = levels.automatic_baseline(SYMBOL, rows, previous_day=days[-1], calendar=days[:-1], snapshot='raw')
        assert result['levels'] == [] and result['reason']
        return
    elif change == 'calendar_duplicate':
        days.insert(-1, days[-2])
    elif change == 'snapshot':
        result = levels.automatic_baseline(SYMBOL, rows, previous_day=days[-1], calendar=days, snapshot=None)
        assert result['levels'] == [] and result['reason']
        return
    result = freeze(rows, days)
    assert result['levels'] == [] and result['reason']


def test_existing_per_stock_profile_and_risk_distance_are_respected():
    rows, days = history()
    short_rule = {'rule_id': 'saved-stock-rule', 'revision': 3, 'profile': 'short_week_v1', 'max_risk_pct': 10}
    short = freeze(rows, days, rule=short_rule)
    assert short['monitoring_baseline']['max_risk_pct'] == 10
    assert candidates(short, 'risk')[0]['evidence']['max_risk_pct'] == 10
    assert candidates(short, 'risk')[0]['lower'] < candidates(freeze(rows, days), 'risk')[0]['lower']
    structural_rule = {**short_rule, 'profile': 'structure'}
    structural = freeze(rows, days, rule=structural_rule)
    assert structural['monitoring_baseline']['profile'] == 'structure'
    assert candidates(structural, 'risk')[0]['label'] == '结构失效参考'
    assert all(item['label'] != '短线风险线' for item in structural['levels'])
    assert any(item['source_id'] == 'level:support' for item in candidates(structural, 'support'))


def test_default_and_existing_short_monitors_preserve_their_higher_saved_anchor():
    rows, days = history()
    for rule in (None, {'rule_id': 'existing-rule', 'revision': 2, 'profile': 'short_week_v1', 'max_risk_pct': 5}):
        original = freeze(rows, days, rule=rule)
        later_rows, later_days = following(rows, days, 14.8)
        fresh = freeze(later_rows, later_days, rule=rule)
        carried = freeze(later_rows, later_days, rule=rule, saved_baseline=original)
        assert candidates(fresh, 'risk')[0]['lower'] < candidates(original, 'risk')[0]['lower']
        assert candidates(carried, 'risk')[0]['lower'] == candidates(original, 'risk')[0]['lower']
        assert carried['monitoring_baseline']['risk_anchor'] == original['monitoring_baseline']['risk_anchor']
        assert 'risk_anchor' not in carried['monitoring_baseline']['risk_anchor']


def test_official_current_revision_baseline_can_be_passed_with_sqlite_metadata():
    rows, days = history()
    rule = {'rule_id': 'official-rule', 'revision': 4, 'profile': 'short_week_v1', 'max_risk_pct': 5}
    official = closing.build_baseline(SYMBOL, rows, days[-1], days, 'original', 'original-raw',
                                      profile='short_week_v1', max_risk_pct=5)
    official = closing.advance_short_baseline(None, official, rows, days)
    official.update(rule_id=rule['rule_id'], rule_revision=rule['revision'])
    repaired = deepcopy(rows)
    repaired[-1].update(close=14.8, open=14.8)
    result = freeze(repaired, days, rule=rule, saved_baseline=official)
    assert candidates(result, 'risk')[0]['lower'] == official['levels']['stop']['price']
    assert result['monitoring_baseline']['risk_anchor']['primary_snapshot_id'] == 'original-raw'


def test_breached_risk_is_not_rearmed_after_rebound_but_explicit_new_revision_starts_fresh():
    rows, days = history()
    rule = {'rule_id': 'existing-rule', 'revision': 2, 'profile': 'short_week_v1', 'max_risk_pct': 5}
    original = freeze(rows, days, rule=rule)
    lost_rows, lost_days = following(rows, days, 13)
    breached = freeze(lost_rows, lost_days, rule=rule, saved_baseline=original)
    assert candidates(breached, 'risk') == [] and '已失守' in breached['risk_reason']
    assert breached['monitoring_baseline']['levels']['stop']['price'] == candidates(original, 'risk')[0]['lower']
    recovered_rows, recovered_days = following(lost_rows, lost_days, 15)
    recovered = freeze(recovered_rows, recovered_days, rule=rule, saved_baseline=breached)
    assert candidates(recovered, 'risk') == [] and '已失守' in recovered['risk_reason']
    reset = freeze(recovered_rows, recovered_days, rule={**rule, 'revision': 3}, saved_baseline=recovered)
    assert candidates(reset, 'risk') and reset['monitoring_baseline']['levels']['stop']['state'] == 'monitoring'


def test_unproven_saved_anchor_is_suspended_instead_of_silently_lowered():
    rows, days = history()
    original = freeze(rows, days)
    unknown_owner = deepcopy(original['monitoring_baseline'])
    unknown_owner.pop('rule_revision')
    result = freeze(rows, days, saved_baseline=unknown_owner)
    assert candidates(result, 'risk') == [] and '版本' in result['risk_reason']
    assert result['monitoring_baseline']['risk_anchor']['suspended'] is True
    later_rows, later_days = following(rows, days, 14.8)
    later = freeze(later_rows, later_days, saved_baseline=result)
    assert candidates(later, 'risk') == [] and later['risk_reason']


def test_saved_anchor_cannot_cross_stock_future_date_or_missing_continuity():
    rows, days = history()
    original = freeze(rows, days)
    for patch in ({'instrument_id': '600001.SH'}, {'as_of': '2026-12-31'}):
        saved = {**deepcopy(original['monitoring_baseline']), **patch}
        result = freeze(rows, days, saved_baseline=saved)
        assert candidates(result, 'risk') == [] and result['risk_reason']
    later_rows, later_days = following(rows, days, 14.8)
    missing_before = later_rows[1:]
    missing_before = [row for row in missing_before if row['date'] != days[-1]]
    result = freeze(missing_before, later_days, saved_baseline=original)
    assert candidates(result, 'risk') == []
    assert result['monitoring_baseline']['risk_anchor']['suspended'] is True


def line(**extra):
    return {'annotation_id': 'manual-one', 'revision': 2, 'price': 100,
            'line_type': 'support', 'price_basis': 'none', 'label': '人工支撑', **extra}


def test_fixed_anchor_line_is_converted_with_evidence_and_input_is_unchanged():
    source = line(price_basis='forward_event_affine', anchor_date='2026-11-11')
    actions = [{'date': '2026-11-12', 'dividend_per_share': 0, 'per_share_bonus': 1,
                'allotment_ratio': 0, 'allotment_price': 0, 'currency': 'CNY'}]
    original = deepcopy([source, actions])
    prepared, unknowns = levels.manual_levels([source], previous_day='2026-11-12', actions=actions)
    assert unknowns == [] and len(prepared) == 1
    assert prepared[0]['lower'] == prepared[0]['upper'] == 50
    assert prepared[0]['price_basis'] == 'none'
    assert prepared[0]['evidence']['saved_price'] == 100
    assert prepared[0]['evidence']['action_events'][0]['bonus'] == 1
    assert prepared[0]['evidence']['comparison_date'] == '2026-11-12'
    assert [source, actions] == original


@pytest.mark.parametrize('anchor,actions', [
    ('2026-11-11', None), ('2026-11-13', []), ('invalid', []),
    ('2026-11-11', [{'date': '2026-11-12'}]),
])
def test_unknown_fixed_anchor_evidence_does_not_create_a_nominal_line(anchor, actions):
    prepared, unknowns = levels.manual_levels([line(price_basis='forward_event_affine', anchor_date=anchor)],
                                             previous_day='2026-11-12', actions=actions)
    assert prepared == [] and len(unknowns) == 1


def test_manual_eligibility_is_callers_job_and_reference_lines_never_become_support():
    # The caller uses active_rules; preparing prices must not silently save,
    # enable or reinterpret any closing-price alert mode.
    sources = [line(alert_modes=[]), line(annotation_id='reference', line_type='reference')]
    prepared, unknowns = levels.manual_levels(sources, previous_day='2026-11-12', actions=None)
    assert len(prepared) == 1 and prepared[0]['source_id'] == 'manual-one'
    assert unknowns == [] and sources[0]['alert_modes'] == []


@pytest.mark.parametrize('live,valid', [(10, True), (10.005, True), (9.995, True), (10.0051, False), (9.9949, False), (None, False)])
def test_live_previous_close_mismatch_uses_exact_half_cent_boundary(live, valid):
    quote = {'valid': True, 'previous_close': live}
    result = levels.comparable_quote(quote, {'previous_close': 10})
    assert result['valid'] is valid
    if not valid:
        assert result['reason']
    if live is not None:
        assert result['previous_close_comparison']['current_session_corporate_action_coverage_proven'] is False
        assert result['previous_close_comparison']['published_previous_close'] == 10
    assert quote == {'valid': True, 'previous_close': live}


def test_missing_published_previous_close_pauses_comparison_and_invalid_quote_reason_survives():
    assert levels.comparable_quote({'valid': True, 'previous_close': 10}, {'previous_close': None})['valid'] is False
    invalid = {'valid': False, 'reason': '来源已经过期'}
    assert levels.comparable_quote(invalid, {'previous_close': 10}) == invalid


def test_bounded_raw_batch_rows_receive_the_same_explicit_action_coverage_as_single_stock_reader():
    from app.services.intraday_monitor_levels import annotate_action_coverage
    rows = {'600001.SH': [
        {'date': '2026-11-12', 'close': 10}, {'date': '2026-11-13', 'close': 11},
    ], '600002.SH': [{'date': '2026-11-13', 'close': 20}]}
    prepared = annotate_action_coverage(rows, {'600001.SH': [{'date': '2026-11-13'}], '600002.SH': []})
    assert prepared['600001.SH'][0]['corporate_action_coverage_known'] is True
    assert prepared['600001.SH'][0]['corporate_action'] is False
    assert prepared['600001.SH'][1]['corporate_action'] is True
    assert prepared['600002.SH'][0]['corporate_action_coverage_known'] is True
    assert prepared['600002.SH'][0]['corporate_action'] is False
    assert all('corporate_action_coverage_known' not in row for values in rows.values() for row in values)
    for unknown in (None, {}, {'600001.SH': [{'date': None}]}):
        assert annotate_action_coverage(rows, unknown)['600001.SH'][0]['corporate_action_coverage_known'] is False
