"""Noon sentiment is a transparent display reference with explicit coverage."""
from copy import deepcopy
import json

import pytest

from app.services.noon_temperature import build_noon_temperature, temperature_label

DAY, PRIOR = '2026-10-28', '2026-10-27'
CODES = ('000001.SH', '399001.SZ', '399006.SZ', '000688.SH')


def pool(number, offset):
    return {'state': 'observed', 'count': number, 'items': [
        {'instrument_id': f'{600000 + offset + i:06}.SH', 'latest_price': 10}
        for i in range(number)]}


def snapshot():
    return {'date': DAY, 'calendar': {'verified': True, 'previous_day': PRIOR},
            'summary': {'breadth': {'up': 8, 'down': 2, 'flat': 0, 'valid': 10, 'total': 10},
                        'coverage': {'enumeration_complete': True}},
            'events': {'limit_up': pool(1, 0), 'limit_down': pool(1, 100), 'limit_break': pool(1, 200)},
            'previous_review': {'date': PRIOR, 'cohort_feedback': {
                'date': PRIOR, 'state': 'observed_sample', 'source_count': 4, 'observed_count': 4,
                'current_valid': 4, 'up': 3, 'down': 1, 'flat': 0}},
            'indices': [{'instrument_id': code, 'latest_price': 100, 'change_pct': 1} for code in CODES]}


def test_reference_is_reproducible_from_frozen_facts_with_visible_contributions():
    payload = snapshot()
    before = deepcopy(payload)
    result = build_noon_temperature(payload)
    assert result['version'] == 'noon-temperature-v1'
    assert result['state'] == 'available' and result['score'] == 70 and result['label'] == '偏暖'
    assert result['coverage'] == {'available_components': 5, 'total_components': 5, 'weight_covered': 100}
    assert [row['contribution'] for row in result['components']] == [20, 10, 10, 15, 15]
    assert '留存样本' in result['components'][3]['evidence']
    assert '不是官方指标' in result['scope'] and '不触发选股' in result['scope']
    assert payload == before
    assert json.dumps(result, ensure_ascii=False, allow_nan=False)


@pytest.mark.parametrize('warm,expected,label', [(False, 0, '偏冷'), (True, 100, '偏热')])
def test_real_zero_and_full_score_survive_without_truthiness_fallback(warm, expected, label):
    payload = snapshot()
    payload['summary']['breadth'].update(up=10 if warm else 0, down=0 if warm else 10)
    payload['events'] = {'limit_up': pool(2 if warm else 0, 0),
                         'limit_down': pool(0 if warm else 2, 100),
                         'limit_break': pool(0 if warm else 2, 200)}
    payload['previous_review']['cohort_feedback'].update(up=4 if warm else 0, down=0 if warm else 4)
    for row in payload['indices']:
        row['change_pct'] = 1 if warm else -1
    result = build_noon_temperature(payload)
    assert result['state'] == 'available'
    assert result['score'] == expected and result['label'] == label


@pytest.mark.parametrize('score,label', [(0, '偏冷'), (19.9, '偏冷'), (20, '偏弱'),
    (40, '中性'), (60, '偏暖'), (80, '偏热'), (100, '偏热'), (None, '数据不足'),
    (True, '数据不足'), (float('nan'), '数据不足'), (-1, '数据不足'), (101, '数据不足')])
def test_reference_band_boundaries_do_not_imply_a_trading_action(score, label):
    assert temperature_label(score) == label


def test_one_missing_input_keeps_weight_and_prevents_a_composite_score():
    payload = snapshot()
    payload['indices'][0]['change_pct'] = None
    result = build_noon_temperature(payload)
    assert result['score'] is None and result['state'] == 'unavailable'
    assert result['coverage']['weight_covered'] == 85
    assert result['coverage']['available_components'] == 4
    assert result['components'][-1]['value'] is None
    assert '不重新分配权重' in result['reason']


def test_complete_enumeration_and_quote_coverage_are_both_required():
    payload = snapshot()
    payload['summary']['coverage']['enumeration_complete'] = False
    assert build_noon_temperature(payload)['components'][0]['value'] is None
    payload['summary']['coverage']['enumeration_complete'] = True
    payload['summary']['breadth']['total'] = 11
    assert build_noon_temperature(payload)['components'][0]['value'] is None
    payload['summary']['breadth'].update(total=100, valid=95, up=76, down=19)
    assert build_noon_temperature(payload)['components'][0]['value'] == 80


@pytest.mark.parametrize('problem', ['duplicate', 'overlap', 'partial', 'mismatched_count', 'invalid_identity'])
def test_pool_conflicts_and_partial_capture_never_become_a_high_temperature(problem):
    payload = snapshot()
    up = payload['events']['limit_up']
    if problem == 'duplicate':
        up['items'].append(deepcopy(up['items'][0])); up['count'] = 2
    elif problem == 'overlap':
        payload['events']['limit_break']['items'] = deepcopy(up['items'])
    elif problem == 'partial':
        up['state'] = 'partial'
    elif problem == 'mismatched_count':
        up['count'] = 9
    else:
        up['items'][0]['instrument_id'] = 'not-a-stock'
    result = build_noon_temperature(payload)
    assert result['score'] is None
    assert result['components'][1]['value'] is None


def test_empty_pools_do_not_silently_mean_zero_or_full_strength():
    payload = snapshot()
    payload['events'] = {key: pool(0, 0) for key in ('limit_up', 'limit_down', 'limit_break')}
    result = build_noon_temperature(payload)
    assert result['components'][1]['value'] is None and result['components'][2]['value'] is None
    assert result['score'] is None


def test_previous_review_text_is_not_required_but_dated_event_coverage_is():
    payload = snapshot()
    feedback = {'date': PRIOR, 'state': 'partial', 'source_count': 100, 'verified_count': 95,
                'valid_count': 95, 'up': 76, 'down': 19, 'flat': 0}
    result = build_noon_temperature(payload, feedback)
    assert result['components'][3]['value'] == 80
    feedback['verified_count'] = feedback['valid_count'] = 94
    feedback.update(up=75, down=19)
    assert build_noon_temperature(payload, feedback)['score'] is None
    feedback.update(source_count=4, verified_count=4, valid_count=4, up=3, down=1, date='2026-10-26')
    assert build_noon_temperature(payload, feedback)['components'][3]['value'] is None


def test_indices_require_four_unique_dated_quotes_with_flat_as_half_support():
    payload = snapshot()
    payload['indices'][0]['change_pct'] = 0
    payload['indices'][1]['change_pct'] = -1
    assert build_noon_temperature(payload)['components'][4]['value'] == 62.5
    payload['indices'][0]['quote_date'] = PRIOR
    assert build_noon_temperature(payload)['components'][4]['value'] is None
    payload['indices'][0].pop('quote_date')
    payload['indices'].append(deepcopy(payload['indices'][0]))
    assert build_noon_temperature(payload)['components'][4]['value'] is None


def test_entirely_missing_or_invalid_counts_remain_unknown_and_serializable():
    result = build_noon_temperature(None)
    assert result['score'] is None and result['coverage']['available_components'] == 0
    assert all(row['value'] is None and row['reason'] for row in result['components'])
    payload = snapshot(); payload['summary']['breadth']['up'] = True
    assert build_noon_temperature(payload)['components'][0]['value'] is None
    assert json.dumps(result, ensure_ascii=False, allow_nan=False)
