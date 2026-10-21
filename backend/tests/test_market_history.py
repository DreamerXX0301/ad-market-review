"""Dated retained references must not turn current membership into history."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app import core
from app.services import market_history as history
from app.services import market_service

A = '600001.SH'
B = '600002.SH'


def topic(day, symbol=A, name='存档行业'):
    return {'topic_id': 'fuyao-881001.TI', 'topic_code': '881001.TI', 'name': name,
            'kind': 'industry', 'observed_date': day, 'members_complete': True,
            'instrument_ids': [symbol]}


def reference(day, symbol=A, *, observed=None):
    return {'topics': [topic(observed or day, symbol)], 'concept_catalog': [],
            'industry_catalog': [{'thscode': '881001.TI'}],
            'sectors_snapshot': [{'thscode': '881001.TI', 'price_change_ratio_pct': 999, 'turnover': 999}],
            'sectors_snapshot_date_semantics': 'current_unverified_trade_date',
            'abnormal': [{'thscode': symbol, 'name': '合成测试', 'monitor_label': '来源标签'}],
            'abnormal_observed_date': day}


@pytest.fixture
def archives(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR'):
        monkeypatch.delenv(key, raising=False)
    core.ensure_ready()
    history.clear_history_cache()
    now = core.now_ms()
    with core.connect() as connection:
        connection.execute("INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,parameters_hash,status,created_at_ms) VALUES('history-test-job','history-test-request','history-test-key','initialise','{}',?,'succeeded',?)", ('a' * 64, now))
    counter = [0]

    def save(generation, day, value):
        counter[0] += 1
        path = tmp_path / 'published' / generation / 'reference.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False))
        identifier = 'topics-' + generation
        core.publish_snapshot(module='topics', job_id='history-test-job', start=day, end=day,
                              artifacts=[{'object_key': str(path.relative_to(tmp_path / 'published')),
                                          'sha256': core.hash_file(path), 'byte_count': path.stat().st_size,
                                          'role': 'reference.json', 'row_count': 1}],
                              lineage={'market_snapshot_id': generation}, snapshot_id=identifier,
                              activate=False)
        return path, identifier
    yield tmp_path, save
    history.clear_history_cache()


class CurrentMarket:
    def calendar(self):
        return ['2026-10-16', '2026-10-19', '2026-10-20']
    def history_panel(self, *args, **kwargs):
        raise AssertionError('archive lookup must not load old/all-stock histories')
    def bars(self, *args, **kwargs):
        raise AssertionError('archive lookup must not read individual histories')


def install_panel(monkeypatch, current, *, snapshot='market-current', selected='2026-10-20'):
    market = CurrentMarket()
    status = {'ready': True, 'snapshot_id': snapshot, 'data_begin': '2023-10-20',
              'data_end': '2026-10-20', 'quality': 'partial'}
    calls = []
    def panel(day=None):
        day = day or selected
        calls.append(day)
        rows = [{'instrument_id': A, 'name': '合成甲', 'change_pct': 5, 'amount': 100, 'limit_up': None, 'date': day},
                {'instrument_id': B, 'name': '合成乙', 'change_pct': -4, 'amount': 200, 'limit_up': False, 'date': day}]
        return market, status, day, current, rows
    monkeypatch.setattr(market_service, 'panel', panel)
    return market, status, calls


def test_older_generation_members_use_requested_day_quotes_and_local_source_only(archives, monkeypatch):
    _, save = archives
    save('market-old', '2026-10-19', reference('2026-10-19'))
    current = reference('2026-10-20', B)
    current['_supplement'] = {'annual_pools': {'2026-01-05': {'items': []}}}
    save('market-current', '2026-10-20', current)
    before = deepcopy(current)
    _, _, calls = install_panel(monkeypatch, current)
    result = market_service.market_page('sectors', '2026-10-19')
    assert calls == ['2026-10-19']
    assert result['items'][0]['change_pct'] == 5 and result['items'][0]['amount'] == 100
    assert result['items'][0]['calculation'] == '存档成员样本等权涨幅'
    assert result['items'][0]['provider_observed_change_pct'] is None
    assert result['items'][0]['limit_up_count'] is None
    assert result['items'][0]['source_snapshot_id'] == 'market-old'
    assert result['data_coverage']['available_dates'] == ['2026-10-19', '2026-10-20']
    assert result['data_coverage']['start'] != '2023-10-20'
    assert current == before  # Neither annual inputs nor the global ref are replaced.


def test_exact_verified_historical_index_snapshot_can_supply_real_index_change(archives, monkeypatch):
    _, save = archives
    old = reference('2026-10-19')
    old.update(sectors_snapshot_date_semantics='verified_trade_date', sectors_snapshot_trade_date='2026-10-19')
    old['sectors_snapshot'][0].update(price_change_ratio_pct=-2.5, turnover=300)
    save('market-old', '2026-10-19', old)
    current = reference('2026-10-20', B)
    install_panel(monkeypatch, current)
    row = market_service.market_page('sectors', '2026-10-19')['items'][0]
    assert row['change_pct'] == -2.5 and row['amount'] == 300
    assert row['calculation'] == '来源板块指数'


def test_abnormal_reads_exact_saved_observed_day_even_when_current_pool_is_newer(archives, monkeypatch):
    _, save = archives
    save('market-old', '2026-10-19', reference('2026-10-19'))
    current = {**reference('2026-10-20', B), 'pool_trade_date': '2026-10-20'}
    save('market-current', '2026-10-20', current)
    install_panel(monkeypatch, current)
    result = market_service.market_page('abnormal', '2026-10-19')
    assert result['items'][0]['instrument_id'] == A
    assert result['items'][0]['change_pct'] == 5
    assert result['items'][0]['observed_date'] == '2026-10-19'
    assert result['items'][0]['source_snapshot_id'] == 'market-old'
    assert result['data_coverage']['selected_date_available']


@pytest.mark.parametrize('kind', ['sectors', 'abnormal'])
def test_missing_old_artifact_cannot_be_replaced_by_current_members_or_labels(archives, monkeypatch, kind):
    _, save = archives
    path, _ = save('market-old', '2026-10-19', reference('2026-10-19'))
    current = reference('2026-10-20', B)
    save('market-current', '2026-10-20', current)
    install_panel(monkeypatch, current)
    assert market_service.market_page(kind, '2026-10-19')['items']
    path.unlink()  # Same service process must invalidate its warm archive cache.
    result = market_service.market_page(kind, '2026-10-19')
    assert result['items'] == [] and result['unavailable_reason']
    assert not result['data_coverage']['selected_date_available']
    assert result['data_coverage']['available_dates'] == ['2026-10-20']


def test_future_members_and_missing_member_dates_remain_unknown(archives, monkeypatch):
    _, save = archives
    current = reference('2026-10-20', B, observed='2026-10-21')
    current['topics'].append({**topic('2026-10-20'), 'observed_date': None})
    save('market-current', '2026-10-20', current)
    install_panel(monkeypatch, current)
    result = market_service.market_page('sectors', '2026-10-20')
    assert not result['items'] and result['data_coverage']['available_dates'] == []
    assert result['data_coverage']['start'] is result['data_coverage']['end'] is None


def test_increment_snapshot_can_use_older_known_members_without_claiming_fresh_collection(archives, monkeypatch):
    _, save = archives
    current = reference('2026-10-20', A, observed='2026-10-19')
    save('market-current', '2026-10-20', current)
    install_panel(monkeypatch, current)
    result = market_service.market_page('sectors', '2026-10-20')
    assert result['items'][0]['membership_observed_date'] == '2026-10-19'
    assert result['items'][0]['membership_snapshot_day'] == '2026-10-20'
    assert result['data_coverage']['selected_date_available']
    assert '日增量沿用成员' in result['data_coverage']['notice']
    assert '完整成员需手动刷新' in result['data_coverage']['notice']


def test_no_snapshot_between_saved_dates_does_not_become_a_continuous_range(archives, monkeypatch):
    _, save = archives
    save('market-old', '2026-10-16', reference('2026-10-16'))
    current = reference('2026-10-20', B)
    save('market-current', '2026-10-20', current)
    install_panel(monkeypatch, current)
    result = market_service.market_page('sectors', '2026-10-19')
    assert result['items'] == [] and not result['data_coverage']['selected_date_available']
    assert result['data_coverage']['available_dates'] == ['2026-10-16', '2026-10-20']


def test_exact_asof_archive_is_preferred_to_a_later_copy_of_same_observed_members(archives, monkeypatch):
    _, save = archives
    save('market-old', '2026-10-19', reference('2026-10-19', A, observed='2026-10-16'))
    current = reference('2026-10-20', B, observed='2026-10-19')
    save('market-current', '2026-10-20', current)
    install_panel(monkeypatch, current)
    row = market_service.market_page('sectors', '2026-10-19')['items'][0]
    assert row['source_snapshot_id'] == 'market-old' and row['change_pct'] == 5


def test_latest_abnormal_requires_matching_observation_date_not_pool_date(archives, monkeypatch):
    _, save = archives
    current = reference('2026-10-20', B)
    current.update(abnormal_observed_date='2026-10-19', pool_trade_date='2026-10-20')
    save('market-current', '2026-10-20', current)
    install_panel(monkeypatch, current)
    result = market_service.market_page('abnormal', '2026-10-20')
    assert not result['items'] and not result['data_coverage']['selected_date_available']
    prior = market_service.market_page('abnormal', '2026-10-19')
    assert prior['items'][0]['instrument_id'] == B


def test_empty_dated_abnormal_list_is_known_empty_not_an_undated_current_fallback(archives, monkeypatch):
    _, save = archives
    current = {**reference('2026-10-20'), 'abnormal': []}
    save('market-current', '2026-10-20', current)
    install_panel(monkeypatch, current)
    result = market_service.market_page('abnormal', '2026-10-20')
    assert result['items'] == [] and result['data_coverage']['selected_date_available']
    assert 'unavailable_reason' not in result


def test_archive_cache_is_bounded_copied_and_invalidates_changed_or_gc_files(archives):
    _, save = archives
    paths = []
    for index in range(8):
        generation = 'market-cache-' + str(index)
        paths.append(save(generation, '2026-10-19', reference('2026-10-19')))
    status = {'snapshot_id': 'market-current', 'data_end': '2026-10-20'}
    first, _ = history.resolve_reference('sectors', '2026-10-19', status, {})
    first['topics'][0]['instrument_ids'].append(B)
    again, _ = history.resolve_reference('sectors', '2026-10-19', status, {})
    assert again['topics'][0]['instrument_ids'] == [A]
    assert len(history._references) <= history.REFERENCE_CACHE_LIMIT
    assert len(history._indexes) <= history.INDEX_CACHE_LIMIT
    # Corruption must not be hidden by a warm extracted-reference cache.
    for path, _ in paths:
        path.write_text(path.read_text().replace('600001.SH', '600002.SH'))
    lost, coverage = history.resolve_reference('sectors', '2026-10-19', status, {})
    assert lost == {} and not coverage['selected_date_available']
    assert not coverage['available_dates']
    path, identifier = save('market-new', '2026-10-19', reference('2026-10-19'))
    assert history.resolve_reference('sectors', '2026-10-19', status, {})[0]
    with core.connect() as connection:
        connection.execute("INSERT INTO snapshot_gc(snapshot_id,state,changed_at_ms,reason) VALUES(?,'purged',?,'fixture')", (identifier, core.now_ms()))
    assert history.resolve_reference('sectors', '2026-10-19', status, {})[0] == {}
    assert len(history._indexes) <= history.INDEX_CACHE_LIMIT


def test_per_feature_coverage_uses_real_source_dates_instead_of_global_price_range():
    status = {'data_begin': '2023-10-20', 'data_end': '2026-10-20'}
    ref = {'pool_history': {}, '_supplement': {
        'annual_pools': {'2026-01-05': {'items': []}, '2026-10-20': {'items': [{}]}},
        'public_pools': {'2026-10-20': {'items': [{}]}},
        'fund_flows': {'2026-10-20': {'items': [{'date': '2026-10-20'}],
                                    'history': [{'date': '2026-10-19'}, {'date': '2026-10-20'}]}}}}
    for kind in ('overview', 'emotion', 'gainers', 'ladder', 'flow'):
        coverage = history.data_coverage(kind, status, '2026-10-20', ref, ['2026-10-19', '2026-10-20'],
                                         {'coverage': {'accepted_count': 14, 'directory_count': 128}})
        assert all(key in coverage for key in ('label', 'start', 'end', 'basis', 'notice'))
        assert coverage['selected_date_available']
        if kind == 'ladder':
            assert coverage['start'] == '2026-01-05'
            assert coverage['detail_available_dates'] == ['2026-10-20']
        elif kind == 'flow':
            assert coverage['start'] == '2026-10-19' and coverage['partial']
            assert '14个行业' in coverage['notice'] and '128个' in coverage['notice']
        else:
            assert coverage['start'] == '2023-10-20'
    unavailable = history.data_coverage('flow', status, '2026-01-05', ref, [])
    assert not unavailable['selected_date_available']


def test_price_coverage_without_calendar_capability_stays_unknown_not_inferred_from_requested_date(monkeypatch):
    status = {'snapshot_id': 'minimal-fixture', 'data_begin': '2026-10-19', 'data_end': '2026-10-20'}
    row = {'instrument_id': A, 'change_30d_pct': 5}
    monkeypatch.setattr(market_service, 'panel', lambda day: (object(), status, day, {}, [row]))
    monkeypatch.setattr(market_service, 'cached_gainers', lambda *args: ([row], {}))
    result = market_service.market_page('gainers', '2026-10-20')
    assert result['items'] == [row]
    assert not result['data_coverage']['selected_date_available']
    assert '日期覆盖待核验' in result['data_coverage']['notice']
