"""Unified identity search is bounded, balanced and wholly offline."""
from copy import deepcopy

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.services import unified_search


class Market:
    def __init__(self):
        self.calls = []
        self.rows = [{'instrument_id': '600373.SH', 'code': '600373', 'name': '中文传媒', 'kind': 'stock'},
                     {'instrument_id': '601999.SH', 'code': '601999', 'name': '出版传媒', 'kind': 'stock'},
                     {'instrument_id': '000001.SH', 'code': '000001', 'name': '上证指数', 'kind': 'index'}]
        self.reference_data = {'industry_catalog': [{'thscode': '881161.TI', 'name': '出版'}],
                               'concept_catalog': [{'thscode': '885728.TI', 'name': '人工智能'},
                                                   {'thscode': '885901.TI', 'name': '出版概念'}]}
    def instruments(self, query='', limit=50):
        self.calls.append((query, limit))
        return [deepcopy(row) for row in self.rows if query.casefold() in (row['name']+' '+row['instrument_id']).casefold()][:limit]
    def reference(self): return deepcopy(self.reference_data)
    def panel(self, *args): raise AssertionError('search must not read quote panels')


@pytest.fixture
def local(monkeypatch):
    market = Market()
    monkeypatch.setattr(unified_search, 'get_market', lambda: market)
    from app.adapters.providers import ProviderClient
    monkeypatch.setattr(ProviderClient, 'request', lambda *a: pytest.fail('search never requests a source'))
    return market


def test_publication_query_finds_exact_board_before_longer_stock_name(local):
    result = unified_search.search('  出版  ', 2)
    assert result['query'] == '出版' and result['total'] == 3
    first = result['items'][0]
    assert first['target_type'] == 'sector' and first['kind'] == first['sector_kind'] == 'industry'
    assert first['topic_code'] == '881161.TI' and first['topic_id'] == 'fuyao-881161.TI'
    assert first['code'] == '881161'
    assert local.calls == [('', 10000)]
    assert result['historical_membership'] is False


@pytest.mark.parametrize('query,code,kind', [('人工智能', '885728.TI', 'concept'),
    ('885728', '885728.TI', 'concept'), ('885728.ti', '885728.TI', 'concept'),
    ('600373', '600373.SH', 'stock'), ('上证', '000001.SH', 'index')])
def test_board_stock_and_index_name_code_lookup(local, query, code, kind):
    result = unified_search.search(query)
    assert result['items'][0]['instrument_id'] == code
    assert result['items'][0]['kind'] == kind
    assert result['items'][0]['target_type'] == ('sector' if kind == 'concept' else kind)


def test_equal_relevance_balances_types_instead_of_stocks_consuming_limit(local):
    local.rows = [{'instrument_id': f'{600000+i:06d}.SH', 'code': f'{600000+i:06d}', 'name': '共同名称', 'kind': 'stock'} for i in range(80)]
    local.rows.append({'instrument_id': '399001.SZ', 'code': '399001', 'name': '共同名称', 'kind': 'index'})
    local.reference_data = {'industry_catalog': [{'thscode': '881101.TI', 'name': '共同名称'}],
                            'concept_catalog': [{'thscode': '885728.TI', 'name': '共同名称'}]}
    result = unified_search.search('共同名称', 4)
    assert result['total'] == 83 and len(result['items']) == 4
    assert {row['kind'] for row in result['items']} == {'stock', 'index', 'industry', 'concept'}
    assert unified_search.search('共同名称', 100)['items'] == unified_search.search('共同名称', 100)['items']


def test_exact_stock_identity_stays_ahead_of_sector_substring(local):
    local.reference_data['concept_catalog'].append({'thscode': '885902.TI', 'name': '600373概念'})
    result = unified_search.search('600373', 1)
    assert result['items'][0]['kind'] == 'stock' and result['total'] == 2


@pytest.mark.parametrize('query,expected', [
    ('hyw', {'002724.SZ', '300053.SZ'}), ('海洋', {'002724.SZ'}),
    ('haiyangwang', {'002724.SZ'}), ('hangyuwei', {'300053.SZ'}),
    ('002724', {'002724.SZ'}), ('海羊王', {'002724.SZ'}),
])
def test_stock_only_search_reuses_global_matching_before_limit(local, query, expected):
    local.rows = [
        {'instrument_id': '002724.SZ', 'name': '海洋王', 'kind': 'stock'},
        {'instrument_id': '300053.SZ', 'name': '航宇微', 'kind': 'stock'},
        {'instrument_id': '000001.SH', 'name': '海洋王', 'kind': 'index'},
    ]
    local.reference_data = {'concept_catalog': [{'thscode': '885728.TI', 'name': '海洋王'}]}
    result = unified_search.search(query, stocks_only=True)
    assert {r['instrument_id'] for r in result['items']} == expected
    assert all(r['kind'] == 'stock' for r in result['items'])
    assert unified_search.search('海洋王', 1, stocks_only=True)['items'][0]['instrument_id'] == '002724.SZ'
    global_stocks = {r['instrument_id'] for r in unified_search.search(query)['items'] if r['kind'] == 'stock'}
    assert global_stocks == expected


def test_duplicate_catalog_identity_is_deduplicated_and_conflict_is_quarantined(local):
    local.reference_data['concept_catalog'].append({'thscode': '885728.TI', 'name': '人工智能'})
    assert unified_search.search('人工智能')['total'] == 1
    local.reference_data['industry_catalog'].append({'thscode': '885728.TI', 'name': '人工智能'})
    assert unified_search.search('人工智能')['total'] == 0


def test_empty_whitespace_query_has_no_market_or_provider_work(monkeypatch):
    monkeypatch.setattr(unified_search, 'get_market', lambda: pytest.fail('blank search should do no work'))
    assert unified_search.search(' \n  ')['items'] == []
    assert unified_search.search('')['total'] == 0


@pytest.mark.parametrize('query,limit', [('x'*101, 20), (' '*513, 20), (None, 20),
    ('出版', 0), ('出版', 101), ('出版', True), ('出版', 1.5)])
def test_input_bounds_are_explicit(local, query, limit):
    with pytest.raises(HTTPException) as caught: unified_search.search(query, limit)
    assert caught.value.status_code == 422 and local.calls == []


def test_api_unified_route_does_not_change_original_stock_picker(local, monkeypatch):
    from app import core
    from app.routers.platform import router
    monkeypatch.setattr(core, 'get_market', lambda: local)
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        new = client.get('/api/v1/search', params={'query': '出版', 'limit': 100})
        old = client.get('/api/v1/instruments', params={'query': '出版'})
        stocks = client.get('/api/v1/search', params={'query': '出版', 'stocks_only': True})
        invalid = client.get('/api/v1/search', params={'query': '出版', 'limit': 101})
    assert new.status_code == old.status_code == 200 and invalid.status_code == 422
    assert any(row['target_type'] == 'sector' for row in new.json()['items'])
    assert all(row['kind'] == 'stock' for row in old.json()['items'])
    assert stocks.status_code == 200 and stocks.json()['stocks_only'] is True
    assert all(row['kind'] == 'stock' for row in stocks.json()['items'])
    assert not any('topic_code' in row for row in old.json()['items'])


@pytest.mark.parametrize('query,code,match_type', [
    ('zhongwenchuanmei', '600373.SH', 'pinyin'), ('ZHONG WEN CHUAN MEI', '600373.SH', 'pinyin'),
    ('zwcm', '600373.SH', 'initials'), ('ＺＷＣＭ', '600373.SH', 'initials'),
    ('rgzn', '885728.TI', 'initials'), ('rengongzhineng', '885728.TI', 'pinyin'),
    ('人工智熊', '885728.TI', 'typo'), ('中闻传媒', '600373.SH', 'homophone'),
    ('zhongwenchuanme', '600373.SH', 'pinyin'), ('zhongwenchuanmwi', '600373.SH', 'typo'),
    ('中文传煤', '600373.SH', 'homophone'),
])
def test_phonetic_and_typo_search_retains_identity(local, query, code, match_type):
    row = unified_search.search(query)['items'][0]
    assert row['instrument_id'] == code and row['match_type'] == match_type
    assert row['is_suggestion'] == (match_type in ('homophone', 'typo'))


def test_numeric_typo_is_never_guessed_and_exact_wins(local):
    assert unified_search.search('600374')['items'] == []
    assert unified_search.search('600373.SZ')['items'] == []
    local.rows.append({'instrument_id': '600371.SH', 'name': '中文传煤', 'kind': 'stock'})
    result = unified_search.search('中文传媒')['items']
    assert result[0]['instrument_id'] == '600373.SH'
    assert result[-1]['instrument_id'] == '600371.SH' and result[-1]['is_suggestion']


def test_name_cache_cannot_keep_renamed_removed_or_new_catalog_entries(local):
    assert unified_search.search('zwcm')['items']
    local.rows[0]['name'] = '新名股份'
    assert not unified_search.search('zwcm')['items']
    assert unified_search.search('xmgf')['items'][0]['instrument_id'] == '600373.SH'
    local.rows.pop(0)
    assert not unified_search.search('xmgf')['items']
    local.reference_data['concept_catalog'].append({'thscode':'885999.TI','name':'冷链物流'})
    assert unified_search.search('llwl')['items'][0]['topic_code'] == '885999.TI'


def test_short_typo_not_widened_and_multisyllable_place_names(local):
    assert not unified_search.search('中闻')['items']
    assert not unified_search.search('zvcm')['items']  # No approximate acronym guesses.
    local.rows.append({'instrument_id':'600999.SH','name':'重庆银行','kind':'stock'})
    assert unified_search.search('cqyh')['items'][0]['name'] == '重庆银行'
    assert unified_search.search('chongqingyinhang')['items'][0]['name'] == '重庆银行'


def test_matching_cache_is_bounded_and_results_are_not_shared(local):
    from app.services.search_matching import phonetic_keys
    assert phonetic_keys.cache_info().maxsize == 20000
    rows = unified_search.search('zwcm')['items']
    rows[0]['name'] = 'caller mutation'
    assert unified_search.search('zwcm')['items'][0]['name'] == '中文传媒'
