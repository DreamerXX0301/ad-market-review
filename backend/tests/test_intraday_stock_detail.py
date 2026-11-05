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
        return {symbol: {'change_30d_pct': 5, 'change_30d_coverage': {'start_suspension': True},
                         'return_ytd_coverage': {'end_suspension': True},
                         'limit_up_1y_observed': 2} for symbol in symbols}

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
        assert result['items']['688012.SH']['change_30d_coverage']['start_suspension']
        assert result['items']['688012.SH']['return_ytd_coverage']['end_suspension']
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


@pytest.mark.parametrize('quotes,suspended', [
    ([], True),
    ([{'amount': 0, 'volume': 0}], True),
    ([{'amount': 1, 'volume': 0}], False),
    ([{'amount': 0, 'volume': 1}], False),
    ([{}, {}], False),
])
def test_quote_explains_verified_halt_without_creating_a_candle(monkeypatch, quotes, suspended):
    symbol, day = '002129.SZ', '2026-11-05'
    class Connection:
        def execute(self, *args):
            return self
        def fetchone(self):
            return {'snapshot_id': 'retained'}
    class Market:
        def calendar(self):
            return ['2026-11-04', day]
    snapshot = {'date': day, 'slot_label': '上午观察', 'collected_end_at': day + 'T11:00:30+08:00',
                'comparison_input': {'stocks': [{'instrument_id': symbol, **quote} for quote in quotes]}}
    evidence = {'instrument_id': symbol, 'state': 'suspended', 'source': 'exchange'}
    monkeypatch.setattr(details, 'connect', lambda: nullcontext(Connection()))
    monkeypatch.setattr(details.intraday, 'decode_snapshot', lambda row: snapshot)
    monkeypatch.setattr(details, 'get_market', Market)
    monkeypatch.setattr(details, 'suspension_status', lambda stock, date: evidence)
    result = details.read_quote('intraday_' + '1' * 32, symbol)
    assert result['quote'] is None
    if suspended:
        assert result['suspension'] == evidence
        assert result['reason'] == '该股票当日停牌，无该时点临时日 K'
    else:
        assert 'suspension' not in result
