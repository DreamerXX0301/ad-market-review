"""Fixed top-1000 membership summaries retain dates, unknowns and overlap."""
from copy import deepcopy
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from app.routers.platform import router
from app.services.market_rank_distribution import ranking_distribution
from app.services import market_service as service
from app.services import stock_memberships, market_history
from test_market_topic_members import rank_scope
from test_workspace import ws
from test_watchlist import personal

DAY = '2026-11-02'
OLD = '2026-10-30'


def row(ordinal, **changes):
    return {'instrument_id': f'{ordinal:06}.SH', 'rank': ordinal, 'change_30d_pct': 2000-ordinal,
            'topics': [{'topic_id': 'stale', 'name': '不应沿用的缓存标签'}], **changes}


def tag(key, name, members, kind='concept', observed=DAY, source='fixture-provider'):
    return {'topic_id': key, 'name': name, 'kind': kind, 'observed_date': observed,
            'source': source, 'instrument_ids': members}


def test_sample_is_original_top1000_before_search_sort_page_and_table_topn(monkeypatch, rank_scope):
    topics = [tag('sample', '样本分类', [f'{rank:06}.SH' for rank in range(1, 1001)]),
              tag('outside', '榜外分类', ['001001.SH'])]
    monkeypatch.setattr(service, 'panel', lambda day, **_: (object(), {'snapshot_id': 'fixture', 'data_end': DAY}, day, {'topics': topics}, []))
    original = deepcopy(rank_scope)
    a = service.market_page('gainers', DAY, top_n=100, query='股票1500', sort_by='change_pct', sort_order='asc', page_size=25)
    b = service.market_page('gainers', DAY, rank_state='unranked', page_size=25)
    c = service.market_page('gainers', DAY, top_n=1000, sort_by='change_pct', page=2, page_size=25)
    assert a['total'] == 0 and b['total'] == 1 and c['total'] == 1000
    assert a['ranking_distribution'] == b['ranking_distribution'] == c['ranking_distribution']
    result = a['ranking_distribution']
    assert result['sample_count'] == 1000 and result['sample_scope'] == 'top_1000_before_filters'
    assert result['topics']['top'][0]['count'] == 1000
    assert result['topics']['top'][0]['share_pct'] == 100
    assert result['topics']['total_categories'] == 1
    assert result['industries']['unknown_count'] == 1000
    assert rank_scope == original


def test_actual_denominator_dedup_per_stock_and_cross_category_overlap():
    ranks = [row(1), row(2), row(3), row(4, rank=None), row(1001)]
    original = deepcopy(ranks)
    members = ['000001.SH', '000002.SH']
    topics = [tag('a', '同名分类', members+members), tag('a', '同名分类', members),
              tag('b', '同名分类', members, source='another-provider'),
              tag('manual', '人工主题', ['000003.SH'], kind='manual'),
              tag('i', '行业甲', members, kind='industry'), tag('ii', '行业乙', ['000001.SH'], kind='industry')]
    rows, result = ranking_distribution(object(), {'data_end': DAY}, {'topics': topics}, DAY, ranks)
    assert result['sample_count'] == 3
    groups = result['topics']
    assert groups['classified_count'] == 3 and groups['unknown_count'] == 0
    assert groups['total_categories'] == 3 and groups['overlap'] is True
    assert groups['top'][0]['count'] == 2 and groups['top'][0]['share_pct'] == 66.67
    assert sum(item['share_pct'] for item in groups['top']) > 100
    assert len({item['id'] for item in groups['top'] if item['name'] == '同名分类'}) == 2
    assert result['industries']['classified_count'] == 2 and result['industries']['unknown_count'] == 1
    assert result['industries']['overlap'] is True
    assert rows[0]['topics'][0]['name'] != '不应沿用的缓存标签'
    assert ranks == original


def test_unknown_dates_kinds_and_future_members_are_not_filled_as_known():
    refs = [tag('future', '未来分类', ['000001.SH'], observed='2026-11-03'),
            tag('no-date', '无日期分类', ['000001.SH'], observed=None),
            tag('no-kind', '无类型分类', ['000001.SH'], kind=None),
            tag('', '无身份分类', ['000001.SH'])]
    _, result = ranking_distribution(object(), {'data_end': DAY}, {'topics': refs}, DAY, [row(1)])
    assert result['topics']['top'] == [] and result['topics']['unknown_count'] == 1
    assert result['industries']['unknown_count'] == 1


def test_top_five_only_and_no_false_other_percentage():
    topics = [tag(str(n), f'分类{n}', ['000001.SH']) for n in range(20)]
    _, result = ranking_distribution(object(), {'data_end': DAY}, {'topics': topics}, DAY, [row(1)])
    group = result['topics']
    assert len(group['top']) == 5 and group['total_categories'] == 20
    assert group['classified_count'] == 1 and group['unknown_count'] == 0
    assert all(item['share_pct'] == 100 for item in group['top'])
    assert 'other_pct' not in group and 'other_count' not in group


def test_latest_manual_changes_are_reread_even_if_rank_price_cache_is_same(monkeypatch):
    class Market:
        def topics(self):
            return []
    calls = []
    current = [{'topic_id': 'm', 'name': '原主题', 'kind': 'manual', 'source_observed_date': None}]
    def read(market, symbols, **kwargs):
        calls.append((symbols, kwargs))
        return {'business_date': DAY, 'scope': '当前业务日归属，不代表历史成分',
                'memberships': {'000001.SH': deepcopy(current)}}
    monkeypatch.setattr(stock_memberships, 'current_memberships', read)
    ranks = [row(1)]
    _, first = ranking_distribution(Market(), {'data_end': DAY}, {}, DAY, ranks)
    current[0]['name'] = '修改后主题'
    _, second = ranking_distribution(Market(), {'data_end': DAY}, {}, DAY, ranks)
    assert first['topics']['top'][0]['name'] == '原主题'
    assert second['topics']['top'][0]['name'] == '修改后主题'
    assert len(calls) == 2 and calls[0][0] == ['000001.SH']
    assert ranks[0]['topics'][0]['topic_id'] == 'stale'
    assert second['classification_state'] == 'current_business_partial'


def test_latest_page_fills_only_missing_provider_kinds_and_keeps_dated_reads_strict(personal, monkeypatch):
    market, client = personal
    client.app.include_router(router)
    day, old = market.entry, market.days[69]
    later, newest = market.days[71:73]
    a, b = '600001.SH', '000001.SZ'
    source = [tag('dated-industry', '时点行业', [a], 'industry', day),
              tag('later-industry', '最新行业', [a, b], 'industry', newest),
              tag('later-concept', '最新概念', [a, b], observed=later),
              tag('provider-only', '另一来源概念', [b], observed=later)]
    monkeypatch.setattr(market, 'topics', lambda: deepcopy(source))
    assert client.post('/api/v1/topics/initialize', json={}).status_code == 200
    definitions = {entry['name']: entry['topic_id'] for entry in client.get('/api/v1/topics').json()['items']}
    # A current manual inclusion is no proof of a later provider membership.
    assert client.post(f'/api/v1/topics/{definitions["另一来源概念"]}/members',
                       json={'instrument_id': a, 'action': 'include'}).status_code == 200
    assert client.post('/api/v1/topics', json={'name': '当日人工题材', 'instrument_ids': [a]}).status_code == 200

    status = {'data_end': day, 'snapshot_id': 'fixture'}
    ranked = [{**row(1), 'instrument_id': a}, {**row(2), 'instrument_id': b}]
    original = deepcopy(ranked)
    monkeypatch.setattr(service, 'panel', lambda selected, **_: (market, status, selected or day, {}, []))
    monkeypatch.setattr(service, 'cached_gainers', lambda *_: (ranked, {}))

    def archive(kind, selected, *_):
        assert (kind, selected) == ('sectors', old)
        return {'topics': [tag('old', '历史行业', [a], 'industry', old), *source]}, {
            'selected_date_available': True, 'source_snapshot_id': 'old-fixture'}

    monkeypatch.setattr(market_history, 'resolve_reference', archive)
    latest = client.get('/api/v1/market/gainers').json()
    strict = service.market_page('gainers', day)
    history = client.get('/api/v1/market/gainers', params={'date': old}).json()
    lookup = service.market_page('gainers', day, gainers_lookup={'mode': 'related', 'query': '最新概念'})

    assert latest['classification_reference'] == {'date': newest, 'dates': [later, newest]}
    distribution = latest['ranking_distribution']
    assert distribution['classification_reference'] == latest['classification_reference']
    assert distribution['sample_count'] == 2 and distribution['industries']['classified_count'] == 2
    assert distribution['topics']['classified_count'] == 2
    first, second = latest['items']
    assert [item['name'] for item in first['industries']] == ['时点行业']
    assert {item['name'] for item in first['topics']} == {'时点行业', '最新概念', '当日人工题材'}
    assert {item['name'] for item in second['topics']} == {'最新行业', '最新概念', '另一来源概念'}
    reference = next(item for item in first['topics'] if item['name'] == '最新概念')
    assert reference['observed_date'] == reference['source_observed_date'] == later
    assert reference['classification_reference'] is True and first['topics_observed_date'] == day
    assert any(item['name'] == '最新概念' and item['source_observed_date'] == later
               for item in distribution['topics']['top'])
    assert [(r['instrument_id'], r['rank'], r['change_30d_pct']) for r in latest['items']] == [
        (r['instrument_id'], r['rank'], r['change_30d_pct']) for r in strict['items']]
    assert ranked == original
    for result in (strict, history):
        assert 'classification_reference' not in result and 'classification_reference' not in result['ranking_distribution']
    assert {item['name'] for item in strict['items'][0]['topics']} == {'时点行业', '当日人工题材'}
    assert strict['items'][1]['topics'] == []
    assert [item['name'] for item in history['items'][0]['topics']] == ['历史行业']
    assert history['items'][1]['topics'] == []
    assert lookup['items'] == [] and lookup['classification_lookup']['state'] == 'unknown'


def test_historical_day_uses_archive_once_not_current_manual_members(monkeypatch):
    class Market:
        def topics(self):
            return []
    monkeypatch.setattr(stock_memberships, 'current_memberships', lambda *_a, **_k: pytest.fail('no current labels on history'))
    calls = []
    def archive(kind, day, status, reference):
        calls.append((kind, day))
        return {'topics': [tag('old', '历史行业', ['000001.SH'], kind='industry', observed=OLD)]}, {'notice': '仅旧日已留存成员', 'selected_date_available': True, 'source_snapshot_id': 'old-snapshot'}
    monkeypatch.setattr(market_history, 'resolve_reference', archive)
    _, result = ranking_distribution(Market(), {'data_end': DAY}, {}, OLD, [row(1), row(2)])
    assert calls == [('sectors', OLD)]
    assert result['industries']['top'][0]['name'] == '历史行业'
    assert result['topics']['unknown_count'] == 2
    assert result['classification_snapshot_id'] == 'old-snapshot'


def test_business_day_mismatch_returns_unknown_not_current_fallback(monkeypatch):
    class Market:
        def topics(self): return []
    def changed(*_a, **_k): raise HTTPException(409, 'business date changed')
    monkeypatch.setattr(stock_memberships, 'current_memberships', changed)
    _, result = ranking_distribution(Market(), {'data_end': DAY}, {'topics': [tag('wrong', '不许回填', ['000001.SH'])]}, DAY, [row(1)])
    assert result['classification_state'] == 'unavailable'
    assert result['topics']['unknown_count'] == 1 and result['topics']['top'] == []


def test_readonly_connection_dependency_is_forwarded_and_sample_hard_bounded(monkeypatch):
    class Market:
        def topics(self): return []
    factory = object()
    def read(market, symbols, **kwargs):
        assert kwargs['connection_factory'] is factory and len(symbols) == 1000
        return {'memberships': {}, 'scope': '只读', 'business_date': DAY}
    monkeypatch.setattr(stock_memberships, 'current_memberships', read)
    # A malformed duplicate-rank caller cannot make aggregation exceed1000.
    ranks = [row(index, rank=1) for index in range(1, 1101)]
    _, result = ranking_distribution(Market(), {'data_end': DAY}, {}, DAY, ranks, connection_factory=factory)
    assert result['sample_count'] == 1000


def test_empty_sample_does_not_create_zero_percent_or_load_memberships(monkeypatch):
    monkeypatch.setattr(stock_memberships, 'current_memberships', lambda *_a, **_k: pytest.fail('empty sample must not query'))
    _, result = ranking_distribution(object(), {'data_end': DAY}, {}, DAY, [row(1, rank=None)])
    assert result['sample_count'] == 0
    assert result['topics']['top'] == result['industries']['top'] == []


def test_only_reviewed_provider_trading_attributes_excluded_from_theme_dimension():
    ranks = [row(1), row(2), row(3), row(4)]
    topics = [tag('finance', '融资融券', ['000001.SH', '000002.SH'], source='fuyao'),
              tag('sh', '沪股通', ['000001.SH'], source='fuyao'),
              tag('sz', '深股通', ['000002.SH'], source='fuyao'),
              tag('manual-finance', '融资融券', ['000001.SH'], kind='manual', source='local-manual'),
              tag('other-finance', '融资融券', ['000003.SH'], source='not-reviewed-provider'),
              tag('substring', '沪股通产业链', ['000003.SH'], source='fuyao'),
              tag('robot', '机器人概念', ['000001.SH'], source='fuyao'),
              tag('reform', '国企改革', ['000001.SH'], source='fuyao'),
              tag('industry', '融资融券', ['000002.SH'], kind='industry', source='fuyao')]
    original = deepcopy(topics)
    rows, result = ranking_distribution(object(), {'data_end': DAY}, {'topics': topics}, DAY, ranks)
    theme = result['topics']
    assert theme['classified_count'] == 2 and theme['attribute_only_count'] == 1 and theme['unknown_count'] == 1
    assert '交易资格' in theme['definition']
    retained = {(item['source'], item['data_kind'], item['name']) for item in theme['top']}
    assert ('local-manual', 'manual', '融资融券') in retained
    assert ('not-reviewed-provider', 'concept', '融资融券') in retained
    assert ('fuyao', 'concept', '沪股通产业链') in retained
    assert ('fuyao', 'concept', '机器人概念') in retained
    assert ('fuyao', 'concept', '国企改革') in retained
    excluded = result['excluded_attributes']
    assert excluded['unique_stock_count'] == 2 and excluded['total_assignments'] == 4
    assert {(item['name'], item['count'], item['share_pct']) for item in excluded['items']} == {
        ('融资融券', 2, 50), ('沪股通', 1, 25), ('深股通', 1, 25)}
    assert all(item['source'] == 'fuyao' and item['data_kind'] == 'concept' for item in excluded['mapping'])
    assert result['industries']['top'][0]['name'] == '融资融券'
    assert result['industries']['classified_count'] == 1
    assert any(item['topic_id'] == 'finance' for item in rows[0]['topics'])
    assert topics == original


def test_unregistered_source_labels_preserved_but_local_exclusion_not_reintroduced(personal, monkeypatch):
    market, client = personal
    source = [{'topic_id': 'source-a', 'name': '来源概念', 'kind': 'concept',
               'observed_date': market.entry, 'members_complete': True, 'instrument_ids': ['600001.SH']}]
    monkeypatch.setattr(market, 'topics', lambda: deepcopy(source))
    before = stock_memberships.current_memberships(market, ['600001.SH'], expected_day=market.entry, include_unregistered_provider=True)
    assert any(item['topic_id'] == 'source-a' for item in before['memberships']['600001.SH'])
    assert client.post('/api/v1/topics/initialize', json={}).status_code == 200
    definitions = client.get('/api/v1/topics').json()['items']
    selected = next(item for item in definitions if item['name'] == '来源概念')
    assert client.post('/api/v1/topics/'+selected['topic_id']+'/members', json={'instrument_id': '600001.SH', 'action': 'exclude'}).status_code == 200
    after = stock_memberships.current_memberships(market, ['600001.SH'], expected_day=market.entry, include_unregistered_provider=True)
    assert not any(item['name'] == '来源概念' for item in after['memberships']['600001.SH'])


def test_membership_factory_uses_only_readonly_connection_not_api_initialization(personal, monkeypatch):
    import sqlite3
    from app.infrastructure.config import sqlite_path
    market, client = personal
    assert client.post('/api/v1/topics/initialize', json={}).status_code == 200
    calls = []
    @contextmanager
    def readonly():
        connection = sqlite3.connect(sqlite_path().resolve().as_uri()+'?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        for pragma in ('foreign_keys=ON', 'recursive_triggers=ON', 'busy_timeout=10000', 'query_only=ON'):
            connection.execute('PRAGMA '+pragma)
        try:
            yield connection
            assert connection.total_changes == 0
            calls.append(1)
        finally:
            connection.close()
    monkeypatch.setattr(stock_memberships, 'connect', lambda: pytest.fail('read-only AI must not initialize/write SQLite'))
    result = stock_memberships.current_memberships(market, ['600001.SH'], expected_day=market.entry,
                                                  include_unregistered_provider=True, connection_factory=readonly)
    assert calls == [1] and result['business_date'] == market.entry
