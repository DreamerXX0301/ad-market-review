"""冻结前日事件与本次竞价的事实对照；合成资料，不读取当前市场。"""
from copy import deepcopy

from fastapi import HTTPException
import pytest

from app import core, worker
from app.services import auction_feedback as feedback, source_service
from test_auction_snapshots import DAY, PREVIOUS, claim, isolated as auction_environment


CODES = [f'{600001 + value:06d}.SH' for value in range(9)]


def event(index, **changes):
    return {'instrument_id': CODES[index], 'name': '前日合成事件' + str(index),
            'date': PREVIOUS, 'previous_board_count': 3, **changes}


def packet():
    changes = [6, -3, 5, -2, 0, 0, 9, None, 1]
    up, broken = [1, 2, 4, 7, 8], [0, 3, 5, 8]
    pools = {kind: {'date': PREVIOUS, 'state': 'observed_sample', 'verified_count': len(indices),
                    'items': [event(index) for index in indices]}
             for kind, indices in [('limit_up', up), ('limit_break', broken)]}
    return {'version': 'auction-snapshot-v1', 'date': DAY, 'previous_date': PREVIOUS,
        'universe': {'items': [{'instrument_id': code, 'name': '合成候选' + str(index)}
                               for index, code in enumerate(CODES)]},
        'quotes': {'items': [{'instrument_id': code, 'name': '合成竞价' + str(index),
            'state': 'observed' if change is not None else 'unknown',
            'auction_price': 10 * (1 + change / 100) if change is not None else None,
            'auction_pct': change, 'auction_amount': 100000, 'auction_volume_ratio': 1.5}
            for index, (code, change) in enumerate(zip(CODES, changes))]},
        'previous_review': {'date': PREVIOUS, 'event_cohorts': pools,
            'payload': {'date': PREVIOUS, 'mode': 'previous_session_price_facts',
                        'up': 0, 'down': 99, 'flat': 1, 'observed': 100, 'comparable': 100}}}


def groups(value):
    return {group['key']: group for group in value['groups']}


def test_all_fact_groups_are_disjoint_dated_and_keep_original_payload():
    payload = packet(); original = deepcopy(payload)
    result = feedback.build_feedback(payload)
    assert payload == original
    assert result['coverage'] == {'requested': 9, 'classified': 6, 'unknown': 3}
    assert [item['key'] for item in result['groups'][:2]] == ['repair', 'weaken']
    indexed = groups(result)
    expected = {'repair': 6, 'weaken': -3, 'continuation': 5, 'weak_continuation': -2,
                'flat_limit_up': 0, 'flat_limit_break': 0}
    for key, change in expected.items():
        assert indexed[key]['count'] == indexed[key]['valid_count'] == 1
        assert indexed[key]['missing_count'] == 0
        assert indexed[key]['average_auction_pct'] == change
        assert indexed[key]['median_auction_pct'] == change
    assert indexed['unknown_quote']['count'] == 1 and indexed['unknown_quote']['valid_count'] == 0
    assert indexed['unknown_quote']['average_auction_pct'] is None
    assert indexed['unknown_event']['items'][0]['instrument_id'] == CODES[6]
    assert indexed['event_conflict']['items'][0]['previous_state'] == 'conflict'
    identities = [item['instrument_id'] for group in result['groups'] for item in group['items']]
    assert len(identities) == len(set(identities)) == 9
    assert indexed['repair']['items'][0]['previous_board_count'] is None
    assert indexed['continuation']['items'][0]['previous_board_count'] == 3


@pytest.mark.parametrize('damage', ['cohort_day', 'unknown_cohort', 'item_day', 'missing_item_day', 'identity_conflict', 'duplicate'])
def test_unverified_or_wrong_day_event_never_becomes_a_fact_group(damage):
    value = packet()
    pool = value['previous_review']['event_cohorts']['limit_break']
    if damage == 'cohort_day': pool['date'] = DAY
    elif damage == 'unknown_cohort': pool['state'] = 'unknown'
    elif damage == 'item_day': pool['items'][0]['date'] = DAY
    elif damage == 'missing_item_day': pool['items'][0].pop('date')
    elif damage == 'identity_conflict': pool['items'][0]['thscode'] = CODES[1]
    else: pool['items'].append(deepcopy(pool['items'][0]))
    result = groups(feedback.build_feedback(value))
    assert result['repair']['count'] == 0
    assert any(item['instrument_id'] == CODES[0] for item in result['unknown_event']['items'])


@pytest.mark.parametrize('field,bad', [('auction_pct', None), ('auction_pct', True),
    ('auction_pct', float('nan')), ('auction_price', None), ('auction_price', 0), ('state', 'unknown')])
def test_invalid_quote_is_unknown_not_flat_or_repair(field, bad):
    value = packet()
    value['quotes']['items'][0][field] = bad
    result = groups(feedback.build_feedback(value))
    assert result['repair']['count'] == 0
    item = next(item for item in result['unknown_quote']['items'] if item['instrument_id'] == CODES[0])
    assert item['auction_pct'] is None and item['auction_amount'] is None


def test_duplicate_quotes_do_not_select_one_and_missing_quote_is_not_zero():
    value = packet()
    value['quotes']['items'].append(deepcopy(value['quotes']['items'][0]))
    value['quotes']['items'] = [row for row in value['quotes']['items'] if row['instrument_id'] != CODES[1]]
    result = groups(feedback.build_feedback(value))
    assert result['repair']['count'] == result['weaken']['count'] == 0
    assert result['unknown_quote']['count'] == 3
    assert result['unknown_quote']['valid_count'] == 0 and result['unknown_quote']['average_auction_pct'] is None


def test_no_current_height_or_large_pct_can_invent_yesterday_event():
    value = packet()
    value['universe']['items'][6]['previous_board_count'] = 99
    value['quotes']['items'][6]['board_count'] = 99
    up = value['previous_review']['event_cohorts']['limit_up']['items']
    next(item for item in up if item['instrument_id'] == CODES[2])['previous_board_count'] = None
    result = groups(feedback.build_feedback(value))
    assert result['unknown_event']['items'][0]['previous_board_count'] is None
    assert result['continuation']['items'][0]['previous_board_count'] is None


def test_unknown_quote_denominators_do_not_dilute_valid_group_means():
    value = packet()
    value['quotes']['items'][6]['auction_pct'] = 0
    value['quotes']['items'][6]['auction_price'] = 10
    result = groups(feedback.build_feedback(value))
    # 只有事件未知的两份有效报价仍保留真实统计；报价缺失的组分母为0。
    assert result['unknown_event']['valid_count'] == 1 and result['unknown_event']['average_auction_pct'] == 0
    assert result['unknown_quote']['valid_count'] == 0 and result['unknown_quote']['median_auction_pct'] is None


def test_outside_candidates_are_not_added_and_builder_never_calls_io(monkeypatch):
    value = packet()
    value['previous_review']['event_cohorts']['limit_up']['items'].append({
        'instrument_id': '000001.SZ', 'date': PREVIOUS, 'previous_board_count': 9})
    monkeypatch.setattr(core, 'get_market', lambda: pytest.fail('pure feedback cannot read current market'))
    monkeypatch.setattr(source_service, 'client', lambda *_: pytest.fail('pure feedback cannot fetch'))
    monkeypatch.setattr(feedback, 'connect', lambda: pytest.fail('pure feedback cannot query SQL'))
    result = feedback.build_feedback(value)
    assert result['coverage']['requested'] == 9
    assert all(item['instrument_id'] in CODES for group in result['groups'] for item in group['items'])


def test_market_breadth_is_separate_context_with_zero_and_no_causal_claim():
    value = packet()
    result = feedback.build_feedback(value)
    context = result['market_background']
    assert context['date'] == PREVIOUS and context['state'] == 'observed_background'
    assert context['up'] == 0 and context['down'] == 99
    assert '不能证明' in context['notice'] and '因果' in context['notice']
    assert all('大盘拖累' not in group['title'] for group in result['groups'])
    value['previous_review']['background'] = {**value['previous_review']['payload'], 'down': 1}
    assert feedback.build_feedback(value)['market_background']['state'] == 'unknown'
    value['previous_review']['payload']['date'] = DAY
    value['previous_review']['background']['date'] = DAY
    assert feedback.build_feedback(value)['market_background']['up'] is None


@pytest.mark.parametrize('damage', ['previous_future', 'previous_review_day', 'duplicate_universe', 'too_many'])
def test_invalid_scope_dates_or_identity_do_not_expand_or_invent_classification(damage):
    value = packet()
    if damage == 'previous_future': value['previous_date'] = DAY
    elif damage == 'previous_review_day': value['previous_review']['date'] = DAY
    elif damage == 'duplicate_universe': value['universe']['items'].append(deepcopy(value['universe']['items'][0]))
    else: value['universe']['items'] = [{'instrument_id': f'{600001 + index:06d}.SH'} for index in range(101)]
    result = feedback.build_feedback(value)
    assert result['coverage']['classified'] == 0 and result['gaps']


def test_reader_validates_sha_and_never_changes_immutable_capture(auction_environment):
    environment = auction_environment
    for pool in environment.previous['event_cohorts'].values():
        for item in pool.get('items', []): item['date'] = PREVIOUS
    job = claim(environment)
    worker.dispatch(job)
    with core.connect() as connection:
        before = tuple(connection.execute('SELECT payload_json,content_sha256 FROM market_auction_snapshot').fetchone())
    result = feedback.read_view(DAY)
    assert result['coverage']['requested'] == result['coverage']['classified'] == 2
    with core.connect() as connection:
        assert tuple(connection.execute('SELECT payload_json,content_sha256 FROM market_auction_snapshot').fetchone()) == before
        connection.execute('DROP TRIGGER retain_auction_snapshot_update')
        connection.execute('UPDATE market_auction_snapshot SET content_sha256=?', ('0' * 64,))
    with pytest.raises(HTTPException) as error:
        feedback.read_view(DAY)
    assert error.value.status_code == 409
