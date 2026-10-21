"""Selected-day constituents and rank scopes must not drift to current data."""
from copy import deepcopy
import hashlib
import json

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.services import market_history as history
from app.services import market_service as market
from app.services import market_topic_members as members

A, B, C, D, E = [f'60000{n}.SH' for n in range(1, 6)]
OLD, TODAY = '2026-10-19', '2026-10-20'


def topic(ids, *, name='存档行业', kind='industry', observed=OLD, complete=True, code='881001.TI'):
    return {'topic_id': 'fuyao-' + code, 'topic_code': code, 'name': name, 'kind': kind,
            'observed_date': observed, 'members_complete': complete, 'instrument_ids': ids}


class LocalMarket:
    def calendar(self):
        return [OLD, TODAY]

    def instruments(self, **kwargs):
        return [{'instrument_id': symbol, 'name': '合成' + symbol[:6]} for symbol in (A, B, C, D, E)]

    def bars(self, *args, **kwargs):
        raise AssertionError('the constituent list must not perform per-stock history reads')

    def history_panel(self, *args, **kwargs):
        raise AssertionError('one already-loaded selected-day panel is sufficient')


@pytest.fixture
def archives(tmp_path, monkeypatch):
    catalog = []
    monkeypatch.setattr(history, 'published_dir', lambda: tmp_path)
    monkeypatch.setattr(history, 'sqlite_path', lambda: tmp_path / 'unused.sqlite3')
    monkeypatch.setattr(history, '_catalog', lambda: (list(catalog), False))
    history.clear_history_cache()

    def save(generation, day, topics):
        path = tmp_path / generation / 'reference.json'
        path.parent.mkdir()
        raw = json.dumps({'topics': topics}, ensure_ascii=False).encode()
        path.write_bytes(raw)
        catalog.append({'snapshot_id': 'topics-' + generation, 'module': 'topics', 'data_end': day,
                        'published_at_ms': len(catalog) + 1, 'lineage_json': json.dumps({'market_snapshot_id': generation}),
                        'object_key': generation + '/reference.json', 'sha256': hashlib.sha256(raw).hexdigest(),
                        'byte_count': len(raw)})
        return path

    yield save
    history.clear_history_cache()


def call(reference, rows, *, day=OLD, **lookup):
    return members.list_members(LocalMarket(), {'snapshot_id': 'market-current', 'data_end': TODAY}, day, reference, rows, **lookup)


def quote(symbol, change, *, day=OLD, close=10, **values):
    return {'instrument_id': symbol, 'date': day, 'name': '合成' + symbol[:6],
            'close': close, 'change_pct': change, 'amount': 100, **values}


def test_exact_archive_members_join_selected_quotes_and_sort_full_set(archives):
    archives('market-old', OLD, [topic([C, B, A, D, A])])
    current = {'topics': [topic([E], observed=TODAY)]}
    archives('market-current', TODAY, current['topics'])
    before = deepcopy(current)
    result = call(current, [quote(A, 5), quote(B, -2), quote(C, 5), quote(D, 99, day=TODAY)], topic_id='fuyao-881001.TI')
    assert [row['instrument_id'] for row in result['items']] == [A, C, B, D]
    assert result['total'] == 4 and result['members_complete']
    assert result['source_snapshot_id'] == 'market-old' and result['membership_observed_date'] == OLD
    assert result['date'] == OLD and result['quote_coverage'] == {'known': 3, 'unknown': 1, 'total': 4, 'complete': False}
    assert result['items'][-1]['quote_date'] is None and result['items'][-1]['change_pct'] is None
    assert result['match_type'] == 'source_topic_id' and current == before


def test_stale_future_invalid_and_duplicate_quotes_never_fill_selected_day(archives):
    archives('market-old', OLD, [topic([A, B, C, D])])
    rows = [quote(A, 7, day='2026-10-16'), quote(B, 8, day=TODAY),
            quote(C, 9, close=0), quote(D, 1), quote(D, 2)]
    result = call({}, rows, source_code='881001.TI', kind='industry')
    assert result['match_type'] == 'source_code'
    assert result['quote_coverage']['known'] == 0
    assert all(row['close'] is None and row['amount'] is None and row['quote_date'] is None for row in result['items'])
    assert '重复' in result['items'][-1]['quote_reason']


def test_verified_empty_members_distinct_from_missing_history(archives):
    archives('market-old', OLD, [topic([])])
    result = call({}, [], topic_id='fuyao-881001.TI')
    assert result['total'] == 0 and result['members_complete'] and result['unavailable_reason'] is None
    assert result['quote_coverage']['complete']
    missing = call({'topics': [topic([E], observed=TODAY)]}, [], day='2026-10-16', topic_id='fuyao-881001.TI')
    assert missing['items'] == [] and missing['members_complete'] is False and missing['unavailable_reason']
    assert missing['match_type'] == 'unavailable'


def test_partial_or_unidentified_members_keep_completeness_unknown(archives):
    archives('market-old', OLD, [topic([A, '600002', None], complete=True)])
    result = call({}, [quote(A, 1)], topic_id='fuyao-881001.TI')
    assert result['total'] == 1 and not result['members_complete']
    assert not result['quote_coverage']['complete'] and any('2条' in value for value in result['warnings'])


def test_date_carry_disclosed_and_future_observation_rejected(archives):
    archives('market-old', OLD, [topic([A], observed='2026-10-16')])
    result = call({}, [quote(A, 1)], topic_id='fuyao-881001.TI')
    assert result['membership_observed_date'] == '2026-10-16' and result['membership_snapshot_day'] == OLD
    assert '日增量沿用成员' in result['data_coverage']['notice']
    future = call({'topics': [topic([B], observed='2026-10-21')]}, [], day=TODAY, topic_id='fuyao-881001.TI')
    assert not future['items'] and future['unavailable_reason']


def test_corrupt_archive_is_not_replaced_by_current_members(archives):
    path = archives('market-old', OLD, [topic([A])])
    archives('market-current', TODAY, [topic([E], observed=TODAY)])
    assert call({}, [quote(A, 1)], topic_id='fuyao-881001.TI')['items']
    path.write_bytes(b'{}')
    result = call({'topics': [topic([E], observed=TODAY)]}, [], topic_id='fuyao-881001.TI')
    assert result['items'] == [] and result['unavailable_reason']
    assert result['data_coverage']['selected_date_available'] is False


def test_unique_cross_provider_industry_name_is_explicit_proxy(archives):
    archives('market-old', OLD, [topic([A], name='文化传媒')])
    result = call({}, [quote(A, 1)], source_code='BK0486', source_name='文化传媒', kind='industry')
    assert result['resolved_topic_id'] == 'fuyao-881001.TI'
    assert result['match_type'] == 'cross_provider_name_proxy'
    assert '本地同名行业（同花顺口径）' in result['membership_basis']
    assert '不是东财行业精确成分' in result['membership_basis']
    assert result['membership_source'] == 'fuyao' and result['membership_observed_date'] == OLD


@pytest.mark.parametrize('topics,kind,name', [
    ([topic([A], name='文化传媒'), topic([B], name='文化传媒', code='881002.TI')], 'industry', '文化传媒'),
    ([topic([A], name='文化传媒Ⅱ')], 'industry', '文化传媒'),
    ([topic([A], name='文化传媒', kind='concept')], 'industry', '文化传媒'),
    ([topic([A], name='文化传媒', kind='concept')], 'concept', '文化传媒'),
])
def test_ambiguous_fuzzy_and_concept_proxy_do_not_guess_members(archives, topics, kind, name):
    archives('market-old', OLD, topics)
    result = call({}, [], source_code='BK0486', source_name=name, kind=kind)
    assert result['items'] == [] and result['resolved_topic_id'] is None
    assert result['match_type'] == 'unavailable' and result['unavailable_reason']


def test_member_link_does_not_change_fund_series_identity():
    row = {'topic_id': 'eastmoney-BK0486', 'topic_code': 'BK0486', 'name': '文化传媒', 'kind': 'industry'}
    original = deepcopy(row)
    link = members.membership_link(row, [topic([A], name='文化传媒')])
    assert row == original and link['membership_topic_id'] == 'fuyao-881001.TI'
    assert link['member_lookup'] == {'source_code': 'BK0486', 'source_name': '文化传媒', 'kind': 'industry'}
    assert link['membership_match_type'] == 'cross_provider_name_proxy'


def test_member_link_without_identity_cannot_guess_the_only_local_topic():
    link = members.membership_link({'name': '未识别分类'}, [topic([A], kind='concept')])
    assert link['membership_topic_id'] is None and link['membership_match_type'] == 'unavailable'


def test_market_wrapper_uses_one_selected_day_panel(archives, monkeypatch):
    archives('market-old', OLD, [topic([A])])
    seen = []
    def panel(day):
        seen.append(day)
        return LocalMarket(), {'snapshot_id': 'market-current', 'data_end': TODAY}, day, {}, [quote(A, 2)]
    monkeypatch.setattr(market, 'panel', panel)
    result = market.market_topic_members(OLD, topic_id='fuyao-881001.TI')
    assert seen == [OLD] and result['items'][0]['change_pct'] == 2


def test_fund_page_exposes_proxy_lookup_without_replacing_fund_id(archives, monkeypatch):
    archives('market-old', OLD, [topic([A], name='文化传媒')])
    row = {'topic_id': 'eastmoney-BK0486', 'topic_code': 'BK0486', 'name': '文化传媒',
           'kind': 'industry', 'date': OLD, 'net_inflow': 123}
    original = deepcopy(row)
    monkeypatch.setattr(market, 'panel', lambda day: (LocalMarket(), {'snapshot_id': 'market-current', 'data_end': TODAY}, day, {}, [quote(A, 2)]))
    monkeypatch.setattr(market, 'fund_flow_for_date', lambda *args: {'items': [row], 'history': [], 'coverage': {}})
    monkeypatch.setattr(market, 'prior_ladder_feedback', lambda *args: [])
    result = market.market_page('flow', OLD)
    assert result['items'][0]['topic_id'] == 'eastmoney-BK0486'
    assert result['items'][0]['membership_topic_id'] == 'fuyao-881001.TI'
    assert result['items'][0]['membership_match_type'] == 'cross_provider_name_proxy'
    assert row == original


@pytest.mark.parametrize('lookup', [{}, {'source_name': '行业'}, {'source_code': 'BK1', 'kind': 'manual'}, {'topic_id': ''}])
def test_member_lookup_rejects_unspecified_or_invalid_identity(lookup):
    with pytest.raises(HTTPException) as error:
        call({}, [], **lookup)
    assert error.value.status_code == 422


def test_negative_or_nonfinite_quote_metrics_remain_unknown(archives):
    archives('market-old', OLD, [topic([A, B])])
    result = call({}, [quote(A, float('nan'), amount=-1, turnover_rate=-2), quote(B, True)], topic_id='fuyao-881001.TI')
    assert all(row['change_pct'] is None for row in result['items'])
    assert result['items'][0]['amount'] is None and result['items'][0]['turnover_rate'] is None


@pytest.fixture
def rank_scope(monkeypatch):
    rows = [{'instrument_id': f'{index:06}.SH', 'name': f'股票{index}', 'rank': index,
             'change_30d_pct': 2000-index, 'change_pct': index, 'topics': []} for index in range(1, 1501)]
    rows.append({'instrument_id': '999999.SH', 'name': '未知股', 'rank': None, 'change_30d_pct': None, 'change_pct': 10000})
    monkeypatch.setattr(market, 'panel', lambda day: (object(), {'snapshot_id': 'fixture', 'data_end': day}, day, {}, []))
    monkeypatch.setattr(market, 'cached_gainers', lambda *args: (rows, {'covered_stocks': 1500, 'unknown_count': 1}))
    return rows


@pytest.mark.parametrize('top_n', [100, 200, 300, 500, 1000])
def test_top_n_is_real_original_rank_scope_before_secondary_sort_and_pages(rank_scope, top_n):
    original = deepcopy(rank_scope)
    result = market.market_page('gainers', TODAY, top_n=top_n, sort_by='change_pct', sort_order='desc', page=2, page_size=25)
    assert result['total'] == top_n and result['pages'] == top_n // 25
    assert [row['rank'] for row in result['items']] == list(range(top_n-25, top_n-50, -1))
    assert result['summary']['available_ranked_total'] == 1500
    assert result['summary']['available_total'] == 1501
    assert result['summary']['rank_scope_total'] == top_n and result['summary']['top_n'] == top_n
    assert rank_scope == original


def test_top_n_search_is_scoped_and_omission_retains_full_market(rank_scope):
    selected = market.market_page('gainers', TODAY, top_n=100, query='股票1500', page_size=25)
    assert selected['total'] == 0 and selected['items'] == []
    full = market.market_page('gainers', TODAY, query='股票1500', page_size=25)
    assert full['total'] == 1 and full['items'][0]['rank'] == 1500
    assert full['summary']['top_n'] is None


def test_nonpaginated_limit_reaches_a_thousand_without_changing_full_total(rank_scope):
    result = market.market_page('gainers', TODAY, limit=1000)
    assert len(result['items']) == 1000 and result['total'] == 1501


@pytest.mark.parametrize('top_n', [99, 400, 1001, True, '100'])
def test_rank_scope_whitelist_rejects_other_values(rank_scope, top_n):
    with pytest.raises(HTTPException) as error:
        market.market_page('gainers', TODAY, top_n=top_n)
    assert error.value.status_code == 422


def test_exact_member_http_route_is_not_swallowed_by_generic_market_route(monkeypatch):
    from app.routers.platform import router
    seen = []
    monkeypatch.setattr(market, 'market_topic_members', lambda day, **lookup: seen.append((day, lookup)) or {'items': [], 'total': 0})
    app = FastAPI(); app.include_router(router)
    result = TestClient(app).get('/api/v1/market/topic-members', params={'date': OLD, 'source_code': 'BK0486', 'source_name': '文化传媒', 'kind': 'industry'})
    assert result.status_code == 200 and result.json()['total'] == 0
    assert seen == [(OLD, {'topic_id': None, 'source_code': 'BK0486', 'source_name': '文化传媒', 'kind': 'industry'})]


def test_http_rank_scope_forwarded_and_bounded(monkeypatch):
    from app.routers.platform import router
    seen = []
    monkeypatch.setattr(market, 'market_page', lambda kind, day, limit, **kwargs: seen.append(kwargs) or {'items': [], 'total': 0})
    app = FastAPI(); app.include_router(router); client = TestClient(app)
    assert client.get('/api/v1/market/gainers?date='+TODAY+'&top_n=1000&page_size=25').status_code == 200
    assert seen[0]['top_n'] == 1000 and seen[0]['page_size'] == 25
    assert client.get('/api/v1/market/gainers?top_n=1001').status_code == 422
