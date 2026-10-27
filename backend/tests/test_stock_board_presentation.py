"""Board height, verified shapes, raw volume and feedback stay independent."""
from copy import deepcopy

import pytest

from app.services import market_ladder, market_service, strategy_limits
from app.services.stock_board_presentation import VERSION, decorate_board_collections
from test_market_ladder import A, B, C, DAYS, Market, OTHER, TODAY, _shape_quote, pool


@pytest.fixture
def market(monkeypatch):
    market = Market()
    for history in market.history.values():
        for row in history:
            row.update(open=10, high=10, low=10, close=10, price_basis='none',
                       corporate_action=False, corporate_action_coverage_known=True,
                       price_continuity=True, change_pct=0, snapshot_id='fixture')
    monkeypatch.setattr(strategy_limits, 'load_supplement', lambda _: {})
    monkeypatch.setattr(market_ladder, 'resolve_reference', lambda *_: (market.local, market.membership))
    return market


def decorate(market, symbols=(OTHER,)):
    rows = [row for row in market.rows() if row['instrument_id'] in symbols]
    return decorate_board_collections(market, market.status(), TODAY, market.ref, {'rows': rows})['rows']


def display(market, symbols=(OTHER,)):
    return decorate(market, symbols)[0]['board_presentation']


def tags(value): return {item['code']: item for item in value['types']}


def test_one_price_volume_contraction_height_and_promotion_are_independent(market):
    market.history[OTHER][-1].update(open=11, high=11, low=11, close=11, volume=50, change_pct=10)
    market.ref['_supplement']['annual_pools'][TODAY]['items'][-1].update(last_price=11, continue_day_cnt=3)
    market.ref['_supplement']['annual_pools'][DAYS[-2]]['items'][-1]['continue_day_cnt'] = 2
    original = deepcopy(market.history)
    value = display(market)
    assert value['version'] == VERSION and value['date'] == TODAY and value['snapshot_id'] == 'fixture'
    assert value['height']['label'] == '3连板' and value['height']['source_date'] == TODAY
    assert value['previous_height']['label'] == '2连板' and value['previous_height']['source_date'] == DAYS[-2]
    assert set(tags(value)) == {'one_price_limit_up'}
    assert value['volume']['state'] == 'contracted' and value['volume']['ratio'] == .5
    assert value['feedback']['code'] == 'promoted' and value['feedback']['cohort_date'] == DAYS[-2]
    assert len(market.history_calls) == 1 and market.history_calls[0][1:] == (DAYS[-2], TODAY)
    assert market.history == original


def test_confirmed_down_and_previous_height_override_loss_threshold(market):
    market.history[B][-1].update(open=9, high=9, low=9, close=9, change_pct=-10)
    down = pool(TODAY, [B], pool_kind='limitdown')
    down['items'][0]['last_price'] = 9
    market.ref['_supplement']['annual_down_pools'] = {TODAY: down}
    value = display(market, (B,))
    assert value['height']['value'] is None and value['previous_height']['label'] == '首板'
    assert set(tags(value)) == {'one_price_limit_down'}
    assert value['feedback']['code'] == 'limit_down' and value['feedback']['label'] == '跌停反馈'


def test_new_down_shape_does_not_change_legacy_ladder_pattern_fields(market):
    market.history[B][-1].update(open=9, high=9, low=9, close=9, change_pct=-10)
    down = pool(TODAY, [B], pool_kind='limitdown'); down['items'][0]['last_price'] = 9
    market.ref['_supplement']['annual_down_pools'] = {TODAY: down}
    result = market_ladder.build_ladder(market, market.status(), TODAY, market.ref, market.rows(), {})
    row = next(item for item in result['failed_items'] if item['instrument_id'] == B)
    assert row['pattern_status']['one_price_limit_down']['state'] is None
    assert not any(item['kind'] == 'one_price_limit_down' for item in row['pattern_events'])
    assert 'one_price_limit_down' in tags(row['board_presentation'])
    assert len(market.history_calls) == 1


@pytest.mark.parametrize('count,repeated', [(0, False), (1, False), (2, False), (3, True), (18, True)])
def test_source_open_count_and_project_threshold_do_not_invent_intraday_path(market, count, repeated):
    market.history[A][-1].update(open=9.8, high=10.2, low=9.7)
    market.ref['_supplement']['annual_pools'][TODAY]['items'][0]['break_count'] = count
    value = display(market, (A,)); actual = tags(value)
    assert 'non_one_price_limit_up' in actual
    assert ('open_count' in actual) is (count > 0)
    assert ('repeated_break' in actual) is repeated
    if count > 0: assert actual['open_count']['label'] == f'开板{count}次'
    assert all('锁量' not in item['label'] and '烂板' not in item['label'] for item in value['types'])


def test_breakpool_unsealed_stock_has_no_current_height_and_conflicting_counts_are_unknown(market):
    market.ref['pool_trade_date'] = TODAY
    market.ref['breakpool'] = [{'thscode': B, 'last_price': 10, 'open_times': 4}]
    value = display(market, (B,))
    assert value['height']['state'] == 'unknown' and value['previous_height']['value'] == 1
    assert set(tags(value)) == {'broken_pool_source', 'open_count', 'repeated_break'}
    market.ref['breakpool'][0]['open_num'] = 2
    assert set(tags(display(market, (B,)))) == {'broken_pool_source'}


@pytest.mark.parametrize('mode', ['missing_p', 'duplicate_t', 'zero_p_volume', 'action', 'action_unknown',
                                 'identity_conflict', 'future_quote', 'panel_generation', 'snapshot_changed'])
def test_volume_unknown_never_becomes_contraction_or_false_feedback(market, mode, monkeypatch):
    if mode == 'missing_p': market.history[B] = [row for row in market.history[B] if row['date'] != DAYS[-2]]
    elif mode == 'duplicate_t': market.history[B].append(deepcopy(market.history[B][-1]))
    elif mode == 'zero_p_volume': market.history[B][-2]['volume'] = 0
    elif mode == 'action': market.history[B][-1]['corporate_action'] = True
    elif mode == 'action_unknown': market.history[B][-1]['corporate_action_coverage_known'] = False
    elif mode == 'identity_conflict': market.history[B][-1]['thscode'] = OTHER
    elif mode == 'future_quote': market.history[B][-1]['trade_date'] = '2099-01-01'
    elif mode == 'panel_generation': market.history[B][-1]['snapshot_id'] = 'different'
    else:
        original = market.status
        status_calls = []
        def changed():
            status_calls.append(True)
            return original() if len(status_calls) == 1 else {'snapshot_id': 'changed', 'data_end': TODAY}
        monkeypatch.setattr(market, 'status', changed)
    value = display(market, (B,))
    assert value['volume']['state'] == 'unknown' and value['volume']['ratio'] is None
    assert value['feedback']['state'] == 'unknown'


@pytest.mark.parametrize('change,label,code', [(-6, '大幅回落', 'large_loss'), (-5, '大幅回落', 'large_loss'),
                                             (-1, '回落', 'negative'), (0, '平盘', 'flat'), (2, '正反馈', 'positive')])
def test_price_feedback_labels_are_project_observations_not_real_fund_absorption(market, change, label, code):
    close = round(10*(1+change/100), 2)
    market.history[B][-1].update(open=10, high=max(10, close), low=min(9.9, close), close=close, change_pct=change)
    value = display(market, (B,))
    assert value['feedback']['code'] == code and value['feedback']['label'] == label
    assert '承接' not in value['feedback']['label']
    assert ('pullback_reclaim' in tags(value)) is (change >= 0)


def test_supplied_collections_share_one_batch_and_future_append_changes_no_t_fact(market):
    originals = market.rows()
    before = decorate_board_collections(market, market.status(), TODAY, market.ref, {'first': originals[:2], 'second': originals[1:3]})
    assert len(market.history_calls) == 1 and len(market.history_calls[0][0]) == 3
    for history in market.history.values(): history.append({**history[-1], 'date': '2099-01-01', 'volume': 1, 'close': 999})
    after = decorate_board_collections(market, market.status(), TODAY, market.ref, {'first': originals[:2], 'second': originals[1:3]})
    assert after == before
    assert all('board_presentation' not in row for row in originals)


@pytest.mark.parametrize('field,value', [('open', 9), ('high', 11), ('low', 9), ('close', 11),
                                       ('volume', 99), ('corporate_action', True)])
def test_action_metadata_never_joins_a_different_panel_quote(market, field, value):
    row = next(item for item in market.rows() if item['instrument_id'] == B)
    row[field] = value
    result = decorate_board_collections(market, market.status(), TODAY, market.ref, {'rows': [row]})['rows'][0]['board_presentation']
    assert result['volume']['state'] == 'unknown'
    assert result['feedback']['state'] == 'unknown'


def test_already_read_supplement_is_not_loaded_again(market, monkeypatch):
    monkeypatch.setattr(strategy_limits, 'load_supplement', lambda _: pytest.fail('labels must reuse the panel supplement'))
    assert display(market)['height']['value'] == 1
    assert market_ladder.build_ladder(market, market.status(), TODAY, market.ref, market.rows(), {})['items']


@pytest.mark.parametrize('one_price,kind', [(True, 'one_price_limit_up'), (False, 'earth_to_sky')])
def test_ladder_uses_existing_three_day_batch_and_verified_shape(one_price, kind, market):
    _shape_quote(market, one_price=one_price)
    market.history[A][-1]['change_pct'] = 10
    result = market_ladder.build_ladder(market, market.status(), TODAY, market.ref, market.rows(), {})
    row = next(item for item in result['items'] if item['instrument_id'] == A)
    assert kind in tags(row['board_presentation'])
    assert row['board_presentation']['height']['label'] == '首板'
    assert len(market.history_calls) == 1 and market.history_calls[0][1:] == (DAYS[-3], TODAY)


def test_overview_decorates_only_two_top5_collections_in_one_small_batch(market, monkeypatch):
    monkeypatch.setattr(market_service, 'panel', lambda _, **_kwargs: (market, market.status(), TODAY, market.ref, market.rows()))
    monkeypatch.setattr(market_service, 'cached_gainers', lambda *_: ([], {}))
    monkeypatch.setattr(market_service, 'data_coverage', lambda *_: {})
    monkeypatch.setattr(market_service, '_breadth', lambda *_: {})
    monkeypatch.setattr(market_service, 'amount_change_metadata', lambda *_: {})
    monkeypatch.setattr(market, 'indices', lambda _: [], raising=False)
    monkeypatch.setattr(market, 'status', lambda: {'snapshot_id': 'fixture', 'data_end': TODAY, 'quality': {}})
    from app.services import market_top_amount
    monkeypatch.setattr(market_top_amount, 'decorate_top_amount', lambda *_: ([], {}))
    market.ref['pool_trade_date'] = TODAY
    market.ref['breakpool'] = [{'thscode': B, 'last_price': 10, 'open_times': 3}]
    result = market_service.overview(TODAY)
    assert len(market.history_calls) == 1 and len(market.history_calls[0][0]) <= 10
    assert result['seal_funds_top5'] == result['largest_seals']
    assert all(row['board_presentation']['version'] == VERSION for row in result['largest_seals']+result['divergence'])


@pytest.mark.parametrize('mode', ['raw_disagreement', 'one_unknown', 'both_unknown'])
def test_feedback_sort_uses_confirmed_height_and_unknown_last_without_changing_raw(market, monkeypatch, mode):
    from app.services import stock_memberships
    market.ref['ladder'] = {'item': [{'date': DAYS[-2], 'boards': {'source': [
        {'thscode': B, 'board_num': 99.5}, {'thscode': OTHER, 'board_num': -7}]}}]}
    previous = market.ref['_supplement']['annual_pools'][DAYS[-2]]['items']
    previous[0]['continue_day_cnt'] = 2; previous[-1]['continue_day_cnt'] = 3
    if mode in ('one_unknown', 'both_unknown'): previous[0]['last_price'] = 99
    if mode == 'both_unknown': previous[-1]['last_price'] = 99
    monkeypatch.setattr(stock_memberships, 'current_memberships', lambda _market, symbols, **_: {
        'memberships': {symbol: [] for symbol in symbols}, 'scope': 'synthetic fixture'})
    result = market_service.prior_ladder_feedback(market, market.ref, TODAY,
                                                 {row['instrument_id']: row for row in market.rows()})
    assert [row['instrument_id'] for row in result] == [OTHER, B]
    assert [row['previous_board_count'] for row in result] == [-7, 99.5]
    states = [row['board_presentation']['previous_height']['state'] for row in result]
    assert states == (['confirmed', 'confirmed'] if mode == 'raw_disagreement' else
                      ['confirmed', 'unknown'] if mode == 'one_unknown' else ['unknown', 'unknown'])
    assert len(market.history_calls) == 1 and market.history_calls[0][1:] == (DAYS[-2], TODAY)
