"""Directional downward signals: not the negation of a golden cross."""
from copy import deepcopy
from datetime import date, timedelta
import math
import pandas as pd
import pytest

from app.analytics.indicators import crossed_down, enrich_bars, aggregate_weekly, INDICATOR_VERSION
from app.analytics.panel_inputs import PanelInputs
from app.domain.conditions import evaluate, metrics_for, metrics_from_enriched, specs, validate
from test_workspace import ws, make_strategy, add_one, advance


def sample(count=85):
    day = date(2026, 1, 5); calendar = []
    while len(calendar) < count:
        if day.weekday() < 5: calendar.append(day.isoformat())
        day += timedelta(days=1)
    rows = [{'instrument_id': '600001.SH', 'date': observed, 'open': 100., 'high': 101., 'low': 99., 'close': 100., 'volume': 100., 'amount': 10000., 'corporate_action': False} for observed in calendar]
    return calendar, rows


@pytest.mark.parametrize('old_fast,old_slow,fast,slow,expected', [
    (1., 1., .9, 1., True),
    (1.1, 1., .9, 1., True),
    (.9, 1., .8, 1., False),
    (1., 1., 1., 1., False),
    (1.1, 1., 1., 1., False),
    (.9, 1., 1.1, 1., False),
    (None, 1., .9, 1., None),
    (float('nan'), 1., .9, 1., None),
    (1., 1., float('inf'), 1., None),
    (True, 1., .9, 1., None),
])
def test_strict_cross_including_prior_equality_and_invalid_numbers(old_fast, old_slow, fast, slow, expected):
    assert crossed_down(old_fast, old_slow, fast, slow) is expected


def test_shared_scalar_panel_and_both_metric_adapters_agree_on_actual_cross_and_continued_below():
    calendar, rows = sample()
    rows[35]['close'] = 99.5
    rows[36]['close'] = 99.
    enriched = enrich_bars(rows, calendar)
    panel = PanelInputs(pd.DataFrame(rows), calendar)
    for field in ('macd_dead_cross', 'kd_dead_cross'):
        assert enriched[34][field] is None
        assert enriched[35][field] is True
        assert enriched[36][field] is False
        for index in (34, 35, 36):
            assert panel.latest(calendar[index])['600001.SH'][field] is enriched[index][field]
            assert metrics_from_enriched(enriched[index], enriched[index - 1])[field] is enriched[index][field]
            assert metrics_for(rows, calendar[index], calendar)[field] is enriched[index][field]
        assert enriched[35][field.replace('dead_', '')] is False
    assert 'dead-cross-v2' in INDICATOR_VERSION


def test_no_golden_cross_is_not_a_death_cross():
    calendar, rows = sample(37)
    last = enrich_bars(rows, calendar)[-1]
    assert last['macd_cross'] is False and last['macd_dead_cross'] is False
    assert last['kd_cross'] is False and last['kd_dead_cross'] is False


@pytest.mark.parametrize('mode', ['missing_previous', 'action', 'weekly_boundary'])
def test_gap_action_or_weekly_reset_restarts_both_cross_warmups(mode):
    calendar, rows = sample()
    if mode == 'missing_previous': rows.pop(35)
    elif mode == 'action': rows[36]['corporate_action'] = True
    else: rows[36]['weekly_continuity_reset_reason'] = '前一周报价不完整'
    selected = next(row for row in enrich_bars(rows, calendar) if row['date'] == calendar[36])
    assert selected['indicator_warmup_bars'] == 1
    for field in ('macd_dead_cross', 'kd_dead_cross'):
        assert selected[field] is None
        assert metrics_from_enriched(selected)[field] is None
        if mode != 'weekly_boundary':
            assert metrics_for(rows, calendar[36], calendar)[field] is None
            assert PanelInputs(pd.DataFrame(rows), calendar).latest(calendar[36])['600001.SH'][field] is None


@pytest.mark.parametrize('mode', ['no_field', 'too_short', 'reset', 'nonfinite'])
def test_adapter_keeps_unproven_old_or_invalid_indicator_rows_unknown(mode):
    row = {'date': '2026-01-05', 'indicator_warmup_bars': 36, 'price_continuity': True,
           'macd_dif': -.1, 'macd_dea': 0, 'kdj_k': 40, 'kdj_d': 50,
           'macd_dead_cross': True, 'kd_dead_cross': True}
    if mode == 'no_field':
        row.pop('macd_dead_cross'); row.pop('kd_dead_cross')
    elif mode == 'too_short': row['indicator_warmup_bars'] = 35
    elif mode == 'reset': row['price_continuity'] = False
    else: row.update(macd_dif=float('nan'), kdj_k=float('inf'))
    metrics = metrics_from_enriched(row)
    for field in ('macd_dead_cross', 'kd_dead_cross'):
        assert metrics[field] is None and metrics[field + '_reason']
        assert evaluate(validate({'field': field, 'op': '==', 'value': False}), metrics)['result'] is None


def test_weekly_metrics_use_completed_weekly_signal_and_do_not_read_future_daily_bars():
    calendar, rows = sample(210)
    for index, row in enumerate(rows):
        value = 110 + math.sin(index / 7) * 5
        row.update(open=value, close=value, high=value + 2, low=value - 2)
    day = calendar[199]
    weekly = [row for row in aggregate_weekly(rows[:200], calendar, as_of=day) if row['complete']]
    enriched = enrich_bars(weekly)
    expected = metrics_from_enriched(enriched[-1], enriched[-2])
    actual = metrics_for(rows, day, calendar, 'W')
    for field in ('macd_dead_cross', 'kd_dead_cross'):
        assert actual[field] is expected[field]
        future = deepcopy(rows)
        for row in future[200:]: row.update(close=900, high=901)
        assert metrics_for(future, day, calendar, 'W')[field] is actual[field]


def test_metadata_is_event_signal_and_boolean_not_a_negative_golden_alias():
    catalog = {row['field']: row for row in specs()}
    for field in ('macd_dead_cross', 'kd_dead_cross'):
        assert catalog[field]['type'] == 'boolean'
        assert catalog[field]['category'] == '事件信号'
        assert catalog[field]['hidden_in_builder'] is False
        assert '死叉' in catalog[field]['label']
        assert validate({'field': field, 'op': '==', 'value': True})['field'] == field


@pytest.mark.parametrize('field', ['macd_dead_cross', 'kd_dead_cross'])
def test_real_death_cross_can_end_round_but_continued_below_is_not_a_new_cross(ws, field):
    market, client = ws
    for row in market.data['600001.SH']:
        row.update(open=100, high=101, low=99, close=100)
    market.data['600001.SH'][71]['close'] = 99.5
    market.data['600001.SH'][72]['close'] = 99.
    strategy = make_strategy(client, policy={'exits': [{'field': field, 'op': '==', 'value': True}]})
    _, round_ = add_one(client, strategy)
    advance(market, 72)
    response = client.post('/api/v1/tracking-rounds/review', json={})
    assert response.status_code == 200, response.text
    detail = client.get('/api/v1/tracking-rounds/' + round_['round_id']).json()
    assert response.json()['ended'] == 1
    assert detail['status'] == 'condition_ended' and detail['end_business_date'] == market.days[71]
    review = next(row for row in detail['events'] if row['event_type'] == 'review' and row['business_date'] == market.days[71])
    assert review['evidence']['metrics'][field] is True
    assert metrics_for(market.data['600001.SH'], market.days[72], market.days)[field] is False


@pytest.mark.parametrize('halt_index', [34, 35])
def test_suspended_current_or_prior_observation_cannot_trigger_death_cross(halt_index):
    calendar, rows = sample(37)
    rows[35]['close'] = 99.5
    rows[36]['close'] = 99.
    rows[halt_index]['volume'] = rows[halt_index]['amount'] = 0.
    enriched = enrich_bars(rows, calendar)
    panel = PanelInputs(pd.DataFrame(rows), calendar)
    for field in ('macd_dead_cross', 'kd_dead_cross'):
        assert enriched[35][field] is None
        assert panel.latest(calendar[35])['600001.SH'][field] is None
        assert metrics_for(rows, calendar[35], calendar)[field] is None
        assert metrics_from_enriched(enriched[35], enriched[34])[field] is None
        # Guard a stale externally-supplied true value too.
        stale = {**enriched[35], field: True}
        assert metrics_from_enriched(stale, enriched[34])[field] is None
