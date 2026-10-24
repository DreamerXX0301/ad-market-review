"""P-day price cohorts must not select only T-day regime-known winners."""
from copy import deepcopy

import pytest

from app.services import strategy_limits
from app.services.market_sentiment import COHORT_PRICE_FEEDBACK_VERSION, describe_sentiment
from app.services.sentiment_context import RULE_VERSION
from test_market_ladder import B, DAYS, Market, OTHER, TODAY
from test_market_sentiment import rows, values


@pytest.fixture
def market(monkeypatch):
    value = Market()
    monkeypatch.setattr(strategy_limits, 'load_supplement', lambda _: {})
    # P confirms both ordinary members. Only today's limit-up OTHER supplies
    # explicit T non-ST identity; losing B must still enter price feedback.
    for record in value.ref['_supplement']['annual_pools'].values():
        for item in record['items']: item['is_st'] = False
    value.history[B][-1]['close'] = 9
    value.history[OTHER][-1]['close'] = 11
    value.ref['_supplement']['annual_pools'][TODAY]['items'][-1]['last_price'] = 11
    return value


def ordinary(market, current=None):
    context = describe_sentiment(market, market.ref, rows(market) if current is None else current, TODAY)
    return context, next(group for group in context['short_term']['groups'] if group['id'] == 'ordinary_10')


def test_frozen_price_feedback_includes_identity_unknown_losers_without_relaxing_promotion(market):
    context, strict = ordinary(market)
    price = strict['cohort_price_feedback']; actual = values(price)
    assert price['version'] == COHORT_PRICE_FEEDBACK_VERSION == 'previous-group-price-feedback-v1'
    assert (price['group_id'], price['date'], price['cohort_date']) == ('ordinary_10', TODAY, DAYS[-2])
    assert price['price_basis'] == 'none'
    assert price['observed_count'] == price['sample_count'] == 2
    assert price['unknown_count'] == 0 and price['coverage_pct'] == 100
    assert actual['previous_limit_up_avg_return_pct'] == pytest.approx(0)
    assert actual['previous_limit_up_median_return_pct'] == pytest.approx(0)
    assert actual['positive_ratio_pct'] == 50
    assert price['state'] == 'mixed' and price['current_identity_unknown_count'] == 1
    # Preserve the former same-regime observation and conservative interval.
    assert strict['sample_count'] == 1 and strict['unknown_count'] == 1
    assert values(strict)['previous_limit_up_avg_return_pct'] == pytest.approx(10)
    assert strict['promoted_count'] == 1 and strict['failed_count'] == 0
    assert strict['promotion_bounds_pct'] == {'lower': 50, 'upper': 100}
    assert values(strict)['promotion_rate_pct'] is None
    assert context['provenance']['rule_version'] == RULE_VERSION == 'descriptive-context-v2'
    # New price statistics reuse the same bounded P/T history batch.
    assert len(market.history_calls) == 1
    assert market.history_calls[0][1:] == (DAYS[-2], TODAY)


def test_t_regime_change_does_not_erase_independent_p_cohort_price_feedback(market):
    identity = next(item for item in market.master if item['instrument_id'] == B)
    identity.update(is_st=True, is_st_date=TODAY)
    _, group = ordinary(market)
    price = group['cohort_price_feedback']
    assert price['sample_count'] == 2 and values(price)['previous_limit_up_avg_return_pct'] == pytest.approx(0)
    assert group['sample_count'] == 1 and group['promotion_unknown_count'] == 1
    assert group['failed_count'] == 0 and group['promotion_bounds_pct'] == {'lower': 50, 'upper': 100}


@pytest.mark.parametrize('mode', [
    'missing_quote', 'duplicate_quote', 'zero_volume', 'company_action',
    'unknown_action_coverage', 'missing_panel', 'duplicate_panel', 'conflicting_return',
    'wrong_panel_date', 'nonfinite_close',
])
def test_missing_suspended_action_or_conflicting_price_is_unknown_not_zero(market, mode):
    current = rows(market)
    panel = next(item for item in current if item['instrument_id'] == B)
    if mode == 'missing_quote': market.history[B].pop()
    elif mode == 'duplicate_quote': market.history[B].append(deepcopy(market.history[B][-1]))
    elif mode == 'zero_volume': market.history[B][-1]['volume'] = 0
    elif mode == 'company_action': panel.update(corporate_action=True, price_continuity=False)
    elif mode == 'unknown_action_coverage': panel['corporate_action_coverage_known'] = False
    elif mode == 'missing_panel': current.remove(panel)
    elif mode == 'duplicate_panel': current.append(deepcopy(panel))
    elif mode == 'conflicting_return': panel['change_pct'] = 999
    elif mode == 'wrong_panel_date': panel['date'] = DAYS[-2]
    else: market.history[B][-1]['close'] = float('nan')
    _, group = ordinary(market, current)
    price = group['cohort_price_feedback']; actual = values(price)
    assert price['observed_count'] == 2 and price['sample_count'] == price['unknown_count'] == 1
    assert price['coverage_pct'] == 50 and price['state'] == 'insufficient'
    assert actual['previous_limit_up_avg_return_pct'] == pytest.approx(10)
    assert actual['previous_limit_up_median_return_pct'] == pytest.approx(10)
    assert actual['positive_ratio_pct'] == 100


def test_unverified_previous_event_does_not_enter_price_cohort_even_with_valid_prices(market):
    market.ref['_supplement']['annual_pools'][DAYS[-2]]['items'][0]['last_price'] = 99
    context, group = ordinary(market)
    price = group['cohort_price_feedback']
    assert price['observed_count'] == price['sample_count'] == 1
    assert price['cohort_source_complete'] is False and price['state'] == 'insufficient'
    assert context['short_term']['previous_unverified_count'] == 1
    assert values(price)['previous_limit_up_avg_return_pct'] == pytest.approx(10)


@pytest.mark.parametrize('mode', ['source_partial', 'previous_identity_missing'])
def test_price_coverage_never_proves_complete_source_or_group_identity(market, mode):
    previous = market.ref['_supplement']['annual_pools'][DAYS[-2]]
    if mode == 'source_partial': previous.update(complete=False, pagination_complete=False)
    else: previous['items'][0].pop('is_st')
    _, group = ordinary(market)
    price = group['cohort_price_feedback']
    assert price['sample_count'] == price['observed_count'] and price['coverage_pct'] == 100
    assert price['state'] == 'insufficient'
    assert not price['cohort_source_complete'] if mode == 'source_partial' else not price['identity_complete']
    assert group['promotion_bounds_pct'] is None


def test_no_comparable_prices_and_empty_group_preserve_null_metrics(market):
    current = rows(market)
    for row in current: row['corporate_action_coverage_known'] = False
    context, group = ordinary(market, current)
    price = group['cohort_price_feedback']
    assert price['sample_count'] == 0 and price['unknown_count'] == 2
    assert price['coverage_pct'] == 0 and all(item['value'] is None and item['reason'] for item in price['metrics'])
    empty = next(item for item in context['short_term']['groups'] if item['id'] == 'st')['cohort_price_feedback']
    assert empty['sample_count'] == empty['observed_count'] == empty['unknown_count'] == 0
    assert empty['coverage_pct'] is None and all(item['value'] is None for item in empty['metrics'])


def test_future_prices_events_and_identity_never_change_t_price_feedback(market):
    current = rows(market)
    before, group_before = ordinary(market, current)
    for history in market.history.values(): history.append({**history[-1], 'date': '2099-01-01', 'close': 999})
    market.ref['_supplement']['annual_pools']['2099-01-01'] = {
        'date': '2099-01-01', 'items': [{'instrument_id': B, 'date': '2099-01-01', 'last_price': 999, 'is_st': True}]}
    next(item for item in market.master if item['instrument_id'] == B).update(is_st=True, is_st_date='2099-01-01')
    after, group_after = ordinary(market, current)
    assert group_after == group_before
    assert after['provenance']['input_fingerprint'] == before['provenance']['input_fingerprint']
