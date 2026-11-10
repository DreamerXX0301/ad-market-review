from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import market_node_replay


DAYS = ['2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30', '2026-11-02',
        '2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06', '2026-11-09',
        '2026-11-10', '2026-11-11', '2026-11-12']
OLD_A, OLD_B, FIRST, GROWTH = '600001.SH', '600002.SH', '600003.SH', '300001.SZ'
PREVIOUS, NODE = '2026-11-03', '2026-11-04'


@pytest.fixture
def market(monkeypatch):
    def event(symbol, day, price, height):
        return {'thscode': symbol, 'ticker': symbol[:6], 'date': day, 'name': '样本' + symbol[:6],
                'last_price': price, 'continue_day_cnt': height}

    def quote(symbol, day, close):
        return {'instrument_id': symbol, 'date': day, 'open': close, 'high': close,
                'low': close, 'close': close, 'volume': 1000, 'source': 'fuyao'}

    prices = {
        OLD_A: [10.0, 11.0, 10.80, 10.80, 10.80, 10.80],
        OLD_B: [10.0, 11.0, 12.10, 13.31, 14.64, 16.10],
        FIRST: [10.0, 10.0, 11.0, 12.10, 13.31, 12.90],
        GROWTH: [10.0, 12.0, 14.40],
    }
    quotes = {symbol: [quote(symbol, day, price) for day, price in zip(DAYS[4:], values)]
              for symbol, values in prices.items()}
    dated_events = {
        DAYS[4]: [event(OLD_A, DAYS[4], 10.0, 3), event(OLD_B, DAYS[4], 10.0, 3)],
        PREVIOUS: [event(OLD_A, PREVIOUS, 11.0, 4), event(OLD_B, PREVIOUS, 11.0, 4),
                   event(GROWTH, PREVIOUS, 12.0, 7)],
        NODE: [event(OLD_B, NODE, 12.10, 5), event(FIRST, NODE, 11.0, 1),
               event(GROWTH, NODE, 14.40, 8)],
        DAYS[7]: [event(OLD_B, DAYS[7], 13.31, 6), event(FIRST, DAYS[7], 12.10, 2)],
        DAYS[8]: [event(OLD_B, DAYS[8], 14.64, 7)],
        DAYS[9]: [event(OLD_B, DAYS[9], 16.10, 8)],
    }
    reference = {'pool_history': {day: {'limitup': events, 'coverage': {'limitup': {'complete': True}}}
                                  for day, events in dated_events.items()}}
    reads = []

    def history(symbols, *, start, end):
        reads.append((start, end))
        return {symbol: [row for row in quotes[symbol] if start <= row['date'] <= end] for symbol in symbols}

    monkeypatch.setattr(market_node_replay, 'load_supplement_with_fingerprint', lambda *_args, **_kwargs: ({}, None))
    monkeypatch.setattr(market_node_replay, 'retained_topics_for_market_day', lambda _day: [])
    monkeypatch.setattr(market_node_replay, 'read_overrides', lambda _day: {})
    return SimpleNamespace(
        status=lambda: {'ready': True, 'data_end': DAYS[-1], 'snapshot_id': 'test-snapshot'},
        calendar=lambda: DAYS,
        reference=lambda: reference,
        instruments=lambda **_: [{'instrument_id': symbol, 'name': symbol, 'kind': 'stock',
                                  'list_date': '2000-01-01'} for symbol in prices],
        history_for_instruments=history, actions_for_instruments=lambda symbols: {symbol: [] for symbol in symbols},
        reads=reads,
    )


def _events(market, day):
    return market.reference()['pool_history'][day]['limitup']


def _topic(name, kind, symbols, *, complete=True):
    return {'topic_id': name, 'name': name, 'kind': kind, 'observed_date': PREVIOUS,
            'members_complete': complete, 'instrument_ids': symbols}


def test_node_replay_keeps_mainboard_anchors_and_uses_dated_mainline_reason(market, monkeypatch):
    for event in _events(market, PREVIOUS):
        event['limit_up_reason'] = '冰雪经济'
    for event in _events(market, NODE):
        event['limit_up_reason'] = '光伏边框+机器人布局' if event['thscode'] == OLD_B else '机器人+储能'
    alternative = [{**event, 'limit_up_reason': '光伏'} for event in _events(market, NODE)]
    monkeypatch.setattr(market_node_replay, 'load_supplement_with_fingerprint', lambda *_args, **_kwargs: (
        {'annual_pools': {NODE: {'date': NODE, 'items': alternative}}}, None))
    monkeypatch.setattr(market_node_replay, 'retained_topics_for_market_day',
                        lambda _day: pytest.fail('有效原因不读取归属'))
    result = market_node_replay.read_nodes.__wrapped__(NODE, market=market)
    assert market.reads == [(DAYS[4], NODE)]
    assert result['as_of'] == NODE
    assert result['sessions'] == []
    group, = result['groups']
    assert group['regime'] == 'main_10'
    assert group['state'] == 'highest_diverged'
    assert group['previous_height'] == 4
    assert [(row['instrument_id'], row['state']) for row in group['anchors']] == [
        (OLD_A, 'broken'), (OLD_B, 'advanced'),
    ]
    cohort, = group['cohorts']
    assert cohort['height'] == 1
    assert cohort['feedback'] == []
    assert group['anchors'][0]['attribution'] == {'basis': 'limit_up_reason', 'labels': ['冰雪经济'],
                                                  'reason': '冰雪经济', 'reason_match': 'primary',
                                                  'observed_date': PREVIOUS}
    assert cohort['members'][0]['attribution'] == {'basis': 'limit_up_reason', 'labels': ['机器人相关'],
                                                  'reason': '机器人+储能', 'reason_match': 'primary',
                                                  'observed_date': NODE}
    ladders = result['topic_ladders']
    assert {row['instrument_id']: row['height'] for row in ladders['stocks']} == {OLD_B: 5, FIRST: 1}
    direction, = ladders['items']
    assert direction['name'] == '机器人相关' and direction['basis'] == 'limit_up_reason'
    assert direction['observed_date'] is None and direction['members_complete'] is None
    assert direction['buckets'] == [{'height': 5, 'stock_ids': [OLD_B]}, {'height': 1, 'stock_ids': [FIRST]}]


def test_unique_secondary_reason_joins_existing_direction_without_counting_twice():
    samples = {
        '600001.SH': (3, '“东方系”+空气能热泵'),
        '600002.SH': (2, '“东方系”+零售'),
        '600003.SH': (3, '签下算力大单+“东方系”+光伏'),
        '600004.SH': (2, '冰雪经济+“东方系”'),
        '600005.SH': (1, '冰雪产业'),
        '600006.SH': (3, '股权转让+“东方系”+冰雪经济'),
        '600007.SH': (3, '业绩增长+“东方系”'),
        '600008.SH': (2, '业务调整+“东方系”+不涉及“东方系”'),
    }
    heights = {symbol: height for symbol, (height, _) in samples.items()}
    events = {symbol: {'limit_up_reason': reason} for symbol, (_, reason) in samples.items()}
    details, groups = market_node_replay._attributions(NODE, heights, events, [])
    result = market_node_replay._topic_ladders(heights, events, details, groups, True)
    by_name = {row['name']: row for row in result['items']}
    assert by_name['“东方系”']['buckets'] == [
        {'height': 3, 'stock_ids': ['600001.SH', '600003.SH']}, {'height': 2, 'stock_ids': ['600002.SH']}]
    assert by_name['冰雪经济']['buckets'] == [
        {'height': 2, 'stock_ids': ['600004.SH']}, {'height': 1, 'stock_ids': ['600005.SH']}]
    assert details['600003.SH']['reason_match'] == 'related'
    assert next(row for row in result['stocks'] if row['instrument_id'] == '600003.SH')['reason_match'] == 'related'
    assert details['600006.SH']['labels'] == ['股权转让']
    assert details['600007.SH']['labels'] == []
    assert details['600008.SH']['labels'] == ['业务调整']


def test_only_missing_reasons_use_a_single_dated_fallback(market, monkeypatch):
    _events(market, NODE)[1]['limit_up_reason'] = '暂无'
    topics = [_topic('光伏概念', 'concept', [FIRST, OLD_B, FIRST, GROWTH]),
              _topic('装备制造', 'industry', [FIRST, OLD_B], complete=False)]
    monkeypatch.setattr(market_node_replay, 'retained_topics_for_market_day',
                        lambda day: topics if day == NODE else [])
    result = market_node_replay.read_nodes.__wrapped__(NODE, market=market)
    fallback, = result['topic_ladders']['items']
    assert fallback['basis'] == 'membership_fallback' and fallback['name'] == '装备制造'
    assert fallback['observed_date'] == PREVIOUS and fallback['members_complete'] is False
    assert fallback['buckets'] == [{'height': 5, 'stock_ids': [OLD_B]}, {'height': 1, 'stock_ids': [FIRST]}]
    assert result['topic_ladders']['state'] == 'partial'

    _events(market, NODE)[1]['limit_up_reason'] = '公司回应：不涉及光伏'
    result = market_node_replay.read_nodes.__wrapped__(NODE, market=market)
    assert result['topic_ladders']['items'] == []
    assert result['topic_ladders']['coverage'] == {'confirmed_count': 2, 'reason_count': 1,
                                                  'fallback_count': 1, 'unclassified_count': 0}
    first, = result['groups'][0]['cohorts'][0]['members']
    assert first['attribution'] == {'basis': 'limit_up_reason', 'labels': [], 'reason': '公司回应：不涉及光伏',
                                    'reason_match': None, 'observed_date': NODE}


def test_dated_source_industry_wins_and_ambiguous_archive_keeps_unknown(market, monkeypatch):
    for event in _events(market, PREVIOUS):
        event['limit_up_reason'] = '光伏'
    public_rows = [{'instrument_id': symbol, 'source_industry': '装备制造'} for symbol in (FIRST, OLD_B)]
    monkeypatch.setattr(market_node_replay, 'load_supplement_with_fingerprint', lambda *_args, **_kwargs: (
        {'public_pools': {NODE: {'items': public_rows}}}, None))
    monkeypatch.setattr(market_node_replay, 'retained_topics_for_market_day',
                        lambda _day: pytest.fail('已有同日来源行业'))
    read = market_node_replay.read_nodes.__wrapped__
    item, = read(NODE, market=market)['topic_ladders']['items']
    assert item['name'] == '装备制造' and item['observed_date'] == NODE and item['members_complete'] is None

    public_rows.clear()
    monkeypatch.setattr(market_node_replay, 'retained_topics_for_market_day', lambda _day: [
        _topic('装备制造', 'industry', [FIRST, OLD_B]), _topic('专用设备', 'industry', [FIRST, OLD_B]),
        _topic('融资融券', 'concept', [FIRST, OLD_B]),
    ])
    assert read(NODE, market=market)['topic_ladders']['coverage']['unclassified_count'] == 2


def test_node_cache_keeps_cutoffs_separate_and_rechecks_retained_fallback(market, monkeypatch, tmp_path):
    from app.services import pane_read_cache

    monkeypatch.setattr(pane_read_cache, '_root', lambda: tmp_path)
    source = ['price-reference-supplement-v1']
    monkeypatch.setattr(market_node_replay, 'snapshot_evidence', lambda *_args: tuple(source))
    topics = [_topic('装备制造', 'industry', [FIRST, OLD_B])]
    monkeypatch.setattr(market_node_replay, 'retained_topics_for_market_day',
                        lambda day: topics if day == NODE else [])
    read = market_node_replay.read_nodes.__wrapped__
    first = read(NODE, market=market)
    assert first == read(NODE, market=market)
    assert market.reads == [(DAYS[4], NODE)]

    def guard(sources):
        assert sources[1][NODE] == topics
        raise HTTPException(403, '来源禁止外发')

    with pytest.raises(HTTPException, match='来源禁止外发'):
        read(NODE, market=market, source_guard=guard)
    assert len(market.reads) == 1
    later = read(NODE, DAYS[7], market=market)
    assert later['sessions'] == [DAYS[7]] and later['topic_ladders'] == first['topic_ladders']
    assert read(NODE, market=market) == first
    assert len(market.reads) == 2
    topics.clear()
    assert read(NODE, market=market)['topic_ladders']['state'] == 'unknown'
    source[:] = ['price-reference-supplement-v2']
    _events(market, NODE)[0]['limit_up_reason'] = '冰雪经济'
    assert read(NODE, market=market)['topic_ladders']['coverage']['reason_count'] == 1
    assert len(market.reads) == 4


def test_manual_placement_preserves_source_and_refreshes_same_day_cache(market, monkeypatch, tmp_path):
    from app.services import pane_read_cache

    monkeypatch.setattr(pane_read_cache, '_root', lambda: tmp_path)
    monkeypatch.setattr(market_node_replay, 'snapshot_evidence', lambda *_args: ('proof',))
    for event in _events(market, NODE):
        event['limit_up_reason'] = '机器人'
    for event in _events(market, PREVIOUS):
        event['limit_up_reason'] = '机器人'
    overrides = {}
    monkeypatch.setattr(market_node_replay, 'read_overrides', lambda day: overrides if day == NODE else {})
    read = market_node_replay.read_nodes.__wrapped__
    automatic = read(NODE, market=market)
    assert automatic['topic_ladders']['items'][0]['name'] == '机器人相关'
    assert list(automatic['topic_ladders']['search_names']['机器人相关']) == ['jiqirenxiangguan', 'jqrxg']

    placement = {'topic_id': 'manual:actuators', 'name': '机器人执行器', 'kind': 'concept', 'revision': 'test'}
    overrides[FIRST] = placement
    adjusted = read(NODE, market=market)
    row, = adjusted['topic_ladders']['items']
    assert row['name'] == '机器人执行器' and row['basis'] == 'manual' and row['manual_count'] == 1
    stock, = adjusted['topic_ladders']['stocks']
    assert stock['reason'] == '机器人' and stock['manual_override'] == placement
    cohort, = adjusted['groups'][0]['cohorts']
    assert cohort['members'][0]['attribution']['automatic']['labels'] == ['机器人相关']
    assert adjusted['groups'][0]['anchors'] == automatic['groups'][0]['anchors']
    assert len(market.reads) == 2
    overrides.clear()
    assert read(NODE, market=market) == automatic


def test_replay_follows_fixed_cohort_and_retains_missing_reference_conflict(market):
    result = market_node_replay.read_nodes.__wrapped__(NODE, DAYS[-1], market=market)
    assert result['topic_ladders']['state'] == 'unknown'
    assert result['topic_ladders']['items'] == []
    assert result['sessions'] == DAYS[7:12]
    assert market.reads == [(DAYS[4], DAYS[11])]
    cohort, = result['groups'][0]['cohorts']
    assert [row['instrument_id'] for row in cohort['members']] == [FIRST]
    assert [(row['advanced'], row['broken'], row['unknown']) for row in cohort['feedback'][:3]] == [
        (1, 0, 0), (0, 0, 1), (0, 1, 0),
    ]


def test_replay_rejects_non_session_feedback_cutoff(market):
    with pytest.raises(HTTPException, match='已留存的市场交易日'):
        market_node_replay.read_nodes.__wrapped__(NODE, '2026-11-07', market=market)
    assert not market.reads


def test_missing_dated_identity_does_not_turn_other_tied_stock_into_a_complete_highest(market):
    market.reference()['pool_history'][PREVIOUS]['limitup'][0].pop('name')
    result = market_node_replay.read_nodes.__wrapped__(NODE, market=market)
    group, = result['groups']
    assert result['state'] == 'partial'
    assert group['state'] == 'unknown'
    assert [row['instrument_id'] for row in group['anchors']] == [OLD_B]
