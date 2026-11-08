"""First boards enrich only the overview, from verified prior-day pool evidence."""
from copy import deepcopy

import pytest

from app.services import stock_memberships, market_service
from app.services.market_feedback import feedback_view
from test_stock_board_presentation import market
from test_market_ladder import B, OTHER, DAYS, TODAY


@pytest.fixture
def feedback(market, monkeypatch):
    market.ref['_supplement']['annual_pools'][DAYS[-2]]['items'][-1]['continue_day_cnt'] = 2
    market.ref['ladder'] = {'item':[{'date':DAYS[-2], 'boards':{'2':[{'thscode':OTHER,'board_num':2}], '1':[{'thscode':B,'board_num':1}]}}]}
    monkeypatch.setattr(stock_memberships, 'current_memberships', lambda actual, symbols, **kwargs: {
        'memberships':{symbol:[] for symbol in symbols}, 'scope':'fixture only'})
    return market


def build(market):
    return feedback_view(market, market.status(), market.ref, TODAY, {row['instrument_id']:row for row in market.rows()})


def test_only_overview_gets_verified_first_board_and_detail_keeps_multi_board_samples(feedback):
    original = deepcopy(feedback.ref)
    result = build(feedback)
    assert [row['instrument_id'] for row in result['feedback']] == [OTHER]
    assert [row['instrument_id'] for row in result['first_board_feedback']] == [B]
    row = result['first_board_feedback'][0]
    assert row['previous_board_date'] == DAYS[-2]
    assert row['board_presentation']['previous_height']['value'] == 1
    assert row['board_presentation']['previous_height']['state'] == 'confirmed'
    assert 'topics' not in row and 'industries' not in row
    assert result['first_board_feedback_coverage']['confirmed_count'] == 1
    assert feedback.ref == original


def test_missing_today_quote_is_unknown_return_not_zero_or_dropped_first_board(feedback):
    feedback.history[B] = feedback.history[B][:-1]
    result = build(feedback)
    assert result['first_board_feedback'][0]['instrument_id'] == B
    assert result['first_board_feedback'][0]['change_pct'] is None


def test_missing_prior_pool_does_not_infer_first_boards_from_ladder_absence(feedback):
    del feedback.ref['_supplement']['annual_pools'][DAYS[-2]]
    result = build(feedback)
    assert result['first_board_feedback'] == []
    assert result['first_board_feedback_coverage']['state'] == 'unknown'
    assert result['first_board_feedback_coverage']['source_available'] is False


@pytest.mark.parametrize('value',[True,'1',None,0,1.5])
def test_bad_prior_height_never_counts_as_first_board(feedback, value):
    feedback.ref['_supplement']['annual_pools'][DAYS[-2]]['items'][0]['continue_day_cnt'] = value
    result = build(feedback)
    assert result['first_board_feedback'] == []
    assert result['first_board_feedback_coverage']['unverified_count'] == 1


def test_conflicting_height_or_raw_price_keeps_first_board_unknown(feedback):
    old = feedback.ref['_supplement']['annual_pools'][DAYS[-2]]['items']
    old.append({**old[0], 'continue_day_cnt':2})
    assert build(feedback)['first_board_feedback'] == []
    old.pop()
    old[0]['last_price'] = 99
    result = build(feedback)
    assert result['first_board_feedback'] == []
    assert result['first_board_feedback_coverage']['unverified_count'] == 1


def test_known_empty_first_board_sample_does_not_become_missing_source(feedback):
    feedback.ref['_supplement']['annual_pools'][DAYS[-2]]['items'][0]['continue_day_cnt'] = 2
    result = build(feedback)
    assert result['first_board_feedback'] == []
    assert result['first_board_feedback_coverage']['state'] == 'ready'
    assert result['first_board_feedback_coverage']['confirmed_count'] == 0


def test_missing_calendar_keeps_first_board_scope_unknown_without_borrowing_a_date():
    result = feedback_view(object(), {'snapshot_id':'fixture'}, {}, TODAY, {})
    assert result['feedback'] == [] and result['first_board_feedback'] == []
    assert result['first_board_feedback_coverage']['previous_date'] is None
    assert result['first_board_feedback_coverage']['state'] == 'unknown'


def test_immutable_board_facts_hit_cache_and_manual_memberships_stay_live(feedback, monkeypatch, tmp_path):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    title = ['初始人工题材']
    monkeypatch.setattr(stock_memberships, 'current_memberships', lambda *_args, **_kwargs: {
        'memberships': {OTHER: [{'topic_id': 'manual', 'name': title[0], 'kind': 'concept'}]}, 'scope': 'fixture only'})

    def read(evidence):
        indexed = {row['instrument_id']: row for row in feedback.rows()}
        return feedback_view(feedback, feedback.status(), feedback.ref, TODAY, indexed, evidence=evidence)

    uncached = build(feedback)
    feedback.history_calls.clear()
    first = read('source-proof')
    assert first == uncached
    assert len(feedback.history_calls) == 2
    assert list(tmp_path.rglob('emotion-ladder-feedback-facts.json'))
    assert list(tmp_path.rglob('emotion-first-board-facts.json'))
    title[0] = '已更新人工题材'
    updated = read('source-proof')
    assert len(feedback.history_calls) == 2
    assert updated['feedback'][0]['topics'][0]['name'] == '已更新人工题材'
    assert updated['feedback'][0]['board_presentation'] == first['feedback'][0]['board_presentation']
    assert updated['first_board_feedback'] == first['first_board_feedback']
    assert read('changed-source-proof') == updated
    assert len(feedback.history_calls) == 4


def test_flow_no_longer_computes_feedback(monkeypatch, feedback):
    monkeypatch.setattr(market_service, 'panel', lambda *a, **k: (feedback, feedback.status(), TODAY, feedback.ref, feedback.rows()))
    monkeypatch.setattr(market_service, 'prior_ladder_feedback', lambda *a, **k: pytest.fail('flow cannot compute removed feedback'))
    monkeypatch.setattr(market_service, 'fund_flow_for_date', lambda *a: {'items':[]})
    monkeypatch.setattr(market_service, '_sectors_from_panel', lambda *a: {'items':[]})
    monkeypatch.setattr(market_service, 'supplement_status', lambda *a: {})
    monkeypatch.setattr(market_service, 'data_coverage', lambda *a: {})
    result = market_service.market_page('flow', TODAY)
    assert 'feedback' not in result and 'first_board_feedback' not in result
