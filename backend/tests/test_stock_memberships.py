"""All stock detail entries and the watchlist share current local classifications."""
from copy import deepcopy

from app import core
from app.routers import platform
from app.services import market_service, stock_memberships
from test_workspace import ws
from test_watchlist import personal, add


def test_detail_and_watchlist_share_current_memberships_without_historical_backfill(personal, monkeypatch):
    market, client = personal
    source_day = market.days[68]
    catalog = [
        {'topic_id': 'concept-a', 'name': '来源概念', 'kind': 'concept', 'observed_date': source_day,
         'members_complete': False, 'instrument_ids': ['600001.SH']},
        {'topic_id': 'industry-a', 'name': '来源行业', 'kind': 'industry', 'observed_date': source_day,
         'members_complete': True, 'instrument_ids': ['600001.SH', '000001.SZ']},
    ]
    monkeypatch.setattr(market, 'topics', lambda: deepcopy(catalog))
    assert client.post('/api/v1/topics/initialize', json={}).status_code == 200
    manual = client.post('/api/v1/topics', json={'name': '人工观察', 'instrument_ids': ['600001.SH']}).json()
    assert add(client).status_code == 200
    client.app.include_router(platform.router)
    status = {'data_end': market.entry, 'snapshot_id': 'fixture-snapshot'}
    monkeypatch.setattr(market_service, 'context', lambda as_of=None: (market, status, as_of or market.entry))
    monkeypatch.setattr(market, 'instrument', lambda symbol: {'instrument_id': symbol.upper(), 'kind': 'stock', 'name': symbol}, raising=False)
    monkeypatch.setattr(market, 'chart', lambda symbol, as_of=None: {'bars': market.bars(symbol, as_of)}, raising=False)
    monkeypatch.setattr(market, 'topics_data', lambda: deepcopy(catalog), raising=False)
    monkeypatch.setattr(market, 'reference', lambda: {'abnormal': []}, raising=False)
    with core.connect() as connection:
        initial_jobs = connection.execute('SELECT count(*) FROM job').fetchone()[0]
        initial_rounds = connection.execute('SELECT count(*) FROM tracking_round').fetchone()[0]
    listed = client.get('/api/v1/watchlist').json()['items'][0]
    current = client.get('/api/v1/instruments/600001.SH').json()
    historical = client.get('/api/v1/instruments/600001.SH', params={'as_of': market.days[69]}).json()
    assert current['current_topics'] == historical['current_topics'] == listed['current_topics']
    assert current['current_industries'] == listed['current_industries']
    assert current['current_topics_observed_date'] == historical['current_topics_observed_date'] == market.entry
    assert historical['data_date'] == market.days[69]
    assert historical['topics'] == [], 'old observed-date topics must not become current classifications'
    assert any('不使用当前分类回填历史' in warning for warning in historical['warnings'])
    assert '当日上涨原因' in current['current_topics_scope']
    assert next(tag for tag in current['current_topics'] if tag['name'] == '来源行业')['source_observed_date'] == source_day
    assert 'event_close' not in current and 'tracking_round_id' not in current
    # Manual membership changes must not be hidden by a quote-generation cache.
    client.post('/api/v1/topics/' + manual['topic_id'] + '/members', json={'instrument_id': '600001.SH', 'action': 'exclude'})
    changed = client.get('/api/v1/instruments/600001.SH').json()
    assert '人工观察' not in [tag['name'] for tag in changed['current_topics']]
    assert changed['current_topics'] == client.get('/api/v1/watchlist').json()['items'][0]['current_topics']
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM job').fetchone()[0] == initial_jobs
        assert connection.execute('SELECT count(*) FROM tracking_round').fetchone()[0] == initial_rounds


def test_shared_membership_read_does_not_read_quotes_or_create_tasks(personal, monkeypatch):
    market, client = personal
    assert client.post('/api/v1/topics/initialize', json={}).status_code == 200
    def forbidden(*args, **kwargs):
        raise AssertionError('current membership does not read quotes, active DuckDB or provider data')
    for name in ('panel', 'bars', 'history_panel', 'history_for_instruments'):
        monkeypatch.setattr(market, name, forbidden, raising=False)
    before = stock_memberships.current_memberships(market, ['600001.SH', '000001.SZ'])
    assert before['business_date'] == market.entry
    assert before['memberships']['600001.SH'] and not before['memberships']['000001.SZ']
    assert stock_memberships.stock_membership_fields(market, '000001.SZ')['current_topics'] == []
