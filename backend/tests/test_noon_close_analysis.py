"""Same-day synthetic endpoint evidence; no database, provider or market reader."""
from copy import deepcopy
import json
import math

import pytest

from app.services.noon_close_analysis import build_close_review

DAY = '2026-10-28'
PRIOR = '2026-10-27'


def quote(symbol, price, *, closing=False, previous=10, **extra):
    return {'instrument_id': symbol, 'name': '合成' + symbol[:6], 'date': DAY,
            'close' if closing else 'latest_price': price, 'prev_close': previous,
            'change_pct': (price / previous - 1) * 100, 'price_basis': 'none',
            'currency': 'CNY', 'amount_unit': 'CNY', 'source': 'fuyao',
            'volume': 1000, 'amount': 2000 if closing else 1000, **extra}


def pool(rows=(), *, complete=True, **extra):
    return {'date': DAY, 'state': 'observed' if complete else 'partial',
            'complete': complete, 'source_count': len(rows), 'verified_count': len(rows),
            'unverified_count': 0, 'items': list(rows), **extra}


def event(symbol, price, height=None):
    return {'instrument_id': symbol, 'date': DAY, 'previous_close': price,
            'previous_board_count': height, 'continue_day_cnt': height}


def fixture():
    symbols = [f'60000{i}.SH' for i in range(1, 10)] + ['300010.SZ']
    prices = [11, 11, 10.5, 10.5, 9, 10.3, 9.7, 9, 11, 10]
    closing = [11, 10.4, 11, 10.1, 9.6, 11, 9, 9, 10.6, 9.9]
    noon = {'date': DAY, 'version': 'noon-snapshot-v1', 'calendar': {'verified': True, 'previous_day': PRIOR},
        'stocks': [quote(symbol, price) for symbol, price in zip(symbols, prices)],
        'indices': [{'instrument_id': symbol, 'date': DAY, 'latest_price': 101, 'change_pct': 1,
                     'prev_close': 100, 'price_basis': 'none'} for symbol in ('000001.SH', '399001.SZ', '399006.SZ', '000688.SH')],
        'sectors': [], 'hot_stocks': [{'instrument_id': symbols[9], 'rank': 1}],
        'summary': {'coverage': {'enumeration_complete': True},
                    'breadth': {'up': 6, 'down': 3, 'flat': 1, 'valid': 10, 'total': 10},
                    'top_amount': [{'instrument_id': symbols[0]}, {'instrument_id': symbols[9]}]},
        'events': {
            'limit_up': pool([event(symbols[i], prices[i], 3 if i == 0 else 4 if i == 1 else 1) for i in (0, 1, 8)]),
            'limit_break': pool([event(symbols[i], prices[i]) for i in (2, 3)]),
            'limit_down': pool([event(symbols[i], prices[i]) for i in (4, 7)])},
        'previous_review': {'date': PRIOR, 'observations': [{'instrument_id': symbols[2], 'date': DAY, 'previous_date': PRIOR}],
            'mainlines': [{'instrument_id': symbols[2], 'mainline': '原文：若回封，再观察', 'recorded_at_ms': 100}],
            'event_cohorts': {'limit_up': {'date': PRIOR, 'state': 'observed_sample', 'source_count': 5, 'verified_count': 5,
                'items': [{'instrument_id': symbols[i], 'date': PRIOR, 'previous_board_count': h}
                          for i, h in ((0, 2), (1, 3), (2, 2), (3, 1), (9, None))]}}},
        'stock_classifications': {symbols[i]: {'state': 'observed_sample',
            'industries': [{'topic_code': '881001.TI', 'name': '合成行业', 'kind': 'industry',
                            'source': 'fuyao', 'members_observed_date': PRIOR}],
            'topics': [{'topic_code': '885001.TI', 'name': '合成概念', 'kind': 'concept',
                        'source': 'fuyao', 'members_observed_date': PRIOR}]} for i in range(10)}}
    close = {'date': DAY, 'stocks': [quote(symbol, price, closing=True) for symbol, price in zip(symbols, closing)],
        'indices': [{'instrument_id': symbol, 'date': DAY, 'close': 102, 'change_pct': 2,
                     'price_basis': 'none'} for symbol in ('000001.SH', '399001.SZ', '399006.SZ', '000688.SH')],
        'sectors': [], 'amount_comparable': True, 'amount_unit': 'CNY',
        'pool_semantics': {'limit_break': 'final_unsealed'},
        'events': {
            'limit_up': pool([event(symbols[i], closing[i], 3 if i in (0, 2) else 1) for i in (0, 2, 5)]),
            'limit_break': pool([event(symbols[i], closing[i]) for i in (1, 3, 8)]),
            'limit_down': pool([event(symbols[i], closing[i]) for i in (6, 7)])}}
    return noon, close, symbols


def indexed(result):
    return {row['instrument_id']: row for row in result['items']}


def test_endpoint_transitions_relay_and_immutability():
    noon, close, ids = fixture()
    before = deepcopy((noon, close))
    result = build_close_review(noon, close)
    assert (noon, close) == before
    rows = indexed(result)
    assert rows[ids[0]]['transition'] == 'kept' and rows[ids[0]]['final_board_count'] == 3
    assert rows[ids[1]]['transition'] == 'opened' and rows[ids[1]]['relay_state'] == 'broken'
    assert rows[ids[2]]['transition'] == 'resealed' and rows[ids[2]]['relay_state'] == 'promoted'
    assert rows[ids[3]]['transition'] == 'not_resealed'
    assert rows[ids[4]]['transition'] == 'down_opened'
    assert rows[ids[5]]['transition'] == 'new_limit_up' and rows[ids[5]]['final_board_count'] == 1
    assert rows[ids[6]]['transition'] == 'new_limit_down'
    assert rows[ids[7]]['transition'] == 'unchanged'
    assert result['summary']['relay']['source_count'] == 5
    assert result['summary']['relay']['unknown_count'] == 1
    assert all(row['previous_board_count'] is None for row in rows.values() if row['instrument_id'] not in ids[:4])
    assert result['summary']['metrics']['kept']['source_count'] == 3
    assert result['summary']['metrics']['kept']['count'] == 1
    assert result['summary']['metrics']['resealed']['count'] == 1
    assert result['summary']['metrics']['new_limit_up']['count'] == 2


def test_price_ratio_and_percentage_points_are_separate_zero_is_real():
    noon, close, ids = fixture()
    row = indexed(build_close_review(noon, close))[ids[2]]
    assert row['afternoon_change_pct'] == pytest.approx((11 / 10.5 - 1) * 100)
    assert row['change_pct_points'] == pytest.approx(5)
    assert row['afternoon_change_pct'] != row['change_pct_points']
    kept = indexed(build_close_review(noon, close))[ids[0]]
    assert kept['afternoon_change_pct'] == 0
    index = build_close_review(noon, close)['summary']['indices'][0]
    assert index['change_pct_points'] == 1
    assert index['afternoon_change_pct'] == pytest.approx((102 / 101 - 1) * 100)


def test_missing_close_does_not_count_as_failed_reseal_or_opening():
    noon, close, ids = fixture()
    close['stocks'] = [row for row in close['stocks'] if row['instrument_id'] != ids[8]]
    result = build_close_review(noon, close)
    row = indexed(result)[ids[8]]
    assert row['close_state'] == 'unknown'
    assert row['close_price'] is None and row['afternoon_change_pct'] is None
    metrics = result['summary']['metrics']
    assert metrics['kept']['source_count'] == 3
    assert metrics['kept']['denominator'] == 2 and metrics['kept']['unknown_count'] == 1
    assert metrics['opened']['count'] == 1
    assert result['state'] == 'partial'


@pytest.mark.parametrize('bad', [0, -1, True, math.nan, math.inf])
def test_bad_close_price_remains_unknown(bad):
    noon, close, ids = fixture()
    close['stocks'][0]['close'] = bad
    row = indexed(build_close_review(noon, close))[ids[0]]
    assert row['close_state'] == 'unknown' and row['close_price'] is None
    assert row['final_board_count'] is None


def test_duplicate_close_identity_does_not_pick_favorable_row():
    noon, close, ids = fixture()
    close['stocks'].append({**close['stocks'][0], 'close': 9})
    result = build_close_review(noon, close)
    assert indexed(result)[ids[0]]['close_price'] is None
    assert any('重复' in gap for gap in result['gaps'])


@pytest.mark.parametrize('change', [{'date': PRIOR}, {'price_basis': 'qfq'}, {'volume': 0}, {'volume': None}])
def test_same_day_basis_and_actual_trading_required(change):
    noon, close, ids = fixture()
    close['stocks'][0].update(change)
    result = indexed(build_close_review(noon, close))[ids[0]]
    assert result['close_price'] is None and result['close_state'] == 'unknown'
    assert '停牌' not in result['reason'] or '不能' in result['reason']


def test_different_day_input_cannot_create_comparison():
    noon, close, _ = fixture()
    close['date'] = PRIOR
    with pytest.raises(ValueError, match='同一'):
        build_close_review(noon, close)


def test_incomplete_pool_omission_is_unknown_not_confirmed_broken():
    noon, close, ids = fixture()
    close['events']['limit_up'].update(state='partial', complete=False)
    close['events']['limit_break']['items'] = [row for row in close['events']['limit_break']['items'] if row['instrument_id'] != ids[1]]
    row = indexed(build_close_review(noon, close))[ids[1]]
    assert row['close_state'] == 'unknown'
    assert row['relay_state'] == 'unknown'
    assert row['transition'] == 'unknown'


def test_ever_broken_flag_may_coexist_with_confirmed_closing_limit_up():
    noon, close, ids = fixture()
    close['pool_semantics']['limit_break'] = 'ever_broken'
    close['events']['limit_break']['items'].append(event(ids[2], 11))
    close['events']['limit_break'].update(source_count=4, verified_count=4)
    row = indexed(build_close_review(noon, close))[ids[2]]
    assert row['close_state'] == 'limit_up' and row['transition'] == 'resealed'
    assert row['day_events'] == ['ever_broken']


@pytest.mark.parametrize('semantics', ['unknown', 'final_unsealed'])
def test_unexplained_overlap_not_silently_treated_as_reseal(semantics):
    noon, close, ids = fixture()
    close['pool_semantics']['limit_break'] = semantics
    close['events']['limit_break']['items'].append(event(ids[2], 11))
    row = indexed(build_close_review(noon, close))[ids[2]]
    assert row['close_state'] == 'conflict' and row['transition'] == 'unknown'


def test_ever_broken_alone_does_not_prove_unsealed_when_up_coverage_is_missing():
    noon, close, ids = fixture()
    close['pool_semantics']['limit_break'] = 'ever_broken'
    close['events']['limit_up'].update(complete=False, state='partial')
    row = indexed(build_close_review(noon, close))[ids[1]]
    assert row['close_state'] == 'unknown'


def test_transition_flags_capture_overlapping_meanings():
    noon, close, ids = fixture()
    close['events']['limit_break']['items'] = [row for row in close['events']['limit_break']['items'] if row['instrument_id'] != ids[1]]
    close['events']['limit_break'].update(source_count=2, verified_count=2)
    close['stocks'][1].update(close=9, change_pct=-10)
    close['events']['limit_down']['items'].append(event(ids[1], 9))
    close['events']['limit_down'].update(source_count=3, verified_count=3)
    rows = indexed(build_close_review(noon, close))
    assert rows[ids[1]]['transition'] == 'opened'
    assert set(rows[ids[1]]['transition_flags']) == {'opened', 'new_limit_down'}
    assert set(rows[ids[2]]['transition_flags']) == {'resealed', 'new_limit_up'}


def test_declared_noon_members_not_verified_remain_in_unknown_denominator():
    noon, close, _ = fixture()
    noon['events']['limit_up']['source_count'] = 4
    metrics = build_close_review(noon, close)['summary']['metrics']['kept']
    assert metrics['source_count'] == 4
    assert metrics['denominator'] == 3 and metrics['unknown_count'] == 1


def test_original_noon_declared_total_and_exclusions_are_not_lost():
    noon, close, _ = fixture()
    original = noon['events']['limit_up']
    original.pop('source_count')
    original.update(count=3, declared_total=4, excluded_rows=1, complete=False, state='partial')
    metric = build_close_review(noon, close)['summary']['metrics']['kept']
    assert metric['source_count'] == 4 and metric['unknown_count'] == 1


def test_invalid_event_price_cannot_be_overridden_by_verified_flag():
    noon, close, ids = fixture()
    close['events']['limit_up']['items'][0].update(previous_close=0, price_verified=True)
    row = indexed(build_close_review(noon, close))[ids[0]]
    assert row['close_state'] == 'unknown'


def test_missing_pool_and_verified_empty_pool_are_distinct():
    noon, close, _ = fixture()
    noon['events']['limit_up'] = {'state': 'unknown', 'items': []}
    assert build_close_review(noon, close)['summary']['metrics']['kept']['count'] is None
    noon['events']['limit_up'] = pool()
    value = build_close_review(noon, close)['summary']['metrics']['kept']
    assert value['count'] == 0 and value['rate_pct'] is None


@pytest.mark.parametrize('bad', [{'amount_comparable': False}, {'stock_unit': '亿元'}, {'stock_amount': 500}, {'currency': 'USD'}])
def test_amount_difference_requires_equal_source_units_and_monotone_cumulative(bad):
    noon, close, ids = fixture()
    if 'stock_unit' in bad:
        close['stocks'][0]['amount_unit'] = bad['stock_unit']
    elif 'stock_amount' in bad:
        close['stocks'][0]['amount'] = bad['stock_amount']
    elif 'currency' in bad:
        close['stocks'][0]['currency'] = bad['currency']
    else:
        close.update(bad)
    row = indexed(build_close_review(noon, close))[ids[0]]
    assert row['amount_delta'] is None and row['amount_delta_reason']
    summary = build_close_review(noon, close)['summary']['amount']
    assert summary['complete'] is False
    assert summary['valid_count'] <= 9


def test_price_pair_remains_comparable_without_daily_percentage_baseline():
    noon, close, ids = fixture()
    noon['stocks'][0].update(prev_close=None, change_pct=None)
    result = build_close_review(noon, close)
    common = result['summary']['breadth']['common']
    assert common['count'] == 10
    assert common['noon']['valid'] == 9
    assert common['afternoon']['valid'] == 10
    assert indexed(result)[ids[0]]['afternoon_change_pct'] == 0


def test_classification_price_coverage_and_event_coverage_are_separate():
    noon, close, ids = fixture()
    close['events'] = {key: {'state': 'unknown', 'items': []} for key in close['events']}
    result = build_close_review(noon, close)
    group = result['classification_counts']['industries'][0]
    focused = len(result['items'])
    assert group['valid_count'] == focused and group['unknown_count'] == 0
    assert group['unknown_close_state_count'] == focused
    assert group['afternoon_up_count'] + group['afternoon_down_count'] + group['afternoon_flat_count'] == focused


def test_same_day_previous_price_basis_handles_corporate_action_without_previous_day_close():
    noon, close, ids = fixture()
    noon['stocks'][0].update(prev_close=20, latest_price=22, change_pct=10)
    noon['events']['limit_up']['items'][0]['previous_close'] = 22
    close['stocks'][0].update(close=23, prev_close=20, change_pct=None)
    close['events']['limit_up']['items'][0]['previous_close'] = 23
    row = indexed(build_close_review(noon, close))[ids[0]]
    assert row['close_change_pct'] == pytest.approx(15)
    assert row['afternoon_change_pct'] == pytest.approx((23/22-1)*100)
    assert build_close_review(noon, close)['summary']['breadth']['close']['valid'] == 10


def test_explicit_wrong_noon_day_cannot_supply_daily_change_baseline():
    noon, close, ids = fixture()
    noon['stocks'][0].update(date='2026-10-27', prev_close=1)
    close['stocks'][0].pop('prev_close')
    close['stocks'][0]['change_pct'] = 1000
    row = indexed(build_close_review(noon, close))[ids[0]]
    assert row['noon_price'] is None and row['afternoon_change_pct'] is None
    assert row['close_price'] == 11 and row['close_change_pct'] is None


def test_classification_identity_date_and_frozen_members_are_preserved():
    noon, close, ids = fixture()
    original = noon['stock_classifications'][ids[0]]['topics'][0]
    noon['stock_classifications'][ids[0]]['topics'] += [deepcopy(original), {**original, 'topic_code': '885002.TI'},
        {**original, 'topic_code': '885003.TI', 'members_observed_date': '2026-10-29'}]
    close['stock_classifications'] = {ids[0]: {'industries': [{'name': '今日新归属'}]}}
    result = build_close_review(noon, close)
    row = indexed(result)[ids[0]]
    assert len(row['topics']) == 2
    assert all(tag['members_observed_date'] == PRIOR for tag in row['topics'])
    assert {tag['topic_code'] for tag in row['topics']} == {'885001.TI', '885002.TI'}
    groups = result['classification_counts']['topics']
    assert len(groups) == 2 and groups[0]['count'] == 10
    assert groups[0]['key'] == 'concept|fuyao|885001.TI|' + PRIOR


def test_missing_classification_quote_mean_is_null_zero_is_preserved():
    noon, close, ids = fixture()
    noon['stock_classifications'] = {ids[0]: noon['stock_classifications'][ids[0]]}
    close['stocks'] = [row for row in close['stocks'] if row['instrument_id'] != ids[0]]
    group = build_close_review(noon, close)['classification_counts']['industries'][0]
    assert group['valid_count'] == 0 and group['mean_afternoon_change_pct'] is None
    close['stocks'].append(quote(ids[0], 11, closing=True))
    group = build_close_review(noon, close)['classification_counts']['industries'][0]
    assert group['mean_afternoon_change_pct'] == 0


def test_sector_index_same_code_kind_only_and_member_results_survive_absent_index():
    noon, close, _ = fixture()
    noon['sectors'] = [{'instrument_id': '881001.TI', 'kind': 'industry', 'latest_price': 100,
                        'change_pct': 1, 'date': DAY, 'price_basis': 'none'}]
    close['sectors'] = [{'instrument_id': '881001.TI', 'kind': 'industry', 'close': 105,
                         'change_pct': 6.05, 'date': DAY, 'price_basis': 'none', 'source': 'fuyao'}]
    group = build_close_review(noon, close)['classification_counts']['industries'][0]
    assert group['index_afternoon_change_pct'] == pytest.approx(5)
    rule = next(row for row in build_close_review(noon, close)['observations'] if row.get('kind') == 'sector')
    assert rule['sector_objective']['topic_code'] == '881001.TI'
    assert rule['sector_objective']['afternoon_change_pct'] == pytest.approx(5)
    assert not rule['instrument_ids']
    close['sectors'][0]['kind'] = 'concept'
    group = build_close_review(noon, close)['classification_counts']['industries'][0]
    assert group['index_close_change_pct'] is None
    assert group['mean_afternoon_change_pct'] is not None
    close['sectors'][0].update(kind='industry', source='another-provider')
    group = build_close_review(noon, close)['classification_counts']['industries'][0]
    assert group['index_close_change_pct'] is None and group['mean_afternoon_change_pct'] is not None


@pytest.mark.parametrize('name', [None, '', '合成方向'])
def test_legacy_sector_optional_name_and_member_fields_do_not_block_review(name):
    noon, close, ids = fixture()
    row = {'instrument_id': '881001.TI', 'kind': 'industry', 'latest_price': 100,
           'change_pct': 1, 'date': DAY, 'price_basis': 'none'}
    if name is not None:
        row['name'] = name
    noon['sectors'] = [row, {}, {'name': '缺少代码的旧条目'}]
    before = deepcopy(noon)
    result = build_close_review(noon, close)
    assert noon == before
    assert indexed(result)[ids[0]]['transition'] == 'kept'
    observation = next(line for line in result['observations'] if line.get('kind') == 'sector')
    assert (name or '881001.TI') in observation['label']
    assert '成员广度缺失' in observation['evidence']
    assert observation['sector_objective']['close_price'] is None


def test_original_observation_text_kept_without_automatic_verdict():
    noon, close, ids = fixture()
    noon['afternoon_observations'] = [{'instrument_id': ids[2], 'text': '突破则关注；这句话仍是原文', 'recorded_at_ms': 123}]
    result = build_close_review(noon, close)
    line = next(line for line in result['observations'] if line.get('key') == 'noon_observation_0')
    assert line['text'] == '突破则关注；这句话仍是原文'
    assert line['objective'][0]['transition'] == 'resealed'
    assert not any(key in line for key in ('correct', 'fulfilled', 'verdict', 'falsified'))


def test_noon_rule_conditions_remain_identified_frozen_fact_derivations():
    noon, close, ids = fixture()
    result = build_close_review(noon, close)
    rules = [row for row in result['observations'] if row.get('rule_version') == 'noon-workbench-v1']
    assert rules and all(row['text'] and row['evidence'] for row in rules)
    assert all(row['recorded_at_ms'] is None and '派生' in row['observation_scope'] for row in rules)
    assert all(row['instrument_ids'] for row in rules if row['kind'] == 'stock')
    assert all(not row['instrument_ids'] for row in rules if row['kind'] == 'sector')


def test_original_leader_and_hot_order_kept_and_tomorrow_uses_actual_closing_height():
    noon, close, ids = fixture()
    result = build_close_review(noon, close)
    assert [row['instrument_id'] for row in result['summary']['leaders']['hot']] == [ids[9]]
    assert result['summary']['leaders']['hot'][0]['rank'] == 1
    assert [row['instrument_id'] for row in result['summary']['leaders']['amount']] == [ids[0], ids[9]]
    next_groups = {group['key']: group for group in result['tomorrow']['groups']}
    assert next_groups['resealed']['instrument_ids'] == [ids[2]]
    assert set(next_groups['new_limit_up']['instrument_ids']) == {ids[2], ids[5]}
    assert indexed(result)[ids[2]]['final_board_count'] == 3
    assert 'tomorrow' in indexed(result)[ids[2]]['sections']


def test_hot_missing_source_rank_not_replaced_with_list_position():
    noon, close, _ = fixture()
    noon['hot_stocks'][0].pop('rank')
    row = build_close_review(noon, close)['summary']['leaders']['hot'][0]
    assert row['rank'] is None and row['source_rank'] is None


def test_focus_budget_explicitly_partial_but_full_statistics_do_not_change():
    noon, close, _ = fixture()
    full = build_close_review(noon, close)
    close['max_focus_items'] = 2
    bounded = build_close_review(noon, close)
    assert len(bounded['items']) == 2
    assert bounded['coverage']['focus_requested_count'] == 10
    assert bounded['coverage']['omitted_item_count'] == 8
    assert bounded['summary'] == full['summary']
    assert bounded['state'] == 'partial' and any('未保存8只' in gap for gap in bounded['gaps'])


@pytest.mark.parametrize('limit', [0, -1, True, 20001])
def test_invalid_budget_rejected(limit):
    noon, close, _ = fixture()
    close['max_focus_items'] = limit
    with pytest.raises(ValueError, match='容量'):
        build_close_review(noon, close)


def test_boolean_or_days_text_height_not_guessed():
    noon, close, ids = fixture()
    for value in (True, '3天3板'):
        close['events']['limit_up']['items'][0]['previous_board_count'] = value
        row = indexed(build_close_review(noon, close))[ids[0]]
        assert row['final_board_count'] is None and row['relay_state'] == 'unknown'


def test_prior_date_must_match_verified_market_previous_day():
    noon, close, ids = fixture()
    noon['calendar']['previous_day'] = '2026-10-26'
    result = build_close_review(noon, close)
    assert result['summary']['relay']['source_count'] == 0
    assert indexed(result)[ids[0]]['previous_board_count'] is None


def test_temperature_only_with_all_five_same_rule_inputs_and_semantics():
    noon, close, _ = fixture()
    result = build_close_review(noon, close)
    temperature = result['summary']['temperature']
    assert temperature['noon']['score'] is not None
    assert temperature['close']['score'] is not None
    assert temperature['delta'] == pytest.approx(temperature['close']['score'] - temperature['noon']['score'])
    original_score = temperature['noon']['score']
    close['pool_semantics']['limit_break'] = 'unknown'
    temperature = build_close_review(noon, close)['summary']['temperature']
    assert temperature['close']['score'] is None and temperature['delta'] is None
    assert temperature['noon']['score'] == original_score


def test_market_join_5600_rows_keeps_only_union_focus_not_all_prices():
    noon, close, _ = fixture()
    noon['stocks'] = [quote(f'{600000+i:06}.SH', 10) for i in range(5600)]
    close['stocks'] = [quote(f'{600000+i:06}.SH', 10.1, closing=True) for i in range(5600)]
    noon['events'] = {key: pool() for key in ('limit_up', 'limit_break', 'limit_down')}
    close['events'] = {key: pool() for key in ('limit_up', 'limit_break', 'limit_down')}
    noon['previous_review'] = {'date': PRIOR}
    noon['summary']['top_amount'] = [{'instrument_id': '600000.SH'}, {'instrument_id': '600001.SH'}]
    noon['summary']['top_gainers'] = []
    noon['hot_stocks'] = [{'instrument_id': '600002.SH', 'rank': 1}]
    result = build_close_review(noon, close)
    assert result['summary']['breadth']['common']['count'] == 5600
    assert len(result['items']) == 3
    assert result['summary']['breadth']['common']['afternoon']['mean_pct'] == pytest.approx(1)
    assert len(json.dumps(result).encode()) < 50000
