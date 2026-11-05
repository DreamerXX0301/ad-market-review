"""Same-slot direction metrics retain prices and distinguish gaps from verified zero."""
from copy import deepcopy
from hashlib import sha256

import pytest

from app.services import intraday_rotation as rotation
from app.services.noon_collection import canonical

DAY, PRIOR, GENERATION = '2026-11-05', '2026-11-04', 'market-' + 'a' * 24
CODE = '884306.TI'


def _quote(symbol, price):
    return {'instrument_id': symbol, 'latest_price': price, 'prev_close': 10,
            'change_pct': (price / 10 - 1) * 100, 'currency': 'CNY', 'price_basis': 'none'}


def _live():
    quotes = [_quote('600001.SH', 11), _quote('600002.SH', 9)]
    index = {'instrument_id': CODE, 'kind': 'industry', 'latest_price': 101,
             'prev_close': 100, 'change_pct': 1, 'currency': 'CNY', 'price_basis': 'none'}
    snapshot = {'date': DAY, 'slot': '1030', 'collected_start_at': DAY + 'T10:30:00+08:00',
                'collected_end_at': DAY + 'T10:30:10+08:00',
                'source_checks': [{'domain': key + '_page_1', 'state': 'accepted',
                                   'response_sha256': 'a' * 64, 'source_time': DAY + 'T10:30:01+08:00'}
                                  for key in ('limit_up', 'limit_break')],
                'comparison_input': {'stocks': quotes, 'sectors': [index]},
                'sealed_pool_input': {'state': 'observed', 'items': [{'instrument_id': '600001.SH'}]},
                'ladder': {'groups': [{'board_count': 2, 'items': [{'instrument_id': '600001.SH'}]}],
                           'broken_board': {'state': 'observed', 'count': 1,
                                            'items': [{'instrument_id': '600002.SH'}]}}}
    reference = {'topics': [{'topic_code': CODE, 'name': '原时点板块', 'kind': 'industry', 'source': 'fuyao',
                            'observed_date': PRIOR, 'members_complete': True,
                            'instrument_ids': ['600001.SH', '600002.SH']}]}
    return snapshot, reference


def _row(snapshot, reference):
    return rotation.live_rotation_sectors(snapshot, reference=reference, identity=(GENERATION, PRIOR))[0]


def test_original_member_breadth_and_both_pools_recovered_without_snapshot_mutation():
    snapshot, reference = _live()
    before = deepcopy(snapshot)
    row = _row(snapshot, reference)
    assert row['up_ratio'] == 50 and row['advancers'] == row['decliners'] == 1
    assert row['rankable_members'] == row['member_count'] == 2
    assert row['breadth_state'] == 'observed' and row['breadth_reason'] is None
    assert row['limit_up_count'] == row['broken_count'] == 1
    assert row['first_board_count'] == 0 and row['multi_board_count'] == 1
    assert row['broken_count_state'] == 'observed' and row['broken_count_reason'] is None
    assert snapshot == before and 'instrument_ids' not in row and 'stocks' not in row


def test_partial_quote_coverage_counts_only_valid_prices_and_explains_denominator():
    snapshot, reference = _live()
    snapshot['comparison_input']['stocks'][1]['change_pct'] = 88
    row = _row(snapshot, reference)
    assert row['up_ratio'] == 100 and row['rankable_members'] == 1
    assert row['member_coverage_pct'] == 50 and row['breadth_state'] == 'partial'
    assert '缺报价' in row['breadth_reason']
    snapshot['comparison_input']['stocks'].append(snapshot['comparison_input']['stocks'][0])
    row = _row(snapshot, reference)
    assert row['up_ratio'] is None and row['breadth_state'] == 'unknown'


def test_explicit_complete_empty_pool_is_zero_missing_or_unreceipted_pool_is_unknown():
    snapshot, reference = _live()
    snapshot['ladder']['broken_board'] = {'state': 'observed', 'count': 0, 'items': []}
    row = _row(snapshot, reference)
    assert row['broken_count'] == 0 and row['broken_count_state'] == 'observed'
    snapshot['source_checks'] = snapshot['source_checks'][:1]
    row = _row(snapshot, reference)
    assert row['broken_count'] is None and row['broken_count_state'] == 'unknown'
    assert '不代表零' in row['broken_count_reason']
    assert row['up_ratio'] == 50 and row['latest_price'] == 101


def test_conflicting_pools_and_partial_members_do_not_assert_false_exact_counts():
    snapshot, reference = _live()
    snapshot['ladder']['broken_board']['items'][0]['instrument_id'] = '600001.SH'
    row = _row(snapshot, reference)
    assert row['limit_up_count'] is None and row['broken_count'] is None
    reference['topics'][0]['members_complete'] = False
    snapshot['ladder']['broken_board'] = {'state': 'observed', 'count': 0, 'items': []}
    row = _row(snapshot, reference)
    assert row['breadth_state'] == 'partial' and row['broken_count'] is None
    reference['topics'][0]['observed_date'] = '2026-11-06'
    row = _row(snapshot, reference)
    assert row['latest_price'] == 101 and 'up_ratio' not in row
    assert row['breadth_state'] == 'unknown' and '成员' in row['breadth_reason']


def test_shared_projection_preserves_shortlist_order_and_nested_stock_samples():
    snapshot, reference = _live()
    snapshot['rotation_sectors'] = [_row(snapshot, reference)]
    samples = [{'instrument_id': '600001.SH', 'change_pct': 10}]
    row = {**snapshot['comparison_input']['sectors'][0], 'rank': 9, 'candidates': samples}
    snapshot['strong_sectors'] = [row]
    rotation.attach_sector_metrics(snapshot)
    assert row['up_ratio'] == 50 and row['broken_count'] == 1
    assert row['rank'] == 9 and row['candidates'] is samples


def test_complete_saved_height_aggregate_survives_truncated_ladder_samples():
    snapshot, reference = _live()
    snapshot['ladder']['groups'] = []
    snapshot['strong_sectors'] = [{**snapshot['comparison_input']['sectors'][0],
        'member_count': 2, 'member_date': PRIOR, 'member_list_complete': True,
        'limit_up_count': 1, 'limit_up_count_state': 'observed', 'first_board_count': 0,
        'multi_board_count': 1, 'board_height_count_state': 'observed'}]
    row = _row(snapshot, reference)
    assert row['first_board_count'] == 0 and row['multi_board_count'] == 1
    assert row['board_height_count_state'] == 'observed'
    snapshot['strong_sectors'][0]['limit_up_count'] = 2
    row = _row(snapshot, reference)
    assert row['first_board_count'] is None and row['multi_board_count'] is None


def test_quote_pool_indices_constructed_once_for_whole_rotation(monkeypatch):
    snapshot, reference = _live()
    snapshot['comparison_input']['sectors'] *= 2
    snapshot['comparison_input']['sectors'][1] = {**snapshot['comparison_input']['sectors'][1], 'instrument_id': '884307.TI'}
    reference['topics'].append({**reference['topics'][0], 'topic_code': '884307.TI'})
    original, calls = rotation._metric_inputs, []
    def counted(value):
        calls.append(value)
        return original(value)
    monkeypatch.setattr(rotation, '_metric_inputs', counted)
    assert len(rotation.live_rotation_sectors(snapshot, reference=reference, identity=(GENERATION, PRIOR))) == 2
    assert len(calls) == 1


def test_noon_frozen_member_sha_breadth_and_complete_empty_pools():
    from app.services.noon_sector_views import VERSION
    snapshot, reference = _live()
    sector = {**snapshot['comparison_input']['sectors'][0], 'topic_code': CODE,
              'members_observed_date': PRIOR, 'member_coverage': {'total': 2, 'valid': 2, 'complete': True},
              'member_up': 1, 'member_down': 1}
    entry = {'kind': 'industry', 'members_observed_date': PRIOR, 'members_complete': True,
             'instrument_ids': reference['topics'][0]['instrument_ids'], 'source': 'fuyao'}
    entry['sha256'] = sha256(canonical(entry)).hexdigest()
    payload = {'date': DAY, 'previous_review': {'date': PRIOR, 'background': {
        'date': PRIOR, 'mode': 'previous_session_price_facts', 'source_snapshot_id': GENERATION}},
        'sector_memberships': {'version': VERSION, 'source_snapshot_id': GENERATION, 'items': {CODE: entry}},
        'stocks': snapshot['comparison_input']['stocks'], 'sectors': [sector],
        'collected_start_at': DAY + 'T11:35:00+08:00', 'collected_end_at': DAY + 'T11:35:15+08:00',
        'sources': [{'domain': key + '_page_1', 'state': 'received', 'source': 'fuyao',
                     'endpoint': '/api/a-share/special-data/' + key.replace('_', '-') + '-pool',
                     'sha256': 'a' * 64, 'source_timestamp': {'state': 'observed', 'time': DAY + 'T11:35:01+08:00'}}
                    for key in ('limit_up', 'limit_break')],
        'events': {key: {'state': 'observed', 'count': 0, 'items': []} for key in ('limit_up', 'limit_break')}}
    before = deepcopy(payload)
    row = rotation.noon_rotation_sectors(payload)[0]
    assert row['up_ratio'] == 50 and row['broken_count'] == row['limit_up_count'] == 0
    assert row['first_board_count'] == row['multi_board_count'] == 0
    assert row['member_list_complete'] and row['breadth_state'] == 'observed'
    assert payload == before
    entry['sha256'] = '0' * 64
    row = rotation.noon_rotation_sectors(payload)[0]
    assert row['latest_price'] == 101 and row['breadth_state'] == 'unknown'


def test_closing_metrics_context_uses_own_prices_and_conflicts():
    snapshot, _ = _live()
    context = {'quotes': {row['instrument_id']: row for row in snapshot['comparison_input']['stocks']},
               'pools': {'limit_up': {'state': 'observed', 'count': 1, 'items': [
                   {'instrument_id': '600001.SH', 'previous_board_count': 2}]},
                         'limit_break': {'state': 'observed', 'count': 0, 'items': []}}}
    inputs = rotation._closing_metric_inputs(context)
    row = rotation._member_metric_projection(['600001.SH', '600002.SH'], PRIOR, True, inputs)
    assert row['up_ratio'] == 50 and row['broken_count'] == 0 and row['multi_board_count'] == 1
    context['pools']['limit_break'].update(count=1, items=[{'instrument_id': '600001.SH'}])
    row = rotation._member_metric_projection(['600001.SH', '600002.SH'], PRIOR, True,
                                           rotation._closing_metric_inputs(context))
    assert row['limit_up_count'] is None and row['broken_count'] is None
