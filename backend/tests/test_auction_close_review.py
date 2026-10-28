"""Post-close facts retain the pre-open cohort and require exact traded dates."""
from copy import deepcopy
from datetime import datetime

import pytest

from app.services.auction_analysis import CHINA
from app.services.auction_close_review import build_close_review

DAY = '2026-10-29'
NOW = datetime(2026, 10, 29, 16, tzinfo=CHINA)
CODE = '600001.SH'


def payload():
    return {'date': DAY, 'quotes': {'items': [{'instrument_id': CODE, 'name': '合成证券',
        'state': 'observed', 'auction_price': 10, 'auction_pct': 0}]}}


class PublishedMarket:
    def __init__(self):
        self.head = {'ready': True, 'data_end': DAY, 'snapshot_id': 'fixture-immutable-generation',
                     'source': 'fuyao', 'price_basis': 'none', 'amount_unit': 'CNY',
                     'manifest': {'source_sha256': 'a' * 64}}
        self.bars = {CODE: [{'instrument_id': CODE, 'date': DAY, 'open': 10, 'high': 12,
                            'low': 9, 'close': 11, 'volume': 100, 'source': 'fuyao', 'price_basis': 'none'}]}
        self.calls = []

    def status(self):
        return self.head

    def calendar(self):
        return ['2026-10-28', DAY]

    def history_for_instruments(self, codes, start, end):
        self.calls.append((codes, start, end))
        return self.bars


def test_close_price_difference_has_exact_scope_and_is_explicitly_posthoc():
    source = payload(); frozen = deepcopy(source); market = PublishedMarket()
    result = build_close_review(source, market, at=NOW)
    assert source == frozen
    assert result['state'] == 'available' and result['posthoc']
    assert result['items'][0]['auction_to_close_pct'] == pytest.approx(10)
    assert result['summary']['above_auction'] == 1
    assert result['summary']['below_auction'] == 0
    assert result['source_sha256'] == 'a' * 64
    assert market.calls == [([CODE], DAY, DAY)]


def test_no_postclose_data_is_read_before_market_close():
    assert build_close_review(payload(), None, at=NOW.replace(hour=14, minute=59))['state'] == 'pending'


def test_unpublished_day_stays_pending_instead_of_using_previous_close():
    market = PublishedMarket(); market.head['data_end'] = '2026-10-28'
    assert build_close_review(payload(), market, at=NOW)['state'] == 'pending'
    assert not market.calls


@pytest.mark.parametrize('changes', [{'volume': 0}, {'volume': None}, {'date': '2026-10-28'},
    {'price_basis': 'forward'}, {'source': 'other'}, {'currency': 'USD'}, {'close': 13},
    {'instrument_id': '000001.SZ'}, {'low': 11}, {'close': True}])
def test_missing_suspended_or_inconsistent_bars_are_unknown(changes):
    market = PublishedMarket(); market.bars[CODE][0].update(changes)
    result = build_close_review(payload(), market, at=NOW)
    assert result['state'] == 'unknown'
    assert result['summary']['valid'] == 0
    assert result['summary']['above_auction'] is None
    assert result['items'][0]['close'] is None


def test_duplicate_day_and_unknown_auction_never_create_comparable_returns():
    market = PublishedMarket(); market.bars[CODE] *= 2
    assert build_close_review(payload(), market, at=NOW)['state'] == 'unknown'
    source = payload(); source['quotes']['items'][0].update(state='unknown', auction_price=None)
    market = PublishedMarket()
    result = build_close_review(source, market, at=NOW)
    assert '不' in result['items'][0]['reason']
    assert result['items'][0]['auction_price'] is None


def test_opening_price_discrepancy_is_separate_and_excluded_from_summary():
    market = PublishedMarket(); market.bars[CODE][0]['open'] = 10.5
    result = build_close_review(payload(), market, at=NOW)
    assert result['state'] == 'unknown'
    assert result['items'][0]['state'] == 'mismatch'
    assert result['items'][0]['close'] == 11
    assert result['summary']['open_mismatch'] == 1
    assert result['summary']['valid'] == 0


def test_partial_coverage_never_fills_missing_with_zero():
    source = payload(); source['quotes']['items'].append({'instrument_id': '000001.SZ',
        'state': 'observed', 'auction_price': 10, 'auction_pct': 0})
    result = build_close_review(source, PublishedMarket(), at=NOW)
    assert result['state'] == 'partial'
    assert result['summary']['valid'] == 1 and result['summary']['missing'] == 1
    assert result['items'][1]['close'] is None


@pytest.mark.parametrize('changes', [{'price_basis': 'forward'}, {'source': 'other'}, {'amount_unit': 'USD'}])
def test_unadjusted_currency_and_same_provider_are_required(changes):
    market = PublishedMarket(); market.head.update(changes)
    assert build_close_review(payload(), market, at=NOW)['state'] == 'unknown'
    assert not market.calls
