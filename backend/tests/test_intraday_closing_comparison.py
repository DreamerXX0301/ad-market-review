"""A published close is an independent, dated comparison on intraday rows."""
from copy import deepcopy
from datetime import date, timedelta
import json

import duckdb
import pyarrow.parquet as pq
import pytest

from app.analytics.market import MarketData
from app.services import intraday_members as members
from app.services import intraday_stock_detail as details
from fastapi import HTTPException
from test_intraday_close import A, B, C, DAY, PRIOR, GENERATION, source


@pytest.fixture
def published_source(source):
    root, directory, _, publish = source
    path = directory / 'daily.manifest.json'
    descriptor = json.loads(path.read_text())
    for part in descriptor['partitions']:
        part['rows'] = pq.ParquetFile(root / part['object_key']).metadata.num_rows
    descriptor.update(rows=sum(part['rows'] for part in descriptor['partitions']), data_begin=PRIOR, data_end=DAY)
    path.write_text(json.dumps(descriptor))
    publish()
    return source


def test_comparison_reads_real_published_parquet_and_never_substitutes_a_later_day(published_source):
    root, _, _, _ = published_source
    market = MarketData(root.parent, snapshot_id=GENERATION)
    try:
        result = details.closing_for_symbols(DAY, [A, B, C], market=market)
        assert result['closing_date'] == DAY
        assert result['closing_snapshot_id'] == GENERATION
        assert [result['items'][code]['closing_change_pct'] for code in (A, B, C)] == pytest.approx([10, 10, 5])
        older = details.closing_for_symbols(PRIOR, [A], market=market)
        assert older['items'][A]['closing_quote_date'] == PRIOR
        assert older['items'][A]['closing_change_pct'] is None  # No previous session was retained.
        future = details.closing_for_symbols('2026-11-06', [A], market=market)
        assert future['closing_date'] is None and future['items'] == {}
    finally:
        market.close()


@pytest.mark.parametrize('damage', ['missing_actions', 'actions_sha', 'before_close', 'wrong_source',
                                  'reference_sha', 'daily_descriptor_sha', 'daily_object_sha'])
def test_comparison_does_not_guess_when_published_evidence_is_invalid(published_source, damage):
    root, directory, _, _ = published_source
    if damage == 'missing_actions':
        (directory / 'corporate-actions.parquet').unlink()
    elif damage == 'actions_sha':
        (directory / 'corporate-actions.parquet').write_bytes(b'changed source')
    elif damage == 'reference_sha':
        # Valid JSON with an unchanged calendar is still a different source.
        path = directory / 'reference.json'
        path.write_text(path.read_text() + '\n')
    elif damage == 'daily_descriptor_sha':
        path = directory / 'daily.manifest.json'
        path.write_text(path.read_text() + '\n')
    elif damage == 'daily_object_sha':
        descriptor = json.loads((directory / 'daily.manifest.json').read_text())
        (root / descriptor['partitions'][0]['object_key']).write_bytes(b'changed source')
    else:
        path = directory / 'manifest.json'
        manifest = json.loads(path.read_text())
        if damage == 'before_close':
            manifest['published_at'] = DAY + 'T14:59:59+08:00'
        else:
            manifest['summary']['source'] = 'other'
        path.write_text(json.dumps(manifest))
    market = MarketData(root.parent, snapshot_id=GENERATION)
    try:
        result = details.closing_for_symbols(DAY, [A], market=market)
        assert result['items'].get(A, {}).get('closing_change_pct') is None
        if damage in ('missing_actions', 'actions_sha'):
            assert result['items'][A]['closing_quote_date'] == DAY
            assert result['items'][A]['closing_reason']
        else:
            assert result['items'] == {}
    finally:
        market.close()


class Market:
    def __init__(self, symbols=(A, B)):
        self.symbols = list(symbols)
        self.generation = GENERATION
        self.calls = []
        self.prices = {code: [self.quote(code, PRIOR, 10), self.quote(code, DAY, 11)] for code in symbols}
        self.actions = {code: [] for code in symbols}

    @staticmethod
    def quote(code, day, close):
        return {'instrument_id': code, 'date': day, 'close': close, 'volume': 100,
                'source': 'fuyao', 'price_basis': 'none'}

    def status(self):
        return {'ready': True, 'snapshot_id': self.generation, 'data_end': DAY}

    def calendar(self):
        return [PRIOR, DAY]

    def history_for_instruments(self, symbols, *, start, end):
        self.calls.append((list(symbols), start, end))
        return {code: [dict(row) for row in self.prices.get(code, ()) if start <= row['date'] <= end]
                for code in symbols}

    def actions_for_instruments(self, symbols):
        return {code: self.actions[code] for code in symbols if code in self.actions}

    def instruments(self, **kwargs):
        return [{'instrument_id': code, 'kind': 'stock'} for code in self.symbols]

    def reference(self):
        return {}


@pytest.mark.parametrize('damage', ['current_identity', 'prior_identity', 'missing_identity', 'date_suffix',
                                  'halt', 'prior_halt', 'duplicate', 'adjusted', 'action', 'unknown_actions',
                                  'invalid_action_date', 'wrong_action_identity'])
def test_exact_positive_volume_identity_and_action_evidence_are_required(damage):
    market = Market()
    prior, current = market.prices[A]
    if damage == 'current_identity':
        current['instrument_id'] = B
    elif damage == 'prior_identity':
        prior['thscode'] = B
    elif damage == 'missing_identity':
        current.pop('instrument_id')
    elif damage == 'date_suffix':
        current['date'] += 'T15:00:00'
    elif damage == 'halt':
        current['volume'] = 0
    elif damage == 'prior_halt':
        prior['volume'] = 0
    elif damage == 'duplicate':
        market.prices[A].append(dict(current))
    elif damage == 'adjusted':
        current['price_basis'] = 'forward'
    elif damage == 'unknown_actions':
        market.actions.pop(A)
    else:
        market.actions[A] = [{'date': DAY if damage == 'action' else 'invalid' if damage == 'invalid_action_date' else PRIOR,
                              'thscode': B if damage == 'wrong_action_identity' else A}]
    result = details.closing_for_symbols(DAY, [A, B], market=market)
    assert result['items'][A]['closing_change_pct'] is None
    assert result['items'][A]['closing_reason']
    assert result['items'][B]['closing_change_pct'] == pytest.approx(10)


@pytest.mark.parametrize('failure', ['malformed_second_quote', 'parquet_error', 'generation_change'])
def test_failed_comparison_returns_no_partial_result(monkeypatch, failure):
    market = Market()
    if failure == 'malformed_second_quote':
        monkeypatch.setattr(members, '_closing_quotes', lambda *args, **kwargs: {A: {'change_pct': 10}, B: None})
    elif failure == 'parquet_error':
        def fail(*args, **kwargs):
            raise duckdb.InvalidInputException('invalid parquet')
        monkeypatch.setattr(market, 'history_for_instruments', fail)
    else:
        original = market.actions_for_instruments
        def rotate(symbols):
            market.generation = 'market-new'
            return original(symbols)
        monkeypatch.setattr(market, 'actions_for_instruments', rotate)
    result = details.closing_for_symbols(DAY, [A, B], market=market)
    if failure == 'generation_change':
        assert result.pop('reason') == '行情发布代际变化，请刷新'
    assert result == {'date': DAY, 'closing_date': None, 'closing_snapshot_id': None, 'items': {}}


def metrics_setup(monkeypatch, market, seen):
    monkeypatch.setattr(details, 'load_supplement_with_fingerprint', lambda *args, **kwargs: ({}, 'checked'))
    def metrics(actual, day, symbols, *args, **kwargs):
        assert actual is market
        seen.append((day, list(symbols)))
        return {code: {'change_30d_pct': i + 100, 'return_ytd_pct': i + 200} for i, code in enumerate(symbols)}
    monkeypatch.setattr(details, 'member_metrics', metrics)
    monkeypatch.setattr(members, 'suspensions_for_date', lambda _: {})


def test_early_history_keeps_previous_close_and_reuses_only_visible_close_rows(monkeypatch):
    market, seen = Market(), []
    metrics_setup(monkeypatch, market, seen)
    comparison = details.closing_for_symbols(DAY, [A, B], market=market)
    original = deepcopy(comparison)
    result = details.history_for_symbols(DAY, '1400', [A], market=market, closing=comparison)
    assert result['as_of'] == PRIOR
    assert result['items'][A]['history_as_of'] == PRIOR
    assert result['items'][A]['closing_quote_date'] == DAY
    assert result['items'][A]['closing_change_pct'] == pytest.approx(10)
    assert set(result['items']) == {A}
    assert seen == [(PRIOR, [A])]
    assert market.calls == [([A, B], PRIOR, DAY), ([A], PRIOR, PRIOR)]
    assert comparison == original


@pytest.mark.parametrize('early_return', ['no_cutoff', 'no_master', 'after_metrics'])
def test_history_generation_guard_covers_all_return_paths(monkeypatch, early_return):
    market, seen = Market(), []
    metrics_setup(monkeypatch, market, seen)
    comparison = details.closing_for_symbols(DAY, [A], market=market)
    if early_return == 'no_cutoff':
        def calendar():
            market.generation = 'market-new'
            return []
        monkeypatch.setattr(market, 'calendar', calendar)
    elif early_return == 'no_master':
        def instruments(**kwargs):
            market.generation = 'market-new'
            return []
        monkeypatch.setattr(market, 'instruments', instruments)
    else:
        original = details.member_metrics
        def metrics(*args, **kwargs):
            result = original(*args, **kwargs)
            market.generation = 'market-new'
            return result
        monkeypatch.setattr(details, 'member_metrics', metrics)
    result = details.history_for_symbols(DAY, '1400', [A], market=market, closing=comparison)
    assert result['items'] == {} and result['as_of'] is None
    assert result['closing_snapshot_id'] is None
    assert result['reason'] == '行情发布代际变化，请刷新'


def base(**kwargs):
    return {'date': DAY, 'slot': '1400', 'board': 'all', 'sort': 'closing_change_pct',
            'direction': 'desc', 'limit': 32, 'page': 1, **kwargs}


def test_closing_sort_orders_all_members_before_paging_and_only_enriches_visible_32(monkeypatch):
    symbols = [f'{i:06}.SZ' for i in range(1, 71)]
    market, seen = Market(symbols), []
    metrics_setup(monkeypatch, market, seen)
    for i, code in enumerate(symbols):
        market.prices[code][-1]['close'] = 10 + i
    market.prices[symbols[-1]][-1]['volume'] = 0  # Unknown always sorts after known values.
    calls = []
    def get_market():
        calls.append(True)
        return market
    monkeypatch.setattr(members, 'get_market', get_market)
    monkeypatch.setattr(details, 'get_market', lambda: pytest.fail('must reuse the same fixed market'))
    rows = [members._stock(code, {'latest_price': 10, 'change_pct': 70 - i}) for i, code in enumerate(symbols)]
    result = members._page(base(page=2), rows, complete=True, observed=PRIOR, basis='test')
    expected = list(reversed(symbols[:-1]))[32:64]
    assert [row['instrument_id'] for row in result['items']] == expected
    assert result['pages'] == 3 and result['total'] == 70
    assert set(result['history']['items']) == set(expected)
    assert seen == [(PRIOR, sorted(expected))] and calls == [True]
    assert market.calls == [(symbols, PRIOR, DAY), (sorted(expected), PRIOR, PRIOR)]
    for row in result['items']:
        i = symbols.index(row['instrument_id'])
        assert row['change_pct'] == 70 - i
        assert result['history']['items'][row['instrument_id']]['closing_change_pct'] == pytest.approx(i * 10)


def test_closing_sort_rejects_publication_change_instead_of_mixing_page_history(monkeypatch):
    market, seen = Market(), []
    metrics_setup(monkeypatch, market, seen)
    monkeypatch.setattr(members, 'get_market', lambda: market)
    original = market.instruments
    def rotate(**kwargs):
        market.generation = 'market-new'
        return original(**kwargs)
    monkeypatch.setattr(market, 'instruments', rotate)
    rows = [members._stock(code, {'latest_price': 10, 'change_pct': 1}) for code in (A, B)]
    result = members._page(base(), rows, complete=True, observed=PRIOR, basis='test')
    assert result['state'] == 'unknown' and result['items'] == []
    assert result['unavailable_reason'] == '行情发布代际变化，请刷新'


@pytest.mark.parametrize('field', sorted(members.HISTORY_SORTS))
def test_original_history_sorts_still_compute_all_members_before_paging(monkeypatch, field):
    symbols = [f'{i:06}.SZ' for i in range(1, 41)]
    seen = []
    def history(day, slot, requested):
        seen.append(list(requested))
        return {'date': day, 'slot': slot, 'as_of': PRIOR,
                'items': {code: {field: (date(2026, 9, 1) + timedelta(days=i)).isoformat() if field == 'last_limit_up_date' else i}
                          for i, code in enumerate(symbols)}}
    monkeypatch.setattr(members, 'history_for_symbols', history)
    monkeypatch.setattr(members, 'suspensions_for_date', lambda _: {})
    rows = [members._stock(code, {'latest_price': 10, 'change_pct': 40 - i}) for i, code in enumerate(symbols)]
    result = members._page(base(sort=field), rows, complete=True, observed=PRIOR, basis='test')
    assert seen == [symbols]
    assert [row['instrument_id'] for row in result['items']] == list(reversed(symbols))[:32]
    assert len(result['history']['items']) == 32


def test_ordinary_sort_keeps_history_in_the_separate_visible_row_batch(monkeypatch):
    monkeypatch.setattr(members, 'history_for_symbols', lambda *args, **kwargs: pytest.fail('no whole-set annual scan'))
    monkeypatch.setattr(members, 'closing_for_symbols', lambda *args, **kwargs: pytest.fail('no whole-set close scan'))
    monkeypatch.setattr(members, 'suspensions_for_date', lambda _: {})
    rows = [members._stock(A, {'latest_price': 10, 'change_pct': 2}),
            members._stock(B, {'latest_price': 10, 'change_pct': 1})]
    result = members._page(base(sort='change_pct', limit=1), rows, complete=True, observed=PRIOR, basis='test')
    assert [row['instrument_id'] for row in result['items']] == [A]
    assert result['history'] is None


def test_visible_detail_batch_rejects_more_than_32_before_reading_market(monkeypatch):
    monkeypatch.setattr(details, 'get_market', lambda: pytest.fail('reject oversized detail batch before I/O'))
    symbols = ','.join(f'{i:06}.SZ' for i in range(1, 34))
    with pytest.raises(HTTPException) as failure:
        details.read_history.__wrapped__(DAY, '1400', symbols)
    assert failure.value.status_code == 422
