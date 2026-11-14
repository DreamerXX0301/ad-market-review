"""Synthetic provider fixtures only; no real quote collection or publication."""
from datetime import datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace

import pytest

from app.adapters.providers import ProviderError
from app.services import intraday_monitor_capture as capture
from app.services.noon_collection import CHINA, canonical

DAY = '2026-11-13'
SCHEDULED = datetime(2026, 11, 13, 9, 40, tzinfo=CHINA)
FIRST = '600001.SH'
SECOND = '600002.SH'


def quote(identifier=FIRST, **changes):
    return {**dict(thscode=identifier, last_price=11., prev_price=10., open_price=10.,
                   high_price=12., low_price=9., price_change=1., price_change_ratio_pct=10.,
                   volume=100., turnover=1100.), **changes}


def payload(ids, stamp=None):
    return dict(code=0, data=dict(item=[quote(identifier) for identifier in ids],
                                timestamp=int((stamp or SCHEDULED).timestamp() * 1000)))


class Clock:
    def __init__(self, value=SCHEDULED):
        self.value = value

    def now(self):
        return self.value


class Adapter:
    key = 'fixture-secret-never-persist'

    def __init__(self, handler=None):
        self.handler = handler
        self.calls = []

    def request(self, endpoint, params, **options):
        assert endpoint == capture.ENDPOINT
        assert options['max_attempts'] == 1
        options['before_attempt']()
        assert 0 < options['timeout'].read <= 5
        assert options['timeout'].connect == options['timeout'].read
        ids = params['thscodes'].split(',')
        self.calls.append((ids, options))
        return self.handler(ids, options) if self.handler else payload(ids)


def collect(ids, adapter=None, clock=None, guard=lambda: None, **changes):
    clock = clock or Clock()
    return capture.collect_quotes(ids, day=DAY, scheduled_at=SCHEDULED,
                                  deadline_at=SCHEDULED + timedelta(seconds=120),
                                  adapter=adapter or Adapter(), guard=guard, now=clock.now, **changes)


def test_selected_pool_batches_once_and_preserves_batch_only_time_and_canonical_sha():
    identifiers = [f'{index:06}.SZ' for index in range(1, 784)]
    adapter = Adapter()
    result = collect(identifiers, adapter)
    assert len(adapter.calls) == result['request_count'] == 8
    assert [len(ids) for ids, _ in adapter.calls] == [100] * 7 + [83]
    assert result['quality'] == 'complete' and not result['gaps']
    assert list(result['quotes_by_id']) == identifiers
    for source in result['sources']:
        assert source['sha256'] == sha256(canonical(source['payload'])).hexdigest()
        assert source['state'] == 'accepted'
        for identifier in source['requested_ids']:
            row = result['quotes_by_id'][identifier]
            assert row['source_sha256'] == source['sha256']
            assert row['valid'] and row['quality'] == 'batch_time_only'
            assert row['quote_time_ms'] is None
            assert row['received_at_ms'] == source['received_at_ms']
            assert row['source_time_ms'] == source['source_time_ms']
            assert row['price_basis'] == 'none' and row['previous_close'] == 10


def test_maximum_pool_is_20_requests_and_deduplication_does_not_repeat_network():
    ids = [f'{index:06}.SZ' for index in range(1, 2001)]
    result = collect(ids)
    assert result['request_count'] == 20 and len(result['quotes_by_id']) == 2000
    adapter = Adapter()
    assert len(collect([FIRST, FIRST, SECOND], adapter)['quotes_by_id']) == 2
    assert adapter.calls[0][0] == [FIRST, SECOND]


@pytest.mark.parametrize('identifiers', [None, FIRST, ['600001.sh'], ['881101.TI'],
                                         ['000001.SH?extra'], [FIRST] * 2001])
def test_invalid_or_oversized_pool_is_rejected_without_request(identifiers):
    adapter = Adapter()
    with pytest.raises(ValueError):
        collect(identifiers, adapter)
    assert not adapter.calls


@pytest.mark.parametrize('stamp', [None, SCHEDULED - timedelta(days=1),
                                  SCHEDULED - timedelta(seconds=121),
                                  SCHEDULED + timedelta(seconds=6)])
def test_missing_wrong_day_old_and_future_batch_time_are_unknown(stamp):
    body = payload([FIRST], stamp)
    if stamp is None:
        body['data'].pop('timestamp')
    result = collect([FIRST], Adapter(lambda *_: body))
    row = result['quotes_by_id'][FIRST]
    assert not row['valid'] and row['latest_price'] is None and row['quote_time_ms'] is None
    assert result['quality'] == 'partial' and result['gaps'][0]['instrument_id'] == FIRST
    assert len(result['sources']) == 1


def test_edge_batch_timestamp_and_timestamp_never_becomes_individual_time():
    result = collect([FIRST], Adapter(lambda ids, _: payload(ids, SCHEDULED - timedelta(seconds=120))))
    row = result['quotes_by_id'][FIRST]
    assert row['valid'] and row['source_time_ms'] == int(SCHEDULED.timestamp() * 1000) - 120_000
    assert row['quote_time_ms'] is None


@pytest.mark.parametrize('stamp', [True, '1794534000000', 1e30, 1794534000000.5])
def test_invalid_batch_epoch_never_reaches_integer_timestamp_fields(stamp):
    body = payload([FIRST])
    body['data']['timestamp'] = stamp
    result = collect([FIRST], Adapter(lambda *_: body))
    assert not result['quotes_by_id'][FIRST]['valid']
    assert result['quotes_by_id'][FIRST]['source_time_ms'] is None
    assert result['sources'][0]['source_time_ms'] is None


@pytest.mark.parametrize('changes', [dict(last_price=True), dict(last_price='11'),
                                     dict(last_price=0), dict(volume=0), dict(turnover=0),
                                     dict(volume=-1), dict(turnover=float('nan')),
                                     dict(high_price=10), dict(low_price=12),
                                     dict(open_price=8), dict(price_change_ratio_pct=9),
                                     dict(price_change=2), dict(prev_price=0)])
def test_invalid_values_ohlc_and_change_are_not_replaced_with_stale_prices(changes):
    body = payload([FIRST])
    body['data']['item'][0].update(changes)
    result = collect([FIRST], Adapter(lambda *_: body))
    row = result['quotes_by_id'][FIRST]
    assert row['valid'] is False and row['latest_price'] is None and row['quality'] == 'unknown'
    assert result['gaps'] and 'reason' in result['gaps'][0]


def test_duplicate_and_missing_rows_stay_unknown_without_hiding_valid_sibling():
    third = '600003.SH'
    body = payload([FIRST, SECOND])
    body['data']['item'].append(quote(SECOND))
    result = collect([FIRST, SECOND, third], Adapter(lambda *_: body))
    assert result['quotes_by_id'][FIRST]['valid']
    assert not result['quotes_by_id'][SECOND]['valid']
    assert not result['quotes_by_id'][third]['valid']
    assert result['sources'][0]['state'] == 'partial'
    assert {gap['instrument_id'] for gap in result['gaps']} == {SECOND, third}


@pytest.mark.parametrize('extra', [quote('000001.SZ'), {'last_price': 11}, None])
def test_unrequested_or_invalid_identity_rejects_the_batch(extra):
    body = payload([FIRST])
    body['data']['item'].append(extra)
    result = collect([FIRST], Adapter(lambda *_: body))
    assert not result['quotes_by_id'][FIRST]['valid']
    assert result['sources'][0]['state'] == 'unknown'


def test_late_response_is_not_persisted_and_later_batches_are_not_requested():
    clock = Clock()
    def handler(ids, _):
        body = payload(ids)
        clock.value = SCHEDULED + timedelta(seconds=120)
        return body
    adapter = Adapter(handler)
    result = collect([f'{index:06}.SZ' for index in range(1, capture.BATCH_SIZE + 2)], adapter, clock)
    assert len(adapter.calls) == 1 and not result['sources']
    assert len(result['gaps']) == capture.BATCH_SIZE + 1 and not any(row['valid'] for row in result['quotes_by_id'].values())
    assert result['stopped_reason']


def test_pacing_rechecks_deadline_before_transport_and_recomputes_timeout():
    clock = Clock(SCHEDULED + timedelta(seconds=117))
    seen = []
    def request(_endpoint, params, **options):
        clock.value += timedelta(seconds=2)
        options['before_attempt']()
        seen.append(options['timeout'].read)
        return payload(params['thscodes'].split(','), clock.value)
    result = collect([FIRST], SimpleNamespace(request=request), clock)
    assert result['quotes_by_id'][FIRST]['valid'] and seen == [1]

    clock.value = SCHEDULED + timedelta(seconds=119)
    result = collect([FIRST], SimpleNamespace(request=request), clock)
    assert result['request_count'] == 0 and not result['sources'] and result['stopped_reason']


@pytest.mark.parametrize('error', [ProviderError('secret must not leak', status=401),
                                   ProviderError('secret must not leak', status=403),
                                   ProviderError('secret must not leak', status=429),
                                   ProviderError('secret must not leak', code=4001)])
def test_auth_permission_and_rate_errors_stop_later_batches_without_logging_body(error):
    def handler(*_):
        raise error
    adapter = Adapter(handler)
    result = collect([f'{index:06}.SZ' for index in range(1, capture.BATCH_SIZE + 2)], adapter)
    assert len(adapter.calls) == 1 and len(result['gaps']) == capture.BATCH_SIZE + 1
    assert result['stopped_reason'] and 'secret must not leak' not in str(result)


def test_business_permission_response_stops_and_temporary_failure_continues_only_next_batch():
    identifiers = [f'{index:06}.SZ' for index in range(1, capture.BATCH_SIZE + 2)]
    adapter = Adapter(lambda *_: {'code': 2003, 'data': {}})
    result = collect(identifiers, adapter)
    assert len(adapter.calls) == 1 and len(result['gaps']) == capture.BATCH_SIZE + 1
    assert result['sources'][0]['state'] == 'rejected'

    def handler(ids, _):
        if len(ids) == capture.BATCH_SIZE:
            raise ProviderError('temporary', status=503)
        return payload(ids)
    adapter = Adapter(handler)
    result = collect(identifiers, adapter)
    assert len(adapter.calls) == 2 and len(result['gaps']) == capture.BATCH_SIZE
    assert result['quotes_by_id'][identifiers[-1]]['valid']


@pytest.mark.parametrize('kind', ['secret', 'capacity'])
def test_credential_echo_and_over_capacity_payloads_never_enter_evidence(kind):
    body = payload([FIRST])
    body['extra'] = Adapter.key if kind == 'secret' else 'x' * capture.MAX_RESPONSE_BYTES
    result = collect([FIRST], Adapter(lambda *_: body))
    assert not result['sources'] and not result['quotes_by_id'][FIRST]['valid']
    assert Adapter.key not in str(result)


def test_guard_pause_or_lost_lease_propagates_without_an_extra_request():
    class Stopped(Exception):
        pass
    adapter = Adapter()
    with pytest.raises(Stopped):
        collect([FIRST], adapter, guard=lambda: (_ for _ in ()).throw(Stopped()))
    assert not adapter.calls


def test_guard_is_rechecked_after_success_and_after_provider_failure():
    class Stopped(Exception):
        pass
    active = True
    def guard():
        if not active:
            raise Stopped()
    def handler(ids, _):
        nonlocal active
        active = False
        return payload(ids)
    with pytest.raises(Stopped):
        collect([FIRST], Adapter(handler), guard=guard)

    active = True
    def failed(*_):
        nonlocal active
        active = False
        raise ProviderError('temporary', status=503)
    with pytest.raises(Stopped):
        collect([FIRST], Adapter(failed), guard=guard)
