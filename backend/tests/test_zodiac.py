"""Controlled fixtures only; independent DB, no providers or running installation."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import uuid
import pytest
from test_workspace import ws, advance
from app import core
from app.routers.zodiac import router
from app.services import zodiac, zodiac_metrics


@pytest.fixture
def annual(ws, monkeypatch):
    market, client = ws
    client.app.include_router(router)
    for module in (zodiac, zodiac_metrics): monkeypatch.setattr(module, 'get_market', lambda: market)
    monkeypatch.setattr(market, 'history_for_instruments', lambda symbols, start=None, end=None: {s: [deepcopy(r) for r in market.data.get(s, []) if (not start or r['date'] >= start) and (not end or r['date'] <= end)] for s in symbols}, raising=False)
    monkeypatch.setattr(market, 'actions_for_instruments', lambda symbols: {s: [] for s in symbols}, raising=False)
    return market, client


def command(client, year=2027, **extra):
    head = client.get(f'/api/v1/zodiac-pools/{year}').json()
    return {'request_key': str(uuid.uuid4()), 'expected_business_date': head['business_date'], 'expected_generation': head['generation'], **extra}


def add(client, year=2027, symbol='600001.SH', **meta):
    return client.post(f'/api/v1/zodiac-pools/{year}/members', json=command(client, year, items=[{'instrument_id': symbol, **meta}]))


def listing(client, year=2027, **params):
    response = client.get(f'/api/v1/zodiac-pools/{year}/members', params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_years_and_empty_gets_do_not_write(annual):
    _, client = annual
    assert zodiac.year_info(2026)['zodiac'] == '羊'
    assert zodiac.year_info(2027)['target_year'] == 2028
    assert zodiac.year_info(2027)['zodiac'] == '猴'
    assert zodiac.year_info(2028)['zodiac'] == '鸡'
    for year in (2026, 2027, 2028): assert listing(client, year)['items'] == []
    with core.connect() as c: assert c.execute('SELECT count(*) FROM zodiac_pool').fetchone()[0] == 0
    assert client.get('/api/v1/zodiac-pools/2200').status_code == 422


def test_add_duplicate_immutable_history_readd_and_independent_pools(annual):
    market, client = annual
    payload = command(client, items=[{'instrument_id': '600001.SH', 'reason': '个人观察', 'focused': True}])
    first = client.post('/api/v1/zodiac-pools/2027/members', json=payload)
    assert first.status_code == 200, first.text
    row = first.json()['items'][0]['item']; identifier = row['member_id']
    assert row['added_business_date'] == market.entry
    assert row['entry_price'] == market.data['600001.SH'][70]['close']
    assert row['entry_basis']['quote_sha256'] and row['entry_basis']['source_snapshots']
    assert client.post('/api/v1/zodiac-pools/2027/members', json=payload).json() == first.json()
    bad = {**payload, 'items': [{'instrument_id': '000001.SZ'}]}
    assert client.post('/api/v1/zodiac-pools/2027/members', json=bad).status_code == 409
    duplicate = add(client, reason='不应覆盖').json()['items'][0]
    assert duplicate['state'] == 'already_active' and duplicate['item']['reason'] == '个人观察'
    edit = command(client, revision=row['revision'], reason='修订')
    changed = client.patch('/api/v1/zodiac-pool-members/'+identifier, json=edit).json()['item']
    assert changed['entry_basis'] == row['entry_basis'] and changed['entry_price'] == row['entry_price']
    assert client.patch('/api/v1/zodiac-pool-members/'+identifier, json={**edit, 'request_key': str(uuid.uuid4())}).status_code == 409
    advance(market, 71)
    ended = client.post('/api/v1/zodiac-pool-members/'+identifier+'/end', json=command(client, revision=changed['revision'], end_reason='结束')).json()['item']
    assert ended['end_business_date'] == market.days[71]
    prior = listing(client, status='ended')['items'][0]
    advance(market, 75)
    later = listing(client, status='ended')['items'][0]
    for key in ('metrics_as_of', 'close', 'return_ytd_pct', 'change_30d_pct', 'since_entry_pct', 'stats_year'):
        assert prior[key] == later[key]
    new = add(client).json()['items'][0]['item']
    assert new['round_number'] == 2 and new['entry_price'] != row['entry_price']
    other = add(client, 2028).json()['items'][0]['item']
    assert other['round_number'] == 1 and other['member_id'] != new['member_id']
    history = client.get('/api/v1/zodiac-pool-members/'+identifier+'/history').json()
    assert [a['action'] for a in history['items']] == ['add', 'edit', 'end']
    with core.connect() as c:
        for table in ('tracking_round', 'watchlist_item'): assert c.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError): c.execute('UPDATE zodiac_pool_member SET entry_price_e8=1 WHERE member_id=?', (identifier,))


def test_batch_atomic_capacity_and_validation(annual, monkeypatch):
    _, client = annual
    body = command(client, items=[{'instrument_id': '600001.SH'}, {'instrument_id': 'BAD'}])
    assert client.post('/api/v1/zodiac-pools/2027/members', json=body).status_code == 422
    assert listing(client)['total'] == 0
    monkeypatch.setattr(zodiac, 'MAX_ACTIVE', 1)
    body['items'][1]['instrument_id'] = '000001.SZ'
    assert client.post('/api/v1/zodiac-pools/2027/members', json=body).status_code == 409
    assert listing(client)['total'] == 0
    for meta in ({'focused': 'yes'}, {'links': ['javascript:alert(1)']}, {'relation_type': []}, {'reason': 'a'*1001}):
        assert add(client, **meta).status_code == 422
    preview = client.post('/api/v1/zodiac-pools/2027/preview', json={'text': '600001.SH,600001,不存在'}).json()['items']
    assert [r['state'] for r in preview] == ['ready', 'duplicate', 'unrecognized']


def test_concurrent_adds_and_generation_race(annual, monkeypatch):
    market, client = annual
    payloads = [command(client, items=[{'instrument_id': '600001.SH'}]) for _ in range(2)]
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda p: zodiac.add(2027, p), payloads))
    assert sorted(r['items'][0]['state'] for r in results) == ['added', 'already_active']
    original = zodiac._baseline_batch
    def race(*args):
        result = original(*args); advance(market, 71); return result
    monkeypatch.setattr(zodiac, '_baseline_batch', race)
    assert add(client, symbol='000001.SZ').status_code == 409
    assert listing(client)['total'] == 1


def test_missing_baseline_only_explicitly_repaired_and_never_overwritten(annual):
    market, client = annual
    missing = market.data['600001.SH'].pop(70)
    row = add(client).json()['items'][0]['item']; identifier = row['member_id']
    assert row['entry_price'] is None
    market.data['600001.SH'].insert(70, missing)
    assert listing(client)['items'][0]['entry_price'] is None
    verified = client.post('/api/v1/zodiac-pools/2027/verify-baselines', json=command(client, member_ids=[identifier]))
    assert verified.status_code == 200, verified.text
    assert verified.json()['items'][0]['entry_price'] == missing['close']
    frozen = missing['close']
    market.data['600001.SH'][70]['close'] += 1
    again = client.post('/api/v1/zodiac-pools/2027/verify-baselines', json=command(client, member_ids=[identifier])).json()
    assert again['items'][0]['entry_price'] == frozen
    assert listing(client)['items'][0]['peak_return_pct'] is None


def test_metrics_unknown_sorting_full_pool_and_cutoff(annual):
    market, client = annual
    client.post('/api/v1/topics/initialize', json={})
    add(client); add(client, symbol='000001.SZ')
    value = listing(client, sort='change_30d_pct:desc', limit=1)
    assert value['matched_count'] == 2 and value['items'][0]['instrument_id'] == '600001.SH'
    row = value['items'][0]
    assert row['stats_year'] == 2026 and row['return_ytd_pct'] is None
    assert row['limit_up_ytd'] is None and row['limit_up_ytd_observed'] == 0
    assert row['change_30d_pct'] == pytest.approx((market.data['600001.SH'][70]['close']/market.data['600001.SH'][40]['close']-1)*100)
    assert row['since_entry_pct'] == 0 and row['max_drawdown_pct'] == 0
    assert row['current_topics']
    # Exact endpoint missing stays last in both sorting directions; don't shift date.
    market.data['600001.SH'].pop(40)
    for direction in ('asc', 'desc'):
        value = listing(client, sort='change_30d_pct:'+direction)
        assert value['items'][-1]['instrument_id'] == '600001.SH'
        assert value['items'][-1]['change_30d_pct'] is None
    assert listing(client, query='合成题材')['matched_count'] == 1
    detail = client.get('/api/v1/zodiac-pool-members/'+row['member_id']+'/metrics')
    assert detail.status_code == 200, detail.text


def test_path_drawdown_chronology_gaps_actions_and_boll(annual, monkeypatch):
    market, client = annual
    row = add(client).json()['items'][0]['item']
    values = [122, 134.2, 109.8, 128.1, 115.9]
    for i, close in enumerate(values, 70):
        market.data['600001.SH'][i].update(open=close, high=close+1, low=close-1, close=close)
    advance(market, 74)
    result = listing(client)['items'][0]
    assert result['since_entry_pct'] == pytest.approx(-5)
    assert result['peak_return_pct'] == pytest.approx(10)
    assert result['drawdown_pct'] == pytest.approx((115.9/134.2-1)*100)
    assert result['max_drawdown_pct'] == pytest.approx((109.8/134.2-1)*100)
    assert result['holding_sessions'] == 4
    closes = [r['close'] for r in market.data['600001.SH'][55:75]]
    mean = sum(closes)/20
    variance = sum((x-mean)**2 for x in closes)/20
    assert result['ma20'] == pytest.approx(mean)
    assert result['boll_mid'] == pytest.approx(mean)
    assert result['boll_upper'] == pytest.approx(mean+2*variance**.5)
    assert result['ma250'] is None
    # Endpoint remains valid, but a missing internal session invalidates path metrics.
    removed = market.data['600001.SH'].pop(72)
    result = listing(client)['items'][0]
    assert result['since_entry_pct'] == pytest.approx(-5)
    assert result['peak_return_pct'] is None and result['max_drawdown_pct'] is None
    assert result['ma20'] is None and result['boll_mid'] is None
    market.data['600001.SH'].insert(72, removed)
    monkeypatch.setattr(market, 'actions_for_instruments', lambda symbols: {s: None for s in symbols})
    result = listing(client)['items'][0]
    assert result['since_entry_pct'] is None and result['return_5d_pct'] is None
    assert result['change_pct'] is None and result['volume_vs_5d'] is None


def test_readonly_get_missing_market_and_backup_closure(annual, monkeypatch, tmp_path):
    market, client = annual
    add(client)
    with core.connect() as c:
        before = {t: c.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ('job','zodiac_pool_audit','zodiac_pool_request')}
    row = listing(client)['items'][0]
    client.get('/api/v1/zodiac-pool-members/'+row['member_id']+'/metrics')
    with core.connect() as c:
        after = {t: c.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in before}
        assert before == after
        target = sqlite3.connect(tmp_path/'restored.sqlite')
        c.backup(target)
    raw = target.execute('SELECT entry_basis_json FROM zodiac_pool_member').fetchone()[0]
    import hashlib, json
    proof = json.loads(raw)
    assert hashlib.sha256(zodiac.js(proof['quote']).encode()).hexdigest() == proof['quote_sha256']
    assert target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    target.close()


def test_empty_install_browse_does_not_require_or_initialize_market(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for key in ('SHIXU_SQLITE_PATH','SHIXU_PUBLISHED_DIR','SHIXU_RAW_DIR','SHIXU_WORK_DIR'): monkeypatch.delenv(key, raising=False)
    core.ensure_ready()
    monkeypatch.setattr(zodiac_metrics, 'get_market', lambda: (_ for _ in ()).throw(AssertionError('empty browse must not touch market')))
    app = FastAPI(); app.include_router(router); client = TestClient(app)
    result = listing(client)
    assert result['business_date'] is None and result['items'] == []
    assert add(client).status_code == 409


def test_shared_exact_year_limits_lower_bounds_and_batched_reads(annual, monkeypatch):
    from test_topic_member_metrics import MetricMarket, complete_year, A, B
    source = MetricMarket()
    market, client = annual
    market.days = source.days; market.data = source.data
    monkeypatch.setattr(market, 'status', source.status, raising=False)
    monkeypatch.setattr(market, 'instruments', source.instruments)
    monkeypatch.setattr(market, 'history_for_instruments', source.history_for_instruments)
    monkeypatch.setattr(market, 'actions_for_instruments', source.actions_for_instruments)
    data = complete_year(source, {day: [A] if day in (source.days[20], source.day) else ['688001.SH'] for day in source.days if day[:4] == source.day[:4] and day <= source.day})
    monkeypatch.setattr(zodiac_metrics, 'load_supplement_with_fingerprint', lambda *args, **kwargs: (data, 'isolated-test'))
    # Rebuild only this empty fixture's initial clock before any pool exists.
    with core.connect() as c:
        c.execute('DELETE FROM business_clock')
        c.execute("INSERT INTO business_clock VALUES(1,?,'cal',?)", (source.day, core.now_ms()))
    assert add(client, symbol=A).status_code == 200
    assert add(client, symbol=B).status_code == 200
    source.calls.clear()
    result = listing(client, sort='limit_up_ytd:desc')
    assert source.calls['history_for_instruments'] == 3, 'one quote batch + one extended batch + one canonical metric batch'
    assert source.calls['panel'] == 0
    a,b = result['items']
    assert a['instrument_id'] == A and a['limit_up_ytd'] == 2
    assert b['instrument_id'] == B and b['limit_up_ytd'] == 0
    assert a['limit_up_30d'] == 2 and a['today_limit_up'] is True
    assert a['return_ytd_pct'] is not None
    assert a['return_ytd_coverage']['start'] == '2025-12-31'
    del data['annual_pools'][source.days[30]]
    partial = listing(client)['items']
    a = next(r for r in partial if r['instrument_id']==A)
    assert a['limit_up_ytd'] is None and a['limit_up_ytd_observed'] == 2
    assert a['limit_up_30d'] is None and a['limit_up_30d_observed'] == 2


def test_migrate_existing_v32_without_changing_watchlist(tmp_path, monkeypatch):
    import shutil
    from app.infrastructure import database
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path/'data'))
    for key in ('SHIXU_SQLITE_PATH','SHIXU_PUBLISHED_DIR','SHIXU_RAW_DIR','SHIXU_WORK_DIR'): monkeypatch.delenv(key, raising=False)
    original = database.MIGRATIONS_DIR
    migrations = tmp_path/'migrations'; migrations.mkdir()
    for file in original.glob('[0-9]*.sql'):
        if int(file.name.split('_')[0]) < 33: shutil.copyfile(file, migrations/file.name)
    monkeypatch.setattr(database, 'MIGRATIONS_DIR', migrations)
    core.ensure_ready()
    with core.connect() as c:
        c.execute("INSERT INTO watchlist_item(instrument_id,source_kind,source_name,added_business_date,event_basis_json,created_at_ms) VALUES('600001.SH','stock','迁移保留测试','2026-10-30','{}',1)")
        before = dict(c.execute('SELECT * FROM watchlist_item').fetchone())
        assert c.execute('PRAGMA user_version').fetchone()[0] == 32
    shutil.copyfile(original/'033_zodiac_pools.sql', migrations/'033_zodiac_pools.sql')
    core.ensure_ready()
    with core.connect() as c:
        assert c.execute('PRAGMA user_version').fetchone()[0] == 33
        assert dict(c.execute('SELECT * FROM watchlist_item').fetchone()) == before
        assert c.execute('SELECT count(*) FROM zodiac_pool').fetchone()[0] == 0
        assert c.execute('PRAGMA foreign_key_check').fetchall() == []
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_same_day_raw_publication_invalidates_write_and_inflight_list(annual, monkeypatch):
    from app.services.workspace_common import insert
    _, client = annual
    row = add(client).json()['items'][0]['item']
    stale = command(client, revision=row['revision'], reason='不应写入旧代际')
    before = client.get('/api/v1/zodiac-pools/2027').json()

    def publish_raw(suffix):
        with core.connect() as c:
            snapshot = dict(c.execute("SELECT * FROM dataset_snapshot WHERE snapshot_id='raw'").fetchone())
            identifier = 'raw-' + suffix
            snapshot.update(snapshot_id=identifier, parent_snapshot_id='raw', state='staged', published_at_ms=None)
            insert(c, 'dataset_snapshot', snapshot)
            c.execute('INSERT INTO snapshot_artifact SELECT ?,object_key,role,sha256,byte_count,row_count FROM snapshot_artifact WHERE snapshot_id=?', (identifier, 'raw'))
            for coverage in c.execute("SELECT * FROM snapshot_coverage WHERE snapshot_id='raw'").fetchall():
                insert(c, 'snapshot_coverage', {**dict(coverage), 'snapshot_id': identifier})
            c.execute("UPDATE dataset_snapshot SET state='validated' WHERE snapshot_id=?", (identifier,))
            c.execute("UPDATE dataset_snapshot SET state='published',published_at_ms=? WHERE snapshot_id=?", (core.now_ms(), identifier))
            c.execute("UPDATE published_head SET snapshot_id=? WHERE module='daily_raw'", (identifier,))

    publish_raw('second')
    after = client.get('/api/v1/zodiac-pools/2027').json()
    assert after['business_date'] == before['business_date']
    assert after['primary_snapshot_id'] == before['primary_snapshot_id']
    assert after['generation'] != before['generation']
    assert client.patch('/api/v1/zodiac-pool-members/'+row['member_id'], json=stale).status_code == 409
    original = zodiac_metrics.enrich

    def changing_generation(*args):
        result = original(*args)
        publish_raw('third')
        return result

    monkeypatch.setattr(zodiac_metrics, 'enrich', changing_generation)
    assert client.get('/api/v1/zodiac-pools/2027/members').status_code == 409


def test_detail_rechecks_revision_after_retained_evidence_reads(annual, monkeypatch):
    _, client = annual
    row = add(client).json()['items'][0]['item']
    original = zodiac_metrics.retained_events

    def concurrent_edit(*args):
        result = original(*args)
        changed = client.patch('/api/v1/zodiac-pool-members/'+row['member_id'], json=command(client, revision=row['revision'], reason='读取详情期间修订'))
        assert changed.status_code == 200
        return result

    monkeypatch.setattr(zodiac_metrics, 'retained_events', concurrent_edit)
    response = client.get('/api/v1/zodiac-pool-members/'+row['member_id']+'/metrics')
    assert response.status_code == 409, '详情不得混合旧成员与新维护记录'
    monkeypatch.setattr(zodiac_metrics, 'retained_events', original)
    refreshed = client.get('/api/v1/zodiac-pool-members/'+row['member_id']+'/metrics').json()
    assert refreshed['item']['reason'] == '读取详情期间修订'
    assert refreshed['history'][-1]['action'] == 'edit'


def test_full_capacity_pagination_and_bounded_market_reads(annual, monkeypatch):
    market, client = annual
    template = market.data['600001.SH']
    symbols = [f'{600001+i:06d}.SH' for i in range(501)]
    market.data = {symbol: deepcopy(template) for symbol in symbols}
    for offset in range(0, 500, 100):
        payload = command(client, items=[{'instrument_id': s} for s in symbols[offset:offset+100]])
        assert client.post('/api/v1/zodiac-pools/2027/members', json=payload).status_code == 200
    assert add(client, symbol=symbols[-1]).status_code == 409
    calls = []
    original = market.history_for_instruments

    def record_batch(requested, **kwargs):
        calls.append(len(requested))
        return original(requested, **kwargs)

    monkeypatch.setattr(market, 'history_for_instruments', record_batch)
    result = listing(client, sort='instrument_id:desc', offset=450, limit=50)
    assert result['total'] == result['matched_count'] == len(result['navigation']) == 500
    assert [r['instrument_id'] for r in result['items']] == list(reversed(symbols[:50]))
    assert len(calls) <= 12 and max(calls) <= 129, '行情应按128只批读，扩展指标可另带一个基准指数'
    with core.connect() as c:
        assert c.execute('SELECT count(*) FROM zodiac_pool_member').fetchone()[0] == 500


def test_pasted_partial_names_initials_and_pinyin_need_confirmation(annual, monkeypatch):
    market, client = annual
    monkeypatch.setattr(market, 'instruments', lambda *a, **k: [
        {'instrument_id': '002724.SZ', 'name': '海洋王', 'kind': 'stock'},
        {'instrument_id': '300053.SZ', 'name': '航宇微', 'kind': 'stock'},
        {'instrument_id': '000001.SH', 'name': '海洋王', 'kind': 'index'},
        {'instrument_id': '885728.TI', 'name': '海洋概念', 'kind': 'concept'},
    ])
    response = client.post('/api/v1/zodiac-pools/2027/preview', json={'text': '洋 hyw haiyangwang 002724 002724.SZ 885728.TI 000001.SH'})
    assert response.status_code == 200
    items = response.json()['items']
    assert [r['state'] for r in items] == ['ambiguous', 'ambiguous', 'ambiguous', 'ready', 'duplicate', 'unrecognized', 'unrecognized']
    assert {r['instrument_id'] for r in items[1]['candidates']} == {'002724.SZ', '300053.SZ'}
    assert all(r['instrument_id'] is None for r in items[:3]), '部分名称和拼音即使只有一项匹配也须主动确认'
    assert add(client, symbol='000001.SH').status_code == 422
    assert add(client, symbol='885728.TI').status_code == 422
    with core.connect() as c:
        assert c.execute('SELECT count(*) FROM zodiac_pool_member').fetchone()[0] == 0
