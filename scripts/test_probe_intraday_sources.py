"""盘中探针的合成契约测试；不访问真实数据源或凭据。"""
from datetime import datetime, timedelta
import json
import pytest

from probe_intraday_sources import CHINA, Probe, compare_quotes, inspect_quotes, timestamp_state

NOW = datetime(2026, 10, 28, 11, 50, tzinfo=CHINA)
DAY = NOW.date().isoformat()


def fixture_quote(code='600519.SH', **extra):
    return {'thscode': code, 'last_price': 11, 'prev_price': 10, 'open_price': 10,
            'high_price': 11, 'low_price': 10, 'price_change': 1,
            'price_change_ratio_pct': 10, 'volume': 100, 'turnover': 1100, **extra}


def fixture_snapshot(rows, stamp=NOW):
    return {'code': 0, 'data': {'timestamp': int(stamp.timestamp() * 1000), 'item': rows}}


def test_today_envelope_and_stable_prices_do_not_prove_individual_quote_time():
    first = fixture_snapshot([fixture_quote()])
    second = fixture_snapshot([fixture_quote()], NOW + timedelta(seconds=30))
    result = inspect_quotes(first, DAY, NOW, ['600519.SH'])
    assert result['state'] == 'sample_observed'
    assert result['source_timestamp']['state'] == 'target_date'
    assert result['rows_with_any_time_field'] == 0
    assert result['quote_as_of'] is None and not result['individual_quote_dates_verified']
    comparison = compare_quotes(first, second)
    assert comparison['source_timestamp_changed'] and comparison['changed_quote_rows'] == 0
    assert comparison['quote_as_of'] is None


def test_stale_future_missing_and_partial_quotes_remain_explicit():
    assert timestamp_state(None, DAY, NOW)['state'] == 'missing_or_invalid'
    assert timestamp_state((NOW - timedelta(days=1)).timestamp() * 1000, DAY, NOW)['state'] == 'different_date'
    assert timestamp_state((NOW + timedelta(seconds=60)).timestamp() * 1000, DAY, NOW)['state'] == 'future'
    result = inspect_quotes(fixture_snapshot([fixture_quote(), fixture_quote()]), DAY, NOW, ['600519.SH', '000001.SZ'])
    assert result['state'] == 'partial'
    assert result['duplicate_codes'] == ['600519.SH']
    assert result['missing_codes'] == ['000001.SZ']
    stale = inspect_quotes(fixture_snapshot([fixture_quote()], NOW - timedelta(days=1)), DAY, NOW)
    future = inspect_quotes(fixture_snapshot([fixture_quote()], NOW + timedelta(seconds=60)), DAY, NOW)
    assert stale['state'] == future['state'] == 'partial'


class FixtureAdapter:
    def __init__(self, pages):
        self.pages, self.calls = iter(pages), []

    def request(self, path, params):
        self.calls.append((path, params))
        return next(self.pages)


def test_changed_page_total_or_duplicate_codes_reject_complete_enumeration(tmp_path):
    first = fixture_snapshot([fixture_quote(f'{n:06d}.SH') for n in range(1000)])
    first['data']['total'] = 1001
    changed = fixture_snapshot([fixture_quote('001000.SH')])
    changed['data']['total'] = 1002
    probe = Probe(FixtureAdapter([first, changed]), tmp_path / 'changed', DAY, now=lambda: NOW)
    result = probe.paged('market', '/api/a-share/prices/snapshot', {}, market=True)
    assert not result['enumeration_complete'] and 'total_changed' in result['failures']
    duplicate = fixture_snapshot([fixture_quote(), fixture_quote()])
    duplicate['data']['total'] = 2
    probe = Probe(FixtureAdapter([duplicate]), tmp_path / 'duplicate', DAY, now=lambda: NOW)
    result = probe.paged('market', '/api/a-share/prices/snapshot', {}, market=True)
    assert not result['enumeration_complete'] and 'duplicate_identity' in result['failures']


def test_enumeration_complete_does_not_mean_valid_or_dated_quote_coverage(tmp_path):
    payload = fixture_snapshot([fixture_quote(last_price=None)])
    payload['data']['total'] = 1
    probe = Probe(FixtureAdapter([payload]), tmp_path / 'coverage', DAY, now=lambda: NOW)
    result = probe.paged('market', '/api/a-share/prices/snapshot', {}, market=True)
    assert result['enumeration_complete']
    assert result['quotes']['positive_price_rows'] == 0
    assert result['quotes']['state'] == 'partial'
    assert result['current_date_quote_coverage'] == 'unknown'


def test_empty_pool_is_unverified_and_never_claims_zero_events(tmp_path):
    payload = {'code': 0, 'data': {'timestamp': int(NOW.timestamp() * 1000), 'item': [],
                                 'pagination': {'total': 0, 'pages': 0, 'page': 1, 'size': 200}}}
    probe = Probe(FixtureAdapter([payload]), tmp_path / 'empty', DAY, now=lambda: NOW)
    result = probe.paged('pool', '/api/a-share/special-data/limit-up-pool', {'date_ms': 1})
    assert result['state'] == 'empty_unverified' and not result['enumeration_complete']


def test_error_or_echoed_credential_never_writes_secret(tmp_path):
    secret = 'fixture-private-key'
    probe = Probe(FixtureAdapter([{'code': 0, 'data': {'echo': secret}}]), tmp_path / 'secret', DAY,
                  now=lambda: NOW, secret=secret)
    assert probe.request('secret', '/api/a-share/prices/snapshot', {}) is None
    assert list((probe.output / 'responses').iterdir()) == []
    assert secret not in json.dumps(probe.report)


@pytest.mark.parametrize('location', ['value', 'key'])
def test_json_escaped_credential_is_rejected_before_serialization(tmp_path, location):
    secret = 'fixture-"quoted\\credential'
    nested = {'nested': [{'echo': 'prefix ' + secret + ' suffix'}]} if location == 'value' else {'nested': [{secret: 1}]}
    probe = Probe(FixtureAdapter([{'code': 0, 'data': nested}]), tmp_path / location, DAY,
                  now=lambda: NOW, secret=secret)
    assert probe.request('echo', '/api/a-share/prices/snapshot', {}) is None
    assert list((probe.output / 'responses').iterdir()) == []
    assert secret not in json.dumps(json.loads((probe.output / 'report.json').read_text()))


def test_complete_run_preserves_independent_pool_and_statistics_counts(tmp_path):
    from probe_intraday_sources import SAMPLES
    samples = [fixture_snapshot([fixture_quote(code) for code in codes]) for _, codes in SAMPLES.values()]
    pools = [{'code': 0, 'data': {'timestamp': int(NOW.timestamp() * 1000), 'item': [fixture_quote()],
                                'pagination': {'total': 1, 'pages': 1, 'page': 1, 'size': 200}}} for _ in range(3)]
    stats = {'code': 0, 'data': {'trade_date': DAY, 'limit_up_stock_count': 2,
                                'open_stock_count': 1, 'touched_limit_up_stock_count': 3}}
    hot = {'code': 0, 'data': {'item': [{'thscode': '600519.SH', 'rank': 1}]}}
    adapter = FixtureAdapter(samples + pools + [stats, hot])
    probe = Probe(adapter, tmp_path / 'run', DAY, now=lambda: NOW)
    report = probe.run()
    assert report['logical_requests'] == 8
    assert report['pool_statistics_comparison']['counts_equal'] is False
    assert report['pool_statistics_comparison']['pool_total'] == 1
    assert report['capabilities']['limit_statistics']['limit_up_stock_count'] == 2
    assert json.loads((probe.output / 'report.json').read_text())['quote_as_of'] is None


def test_invalid_numeric_domains_and_unexpected_data_shapes_are_safe():
    payload = fixture_snapshot([fixture_quote(volume=-1), fixture_quote(prev_price=0), fixture_quote(turnover=-1)])
    result = inspect_quotes(payload, DAY, NOW)
    assert result['state'] == 'partial' and result['invalid_value_rows'] == 3
    assert compare_quotes({'data': None}, {'data': []})['common_codes'] == 0
    assert inspect_quotes({'data': []}, DAY, NOW)['state'] == 'unknown'


def test_price_amount_and_ohlc_invariants_use_source_precision_tolerance():
    mismatch = inspect_quotes(fixture_snapshot([fixture_quote(price_change=2), fixture_quote(open_price=12)]), DAY, NOW)
    assert mismatch['state'] == 'partial'
    assert mismatch['price_amount_mismatch_rows'] == 1 and mismatch['ohlc_mismatch_rows'] == 1
    rounded = inspect_quotes(fixture_snapshot([fixture_quote(price_change=1.01, high_price=10.99)]), DAY, NOW)
    assert rounded['price_amount_mismatch_rows'] == rounded['ohlc_mismatch_rows'] == 0


def test_checkpoint_retains_partial_run_on_unexpected_interruption_and_refuses_existing_output(tmp_path):
    output = tmp_path / 'interrupted'
    probe = Probe(FixtureAdapter([fixture_snapshot([fixture_quote()])]), output, DAY, now=lambda: NOW)
    probe.request('first', '/api/a-share/prices/snapshot', {})
    with pytest.raises(StopIteration):
        probe.request('second', '/api/a-share/prices/snapshot', {})
    saved = json.loads((output / 'report.json').read_text())
    assert saved['execution_state'] == 'running' and len(saved['requests']) == 2
    assert saved['requests'][0]['state'] == 'received' and saved['requests'][1]['state'] == 'interrupted'
    with pytest.raises(FileExistsError):
        Probe(FixtureAdapter([]), output, DAY, now=lambda: NOW)
