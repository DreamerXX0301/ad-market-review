"""Real tool composition on isolated local fixtures; never providers/live databases."""
from copy import deepcopy
from datetime import datetime
import socket

import pytest

from app.core import dumps
from app.services import ai_read_tools as tools, market_service, market_ladder, market_supplement

DAY = '2026-11-02'
PREVIOUS = '2026-10-30'
SYMBOL = '600036.SH'
SNAPSHOT = 'market-fixture'


class Market:
    def __init__(self):
        self.ref = {}
        self.master = [{'instrument_id': SYMBOL, 'name': '招商银行', 'kind': 'stock'}]
        self.head = {'ready': True, 'snapshot_id': SNAPSHOT, 'data_end': DAY, 'quality': 'partial'}
        self.calls = []
        self.history = [{
            'instrument_id': SYMBOL, 'date': date, 'open': 10, 'high': 12, 'low': 9,
            'close': close, 'amount': amount, 'volume': 1000, 'price_basis': 'none', 'source': 'fixture',
        } for date, close, amount in [(PREVIOUS, 10, 100), (DAY, 11, 123456789)]]
    def status(self): return deepcopy(self.head)
    def reference(self): return deepcopy(self.ref)
    def calendar(self): return [PREVIOUS, DAY]
    def instruments(self, query='', limit=50):
        return [deepcopy(row) for row in self.master if not query or query.casefold() in
                (row['instrument_id']+' '+row['name']).casefold()][:limit]
    def history_for_instruments(self, symbols, start=None, end=None):
        self.calls.append((list(symbols), start, end))
        return {s: [deepcopy(row) for row in self.history if row['instrument_id'] == s and start <= row['date'] <= end] for s in symbols}
    def actions_for_instruments(self, symbols): return {s: [] for s in symbols}
    def panel(self, day): return []
    def index_quotes(self, day): return [{'instrument_id': '000001.SH', 'name': '上证指数', 'date': day, 'close': 3200}]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    original = socket.socket
    class Blocked(original):
        def __new__(cls, family=socket.AF_INET, *args, **kwargs):
            if family in (socket.AF_INET, socket.AF_INET6):
                raise AssertionError('Network forbidden in local data tool tests')
            return super().__new__(cls, family, *args, **kwargs)
    monkeypatch.setattr(socket, 'socket', Blocked)


@pytest.fixture
def market(monkeypatch, tmp_path):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path/'data'))
    monkeypatch.setenv('SHIXU_SQLITE_PATH', str(tmp_path/'data/business/app.sqlite3'))
    monkeypatch.setenv('SHIXU_PUBLISHED_DIR', str(tmp_path/'data/published'))
    monkeypatch.delenv('SHIXU_AI_READ_SQLITE_PATH', raising=False)
    result = Market()
    monkeypatch.setattr(tools, '_market', lambda *args: result)
    monkeypatch.setattr(tools, '_heads', lambda: {'daily_raw': {'snapshot_id': 'daily-head', 'market_snapshot_id': SNAPSHOT}})
    monkeypatch.setattr(tools, '_supplement_head', lambda: {'snapshot_id': 'supplement-head'})
    monkeypatch.setattr(tools, 'local_now', lambda: datetime.fromisoformat(DAY+'T20:00:00+08:00'))
    return result


def quote(identifier='招商银行', date='today', **kw):
    return tools.execute_read_tool('read_stock_quote', {'date': date, 'instrument_id': identifier}, **kw)


@pytest.mark.parametrize('identifier', ['招商银行', '600036', '600036.SH', '600036.sh'])
def test_quote_resolves_exact_local_identity_and_reads_only_requested_pair(market, identifier):
    value = quote(identifier)
    assert value['ok'] and value['state'] == 'available'
    assert value['date'] == DAY and value['summary']['instrument']['instrument_id'] == SYMBOL
    assert market.calls == [([SYMBOL], PREVIOUS, DAY)]
    metrics = {r['label']: r for r in value['metrics']}
    assert metrics['当日成交额']['value'] == 123456789 and metrics['当日成交额']['unit'] == 'CNY'
    assert metrics['当日成交量']['unit'] == '股'
    assert metrics['同日可比涨幅']['value'] == pytest.approx(10)
    assert value['source_bindings']['published_detail']['market_snapshot_id'] == SNAPSHOT
    assert '/tmp/' not in dumps(value) and 'api_key' not in dumps(value)


def test_unknown_or_ambiguous_names_never_guess_a_security(market):
    missing = quote('长鑫科技')
    assert missing['state'] == 'unknown' and missing['summary']['identity_state'] == 'not_found'
    assert not missing['evidence'] and market.calls == []
    market.master += [{'instrument_id': '000001.SZ', 'name': '招商银行', 'kind': 'stock'}]
    ambiguous = quote()
    assert ambiguous['summary']['identity_state'] == 'ambiguous' and not ambiguous['evidence'] and not market.calls


def test_empty_id_uses_only_explicit_page_identity(market):
    assert quote('', page_context={'instrument_id': SYMBOL})['summary']['identity_state'] == 'resolved'
    assert not quote('')['ok']


@pytest.mark.parametrize('mutate', ['missing', 'duplicate', 'wrong_day', 'forward', 'denied'])
def test_bad_or_crossday_quotes_stay_unknown(market, mutate):
    if mutate == 'missing': market.history = market.history[:1]
    elif mutate == 'duplicate': market.history.append(deepcopy(market.history[-1]))
    elif mutate == 'wrong_day': market.history[-1]['date'] = '2026-11-03'
    elif mutate == 'forward': market.history[-1]['price_basis'] = 'forward'
    else: market.history[-1]['ai_processing_allowed'] = False
    value = quote()
    assert value['state'] == 'unknown' and not value['evidence'] and value['gaps']


def test_zero_turnover_is_real_zero_but_missing_amount_is_not_zero(market):
    market.history[-1]['amount'] = 0
    assert next(r['value'] for r in quote()['metrics'] if r['label'] == '当日成交额') == 0
    del market.history[-1]['amount']
    value = quote()
    assert value['state'] == 'partial' and next(r['value'] for r in value['metrics'] if r['label'] == '当日成交额') is None


def test_future_or_missing_day_does_not_reuse_latest_quote(market):
    value = quote(date='2026-11-03')
    assert value['date'] == '2026-11-03' and value['state'] == 'unknown' and not market.calls


def test_source_policy_or_publication_change_never_returns_quote(market, monkeypatch):
    market.ref['egress_allowed'] = False
    assert not quote()['evidence'] and not market.calls
    market.ref = {}
    count = 0
    def heads():
        nonlocal count
        count += 1
        return {'daily_raw': {'market_snapshot_id': SNAPSHOT if count == 1 else 'different'}}
    monkeypatch.setattr(tools, '_heads', heads)
    value = quote()
    assert not value['ok'] and not value['evidence']


def ladder_row(symbol, height, *, confirmed=True):
    return {'instrument_id': symbol, 'name': symbol, 'date': DAY, 'close': 10, 'amount': 10,
        'board_count': height, 'limit_up_state': True, 'topics': [],
        'board_presentation': {'height': {'value': height if confirmed else None,
            'state': 'confirmed' if confirmed else 'unknown', 'reason': 'fixture same-day source proof'}}}


def ladder_patch(monkeypatch, rows, complete=True):
    monkeypatch.setattr(market_supplement, '_load_supplement_with_fingerprint', lambda *a, **kw: ({}, {'sha': 'same'}))
    def build(*args, **kwargs):
        assert kwargs['include_patterns'] is False
        return {'items': deepcopy(rows), 'main_coverage': {'complete': complete, 'source_pool_complete': complete,
                'confirmed_count': len(rows), 'unknown_count': 0, 'scope': 'closing source sample'},
                'theme_counts': [], 'unverified_items': [], 'classification_coverage': {}}
    monkeypatch.setattr(market_ladder, 'build_ladder', build)


@pytest.mark.parametrize('complete', [True, False])
def test_first_board_and_max_names_preserve_exact_vs_observed(market, monkeypatch, complete):
    ladder_patch(monkeypatch, [ladder_row('600001.SH', 1), ladder_row('600002.SH', 6), ladder_row('600003.SH', 6)], complete)
    value = tools.execute_read_tool('read_limit_up_data', {'date': 'today'})
    summary = value['summary']
    assert summary['first_board_count'] == (1 if complete else None)
    assert summary['first_board_observed_count'] == 1
    assert summary['highest_board_count'] == (6 if complete else None)
    assert summary['highest_board_observed_count'] == 6
    assert {r['instrument_id'] for r in summary['highest_board_stocks']['items']} == {'600002.SH', '600003.SH'}
    assert value['state'] == ('available' if complete else 'partial')


def test_unknown_height_never_makes_exact_first_or_highest_counts(market, monkeypatch):
    ladder_patch(monkeypatch, [ladder_row('600001.SH', 1), ladder_row('600002.SH', 9, confirmed=False)])
    summary = tools.execute_read_tool('read_limit_up_data', {'date': 'today'})['summary']
    assert summary['first_board_count'] is None and summary['highest_board_count'] is None
    assert summary['first_board_observed_count'] == 1 and summary['unknown_height_count'] == 1


def test_large_ladder_keeps_counts_and_explicit_bounded_lists(market, monkeypatch):
    rows = [ladder_row(f'{600000+n:06}.SH', 1 if n else 6) for n in range(400)]
    ladder_patch(monkeypatch, rows)
    value = tools.execute_read_tool('read_limit_up_data', {'date': 'today'})
    assert value['summary']['first_board_count'] == 399 and value['summary']['confirmed_limit_up_count'] == 400
    data = value['evidence'][0]['data']; items = data['closing_limit_up']
    assert items['observed_count'] == 400 and items['truncated'] is True and items['returned_count'] <= 30
    assert len(dumps(value).encode()) <= tools.MAX_RESULT_BYTES
    assert value['summary']['highest_board_stocks']['items'][0]['instrument_id'] == '600000.SH'


@pytest.mark.parametrize('section', tools.DETAIL_SECTIONS)
def test_detail_injects_fixed_market_and_is_bounded(market, monkeypatch, section):
    row = ladder_row(SYMBOL, 1)
    lookup_mode = tools.GAINERS_LOOKUP_MODES.get(section)
    query = 'fixture-category' if lookup_mode else ''
    def page(kind, day, **kwargs):
        assert kwargs['market'] is market
        data = {'date': day, 'snapshot_id': SNAPSHOT, 'items': [row], 'total': 1,
            'summary': {'basis': 'saved-page'}, 'data_coverage': {'partial': True, 'notice': 'fixture coverage'},
            'market_outlook': {'date': day, 'notice': 'conditional observation, not a prediction',
                'next_session': {'date': None}, 'plan': {'stance': 'wait'},
                'watchlist': {'stocks': [row], 'topics': [], 'coverage': {'complete': False}}}}
        if lookup_mode:
            assert kind == 'gainers' and kwargs['top_n'] == 1000
            assert kwargs['query'] == '' and kwargs['gainers_lookup'] == {'mode': lookup_mode, 'query': query}
            data.update(items=[], total=0, classification_lookup={
                'state': 'unknown', 'matched_count': None, 'observed_count': 0, 'categories': [],
            })
        return data
    def indices(day, **kwargs):
        assert kwargs['market'] is market
        return {'date': day, 'snapshot_id': SNAPSHOT, 'indices': market.index_quotes(day), 'quality': 'partial'}
    monkeypatch.setattr(market_service, 'market_page', page)
    monkeypatch.setattr(market_service, 'index_overview', indices)
    monkeypatch.setattr(market_service, 'get_market', lambda: (_ for _ in ()).throw(AssertionError('rw app connect forbidden')))
    value = tools.execute_read_tool('read_market_detail', {'date': 'today', 'section': section, 'query': query})
    assert value['ok'] and value['evidence'] and value['date'] == DAY
    assert value['source_bindings']['published_detail']['market_snapshot_id'] == SNAPSHOT
    assert len(dumps(value).encode()) <= tools.MAX_RESULT_BYTES
    if section == 'emotion':
        data = value['evidence'][0]['data']
        assert data['market_outlook']['plan']['stance'] == 'wait' and '不是' in data['scope']


def test_board_quote_survives_missing_members_and_query_supports_source_code(market, monkeypatch):
    def page(kind, day, **kwargs):
        return {'date': day, 'snapshot_id': SNAPSHOT, 'items': [{'topic_code': '881101.TI', 'name': '行业',
            'change_pct': 2.3, 'amount': 100000, 'quote_date': day, 'quote_basis': 'provider_index',
            'members_available': False, 'member_count': None, 'limit_up_count': None}], 'total': 1}
    monkeypatch.setattr(market_service, 'market_page', page)
    value = tools.execute_read_tool('read_market_detail', {'date': 'today', 'section': 'sectors', 'query': '881101.TI'})
    row = value['evidence'][0]['data']['sectors']['items'][0]
    assert row['change_pct'] == 2.3 and row['member_count'] is None and row['limit_up_count'] is None


@pytest.mark.parametrize('name,args', [
    ('read_stock_quote', {'date': 'today', 'instrument_id': SYMBOL, 'sql': 'SELECT 1'}),
    ('read_limit_up_data', {'date': 'today', 'path': '/private'}),
    ('read_market_detail', {'date': 'today', 'section': 'other', 'query': ''}),
    ('read_market_detail', {'date': 'today', 'section': 'sectors', 'query': '\n'}),
    ('read_stock_quote', {'date': 'yesterday', 'instrument_id': SYMBOL}),
])
def test_tool_parameters_are_closed_and_cannot_choose_files_or_sql(market, name, args):
    value = tools.execute_read_tool(name, args)
    assert not value['ok'] and value['state'] == 'error' and not value['evidence']


def test_index_service_injection_never_calls_app_facade(market, monkeypatch):
    monkeypatch.setattr(market_service, 'get_market', lambda: (_ for _ in ()).throw(AssertionError('rw connect forbidden')))
    monkeypatch.setattr(market_service, 'data_coverage', lambda *args: {'notice': 'test read coverage'})
    value = market_service.index_overview(DAY, market=market)
    assert value['date'] == DAY and value['indices'][0]['close'] == 3200


def test_gainers_uses_original_top1000_distribution_before_topic_query(market, monkeypatch):
    classification = {'version': 'gainers-top1000-distribution-v1', 'sample_scope': 'top_1000_before_filters',
        'sample_count': 1000, 'topics': {'top': [{'name': '半导体', 'count': 123, 'share_pct': 12.3}]}}
    def page(kind, day, **kwargs):
        assert kind == 'gainers' and kwargs['top_n'] == 1000 and kwargs['query'] == '半导体'
        return {'date': day, 'snapshot_id': SNAPSHOT, 'items': [{**ladder_row(SYMBOL, 1), 'rank': 23,
            'change_30d_pct': 56.7, 'topics': [{'name': '半导体', 'topic_id': 'semi', 'kind': 'concept', 'source_observed_date': DAY}]}],
            'total': 1, 'ranking_distribution': classification}
    monkeypatch.setattr(market_service, 'market_page', page)
    value = tools.execute_read_tool('read_market_detail', {'date': 'today', 'section': 'gainers', 'query': '半导体'})
    data = value['evidence'][0]['data']
    assert data['ranking_distribution'] == classification
    assert data['stocks']['observed_count'] == 1 and data['stocks']['items'][0]['rank'] == 23
    assert data['stocks']['items'][0]['change_30d_pct'] == 56.7


def test_future_calendar_entry_does_not_claim_a_completed_day(market):
    market.head['data_end'] = PREVIOUS
    assert quote()['state'] == 'unknown' and not market.calls
    result = tools.execute_read_tool('read_limit_up_data', {'date': 'today'})
    assert not result['evidence'] and result['gaps'][0]['capability'] == 'completed_market'


@pytest.mark.parametrize('name,args', [
    ('read_stock_quote', {'date': 'today', 'instrument_id': SYMBOL}),
    ('read_limit_up_data', {'date': 'today'}),
    ('read_market_detail', {'date': 'today', 'section': 'indices', 'query': ''}),
])
@pytest.mark.parametrize('hour,known,expected', [(12, True, 'not_due'), (15, True, 'unknown'), (12, False, 'unknown')])
def test_completed_quotes_not_due_only_for_verified_today_before_close(market, monkeypatch, name, args, hour, known, expected):
    market.head['data_end'] = PREVIOUS
    monkeypatch.setattr(tools, 'local_now', lambda: datetime.fromisoformat(DAY+f'T{hour}:00:00+08:00'))
    if not known: monkeypatch.setattr(market, 'calendar', lambda: [PREVIOUS])
    value = tools.execute_read_tool(name, args)
    assert value['state'] == expected and value['gaps'][0]['state'] == expected
    assert not value['evidence'] and not market.calls


def test_future_known_calendar_day_is_unknown_not_a_today_phase(market, monkeypatch):
    monkeypatch.setattr(market, 'calendar', lambda: [PREVIOUS, DAY, '2026-11-03'])
    monkeypatch.setattr(tools, 'local_now', lambda: datetime.fromisoformat(DAY+'T12:00:00+08:00'))
    value = tools.execute_read_tool('read_limit_up_data', {'date': '2026-11-03'})
    assert value['state'] == 'unknown' and value['gaps'][0]['state'] == 'unknown'


def test_sector_tool_requests_only_selected_supplement_day(market, monkeypatch):
    calls = []
    def page(kind, day, **kwargs):
        calls.append(kwargs['supplement_dates'])
        assert kind == 'sectors' and kwargs['market'] is market
        return {'date': day, 'snapshot_id': SNAPSHOT, 'items': [], 'total': 0}
    monkeypatch.setattr(market_service, 'market_page', page)
    value = tools.execute_read_tool('read_market_detail', {'date': DAY, 'section': 'sectors', 'query': ''})
    assert value['ok'] and calls == [(DAY,)]
    assert value['source_bindings']['published_detail']['supplement_dates'] == [DAY]


def test_panel_forwards_selected_dates_without_expanding_history(market, monkeypatch):
    calls = []
    def read(snapshot, *, fields, dates):
        assert snapshot == SNAPSHOT and dates == (DAY,)
        calls.append((fields, dates))
        return {}, ('verified-selected-day',)
    monkeypatch.setattr(market_service, 'load_supplement_with_fingerprint', read)
    market_service.panel(DAY, market=market, supplement_fields=('sector_index_history',), supplement_dates=(DAY,))
    assert calls == [(('sector_index_history',), (DAY,))]


@pytest.mark.parametrize('changing', ['_heads', '_supplement_head'])
def test_sector_projection_rejects_changed_publication_before_sending_facts(market, monkeypatch, changing):
    monkeypatch.setattr(market_service, 'market_page', lambda kind, day, **kw: {
        'date': day, 'snapshot_id': SNAPSHOT, 'items': [{'name': 'must-not-leak', 'change_pct': 2}], 'total': 1})
    original = getattr(tools, changing)
    count = 0
    def changed():
        nonlocal count
        count += 1
        return original() if count == 1 else {'changed': True}
    monkeypatch.setattr(tools, changing, changed)
    value = tools.execute_read_tool('read_market_detail', {'date': DAY, 'section': 'sectors', 'query': ''})
    assert not value['ok'] and value['state'] == 'error' and not value['evidence']
    assert 'must-not-leak' not in dumps(value)


def test_denied_pool_does_not_send_events_or_counts(market, monkeypatch):
    monkeypatch.setattr(market_supplement, '_load_supplement_with_fingerprint', lambda *a, **kw: (
        {'annual_pools': {DAY: {'ai_processing_allowed': False, 'items': [{'name': 'blocked'}]}}}, {'id': 'blocked'}))
    monkeypatch.setattr(market_ladder, 'build_ladder', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('must not assemble blocked source')))
    result = tools.execute_read_tool('read_limit_up_data', {'date': 'today'})
    assert not result['evidence'] and result['gaps'][0]['capability'] == 'source_permission'
