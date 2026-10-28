"""Synthetic source responses validate temporal and financial auction boundaries."""
from copy import deepcopy
from datetime import datetime, timedelta
import math

import pytest

from app.services.auction_analysis import CHINA, build_analysis, normalize_benchmark, normalize_snapshot

DAY = '2026-10-29'
RECEIVED = datetime(2026, 10, 29, 9, 26, tzinfo=CHINA)
CODES = ['600001.SH', '000001.SZ']


def quote(code=CODES[0], **changes):
    return {'thscode': code, 'ticker': code[:6], 'name': '合成测试证券',
            'auction_price': 10.5, 'pre_close_price': 10, 'auction_pct': 5,
            'auction_volume': 100, 'auction_amount': 105000,
            'auction_turnover_pct': .2, 'auction_yesterday_ratio_pct': 12.5,
            'auction_volume_ratio': 1.3, **changes}


def envelope(values=None, **changes):
    values = [quote()] if values is None else values
    return {'code': 0, 'data': {'timestamp': int(RECEIVED.timestamp() * 1000),
            'auction_phase': 'final', 'data_status': 'ready', 'item': values, 'total': len(values), **changes}}


def normalize(body, codes=None):
    return normalize_snapshot(body, codes or [CODES[0]], DAY, RECEIVED)


def test_price_pct_lot_amount_and_percent_values_reconcile_without_scaling():
    body = envelope()
    frozen = deepcopy(body)
    value = normalize(body)
    row = value['items'][0]
    assert body == frozen
    assert value['state'] == 'observed'
    assert row['auction_amount'] == 105000
    assert row['auction_volume'] == 100
    assert row['auction_pct'] == 5
    assert row['auction_yesterday_ratio_pct'] == 12.5
    assert row['unit_check']['state'] == 'verified'
    assert row['quote_at'] is None
    assert value['source_timestamp']['meaning'] == 'response_assembly_time'
    assert value['quote_as_of'] is None
    assert value['summary']['median_pct'] == 5


@pytest.mark.parametrize('phase,status', [('closed', 'not_ready'), ('final', 'not_ready'),
                                         ('live', 'ready'), ('closed', 'ready'), ('', 'ready')])
def test_nonempty_not_ready_values_never_become_observed(phase, status):
    value = normalize(envelope(auction_phase=phase, data_status=status))
    assert value['state'] == 'unknown'
    assert all(value['items'][0][field] is None for field in ('auction_price', 'auction_amount', 'auction_pct'))
    assert value['summary']['valid'] == 0
    assert value['summary']['up'] is None
    assert value['summary']['amount'] is None


@pytest.mark.parametrize('stamp', [None, True, math.nan, -1, 10**1000,
    int((RECEIVED - timedelta(days=1)).timestamp() * 1000),
    int((RECEIVED + timedelta(seconds=6)).timestamp() * 1000),
    int(RECEIVED.replace(hour=9, minute=24).timestamp() * 1000),
    int(RECEIVED.replace(hour=9, minute=30).timestamp() * 1000)])
def test_assembly_time_must_be_current_and_in_final_capture_window(stamp):
    assert normalize(envelope(timestamp=stamp))['state'] == 'unknown'


def test_response_received_after_open_cannot_become_a_preopen_snapshot():
    value = normalize_snapshot(envelope(), [CODES[0]], DAY, RECEIVED.replace(minute=30))
    assert value['state'] == 'unknown'


def test_unknown_quote_and_true_zero_are_distinct():
    values = [quote(auction_price=10, auction_pct=0, auction_amount=0, auction_volume=0),
              quote(CODES[1], auction_price=0, auction_pct=0)]
    value = normalize(envelope(values), CODES)
    assert value['state'] == 'partial'
    assert value['summary']['valid'] == 1
    assert value['summary']['missing'] == 1
    assert value['summary']['flat'] == 1
    assert value['summary']['up'] == 0
    assert value['summary']['amount'] == 0
    assert value['items'][1]['auction_pct'] is None


def test_duplicate_missing_and_unrequested_rows_do_not_pick_an_arbitrary_quote():
    value = normalize(envelope([quote(), quote(), quote('300001.SZ')]), CODES)
    assert [row['instrument_id'] for row in value['items']] == CODES
    assert value['summary']['missing'] == 2
    assert '重复' in value['items'][0]['reason']
    assert '未返回' in value['items'][1]['reason']
    assert any('未请求' in gap for gap in value['gaps'])


def test_total_mismatch_retains_verified_partial_sample_but_never_claims_complete():
    value = normalize(envelope(total=5000))
    assert value['state'] == 'partial'
    assert value['summary']['valid'] == 1
    assert '全市场' in value['summary']['scope']


@pytest.mark.parametrize('changes', [{'auction_pct': 50}, {'pre_close_price': None},
    {'ticker': '999999'}, {'auction_pct': True}, {'auction_price': math.inf},
    {'auction_price': 10**1000}, {'pre_close_price': 1e-300, 'auction_price': 1e300}])
def test_invalid_price_or_pct_does_not_pollute_summary(changes):
    value = normalize(envelope([quote(**changes)]))
    assert value['summary']['valid'] == 0
    assert value['items'][0]['auction_price'] is None


def test_lot_share_unit_mix_is_not_silently_corrected():
    value = normalize(envelope([quote(auction_amount=1050)]))
    row = value['items'][0]
    assert value['state'] == 'partial'
    assert row['state'] == 'observed'
    assert row['auction_amount'] is None and row['auction_volume'] is None
    assert row['unit_check']['state'] == 'mismatch'
    assert value['summary']['amount'] is None
    assert value['summary']['amount_valid'] == 0


def test_huge_products_and_missing_optional_fields_do_not_crash_or_imply_zero():
    value = normalize(envelope([quote(auction_volume=1e308, auction_amount=1e308,
                                     auction_volume_ratio=None, auction_turnover_pct=-1)]))
    assert value['items'][0]['unit_check']['state'] == 'mismatch'
    assert value['items'][0]['auction_volume_ratio'] is None
    assert value['items'][0]['auction_turnover_pct'] is None


def test_statistics_use_all_valid_candidates_and_exclude_unknowns():
    codes = [*CODES, '300001.SZ', '600002.SH']
    values = [quote(), quote(CODES[1], auction_price=9, auction_pct=-10, auction_amount=90000),
              quote('300001.SZ', auction_price=10, auction_pct=0, auction_amount=100000)]
    value = normalize(envelope(values), codes)
    assert value['summary'] | {} == {'requested': 4, 'valid': 3, 'missing': 1,
        'up': 1, 'down': 1, 'flat': 1, 'median_pct': 0, 'amount': 295000,
        'amount_valid': 3, 'scope': value['summary']['scope']}
    assert '未知1只' in build_analysis(value)[0]


@pytest.mark.parametrize('codes', [[], CODES * 51, [CODES[0]] * 2, ['600001'], [None], '600001.SH'])
def test_candidate_identity_and_request_budget_are_explicit(codes):
    with pytest.raises(ValueError):
        normalize_snapshot(envelope(), codes, DAY, RECEIVED)


def benchmark(values=None, **changes):
    return {'code': 0, 'data': {'date': DAY, 'date_ms': int(RECEIVED.replace(hour=0, minute=0).timestamp() * 1000),
            'item': values if values is not None else [{'thscode': CODES[0], 'auction_pct': 2, 'tags': ['高开']}], **changes}}


def test_benchmark_is_a_separate_dated_source_cohort():
    value = normalize_benchmark(benchmark(), DAY)
    assert value['state'] == 'observed'
    assert value['items'][0]['auction_pct'] == 2
    assert value['items'][0]['tags'] == ['高开']
    assert normalize_benchmark(benchmark([]), DAY)['state'] == 'unknown'
    assert normalize_benchmark(benchmark(date='2026-10-28'), DAY)['state'] == 'unknown'
    assert normalize_benchmark(benchmark(date_ms=0), DAY)['state'] == 'unknown'


def test_duplicate_or_invalid_benchmark_rows_are_not_called_an_empty_complete_market():
    values = [{'thscode': CODES[0], 'auction_pct': 2}, {'thscode': CODES[0], 'auction_pct': 9},
              {'thscode': CODES[1], 'auction_pct': -2, 'tags': 'not a list'}]
    value = normalize_benchmark(benchmark(values), DAY)
    assert value['state'] == 'partial'
    assert [row['instrument_id'] for row in value['items']] == [CODES[1]]
    assert value['items'][0]['tags'] == []


def test_invalid_envelopes_stay_unknown_without_mutating_input():
    for body in (None, [], {}, {'code': 403, 'data': envelope()['data']}, {'code': 0, 'data': []}):
        assert normalize(body)['state'] == 'unknown'
        assert normalize_benchmark(body, DAY)['state'] == 'unknown'
