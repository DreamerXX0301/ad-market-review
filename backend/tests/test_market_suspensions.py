from app.services import market_suspension_capture as capture
from app.services import market_suspensions as suspensions


def _row(code, start, end):
    return {'SECUCODE': code, 'SECURITY_CODE': code[:6], 'SECURITY_TYPE_CODE': '058001001',
            'SUSPEND_START_TIME': start, 'SUSPEND_END_TIME': end, 'SUSPEND_EXPIRE': '连续停牌'}


def test_dated_public_list_may_contain_future_and_partial_sessions():
    rows = [_row('002129.SZ', '2026-11-02 09:30:00', '2026-11-13 15:00:00'),
            _row('002130.SZ', '2026-11-06 09:30:00', '2026-11-13 15:00:00'),
            _row('002131.SZ', '2026-11-05 10:30:00', '2026-11-05 15:00:00'),
            _row('002132.SZ', '2026-11-05 09:30:00', '2026-11-05 13:00:00')]
    body = {'code': 0, 'success': True, 'result': {'pages': 1, 'count': len(rows), 'data': rows}}
    evidence = capture.dated_records(body, '2026-11-05')
    assert [(row['instrument_id'], row['start_date'], row['end_date']) for row in evidence] == [
        ('002129.SZ', '2026-11-05', '2026-11-05')]
    body['result']['pages'] = 2
    assert capture.dated_records(body, '2026-11-05') is None


def test_last_real_close_cannot_cross_unknown_gap_or_conflicting_trade(monkeypatch):
    days = ['2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05']
    symbol = '002129.SZ'
    proof = {days[2], days[3]}
    monkeypatch.setattr(suspensions, 'suspended_on', lambda symbol, day: day in proof)
    index = {days[0]: {'close': 8.0, 'volume': 100}}
    assert suspensions.suspension_endpoint(index, days, days[3], symbol) is None
    index[days[1]] = {'close': 9.0, 'volume': 200}
    assert suspensions.suspension_endpoint(index, days, days[3], symbol) == days[1]
    index[days[2]] = {'close': 10.0, 'volume': 200}
    assert suspensions.suspension_endpoint(index, days, days[3], symbol) is None


def test_auxiliary_worker_capture_requires_new_frozen_scope_and_deduplicates(monkeypatch):
    from datetime import datetime

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 5, 11, 35, tzinfo=tz)

    calls = []
    monkeypatch.setattr(capture, 'datetime', Clock)
    monkeypatch.setattr(capture, '_ATTEMPTED', set())
    monkeypatch.setattr(capture, 'capture_for_date', lambda day: calls.append(day) or True)
    job = {'target_date': '2026-11-05'}
    assert not capture.capture_after_market_snapshot(job, {})
    assert capture.capture_after_market_snapshot(job, {'suspension_evidence': suspensions.VERSION})
    assert not capture.capture_after_market_snapshot(job, {'suspension_evidence': suspensions.VERSION})
    assert calls == [job['target_date']]
