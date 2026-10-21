"""True tail requests and thin immutable lineage, all local synthetic fixtures."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from app.services import sector_charts as charts, sector_chart_batch as batch
from app.services import sector_history as sector
from app.services.task_service import TaskPaused
from test_sector_charts import Market, Adapter, CODE, DAY

NEXT = '2026-10-21'


@pytest.fixture
def local(tmp_path, monkeypatch):
    root = tmp_path/'published'
    monkeypatch.setattr(charts, 'published_dir', lambda: root)
    monkeypatch.setattr(batch.time, 'sleep', lambda seconds: None)
    market = Market()
    market.days += pd.bdate_range(NEXT, '2026-11-05').strftime('%Y-%m-%d').tolist()
    market.state['data_end'] = market.days[-1]
    clock = {'day': DAY}
    monkeypatch.setattr(charts, '_source_today', lambda: clock['day'])
    return market, Adapter(market), root/'market-supplement', clock


def plan(local, day=DAY): return charts.plan_sector_chart(local[0], CODE, day)


def initial(local, day=DAY):
    local[3]['day'] = day
    return charts.collect_sector_chart(local[0], plan(local, day), local[1])


def increment(local, day=NEXT, **kwargs):
    local[3]['day'] = day
    return charts.collect_sector_chart_incremental(local[0], plan(local, day), local[1], **kwargs)


def test_increment_only_requests_dates_after_last_cache_and_keeps_raw_proof(local):
    base = initial(local)
    result = increment(local)
    assert result['request_kind'] == 'tail' and result['downloaded_rows'] == 1
    assert local[1].calls[-1][1]['start'] == charts._midnight(NEXT)
    assert local[1].calls[-1][1]['end'] == charts._midnight(NEXT)+86400000-1
    assert result['reused_rows'] == len(base['record']['bars'])-1
    assert result['record']['bars'][-1]['date'] == NEXT
    assert result['record']['raw_lineage']['segments'] == 2
    raw = json.loads((local[2]/result['record']['raw_artifact']).read_text())
    assert raw['contract'] == charts.INCREMENT_CONTRACT
    assert raw['previous']['artifact'] == base['artifact'] and raw['previous']['sha256'] == base['sha256']
    thin = json.loads((local[2]/result['artifact']).read_text())
    assert thin['storage'] == 'thin-increment-v1' and 'bars' not in thin
    assert 'missing_dates' not in thin['coverage']
    assert (local[2]/base['artifact']).is_file()


def test_noop_increment_and_new_board_full_are_distinguished(local):
    first = increment(local, DAY)
    assert first['request_kind'] == 'full' and len(local[1].calls) == 1
    second = increment(local, DAY)
    assert second['cache_hit'] and second['request_kind'] == 'cache'
    assert second['downloaded_rows'] == 0 and len(local[1].calls) == 1


def test_missed_days_use_one_exact_tail_window(local):
    initial(local)
    result = increment(local, '2026-10-23')
    assert result['downloaded_rows'] == 3
    assert local[1].calls[-1][1]['start'] == charts._midnight(NEXT)
    assert result['record']['coverage']['missing_count'] == 0


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
def test_multiday_increment_equals_independent_full_download_dwm(local, monkeypatch, tmp_path, period):
    initial(local)
    for day in (NEXT, '2026-10-22', '2026-10-23', '2026-10-26'):
        increment(local, day)
    target = '2026-10-26'
    merged = charts.read_sector_chart(local[0], CODE, target, period)
    monkeypatch.setattr(charts, 'published_dir', lambda: tmp_path/'independent-full')
    charts.collect_sector_chart(local[0], plan(local, target), Adapter(local[0]))
    complete = charts.read_sector_chart(local[0], CODE, target, period)
    assert merged['bars'] == complete['bars']
    assert merged['waves'] == complete['waves']
    assert merged['raw_lineage']['segments'] == 5


def test_thin_daily_storage_does_not_duplicate_full_base(local):
    base = initial(local); base_size = (local[2]/base['artifact']).stat().st_size
    before = sum(path.stat().st_size for path in local[2].rglob('*.json'))
    for day in local[0].days[-12:]:
        if day <= DAY: continue
        result = increment(local, day)
        assert (local[2]/result['artifact']).stat().st_size < base_size/10
        assert json.loads((local[2]/result['artifact']).read_text()).get('bars') is None
    added = sum(path.stat().st_size for path in local[2].rglob('*.json'))-before
    assert added < base_size, 'twelve thin tails must be smaller than one repeated full base'


def test_missing_target_does_not_accept_tail_or_new_chart_and_keeps_old_cache(local):
    initial(local)
    local[1].transform = lambda value: {**value, 'data': {**value['data'], 'item': []}}
    with pytest.raises(ValueError, match='空列表'): increment(local)
    assert charts.read_sector_chart(local[0], CODE, DAY)['available']
    assert not (local[2]/'accepted/fuyao/sector-chart'/(NEXT+'-'+CODE+'.json')).exists()
    assert not (local[2]/'accepted/fuyao/sector-chart-tail').exists()


def test_old_internal_gaps_survive_without_full_redownload(local):
    missing = '2024-09-03'
    local[1].transform = lambda value: {**value, 'data': {**value['data'], 'item': [
        row for row in value['data']['item'] if row['date_ms'] != charts._midnight(missing)]}}
    initial(local)
    local[1].transform = None
    result = increment(local)
    assert result['coverage']['missing_dates'] == [missing]
    assert result['quality'] == 'partial' and result['request_kind'] == 'tail'
    assert local[1].calls[-1][1]['start'] == charts._midnight(NEXT)


def test_tail_missing_intermediate_day_is_unknown_and_never_filled(local):
    initial(local)
    local[1].transform = lambda value: {**value, 'data': {**value['data'], 'item': [
        row for row in value['data']['item'] if row['date_ms'] != charts._midnight(NEXT)]}}
    result = increment(local, '2026-10-22')
    assert result['coverage']['missing_dates'] == [NEXT] and result['quality'] == 'partial'
    assert charts.read_sector_chart(local[0], CODE, '2026-10-22')['bars'][-1]['macd_dif'] is None


def test_tail_provider_cannot_overwrite_previous_day(local):
    initial(local)
    def overlap(value):
        value['data']['item'][0]['date_ms'] = charts._midnight(DAY)
        return value
    local[1].transform = overlap
    with pytest.raises(ValueError, match='窗口外'): increment(local)


def test_pause_after_network_does_not_publish_new_chart(local):
    initial(local); old_calls = len(local[1].calls)
    def guard():
        if len(local[1].calls) > old_calls: raise TaskPaused()
    with pytest.raises(TaskPaused): increment(local, guard=guard)
    assert not (local[2]/'accepted/fuyao/sector-chart'/(NEXT+'-'+CODE+'.json')).exists()


def test_resume_reuses_valid_tail_after_pause_before_merge_accept(local):
    initial(local)
    def guard():
        if (local[2]/'accepted/fuyao/sector-chart-tail').exists(): raise TaskPaused()
    with pytest.raises(TaskPaused): increment(local, guard=guard)
    requests = len(local[1].calls)
    result = increment(local)
    assert result['cache_hit'] and result['downloaded_rows'] == 0
    assert len(local[1].calls) == requests and result['record']['bars'][-1]['date'] == NEXT


@pytest.mark.parametrize('part', ['base_raw', 'tail_raw', 'previous_metadata'])
def test_read_revalidates_all_referenced_proofs(local, part):
    base = initial(local); result = increment(local)
    receipt = json.loads((local[2]/result['record']['raw_artifact']).read_text())
    if part == 'base_raw': relative = base['record']['raw_artifact']
    elif part == 'previous_metadata': relative = receipt['previous']['artifact']
    else:
        tail = json.loads((local[2]/receipt['tail']['artifact']).read_text())
        relative = tail['raw_artifact']
    (local[2]/relative).write_text('{}')
    assert not charts.read_sector_chart(local[0], CODE, NEXT)['available']


def test_corrupt_old_cache_is_not_silently_replaced_by_full_request(local):
    base = initial(local)
    (local[2]/base['record']['raw_artifact']).write_text('{}')
    count = len(local[1].calls)
    with pytest.raises(ValueError, match='未静默'): increment(local)
    assert len(local[1].calls) == count


def test_lineage_bound_is_explicit_not_python_recursion(local, monkeypatch):
    initial(local); increment(local)
    monkeypatch.setattr(charts, 'MAX_LINEAGE_SEGMENTS', 1)
    assert not charts.read_sector_chart(local[0], CODE, NEXT)['available']


def test_merged_history_projection_excludes_future_tail_prices(local):
    initial(local); increment(local)
    before = charts.read_sector_chart(local[0], CODE, NEXT)['bars']
    def spike(value):
        value['data']['item'][-1].update(open_price=9999, high_price=10000, low_price=9998, close_price=9999)
        return value
    local[1].transform = spike
    increment(local, '2026-10-22')
    exact = local[2]/'accepted/fuyao/sector-chart'/(NEXT+'-'+CODE+'.json')
    exact.unlink()  # immutable referenced normalized/raw proofs remain intact
    projected = charts.read_sector_chart(local[0], CODE, NEXT)
    assert projected['available'] and projected['bars'] == before
    assert projected['cache_as_of'] == '2026-10-22' and projected['raw_lineage']['segments'] == 3


def test_daily_pair_reuse_reads_merged_raw_chain(local):
    initial(local); increment(local)
    topic = sector._catalog(local[0].reference())[0][CODE]
    row = sector.full_sector_pair_from_cache(local[0], topic, NEXT, DAY)
    assert row['date'] == NEXT and row['previous_date'] == DAY
    assert row['full_chart_artifact'] and row['raw_sha256']


def test_increment_batch_uses_tail_counts_and_keeps_initial_once_manifest(local):
    market, adapter, root, clock = local
    old_plan = batch.plan_sector_chart_batch(market, DAY)
    assert 'mode' not in old_plan
    assert old_plan == batch.plan_sector_chart_batch(market, DAY, mode='initial')
    initial_report = batch.collect_sector_chart_batch(market, old_plan, adapter)
    initial_pointer = root/'accepted/fuyao/sector-chart-batch'/(DAY+'.json')
    old_bytes = initial_pointer.read_bytes()
    # A same-day noop increment has its own namespace and cannot erase the
    # cross-generation initial-once evidence.
    noop_plan = batch.plan_sector_chart_batch(market, DAY, mode='increment')
    assert noop_plan['request_signature'] != old_plan['request_signature']
    noop = batch.collect_sector_chart_batch(market, noop_plan, adapter)
    assert noop['coverage']['cache_hit_count'] == 2 and noop['coverage']['request_count'] == 0
    assert initial_pointer.read_bytes() == old_bytes
    assert batch.read_frozen_sector_chart_batch_report(old_plan)['sha256'] == initial_report['sha256']
    assert batch.read_sector_chart_batch_report(market, DAY, mode='increment')['sha256'] == noop['sha256']
    clock['day'] = NEXT
    tail_plan = batch.plan_sector_chart_batch(market, NEXT, mode='increment')
    result = batch.collect_sector_chart_batch(market, tail_plan, adapter)
    assert result['coverage']['tail_request_count'] == result['coverage']['request_count'] == 2
    assert result['coverage']['full_request_count'] == 0
    assert result['coverage']['downloaded_rows'] == 2
    assert result['coverage']['reused_rows'] > 2000
    assert all(item['request_kind'] == 'tail' for item in result['items'])
    assert initial_pointer.read_bytes() == old_bytes
    market.state['snapshot_id'] = 'new-generation'
    assert batch.read_sector_chart_batch_report(market, DAY) is None
    assert batch.read_frozen_sector_chart_batch_report(old_plan)['sha256'] == initial_report['sha256']


def test_increment_batch_new_catalog_code_uses_full_once_only_for_new_code(local):
    market, adapter, _, clock = local
    batch.collect_sector_chart_batch(market, batch.plan_sector_chart_batch(market, DAY), adapter)
    market.ref['concept_catalog'].append({'thscode': '885999.TI', 'name': '新增概念'})
    clock['day'] = NEXT
    result = batch.collect_sector_chart_batch(market, batch.plan_sector_chart_batch(market, NEXT, 'increment'), adapter)
    assert result['coverage']['tail_request_count'] == 2
    assert result['coverage']['full_request_count'] == 1
    assert result['coverage']['request_count'] == 3
    added = next(item for item in result['items'] if item['topic_code'] == '885999.TI')
    assert added['request_kind'] == 'full' and added['reused_rows'] == 0


def test_frozen_manifest_reader_rejects_wrong_scope_without_current_market(local):
    market, adapter, _, _ = local
    frozen = batch.plan_sector_chart_batch(market, DAY)
    batch.collect_sector_chart_batch(market, frozen, adapter)
    with pytest.raises(ValueError, match='证明不符'):
        batch.read_frozen_sector_chart_batch_report({**frozen, 'snapshot_id': 'wrong-generation'})
    assert batch.read_frozen_sector_chart_batch_report({**frozen, 'request_signature': '0'*64}) is None


def test_legacy_initial_manifest_without_mode_is_still_readable(local):
    market, adapter, root, _ = local
    frozen = batch.plan_sector_chart_batch(market, DAY)
    report = batch.collect_sector_chart_batch(market, frozen, adapter)
    # Rebuild the old-format receipt in the isolated fixture. Its frozen plan
    # and signature are not recomputed or reinterpreted as a new-mode task.
    from app.services.market_supplement import SupplementCache
    old = {key: value for key, value in report.items() if key not in
           ('mode','fetched_at','raw_artifact','raw_sha256','validation_version','artifact','sha256')}
    SupplementCache(root).accept('fuyao','sector-chart-batch',DAY,frozen['request_signature'],old,
                                {'contract': batch.NORMALIZATION_VERSION, 'report': old})
    assert batch.read_frozen_sector_chart_batch_report(frozen)['coverage']['available_count'] == 2


def test_backup_inventory_includes_thin_chain_tail_and_full_base(local):
    base = initial(local); result = increment(local)
    path = Path(__file__).resolve().parents[2]/'scripts/backup.py'
    spec = importlib.util.spec_from_file_location('increment_backup_fixture', path)
    backup = importlib.util.module_from_spec(spec); spec.loader.exec_module(backup)
    files = backup.supplement_files(local[2].parent)
    assert files['market-supplement/'+base['artifact']] == base['sha256']
    assert files['market-supplement/'+result['artifact']] == result['sha256']
    assert any('/sector-chart-tail/' in name for name in files)
