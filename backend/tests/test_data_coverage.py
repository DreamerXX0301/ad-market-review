"""Coverage denominators describe their real dimensions and reject fake totals."""
import json

from app.services.data_coverage import dataset_progress, measure, snapshot_quality_view
from test_platform import isolated


def row(module, observed=100, expected=None, coverage=None, generation='market-fixture'):
    return {'module': module, 'observed_rows': observed, 'expected_rows': expected,
            'data_begin': '2026-01-05', 'data_end': '2026-01-06',
            'lineage_json': json.dumps({'market_snapshot_id': generation, 'coverage': coverage or {}})}


def test_record_counts_do_not_create_a_denominator():
    for module in ('daily_raw', 'analysis_bars', 'corporate_actions'):
        progress = dataset_progress(row(module, 6_000_000))
        assert progress['observed_count'] == 6_000_000
        assert progress['expected_count'] is None and progress['percent'] is None
        assert progress['reason']
    indicators = dataset_progress(row('indicators', 6_000_000, 6_000_000))
    assert indicators['observed_count'] is None and indicators['percent'] is None
    assert indicators['dimension'] == 'indicator_readiness'


def test_listing_identity_is_a_field_completion_ratio_not_market_history_coverage():
    progress = dataset_progress(row('security_identity', 100, coverage={'identity_unknown_count': 20}))
    assert progress['observed_count'] == 80 and progress['expected_count'] == 100
    assert progress['percent'] == 80 and progress['dimension'] == 'listing_dates'
    raw = dataset_progress(row('daily_raw', 1000, 1100, {'identity_unknown_count': 20}))
    assert raw['expected_count'] is None and raw['percent'] is None
    assert '20' in raw['reason']


def test_known_snapshot_expected_rows_can_have_partial_percentage():
    progress = dataset_progress(row('daily_raw', 95, 100, {'identity_unknown_count': 0}))
    assert progress['percent'] == 95 and progress['unit'] == '条'
    for observed, expected in ((101, 100), (0, 0), (True, 1), (5, None)):
        assert measure('test', observed, expected, '条', 'records', 'fixture')['percent'] is None


def test_calendar_requires_its_actual_frozen_window_receipt():
    provenance = {'basis': 'source_derived_cross_checked', 'count': 1, 'end': '2026-01-05',
                  'retained_calendar_count': 2, 'recent_extension': {'end': '2026-01-06'}}
    progress = dataset_progress(row('calendar', 2, coverage={'calendar_provenance': provenance}))
    assert progress['percent'] == 100 and progress['dimension'] == 'trading_days'
    provenance['recent_extension']['end'] = '2026-01-07'
    assert dataset_progress(row('calendar', 2, coverage={'calendar_provenance': provenance}))['percent'] is None
    assert dataset_progress(row('calendar', 2))['percent'] is None


def test_topic_groups_count_complete_groups_against_same_generation_catalog():
    reference = {'concept_catalog': [{'thscode': '885001.TI'}, {'thscode': '885002.TI'}],
                 'industry_catalog': [{'thscode': '885001.TI'}, {'thscode': '881001.TI'}],
                 'topics': [{'topic_code': '885001.TI', 'members_complete': True},
                            {'topic_code': '885002.TI', 'members_complete': False},
                            {'topic_code': '881001.TI', 'members_complete': True},
                            {'topic_code': 'outside.TI', 'members_complete': True}]}
    progress = dataset_progress(row('topics', 999999), reference, 'market-fixture')
    assert progress['observed_count'] == 2 and progress['expected_count'] == 3
    assert abs(progress['percent'] - 66.6667) < 0.001
    assert dataset_progress(row('topics'), reference, 'different-generation')['percent'] is None


def test_market_event_percentage_excludes_unproven_pools_and_wrong_dates():
    reference = {'pool_trade_date': '2026-01-06', 'pool_coverage': {
        'limitup': {'observed_rows': 10, 'declared_total': 10, 'complete': True},
        'limitdown': {'observed_rows': 1, 'declared_total': 1, 'complete': True},
        'breakpool': {'observed_rows': 3, 'declared_total': 3, 'complete': True},
    }}
    progress = dataset_progress(row('market_review', 500), reference, 'market-fixture')
    assert progress['observed_count'] == 14 and progress['expected_count'] == 14
    assert progress['percent'] == 100
    assert '异动' in progress['basis']
    reference['pool_coverage']['limitup']['complete'] = False
    assert dataset_progress(row('market_review'), reference, 'market-fixture')['percent'] is None
    reference['pool_coverage']['limitup']['complete'] = True
    reference['pool_trade_date'] = '2026-01-05'
    assert dataset_progress(row('market_review'), reference, 'market-fixture')['percent'] is None


def test_derived_input_reuses_raw_identity_gap_guard_without_inventing_indicator_readiness():
    progress = dataset_progress(row('analysis_bars', 100, 100), raw_coverage={'identity_unknown_count': 4})
    assert progress['percent'] is None and progress['expected_count'] is None


def test_measure_status_describes_current_dimension_not_snapshot_quality():
    complete = measure('成员组', 710, 710, '组', 'topic_groups', '当前目录')
    assert complete['coverage_status'] == 'covered'
    assert complete['coverage_label'] == '当前范围已覆盖'
    assert complete['scope_complete'] is True
    partial = measure('成员组', 709, 710, '组', 'topic_groups', '当前目录')
    assert partial['coverage_status'] == 'partial' and partial['scope_complete'] is False
    assert partial['coverage_label'] == '当前范围有缺口'
    empty = measure('成员组', 0, 710, '组', 'topic_groups', '当前目录')
    assert empty['coverage_status'] == 'partial' and empty['percent'] == 0
    for observed, expected in ((710, None), (711, 710), (0, 0), (None, 710)):
        unknown = measure('成员组', observed, expected, '组', 'topic_groups', 'fixture')
        assert unknown['coverage_status'] == 'unknown'
        assert unknown['scope_complete'] is None and unknown['percent'] is None


def test_small_remaining_gaps_do_not_round_to_complete():
    progress = measure('记录', 9_999_999, 10_000_000, '条', 'records', 'fixture')
    assert progress['percent'] < 100
    assert progress['coverage_status'] == 'partial'


def test_snapshot_publication_quality_is_immutable_and_separate():
    source = {**row('topics'), 'quality': 'partial'}
    original = dict(source)
    progress = measure('当前目录成员组完整度', 710, 710, '组', 'topic_groups', '当前目录')
    view = snapshot_quality_view(source, progress)
    assert view['coverage_status'] == 'covered'
    assert view['snapshot_quality'] == 'partial'
    assert view['snapshot_quality_label'] == '发布时部分覆盖'
    assert '历史每日' in view['snapshot_quality_note']
    assert source == original, 'Read model must not rewrite publication metadata'


def test_on_demand_indicators_are_not_reported_as_failed_or_complete():
    progress = dataset_progress(row('indicators', 6_000_000, 6_000_000))
    assert progress['coverage_status'] == 'on_demand'
    assert progress['coverage_label'] == '按需计算'
    assert progress['scope_complete'] is None and progress['percent'] is None
    assert progress['state'] == 'unknown', 'Legacy numerical-evidence flag remains compatible'


def test_listing_date_completion_does_not_claim_historical_identity_completeness():
    source = {**row('security_identity', 100, coverage={'identity_unknown_count': 0}), 'quality': 'partial'}
    progress = dataset_progress(source)
    view = snapshot_quality_view(source, progress)
    assert progress['percent'] == 100 and progress['coverage_status'] == 'covered'
    assert view['snapshot_quality'] == 'partial'
    assert '历史退市' in view['snapshot_quality_note']


def test_pool_count_contradictions_cannot_cancel_each_other_into_one_hundred_percent():
    reference = {'pool_trade_date': '2026-01-06', 'pool_coverage': {
        'limitup': {'observed_rows': 11, 'declared_total': 10, 'complete': True},
        'limitdown': {'observed_rows': 0, 'declared_total': 1, 'complete': True},
        'breakpool': {'observed_rows': 3, 'declared_total': 3, 'complete': True},
    }}
    progress = dataset_progress(row('market_review'), reference, 'market-fixture')
    assert progress['coverage_status'] == 'unknown' and progress['percent'] is None


def test_data_status_separates_measure_snapshot_and_task_without_writes(isolated, monkeypatch):
    from app import core
    from app.services import platform_service, source_service, sector_chart_batch_jobs, task_service
    reference = {'concept_catalog': [{'thscode': '885001.TI'}], 'industry_catalog': [],
                 'topics': [{'topic_code': '885001.TI', 'members_complete': True}]}
    class Market:
        def status(self): return {'snapshot_id': 'market-fixture', 'ready': False}
        def reference(self): return reference
    monkeypatch.setattr(platform_service, 'get_market', lambda: Market())
    monkeypatch.setattr(source_service, 'list_sources', lambda: {'items': [], 'public_supplement': {}})
    monkeypatch.setattr(sector_chart_batch_jobs, 'batch_status', lambda market: {})
    task = task_service.create_task('topic_refresh', {})
    artifact = core.published_dir() / 'coverage-fixture.json'
    artifact.write_text('{}')
    snapshot = core.publish_snapshot(module='topics', job_id=task['job_id'], start='2026-01-06', end='2026-01-06',
        artifacts=[{'object_key': artifact.name, 'sha256': core.hash_file(artifact), 'byte_count': artifact.stat().st_size, 'row_count': 10}],
        lineage={'market_snapshot_id': 'market-fixture'}, quality='partial')
    task_service.finish(task['job_id'], 'partial', '当前范围已完成，整体边界保留')
    before = task_service.list_tasks()['total']
    dataset = platform_service.data_status()['datasets'][0]
    assert dataset['coverage_status'] == 'covered' and dataset['coverage_label'] == '当前范围已覆盖'
    assert dataset['coverage_progress']['percent'] == 100
    assert dataset['status'] == dataset['snapshot_quality'] == 'partial'
    assert task_service.task_view(task['job_id'])['status'] == 'partial'
    with core.connect() as connection:
        assert connection.execute('SELECT quality FROM dataset_snapshot WHERE snapshot_id=?', (snapshot,)).fetchone()[0] == 'partial'
    assert task_service.list_tasks()['total'] == before
