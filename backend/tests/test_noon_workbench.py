"""Cross-session noon interpretation uses retained facts and unknown coverage."""
from copy import deepcopy
from hashlib import sha256
import json

from app.services.noon_workbench import build_workbench


def snapshot():
    return {'date': '2026-10-28', 'calendar': {'previous_day': '2026-10-27'},
            'previous_review': {'date': '2026-10-27', 'state': 'background_only',
                'cohort_feedback': {'date': '2026-10-27', 'source_count': 4,
                    'observed_count': 4, 'state': 'observed_sample', 'items': [
                        {'instrument_id': '600001.SH', 'name': '合成高板', 'previous_board_count': 5},
                        {'instrument_id': '600002.SH', 'name': '合成二板', 'previous_board_count': 2},
                        {'instrument_id': '600003.SH', 'name': '合成首板', 'previous_board_count': 1},
                        {'instrument_id': '600004.SH', 'name': '合成未知', 'previous_board_count': None}]}},
            'stocks': [{'instrument_id': '600001.SH', 'latest_price': 9.4, 'change_pct': -6,
                        'open': 10.1, 'high': 10.2, 'low': 9.3},
                       {'instrument_id': '600002.SH', 'latest_price': 11, 'change_pct': 10},
                       {'instrument_id': '600003.SH', 'latest_price': 10, 'change_pct': 0}],
            'sectors': [], 'events': {key: {'state': 'observed', 'items': []}
                                     for key in ('limit_up', 'limit_break', 'limit_down')}}


def test_old_snapshot_groups_are_complete_unknown_is_not_zero_and_payload_is_unchanged():
    payload = snapshot()
    payload['events']['limit_up']['items'] = [{'instrument_id': '600002.SH', 'continue_day_cnt': 3}]
    before = deepcopy(payload)
    original_sha = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    result = build_workbench(payload)
    assert payload == before
    assert sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() == original_sha
    feedback = result['previous_feedback']
    assert feedback['verified_count'] == 4 and feedback['valid_count'] == 3
    assert feedback['unknown_count'] == 1
    assert feedback['max_confirmed_height'] == 5
    assert [row['instrument_id'] for row in feedback['highest_items']] == ['600001.SH']
    assert feedback['mean_pct'] == 4 / 3 and feedback['median_pct'] == 0
    groups = {row['key']: row for row in feedback['groups']}
    assert groups['board_5_plus']['large_drop_count'] == 1
    assert groups['board_2']['pool_counts']['limit_up'] == 1
    assert groups['first']['flat'] == 1
    assert groups['height_unknown']['mean_pct'] is None
    assert groups['height_unknown']['down'] is None
    assert result['negative_feedback']['high_position_drops'][0]['instrument_id'] == '600001.SH'
    assert all(row['source_count'] is None for row in result['negative_feedback']['cohorts'])
    assert result['directions']['comparison'] == []


def test_four_and_five_plus_keep_separate_members_and_each_stock_actual_height():
    payload = snapshot()
    prior = payload['previous_review']['cohort_feedback']
    prior['items'] = [
        {'instrument_id': '600001.SH', 'previous_board_count': 5},
        {'instrument_id': '600002.SH', 'previous_board_count': 4},
        {'instrument_id': '600003.SH', 'previous_board_count': 4},
        {'instrument_id': '600004.SH', 'previous_board_count': 11}]
    groups = {row['key']: row for row in build_workbench(payload)['previous_feedback']['groups']}
    assert 'board_4_plus' not in groups
    assert groups['board_4']['count'] == 2 and groups['board_4']['mean_pct'] == 5
    assert {row['instrument_id'] for row in groups['board_4']['items']} == {'600002.SH', '600003.SH'}
    assert [row['instrument_id'] for row in groups['board_5_plus']['items']] == ['600004.SH', '600001.SH']
    assert [row['previous_board_count'] for row in groups['board_5_plus']['items']] == [11, 5]
    assert groups['board_5_plus']['mean_pct'] == -6
    assert groups['board_5_plus']['count'] == 2 and groups['board_5_plus']['valid_count'] == 1
    assert groups['board_5_plus']['unknown_count'] == 1


def test_pool_omission_does_not_mean_rejection_when_one_pool_is_partial():
    payload = snapshot()
    payload['events']['limit_down']['state'] = 'partial'
    result = build_workbench(payload)
    assert all(row['current_state'] == 'unknown' for row in result['previous_feedback']['items'])
    payload['events']['limit_up']['items'] = [{'instrument_id': '600001.SH'}]
    payload['events']['limit_break']['items'] = [{'instrument_id': '600001.SH'}]
    result = build_workbench(payload)
    assert result['previous_feedback']['items'][0]['current_state'] == 'conflict'
    assert result['previous_feedback']['pool_counts']['conflict'] == 1


def test_statistics_cover_full_sample_before_any_frontend_page():
    payload = snapshot()
    payload['stocks'] = [{'instrument_id': f'{i:06}.SH', 'latest_price': 10, 'change_pct': i - 10}
                         for i in range(21)]
    payload['previous_review']['cohort_feedback'].update(source_count=21, observed_count=21,
        items=[{'instrument_id': row['instrument_id'], 'previous_board_count': 2} for row in payload['stocks']])
    group = build_workbench(payload)['previous_feedback']['groups'][1]
    assert group['count'] == 21 and group['valid_count'] == 21
    assert group['mean_pct'] == 0 and group['median_pct'] == 0
    assert group['up'] == 10 and group['down'] == 10 and group['flat'] == 1
    assert len(group['items']) == 21


def test_ladder_uses_explicit_positive_integer_height_and_keeps_unknown():
    payload = snapshot()
    payload['events']['limit_up']['items'] = [
        {'instrument_id': '600001.SH', 'continue_day_cnt': 4},
        {'instrument_id': '600002.SH', 'continue_day_cnt': 1},
        {'instrument_id': '600003.SH', 'continue_day_cnt': True},
        {'instrument_id': '600004.SH', 'continue_day_cnt': '2天2板'},
        {'instrument_id': '600005.SH', 'continue_day_cnt': 2.5}]
    result = build_workbench(payload)['today_ladder']
    assert [(row['board_count'], row['count']) for row in result['distribution']] == [(4, 1), (1, 1)]
    assert result['unknown_count'] == 3


def test_direction_comparison_requires_same_code_kind_and_prior_date():
    payload = snapshot()
    payload['sectors'] = [
        {'instrument_id': '881001.TI', 'name': '同名方向', 'kind': 'industry', 'latest_price': 100, 'change_pct': -2},
        {'instrument_id': '885001.TI', 'name': '同名方向', 'kind': 'concept', 'latest_price': 200, 'change_pct': 3}]
    payload['previous_review']['sector_background'] = {'date': '2026-10-27', 'items': [
        {'instrument_id': '881001.TI', 'name': '旧名称', 'kind': 'industry', 'change_pct': 5},
        {'instrument_id': '885001.TI', 'kind': 'industry', 'change_pct': 6}]}
    directions = build_workbench(payload)['directions']
    assert len(directions['comparison']) == 1
    assert directions['comparison'][0]['state'] == 'turned_weak'
    assert directions['comparison'][0]['previous_change_pct'] == 5
    assert len(directions['current_leaders']) == 2
    payload['previous_review']['sector_background']['date'] = '2026-10-26'
    assert build_workbench(payload)['directions']['comparison'] == []


def test_no_review_text_is_required_for_frozen_event_feedback_and_zero_pool_is_distinct():
    payload = snapshot()
    payload['previous_review']['event_cohorts'] = {
        'limit_up': {'date': '2026-10-27', 'state': 'observed', 'source_count': 1,
                     'items': [{'instrument_id': '600002.SH', 'previous_board_count': 2}]},
        'limit_break': {'date': '2026-10-27', 'state': 'observed', 'source_count': 0, 'items': []},
        'limit_down': {'date': '2026-10-27', 'state': 'partial', 'source_count': 1,
                       'items': [{'instrument_id': '600001.SH', 'previous_board_count': None}]}}
    result = build_workbench(payload)
    assert result['previous_feedback']['verified_count'] == 1
    repair, down = result['negative_feedback']['cohorts']
    assert repair['source_count'] == 0 and repair['count'] == 0
    assert down['source_count'] == 1 and down['mean_pct'] == -6


def test_mismatched_prior_calendar_cannot_be_labeled_yesterday():
    payload = snapshot()
    payload['previous_review']['date'] = '2026-10-26'
    result = build_workbench(payload)
    assert result['previous_feedback']['source_count'] is None
    assert result['previous_feedback']['items'] == []
    assert result['previous_feedback']['valid_count'] is None
    assert result['previous_feedback']['max_confirmed_height'] is None
    assert result['previous_feedback']['highest_items'] == []
    assert all(group['pool_counts']['limit_up'] is None for group in result['previous_feedback']['groups'])


def test_missing_current_pool_is_distinct_from_a_confirmed_empty_pool():
    payload = snapshot()
    payload['events']['limit_up'] = {'state': 'unknown', 'items': []}
    assert build_workbench(payload)['today_ladder']['unknown_count'] is None
    payload['events']['limit_up'] = {'state': 'observed', 'items': []}
    assert build_workbench(payload)['today_ladder']['unknown_count'] == 0


def test_afternoon_names_specific_objects_and_saved_price_levels_without_signal():
    result = build_workbench(snapshot())
    drop = next(row for row in result['afternoon'] if row['kind'] == 'negative_feedback')
    assert drop['object'] == '600001.SH'
    assert '9.40' in drop['condition'] and '10.10' in drop['condition'] and '9.30' in drop['condition']
    assert '-6.00%' in drop['evidence']
    assert '策略信号' in drop['scope']


def test_manual_mainline_only_uses_frozen_watched_stocks_and_incomplete_prices():
    payload = snapshot()
    payload['previous_review']['observations'] = [
        {'instrument_id': '600001.SH', 'mainline': '昨日人工方向'},
        {'instrument_id': '600004.SH', 'mainline': '昨日人工方向'}]
    watched = build_workbench(payload)['directions']['watched'][0]
    assert watched['count'] == 2 and watched['unknown_count'] == 1
    assert watched['mean_pct'] == -6
    assert len(watched['items']) == 2


def test_prior_theme_and_current_pool_clues_are_separate_dated_claims():
    payload = snapshot()
    payload['events']['limit_up']['items'] = [{'instrument_id': '600002.SH', 'limit_up_reason': '午间机器人+汽车'}]
    prior = payload['previous_review']['cohort_feedback']['items'][0]
    prior.update(theme_text='昨日商贸+消费', theme_date='2026-10-27', theme_source='previous_session_pool')
    rows = {row['instrument_id']: row for row in build_workbench(payload)['previous_feedback']['items']}
    assert rows['600001.SH']['theme_source'] == 'previous_session_pool'
    assert rows['600001.SH']['theme_date'] == '2026-10-27'
    assert rows['600002.SH']['theme_text'] == '午间机器人+汽车'
    assert rows['600002.SH']['theme_source'] == 'current_noon_pool'
    assert rows['600002.SH']['theme_date'] == '2026-10-28'
    assert rows['600003.SH']['theme_text'] is None


def test_new_sector_limit_counts_distinguish_confirmed_zero_and_partial_absence():
    from app.services.noon_analysis import sector_members
    def calculate(state, items, *, complete=True, bad_member=False):
        rows = [{'instrument_id': '881001.TI', 'topic_code': '881001.TI', 'change_pct': 1}]
        topics = [{'topic_code': '881001.TI', 'observed_date': '2026-10-27',
                   'instrument_ids': ['600001.SH'] + (['invalid'] if bad_member else []), 'members_complete': complete}]
        quotes = {'600001.SH': {'latest_price': 10, 'prev_close': 10, 'change_pct': 0}}
        return sector_members(rows, topics, quotes, '2026-10-28', {'state': state, 'items': items})[0]
    assert calculate('observed', [])['member_limit_up_count'] == 0
    assert calculate('partial', [])['member_limit_up_count'] is None
    assert calculate('partial', [{'instrument_id': '600001.SH'}])['member_limit_up_state'] == 'partial'
    assert calculate('observed', [{'instrument_id': '600001.SH'}], complete=False)['member_limit_up_count'] == 1
    assert calculate('observed', [], bad_member=True)['member_limit_up_count'] is None


def test_original_generation_event_evidence_is_separate_and_never_replaces_frozen_keys():
    payload = snapshot()
    evidence = {'date': '2026-10-27', 'event_cohorts': {
        'limit_break': {'date': '2026-10-27', 'state': 'observed_sample',
                        'source_count': 1, 'items': [{'instrument_id': '600001.SH'}]}}}
    before, evidence_before = deepcopy(payload), deepcopy(evidence)
    result = build_workbench(payload, prior_event_context=evidence)
    repair = result['negative_feedback']['cohorts'][0]
    assert repair['source_count'] == 1 and repair['mean_pct'] == -6
    assert payload == before and evidence == evidence_before

    payload['previous_review']['event_cohorts'] = {'limit_break': {
        'date': '2026-10-27', 'state': 'unknown', 'source_count': None, 'items': []}}
    repair = build_workbench(payload, prior_event_context=evidence)['negative_feedback']['cohorts'][0]
    assert repair['source_count'] is None and repair['items'] == []


def test_extra_event_evidence_requires_the_exact_previous_trading_day():
    payload = snapshot()
    evidence = {'date': '2026-10-26', 'event_cohorts': {
        'limit_break': {'date': '2026-10-26', 'state': 'observed_sample',
                        'source_count': 1, 'items': [{'instrument_id': '600001.SH'}]}}}
    repair = build_workbench(payload, prior_event_context=evidence)['negative_feedback']['cohorts'][0]
    assert repair['source_count'] is None and repair['items'] == []


def test_membership_classifications_reach_feedback_and_ladder_without_creating_a_cause():
    payload = snapshot()
    payload['events']['limit_up']['items'] = [{'instrument_id': '600001.SH', 'continue_day_cnt': 5}]
    industry = {'topic_code': '881001.TI', 'name': '合成行业', 'kind': 'industry',
                'members_observed_date': '2026-10-27', 'source': 'fuyao'}
    concept = {**industry, 'topic_code': '885001.TI', 'name': '合成概念', 'kind': 'concept'}
    context = {'stock_classifications': {'600001.SH': {
        'industries': [industry], 'topics': [concept], 'state': 'observed_sample', 'reason': '名单核验通过'}}}
    before, context_before = deepcopy(payload), deepcopy(context)
    result = build_workbench(payload, membership_context=context)
    rows = [result['previous_feedback']['items'][0],
            result['negative_feedback']['high_position_drops'][0],
            result['today_ladder']['distribution'][0]['items'][0]]
    for row in rows:
        assert row['industries'] == [industry] and row['topics'] == [concept]
        assert row['membership_state'] == 'observed_sample'
    assert rows[0]['theme_text'] is None
    assert payload == before and context == context_before


def test_watched_stock_reuses_verified_yesterday_height_and_reason_with_separate_membership_date():
    payload = snapshot()
    prior = payload['previous_review']['cohort_feedback']['items'][0]
    prior.update(previous_close=10, theme_text='昨日商贸+消费', theme_date='2026-10-27',
                 theme_source='previous_session_pool')
    payload['previous_review']['observations'] = [{
        'instrument_id': '600001.SH', 'mainline': '昨日人工方向', 'previous_date': '2026-10-27'}]
    payload['events']['limit_up']['items'] = [{
        'instrument_id': '600001.SH', 'continue_day_cnt': 9, 'limit_up_reason': '午间新题材'}]
    industry = {'topic_code': '881001.TI', 'name': '合成行业', 'kind': 'industry',
                'members_observed_date': '2026-10-21', 'source': 'fuyao'}
    context = {'stock_classifications': {'600001.SH': {
        'industries': [industry], 'topics': [], 'state': 'observed_sample'}}}
    before = deepcopy(payload)
    result = build_workbench(payload, membership_context=context)
    watched = result['directions']['watched'][0]['items'][0]
    assert watched['previous_board_count'] == 5 and watched['previous_close'] == 10
    assert watched['theme_text'] == '昨日商贸+消费'
    assert watched['theme_date'] == '2026-10-27' and watched['theme_source'] == 'previous_session_pool'
    assert watched['industries'][0]['members_observed_date'] == '2026-10-21'
    assert result['today_ladder']['items'][0]['board_count'] == 9
    assert payload == before


def test_watched_stock_without_previous_evidence_never_infers_height_from_current_pool():
    payload = snapshot()
    payload['previous_review']['observations'] = [{
        'instrument_id': '600099.SH', 'mainline': '昨日人工方向', 'previous_date': '2026-10-27'}]
    payload['events']['limit_up']['items'] = [{
        'instrument_id': '600099.SH', 'continue_day_cnt': 9, 'limit_up_reason': '午间新题材'}]
    watched = build_workbench(payload)['directions']['watched'][0]['items'][0]
    assert watched['previous_board_count'] is None
    assert watched['theme_date'] == '2026-10-27' and watched['theme_source'] == 'previous_manual'
    assert watched['theme_text'] == '昨日人工方向'


def test_watched_height_join_rejects_date_conflicts_and_duplicate_previous_rows():
    payload = snapshot()
    payload['previous_review']['observations'] = [{
        'instrument_id': '600001.SH', 'mainline': '昨日人工方向', 'previous_date': '2026-10-26'}]
    watched = build_workbench(payload)['directions']['watched'][0]['items'][0]
    assert watched['previous_board_count'] is None
    payload['previous_review']['observations'][0]['previous_date'] = '2026-10-27'
    cohort = payload['previous_review']['cohort_feedback']
    cohort['items'].append(deepcopy(cohort['items'][0]))
    watched = build_workbench(payload)['directions']['watched'][0]['items'][0]
    assert watched['previous_board_count'] is None


def test_conflicting_watched_and_verified_previous_heights_are_not_silently_selected():
    payload = snapshot()
    payload['previous_review']['observations'] = [{
        'instrument_id': '600001.SH', 'mainline': '昨日人工方向', 'previous_board_count': 3,
        'previous_date': '2026-10-27'}]
    watched = build_workbench(payload)['directions']['watched'][0]['items'][0]
    assert watched['previous_board_count'] is None
    assert build_workbench(payload)['previous_feedback']['highest_items'][0]['previous_board_count'] == 5


def test_current_pool_classification_counts_use_unique_codes_dates_and_known_heights():
    payload = snapshot()
    payload['events']['limit_up']['items'] = [
        {'instrument_id': f'60000{i}.SH', 'name': f'合成{i}', 'continue_day_cnt': height}
        for i, height in enumerate((5, 3, None, 2, 1), 1)]
    payload['stocks'] = [{'instrument_id': f'60000{i}.SH', 'latest_price': 10,
                          'change_pct': i} for i in range(1, 6)]
    def tag(code, name, kind='industry', day='2026-10-21'):
        return {'topic_code': code, 'name': name, 'kind': kind,
                'members_observed_date': day, 'source': 'fuyao'}
    industry = tag('881001.TI', '合成行业甲')
    other = tag('881002.TI', '合成行业乙')
    concept = tag('885001.TI', '同名概念', 'concept')
    same_name = tag('885002.TI', '同名概念', 'concept')
    classes = {
        '600001.SH': {'industries': [industry, deepcopy(industry)], 'topics': [concept, same_name], 'state': 'observed_sample'},
        '600002.SH': {'industries': [industry], 'topics': [concept], 'state': 'observed_sample'},
        '600003.SH': {'industries': [other], 'topics': [concept], 'state': 'partial'},
        '600004.SH': {'industries': [other], 'topics': [], 'state': 'observed_sample'}}
    before, context_before = deepcopy(payload), deepcopy(classes)
    result = build_workbench(payload, membership_context={'stock_classifications': classes})
    counts = result['today_ladder']['classification_counts']
    assert counts == result['event_classification_counts']['limit_up']
    assert counts['state'] == 'partial'
    assert counts['sample_count'] == 5 and counts['classified_count'] == 4
    assert counts['unclassified_count'] == 1 and counts['partial_classification_count'] == 1
    first, second = counts['industries']
    assert [(row['topic_code'], row['count']) for row in counts['industries']] == [('881001.TI', 2), ('881002.TI', 2)]
    assert first['max_board_count'] == 5 and first['members_observed_date'] == '2026-10-21'
    assert first['board_counts'] == [{'board_count': 5, 'count': 1}, {'board_count': 3, 'count': 1}]
    assert [row['instrument_id'] for row in first['items']] == ['600001.SH', '600002.SH']
    assert second['max_board_count'] == 2 and second['unknown_board_count'] == 1
    assert [(row['topic_code'], row['count']) for row in counts['topics']] == [('885001.TI', 3), ('885002.TI', 1)]
    assert counts['topics'][0]['state'] == 'partial'
    assert '不能相加' in counts['scope']
    assert payload == before and classes == context_before


def test_each_event_pool_counts_its_own_samples_without_treating_down_days_as_up_boards():
    payload = snapshot()
    payload['events']['limit_up']['items'] = [{'instrument_id': '600001.SH', 'continue_day_cnt': 5}]
    payload['events']['limit_down']['items'] = [{'instrument_id': '600002.SH', 'continue_day_cnt': 9}]
    payload['events']['limit_break'] = {'state': 'partial', 'items': [{'instrument_id': '600003.SH'}]}
    tag = {'topic_code': '881001.TI', 'name': '合成行业', 'kind': 'industry',
           'members_observed_date': '2026-10-21', 'source': 'fuyao'}
    classes = {f'60000{i}.SH': {'industries': [tag], 'topics': [], 'state': 'observed_sample'} for i in range(1, 4)}
    counts = build_workbench(payload, membership_context={'stock_classifications': classes})['event_classification_counts']
    assert counts['limit_up']['industries'][0]['max_board_count'] == 5
    for key, symbol in (('limit_down', '600002.SH'), ('limit_break', '600003.SH')):
        group = counts[key]['industries'][0]
        assert group['count'] == 1 and group['items'][0]['instrument_id'] == symbol
        assert group['max_board_count'] is None and group['board_counts'] == []
    assert counts['limit_break']['state'] == 'partial'
    payload['events']['limit_down'] = {'state': 'unknown', 'items': []}
    unknown = build_workbench(payload)['event_classification_counts']['limit_down']
    assert unknown['state'] == 'unknown' and unknown['sample_count'] is None
    assert unknown['unclassified_count'] is None and unknown['industries'] == []


def test_empty_current_pool_counts_are_zero_and_invalid_memberships_do_not_create_groups():
    payload = snapshot()
    empty = build_workbench(payload)['today_ladder']['classification_counts']
    assert empty['state'] == 'observed_sample' and empty['sample_count'] == 0
    assert empty['unclassified_count'] == 0 and empty['topics'] == []
    payload['events']['limit_up']['items'] = [{'instrument_id': '600001.SH', 'continue_day_cnt': 2}]
    bad = {'topic_code': '881001.TI', 'name': '不应出现', 'kind': 'industry',
           'members_observed_date': '2026-10-29', 'source': 'fuyao'}
    counts = build_workbench(payload, membership_context={'stock_classifications': {
        '600001.SH': {'industries': [bad], 'topics': [], 'state': 'observed_sample'}}})['today_ladder']['classification_counts']
    assert counts['state'] == 'unknown' and counts['unclassified_count'] == 1
    assert counts['industries'] == []


def test_previous_pool_classification_counts_use_yesterday_heights_with_separate_member_dates():
    payload = snapshot()
    payload['events']['limit_up']['items'] = [{'instrument_id': '600001.SH', 'continue_day_cnt': 9}]
    payload['events']['limit_break']['items'] = [{'instrument_id': '600002.SH', 'continue_day_cnt': 7}]
    payload['events']['limit_down']['items'] = [{'instrument_id': '600003.SH', 'continue_day_cnt': 8}]
    payload['previous_review']['event_cohorts'] = {
        'limit_break': {'date': '2026-10-27', 'state': 'observed_sample', 'source_count': 1,
                        'items': [{'instrument_id': '600002.SH', 'previous_board_count': 2}]},
        'limit_down': {'date': '2026-10-27', 'state': 'partial', 'source_count': 2,
                       'unverified_count': 1, 'items': [{'instrument_id': '600003.SH', 'previous_board_count': None}]}}
    industry = {'topic_code': '881001.TI', 'name': '合成行业', 'kind': 'industry',
                'members_observed_date': '2026-10-21', 'source': 'fuyao'}
    concept = {**industry, 'topic_code': '885001.TI', 'name': '合成概念', 'kind': 'concept'}
    context = {'stock_classifications': {f'60000{i}.SH': {
        'industries': [industry], 'topics': [concept], 'state': 'observed_sample'} for i in range(1, 5)}}
    before = deepcopy(payload)
    result = build_workbench(payload, membership_context=context)
    prior = result['previous_feedback']['classification_counts']
    assert prior['sample_count'] == 4 and prior['industries'][0]['max_board_count'] == 5
    assert prior['topics'][0]['board_counts'] == [
        {'board_count': 5, 'count': 1}, {'board_count': 2, 'count': 1}, {'board_count': 1, 'count': 1}]
    assert prior['topics'][0]['unknown_board_count'] == 1
    repair, down = [row['classification_counts'] for row in result['negative_feedback']['cohorts']]
    assert repair['sample_count'] == 1 and repair['industries'][0]['max_board_count'] == 2
    assert down['sample_count'] == 1 and down['state'] == 'partial'
    assert down['industries'][0]['max_board_count'] is None and down['industries'][0]['board_counts'] == []
    for counts in (prior, repair, down):
        assert counts['topics'][0]['members_observed_date'] == '2026-10-21'
        assert '不表示昨日涨停原因' in counts['scope']
    assert result['today_ladder']['classification_counts']['industries'][0]['max_board_count'] == 9
    assert payload == before


def test_missing_or_wrong_day_previous_pool_classification_is_unknown_not_zero():
    payload = snapshot()
    payload['previous_review']['date'] = '2026-10-26'
    result = build_workbench(payload)
    counts = [result['previous_feedback']['classification_counts'],
              *[row['classification_counts'] for row in result['negative_feedback']['cohorts']]]
    assert all(row['state'] == 'unknown' and row['sample_count'] is None for row in counts)
    assert all(row['unclassified_count'] is None and row['industries'] == [] for row in counts)


def _red_ratio_classes(symbols):
    industry = {'topic_code': '881001.TI', 'name': '合成红盘率行业', 'kind': 'industry',
                'members_observed_date': '2026-10-21', 'source': 'fuyao'}
    concept = {**industry, 'topic_code': '885001.TI', 'name': '合成红盘率概念', 'kind': 'concept'}
    return {'stock_classifications': {symbol: {'industries': [industry, deepcopy(industry)],
        'topics': [concept], 'state': 'observed_sample'} for symbol in symbols}}


def test_current_negative_pool_red_ratio_uses_valid_noon_quotes_and_keeps_full_sample_count():
    payload = snapshot()
    symbols = [f'60000{i}.SH' for i in range(1, 5)]
    payload['stocks'] = [{'instrument_id': symbol, 'latest_price': 10, 'change_pct': change}
                         for symbol, change in zip(symbols, (2, -1, 0, None))]
    for key in ('limit_break', 'limit_down'):
        payload['events'][key] = {'state': 'partial', 'items': [
            {'instrument_id': symbol, 'latest_price': 88, 'change_pct': 99} for symbol in symbols]}
    context = _red_ratio_classes(symbols)
    before, context_before = deepcopy(payload), deepcopy(context)
    result = build_workbench(payload, membership_context=context)
    for key in ('limit_break', 'limit_down'):
        counts = result['event_classification_counts'][key]
        assert counts['sample_count'] == 4 and counts['classified_count'] == 4
        assert counts['up_count'] == 1 and counts['valid_count'] == 3
        assert abs(counts['red_ratio_pct'] - 100 / 3) < 1e-10
        assert counts['state'] == 'partial'
        for group in [*counts['industries'], *counts['topics']]:
            assert group['count'] == 4
            assert group['up_count'] == 1 and group['valid_count'] == 3
            assert abs(group['red_ratio_pct'] - 100 / 3) < 1e-10
            assert [row['change_pct'] for row in group['items']] == [2, 0, -1, None]
    assert payload == before and context == context_before


def test_red_ratio_zero_up_is_known_and_flat_quotes_still_enter_denominator():
    payload = snapshot()
    symbols = ['600001.SH', '600002.SH']
    payload['events']['limit_break']['items'] = [{'instrument_id': symbol} for symbol in symbols]
    payload['stocks'] = [{'instrument_id': symbol, 'latest_price': 10, 'change_pct': change}
                         for symbol, change in zip(symbols, (0, -3))]
    counts = build_workbench(payload, membership_context=_red_ratio_classes(symbols))['event_classification_counts']['limit_break']
    assert (counts['up_count'], counts['valid_count'], counts['red_ratio_pct']) == (0, 2, 0)
    assert (counts['industries'][0]['up_count'], counts['industries'][0]['valid_count'],
            counts['industries'][0]['red_ratio_pct']) == (0, 2, 0)
    assert counts['topics'][0]['count'] == 2


def test_red_ratio_requires_positive_latest_price_and_valid_pct_in_same_snapshot():
    payload = snapshot()
    symbols = [f'60000{i}.SH' for i in range(1, 6)]
    payload['events']['limit_break']['items'] = [{'instrument_id': symbol, 'change_pct': 10,
                                                 'latest_price': 10} for symbol in symbols]
    payload['stocks'] = [
        {'instrument_id': symbols[0], 'latest_price': 0, 'change_pct': 10},
        {'instrument_id': symbols[1], 'latest_price': -1, 'change_pct': 10},
        {'instrument_id': symbols[2], 'latest_price': 10, 'change_pct': False},
        {'instrument_id': symbols[3], 'latest_price': None, 'change_pct': 10}]
    counts = build_workbench(payload, membership_context=_red_ratio_classes(symbols))['event_classification_counts']['limit_break']
    assert counts['sample_count'] == 5 and counts['industries'][0]['count'] == 5
    for row in (counts, counts['industries'][0], counts['topics'][0]):
        assert row['valid_count'] == 0 and row['up_count'] is None and row['red_ratio_pct'] is None
    assert all(row['change_pct'] is None for row in counts['industries'][0]['items'])


def test_red_ratio_duplicate_identities_or_quotes_never_inflate_numerator_or_denominator():
    payload = snapshot()
    first, second = '600001.SH', '600002.SH'
    payload['events']['limit_break']['items'] = [{'instrument_id': first}, {'instrument_id': second}]
    payload['stocks'] = [
        {'instrument_id': first, 'latest_price': 10, 'change_pct': 10},
        {'instrument_id': first, 'latest_price': 10, 'change_pct': -10},
        {'instrument_id': second, 'latest_price': 10, 'change_pct': 0}]
    counts = build_workbench(payload, membership_context=_red_ratio_classes([first, second]))['event_classification_counts']['limit_break']
    assert counts['sample_count'] == 2 and counts['topics'][0]['count'] == 2
    assert (counts['up_count'], counts['valid_count'], counts['red_ratio_pct']) == (0, 1, 0)
    assert counts['industries'][0]['valid_count'] == 1
    payload['events']['limit_break']['items'].append({'instrument_id': first})
    duplicate = build_workbench(payload, membership_context=_red_ratio_classes([first, second]))['event_classification_counts']['limit_break']
    assert duplicate['sample_count'] == 1 and duplicate['industries'][0]['count'] == 1
    assert (duplicate['up_count'], duplicate['valid_count'], duplicate['red_ratio_pct']) == (0, 1, 0)


def test_previous_negative_red_ratio_describes_current_feedback_not_yesterday_event_prices():
    payload = snapshot()
    symbols = [f'60000{i}.SH' for i in range(1, 5)]
    payload['stocks'] = [{'instrument_id': symbol, 'latest_price': 10, 'change_pct': change}
                         for symbol, change in zip(symbols, (5, -2, 0, None))]
    payload['previous_review']['event_cohorts'] = {key: {
        'date': '2026-10-27', 'state': 'observed_sample', 'source_count': 4,
        'items': [{'instrument_id': symbol, 'previous_board_count': 2,
                   'change_pct': -10, 'latest_price': 20} for symbol in symbols]}
        for key in ('limit_break', 'limit_down')}
    before = deepcopy(payload)
    result = build_workbench(payload, membership_context=_red_ratio_classes(symbols))
    for cohort in result['negative_feedback']['cohorts']:
        counts = cohort['classification_counts']
        assert counts['sample_count'] == 4
        assert counts['up_count'] == 1 and counts['valid_count'] == 3
        assert abs(counts['red_ratio_pct'] - 100 / 3) < 1e-10
        for group in (*counts['industries'], *counts['topics']):
            assert group['count'] == 4 and group['up_count'] == 1 and group['valid_count'] == 3
            assert abs(group['red_ratio_pct'] - 100 / 3) < 1e-10
    assert payload == before


def test_empty_or_unknown_pool_red_ratio_does_not_invent_zero_percent():
    payload = snapshot()
    empty = build_workbench(payload)['event_classification_counts']['limit_down']
    assert empty['sample_count'] == 0 and empty['valid_count'] == 0
    assert empty['up_count'] is None and empty['red_ratio_pct'] is None
    payload['events']['limit_down']['state'] = 'unknown'
    unknown = build_workbench(payload)['event_classification_counts']['limit_down']
    assert unknown['sample_count'] is None and unknown['up_count'] is None
    assert unknown['valid_count'] is None and unknown['red_ratio_pct'] is None


def test_missing_noon_quotes_cannot_reuse_previous_event_values_for_red_ratio():
    payload = snapshot()
    symbol = '600001.SH'
    payload['stocks'] = []
    payload['previous_review']['event_cohorts'] = {key: {
        'date': '2026-10-27', 'state': 'observed_sample', 'source_count': 1,
        'items': [{'instrument_id': symbol, 'previous_board_count': 2,
                   'change_pct': 10, 'latest_price': 20}]}
        for key in ('limit_up', 'limit_break', 'limit_down')}
    result = build_workbench(payload, membership_context=_red_ratio_classes([symbol]))
    for counts in [result['previous_feedback']['classification_counts'],
                   *[cohort['classification_counts'] for cohort in result['negative_feedback']['cohorts']]]:
        assert counts['sample_count'] == 1 and counts['valid_count'] == 0
        assert counts['up_count'] is None and counts['red_ratio_pct'] is None
        assert counts['industries'][0]['items'][0]['change_pct'] is None
    # Backward compatibility: this older container explicitly froze the noon
    # quote in its cohort row, unlike a prior-day event-cohort container.
    payload['previous_review'].pop('event_cohorts')
    payload['previous_review']['cohort_feedback']['items'] = [
        {'instrument_id': symbol, 'previous_board_count': 2, 'latest_price': 20, 'change_pct': 10}]
    legacy = build_workbench(payload)['previous_feedback']
    assert legacy['items'][0]['change_pct'] == 10
    assert legacy['classification_counts']['red_ratio_pct'] == 100


def test_classification_mean_change_uses_signed_valid_quotes_including_true_zero():
    symbols = ['600001.SH', '600002.SH', '600003.SH']
    for changes, expected, valid_count in (
            ((4, -2, 1), 1, 3),
            ((5, -5, None), 0, 2),
            ((-5, -1, 0), -2, 3)):
        payload = snapshot()
        payload['events']['limit_break']['items'] = [{'instrument_id': symbol} for symbol in symbols]
        payload['stocks'] = [{'instrument_id': symbol, 'latest_price': 10, 'change_pct': change}
                             for symbol, change in zip(symbols, changes)]
        context = _red_ratio_classes(symbols)
        before, context_before = deepcopy(payload), deepcopy(context)
        counts = build_workbench(payload, membership_context=context)['event_classification_counts']['limit_break']
        for row in (counts, *counts['industries'], *counts['topics']):
            assert row['valid_count'] == valid_count
            assert row['mean_change_pct'] == expected
        assert counts['sample_count'] == 3 and counts['industries'][0]['count'] == 3
        assert payload == before and context == context_before


def test_classification_mean_change_missing_quotes_do_not_shrink_event_count_or_fill_zero():
    payload = snapshot()
    symbols = [f'60000{i}.SH' for i in range(1, 5)]
    payload['events']['limit_down']['items'] = [
        {'instrument_id': symbol, 'latest_price': 20, 'change_pct': 100} for symbol in symbols]
    payload['stocks'] = [
        {'instrument_id': symbols[0], 'latest_price': None, 'change_pct': 20},
        {'instrument_id': symbols[1], 'latest_price': 10, 'change_pct': None},
        {'instrument_id': symbols[2], 'latest_price': 10, 'change_pct': -3}]
    context = _red_ratio_classes(symbols)
    counts = build_workbench(payload, membership_context=context)['event_classification_counts']['limit_down']
    assert counts['sample_count'] == 4 and counts['industries'][0]['count'] == 4
    for row in (counts, *counts['industries'], *counts['topics']):
        assert row['valid_count'] == 1 and row['mean_change_pct'] == -3
    payload['stocks'] = []
    missing = build_workbench(payload, membership_context=context)['event_classification_counts']['limit_down']
    for row in (missing, *missing['industries'], *missing['topics']):
        assert row['valid_count'] == 0 and row['mean_change_pct'] is None
    payload['events']['limit_down']['state'] = 'unknown'
    unknown = build_workbench(payload, membership_context=context)['event_classification_counts']['limit_down']
    assert unknown['valid_count'] is None and unknown['mean_change_pct'] is None


def test_classification_mean_change_duplicates_and_previous_events_keep_noon_quote_boundary():
    payload = snapshot()
    first, second = '600001.SH', '600002.SH'
    payload['events']['limit_break']['items'] = [{'instrument_id': first}, {'instrument_id': second}]
    payload['stocks'] = [
        {'instrument_id': first, 'latest_price': 10, 'change_pct': 3},
        {'instrument_id': first, 'latest_price': 10, 'change_pct': -3},
        {'instrument_id': second, 'latest_price': 10, 'change_pct': -4}]
    context = _red_ratio_classes([first, second])
    payload['previous_review']['event_cohorts'] = {'limit_break': {
        'date': '2026-10-27', 'state': 'observed_sample', 'source_count': 2,
        'items': [{'instrument_id': symbol, 'latest_price': 20, 'change_pct': 100}
                  for symbol in (first, second)]}}
    before = deepcopy(payload)
    result = build_workbench(payload, membership_context=context)
    current = result['event_classification_counts']['limit_break']
    previous = result['negative_feedback']['cohorts'][0]['classification_counts']
    for counts in (current, previous):
        assert counts['sample_count'] == 2
        for row in (counts, *counts['industries'], *counts['topics']):
            assert row['valid_count'] == 1 and row['mean_change_pct'] == -4
    assert payload == before
    payload['events']['limit_break']['items'].append({'instrument_id': first})
    duplicate = build_workbench(payload, membership_context=context)['event_classification_counts']['limit_break']
    assert duplicate['sample_count'] == 1
    assert duplicate['industries'][0]['count'] == 1 and duplicate['industries'][0]['mean_change_pct'] == -4
    payload['stocks'] = []
    previous_missing = build_workbench(payload, membership_context=context)['negative_feedback']['cohorts'][0]['classification_counts']
    assert previous_missing['valid_count'] == 0 and previous_missing['mean_change_pct'] is None
