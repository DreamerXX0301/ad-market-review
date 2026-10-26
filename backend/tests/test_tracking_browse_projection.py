"""Request-local tracking projections preserve complete legacy view evidence."""
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.services import tracking as stock, sector_tracking as sector
from app.services.workspace_common import js, rows

DAY = '2026-01-08'
END = '2026-01-06'
CALENDAR = ['2026-01-02', '2026-01-05', END, '2026-01-07', DAY]


@pytest.fixture
def connection():
    value = sqlite3.connect(':memory:')
    value.row_factory = sqlite3.Row
    for pragma in ('foreign_keys=ON', 'recursive_triggers=ON', 'busy_timeout=10000'):
        value.execute('PRAGMA ' + pragma)
    value.executescript('''
      CREATE TABLE tracking_policy_version(strategy_id TEXT,policy_version INTEGER,duration_sessions INTEGER,
        allow_entry_day_alerts INTEGER,allow_entry_day_exits INTEGER,alert_logic TEXT,alerts_json TEXT,
        exit_logic TEXT,exits_json TEXT,recorded_at_ms INTEGER,PRIMARY KEY(strategy_id,policy_version));
      CREATE TABLE tracking_policy_application(application_id TEXT PRIMARY KEY,round_id TEXT,strategy_id TEXT,
        previous_policy_version INTEGER,policy_version INTEGER,effective_business_date TEXT,recorded_at_ms INTEGER,reason TEXT);
      CREATE TABLE tracking_event(event_id TEXT PRIMARY KEY,event_key TEXT,round_id TEXT,event_type TEXT,
        business_date TEXT,data_snapshot_id TEXT,data_state TEXT,outcome TEXT,trading_age INTEGER,
        evidence_json TEXT,recorded_at_ms INTEGER);
      CREATE INDEX trigger_list ON tracking_event(event_type,business_date DESC,round_id);
      CREATE TABLE sector_tracking_event(event_id TEXT PRIMARY KEY,event_key TEXT,round_id TEXT,event_type TEXT,
        business_date TEXT,policy_revision INTEGER,data_state TEXT,outcome TEXT,trading_age INTEGER,
        evidence_json TEXT,recorded_at_ms INTEGER);
      CREATE INDEX sector_tracking_event_list ON sector_tracking_event(business_date DESC,event_type,round_id);
    ''')
    for version in (1, 2, 3):
        value.execute('INSERT INTO tracking_policy_version VALUES(?,?,?,?,?,?,?,?,?,?)',
                      ('strategy', version, version*10, 0, 0, 'OR', js([{'version': version}]), 'OR', '[]', version))
    yield value
    value.close()


def stock_round(identifier='round-one', ended=False, forward=False):
    return {'round_id': identifier, 'strategy_id': 'strategy', 'entry_strategy_version': 1,
        'instrument_id': '600001.SH', 'entry_policy_version': 1, 'current_policy_version': 3,
        'entry_business_date': CALENDAR[0], 'end_business_date': END if ended else None,
        'status': 'manual_removed' if ended else 'tracking', 'entry_price_e8': 1000000000,
        'entry_basis_json': js({'price_basis': 'none', 'entry_topics': ['frozen-entry'],
            'signal_price_basis': 'forward' if forward else 'none'}), 'created_at_ms': 1}


def event(connection, table, rid, kind, day, stamp, evidence, suffix='', revision=1):
    number = connection.execute('SELECT count(*) FROM ' + table).fetchone()[0]
    identifier = f'{table}:{number}:{suffix}'
    if table == 'tracking_event':
        connection.execute('INSERT INTO tracking_event VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (identifier, identifier, rid, kind, day, 'snapshot', 'ready', 'not_applicable', 1, js(evidence), stamp))
    else:
        connection.execute('INSERT INTO sector_tracking_event VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (identifier, identifier, rid, kind, day, revision, 'ready', 'not_applicable', 1, js(evidence), stamp))


def seed_stock(connection, row):
    rid = row['round_id']
    for number, version in enumerate((2, 3)):
        # Equal effective/recorded dates must resolve by the last rowid.
        connection.execute('INSERT INTO tracking_policy_application VALUES(?,?,?,?,?,?,?,?)',
            (f'{rid}:{number}', rid, 'strategy', 1, version, END, 100, 'same timestamp'))
    event(connection, 'tracking_event', rid, 'entry', CALENDAR[0], 1, {'basis': 'entry'})
    metrics = {'close': 12, 'change_pct': 4, 'tracking_comparison_anchor': END,
        'tracking_comparison_market_snapshot_id': 'old-generation', 'tracking_reference_price_basis': 'forward_event_affine',
        'tracking_reference_price': 8, 'tracking_change_pct': 50, 'tracking_reference_evidence': {'verified': True},
        'tracking_comparison_scope': 'frozen comparison'}
    event(connection, 'tracking_event', rid, 'review', END, 101,
          {'metrics': metrics, 'topics': ['ending-review'], 'policy_version': 2, 'comparison_state': 'verified'})
    event(connection, 'tracking_event', rid, 'review', END, 101,
          {'metrics': metrics, 'topics': ['last-rowid-ending-review'], 'policy_version': 3, 'comparison_state': 'verified'})
    event(connection, 'tracking_event', rid, 'review', DAY, 200,
          {'metrics': {'close': 13, 'change_pct': 5}, 'topics': ['current-review'], 'policy_version': 2})
    # Alerts keep recorded-desc then original ascending-rowid tie order.
    event(connection, 'tracking_event', rid, 'alert', DAY, 300, {'name': 'first-tied-alert'})
    event(connection, 'tracking_event', rid, 'alert', DAY, 300, {'name': 'second-tied-alert'})
    event(connection, 'tracking_event', rid, 'note', DAY, 300, {'name': 'latest-rowid-note'})


@pytest.mark.parametrize('ended', [False, True])
@pytest.mark.parametrize('forward', [False, True])
@pytest.mark.parametrize('with_events', [False, True])
def test_stock_projection_matches_full_legacy_view_and_ties(connection, ended, forward, with_events):
    row = stock_round(ended=ended, forward=forward); seed_stock(connection, row)
    market = SimpleNamespace(corporate_actions_between=lambda *args: [], status=lambda: {'snapshot_id': 'current-generation'})
    kwargs = {'master': {'600001.SH': {'name': 'synthetic'}}, 'calendar': CALENDAR, 'bars': [], 'day': DAY,
              'with_events': with_events, 'current_memberships': {'600001.SH': ['fresh-current']},
              'quote': {'close': 15, 'change_pct': 8}, 'market': market}
    expected = stock._round_view(connection, row, **kwargs)
    prepared = stock._browse_inputs(connection, [row], DAY, with_events=with_events)
    actual = stock._round_view(connection, row, **kwargs, browse=prepared[row['round_id']])
    assert actual == expected
    assert actual['policy']['policy_version'] == 3
    assert [item['evidence']['name'] for item in actual['alerts']] == ['first-tied-alert', 'second-tied-alert']
    assert actual['latest_event']['evidence']['name'] == 'latest-rowid-note'
    assert actual['topics'] == ['current-review'] and actual['topics_snapshot'] == ['frozen-entry']
    if ended:
        assert actual['latest_price'] == 12 and actual['today_change_pct'] == 4
        if forward: assert actual['tracking_change_pct'] == 50


def test_stock_projection_reads_fresh_policy_and_review_each_request(connection):
    row = stock_round(); seed_stock(connection, row)
    first = stock._browse_inputs(connection, [row], DAY)[row['round_id']]
    connection.execute('INSERT INTO tracking_policy_application VALUES(?,?,?,?,?,?,?,?)',
        ('new-application', row['round_id'], 'strategy', 3, 1, DAY, 500, 'new request'))
    event(connection, 'tracking_event', row['round_id'], 'review', DAY, 500,
          {'topics': ['changed-after-first-request'], 'policy_version': 1})
    second = stock._browse_inputs(connection, [row], DAY)[row['round_id']]
    assert first['policy']['policy_version'] == 3 and second['policy']['policy_version'] == 1
    assert stock._browse_payload(second['latest_review'], second['payloads'])['topics'] == ['changed-after-first-request']


def test_stock_queries_scale_by_chunk_and_do_not_load_historic_json(connection):
    selected = [stock_round(f'round-{index}') for index in range(260)]
    for row in selected:
        for stamp in range(4):
            event(connection, 'tracking_event', row['round_id'], 'review', END, stamp+1, {'historic': stamp})
        event(connection, 'tracking_event', row['round_id'], 'review', DAY, 5, {'current': True})
    statements = []; connection.set_trace_callback(statements.append)
    prepared = stock._browse_inputs(connection, selected, DAY)
    connection.set_trace_callback(None)
    assert len(statements) == 6  # policy + compact event projection per 128 rounds
    assert len(prepared) == 260
    assert all(value['events'] is None for value in prepared.values())
    assert all(value['saved_review']['evidence_json'] == '{"current":true}' for value in prepared.values())
    assert all(value['latest'] == value['latest_review'] == value['saved_review'] for value in prepared.values())


def test_stock_entry_and_shared_event_payload_decode_only_once(connection, monkeypatch):
    row = stock_round(); seed_stock(connection, row)
    prepared = stock._browse_inputs(connection, [row], DAY)[row['round_id']]
    original = stock.obj; counts = {}
    def decode(value, default=None):
        counts[value] = counts.get(value, 0)+1
        return original(value, default)
    monkeypatch.setattr(stock, 'obj', decode)
    stock._round_view(connection, row, {}, CALENDAR, [], DAY, current_memberships={},
        quote={'close': 15, 'change_pct': 8}, market=SimpleNamespace(corporate_actions_between=lambda *args: []), browse=prepared)
    assert counts[row['entry_basis_json']] == 1
    assert all(count == 1 for count in counts.values())


def sector_round(ended=False):
    policy = {'duration_sessions': 30, 'alerts': [], 'exits': [], 'alert_logic': 'ANY', 'exit_logic': 'ANY'}
    return {'round_id': 'sector-one', 'strategy_id': 'sector-strategy', 'entry_business_date': CALENDAR[0],
        'end_business_date': END if ended else None, 'status': 'manual_removed' if ended else 'tracking',
        'target_id': '885001.TI', 'entry_price_e8': 10000000000, 'entry_high_e8': 10200000000,
        'entry_low_e8': 9800000000, 'entry_basis_json': js({'anchor_state': 'ready'}),
        'entry_cache_refs_json': js({'sha256': 'a'*64}), 'entry_policy_json': js(policy), 'policy_json': js(policy),
        'entry_period': 'W', 'policy_revision': 3, 'created_at_ms': 1, 'duration_sessions': 30}


def sector_info(close=105):
    return {'close_price': close, 'metrics_by_period': {'D': {'close': close, 'change_pct': 3,
        'observed_date': END, 'private_nested': {'must_not_mutate': True}}, 'W': {'large': ['not public']*10}},
        'coverage': {'available': True, 'observation_date': END, 'preserved': 'whole coverage'}}


def seed_sector(connection, row):
    rid = row['round_id']; policy = json.loads(row['entry_policy_json'])
    event(connection, 'sector_tracking_event', rid, 'entry', CALENDAR[0], 1, {'entry': True})
    for version in (2, 3):
        event(connection, 'sector_tracking_event', rid, 'policy_change', END, 100,
              {'policy': {**policy, 'duration_sessions': version*10}}, revision=version)
    event(connection, 'sector_tracking_event', rid, 'review', END, 200,
          {'market_info': sector_info(105), 'revision': 2}, revision=2)
    # _ended_info deliberately skips a newer review without a market_info
    # object; the compact query must retain the earlier frozen ending proof.
    event(connection, 'sector_tracking_event', rid, 'review', END, 200,
          {'market_info': None, 'revision': 3}, revision=3)
    event(connection, 'sector_tracking_event', rid, 'alert', END, 201, {'alert': 'first'}, revision=3)
    event(connection, 'sector_tracking_event', rid, 'alert', END, 201, {'alert': 'second'}, revision=3)
    event(connection, 'sector_tracking_event', rid, 'manual_remove', DAY, 300, {'latest': True}, revision=3)


@pytest.mark.parametrize('ended', [False, True])
@pytest.mark.parametrize('with_events', [False, True])
def test_sector_compact_events_policy_frozen_review_and_public_fields_match(connection, ended, with_events):
    row = sector_round(ended); seed_sector(connection, row)
    valuation = row['end_business_date'] or DAY
    full = rows(connection, 'SELECT *,rowid AS _order FROM sector_tracking_event WHERE round_id=? ORDER BY business_date,recorded_at_ms,rowid', (row['round_id'],))
    policy, _ = sector._policy_at(connection, row, valuation)
    current_info = sector_info(999); current_info['metrics_by_period']['D']['observed_date'] = valuation
    frozen = sector._ended_info(row, full, valuation)
    expected = sector._view(row, DAY, CALENDAR, frozen or current_info, policy, full)
    prepared = sector._browse_inputs(connection, [row], DAY, with_events=with_events)[row['round_id']]
    actual_frozen = sector._ended_info(row, prepared['events'], valuation, prepared['payloads'])
    info_before = deepcopy(current_info)
    actual = sector._view(row, DAY, CALENDAR, actual_frozen or current_info, prepared['policy'], prepared['events'], prepared['payloads'])
    assert actual == expected and current_info == info_before
    assert actual['policy']['duration_sessions'] == 30
    assert actual['latest_event']['evidence'] == {'latest': True}
    if ended:
        assert actual['latest_price'] == 105
        assert [item['evidence']['alert'] for item in actual['alerts']] == ['first', 'second']
        assert actual['policy_review_pending'] is False
    if with_events:
        assert [sector._event_view(event,prepared['payloads']) for event in prepared['events']] == [sector._event_view(event) for event in full]


def test_sector_policy_cutoff_does_not_borrow_future_revision(connection):
    row = sector_round(ended=True); seed_sector(connection, row)
    event(connection, 'sector_tracking_event', row['round_id'], 'policy_change', DAY, 999,
          {'policy': {'duration_sessions': 99}}, revision=4)
    prepared = sector._browse_inputs(connection, [row], DAY)[row['round_id']]
    assert prepared['policy']['duration_sessions'] == 30
    assert prepared['policy'] == sector._policy_at(connection,row,END)[0]
    assert prepared['events'][-1]['policy_revision'] == 4  # global latest is still shown


def test_sector_browse_uses_only_required_event_json_and_keeps_detail_history(connection):
    row = sector_round()
    for stamp in range(200):
        event(connection, 'sector_tracking_event', row['round_id'], 'review', END, stamp+1, {'historic': stamp})
    event(connection, 'sector_tracking_event', row['round_id'], 'review', DAY, 500, {'current': True})
    compact = sector._browse_inputs(connection,[row],DAY)[row['round_id']]
    detail = sector._browse_inputs(connection,[row],DAY,with_events=True)[row['round_id']]
    assert len(compact['events']) == 1 and len(detail['events']) == 201
