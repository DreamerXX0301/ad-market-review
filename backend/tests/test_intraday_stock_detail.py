from contextlib import nullcontext

from fastapi import HTTPException
import pytest

from app.services import intraday_stock_detail as details


def test_early_history_remains_anchored_to_prior_close(monkeypatch):
    class Market:
        def status(self):
            return {'ready': True, 'snapshot_id': 'fixed', 'data_end': '2026-11-04'}

        def calendar(self):
            return ['2026-11-03', '2026-11-04']

        def instruments(self, **kwargs):
            return [{'instrument_id': symbol, 'kind': 'stock'} for symbol in ('688012.SH', '688013.SH', '688015.SH')]

        def reference(self):
            return {'pool_trade_date': '2026-11-04', 'limitup': [
                {'thscode': '688012.SH', 'date': '2026-11-04', 'last_price': 110, 'continue_day_cnt': 4},
                {'thscode': '688015.SH', 'date': '2026-11-04', 'last_price': 110, 'continue_day_cnt': 1},
            ], 'pool_history': {'2026-11-03': {'limitup': [
                {'thscode': '688012.SH', 'date': '2026-11-03', 'last_price': 100, 'continue_day_cnt': 3},
                {'thscode': '688013.SH', 'date': '2026-11-03', 'last_price': 99, 'continue_day_cnt': 2},
            ]}}}

        def history_for_instruments(self, symbols, *, start, end):
            return {symbol: [{'date': end, 'close': 100 if end == '2026-11-03' else 110,
                              'volume': 100, 'price_basis': 'none'}] for symbol in symbols}

    seen = []

    def metrics(market, day, symbols, master, quotes, supplement, **kwargs):
        seen.append((day, kwargs['rolling_start'], quotes['688012.SH']['date']))
        return {symbol: {'change_30d_pct': 5, 'limit_up_1y_observed': 2} for symbol in symbols}

    from app.services import supplement_access
    monkeypatch.setattr(supplement_access, 'supplement_access', nullcontext)
    monkeypatch.setattr(details, 'get_market', Market)
    monkeypatch.setattr(details, 'member_metrics', metrics)
    monkeypatch.setattr(details, 'load_supplement_with_fingerprint', lambda *args, **kwargs: ({}, 'fixed'))
    symbols = '688012.SH,688013.SH,688015.SH'
    early = details.read_history('2026-11-04', '1400', symbols)
    closing = details.read_history('2026-11-04', 'close', symbols)
    assert early['as_of'] == '2026-11-03'
    assert closing['as_of'] == '2026-11-04'
    assert early['items']['688012.SH']['limit_up_1y'] is None
    assert early['items']['688012.SH']['limit_up_1y_observed'] == 2
    for result in (early, closing):
        assert result['items']['688012.SH']['previous_board_count'] == 3
        assert result['items']['688013.SH']['previous_board_count'] is None  # Prior price conflict.
        assert result['items']['688015.SH']['previous_board_count'] is None  # Only today's height exists.
    assert seen == [('2026-11-03', '2025-11-04', '2026-11-03'), ('2026-11-04', '2025-11-04', '2026-11-04')]


def test_bad_history_and_quote_scope_rejected():
    for call in (
        lambda: details.read_history.__wrapped__('2026-11-04', '2500', '688012.SH'),
        lambda: details.read_history.__wrapped__('2026-11-04', '1400', '688012'),
        lambda: details.read_quote('noon_' + '1' * 32, '688012.SH'),
    ):
        with pytest.raises(HTTPException) as error:
            call()
        assert error.value.status_code == 422
