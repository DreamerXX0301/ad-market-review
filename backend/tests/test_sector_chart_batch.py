"""Small local catalogs prove bounded/resumable full-board collection semantics."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path

import pytest

from app.adapters.providers import ProviderError
from app.services import sector_chart_batch as batch, sector_charts as charts
from app.services import sector_history as sector
from app.services.market_supplement import SupplementCache
from app.services.task_service import TaskPaused, TaskCancelled
from test_sector_charts import Adapter, CODE, DAY, Market

INDUSTRY = '881101.TI'


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(charts, 'published_dir', lambda: tmp_path/'published')
    monkeypatch.setattr(charts, '_source_today', lambda: DAY)
    monkeypatch.setattr(batch.time, 'sleep', lambda delay: None)
    market = Market(start='2026-07-01')
    return market, Adapter(market), tmp_path/'published'


def run(local, adapter=None, **kwargs):
    return batch.collect_sector_chart_batch(local[0], batch.plan_sector_chart_batch(local[0], DAY),
                                            adapter or local[1], **kwargs)


def test_plan_freezes_catalog_identity_window_and_scope(local):
    plan = batch.plan_sector_chart_batch(local[0], DAY)
    assert plan['as_of'] == plan['end'] == DAY
    assert plan['start'] == '2026-07-01' and plan['snapshot_id'] == 'market-fixture'
    assert [board['topic_code'] for board in plan['boards']] == [INDUSTRY, CODE]
    assert {board['instrument']['sector_kind'] for board in plan['boards']} == {'industry', 'concept'}
    assert len(plan['request_signature']) == len(plan['catalog_signature']) == 64


def test_new_tail_pacing_is_explicit_signed_and_does_not_change_board_scope(local):
    legacy=batch.plan_sector_chart_batch(local[0],DAY,mode='increment')
    assert batch.plan_sector_chart_batch(local[0],DAY,mode='increment',request_policy_version=None)==legacy
    current=batch.plan_sector_chart_batch(local[0],DAY,mode='increment',request_policy_version=batch.SECTOR_TAIL_PACING_VERSION)
    assert current['boards']==legacy['boards'] and current['catalog_signature']==legacy['catalog_signature']
    assert current['request_signature']!=legacy['request_signature']
    assert batch._validate_plan(local[0],current)==current
    tampered=deepcopy(current);tampered.pop('sector_tail_request_policy_version')
    with pytest.raises(ValueError,match='冻结'):batch._validate_plan(local[0],tampered)
    for mode,version in [('initial',batch.SECTOR_TAIL_PACING_VERSION),('increment','future')]:
        with pytest.raises(ValueError):batch.plan_sector_chart_batch(local[0],DAY,mode,request_policy_version=version)
    assert local[1].calls==[]


@pytest.mark.parametrize('code',[2001,2003,4001])
def test_new_tail_refusal_stops_misses_but_revalidates_later_cache(local,code):
    market,adapter,_=local
    charts.collect_sector_chart(market,charts.plan_sector_chart(market,CODE,DAY),adapter)
    market.ref['concept_catalog'].append({'thscode':'886001.TI','name':'未请求概念'})
    class Refused:
        def request(self,*args):raise ProviderError('controlled refusal',code=code)
    plan=batch.plan_sector_chart_batch(market,DAY,'increment',request_policy_version=batch.SECTOR_TAIL_PACING_VERSION)
    result=batch.collect_sector_chart_batch(market,plan,Refused())
    assert result['coverage']['request_count']==result['coverage']['failed_count']==1
    assert result['coverage']['cache_hit_count']==result['coverage']['unrequested_count']==1
    assert result['sector_tail_request_policy_version']==batch.SECTOR_TAIL_PACING_VERSION
    assert batch.read_frozen_sector_chart_batch_report(plan,'increment')['coverage']==result['coverage']
    assert batch.read_sector_chart_batch_report(market,DAY,'increment')['sha256']==result['sha256']


@pytest.mark.parametrize('error_type',[TaskPaused,TaskCancelled,ValueError])
def test_new_tail_guard_propagates_without_publishing_report(local,error_type):
    plan=batch.plan_sector_chart_batch(local[0],DAY,'increment',request_policy_version=batch.SECTOR_TAIL_PACING_VERSION)
    def guard():
        if local[1].calls:raise error_type('controlled interruption')
    with pytest.raises(error_type):batch.collect_sector_chart_batch(local[0],plan,local[1],guard=guard)
    assert len(local[1].calls)==1
    assert not (local[2]/'market-supplement/accepted/fuyao/sector-chart-increment-batch').exists()


def test_batch_plan_and_manifest_status_read_reference_calendar_and_status_once(local, monkeypatch):
    market, _, _ = local
    counts = {'reference': 0, 'calendar': 0, 'status': 0}
    for name in counts:
        original = getattr(market, name)
        def counted(name=name, original=original):
            counts[name] += 1
            return original()
        monkeypatch.setattr(market, name, counted)
    batch.plan_sector_chart_batch(market, DAY)
    assert counts == {'reference': 1, 'calendar': 1, 'status': 1}
    counts.update(reference=0, calendar=0, status=0)
    assert batch.read_sector_chart_batch_report(market, DAY) is None
    assert counts == {'reference': 1, 'calendar': 1, 'status': 1}


def test_full_catalog_download_stays_per_board_and_report_contains_no_histories(local):
    events = []
    result = run(local, progress=lambda *args: events.append(args))
    assert result['quality'] == 'complete'
    assert result['coverage']['collection_complete'] and result['coverage']['complete']
    assert result['coverage']['available_count'] == result['coverage']['request_count'] == 2
    assert result['coverage']['cache_hit_count'] == result['coverage']['failed_count'] == 0
    assert [call[1]['thscode'] for call in local[1].calls] == [INDUSTRY, CODE]
    for item in result['items']:
        assert item['artifact'] and item['raw_artifact'] and item['sha256'] and item['raw_sha256']
        assert 'bars' not in item and 'missing_dates' not in item['coverage']
    assert len(json.dumps(result)) < 10000
    assert events[-1][3]['completed_count'] == events[-1][3]['total_count'] == 2
    assert events[-1][3]['request_count'] == 2


def test_batch_manifest_is_small_read_only_and_matches_frozen_generation(local, monkeypatch):
    market, adapter, published = local
    assert batch.read_sector_chart_batch_report(market, DAY) is None
    result = run(local)
    calls = len(adapter.calls)
    def not_a_full_chart_read(*args): raise AssertionError('status must not read every full chart')
    monkeypatch.setattr(charts, '_read_cached', not_a_full_chart_read)
    report = batch.read_sector_chart_batch_report(market, DAY)
    assert report['coverage'] == result['coverage'] and report['items'] == result['items']
    assert report['artifact'] == result['artifact'] and report['sha256'] == result['sha256']
    assert len(adapter.calls) == calls
    assert (published/'market-supplement/accepted/fuyao/sector-chart-batch'/(DAY+'.json')).is_file()
    market.state['snapshot_id'] = 'market-next'
    assert batch.read_sector_chart_batch_report(market, DAY) is None


@pytest.mark.parametrize('part', ['raw', 'normalized'])
def test_manifest_corrupt_sha_remains_unknown(local, part):
    report = run(local)
    relative = report['artifact'] if part == 'normalized' else report['raw_artifact']
    (local[2]/'market-supplement'/relative).write_text('{}')
    with pytest.raises(ValueError, match='SHA'): batch.read_sector_chart_batch_report(local[0], DAY)


def test_manifest_normalized_counts_cannot_diverge_from_raw_manifest_even_with_rehash(local):
    report = run(local)
    root = local[2]/'market-supplement'
    path = root/report['artifact']
    value = json.loads(path.read_text()); value['coverage']['available_count'] = 999
    path.write_text(json.dumps(value))
    pointer = root/'accepted/fuyao/sector-chart-batch'/(DAY+'.json')
    saved = json.loads(pointer.read_text()); saved['sha256'] = sha256(path.read_bytes()).hexdigest()
    pointer.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match='证明不符'): batch.read_sector_chart_batch_report(local[0], DAY)


def test_existing_backup_inventory_covers_batch_manifest_and_all_board_raw_proofs(local):
    report = run(local)
    script = Path(__file__).resolve().parents[2]/'scripts/backup.py'
    spec = importlib.util.spec_from_file_location('sector_batch_backup_fixture', script)
    backup = importlib.util.module_from_spec(spec); spec.loader.exec_module(backup)
    inventory = backup.supplement_files(local[2])
    assert inventory['market-supplement/'+report['artifact']] == report['sha256']
    assert inventory['market-supplement/'+report['raw_artifact']] == report['raw_sha256']
    for item in report['items']:
        assert inventory['market-supplement/'+item['artifact']] == item['sha256']
        assert inventory['market-supplement/'+item['raw_artifact']] == item['raw_sha256']


def test_same_target_rerun_revalidates_cache_and_requests_nothing(local):
    first = run(local)
    class Offline:
        def request(self, *args): raise AssertionError('must reuse both existing raw caches')
    second = run(local, Offline())
    assert second['coverage']['request_count'] == 0
    assert second['coverage']['cache_hit_count'] == 2
    assert [row['fetched_at'] for row in second['items']] == [row['fetched_at'] for row in first['items']]
    assert [row['raw_sha256'] for row in second['items']] == [row['raw_sha256'] for row in first['items']]


def test_rerun_only_remotely_retries_failed_board(local):
    class Failing(Adapter):
        def request(self, path, params):
            if params['thscode'] == INDUSTRY:
                self.calls.append((path, dict(params)))
                raise ProviderError('controlled transient connection failure')
            return super().request(path, params)
    first = run(local, Failing(local[0]))
    assert first['coverage']['failed_count'] == 1 and first['coverage']['available_count'] == 1
    assert first['quality'] == 'partial'
    assert first['failures'][0]['requested'] is True
    second = run(local)
    assert second['coverage']['available_count'] == 2 and second['coverage']['cache_hit_count'] == 1
    assert second['coverage']['request_count'] == 1 and len(local[1].calls) == 1
    assert local[1].calls[0][1]['thscode'] == INDUSTRY


@pytest.mark.parametrize('status', [401, 403, 429])
def test_source_refusal_stops_remote_requests_but_still_reuses_later_cache(local, status):
    market, adapter, _ = local
    charts.collect_sector_chart(market, charts.plan_sector_chart(market, CODE, DAY), adapter)
    market.ref['concept_catalog'].append({'thscode': '886001.TI', 'name': '未请求概念'})
    class Refused(Adapter):
        def request(self, path, params):
            self.calls.append((path, dict(params)))
            raise ProviderError('controlled refusal', status=status)
    refused = Refused(market)
    result = run(local, refused)
    assert result['coverage']['request_count'] == len(refused.calls) == 1
    assert result['coverage']['cache_hit_count'] == result['coverage']['available_count'] == 1
    assert result['coverage']['failed_count'] == result['coverage']['unrequested_count'] == 1
    assert [row['topic_code'] for row in result['items']] == [CODE]
    assert result['failures'][-1]['topic_code'] == '886001.TI'
    assert result['failures'][-1]['stage'] == 'source_blocked' and not result['failures'][-1]['requested']


def test_auth_business_code_also_stops_remaining_requests(local):
    class Refused:
        def request(self, *args): raise ProviderError('controlled invalid credential', code=2001)
    result = run(local, Refused())
    assert result['coverage']['request_count'] == result['coverage']['failed_count'] == 1
    assert result['coverage']['unrequested_count'] == 1


@pytest.mark.parametrize('exception', [TaskPaused, TaskCancelled, ValueError])
def test_guard_interruption_is_not_swallowed_as_per_board_failure(local, exception):
    def guard():
        if local[1].calls: raise exception('controlled interrupted job')
    with pytest.raises(exception): run(local, guard=guard)
    assert len(local[1].calls) == 1
    assert not (local[2]/'market-supplement/accepted').exists()


def test_changed_snapshot_or_catalog_rejects_whole_frozen_plan_before_network(local):
    plan = batch.plan_sector_chart_batch(local[0], DAY)
    local[0].state['snapshot_id'] = 'market-next'
    with pytest.raises(ValueError, match='代际'): batch.collect_sector_chart_batch(local[0], plan, local[1])
    local[0].state['snapshot_id'] = 'market-fixture'
    local[0].ref['industry_catalog'][0]['name'] = 'changed identity'
    with pytest.raises(ValueError, match='冻结目录'): batch.collect_sector_chart_batch(local[0], plan, local[1])
    assert local[1].calls == []


def test_limits_reject_empty_or_over_800_catalog_before_any_request(local):
    market, adapter, _ = local
    market.ref = {}
    with pytest.raises(ValueError, match='目录为空'): batch.plan_sector_chart_batch(market, DAY)
    market.ref = {'concept_catalog': [{'thscode': f'{880000+i}.TI', 'name': f'板块{i}'} for i in range(801)]}
    with pytest.raises(ValueError, match='800'): batch.plan_sector_chart_batch(market, DAY)
    assert adapter.calls == []


def test_conflicting_catalog_identity_is_reported_not_silently_complete(local):
    local[0].ref['concept_catalog'].append({'thscode': INDUSTRY, 'name': 'conflicting concept'})
    result = run(local)
    assert result['coverage']['available_count'] == 1 and result['quality'] == 'partial'
    assert result['coverage']['catalog_failure_count'] == 1
    assert result['failures'][0]['stage'] == 'catalog'


def test_rate_limit_applies_to_remote_misses_only(local, monkeypatch):
    delays = []
    monkeypatch.setattr(batch.time, 'monotonic', lambda: 100)
    monkeypatch.setattr(batch.time, 'sleep', delays.append)
    assert run(local)['coverage']['request_count'] == 2
    assert delays == [1.2]
    delays.clear()
    assert run(local)['coverage']['request_count'] == 0 and delays == []


def test_corrupt_raw_is_unknown_and_not_overwritten_while_other_board_survives(local):
    first = run(local)
    row = next(row for row in first['items'] if row['topic_code'] == INDUSTRY)
    (local[2]/'market-supplement'/row['raw_artifact']).write_text('{}')
    before = len(local[1].calls)
    second = run(local)
    assert len(local[1].calls) == before
    assert second['coverage']['available_count'] == second['coverage']['cache_hit_count'] == 1
    assert second['coverage']['failed_count'] == 1 and not second['failures'][0]['requested']


def test_rolling_leading_gap_is_partial_even_when_all_boards_are_available(local, monkeypatch):
    market = Market()
    monkeypatch.setattr(charts, '_source_today', lambda: '2026-10-21')
    result = run((market, Adapter(market), local[2]))
    assert result['coverage']['collection_complete'] and result['coverage']['available_count'] == 2
    assert result['coverage']['partial_count'] == 2 and not result['complete']
    assert all(row['coverage']['missing_count'] == 1 for row in result['items'])


def test_full_charts_prove_both_daily_pairs_without_710_duplicate_short_requests(local):
    market, adapter, published = local
    run(local)
    count = len(adapter.calls)
    class Offline:
        def request(self, *args): raise AssertionError('full histories already prove exact T/T-1')
    report = sector.collect_sector_history(market, DAY, SupplementCache(published/'market-supplement'), Offline())
    assert report['complete'] and len(report['items']) == 2
    assert report['coverage']['request_count'] == 0
    assert report['coverage']['full_chart_cached_count'] == report['coverage']['cached_count'] == 2
    assert len(adapter.calls) == count
    for row in report['items']:
        assert row['date'] == DAY and row['previous_date'] == '2026-10-19'
        assert row['full_chart_artifact'] and row['full_chart_sha256'] and row['raw_sha256']
        assert '精确T/T-1' in row['price_evidence_basis']
    view = sector.sector_prices_for_date({'target_date': DAY, 'sector_index_history': {DAY: report}}, DAY)
    assert len(view['items']) == 2


def test_full_chart_missing_previous_cannot_prove_daily_pair(local):
    market, adapter, _ = local
    adapter.transform = lambda value: {**value, 'data': {**value['data'],
        'item': [row for row in value['data']['item'] if row['date_ms'] != charts._midnight('2026-10-19')]}}
    run(local)
    topic = sector._catalog(market.reference())[0][CODE]
    assert sector.full_sector_pair_from_cache(market, topic, DAY, '2026-10-19') is None


def test_full_chart_cache_is_revalidated_not_trusted_for_pair(local):
    market, _, published = local
    first = run(local)
    industry = next(row for row in first['items'] if row['topic_code'] == INDUSTRY)
    (published/'market-supplement'/industry['raw_artifact']).write_text('{}')
    topic = sector._catalog(market.reference())[0][INDUSTRY]
    with pytest.raises(ValueError, match='SHA'): sector.full_sector_pair_from_cache(market, topic, DAY, '2026-10-19')


def test_full_pair_rejects_wrong_catalog_identity_even_with_matching_code(local):
    market, _, _ = local
    run(local)
    topic = dict(sector._catalog(market.reference())[0][CODE], kind='industry')
    with pytest.raises(ValueError, match='目录身份'):
        sector.full_sector_pair_from_cache(market, topic, DAY, '2026-10-19')


def test_full_pair_never_uses_another_day_chart_or_members(local):
    market, _, _ = local
    run(local)
    topic = sector._catalog(market.reference())[0][CODE]
    assert sector.full_sector_pair_from_cache(market, topic, '2026-10-19', '2026-10-16') is None
    market.ref['topics'] = [{'topic_code': CODE, 'observed_date': '2099-01-01',
                             'instrument_ids': ['not-an-actual-member']}]
    assert sector.full_sector_pair_from_cache(market, topic, DAY, '2026-10-19')['date'] == DAY
