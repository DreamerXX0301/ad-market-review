"""Today/history refer to immutable event dates, never replayed current quotes."""
import json

from app import core
from app.services import line_alerts as alerts
from test_line_alerts import lines, quote, create, drain
from test_workspace import ws, advance
from test_watchlist import personal


def test_today_history_are_disjoint_and_late_backfill_does_not_become_today(lines):
    market, client = lines
    quote(market, 69, close=105)
    quote(market, 70, low=100, close=108)
    create(client, auto=False)
    drain()
    original = client.get('/api/v1/watchlist/line-alerts').json()['items'][0]
    assert client.get('/api/v1/watchlist/line-alerts', params={'scope': 'today'}).json()['total'] == 1
    advance(market, 71)
    today = client.get('/api/v1/watchlist/line-alerts', params={'scope': 'today'}).json()
    history = client.get('/api/v1/watchlist/line-alerts', params={'scope': 'history'}).json()
    assert today['total'] == 0 and today['today_total'] == 0
    assert today['history_total'] == history['total'] == 1
    assert history['items'][0] == original
    assert history['business_date'] == market.days[71] and history['scope'] == 'history'
    assert client.get('/api/v1/watchlist/line-alerts', params={'scope': 'today', 'as_of': market.days[70]}).status_code == 422
    assert client.get('/api/v1/watchlist/line-alerts', params={'scope': 'history', 'as_of': market.days[69]}).json()['items'] == []
    assert client.get('/api/v1/watchlist/line-alerts', params={'scope': 'unknown'}).status_code == 422


def test_header_summary_is_bounded_indexed_and_never_reads_market_or_writes(lines, monkeypatch):
    market, client = lines
    quote(market, 69, close=105)
    quote(market, 70, low=100, close=108)
    create(client, auto=False)
    drain()
    with core.connect() as connection:
        base = dict(connection.execute('SELECT * FROM line_alert_event').fetchone())
        for n in range(300):
            item = {**base, 'event_id': f'event-seed-{n:04}', 'annotation_id': f'line-seed-{n:04}', 'created_at_ms': base['created_at_ms'] + n + 1}
            body = json.loads(base['event_json'])
            body.update({k: item[k] for k in ('event_id', 'annotation_id', 'created_at_ms')})
            item['event_json'] = json.dumps(body)
            connection.execute('INSERT INTO line_alert_event VALUES(?,?,?,?,?,?,?,?,?,?,?)', list(item.values()))
        connection.commit()
        before = {name: connection.execute('SELECT count(*) FROM ' + name).fetchone()[0] for name in ('job', 'line_alert_event', 'line_alert_evaluation', 'watchlist_item')}
        plan = connection.execute('EXPLAIN QUERY PLAN SELECT event_json FROM line_alert_event WHERE trade_date=? ORDER BY created_at_ms DESC,event_id DESC LIMIT 5', (market.entry,)).fetchall()
        assert any('line_alert_by_day' in row[3] for row in plan)
    monkeypatch.setattr(alerts, 'get_market', lambda: (_ for _ in ()).throw(AssertionError('GET must not load quotes')))
    response = client.get('/api/v1/watchlist/line-alerts/summary')
    assert response.status_code == 200
    data = response.json()
    assert data['business_date'] == market.entry and data['total'] == 301
    assert len(data['items']) == data['limit'] == 5
    assert data['items'][0]['event_id'] == 'event-seed-0299'
    assert 'comparison_evidence' not in data['items'][0] and 'input_snapshots' not in data['items'][0]
    assert len(response.content) < 5000
    with core.connect() as connection:
        after = {name: connection.execute('SELECT count(*) FROM ' + name).fetchone()[0] for name in before}
    assert before == after


def test_legacy_all_and_today_limit_counts_are_not_truncated(lines):
    market, client = lines
    quote(market, 69, close=105)
    quote(market, 70, low=100, close=108)
    create(client, auto=False)
    drain()
    all_data = client.get('/api/v1/watchlist/line-alerts').json()
    today = client.get('/api/v1/watchlist/line-alerts', params={'scope': 'today', 'limit': 1}).json()
    assert all_data['scope'] == 'all'
    assert today['items'] == all_data['items'] and today['total'] == today['today_total'] == 1
    assert today['latest_check']['job_id'] == all_data['latest_check']['job_id']
