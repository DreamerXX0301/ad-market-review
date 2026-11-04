"""冻结前日事件与本次竞价的事实对照；合成资料，不读取当前市场。"""
from copy import deepcopy

from fastapi import HTTPException
import pytest

from app import core, worker
from app.services import auction_feedback as feedback, source_service
from app.services.intraday_auction_reference import build_reference
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
    assert [item['key'] for item in result['groups'][:3]] == ['limit_up_open', 'repair', 'weaken']
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


def test_intraday_auction_height_distinguishes_prior_heights_from_quote_status():
    value = packet()
    prior = value['previous_review']['event_cohorts']['limit_up']['items']
    for row, height in zip(prior, (4, 4, None, 2, 99)):
        row['previous_board_count'] = height
    value['quotes']['items'][2].update(
        auction_pct=10, auction_price=11, pre_close_price=10, auction_volume=100,
        unit_check={'state': 'verified'},
    )

    reference = build_reference(feedback.build_feedback(value))
    assert reference['previous_date'] == PREVIOUS
    assert reference['highest_board'] == 4
    assert reference['candidate_count'] == 9
    assert reference['verified_height_count'] == 3
    assert reference['unknown_height_count'] == 1
    top, lower = reference['groups']
    assert (top['up'], top['down'], top['flat'], top['unknown']) == (1, 1, 0, 0)
    assert top['items'][0] == {
        'instrument_id': CODES[2], 'name': '合成竞价2', 'auction_pct': 10,
        'opening_reference_price': 11,
    }
    assert lower['board_count'] == 2 and lower['unknown'] == 1
    assert lower['items'][0]['auction_pct'] is None


@pytest.mark.parametrize('change', [5, 9.98, 10, 10.07, 20, 30])
def test_limit_open_is_first_and_unknown_without_independent_today_limit_evidence(change):
    value = packet()
    quote = value['quotes']['items'][2]
    quote.update(auction_pct=change, auction_price=10 * (1 + change / 100),
                 open_price=10 * (1 + change / 100), auction_unmatched=100000)
    # Unspecified source fields must not turn into a trusted limit-price contract.
    quote.update(limit_up_price=quote['auction_price'], is_limit_up=True)
    original = deepcopy(value)
    result = feedback.build_feedback(value)
    group = result['groups'][0]
    assert group['key'] == 'limit_up_open' and group['state'] == 'unknown'
    assert group['count'] is None and group['average_auction_pct'] is None and not group['items']
    assert group['reason'] == feedback.LIMIT_OPEN_REASON
    assert '不代表全天一字' in group['interpretation']
    assert groups(result)['continuation']['items'][0]['auction_pct'] == change
    assert result['coverage'] == {'requested': 9, 'classified': 6, 'unknown': 3}
    assert value == original


def test_limit_open_missing_capture_does_not_claim_zero_observations():
    group = feedback.build_feedback({'date': DAY})['groups'][0]
    assert group['key'] == 'limit_up_open' and group['count'] is None
    assert group['state'] == 'unknown' and group['items'] == []


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


def test_reader_validates_sha_and_never_changes_immutable_capture(auction_environment, monkeypatch):
    environment = auction_environment
    for pool in environment.previous['event_cohorts'].values():
        for item in pool.get('items', []): item['date'] = PREVIOUS
    job = claim(environment)
    worker.dispatch(job)
    with core.connect() as connection:
        before = tuple(connection.execute('SELECT payload_json,content_sha256 FROM market_auction_snapshot').fetchone())
    result = feedback.read_view(DAY)
    assert result['coverage']['requested'] == result['coverage']['classified'] == 2
    assert 'stock_classifications' not in result
    from app.services import auction_topic_summary
    from app.services.intraday_auction_reference import read_auction_reference

    prior_symbol = next(item['instrument_id'] for group in result['groups'] for item in group['items']
                        if item['previous_state'] == 'limit_up')
    tag = {'topic_code': '885001.TI', 'name': '合成概念', 'kind': 'concept',
           'source': 'fuyao', 'members_observed_date': PREVIOUS}

    def classify(frozen):
        from json import loads
        assert frozen == loads(before[0])
        return {'stock_classifications': {prior_symbol: {'industries': [], 'topics': [tag]}}}

    monkeypatch.setattr(auction_topic_summary, 'build_topic_summary', classify)
    reference = read_auction_reference(DAY)
    quote = next(item for group in reference['groups'] for item in group['items']
                 if item['instrument_id'] == prior_symbol)
    assert quote['themes'] == [tag]
    with core.connect() as connection:
        assert tuple(connection.execute('SELECT payload_json,content_sha256 FROM market_auction_snapshot').fetchone()) == before
        connection.execute('DROP TRIGGER retain_auction_snapshot_update')
        connection.execute('UPDATE market_auction_snapshot SET content_sha256=?', ('0' * 64,))
    with pytest.raises(HTTPException) as error:
        read_auction_reference(DAY)
    assert error.value.status_code == 409


def reference_packet(specifications):
    """明确的合成分币价格与昨日事件；不读取市场身份或实际个股。"""
    result = {'version': 'auction-snapshot-v1', 'date': DAY, 'previous_date': PREVIOUS,
              'universe': {'items': []}, 'quotes': {'items': []},
              'previous_review': {'date': PREVIOUS, 'event_cohorts': {
                  kind: {'date': PREVIOUS, 'state': 'observed_sample', 'items': []}
                  for kind in ('limit_up', 'limit_break')}}}
    for index, specification in enumerate(specifications):
        symbol, previous, price, change, kind = specification
        name = '参考筛选合成样本' + str(index)
        result['universe']['items'].append({'instrument_id': symbol, 'name': name})
        result['quotes']['items'].append({'instrument_id': symbol, 'name': name, 'state': 'observed',
            'pre_close_price': previous, 'auction_price': price, 'auction_pct': change,
            'auction_volume': 100, 'auction_amount': price * 10000, 'auction_volume_ratio': 2,
            'unit_check': {'state': 'verified'}})
        result['previous_review']['event_cohorts'][kind]['items'].append({
            'instrument_id': symbol, 'date': PREVIOUS, 'name': name, 'previous_board_count': 2})
    return result


def one_reference():
    return reference_packet([(CODES[0], 14.23, 15.65, 9.9789, 'limit_up')])


def test_rounded_reference_matches_six_synthetic_prices_not_a_ten_percent_threshold():
    # 14.23×1.1=15.653→15.65，1.49×1.1=1.639→1.64。
    # 两种分币舍入使真实比值分别约为9.9789%和10.0671%，都不能按10%硬切。
    pairs = [(14.23, 15.65, 9.9789), (18.94, 20.83, 9.9789),
             (1.49, 1.64, 10.0671), (2.98, 3.28, 10.0671),
             (4.47, 4.92, 10.0671), (7.45, 8.20, 10.0671)]
    value = reference_packet([(CODES[index], previous, price, change,
                               'limit_up' if index % 2 else 'limit_break')
                              for index, (previous, price, change) in enumerate(pairs)] + [
        (CODES[6], 10, 10.5, 5, 'limit_up'), (CODES[7], 10, 10.2, 2, 'limit_break')])
    original = deepcopy(value)
    result = feedback.build_feedback(value)
    indexed = groups(result); opening = result['groups'][0]
    assert opening['key'] == 'limit_up_open' and opening['state'] == 'reference'
    assert opening['count'] == opening['valid_count'] == 6 and opening['missing_count'] == 0
    assert opening['checked_count'] == 8 and opening['confirmed_limit_price'] is False
    assert opening['average_auction_pct'] == pytest.approx(10.0377)
    assert opening['median_auction_pct'] == pytest.approx(10.0671)
    assert [item['opening_reference']['reference_price'] for item in opening['items']] == [
        15.65, 20.83, 1.64, 3.28, 4.92, 8.20]
    assert all(item['opening_reference']['rate_pct'] == 10
               and item['opening_reference']['basis'] == feedback.LIMIT_OPEN_BASIS
               and item['opening_reference']['confirmed_limit_price'] is False for item in opening['items'])
    assert {item['previous_state'] for item in opening['items']} == {'limit_up', 'limit_break'}
    assert indexed['continuation']['count'] == indexed['repair']['count'] == 1
    assert indexed['continuation']['average_auction_pct'] == 5
    assert indexed['repair']['average_auction_pct'] == 2
    identities = [item['instrument_id'] for group in result['groups'] for item in group['items']]
    assert len(identities) == len(set(identities)) == 8
    assert result['coverage'] == {'requested': 8, 'classified': 8, 'unknown': 0}
    assert value == original


@pytest.mark.parametrize('symbol,previous,price,change,rate', [
    ('300001.SZ', 7.43, 8.92, 20.0538, 20),
    ('688001.SH', 7.43, 8.92, 20.0538, 20),
    ('830001.BJ', 5.15, 6.70, 30.0971, 30),
    ('920001.BJ', 5.15, 6.70, 30.0971, 30),
])
def test_twenty_and_thirty_percent_reference_bands_use_cent_half_up(symbol, previous, price, change, rate):
    value = reference_packet([(symbol, previous, price, change, 'limit_up')])
    result = feedback.build_feedback(value)
    opening = result['groups'][0]
    assert opening['state'] == 'reference' and opening['count'] == opening['checked_count'] == 1
    item = opening['items'][0]
    assert item['opening_reference']['reference_price'] == price
    assert item['opening_reference']['rate_pct'] == rate
    assert groups(result)['continuation']['count'] == 0
    assert result['coverage'] == {'requested': 1, 'classified': 1, 'unknown': 0}


def test_near_ten_percent_does_not_match_and_extra_source_flags_do_not_override_reference():
    value = reference_packet([(CODES[0], 10, 10.99, 9.9, 'limit_up')])
    quote = value['quotes']['items'][0]
    quote.update(limit_up_price=10.99, upper_limit=10.99, is_limit_up=True,
                 is_one_price=True, auction_unmatched=999999999)
    original = deepcopy(value)
    result = feedback.build_feedback(value)
    opening = result['groups'][0]
    assert opening['state'] == 'reference' and opening['checked_count'] == 1
    assert opening['count'] == opening['valid_count'] == 0 and opening['items'] == []
    assert opening['average_auction_pct'] is opening['median_auction_pct'] is None
    assert groups(result)['continuation']['count'] == 1
    assert value == original
    # 同样不能让来源外的冲突限价字段否决已经明确标注的常规参考价筛选。
    matched = one_reference()
    matched['quotes']['items'][0].update(limit_up_price=999, upper_limit=1, is_limit_up=False)
    assert feedback.build_feedback(matched)['groups'][0]['count'] == 1


@pytest.mark.parametrize('name', ['ST合成', '*ST合成', 'SST合成', 'S*ST合成',
                                  'N合成', 'C合成', '', None])
def test_unverified_risk_or_listing_name_does_not_move_positive_feedback(name):
    value = one_reference(); value['quotes']['items'][0]['name'] = name
    result = feedback.build_feedback(value)
    assert result['groups'][0]['state'] == 'unknown' and result['groups'][0]['count'] is None
    assert result['groups'][0]['checked_count'] == 0
    assert groups(result)['continuation']['count'] == 1
    assert result['coverage'] == {'requested': 1, 'classified': 1, 'unknown': 0}


@pytest.mark.parametrize('field,bad', [
    ('pre_close_price', None), ('pre_close_price', 0), ('pre_close_price', True),
    ('auction_volume', 0), ('auction_volume', None), ('auction_volume', True),
    ('auction_amount', 0), ('auction_amount', None),
    ('unit_check', None), ('unit_check', {'state': 'mismatch'}),
    ('unit_check', {'state': 'unknown'}), ('auction_pct', 11),
    ('auction_price', 15.651),
])
def test_incomplete_or_invalid_reference_inputs_keep_existing_positive_group(field, bad):
    value = one_reference(); value['quotes']['items'][0][field] = bad
    result = feedback.build_feedback(value)
    opening = result['groups'][0]
    assert opening['state'] == 'unknown' and opening['count'] is None and opening['items'] == []
    assert groups(result)['continuation']['count'] == 1
    assert result['coverage'] == {'requested': 1, 'classified': 1, 'unknown': 0}


@pytest.mark.parametrize('damage,expected', [
    ('missing_event', 'unknown_event'), ('wrong_day', 'unknown_event'),
    ('unknown_pool', 'unknown_event'), ('duplicate_event', 'unknown_event'),
    ('both_events', 'event_conflict'), ('duplicate_quote', 'unknown_quote'),
])
def test_reference_price_cannot_bypass_unknown_conflicting_or_duplicate_evidence(damage, expected):
    value = one_reference()
    up = value['previous_review']['event_cohorts']['limit_up']
    if damage == 'missing_event': up['items'] = []
    elif damage == 'wrong_day': up['items'][0]['date'] = DAY
    elif damage == 'unknown_pool': up['state'] = 'unknown'
    elif damage == 'duplicate_event': up['items'].append(deepcopy(up['items'][0]))
    elif damage == 'both_events':
        value['previous_review']['event_cohorts']['limit_break']['items'] = deepcopy(up['items'])
    else:
        value['quotes']['items'].append(deepcopy(value['quotes']['items'][0]))
    original = deepcopy(value)
    result = feedback.build_feedback(value)
    opening = result['groups'][0]
    assert opening['items'] == [] and opening['count'] in (None, 0)
    assert groups(result)[expected]['count'] == 1
    assert groups(result)[expected]['items'][0]['instrument_id'] == CODES[0]
    assert groups(result)['continuation']['count'] == groups(result)['repair']['count'] == 0
    assert result['coverage'] == {'requested': 1, 'classified': 0, 'unknown': 1}
    assert value == original


def test_reference_builder_uses_only_frozen_payload_even_for_recognized_prices(monkeypatch):
    value = one_reference(); original = deepcopy(value)
    monkeypatch.setattr(core, 'get_market', lambda: pytest.fail('reference screen must not read current market'))
    monkeypatch.setattr(source_service, 'client', lambda *_: pytest.fail('reference screen must not fetch'))
    monkeypatch.setattr(feedback, 'connect', lambda: pytest.fail('reference screen must not query SQL'))
    assert feedback.build_feedback(value)['groups'][0]['count'] == 1
    assert value == original
