"""Saved adjacent pools are observations, never inferred continuous limit events."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from app.services import intraday_board_changes as changes

DAY = '2026-11-05'
A, B, C, D = ('000001.SZ', '000002.SZ', '000003.SZ', '000004.SZ')


def frame(slot, up=(), broken=(), down=()):
    clock = {'1000': '10:00', '1030': '10:30', '1330': '13:30', '1400': '14:00', '1430': '14:30'}[slot]
    stocks = [{'instrument_id': code, 'name': '股票' + code, 'latest_price': 11., 'prev_close': 10.,
               'change_pct': 10., 'amount': 1000, 'price_basis': 'none', 'currency': 'CNY'} for code in (A, B, C, D)]
    pools = {key: {'state': 'observed', 'count': len(codes),
                  'items': [{'instrument_id': code, 'continue_day_cnt': 2} for code in codes]}
             for key, codes in (('limit_up', up), ('limit_break', broken), ('limit_down', down))}
    return {'date': DAY, 'id': 'intraday-' + slot, 'slot': slot, 'version': 'intraday-snapshot-v5',
            'collected_start_at': DAY + 'T' + clock + ':00+08:00',
            'collected_end_at': DAY + 'T' + clock + ':30+08:00',
            'comparison_input': {'stocks': stocks}, 'stocks': stocks,
            'source_checks': [{'domain': key + '_page_1', 'state': 'accepted',
                               'source_time': DAY + 'T' + clock + ':05+08:00', 'response_sha256': 'a' * 64}
                              for key in pools],
            'board_pool_evidence': {'version': changes.EVIDENCE_VERSION, **pools}}


def raw_row(value):
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return {'trade_date': value['date'], 'snapshot_id': value['id'], 'slot': value['slot'],
            'payload_json': raw, 'content_sha256': sha256(raw.encode()).hexdigest()}


def compare(current, previous):
    return changes.build_changes(current, previous, current_row=raw_row(current), previous_row=raw_row(previous))


def test_exact_pool_changes_and_intersections_are_separate_and_immutable():
    prior, current = frame('1400', [A, B], [C]), frame('1430', [B, C], [A], [D])
    original = deepcopy((prior, current))
    result = compare(current, prior)
    assert result['state'] == 'observed' and result['baseline_slot'] == '1400'
    expected = {'new_sealed': C, 'new_broken': A, 'opened': A, 'resealed': C, 'new_limit_down': D}
    for key, code in expected.items():
        group = result['groups'][key]
        assert group['state'] == 'observed' and group['count'] == 1
        row = group['items'][0]
        assert row['instrument_id'] == code and row['continue_day_cnt'] == 2
        assert row['quote_context'] == {'date': DAY, 'slot': '1430', 'snapshot_id': current['id']}
        assert row.get('limit_down_state') == 'sealed' if key == 'new_limit_down' else row['limit_up_state'] in ('sealed', 'broken')
    assert result['source_bindings']['previous']['content_sha256'] == raw_row(prior)['content_sha256']
    assert (prior, current) == original


def test_old_partial_broken_samples_are_lower_bounds_and_missing_down_is_unknown():
    prior, current = frame('1400', [A, B], [C]), frame('1430', [B, C], [A])
    for value in (prior, current):
        pools = value.pop('board_pool_evidence')
        value['sealed_pool_input'] = pools['limit_up']
        value['ladder'] = {'broken_board': {**pools['limit_break'], 'state': 'partial', 'count': 20}}
    result = compare(current, prior)['groups']
    assert result['new_sealed']['count'] == 1 and result['new_sealed']['state'] == 'observed'
    for key in ('opened', 'resealed', 'new_broken'):
        assert result[key]['count'] == 1 and result[key]['state'] == 'partial'
    assert result['new_limit_down']['count'] is None
    assert result['new_limit_down']['reason_code'] == 'no_saved_down_pool'


def test_absence_never_proves_opened_or_limit_down_released():
    result = compare(frame('1430'), frame('1400', [A], [], [D]))['groups']
    assert result['opened']['count'] == 0 and not result['opened']['items']
    assert result['new_limit_down']['count'] == 0
    assert all('released' not in key for key in result)


@pytest.mark.parametrize('damage', ['sha', 'source', 'time', 'date', 'baseline'])
def test_invalid_source_identity_or_nonadjacent_time_keeps_unknown(damage):
    prior, current = frame('1400'), frame('1430', [A])
    if damage == 'source':
        prior['source_checks'] = []
    if damage == 'time':
        prior['collected_end_at'] = current['collected_start_at']
    if damage == 'date':
        prior['date'] = '2026-11-04'
    if damage == 'baseline':
        prior = frame('1330')
    record = raw_row(prior)
    if damage == 'sha':
        record['content_sha256'] = 'b' * 64
    result = changes.build_changes(current, prior, current_row=raw_row(current), previous_row=record)
    assert result['state'] == 'unknown' and all(group['count'] is None for group in result['groups'].values())


def test_conflicting_pools_and_changed_previous_close_do_not_produce_exact_claims():
    prior, current = frame('1400', [], [C]), frame('1430', [C], [C])
    result = compare(current, prior)['groups']
    assert result['new_sealed']['count'] is None and result['resealed']['count'] is None
    current = frame('1430', [C])
    current['comparison_input']['stocks'][2].update(prev_close=11, latest_price=12.1)
    result = compare(current, prior)['groups']
    assert result['new_sealed']['count'] is None and result['resealed']['count'] is None


def test_missing_candidate_quote_means_count_is_lower_bound():
    prior, current = frame('1400'), frame('1430', [A, B])
    current['comparison_input']['stocks'] = [row for row in current['comparison_input']['stocks'] if row['instrument_id'] != B]
    result = compare(current, prior)['groups']['new_sealed']
    assert result['state'] == 'partial' and result['count'] == 1


def test_noon_baseline_has_its_own_received_source_receipt_and_actual_price():
    previous = frame('1400', [A], [C])
    pools = previous['board_pool_evidence']
    noon = {'date': DAY, 'version': 'noon-snapshot-v1',
            'collected_start_at': DAY + 'T11:35:00+08:00', 'collected_end_at': DAY + 'T11:35:30+08:00',
            'stocks': previous['comparison_input']['stocks'],
            'events': {key: value for key, value in pools.items() if key != 'version'},
            'sources': [{'domain': key + '_page_1', 'state': 'received', 'source': 'fuyao',
                         'endpoint': '/api/a-share/special-data/' + key.replace('_', '-') + '-pool',
                         'sha256': 'a' * 64, 'source_timestamp': {'state': 'observed', 'time': DAY + 'T11:30:00+08:00'}}
                        for key in pools if key != 'version']}
    body = json.dumps(noon)
    record = {'trade_date': DAY, 'snapshot_id': 'noon-test', 'payload_json': body,
              'content_sha256': sha256(body.encode()).hexdigest()}
    current = frame('1330', [A, C])
    result = changes.build_changes(current, changes.noon_frame(record), current_row=raw_row(current), previous_row=record)
    assert result['groups']['resealed']['count'] == 1
    assert result['source_bindings']['previous']['slot'] == 'noon'


def test_current_positive_broken_evidence_decorates_historical_feedback_only_same_snapshot():
    value = frame('1430', [], [A])
    item = {'instrument_id': A, 'state': 'not_in_verified_pool'}
    value['relay_feedback'] = {'prior_broken_session': {'items': [item]}}
    changes.attach_current_states(value)
    assert item['state'] == 'not_in_verified_pool' and item['limit_up_state'] == 'broken'
    other = frame('1400')
    unknown = {'instrument_id': A, 'state': 'not_in_verified_pool'}
    other['relay_feedback'] = {'prior_broken_session': {'items': [unknown]}}
    changes.attach_current_states(other)
    assert 'limit_up_state' not in unknown


def test_confirmed_reseal_badge_is_shared_by_all_same_snapshot_rows_only():
    prior, current = frame('1400', [], [A]), frame('1430', [A])
    current['board_changes'] = compare(current, prior)
    feedback = {'instrument_id': A, 'state': 'promoted'}
    current['relay_feedback'] = {'prior_session': {'items': [feedback]}}
    changes.attach_current_states(current)
    entered = current['board_changes']['groups']['new_sealed']['items'][0]
    assert entered['board_transition'] == feedback['board_transition'] == current['stocks'][0]['board_transition'] == 'resealed'
    assert feedback['state'] == 'promoted' and feedback['limit_up_state'] == 'sealed'
    unknown = frame('1400', [A])
    changes.attach_current_states(unknown)
    assert 'board_transition' not in unknown['stocks'][0]
    conflicted = frame('1430', [A], [A])
    conflicted['board_changes'] = deepcopy(current['board_changes'])
    changes.attach_current_states(conflicted)
    assert 'board_transition' not in conflicted['stocks'][0]


def test_group_details_are_bounded_without_changing_verified_total():
    prior, current = frame('1400'), frame('1430')
    symbols = [f'{n:06d}.SZ' for n in range(1, 41)]
    current['board_pool_evidence']['limit_up'].update(count=40, items=[{'instrument_id': code} for code in symbols])
    current['comparison_input']['stocks'] = [{**current['stocks'][0], 'instrument_id': code} for code in symbols]
    group = compare(current, prior)['groups']['new_sealed']
    assert group['state'] == 'observed' and group['count'] == 40
    assert len(group['items']) == changes.LIMIT and group['omitted_count'] == 8


def test_new_down_pool_has_only_two_no_retry_pages_and_old_tasks_cannot_request_it(monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace
    from app.services import intraday_service as service
    from app.services.noon_collection import CHINA

    clock = datetime(2026, 11, 5, 14, 0, 1, tzinfo=CHINA)
    monkeypatch.setattr(service, 'now_china', lambda: clock)
    monkeypatch.setattr(service, '_guard_job', lambda _: None)
    calls = []
    def request(endpoint, params, *, before_attempt, **options):
        before_attempt(); calls.append((endpoint, params, options))
        return {'data': {'item': [], 'timestamp': int(clock.timestamp() * 1000)}}
    params = {'version': service.VERSION, 'board_change_evidence': changes.EVIDENCE_VERSION}
    capture = service.Capture({'parameters_json': json.dumps(params)}, DAY, service.SLOTS[4],
                              SimpleNamespace(request=request), None, 32)
    endpoint = '/api/a-share/special-data/limit-down-pool'
    assert capture.request('limit_down_page_1', endpoint, dated=True)
    assert capture.request('limit_down_page_2', endpoint, dated=True)
    assert capture.request('limit_down_page_3', endpoint, dated=True) is None
    assert len(calls) == 2 and all(call[2] == {'max_attempts': 1, 'timeout': 8.} for call in calls)
    legacy = service.Capture({'parameters_json': json.dumps({'version': service.VERSION})}, DAY,
                             service.SLOTS[4], SimpleNamespace(request=request), None, 30)
    assert legacy.request('limit_down_page_1', endpoint, dated=True) is None and len(calls) == 2


def test_three_pool_conflicts_are_removed_before_aggregates_and_block_stale_row_badges():
    from app.services.intraday_service import _reconcile_board_pools

    value = frame('1430', [A, B], [C], [A])
    pools = value['board_pool_evidence']
    conflict = _reconcile_board_pools(pools['limit_up'], pools['limit_break'], pools['limit_down'])
    assert conflict == {A}
    assert pools['limit_up']['state'] == 'partial' and pools['limit_up']['count'] == 1
    assert pools['limit_down']['state'] == 'unknown' and pools['limit_down']['count'] is None
    pools['conflicts'] = sorted(conflict)
    row = {'instrument_id': A, 'state': 'promoted', 'limit_up_state': 'sealed', 'continue_day_cnt': 4}
    value['relay_feedback'] = {'prior_session': {'items': [row]}}
    changes.attach_current_states(value)
    assert row == {'instrument_id': A, 'state': 'promoted', 'limit_up_state': 'unknown',
                   'limit_down_state': 'unknown', 'continue_day_cnt': None}
    result = compare(value, frame('1400'))['groups']
    assert result['new_sealed']['state'] == 'partial' and result['new_sealed']['count'] == 1
    assert result['new_limit_down']['state'] == 'unknown'
