"""Cold stock reads stay bounded; compact whole-market reads preserve evidence."""
import json
from pathlib import Path

import pandas as pd
import pytest

from app.analytics.market import MarketData
from app.services.sync_service import midnight_ms


@pytest.fixture
def retained_market(tmp_path):
    days = ['2026-01-05', '2026-01-06', '2026-01-07']
    symbols = ['000001.SZ', '600000.SH', '688001.SH']
    folder = tmp_path/'published'/'fixture'; folder.mkdir(parents=True)
    rows = [{'instrument_id': symbol, 'date': day, 'open': 10., 'high': 11., 'low': 9.,
             'close': 10.+i, 'volume': 100., 'amount': 1000., 'source': 'fixture', 'price_basis': 'none'}
            for symbol in symbols for i, day in enumerate(days)]
    # Keep valid OHLC while changing closes.
    for row in rows: row['high'] = row['close'] + 1
    pd.DataFrame(rows).to_parquet(folder/'daily.parquet', index=False)
    pd.DataFrame([{'thscode': '600000.SH', 'ex_date_ms': midnight_ms(days[1])},
                  {'thscode': '600000.SH', 'ex_date_ms': midnight_ms(days[1])}]).to_parquet(folder/'corporate-actions.parquet', index=False)
    reference = {'calendar': days, 'master': {s: {'kind': 'stock', 'name': s} for s in symbols},
                 'pool_history': {days[0]: {'limitup': [{'thscode': '600000.SH'}], 'coverage': {'limitup': {'complete': True}}},
                                  days[1]: {'limitup': [{'thscode': '000001.SZ'}], 'coverage': {'limitup': {'complete': False}}}},
                 'pool_trade_date': days[2], 'limitup': [], 'pool_coverage': {'limitup': {'complete': True}}}
    (folder/'reference.json').write_text(json.dumps(reference))
    head = {'snapshot_id': 'fixture', 'data_begin': days[0], 'data_end': days[-1]}
    (folder/'manifest.json').write_text(json.dumps({'summary': head}))
    (tmp_path/'published'/'market-head.json').write_text(json.dumps(head))
    return tmp_path, days, symbols


def test_cold_bars_are_bounded_and_match_cached_rows_including_evidence(retained_market, monkeypatch):
    root, days, symbols = retained_market
    market = MarketData(root)
    with monkeypatch.context() as patch:
        patch.setattr(market, '_daily_frame', lambda: pytest.fail('a stock chart must not load all securities'))
        cold = {s: market.bars(s) for s in symbols}
        assert market.bars(symbols[0], days[0]) == cold[symbols[0]][:1]
        assert market.bars('999999.SH') == []
        assert market._frame is None
    market._daily_frame()
    assert {s: market.bars(s) for s in symbols} == cold
    assert [r['limit_up'] for r in cold['600000.SH']] == [True, None, False]
    assert [r['limit_up'] for r in cold['000001.SZ']] == [False, True, False]
    assert [r['corporate_action'] for r in cold['600000.SH']] == [False, True, False]
    assert len(cold['600000.SH']) == 3  # Duplicate evidence cannot duplicate quotes.
    assert all(r['corporate_action_coverage_known'] is True for r in cold['600000.SH'])
    cold['600000.SH'][0]['close'] = -1
    assert market.bars('600000.SH')[0]['close'] == 10.


def test_compact_frame_keeps_date_filters_and_aggregations_compatible(retained_market):
    root, days, symbols = retained_market
    market = MarketData(root)
    frame = market._daily_frame()
    assert all('pyarrow' in str(frame[field].dtype) for field in ('instrument_id', 'date', 'limit_up'))
    assert frame.close.dtype == 'float64'
    # Arbitrary calendar bounds (including nontrading dates) keep string-date semantics.
    assert sum(len(v) for v in market.history_panel('2026-01-10', lookback=2).values()) == 6
    assert len(market.panel(days[-1])) == 3
    assert [r['amount'] for r in market.turnover_history()] == [3000.] * 3
    breadth = market.breadth_history()
    assert [r['up'] for r in breadth] == [0, 2, 3]  # Action day is excluded for 600000.SH.
    assert market._daily_frame() is frame
