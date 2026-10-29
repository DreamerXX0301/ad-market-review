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


def grouped_fixture():
    from test_auction_feedback import reference_packet
    source = reference_packet([
        ('600001.SH', 10, 11, 10, 'limit_up'),
        ('300001.SZ', 10, 11.4, 14, 'limit_break'),
        ('600003.SH', 10, 10.4, 4, 'limit_up'),
        ('600004.SH', 10, 9.7, -3, 'limit_up'),
        ('600005.SH', 10, 9.8, -2, 'limit_break'),
        ('600006.SH', 10, 10, 0, 'limit_up'),
        ('600007.SH', 10, 10, 0, 'limit_break'),
    ])
    # 盘后方向故意与盘前相反：不能据新涨跌重新划分“修复/转弱”。
    closes = [9, 9.2, 9.3, 11.2, 11.1, 10, 10]
    market = PublishedMarket()
    market.bars = {}
    for quote, close in zip(source['quotes']['items'], closes):
        price = quote['auction_price']
        market.bars[quote['instrument_id']] = [{
            'instrument_id': quote['instrument_id'], 'date': DAY,
            'open': price, 'high': max(price, close) + 1,
            'low': min(price, close) - 1, 'close': close,
            'volume': 100, 'source': 'fuyao', 'price_basis': 'none'}]
    return source, market


def indexed_groups(result):
    return {group['key']: group for group in result['groups']}


def test_close_groups_preserve_original_partition_and_sort_by_auction_not_close():
    from app.services.auction_feedback import build_feedback
    source, market = grouped_fixture()
    frozen, bars = deepcopy(source), deepcopy(market.bars)
    before = build_feedback(source)
    result = build_close_review(source, market, at=NOW)
    indexed = indexed_groups(result)
    assert result['state'] == 'available'
    assert [group['key'] for group in result['groups'] if group['count']] == [
        'limit_up_open', 'repair', 'continuation', 'flat_limit_up',
        'flat_limit_break', 'weak_continuation', 'weaken']
    for original in before['groups']:
        current = indexed[original['key']]
        assert current['title'] == original['title']
        assert current['interpretation'] == original['interpretation']
        assert current['count'] == original['count']
        assert current['average_auction_pct'] == original['average_auction_pct']
        assert [row['instrument_id'] for row in current['items']] == [row['instrument_id'] for row in original['items']]
        for observed, prior in zip(current['items'], original['items']):
            assert observed['previous_state'] == prior['previous_state']
            assert observed['previous_board_count'] == prior['previous_board_count']
    assert indexed['repair']['average_auction_pct'] == 14
    assert indexed['repair']['average_close_pct'] == pytest.approx(-8)
    assert indexed['weaken']['average_auction_pct'] == -3
    assert indexed['weaken']['average_close_pct'] == pytest.approx(12)
    assert source == frozen and market.bars == bars
    quoted = {quote['instrument_id'] for quote in source['quotes']['items']}
    flattened = [row['instrument_id'] for group in result['groups'] for row in group['items']]
    assert len(flattened) == len(set(flattened)) == len(quoted)
    assert set(flattened) == {row['instrument_id'] for row in result['items']} == quoted
    assert result['summary']['close_valid_count'] == len(quoted)
    assert result['summary']['close_missing_count'] == 0
    assert result['summary']['average_close_pct'] == pytest.approx(sum([-10, -8, -7, 12, 11, 0, 0]) / 7)
    assert result['summary']['average_auction_to_close_pct'] == pytest.approx(
        sum(row['auction_to_close_pct'] for row in result['items']) / len(quoted))


@pytest.mark.parametrize('pending_kind', ['before_close', 'unpublished'])
def test_pending_keeps_the_frozen_groups_and_original_means_without_reading_bars(pending_kind):
    from app.services.auction_feedback import build_feedback
    source, market = grouped_fixture()
    original = build_feedback(source)
    frozen = deepcopy(source)
    if pending_kind == 'before_close':
        result = build_close_review(source, None, at=NOW.replace(hour=14, minute=59))
    else:
        market.head['data_end'] = '2026-10-28'
        result = build_close_review(source, market, at=NOW)
    assert result['state'] == 'pending' and result['items'] == [] and result['summary'] is None
    indexed = indexed_groups(result)
    for previous in original['groups']:
        group = indexed[previous['key']]
        assert group['count'] == previous['count']
        assert group['average_auction_pct'] == previous['average_auction_pct']
        assert group['valid_count'] == group['close_valid_count'] == 0
        assert group['missing_count'] == group['close_missing_count'] == previous['count']
        assert group['average_close_pct'] is group['average_auction_to_close_pct'] is None
        for row in group['items']:
            assert row['state'] == 'unknown'
            assert row['close'] is row['close_pct'] is row['auction_to_close_pct'] is None
            assert row['pre_close_price'] == 10
    assert source == frozen and market.calls == []


@pytest.mark.parametrize('bad', [None, 0, -1, True, float('nan'), float('inf'), '10'])
def test_missing_or_invalid_frozen_previous_close_does_not_reduce_original_comparability_or_fill_zero(bad):
    from test_auction_feedback import reference_packet
    source = reference_packet([(CODE, 10, 10, 0, 'limit_up')])
    source['quotes']['items'][0]['pre_close_price'] = bad
    result = build_close_review(source, PublishedMarket(), at=NOW)
    row = result['items'][0]
    assert result['state'] == 'available' and row['state'] == 'observed'
    assert row['pre_close_price'] is row['close_pct'] is None
    assert row['close_pct_reason']
    assert row['auction_to_close_pct'] == pytest.approx(10)
    summary = result['summary']
    assert summary['valid'] == 1 and summary['missing'] == 0
    assert summary['close_valid_count'] == 0 and summary['close_missing_count'] == 1
    assert summary['average_close_pct'] is None
    assert summary['average_auction_to_close_pct'] == pytest.approx(10)
    group = indexed_groups(result)['flat_limit_up']
    assert group['valid_count'] == 1 and group['missing_count'] == 0
    assert group['close_valid_count'] == 0 and group['close_missing_count'] == 1
    assert group['average_close_pct'] is None
    assert group['average_auction_to_close_pct'] == pytest.approx(10)


def test_zero_close_change_is_a_valid_observation_and_missing_denominators_stay_unknown():
    from test_auction_feedback import reference_packet
    source = reference_packet([(CODE, 10, 10, 0, 'limit_up'),
                               ('600002.SH', 10, 10, 0, 'limit_up')])
    market = PublishedMarket()
    market.bars[CODE][0]['close'] = 10
    result = build_close_review(source, market, at=NOW)
    group = indexed_groups(result)['flat_limit_up']
    assert result['state'] == 'partial'
    for value in (result['summary'], group):
        assert value['close_valid_count'] == 1 and value['close_missing_count'] == 1
        assert value['average_close_pct'] == 0
        assert value['average_auction_to_close_pct'] == 0
    assert result['items'][0]['close_pct'] == 0 and result['items'][0]['close_pct_reason'] is None
    assert result['items'][1]['close_pct'] is None and result['items'][1]['close_pct_reason']


@pytest.mark.parametrize('problem', ['mismatch', 'unknown_auction', 'missing_daily'])
def test_unobserved_or_mismatch_close_never_contributes_close_percentage(problem):
    from test_auction_feedback import reference_packet
    source = reference_packet([(CODE, 10, 10, 0, 'limit_up')])
    market = PublishedMarket()
    if problem == 'mismatch':
        market.bars[CODE][0]['open'] = 10.5
    elif problem == 'unknown_auction':
        source['quotes']['items'][0]['state'] = 'unknown'
    else:
        market.bars = {}
    result = build_close_review(source, market, at=NOW)
    row = result['items'][0]
    assert row['pre_close_price'] == 10
    assert row['close_pct'] is None and row['close_pct_reason']
    assert row['state'] == ('mismatch' if problem == 'mismatch' else 'unknown')
    summary = result['summary']
    assert summary['close_valid_count'] == summary['valid'] == 0
    assert summary['close_missing_count'] == summary['missing'] == 1
    assert summary['average_close_pct'] is summary['average_auction_to_close_pct'] is None
    group = next(group for group in result['groups'] if group['items'])
    assert group['valid_count'] == group['close_valid_count'] == 0
    assert group['missing_count'] == group['close_missing_count'] == 1
    assert group['average_close_pct'] is group['average_auction_to_close_pct'] is None
    assert summary['open_mismatch'] == group['open_mismatch'] == int(problem == 'mismatch')


def test_reference_limit_group_keeps_its_reference_basis_and_unknown_is_not_zero():
    from app.services.auction_feedback import LIMIT_OPEN_BASIS
    source, market = grouped_fixture()
    opening = build_close_review(source, market, at=NOW)['groups'][0]
    assert opening['key'] == 'limit_up_open' and opening['state'] == 'reference'
    assert opening['basis'] == LIMIT_OPEN_BASIS and opening['confirmed_limit_price'] is False
    assert opening['count'] == opening['valid_count'] == opening['close_valid_count'] == 1
    assert opening['items'][0]['opening_reference']['confirmed_limit_price'] is False
    unknown = build_close_review(payload(), PublishedMarket(), at=NOW)['groups'][0]
    assert unknown['key'] == 'limit_up_open' and unknown['state'] == 'unknown'
    assert unknown['count'] is None and unknown['items'] == []
    assert unknown['average_close_pct'] is unknown['average_auction_to_close_pct'] is None


@pytest.mark.parametrize('scope', ['missing_universe', 'duplicate_universe', 'outside_quote'])
def test_unclassified_fallback_preserves_each_unique_close_quote_when_feedback_scope_is_unusable(scope):
    from test_auction_feedback import reference_packet
    source = reference_packet([(CODE, 10, 10, 0, 'limit_up')])
    if scope == 'missing_universe':
        source.pop('universe')
    elif scope == 'duplicate_universe':
        source['universe']['items'] *= 2
    else:
        source['universe']['items'] = [{'instrument_id': '600999.SH', 'name': '无报价冻结样本'}]
    frozen = deepcopy(source)
    result = build_close_review(source, PublishedMarket(), at=NOW)
    fallback = indexed_groups(result)['unclassified']
    assert result['state'] == 'available'
    assert fallback['count'] == fallback['valid_count'] == fallback['close_valid_count'] == 1
    assert fallback['average_auction_pct'] == 0 and fallback['average_close_pct'] == pytest.approx(10)
    assert fallback['items'][0]['instrument_id'] == CODE
    identities = [row['instrument_id'] for group in result['groups'] for row in group['items']]
    assert identities == [CODE]
    assert source == frozen


def test_grouped_builder_only_reads_supplied_published_market_and_keeps_frozen_payload(monkeypatch):
    from app.services import auction_close_review as close_review, source_service
    from app import core
    source, market = grouped_fixture(); original = deepcopy(source)
    monkeypatch.setattr(close_review, 'connect', lambda: pytest.fail('builder must not open SQLite'))
    monkeypatch.setattr(close_review, 'get_market', lambda: pytest.fail('builder must not select current market'))
    monkeypatch.setattr(core, 'get_market', lambda: pytest.fail('builder must not select current market'))
    monkeypatch.setattr(source_service, 'client', lambda *_args, **_kwargs: pytest.fail('builder must not fetch'))
    result = build_close_review(source, market, at=NOW)
    assert len(result['items']) == 7 and result['summary']['close_valid_count'] == 7
    assert source == original
    assert market.calls == [([row['instrument_id'] for row in source['quotes']['items']], DAY, DAY)]


@pytest.mark.parametrize('stage', ['pending', 'published'])
def test_group_quote_pct_uses_verified_feedback_instead_of_unknown_raw_numeric_value(stage):
    from app.services.auction_feedback import build_feedback
    from test_auction_feedback import reference_packet
    source = reference_packet([(CODE, 10, 10, 0, 'limit_up')])
    source['quotes']['items'][0].update(state='unknown', auction_pct=23)
    frozen = deepcopy(source)
    expected = next(group for group in build_feedback(source)['groups'] if group['items'])
    assert expected['key'] == 'unknown_quote' and expected['items'][0]['auction_pct'] is None
    market = PublishedMarket()
    result = build_close_review(source, None if stage == 'pending' else market,
        at=NOW.replace(hour=14, minute=59) if stage == 'pending' else NOW)
    group = indexed_groups(result)['unknown_quote']
    assert group['count'] == 1 and group['average_auction_pct'] is None
    assert group['items'][0]['auction_pct'] is None
    assert group['items'][0]['auction_price'] is None
    assert group['items'][0]['close_pct'] is None
    assert source == frozen


@pytest.mark.parametrize('stage', ['pending', 'published'])
def test_scope_fallback_excludes_unknown_raw_pct_from_each_item_and_group_mean(stage):
    source = payload()
    source['quotes']['items'].append({'instrument_id': '600002.SH', 'name': '未知报价合成样本',
        'state': 'unknown', 'auction_price': 10, 'auction_pct': 23})
    frozen = deepcopy(source)
    result = build_close_review(source, None if stage == 'pending' else PublishedMarket(),
        at=NOW.replace(hour=14, minute=59) if stage == 'pending' else NOW)
    group = indexed_groups(result)['unclassified']
    assert group['count'] == 2 and group['average_auction_pct'] == 0
    items = {row['instrument_id']: row for row in group['items']}
    assert items[CODE]['auction_pct'] == 0
    assert items['600002.SH']['auction_pct'] is None
    assert source == frozen


def test_high_pct_unknown_event_stays_after_all_seven_verified_feedback_groups():
    source, market = grouped_fixture()
    unknown_code = '600008.SH'
    quote = {**deepcopy(source['quotes']['items'][0]),
             'instrument_id': unknown_code, 'name': '前日事件未知合成样本',
             'auction_price': 15, 'auction_pct': 50, 'auction_amount': 150000}
    source['quotes']['items'].append(quote)
    source['universe']['items'].append({'instrument_id': unknown_code, 'name': quote['name']})
    market.bars[unknown_code] = [{'instrument_id': unknown_code, 'date': DAY,
        'open': 15, 'high': 17, 'low': 14, 'close': 16,
        'volume': 100, 'source': 'fuyao', 'price_basis': 'none'}]
    result = build_close_review(source, market, at=NOW)
    known = ['limit_up_open', 'repair', 'continuation', 'flat_limit_up',
             'flat_limit_break', 'weak_continuation', 'weaken']
    keys = [group['key'] for group in result['groups']]
    assert keys[:7] == known
    assert keys.index('unknown_event') > max(keys.index(key) for key in known)
    unknown = indexed_groups(result)['unknown_event']
    assert unknown['average_auction_pct'] == 50 and unknown['count'] == 1
    assert unknown['items'][0]['instrument_id'] == unknown_code
