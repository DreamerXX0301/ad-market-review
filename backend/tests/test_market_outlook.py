"""Dated market checklists use retained evidence, never tomorrow's prices."""
from copy import deepcopy

import pytest

from app.services import market_outlook as outlook, market_service, market_sentiment, strategy_limits
from test_market_ladder import Market, TODAY, DAYS, A, pool


def breadth(day=TODAY, up=3200, down=800, flat=0, **extra):
    return {'date': day, 'up': up, 'down': down, 'flat': flat,
            'observed': up + down + flat, 'total': up + down + flat,
            'expected_current_universe': up + down + flat, 'scope': 'dated-same-scope',
            'universe_expected_known': True, **extra}


def sentiment(*, index_state='weak', short_state='strong', ordinary_coverage=100):
    return {'index_environment': {'indices': [
                {'instrument_id': symbol, 'name': symbol, 'state': index_state,
                 'state_label': index_state, 'coverage_pct': 100,
                 'metrics': [{'key': 'index_20d_return_pct', 'value': -2 if index_state == 'weak' else 2},
                             {'key': 'index_ma20_distance_pct', 'value': -1 if index_state == 'weak' else 1}]}
                for symbol in ('000001.SH', '399001.SZ', '399006.SZ', '000688.SH')]},
            'short_term': {'state': short_state, 'sample_count': 20, 'coverage_pct': 100,
                           'cohort_source_complete': True,
                           'metrics': [{'key': 'previous_limit_up_median_return_pct', 'value': 1},
                                       {'key': 'positive_ratio_pct', 'value': 70}],
                           'groups': [{'id': 'ordinary_10', 'observed_count': 20,
                                       'identity_complete': True, 'cohort_source_complete': True,
                                       'coverage_pct': ordinary_coverage}]},
            'provenance': {'input_fingerprint': 'fixed-retained-source', 'market_collected_at': '2026-10-24T00:00:00+08:00',
                           'known_time_complete': True, 'first_known_at': '2026-01-13T15:01:00+08:00'}}


@pytest.fixture
def market(monkeypatch):
    result = Market()
    monkeypatch.setattr(strategy_limits, 'load_supplement', lambda _: {})
    return result


def build(market, **changes):
    current = changes.pop('breadth', breadth())
    previous = changes.pop('previous', breadth(DAYS[-2], 400, 3600))
    history = changes.pop('history', [previous, current])
    options = {'market': market, 'status': market.status(), 'day': TODAY, 'reference': market.ref,
               'rows': market.rows(), 'breadth': current, 'emotion': {'model': 'legacy-preserved', 'phase': 'do-not-trust'},
               'sentiment': sentiment(), 'history': history, 'previous_breadth': previous, 'ladder': {}}
    options.update(changes)
    return outlook.build_market_outlook(**options)


def test_repair_with_weak_indices_is_explicit_and_all_next_session_actions_remain_unobserved(market):
    result = build(market, sentiment=sentiment(ordinary_coverage=36.67))
    assert result['temperature']['value'] == 80
    assert result['temperature']['previous_value'] == 10
    assert result['temperature']['delta'] == 70
    assert result['stage']['code'] == 'repair' and result['stage']['label'] == '修复'
    assert result['stage']['status'] == 'observation'
    assert '仍弱' in result['stage']['summary'] and result['stage']['conflicts']
    assert any('普通10%' in reason for reason in result['stage']['limitations'])
    actions = {action['id']: action for action in result['plan']['actions']}
    assert {key: value['status'] for key, value in actions.items()} == {'open': 'blocked', 'add': 'wait', 'bottom': 'avoid'}
    assert all(action['trigger_state'] == 'unobserved' and action['triggers'] and action['invalidations'] for action in actions.values())
    assert all(scenario['trigger_state'] == 'unobserved' for scenario in result['plan']['scenarios'])
    assert result['provenance']['known_time_complete'] is False
    assert result['provenance']['first_known_at'] is None
    assert result['next_session']['date'] is None
    assert result['next_session']['label'] == '下一交易日'


@pytest.mark.parametrize('mode', ['wrong_date', 'different_scope', 'low_coverage', 'missing_history', 'duplicate_history'])
def test_repair_requires_exact_adjacent_same_scope_complete_prior_reading(market, mode):
    current = breadth()
    previous = breadth(DAYS[-2], 400, 3600)
    history = [previous, current]
    if mode == 'wrong_date': previous = {**previous, 'date': DAYS[-3]}
    elif mode == 'different_scope': previous = {**previous, 'scope': 'another-universe'}
    elif mode == 'low_coverage': previous = {**previous, 'expected_current_universe': 10000}
    elif mode == 'missing_history': history = [current]
    else: history = [previous, dict(previous), current]
    result = build(market, previous=previous, history=history)
    assert result['temperature']['previous_value'] is None
    assert result['temperature']['delta'] is None
    assert result['stage']['code'] == ('unknown' if mode in ('different_scope', 'duplicate_history') else 'broad_rise')


@pytest.mark.parametrize('change', [{'up': None}, {'up': True}, {'up': float('nan')}, {'up': -1},
                                    {'total': 10000, 'expected_current_universe': 10000},
                                    {'date': '2027-01-01'}])
def test_missing_invalid_and_low_coverage_current_evidence_is_unknown_not_mixed(market, change):
    current = breadth()
    current.update(change)
    result = build(market, breadth=current)
    assert result['stage']['code'] == 'unknown' and result['stage']['label'] == '待核验'
    assert result['plan']['stance'] == 'blocked'
    assert result['plan']['actions'][0]['status'] == 'blocked'


def test_no_direction_conflict_is_not_renamed_chaos_and_missing_axes_do_not_create_a_conflict(market):
    current = breadth(up=1800, down=2200)
    no_axes = {'provenance': {'input_fingerprint': 'fixture'}}
    result = build(market, breadth=current, previous=None, history=[current], sentiment=no_axes)
    assert result['stage']['code'] == 'divergence' and result['stage']['label'] == '分化'
    assert result['stage']['conflicts'] == []
    assert result['axes'][0]['state'] == result['axes'][2]['state'] == 'unknown'
    split = sentiment(index_state='strong')
    split['index_environment']['indices'][0]['state'] = 'weak'
    result = build(market, breadth=current, previous=None, history=[current], sentiment=split)
    assert result['stage']['code'] == 'mixed' and result['stage']['label'] == '混沌'
    assert result['stage']['conflicts']


def test_ice_has_priority_over_ebb_and_is_not_a_bottom_signal(market):
    result = build(market, breadth=breadth(up=300, down=3700), previous=breadth(DAYS[-2], 3600, 400))
    assert result['stage']['code'] == 'ice'
    assert result['plan']['actions'][2]['status'] == 'avoid'
    assert '不直接抄底' in result['plan']['actions'][2]['reason']
    ebb = build(market, breadth=breadth(up=1800, down=2700), previous=breadth(DAYS[-2], 3500, 1000))
    assert ebb['stage']['code'] == 'ebb' and ebb['plan']['stance'] == 'blocked'
    assert [action['status'] for action in ebb['plan']['actions']] == ['blocked', 'avoid', 'avoid']


def test_aligned_broad_rise_is_only_conditional_and_does_not_borrow_all_cohort_promotion(market):
    strong = sentiment(index_state='strong')
    history = [{'date': day, 'amount': 5000, 'scope': 'same-amount-scope', 'source': 'fixture'} for day in DAYS[-2:]]
    result = build(market, previous=None, sentiment=strong, turnover_history=history)
    assert result['stage']['code'] == 'broad_rise'
    assert [action['status'] for action in result['plan']['actions']] == ['conditional', 'conditional', 'avoid']
    strong['short_term']['groups'][0]['coverage_pct'] = 36
    missing_group = build(market, previous=None, sentiment=strong, turnover_history=history)
    assert [action['status'] for action in missing_group['plan']['actions']] == ['conditional', 'conditional', 'avoid']
    routes = {route['id']: route for route in missing_group['entry_gate']['routes']}
    assert routes['trend']['status'] == 'conditional'
    assert routes['relay']['status'] == 'wait'


def test_unknown_turnover_blocks_open_and_cannot_still_offer_conditional_add(market):
    result = build(market, previous=None, sentiment=sentiment(index_state='strong'), turnover_history=[])
    assert result['stage']['code'] == 'broad_rise'
    assert result['turnover']['state'] == 'unknown'
    assert result['entry_gate']['status'] == 'blocked'
    assert [action['status'] for action in result['plan']['actions']] == ['blocked', 'wait', 'avoid']


def test_high_breadth_with_missing_event_pools_is_not_climax(market):
    result = build(market, breadth=breadth(up=3600, down=400), previous=None)
    assert result['stage']['code'] == 'broad_rise'
    assert result['event_counts']['complete'] is False
    assert any('空池不当作零' in reason for reason in result['stage']['limitations'])


def test_climax_requires_both_complete_direction_pools_and_exact_positive_raw_quotes(market):
    ups = [f'600{i:03d}.SH' for i in range(100, 180)]
    downs = [f'002{i:03d}.SZ' for i in range(100, 103)]
    rows = [{'instrument_id': symbol, 'date': TODAY, 'close': 10, 'volume': 100} for symbol in ups + downs]
    reference = {'_supplement': {'annual_pools': {TODAY: pool(TODAY, ups)},
                                 'annual_down_pools': {TODAY: pool(TODAY, downs, pool_kind='limitdown')}}}
    result = build(market, rows=rows, reference=reference, breadth=breadth(up=3600, down=400), previous=None)
    assert result['stage']['code'] == 'climax'
    assert result['event_counts']['up']['confirmed_count'] == 80
    assert result['event_counts']['down']['confirmed_count'] == 3
    assert result['event_counts']['complete'] is True
    rows[0]['close'] = 10.01
    conflict = build(market, rows=rows, reference=reference, breadth=breadth(up=3600, down=400), previous=None)
    assert conflict['event_counts']['up']['unknown_count'] == 1
    assert conflict['stage']['code'] != 'climax'


@pytest.mark.parametrize('mode', ['empty', 'missing', 'zero_volume', 'duplicate_quote', 'quarantine',
                                  'future_event', 'identity_conflict', 'bad_raw_codes', 'both_directions'])
def test_event_gaps_conflicts_and_malformed_metadata_stay_unknown_not_zero_or_complete(market, mode):
    rows = market.rows()
    reference = deepcopy(market.ref)
    source = reference['_supplement']['annual_pools'][TODAY]
    if mode == 'empty': source['items'] = []
    elif mode == 'missing': rows = [row for row in rows if row['instrument_id'] != A]
    elif mode == 'zero_volume': next(row for row in rows if row['instrument_id'] == A)['volume'] = 0
    elif mode == 'duplicate_quote': rows.append(dict(next(row for row in rows if row['instrument_id'] == A)))
    elif mode == 'quarantine': source['rejected_codes'] = [A[:6]]
    elif mode == 'future_event': source['items'][0]['trade_date'] = '2027-01-01'
    elif mode == 'identity_conflict': source['items'][0]['instrument_id'] = '600001.SZ'
    elif mode == 'bad_raw_codes': source['raw_codes'] = [{}]
    else: reference['_supplement']['annual_down_pools'] = {TODAY: pool(TODAY, [A], pool_kind='limitdown')}
    result = outlook._verified_events(market, reference, rows, TODAY)
    assert result['up']['complete'] is False
    if mode not in ('empty', 'quarantine', 'bad_raw_codes'):
        assert result['up']['unknown_count'] >= 1
    assert result['complete'] is False


def watch_stock(symbol, *, price=10, board=1, amount=1000, topics=()):
    return {'instrument_id': symbol, 'name': symbol, 'date': TODAY, 'close': price, 'volume': 100,
            'change_pct': 5, 'board_count': board, 'amount': amount, 'topics': list(topics),
            'limit_up_state': True, 'limit_up_evidence': {'state': True, 'evidence': [
                {'state': True, 'status': 'confirmed', 'source': 'fixture', 'observed_prices': [price],
                 'pattern_validation': {'source_identity_valid': True, 'source_dates_valid': True,
                                        'instrument_id': symbol, 'trade_date': TODAY}}]}}


def test_watchlist_is_bounded_ranked_dated_and_never_automatically_labels_leaders(market):
    rows = []
    topics = []
    for direction in range(5):
        topic = {'topic_id': f'concept-{direction}', 'name': f'概念{direction}', 'kind': 'concept', 'observed_date': TODAY}
        topics.append({'topic_id': topic['topic_id'], 'name': topic['name'], 'kind': 'concept',
                       'membership_observed_date': TODAY, 'limit_up_observed_count': direction + 4,
                       'limit_up_count': None, 'complete': False})
        for index in range(5):
            rows.append(watch_stock(f'600{direction * 5 + index:03d}.SH', board=index + 1,
                                    amount=(direction + 1) * 1000, topics=[topic]))
    ladder = {'items': rows, 'theme_counts': topics, 'theme_coverage': {'selected_date_available': True, 'source_snapshot_day': TODAY},
              'main_coverage': {'source_pool_complete': True, 'complete': True}}
    result = build(market, ladder=ladder)['watchlist']
    assert len(result['topics']) == 3 and len(result['stocks']) == 5
    assert all(len(topic['stocks']) == 3 for topic in result['topics'])
    assert result['topics'][0]['name'] == '概念4'
    assert result['stocks'][0]['instrument_id'] == '600024.SH'
    assert all(stock['role'] == '观察候选' and stock['evidence_date'] == TODAY for stock in result['stocks'])
    assert all(topic['confirmed_limit_up_count'] is None and '至少' in topic['reason'] for topic in result['topics'])
    assert all('一字' in stock['invalidations'][1] for stock in result['stocks'])
    assert result['coverage']['stock_candidate_count'] == 25


@pytest.mark.parametrize('mode', ['missing_price', 'zero_volume', 'wrong_date', 'duplicate', 'future_source',
                                  'wrong_price', 'unconfirmed', 'adjusted_price', 'wrong_proof_stock', 'wrong_proof_date'])
def test_watch_candidates_require_exact_same_day_raw_close_and_positive_source_evidence(market, mode):
    row = watch_stock(A)
    rows = [row]
    if mode == 'missing_price': row['close'] = None
    elif mode == 'zero_volume': row['volume'] = 0
    elif mode == 'wrong_date': row['date'] = DAYS[-2]
    elif mode == 'duplicate': rows.append(deepcopy(row))
    elif mode == 'future_source': row['limit_up_evidence']['evidence'][0]['pattern_validation']['source_dates_valid'] = False
    elif mode == 'wrong_price': row['limit_up_evidence']['evidence'][0]['observed_prices'] = [10.01]
    elif mode == 'unconfirmed': row['limit_up_state'] = None
    elif mode == 'adjusted_price': row['price_basis'] = 'forward'
    elif mode == 'wrong_proof_stock': row['limit_up_evidence']['evidence'][0]['pattern_validation']['instrument_id'] = '600002.SH'
    else: row['limit_up_evidence']['evidence'][0]['pattern_validation']['trade_date'] = DAYS[-2]
    result = build(market, ladder={'items': rows})['watchlist']
    assert result['stocks'] == [] and result['empty_reason']


def test_missing_dated_members_never_uses_current_topic_proxy_but_preserves_confirmed_stock(market):
    current_tag = {'topic_id': 'new', 'kind': 'concept', 'name': '未来成员', 'observed_date': '2027-01-01'}
    row = watch_stock(A, topics=[current_tag])
    ladder = {'items': [row], 'theme_counts': [{'topic_id': 'new', 'name': '未来成员', 'kind': 'concept',
               'membership_observed_date': '2027-01-01', 'limit_up_observed_count': 1}],
              'theme_coverage': {'selected_date_available': True}}
    result = build(market, ladder=ladder)['watchlist']
    assert len(result['stocks']) == 1 and result['topics'] == [] and result['topics_empty_reason']
    ladder['theme_coverage']['selected_date_available'] = False
    assert build(market, ladder=ladder)['watchlist']['topics'] == []


def test_direction_shortlist_excludes_explicit_trading_attributes_only_not_policy_topics(market):
    labels = ['融资融券', '深股通', 'MSCI概念', '机器人概念', '国企改革']
    tags = [{'topic_id': f'topic-{index}', 'name': name, 'kind': 'concept', 'observed_date': TODAY}
            for index, name in enumerate(labels)]
    ladder = {'items': [watch_stock(A, topics=tags)],
              'theme_counts': [{'topic_id': tag['topic_id'], 'name': tag['name'], 'kind': 'concept',
                                'membership_observed_date': TODAY, 'limit_up_observed_count': 1,
                                'limit_up_count': 1, 'complete': True} for tag in tags],
              'theme_coverage': {'selected_date_available': True}}
    original = deepcopy(ladder)
    result = build(market, ladder=ladder)['watchlist']
    assert {topic['name'] for topic in result['topics']} == {'机器人概念', '国企改革'}
    assert result['coverage']['excluded_topic_count'] == 3
    assert {topic['name'] for topic in result['coverage']['excluded_topics']} == {'融资融券', '深股通', 'MSCI概念'}
    assert result['coverage']['topic_filter_version'] == outlook.TOPIC_FILTER_VERSION
    assert ladder == original


def test_next_session_uses_only_verified_calendar_not_natural_day_or_future_prices(market):
    before = build(market)
    market.calendar = lambda: [*DAYS, '2026-01-14']
    after = build(market)
    assert after['next_session']['date'] == '2026-01-14'
    assert before['next_session']['date'] is None
    for rows in market.history.values(): rows.append({'date': '2026-01-14', 'close': 999999, 'volume': 99999})
    future = build(market, rows=[rows[-2] for rows in market.history.values()])
    for result in (after, future): result['provenance'].pop('computed_at')
    assert future == after
    market.calendar = lambda: [*DAYS, TODAY]
    invalid = build(market)
    assert invalid['stage']['code'] == 'unknown' and invalid['next_session']['date'] is None


def test_returned_rules_and_context_do_not_mutate_inputs_or_future_responses(market):
    source = sentiment()
    original = deepcopy(source)
    first = build(market, sentiment=source)
    second = build(market, sentiment=source)
    assert first['provenance']['input_fingerprint'] == second['provenance']['input_fingerprint']
    first['rules'][0]['rule'] = 'mutated'
    assert build(market)['rules'][0]['rule'] != 'mutated'
    assert source == original


def test_service_adds_outlook_without_extra_panel_or_full_history_context_replay(monkeypatch):
    calls = []
    previous, current = breadth(DAYS[-2], 400, 3600), breadth()
    class OnePanel:
        def calendar(self): return DAYS
        def status(self): return {'snapshot_id': 'fixture', 'data_end': TODAY}
        def reference(self): return {}
        def breadth_history(self, day):
            calls.append(('breadth', day))
            return [previous, current]
        def instruments(self, **kwargs):
            return [{'instrument_id': A, 'kind': 'stock', 'list_date': None}]
        def panel(self, *args): raise AssertionError('Do not recompute the panel')
    market = OnePanel()
    def panel(day, **_):
        calls.append(('panel', day))
        return market, market.status(), TODAY, {}, []
    def context(*args):
        calls.append(('sentiment', args[-1]))
        return sentiment()
    monkeypatch.setattr(market_service, 'panel', panel)
    monkeypatch.setattr(market_sentiment, 'describe_sentiment', context)
    result = market_service.market_page('emotion', TODAY)
    assert calls == [('panel', TODAY), ('breadth', TODAY), ('sentiment', TODAY)]
    assert result['emotion']['model'] == 'breadth-event-proxy-v2'
    assert result['summary']['temperature'] == result['market_outlook']['temperature']['value']
    assert result['market_outlook']['watchlist']['stocks'] == []
    assert result['sentiment'] == sentiment()
