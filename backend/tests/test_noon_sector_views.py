"""Synthetic immutable noon members, no Key/provider/network or formal DB."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest
from fastapi import HTTPException

from app import core
from app.services import noon_membership_context as membership
from app.services import noon_sector_views as views
from app.services.noon_analysis import normalize_quotes, sector_members
from app.services.noon_collection import canonical
from test_noon_membership_context import payload, topic, publish, published, CODE, GENERATION
from test_noon_snapshots import noon, execute, DAY
from test_workspace import ws
from test_noon_retention import local, capture as retained_capture, job, publication


def view(value, **filters):
    return views.build_sector_view(value, CODE, snapshot_id='noon_' + '1' * 32,
                                   content_sha256=sha256(canonical(value)).hexdigest(), **filters)


def test_new_frozen_members_join_only_the_same_noon_quotes_and_never_current_topics(monkeypatch):
    value = payload()
    value['stocks'][0].update(name='午间合成甲', amount=123, open=10, high=11, low=10)
    value['stocks'][1].update(name='午间合成乙', amount=0)
    value['calendar'] = {'previous_day': '2026-10-27'}
    value['sector_memberships'] = views.freeze_memberships(value, [topic()])
    before = deepcopy(value)
    monkeypatch.setattr(membership, '_load_topics', lambda *args: pytest.fail('new snapshot must not read another catalog'))
    result = view(value)
    assert value == before
    assert result['formal_chart_as_of'] == '2026-10-27'
    assert [row['latest_price'] for row in result['items']] == [11, 9]
    assert [row['change_pct'] for row in result['items']] == [10, -10]
    assert result['membership_as_of'] == '2026-10-21'
    assert result['quote_coverage'] == {'valid': 2, 'total': 2}
    assert result['quote_scope'] == 'noon_snapshot'
    assert result['items'][0]['current_events'] == ['limit_up']
    assert result['items'][1]['amount'] == 0


def test_closing_targets_reuse_only_original_frozen_members(monkeypatch):
    value = payload()
    value['stock_classifications'] = {}
    value['sector_memberships'] = views.freeze_memberships(value, [topic()])
    symbol = value['stocks'][1]['instrument_id']
    monkeypatch.setattr(membership, '_load_topics', lambda *args: pytest.fail('no current catalog'))
    result = views.close_classifications(value, [symbol, '600999.SH'])
    assert result[symbol]['state'] == 'observed_sample'
    assert result[symbol]['industries'][0]['topic_code'] == CODE
    assert result['600999.SH']['state'] == 'unknown'
    value['sector_memberships']['items'][CODE]['sha256'] = '0' * 64
    assert views.close_classifications(value, [symbol])[symbol]['state'] == 'unknown'


def test_missing_quote_stays_unknown_board_filter_sort_and_pages_are_full_sample():
    value, t = payload(), topic()
    t['instrument_ids'].extend(['300001.SZ', '688001.SH', '920001.BJ'])
    value['sectors'][0]['member_coverage'].update(total=5, complete=False)
    value['sector_memberships'] = views.freeze_memberships(value, [t])
    result = view(value, limit=2, page=3, direction='asc')
    assert result['total'] == 5 and result['pages'] == 3 and result['quote_coverage']['valid'] == 2
    assert result['items'][0]['instrument_id'] == '920001.BJ'
    assert result['items'][0]['latest_price'] is None and result['items'][0]['change_pct'] is None
    assert '不回退' in result['items'][0]['missing_reason']
    star = view(value, board='star')
    assert star['total'] == 5 and star['matched'] == 1 and star['items'][0]['instrument_id'] == '688001.SH'
    assert view(value, query='600001')['matched'] == 1


@pytest.mark.parametrize('tamper', ['hash', 'member', 'future', 'kind', 'source', 'aggregate'])
def test_frozen_list_integrity_identity_and_aggregate_must_reconcile(tamper):
    value = payload()
    value['sector_memberships'] = views.freeze_memberships(value, [topic()])
    entry = value['sector_memberships']['items'][CODE]
    if tamper == 'aggregate':
        value['sectors'][0]['member_up'] = 99
    elif tamper == 'hash':
        entry['sha256'] = '0' * 64
    else:
        if tamper == 'member': entry['instrument_ids'].append(entry['instrument_ids'][0])
        if tamper == 'future': entry['members_observed_date'] = '2026-10-29'
        if tamper == 'kind': entry['kind'] = 'concept'
        if tamper == 'source': entry['source'] = 'other'
        entry['sha256'] = sha256(canonical({k: v for k, v in entry.items() if k != 'sha256'})).hexdigest()
    result = view(value)
    assert result['state'] == 'unknown' and result['total'] is None and result['items'] == []


def test_omitted_frozen_lists_do_not_backfill_and_budget_is_bounded(monkeypatch):
    value = payload()
    saved = views.freeze_memberships(value, [topic()], budget=200)
    assert saved['items'] == {} and saved['omitted_count'] == 1
    value['sector_memberships'] = saved
    monkeypatch.setattr(membership, '_load_topics', lambda *args: pytest.fail('explicit omission cannot backfill'))
    assert view(value)['state'] == 'unknown'
    assert len(canonical(views.freeze_memberships(payload(), [topic()]))) <= views.MAX_BYTES


def test_capacity_keeps_both_existing_leading_and_weak_workbench_directions(monkeypatch):
    value = payload(); sectors, topics = [], []
    for i in range(8):
        code = f'88400{i}.TI'
        sectors.append({**value['sectors'][0], 'instrument_id': code, 'topic_code': code,
                        'latest_price': 10, 'change_pct': i - 4})
        topics.append({**topic(), 'topic_code': code})
    value['sectors'] = sectors
    monkeypatch.setattr(views, 'MAX_LINKS', 12)  # Six two-member lists fit.
    frozen = views.freeze_memberships(value, topics)
    assert set(frozen['items']) == {f'88400{i}.TI' for i in (0, 1, 2, 5, 6, 7)}
    assert frozen['omitted_count'] == 2


@pytest.mark.parametrize('saved', [None, [], {'version': views.VERSION, 'items': ['not-a-map']}])
def test_malformed_frozen_membership_namespace_remains_unknown(saved, monkeypatch):
    value = payload(); value['sector_memberships'] = saved
    monkeypatch.setattr(membership, '_load_topics', lambda *args: pytest.fail('invalid frozen namespace cannot backfill'))
    assert view(value)['state'] == 'unknown'


def test_legacy_snapshot_reads_original_generation_only_and_keeps_body_immutable(published):
    publish(published)
    (published / 'market-head.json').write_text(json.dumps({'snapshot_id': 'do-not-follow-current-head'}))
    value = payload(); original = canonical(value)
    result = view(value)
    assert result['total'] == 2 and result['membership_source_sha256']
    assert canonical(value) == original and 'sector_memberships' not in value
    (published / GENERATION / 'reference.json').write_text('{}')
    assert view(value)['state'] == 'unknown'


@pytest.mark.parametrize('filters', [{'board': 'bad'}, {'page': True}, {'page': 0}, {'limit': 101},
    {'sort': 'close'}, {'direction': 'bad'}, {'query': 'x' * 101}])
def test_invalid_filters_fail_closed(filters):
    with pytest.raises(HTTPException) as error: view(payload(), **filters)
    assert error.value.status_code == 422


def test_actual_capture_and_http_join_add_zero_provider_calls_preserve_noon_and_daily(noon, monkeypatch):
    market, client, source, _, _, _, original = noon
    topics = [{'topic_code': '881101.TI', 'name': '合成行业', 'source': 'fuyao', 'kind': 'industry',
               'observed_date': '2026-10-27', 'members_complete': True,
               'instrument_ids': ['600001.SH', '000001.SZ', '301001.SZ']}]
    monkeypatch.setattr(market, 'topics_data', lambda: topics, raising=False)
    _, result = execute(noon)
    snapshot_id = result['snapshot_id']; calls = deepcopy(source.calls)
    assert len(calls) == 11  # Original synthetic noon scope, no constituent/stock N+1 requests.
    with core.connect() as c:
        old = dict(c.execute('SELECT content_sha256,payload_json FROM market_noon_snapshot WHERE snapshot_id=?', (snapshot_id,)).fetchone())
    assert 'sector_memberships' in json.loads(old['payload_json'])
    response = client.get(f'/api/v1/market/noon/{snapshot_id}/sector-members?topic_code=881101.TI&board=chinext')
    assert response.status_code == 200, response.text
    assert response.json()['total'] == 3 and response.json()['matched'] == 1
    assert response.json()['items'][0]['latest_price'] is None
    assert source.calls == calls
    with core.connect() as c:
        assert dict(c.execute('SELECT content_sha256,payload_json FROM market_noon_snapshot WHERE snapshot_id=?', (snapshot_id,)).fetchone()) == old
        assert [tuple(r) for r in c.execute('SELECT * FROM published_head')] == original['heads']
        assert [tuple(r) for r in c.execute('SELECT * FROM business_clock')] == original['clock']
    assert client.get('/api/v1/market/noon/' + 'noon_' + '0' * 32 + '/sector-members?topic_code=881101.TI').status_code == 404
    assert client.get(f'/api/v1/market/noon/{snapshot_id}/sector-members?topic_code=881101.TI&limit=101').status_code == 422


def test_new_memberships_expire_with_the_original_noon_body_not_an_extra_store(local):
    from app.services import noon_retention
    value = payload()
    value['version'] = 'noon-snapshot-v1'
    value['sector_memberships'] = views.freeze_memberships(value, [topic()])
    snapshot_id, _, path = retained_capture(payload=value)
    assert views.read_sector_view(snapshot_id, CODE)['total'] == 2
    increment = job('fixture-new-increment'); publication(increment['job_id'])
    result = noon_retention.after_increment(increment)
    assert result['purged_snapshots'] == 1 and not path.exists()
    with pytest.raises(HTTPException) as error: views.read_sector_view(snapshot_id, CODE)
    assert error.value.status_code == 404
