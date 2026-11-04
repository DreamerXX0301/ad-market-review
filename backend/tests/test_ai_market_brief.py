"""Single-day synthetic SQLite snapshots; no credentials, model or sources."""
from copy import deepcopy
from pathlib import Path
import json
import sqlite3

import pytest
from fastapi import HTTPException

from app.core import digest, dumps
from app.services import ai_market_brief as brief

DAY = '2026-10-30'
SYMBOL = '600001.SH'


def auction_payload():
    start, end = DAY + 'T09:25:05+08:00', DAY + 'T09:25:10+08:00'
    sources = []
    evidence = []
    for name, endpoint, params in (
            ('calendar', '/api/a-share/calendar/trading-days', {}),
            ('auction', '/api/a-share/auction/snapshot', {'stage': 'final', 'thscodes': SYMBOL})):
        body = {'code': 0, 'data': {}}
        metadata = {'name': name, 'source': 'fuyao', 'endpoint': endpoint, 'params': params,
                    'requested_at': start, 'received_at': end, 'state': 'received',
                    'bytes': len(dumps(body).encode()), 'sha256': digest(body)}
        sources.append(metadata)
        evidence.append({**metadata, 'body': body})
    return {'version': 'auction-snapshot-v1', 'date': DAY,
            'collected_start_at': start, 'collected_end_at': end, 'sources': sources, 'evidence': evidence,
            'universe': {'items': [{'instrument_id': SYMBOL}]},
            'quotes': {'state': 'partial', 'items': [{'instrument_id': SYMBOL, 'state': 'observed', 'auction_pct': 0}],
                       'summary': {'requested': 1, 'valid': 1, 'missing': 0, 'up': 0, 'down': 0,
                                   'flat': 1, 'median_pct': 0, 'amount': 0}}, 'gaps': []}


def noon_payload():
    return {'version': 'noon-snapshot-v1', 'date': DAY,
            'collected_start_at': DAY + 'T11:35:00+08:00', 'collected_end_at': DAY + 'T11:36:00+08:00',
            'summary': {'breadth': {'up': 0, 'down': 0, 'flat': 1, 'valid': 1, 'total': 1, 'missing': 0},
                        'amount': {'value': 0, 'valid': 1, 'total': 1}},
            'sectors': [], 'events': {'limit_up': {'state': 'unknown', 'count': None, 'reason': '池未核验'}}, 'gaps': []}


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / 'copy.sqlite3'
    monkeypatch.setenv('SHIXU_SQLITE_PATH', str(path))
    c = sqlite3.connect(path)
    for statement in ('PRAGMA foreign_keys=ON', 'PRAGMA recursive_triggers=ON', 'PRAGMA busy_timeout=10000'):
        c.execute(statement)
    for table in ('market_auction_snapshot', 'market_noon_snapshot'):
        c.execute('CREATE TABLE ' + table + '(snapshot_id TEXT PRIMARY KEY,trade_date TEXT UNIQUE,'
                  'collected_start_at TEXT,collected_end_at TEXT,content_sha256 TEXT,payload_json TEXT)')
    c.execute('CREATE TABLE market_noon_close_review(review_id TEXT PRIMARY KEY,snapshot_id TEXT,trade_date TEXT,'
              'revision INTEGER,state TEXT,generated_at_ms INTEGER,noon_sha256 TEXT,close_input_sha256 TEXT,'
              'result_sha256 TEXT,result_json TEXT)')
    c.commit()
    yield c
    c.close()


def save(c, table, identifier, payload):
    c.execute('INSERT INTO ' + table + ' VALUES(?,?,?,?,?,?)', (
        identifier, DAY, payload['collected_start_at'], payload['collected_end_at'], digest(payload), dumps(payload)))
    c.commit()


def save_close(c, revision=1, **changes):
    noon = noon_payload()
    result = {'state': 'partial', 'summary': {
        'breadth': {'common': {'afternoon': {'up': 0, 'down': 1, 'flat': 0, 'valid': 1,
                                          'total': 1, 'mean_pct': -2, 'median_pct': -2}}},
        'amount': {'state': 'partial', 'delta': 1000, 'valid_count': 1, 'unknown_count': 1,
                   'complete': False, 'reason': '只核验同股累计额'}},
        'classification_counts': {'state': 'partial', 'classified_count': 1, 'unclassified_count': 1,
                                  'industries': [], 'topics': []}, 'gaps': ['原午间归属部分未知']}
    row = {'review_id': 'review-' + str(revision), 'snapshot_id': 'noon', 'trade_date': DAY,
           'revision': revision, 'state': 'partial', 'generated_at_ms': 12345,
           'noon_sha256': digest(noon), 'close_input_sha256': 'a' * 64,
           'result_sha256': digest(result), 'result_json': dumps(result), **changes}
    c.execute('INSERT INTO market_noon_close_review VALUES(?,?,?,?,?,?,?,?,?,?)', tuple(row.values()))
    c.commit()
    return row


def test_missing_snapshots_are_unknown_not_zero_and_never_fall_back_to_old_day(database):
    result = brief.build_briefs(DAY)
    assert result['blocks'] == [] and len(result['gaps']) == 3
    assert all(row['state'] == 'unknown' and row['date'] == DAY for row in result['gaps'])
    save(database, 'market_noon_snapshot', 'noon', noon_payload())
    assert brief.build_briefs('2026-11-02')['blocks'] == []


def test_snapshot_dates_sha_and_real_zero_amount_are_preserved(database):
    save(database, 'market_auction_snapshot', 'auction', auction_payload())
    save(database, 'market_noon_snapshot', 'noon', noon_payload())
    result = brief.build_briefs(DAY)
    assert len(result['blocks']) == 2
    auction = result['blocks'][0]
    assert auction['evidence']['dates']['trade_date'] == DAY
    assert auction['evidence']['dates']['collected_start_at'].startswith(DAY + 'T09:25')
    assert auction['evidence']['source_binding']['content_sha256'] == digest(auction_payload())
    metrics = {row['metric_id']: row for block in result['blocks'] for row in block['metrics']}
    assert metrics['day_brief:auction:amount']['value'] == 0
    assert metrics['day_brief:auction:amount']['unit'] == 'CNY'
    assert metrics['day_brief:noon:limit_up']['value'] is None
    assert metrics['day_brief:noon:amount']['value'] == 0


def test_discovery_and_opening_comparison_are_bound_to_same_day_read_only_sources(database, monkeypatch):
    from datetime import datetime
    from app.services import ai_read_tools
    from app.services import auction_discovery, intraday_service
    from test_intraday_brief import _auction_opening_inputs, _clock

    save(database, 'market_auction_snapshot', 'auction', auction_payload())
    discovery, opening = _auction_opening_inputs(DAY)
    discovery['primary_snapshot_id'] = 'auction'
    discovery['directions'][0].update(kind='industry', median_auction_pct=3, total_auction_amount=11000)
    opening.update(version=intraday_service.VERSION)
    database.executescript("""
        CREATE TABLE market_auction_discovery(trade_date TEXT PRIMARY KEY, primary_snapshot_id TEXT,
          content_sha256 TEXT, recorded_at_ms INTEGER);
        CREATE TABLE job(job_id TEXT PRIMARY KEY, status TEXT, finished_at_ms INTEGER);
        CREATE TABLE market_intraday_capture(job_id TEXT, slot TEXT, trade_date TEXT,
          snapshot_id TEXT, content_sha256 TEXT, payload_json TEXT);
    """)
    database.execute('INSERT INTO market_auction_discovery VALUES (?,?,?,?)',
                     (DAY, 'auction', discovery['content_sha256'], discovery['recorded_at_ms']))
    database.execute('INSERT INTO job VALUES (?,?,?)', ('test', 'succeeded', _clock('10:01:13', DAY)))
    database.execute('INSERT INTO market_intraday_capture VALUES (?,?,?,?,?,?)',
                     ('test', '1000', DAY, opening['id'], digest(opening), dumps(opening)))
    database.commit()

    def view(day, *, connection):
        assert day == DAY and connection.execute('PRAGMA query_only').fetchone()[0] == 1
        return deepcopy(discovery)

    monkeypatch.setattr(auction_discovery, 'read_view', view)
    output = brief.build_briefs(DAY)
    assert output['fingerprints']['auction_opening']['content_sha256'] == digest(opening)
    block = next(row for row in output['blocks'] if row['evidence']['kind'] == 'auction_discovery_brief')
    data = block['evidence']['data']
    assert data['opening_comparison']['comparable'] == 3
    assert data['directions']['industries'][0]['opening_comparison'] == {'comparable': 2, 'median_change_pp': 1.5}
    assert all(row['value'] is not None for row in block['metrics'])
    monkeypatch.delenv('SHIXU_AI_READ_SQLITE_PATH', raising=False)
    monkeypatch.setattr(ai_read_tools, 'local_now', lambda: datetime.fromisoformat(DAY + 'T11:00:00+08:00'))
    actual_tool = ai_read_tools.execute_read_tool('read_market_data', {'date': DAY, 'section': 'auction'})
    assert actual_tool['ok']
    assert any(evidence['kind'] == 'auction_discovery_brief' for evidence in actual_tool['evidence'])
    assert brief.build_briefs('2026-10-29')['blocks'] == []
    database.execute("UPDATE market_intraday_capture SET content_sha256='invalid'")
    database.commit()
    after = brief.build_briefs(DAY)
    updated = next(row for row in after['blocks'] if row['evidence']['kind'] == 'auction_discovery_brief')
    assert updated['evidence']['data']['opening_state'] == 'unknown'


def test_latest_close_revision_has_exact_parent_sha_and_amount_is_never_net_flow(database):
    save(database, 'market_noon_snapshot', 'noon', noon_payload())
    save_close(database)
    latest = save_close(database, revision=2)
    result = brief.build_briefs(DAY)
    close = next(row for row in result['blocks'] if row['evidence']['kind'] == 'noon_close_brief')
    assert close['evidence']['source_binding']['review_id'] == latest['review_id']
    assert close['evidence']['source_binding']['revision'] == 2
    assert close['evidence']['dates']['noon_collected_start_at'] == noon_payload()['collected_start_at']
    assert '不是净流入' in close['evidence']['data']['scope']
    amount = next(row for row in close['metrics'] if row['metric_id'].endswith(':amount_delta'))
    assert amount['value'] == 1000 and amount['unit'] == 'CNY' and '非净流入' in amount['label']
    assert close['evidence']['data']['amount']['unknown_count'] == 1


@pytest.mark.parametrize('fault', ['sha', 'date', 'window', 'denied'])
def test_invalid_or_explicitly_denied_payload_is_not_sent(database, fault):
    payload = noon_payload()
    if fault == 'date': payload['date'] = '2026-10-29'
    if fault == 'denied': payload['ai_processing_allowed'] = False
    save(database, 'market_noon_snapshot', 'noon', payload)
    if fault == 'sha': database.execute("UPDATE market_noon_snapshot SET content_sha256='broken'")
    if fault == 'window': database.execute("UPDATE market_noon_snapshot SET collected_end_at='wrong'")
    database.commit()
    result = brief.build_briefs(DAY)
    assert not result['blocks']
    assert any(row['capability'] == 'noon_brief' and row['state'] == 'unknown' for row in result['gaps'])


def test_close_with_wrong_parent_is_unknown_instead_of_joining_other_snapshot(database):
    save(database, 'market_noon_snapshot', 'noon', noon_payload())
    save_close(database, noon_sha256='wrong-parent')
    result = brief.build_briefs(DAY)
    assert all(row['evidence']['kind'] != 'noon_close_brief' for row in result['blocks'])


def test_revision_change_while_building_conflicts(database, monkeypatch):
    save(database, 'market_noon_snapshot', 'noon', noon_payload())
    before = brief.fingerprints(DAY)
    after = deepcopy(before)
    after['noon_close'] = {'review_id': 'new-review', 'revision': 2}
    answers = iter([before, after])
    monkeypatch.setattr(brief, 'fingerprints', lambda day: next(answers))
    with pytest.raises(HTTPException) as error:
        brief.build_briefs(DAY)
    assert error.value.status_code == 409


def test_read_connection_is_query_only_and_has_required_pragmas(database):
    c = brief._connect()
    try:
        assert c.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert c.execute('PRAGMA recursive_triggers').fetchone()[0] == 1
        assert c.execute('PRAGMA busy_timeout').fetchone()[0] == 10000
        with pytest.raises(sqlite3.OperationalError): c.execute('DELETE FROM market_noon_snapshot')
    finally:
        c.close()


def test_direction_projection_is_bounded_and_keeps_membership_dates_and_overlap(database, monkeypatch):
    payload = noon_payload()
    payload['sectors'] = [{'topic_code': str(881000 + index) + '.TI', 'name': '合成行业' + str(index),
                           'kind': kind, 'change_pct': index, 'members_observed_date': '2026-10-21',
                           'member_limit_up_count': None, 'member_limit_up_state': 'unknown'}
                          for kind in ('industry', 'concept') for index in range(10)]
    save(database, 'market_noon_snapshot', 'noon', payload)
    result = brief.build_briefs(DAY)
    data = result['blocks'][0]['evidence']['data']
    assert len(dumps(data).encode()) <= brief.MAX_BRIEF_DATA_BYTES
    assert len(data['directions']['industries']) <= 3 and len(data['directions']['concepts']) <= 3
    assert all(row['members_observed_date'] == '2026-10-21' for rows in data['directions'].values() for row in rows)
    assert '非互斥' in data['direction_scope']
    assert all(row['member_limit_up_count'] is None for rows in data['directions'].values() for row in rows)


def test_retained_close_direction_counts_are_referenceable_and_wide_labels_are_not_mainlines(database):
    save(database, 'market_noon_snapshot', 'noon', noon_payload())
    row = save_close(database)
    payload = json.loads(row['result_json'])
    for kind, field in (('industry', 'industries'), ('concept', 'topics')):
        payload['classification_counts'][field] = [
            {'kind': kind, 'source': 'fuyao', 'topic_code': str(881000 + index) + '.TI',
             'name': '融资融券' if kind == 'concept' and index == 0 else '合成方向' + str(index),
             'members_observed_date': '2026-10-21', 'count': 2, 'valid_count': 1,
             'close_limit_up_count': 1, 'mean_afternoon_change_pct': -1,
             'unknown_count': 1, 'state': 'partial'} for index in range(3)]
    database.execute('UPDATE market_noon_close_review SET result_json=?,result_sha256=?',
                     (dumps(payload), digest(payload)))
    database.commit()
    block = next(row for row in brief.build_briefs(DAY)['blocks']
                 if row['evidence']['kind'] == 'noon_close_brief')
    directions = block['evidence']['data']['directions']
    retained = [row for rows in directions.values() for row in rows]
    assert len(retained) >= 3
    counts = [row for row in block['metrics'] if row['metric_id'].endswith(':close_limit_up_count')]
    comparable = [row for row in block['metrics'] if row['metric_id'].endswith(':valid_count')]
    assert len(counts) == len(comparable) == len(retained)
    assert all(row['value'] == 1 and row['unit'] == '家' for row in [*counts, *comparable])
    assert all(row['dates']['members_observed_date'] == '2026-10-21' for row in counts)
    assert '宽标签不是主线或资金方向' in block['evidence']['data']['direction_scope']


def test_auction_single_stock_direction_is_explicitly_a_small_sample(database, monkeypatch):
    save(database, 'market_auction_snapshot', 'auction', auction_payload())
    monkeypatch.setattr(brief, 'build_topic_summary', lambda payload: {
        'state': 'partial', 'groups': [{'kind': 'industry', 'topic_code': '881001.TI',
                                      'name': '合成行业', 'valid_count': 1, 'sample_count': 1,
                                      'average_auction_pct': 1, 'members_observed_date': '2026-10-21'}]})
    block = brief.build_briefs(DAY)['blocks'][0]
    assert block['evidence']['data']['directions']['industries'][0]['valid_count'] == 1
    assert '单股样本，不推全市场' in block['evidence']['data']['direction_scope']
