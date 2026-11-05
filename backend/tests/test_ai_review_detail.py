"""Bounded AI lookup searches synthetic retained evidence, never a current directory."""
from copy import deepcopy
from datetime import datetime
import sqlite3

import pytest

from app.core import digest, dumps
from app.services import ai_review_detail as detail
from app.services import ai_read_tools as tools
from app.services import auction_topic_summary

DAY = '2026-10-30'
NOW = datetime.fromisoformat(DAY + 'T14:00:00+08:00')


def auction():
    from test_auction_carryover_narrative import packet
    value, topics = packet()
    topics['stock_classifications'] = {row['instrument_id']: {
        'state': 'observed_sample', 'industries': [{
            'topic_code': topics['groups'][0]['topic_code'], 'name': '原光伏', 'kind': 'industry'}],
        'topics': []} for row in value['quotes']['items']}
    return value, topics


def noon():
    from test_noon_membership_context import payload, topic
    from app.services.noon_sector_views import freeze_memberships
    value = payload()
    value.update(collected_start_at=value['date'] + 'T11:35:00+08:00',
                 collected_end_at=value['date'] + 'T11:35:10+08:00', slot='noon')
    for i, row in enumerate(value['stocks']):
        row.update(name='合成光伏' + str(i), currency='CNY', price_basis='none', amount=100000, volume=10000)
    value['sectors'][0].update(latest_price=102, prev_close=100, change_pct=2)
    value['events'].update(limit_break={'state': 'observed', 'count': 0, 'items': []},
                          limit_down={'state': 'unknown', 'count': None, 'items': []})
    value['events']['limit_up']['items'][0]['continue_day_cnt'] = 2
    value['sources'] = [{
        'name': key + '_page_1', 'source': 'fuyao', 'state': 'received', 'sha256': 'a' * 64,
        'endpoint': '/api/a-share/special-data/' + key.replace('_', '-') + '-pool',
        'source_timestamp': {'state': 'observed', 'time': value['collected_start_at']}}
        for key in ('limit_up', 'limit_break', 'limit_down')]
    value['sector_memberships'] = freeze_memberships(value, [topic()])
    return value


def test_auction_stock_lookup_keeps_original_price_height_and_reference_boundary():
    value, topics = auction()
    original = deepcopy(value)
    data = detail.project_auction(value, '600001', topics=[topics])
    assert data['stocks']['matched_total'] == 1
    stock = data['stocks']['items'][0]
    assert stock['auction_price'] == 11 and stock['auction_pct'] == 10
    assert stock['previous_board_count'] == 4 and 'board_count' not in stock
    assert stock['opening_reference_price'] == 11
    assert stock['opening_reference_confirmed_limit'] is False
    assert stock['limit_up_state'] == stock['broken_state'] == 'unknown'
    assert stock['classification']['items'][0]['name'] == '原光伏'
    assert data['directions']['matched_total'] == 0
    assert value == original


def test_topic_lookup_uses_all_original_candidates_and_marks_candidate_average():
    value, topics = auction()
    topics['groups'][0]['name'] = '在短榜外的原光伏'
    data = detail.project_auction(value, '原光伏', topics=[topics])
    group = data['directions']['items'][0]
    assert (group['sample_count'], group['high_open'], group['low_open']) == (3, 2, 1)
    assert group['change_pct'] == 4
    assert group['quote_basis'] == 'retained_candidate_auction_average'
    assert group['members']['matched_total'] == 3
    assert '非全板块' in group['membership']['scope']


def test_supplement_is_searchable_before_bound_and_conflicting_duplicate_is_unknown():
    value, topics = auction()
    extra = {'instrument_id': '300999.SZ', 'name': '补充候选', 'auction_price': 10.5,
             'pre_close_price': 10, 'auction_pct': 5}
    discovery = {'stocks': [extra], 'supplement': {'items': []}}
    row = detail.project_auction(value, '补充候选', topics=[topics], discovery=discovery)['stocks']['items'][0]
    assert row['quote_available'] and row['auction_pct'] == 5 and row['board'] == 'chinext'
    assert row['classification']['state'] == 'unknown'
    conflict = {**value['quotes']['items'][0], 'auction_price': 10.8, 'auction_pct': 8}
    discovery['stocks'].append(conflict)
    row = detail.project_auction(value, '600001', topics=[topics], discovery=discovery)['stocks']['items'][0]
    assert row['quote_available'] is False and '冲突' in row['quote_reason']
    assert 'auction_price' not in row and 'opening_reference_price' not in row


def test_ambiguous_query_searches_full_retained_scope_before_output_limit():
    value, topics = auction()
    value['quotes']['items'] = [{
        'instrument_id': f'{600000+i:06}.SH', 'name': '合成同名' + str(i),
        'state': 'observed', 'auction_price': 10, 'pre_close_price': 10,
        'auction_pct': 0} for i in range(80)]
    data = detail.project_auction(value, '合成同名', topics=[])
    assert data['source_scope']['stock_count'] == 80
    assert data['stocks']['matched_total'] == 80
    assert data['stocks']['returned_count'] == 30 and data['stocks']['truncated']
    exact = detail.project_auction(value, '合成同名79', topics=[])
    assert exact['stocks']['matched_total'] == 1
    assert exact['stocks']['items'][0]['instrument_id'] == '600079.SH'


def test_empty_query_keeps_full_counts_and_bounded_distribution_remainders():
    value, topics = auction()
    topics['groups'] = [{**topics['groups'][0], 'topic_code': f'{884001+i:06}.TI',
                         'name': '原方向' + str(i)} for i in range(15)]
    data = detail.project_auction(value, '', topics=[topics])
    assert data['stocks']['matched_total'] == 4 and data['stocks']['returned_count'] == 0
    assert data['directions']['matched_total'] == 15 and data['directions']['returned_count'] == 0
    distribution = data['distributions']['industry']
    assert distribution['matched_total'] == 15 and distribution['returned_count'] == 6 and distribution['truncated']


def test_noon_stock_reports_same_snapshot_price_events_and_frozen_classification():
    value = noon()
    original = deepcopy(value)
    data = detail.project_noon(value, '600001')
    row = data['stocks']['items'][0]
    assert (row['latest_price'], row['change_pct'], row['amount']) == (11, 10, 100000)
    assert row['limit_up_state'] == 'sealed' and row['board_count'] == 2
    assert row['broken_state'] == 'not_in_verified_pool' and row['limit_down_state'] == 'unknown'
    assert row['classification']['items'][0]['topic_code'] == value['sectors'][0]['instrument_id']
    assert data['pools']['limit_break'] == {'state': 'observed', 'count': 0}
    assert data['pools']['limit_down'] == {'state': 'unknown', 'count': None}
    assert value == original


def test_noon_direction_keeps_independent_index_and_full_member_counts():
    value = noon()
    data = detail.project_noon(value, '合成板块')
    row = data['directions']['items'][0]
    assert row['latest_price'] == 102 and row['change_pct'] == 2
    assert row['members']['matched_total'] == 2 and row['up_ratio'] == 50
    assert row['limit_up_count'] == 1 and row['broken_count'] == 0
    assert row['membership']['source_sha256'] == value['sector_memberships']['items']['884288.TI']['sha256']
    assert len(row['members']['items']) == 2
    assert all(stock['classification']['state'] == 'observed_sample' for stock in row['members']['items'])


def test_bad_original_member_sha_does_not_clear_real_index_price_or_follow_current(monkeypatch):
    from app.services import noon_membership_context as membership
    value = noon()
    value['sector_memberships']['items']['884288.TI']['sha256'] = '0' * 64
    monkeypatch.setattr(membership, '_load_topics', lambda *_: pytest.fail('must not read current or legacy members'))
    row = detail.project_noon(value, '884288.TI')['directions']['items'][0]
    assert row['latest_price'] == 102 and row['change_pct'] == 2
    assert row['limit_up_count'] is None and row['membership']['state'] == 'unknown'
    assert row['members']['matched_total'] is None and row['members']['items'] == []


@pytest.mark.parametrize('damage', ['duplicate_quote', 'wrong_pct', 'price_basis', 'currency'])
def test_bad_noon_stock_evidence_never_uses_another_day_or_fabricates_flat(damage):
    value = noon()
    quote = value['stocks'][0]
    if damage == 'duplicate_quote':
        value['stocks'].append(deepcopy(quote))
    elif damage == 'wrong_pct':
        quote['change_pct'] = 0
    else:
        quote[damage] = 'other'
    row = detail.project_noon(value, '600001')['stocks']['items'][0]
    assert row['quote_available'] is False and 'latest_price' not in row
    assert row['limit_up_state'] == 'unknown' and 'board_count' not in row


def test_output_budget_drops_whole_rows_after_full_matching_with_explicit_truncation():
    value, _ = auction()
    value['quotes']['items'] = [{
        'instrument_id': f'{600000+i:06}.SH', 'name': '合成预算' + '长' * 1500,
        'state': 'observed', 'auction_price': 10, 'pre_close_price': 10,
        'auction_pct': 0} for i in range(80)]
    data = detail.project_auction(value, '合成预算', topics=[])
    assert len(dumps(data).encode()) <= detail.MAX_DATA_BYTES
    assert data['stocks']['matched_total'] == 80 and data['stocks']['truncated']
    assert data['stocks']['returned_count'] == len(data['stocks']['items']) < 30
    assert all(len(row['name']) == 1504 for row in data['stocks']['items'])
    assert data['bounded_notice']


@pytest.fixture
def saved(tmp_path, monkeypatch):
    path = tmp_path / 'retained.sqlite3'
    connection = sqlite3.connect(path)
    for table in ('market_auction_snapshot', 'market_noon_snapshot'):
        connection.execute('CREATE TABLE ' + table + '(snapshot_id TEXT,trade_date TEXT UNIQUE,'
            'collected_start_at TEXT,collected_end_at TEXT,content_sha256 TEXT,payload_json TEXT,'
            'recorded_at_ms INTEGER,job_id TEXT)')
    connection.execute('CREATE TABLE job(job_id TEXT,status TEXT,finished_at_ms INTEGER)')
    connection.commit()
    monkeypatch.setattr(tools, '_paths', lambda: [('readonly-test', path)])
    monkeypatch.setattr(detail, '_auction_topics', lambda *args: [])
    yield connection, path
    connection.close()


def save(connection, phase, value=None, **changes):
    from test_ai_market_brief import auction_payload, noon_payload
    value = deepcopy(value or (auction_payload() if phase == 'auction' else noon_payload()))
    end = int(datetime.fromisoformat(value['collected_end_at']).timestamp() * 1000)
    row = {'snapshot_id': 'original-' + phase, 'trade_date': DAY,
           'collected_start_at': value['collected_start_at'], 'collected_end_at': value['collected_end_at'],
           'content_sha256': digest(value), 'payload_json': dumps(value), 'recorded_at_ms': end + 1000, 'job_id': 'job-' + phase}
    row.update(changes)
    connection.execute('INSERT INTO market_' + phase + '_snapshot VALUES(?,?,?,?,?,?,?,?)', tuple(row.values()))
    connection.execute('INSERT INTO job VALUES(?,?,?)', ('job-' + phase, 'succeeded', end + 1000))
    connection.commit()
    return row


@pytest.mark.parametrize('phase', ['auction', 'noon'])
def test_dated_reader_bypasses_close_day_preserves_sha_and_never_writes(saved, phase):
    connection, path = saved
    row = save(connection, phase)
    original = path.read_bytes()
    result = tools._result('read_market_detail', DAY)
    detail.fill_review_detail(result, DAY, phase, '', NOW)
    assert len(result['evidence']) == 1 and result['source'] == ['readonly-test']
    assert result['source_bindings']['review_detail']['content_sha256'] == row['content_sha256']
    assert result['evidence'][0]['dates']['collected_end_at'] == row['collected_end_at']
    assert path.read_bytes() == original


@pytest.mark.parametrize('damage', ['sha', 'unfinished', 'late_finished', 'late_recorded', 'denied', 'wrong_day'])
def test_original_source_tampering_late_completion_and_permission_are_not_read(saved, damage):
    connection, _ = saved
    from test_ai_market_brief import noon_payload
    value = noon_payload()
    if damage == 'denied':
        value['source_permissions'] = {'ai_processing_allowed': False}
    row = save(connection, 'noon', value)
    if damage == 'sha':
        connection.execute("UPDATE market_noon_snapshot SET content_sha256=?", ('0' * 64,))
    elif damage == 'unfinished':
        connection.execute("UPDATE job SET status='running'")
    elif damage == 'late_finished':
        connection.execute('UPDATE job SET finished_at_ms=?', (int(NOW.timestamp() * 1000) + 1,))
    elif damage == 'late_recorded':
        connection.execute('UPDATE market_noon_snapshot SET recorded_at_ms=?', (int(NOW.timestamp() * 1000) + 1,))
    elif damage == 'wrong_day':
        connection.execute("UPDATE market_noon_snapshot SET trade_date='2026-10-29'")
    connection.commit()
    result = tools._result('read_market_detail', row['trade_date'] if damage != 'wrong_day' else '2026-10-29')
    detail.fill_review_detail(result, result['date'], 'noon', '', NOW)
    assert not result['evidence'] and result['gaps']


def test_invalid_first_dated_source_never_falls_through_to_other_current_instance(saved, monkeypatch):
    connection, path = saved
    save(connection, 'noon', content_sha256='0' * 64)
    later = path.parent / 'must-not-open.sqlite3'
    monkeypatch.setattr(tools, '_paths', lambda: [('selected', path), ('other-instance', later)])
    original_connect = tools._connect
    def read(candidate):
        assert candidate != later
        return original_connect(candidate)
    monkeypatch.setattr(tools, '_connect', read)
    result = tools._result('read_market_detail', DAY)
    detail.fill_review_detail(result, DAY, 'noon', '', NOW)
    assert not result['evidence'] and result['gaps']


def test_absent_future_snapshot_is_not_due_and_never_uses_previous_day(saved):
    connection, _ = saved
    save(connection, 'auction')
    result = tools._result('read_market_detail', '2026-11-02')
    detail.fill_review_detail(result, '2026-11-02', 'auction', '', NOW)
    assert result['state'] == 'not_due' and not result['evidence']


def test_auction_topic_proof_can_use_exact_selected_readonly_connection(monkeypatch):
    from test_auction_topic_summary import payload
    reader = sqlite3.connect(':memory:')
    reader.row_factory = sqlite3.Row
    reader.execute('PRAGMA query_only=ON')
    called = []
    def load(generation, previous, expected_end, *, connection=None):
        assert connection is reader
        called.append((generation, previous))
        return [], 'a' * 64
    monkeypatch.setattr(auction_topic_summary, '_load_original_topics', load)
    auction_topic_summary.build_topic_summary(payload(), connection=reader)
    assert len(called) == 1 and reader.execute('PRAGMA query_only').fetchone()[0] == 1
    reader.close()
