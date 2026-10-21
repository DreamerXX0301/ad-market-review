"""Concurrent first readers cannot observe partially published lazy metadata."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from threading import Event

import pytest

from app.analytics import market as market_module
from app.analytics.market import MarketData
from app.services import watchlist
from app.services.tracking_quotes import clear_tracking_quote_cache
from app import core
from test_market_compact_reads import retained_market
from test_history_event_panels import fixture_market
from test_watchlist import personal
from test_watchlist_returns import legacy, SYMBOL
from test_workspace import ws


def pause_reference(monkeypatch):
    entered = Event(); release = Event(); original = market_module.load_json
    def read(path, *args, **kwargs):
        if Path(path).name == 'reference.json' and not entered.is_set():
            entered.set()
            if not release.wait(3): raise AssertionError('fixture reference was not released')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(market_module, 'load_json', read)
    return entered, release


@pytest.mark.parametrize('fixed', [None, 'fixture'])
def test_snapshot_and_calendar_are_published_together_for_concurrent_cold_readers(retained_market, monkeypatch, fixed):
    root, calendar, symbols = retained_market
    market = MarketData(root, snapshot_id=fixed)
    entered, release = pause_reference(monkeypatch)
    second_started = Event()
    def second_read():
        second_started.set()
        return market.calendar(), market.instrument(symbols[0]), market.status()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(market.calendar)
        try:
            assert entered.wait(2)
            # Publishing either marker early was the original race. They must
            # stay old while this generation's reference is still in flight.
            assert market._snapshot is None and market._head_signature is None
            second = pool.submit(second_read); assert second_started.wait(2)
            with pytest.raises(TimeoutError): second.result(timeout=.05)
        finally: release.set()
        assert first.result(timeout=2) == calendar
        second_calendar, identity, status = second.result(timeout=2)
    assert second_calendar == calendar and identity['kind'] == 'stock'
    assert status['ready'] is True and status['snapshot_id'] == 'fixture'


def test_concurrent_first_watchlist_visit_freezes_a_real_price_not_transient_unknown(personal, tmp_path, monkeypatch):
    original, _ = personal
    records = [{**row, 'instrument_id': symbol} for symbol, values in original.data.items() for row in values]
    market = fixture_market(tmp_path, records, original.days)
    legacy(original, added_index=70)
    monkeypatch.setattr(watchlist, 'get_market', lambda: market)
    entered, release = pause_reference(monkeypatch)
    clear_tracking_quote_cache(); started = Event()
    def first_visit():
        started.set()
        return watchlist.list_items()
    with ThreadPoolExecutor(max_workers=2) as pool:
        bootstrap = pool.submit(market.calendar)
        try:
            assert entered.wait(2)
            listing = pool.submit(first_visit); assert started.wait(2)
            with pytest.raises(TimeoutError): listing.result(timeout=.05)
        finally: release.set()
        assert bootstrap.result(timeout=2) == original.days
        row = listing.result(timeout=3)['items'][0]
    price = next(bar['close'] for bar in original.data[SYMBOL] if bar['date'] == original.entry)
    assert row['watchlist_entry_price'] == row['latest_price'] == price
    assert row['watchlist_change_pct'] == 0 and row['today_change_pct'] is not None
    assert row['watchlist_entry_basis']['method'] == watchlist.ENTRY_BASELINE_METHOD
    with core.connect() as connection:
        saved = connection.execute('SELECT watchlist_entry_price_e8 FROM watchlist_item').fetchone()[0]
    assert saved is not None
