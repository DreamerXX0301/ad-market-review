"""竞价前20样本的题材集中度；合成归属与纯计算，不推断今日一字。"""
from copy import deepcopy

import pytest

from app.services import auction_resonance as resonance, auction_topic_summary as topics_service

DAY, PRIOR, OBSERVED = '2026-10-29', '2026-10-28', '2026-10-21'
GENERATION, DIGEST = 'market-resonance-fixture', 'a' * 64
CODES = [f'{600001 + index:06d}.SH' for index in range(30)]


def packet(codes=None):
    codes = CODES if codes is None else codes
    return {'date': DAY, 'previous_date': PRIOR, 'version': 'auction-snapshot-v1',
        'universe': {'items': [{'instrument_id': code} for code in codes]},
        'quotes': {'items': [{'instrument_id': code, 'name': '共振合成样本' + code[:6], 'state': 'observed',
            'auction_price': 10 * (1 + (30 - index) / 1000), 'auction_pct': (30 - index) / 10,
            'auction_amount': 100} for index, code in enumerate(codes)]},
        'previous_review': {'date': PRIOR, 'payload': {'mode': 'previous_session_price_facts',
            'date': PRIOR, 'source_snapshot_id': GENERATION}}}


def group(code, symbols, *, kind='concept', complete=True):
    return {'topic_code': code, 'name': '同名合成题材', 'source': 'fuyao', 'kind': kind,
        'members_observed_date': OBSERVED, 'members_complete': complete, 'sample_count': len(symbols),
        'instrument_ids': list(symbols), 'source_snapshot_id': GENERATION, 'source_sha256': DIGEST,
        'state': 'observed_sample'}


def topic_view(groups=None):
    return {'version': topics_service.VERSION, 'date': DAY, 'state': 'partial',
        'source_snapshot_id': GENERATION, 'source_sha256': DIGEST,
        'groups': groups if groups is not None else [group('881001.TI', CODES[:5], kind='industry'),
            group('885001.TI', CODES[15:25]), group('885002.TI', CODES[:2]),
            group('885003.TI', CODES[-3:])]}


def test_exact_top20_and_minimum_three_sample_topics_are_ranked_without_name_merges(monkeypatch):
    payload, topics = packet(), topic_view()
    before, previous = deepcopy(payload), deepcopy(topics)
    monkeypatch.setattr(topics_service, 'build_topic_summary', lambda *_: pytest.fail('verified topics must be reused'))
    value = resonance.build_resonance(payload, topics)
    assert payload == before and topics == previous
    assert value['strong_sample']['instrument_ids'] == CODES[:20]
    assert value['strong_sample']['count'] == 20 and value['strong_sample']['valid_count'] == 30
    assert [item['topic_code'] for item in value['groups']] == ['881001.TI', '885001.TI', '885003.TI']
    assert len({item['name'] for item in value['groups']}) == 1
    first, second, last = value['groups']
    assert first['top_count'] == second['top_count'] == 5
    assert first['top_share'] == second['top_share'] == .25
    assert first['top_ratio'] == 1 and second['top_ratio'] == .5
    assert first['concentration_lift'] == pytest.approx(first['top_share'] / (first['valid_count'] / 30)) == pytest.approx(1.5)
    assert second['concentration_lift'] == pytest.approx(.75)
    assert value['units']['concentration_lift'] == 'ratio'
    assert first['average_auction_pct'] > second['average_auction_pct']
    assert last['top_count'] == 0
    assert value['coverage']['omitted_small_groups'] == 1
    assert value['rules']['top_share_denominator'] == 'actual_strong_sample_count'
    assert all(item['members_observed_date'] == OBSERVED for item in value['groups'])


def test_equal_changes_use_code_stable_tie_break_independent_of_frozen_order():
    value = packet(list(reversed(CODES)))
    for row in value['quotes']['items']: row['auction_pct'] = 5
    result = resonance.build_resonance(value, topic_view())
    assert result['strong_sample']['instrument_ids'] == CODES[:20]


def test_up_and_mean_denominators_exclude_unknown_quotes_and_amount_coverage_is_separate():
    value = packet(CODES[:5])
    for row, change in zip(value['quotes']['items'], [6, None, 0, None, -3]):
        row.update(auction_pct=change, state='observed' if change is not None else 'unknown')
    value['quotes']['items'][2]['auction_amount'] = None
    result = resonance.build_resonance(value, topic_view([group('885001.TI', CODES[:5])]))
    row = result['groups'][0]
    assert (row['sample_count'], row['valid_count'], row['missing_count']) == (5, 3, 2)
    assert row['up_count'] == 1 and row['up_ratio'] == pytest.approx(1 / 3)
    assert row['average_auction_pct'] == 1 and row['median_auction_pct'] == 0
    assert row['average'] == 1 and row['median'] == 0
    assert row['amount'] == 200 and row['amount_valid'] == 2
    assert row['top_count'] == 3 and row['top_share'] == 1 and row['top_ratio'] == 1
    assert result['strong_sample']['count'] == 3


def test_zero_is_real_and_top_ranking_may_include_negative_changes():
    value = packet(CODES[:5])
    for row, change in zip(value['quotes']['items'], [0, -.1, -.2, -.3, -.4]):
        row.update(auction_pct=change, auction_amount=0)
    result = resonance.build_resonance(value, topic_view([group('885001.TI', CODES[:5])]))
    assert result['strong_sample']['instrument_ids'] == CODES[:5]
    row = result['groups'][0]
    assert row['up_count'] == 0 and row['up_ratio'] == 0
    assert row['median_auction_pct'] == -.2 and row['average_auction_pct'] == pytest.approx(-.2)
    assert row['amount'] == 0 and row['amount_valid'] == 5
    assert '弱市也可能' in result['scope']


def test_no_valid_quote_keeps_ratios_means_and_amount_unknown():
    value = packet(CODES[:5])
    for row in value['quotes']['items']: row['state'] = 'unknown'
    result = resonance.build_resonance(value, topic_view([group('885001.TI', CODES[:5])]))
    assert result['strong_sample']['count'] == 0
    row = result['groups'][0]
    assert row['valid_count'] == 0 and row['missing_count'] == 5 and row['amount_valid'] == 0
    assert all(row[key] is None for key in ['up_count', 'up_ratio', 'average_auction_pct',
                                          'median_auction_pct', 'amount', 'top_share', 'top_ratio', 'concentration_lift'])


def test_today_one_price_anchor_is_never_inferred_from_near_limit_or_yesterday_tags():
    value = packet(CODES[:5])
    value['quotes']['items'][0].update(auction_pct=9.99, one_price_up=True)
    value['previous_review']['payload']['one_price_limit_up'] = [CODES[0]]
    value['benchmark'] = {'items': [{'instrument_id': CODES[0], 'tags': ['一字', '昨日涨停']} ]}
    result = resonance.build_resonance(value, topic_view([group('885001.TI', CODES[:5])]))
    assert result['anchors']['state'] == 'unknown'
    assert result['anchors']['items'] == result['anchors']['groups'] == []
    assert '不能以近10%' in result['anchors']['reason']


@pytest.mark.parametrize('field,bad', [('date', '2026-10-28'), ('source_snapshot_id', 'market-other'),
    ('source_sha256', 'bad')])
def test_wrong_topic_summary_identity_cannot_fill_original_membership(field, bad):
    topics = topic_view(); topics[field] = bad
    result = resonance.build_resonance(packet(), topics)
    assert result['state'] == 'unknown' and result['groups'] == [] and result['gaps']


@pytest.mark.parametrize('damage', ['future_day', 'wrong_source', 'foreign_stock', 'duplicate_stock', 'wrong_sha', 'wrong_generation'])
def test_group_source_dates_and_exact_candidate_intersection_are_required(damage):
    selected = group('885001.TI', CODES[:5])
    if damage == 'future_day': selected['members_observed_date'] = DAY
    elif damage == 'wrong_source': selected['source'] = 'other'
    elif damage == 'foreign_stock': selected['instrument_ids'][0] = '000001.SZ'
    elif damage == 'duplicate_stock': selected['instrument_ids'][0] = selected['instrument_ids'][1]
    elif damage == 'wrong_sha': selected['source_sha256'] = 'b' * 64
    else: selected['source_snapshot_id'] = 'market-other'
    result = resonance.build_resonance(packet(), topic_view([selected]))
    assert result['groups'] == [] and result['coverage']['rejected_groups'] == 1


def test_incomplete_member_lists_remain_sample_lower_bounds():
    topics = topic_view([group('885001.TI', CODES[:5], complete=False)])
    row = resonance.build_resonance(packet(), topics)['groups'][0]
    assert row['members_complete'] is False and row['state'] == 'partial'


def test_topic_summary_is_resolved_exactly_once_when_not_supplied(monkeypatch):
    calls = []
    def resolve(value):
        calls.append(value)
        return topic_view()
    monkeypatch.setattr(topics_service, 'build_topic_summary', resolve)
    assert resonance.build_resonance(packet())['groups']
    assert len(calls) == 1


def test_duplicate_or_missing_quote_identity_is_not_arbitrarily_selected():
    value = packet()
    value['quotes']['items'].append(deepcopy(value['quotes']['items'][0]))
    result = resonance.build_resonance(value, topic_view())
    assert result['state'] == 'unknown' and result['strong_sample']['count'] == 0
    assert result['groups'] == []
