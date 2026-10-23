"""Feedback uses classifications knowable at observation, never today's backfill."""
from types import SimpleNamespace

import pytest

from app.services import market_service, market_history, stock_memberships

DAYS = ['2026-10-21', '2026-10-22', '2026-10-23']
SYMBOL = '600001.SH'


def setup_feedback(monkeypatch, topics):
    market = SimpleNamespace(calendar=lambda: DAYS, status=lambda: {'data_end': DAYS[-1], 'snapshot_id': 'current-fixture'})
    reference = {'topics': topics, 'ladder': {'item': [{'date': day, 'boards': {'2': [{'thscode': SYMBOL, 'board_num': 2}]}} for day in DAYS[:-1]]}}
    indexed = {SYMBOL: {'instrument_id': SYMBOL, 'change_pct': -2, 'topics': [{'name': 'must be replaced'}]}}
    monkeypatch.setattr(market_history, '_archive_index', lambda: {'entries': [], 'listed_generations': set(), 'failure_count': 0, 'truncated': False})
    return market, reference, indexed


def test_historical_feedback_does_not_backfill_current_or_manual_topics(monkeypatch):
    market, reference, indexed = setup_feedback(monkeypatch, [{'topic_id': 'future', 'name': '未来题材', 'kind': 'concept', 'observed_date': DAYS[-1], 'instrument_ids': [SYMBOL]}])
    monkeypatch.setattr(stock_memberships, 'current_memberships', lambda *_args, **_kwargs: pytest.fail('historical view must never read current overrides'))
    row = market_service.prior_ladder_feedback(market, reference, DAYS[1], indexed)[0]
    assert row['topics'] == [] and row['industries'] == []
    assert row['topics_state'] == 'unknown' and row['previous_board_date'] == DAYS[0]


def test_exact_observed_memberships_keep_topics_industries_and_source_dates_separate(monkeypatch):
    topics = [{'topic_id': 'concept', 'name': '当日题材', 'kind': 'concept', 'observed_date': DAYS[1], 'instrument_ids': [SYMBOL]},
              {'topic_id': 'industry', 'name': '当日行业', 'kind': 'industry', 'observed_date': DAYS[1], 'instrument_ids': [SYMBOL]},
              {'topic_id': 'future', 'name': '未来题材', 'kind': 'concept', 'observed_date': DAYS[-1], 'instrument_ids': [SYMBOL]}]
    market, reference, indexed = setup_feedback(monkeypatch, topics)
    row = market_service.prior_ladder_feedback(market, reference, DAYS[1], indexed)[0]
    assert [tag['topic_id'] for tag in row['topics']] == ['concept']
    assert [tag['topic_id'] for tag in row['industries']] == ['industry']
    assert row['topics'][0]['source_observed_date'] == DAYS[1]
    assert row['topics_state'] == 'dated_partial'


def test_later_snapshot_does_not_interpolate_old_member_sample_into_missing_date(monkeypatch):
    market, reference, indexed = setup_feedback(monkeypatch, [{'topic_id': 'old', 'kind': 'concept', 'observed_date': DAYS[0], 'instrument_ids': [SYMBOL]}])
    row = market_service.prior_ladder_feedback(market, reference, DAYS[1], indexed)[0]
    assert row['topics'] == [] and row['topics_state'] == 'unknown'


def test_current_feedback_uses_shared_manual_memberships_and_expected_business_day(monkeypatch):
    market, reference, indexed = setup_feedback(monkeypatch, [])
    def current(actual, symbols, *, expected_day):
        assert actual is market and set(symbols) == {SYMBOL} and expected_day == DAYS[-1]
        return {'memberships': {SYMBOL: [{'topic_id': 'manual', 'name': '人工题材', 'kind': 'concept'}, {'topic_id': 'sector', 'name': '当前行业', 'kind': 'industry'}]}, 'scope': '当前人工归属，不代表历史上涨原因'}
    monkeypatch.setattr(stock_memberships, 'current_memberships', current)
    row = market_service.prior_ladder_feedback(market, reference, DAYS[-1], indexed)[0]
    assert [tag['topic_id'] for tag in row['topics']] == ['manual']
    assert [tag['topic_id'] for tag in row['industries']] == ['sector']
    assert row['topics_state'] == 'current_partial' and row['topics_observed_date'] == DAYS[-1]


def test_first_day_and_absent_ladder_do_not_request_classification(monkeypatch):
    market, reference, indexed = setup_feedback(monkeypatch, [])
    monkeypatch.setattr(stock_memberships, 'current_memberships', lambda *_args, **_kwargs: pytest.fail('no samples, no membership read'))
    assert market_service.prior_ladder_feedback(market, reference, DAYS[0], indexed) == []
    reference['ladder'] = {'item': []}
    assert market_service.prior_ladder_feedback(market, reference, DAYS[-1], indexed) == []
