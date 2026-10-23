"""Tiny in-memory rankings, no business files, fetches or database fixtures."""
from copy import deepcopy
from datetime import date, timedelta
import threading
import gc
import weakref

import pandas as pd
import pytest

from app.analytics.amount_rank import ranked_amount_rows, amount_ranking_fact
from app.analytics.market import MarketData
from app.services import market_top_amount as activity


def days(count=12):
    current = date(2026, 10, 5)
    result = []
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def rows(day, count=20):
    return [{'instrument_id': f'600{index:03d}.SH', 'date': day, 'name': '测试'+str(index),
             'amount': 1000-index*10} for index in range(count)]


class CompactMarket:
    def __init__(self, calendar=None):
        self.days = calendar or days()
        self.records = {day: rows(day) for day in self.days}
        self.snapshot = 'synthetic-amount-generation'
        self.calls = []

    def calendar(self):
        return list(self.days)

    def amount_top10_history(self, dates):
        self.calls.append(list(dates))
        return {'snapshot_id': self.snapshot,
                'days': {day: amount_ranking_fact(self.records[day], day) for day in dates if day in self.records}}

    def panel(self, day):
        pytest.fail('compact history must not compute seven full indicator panels')


@pytest.fixture(autouse=True)
def clear_cache():
    activity.clear_amount_activity_cache()
    yield
    activity.clear_amount_activity_cache()


def decorate(market, day, membership=None):
    return activity.decorate_top_amount(market, {'snapshot_id': market.snapshot, 'data_end': market.days[-1]},
                                        day, market.records.get(day, []), {},
                                        resolved_membership=membership or ({}, {'selected_date_available': False}))


def test_same_stable_rank_selection_and_exact_seven_market_sessions_include_observation():
    market = CompactMarket()
    selected_day = market.days[8]
    # One currently tenth-ranked stock missed the historical fourth session.
    market.records[market.days[4]][9]['amount'] = 0
    before = deepcopy(market.records)
    top, summary = decorate(market, selected_day)
    assert market.records == before
    assert [row['instrument_id'] for row in top] == [row['instrument_id'] for row in ranked_amount_rows(market.records[selected_day])]
    first = top[0]['active_top10_7d']
    assert first['count'] == 7 and first['complete'] is True and first['state'] == 'complete'
    assert first['dates'] == market.days[2:9]
    assert [row['rank'] for row in first['ranks']] == [1]*7
    assert first['label'] == '持续活跃 · 7次'
    assert top[9]['active_top10_7d']['count'] == 6
    assert summary['observed_days'] == summary['expected_days'] == 7
    assert summary['window_end'] == selected_day and summary['window_start'] == market.days[2]
    assert market.calls == [market.days[2:9]], 'future dates are never requested'
    tie = [{'instrument_id': value, 'amount': 100} for value in ('600002.SH', '600001.SH')]
    assert [row['instrument_id'] for row in ranked_amount_rows(tie)] == ['600002.SH', '600001.SH']


def test_missing_dates_short_calendar_and_invalid_amounts_remain_observed_partial():
    market = CompactMarket()
    del market.records[market.days[4]]
    top, summary = decorate(market, market.days[8])
    assert top[0]['active_top10_7d']['count'] == 6
    assert top[0]['active_top10_7d']['state'] == 'observed_partial'
    assert '已观察6次' in top[0]['active_top10_7d']['label']
    assert '样本不完整' in top[0]['active_top10_7d']['label']
    assert '补齐可能改变' in top[0]['active_top10_7d']['reason']
    assert '改变名次和次数' in top[0]['active_top10_7d']['scope']
    assert summary['observed_days'] == 6 and summary['missing_dates'] == [market.days[4]]
    assert summary['complete'] is False
    short = CompactMarket(days(4))
    top, summary = decorate(short, short.days[-1])
    assert top[0]['active_top10_7d']['count'] == 4
    assert summary['expected_days'] == 7 and summary['observed_days'] == 4 and not summary['complete']
    bad = CompactMarket()
    bad.records[bad.days[3]][19]['amount'] = None
    top, summary = decorate(bad, bad.days[8])
    assert not summary['complete'] and bad.days[3] in summary['incomplete_dates']
    assert top[0]['active_top10_7d']['state'] == 'observed_partial'


def test_partial_rank_appearances_can_decrease_after_a_missing_competitor_is_known():
    market = CompactMarket()
    day = market.days[8]
    partial_day = market.days[3]
    market.records[partial_day][19]['amount'] = None
    top, _ = decorate(market, day)
    tenth = top[9]['active_top10_7d']
    assert tenth['count'] == 7 and tenth['state'] == 'observed_partial'
    assert '已观察7次' in tenth['label'] and '至少' not in tenth['label']
    # Completing an unknown competitor moves the old tenth-ranked stock to
    # eleventh. Ranking occurrences are not a monotone lower bound.
    market.records[partial_day][19]['amount'] = 2000
    market.snapshot = 'completed-synthetic-amount-generation'
    top, _ = decorate(market, day)
    tenth = top[9]['active_top10_7d']
    assert tenth['count'] == 6 and tenth['complete'] is True
    assert tenth['state'] == 'complete' and partial_day not in tenth['dates']


def test_directions_use_only_today_repeated_stocks_and_dated_membership_not_historical_backfill():
    market = CompactMarket()
    day = market.days[8]
    topics = [
        {'topic_id': 'valid', 'name': '可核验方向', 'kind': 'concept', 'observed_date': market.days[7],
         'instrument_ids': ['600000.SH', '600001.SH'], 'members_complete': True},
        {'topic_id': 'future', 'name': '未来成员', 'observed_date': market.days[9],
         'instrument_ids': ['600000.SH'], 'members_complete': True},
        {'topic_id': 'unknown', 'name': '日期未知成员', 'instrument_ids': ['600000.SH'], 'members_complete': True},
    ]
    membership = ({'topics': topics}, {'selected_date_available': True, 'source_snapshot_id': 'member-fixture', 'source_snapshot_day': day})
    top, summary = decorate(market, day, membership)
    direction = summary['directions']['items'][0]
    assert [item['topic_id'] for item in summary['directions']['items']] == ['valid']
    assert direction['stock_count'] == 2 and direction['observed_date'] == market.days[7]
    assert direction['items'][0]['dates'] == market.days[2:9]
    assert summary['directions']['state'] == 'partial', 'unmapped repeated stocks remain explicit'
    assert '600002.SH' in summary['directions']['unmapped_instrument_ids']
    assert '不把成员回填' in direction['scope']
    # Membership is never cached with ranking facts; a fresh resolved view is
    # used even if an independently amended source retains the same market ID.
    topics[0]['name'] = '已修订方向'
    _, again = decorate(market, day, membership)
    assert again['directions']['items'][0]['name'] == '已修订方向'
    assert len(market.calls) == 1
    top[0]['active_top10_7d']['ranks'][0]['rank'] = 999
    again, _ = decorate(market, day, membership)
    assert again[0]['active_top10_7d']['ranks'][0]['rank'] == 1


def test_bounded_cache_keys_include_snapshot_asof_and_return_only_past_facts():
    market = CompactMarket(days(40))
    for day in market.days:
        decorate(market, day)
    assert len(activity._cache) == activity.CACHE_LIMIT == 32
    count = len(market.calls)
    decorate(market, market.days[-1])
    assert len(market.calls) == count
    market.snapshot = 'another-synthetic-generation'
    decorate(market, market.days[-1])
    assert len(market.calls) == count + 1


def test_cache_confirms_instance_identity_when_numeric_ids_are_reused(monkeypatch):
    # Deterministically exercise the collision rather than depending on a
    # particular Python allocator to immediately reuse a collected object's ID.
    monkeypatch.setattr(activity, 'id', lambda _: 42, raising=False)
    first = CompactMarket()
    day = first.days[8]
    first.records[first.days[3]][0]['amount'] = 0
    top, _ = decorate(first, day)
    assert top[0]['active_top10_7d']['count'] == 6
    second = CompactMarket()
    again, _ = decorate(second, day)
    assert again[0]['active_top10_7d']['count'] == 7
    assert len(second.calls) == 1, 'matching numeric ID/snapshot must not reuse another live instance'
    # Returning the first instance must likewise recompute rather than reading
    # the second instance's now-colliding cache entry.
    first_again, _ = decorate(first, day)
    assert first_again[0]['active_top10_7d']['count'] == 6


def test_rank_cache_does_not_keep_full_market_instances_alive(monkeypatch):
    monkeypatch.setattr(activity, 'id', lambda _: 77, raising=False)
    market = CompactMarket()
    day = market.days[8]
    market.records[market.days[3]][0]['amount'] = 0
    top, _ = decorate(market, day)
    assert top[0]['active_top10_7d']['count'] == 6
    reference = weakref.ref(market)
    del market
    gc.collect()
    assert reference() is None, 'tiny rank facts must not retain the full historical market frame'
    replacement = CompactMarket()
    top, _ = decorate(replacement, day)
    assert top[0]['active_top10_7d']['count'] == 7
    assert len(replacement.calls) == 1


def test_wrong_history_generation_and_invalid_calendar_cannot_claim_complete():
    market = CompactMarket()
    original = market.amount_top10_history
    market.amount_top10_history = lambda dates: {**original(dates), 'snapshot_id': 'future-generation'}
    top, summary = decorate(market, market.days[8])
    assert top[0]['active_top10_7d']['count'] == 1 and not summary['complete']
    bad = CompactMarket()
    bad.days.append(bad.days[-1])
    top, summary = decorate(bad, bad.days[8])
    assert not summary['complete'] and top[0]['active_top10_7d']['count'] == 1
    assert '交易日历未核验' in summary['reason']


def test_empty_current_table_does_not_load_history_or_membership():
    market = CompactMarket()
    day = market.days[-1]
    market.records[day] = []
    top, summary = decorate(market, day)
    assert top == [] and summary['directions']['state'] == 'unknown'
    assert not market.calls


def test_malformed_identities_do_not_create_confirmed_rankings_or_force_directions():
    fact = amount_ranking_fact([{'instrument_id': {'bad': 'identity'}, 'amount': 100}], days(1)[0])
    assert fact['items'] == [] and fact['complete'] is False
    market = CompactMarket()
    day = market.days[8]
    invalid_topics = [
        {'topic_id': {'bad': 'identity'}, 'name': '无效身份', 'observed_date': day, 'instrument_ids': ['600000.SH']},
        {'topic_id': 'bad-name', 'name': {'bad': 'name'}, 'observed_date': day, 'instrument_ids': ['600000.SH']},
    ]
    _, summary = decorate(market, day, ({'topics': invalid_topics}, {'selected_date_available': True}))
    assert summary['directions']['items'] == [] and summary['directions']['state'] == 'unknown'


def test_market_data_compact_projection_has_the_same_tie_order_without_indicator_panels(monkeypatch):
    market = object.__new__(MarketData)
    market._lock = threading.RLock()
    calendar = days(8)
    records = []
    for day in calendar:
        records.extend(rows(day))
    # The published frame supplies source order; equal amounts retain it.
    records[0]['amount'] = records[1]['amount']
    frame = pd.DataFrame(records)
    monkeypatch.setattr(market, '_load', lambda: {'snapshot_id': 'compact-fixture'})
    monkeypatch.setattr(market, 'calendar', lambda: calendar)
    monkeypatch.setattr(market, '_daily_frame', lambda: frame)
    monkeypatch.setattr(market, 'panel', lambda *_: pytest.fail('no indicators'))
    result = market.amount_top10_history(calendar[:7])
    assert result['snapshot_id'] == 'compact-fixture'
    assert set(result['days']) == set(calendar[:7]) and calendar[-1] not in result['days']
    expected = ranked_amount_rows(records[:20])
    assert [row['instrument_id'] for row in result['days'][calendar[0]]['items']] == [row['instrument_id'] for row in expected]
