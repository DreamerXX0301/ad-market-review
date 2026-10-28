"""Synthetic retained noon amounts exercise provenance and matching clocks."""
from copy import deepcopy
import pytest

from app.services.noon_comparison import build_index_comparisons

CODE = '000001.SH'


def snapshot():
    return {'date': '2026-10-28', 'calendar': {'verified': True, 'previous_day': '2026-10-27'},
            'collected_start_at': '2026-10-28T11:40:00+08:00',
            'collected_end_at': '2026-10-28T11:40:15+08:00', 'quote_as_of': None,
            'sources': [{'name': 'indices', 'source': 'fuyao', 'state': 'received',
                         'endpoint': '/api/a-share-index/prices/snapshot'}],
            'indices': [{'instrument_id': CODE, 'amount': 120, 'currency': 'CNY', 'quote_at': None}],
            'previous_review': {'date': '2026-10-27', 'previous_noon': {
                'date': '2026-10-27', 'state': 'available', 'calendar_verified': True,
                'snapshot_id': 'noon-fixture', 'content_sha256': 'a'*64,
                'recorded_at_ms': 1793072700000, 'source': 'fuyao',
                'collected_start_at': '2026-10-27T12:10:00+08:00',
                'collected_end_at': '2026-10-27T12:10:15+08:00', 'quote_as_of': None,
                'indices': [{'instrument_id': CODE, 'amount': 100, 'currency': 'CNY',
                             'quote_at': None, 'source': 'fuyao'}]}}}


def test_missing_individual_clocks_only_compares_two_retained_noon_snapshots():
    value = snapshot(); before = deepcopy(value)
    value['quote_as_of'] = '2026-10-28T11:30:00+08:00'
    value['previous_review']['previous_noon']['quote_as_of'] = '2026-10-27T11:30:00+08:00'
    baseline = deepcopy(value)
    result = build_index_comparisons(value)[CODE]
    assert result['state'] == 'snapshot_comparison' and result['label'] == '较昨日午评'
    assert result['ratio'] == pytest.approx(1.2) and result['change_pct'] == pytest.approx(20)
    assert result['direction'] == 'increase'
    assert value == baseline and before['indices'] == value['indices']


def test_individual_quote_clocks_prove_same_china_time_even_with_different_timezone_offsets():
    value = snapshot()
    value['indices'][0]['quote_at'] = '2026-10-28T03:30:00Z'
    value['previous_review']['previous_noon']['indices'][0]['quote_at'] = '2026-10-27T11:30:00+08:00'
    result = build_index_comparisons(value)[CODE]
    assert result['state'] == 'matched_period' and result['label'] == '昨日同期'
    assert '11:30:00' in result['scope'] and result['ratio'] == pytest.approx(1.2)


@pytest.mark.parametrize('amount,direction,ratio', [(0, 'decrease', 0), (80, 'decrease', .8), (100, 'flat', 1)])
def test_zero_current_amount_and_decrease_or_flat_are_not_missing(amount, direction, ratio):
    value = snapshot(); value['indices'][0]['amount'] = amount
    result = build_index_comparisons(value)[CODE]
    assert result['state'] == 'snapshot_comparison'
    assert result['direction'] == direction and result['ratio'] == ratio


@pytest.mark.parametrize('fault', ['zero_previous', 'missing_previous', 'negative_current', 'missing_current',
                                 'currency', 'missing_currency', 'wrong_source', 'missing_source',
                                 'source_conflict', 'failed_domain', 'wrong_day', 'wrong_row_day',
                                 'calendar_unknown', 'wrong_calendar_previous', 'unavailable',
                                 'wrong_code', 'duplicate_current', 'duplicate_previous',
                                 'outside_window', 'missing_window', 'bad_time', 'different_clocks',
                                 'wrong_quote_day', 'future_quote', 'overflow'])
def test_unprovable_comparisons_remain_unknown_without_a_fallback(fault):
    value = snapshot(); current = value['indices'][0]
    previous = value['previous_review']['previous_noon']; old = previous['indices'][0]
    if fault == 'zero_previous': old['amount'] = 0
    elif fault == 'missing_previous': old['amount'] = None
    elif fault == 'negative_current': current['amount'] = -1
    elif fault == 'missing_current': current['amount'] = None
    elif fault == 'currency': old['currency'] = 'USD'
    elif fault == 'missing_currency': current.pop('currency')
    elif fault == 'wrong_source': old['source'] = 'another-source'; previous['source'] = 'another-source'
    elif fault == 'missing_source': value['sources'] = []
    elif fault == 'source_conflict': current['source'] = 'another-source'
    elif fault == 'failed_domain': value['sources'][0]['state'] = 'failed'
    elif fault == 'wrong_day': previous['date'] = '2026-10-26'
    elif fault == 'wrong_row_day': current['date'] = '2026-10-27'
    elif fault == 'calendar_unknown': value['calendar']['verified'] = False
    elif fault == 'wrong_calendar_previous': value['calendar']['previous_day'] = '2026-10-26'
    elif fault == 'unavailable': previous['state'] = 'unavailable'
    elif fault == 'wrong_code': old['instrument_id'] = '399001.SZ'
    elif fault == 'duplicate_current': value['indices'].append(dict(current))
    elif fault == 'duplicate_previous': previous['indices'].append(dict(old))
    elif fault == 'outside_window': value['collected_end_at'] = '2026-10-28T13:00:00+08:00'
    elif fault == 'missing_window': previous.pop('collected_start_at')
    elif fault == 'bad_time': current['quote_at'] = 'not-a-clock'
    elif fault == 'different_clocks': current['quote_at'] = '2026-10-28T11:29:00+08:00'; old['quote_at'] = '2026-10-27T11:30:00+08:00'
    elif fault == 'wrong_quote_day': old['quote_at'] = '2026-10-26T11:30:00+08:00'
    elif fault == 'future_quote': current['quote_at'] = '2026-10-28T14:00:00+08:00'
    elif fault == 'overflow': current['amount'] = 1e308; old['amount'] = 1e-308
    result = build_index_comparisons(value)[CODE]
    assert result['state'] == 'unavailable'
    assert result['ratio'] is None and result['change_pct'] is None and result['direction'] is None
    assert result['reason']


def test_one_missing_clock_can_only_be_a_snapshot_comparison():
    value = snapshot(); value['indices'][0]['quote_at'] = '2026-10-28T11:30:00+08:00'
    assert build_index_comparisons(value)[CODE]['state'] == 'snapshot_comparison'


def test_unstructured_or_old_payload_without_a_frozen_previous_noon_does_not_query_anything():
    assert build_index_comparisons(None) == {}
    value = snapshot(); value['previous_review'].pop('previous_noon')
    assert build_index_comparisons(value)[CODE]['state'] == 'unavailable'
