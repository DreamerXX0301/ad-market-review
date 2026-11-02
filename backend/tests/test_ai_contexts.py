"""Synthetic local inputs only: no live service, credentials, HTTP or models."""
from contextlib import nullcontext
from copy import deepcopy
import json

import pytest
from fastapi import HTTPException

from app.core import digest, dumps
from app import core
from app.services import ai_contexts as ai, sector_history
from test_market_ladder import Market, B, OTHER, DAYS, TODAY
from test_market_sentiment import rows as comparable_rows
from test_workspace import ws


SYMBOL = '600001.SH'


def report():
    evidence = []
    for identifier, kind, data in [
        ('valuation', 'valuation_snapshot', {'pe_ttm': 12.5, 'pb_mrq': None, 'unit': '倍',
                                           'scope': '最新快照', 'raw': {'api_key': 'fixture-secret'}}),
        ('business', 'business_composition', {'report_period': '2025-12-31', 'category': '产品',
                                            'name': '合成测试产品', 'revenue_cny': 100, 'revenue_ratio': .5}),
        ('announcement', 'announcement', {'title': '合成测试公告标题', 'published_date': TODAY}),
    ]:
        evidence.append({'evidence_id': identifier, 'source': 'cninfo' if kind == 'announcement' else 'fuyao',
                         'kind': kind, 'title': '合成测试证据', 'instrument_id': SYMBOL, 'data': data,
                         'published_at_ms': None, 'fetched_at_ms': 1234, 'report_period': data.get('report_period'),
                         'content_hash': digest(data), 'historical_usable': False})
    return {'schema_version': 2, 'report_id': 'fixture-report', 'data_cutoff': TODAY,
            'instrument': {'instrument_id': SYMBOL, 'name': '合成测试个股', 'kind': 'stock'},
            'material_window': {'collection_started_at_ms': 1000, 'collection_finished_at_ms': 2000},
            'snapshot_refs': {'daily_raw': 'fixture-raw'}, 'evidence': evidence,
            'claims': [{'claim_id': 'business-claim', 'text': '来源主营产品线索', 'kind': 'source_statement',
                        'evidence_ids': ['business']},
                       {'claim_id': 'title-claim', 'text': '合成测试公告标题', 'kind': 'title_clue',
                        'evidence_ids': ['announcement']}],
            'source_coverage': [], 'gaps': [{'capability': 'media_news', 'state': 'unavailable',
                                           'reason': '没有媒体新闻来源'}],
            'topics': ['当前成员不能用于历史回填'],
            'cache': {'expires_at_ms': 9999}}


@pytest.fixture
def saved(monkeypatch):
    value = report()
    calls = []
    def read(identifier, *, touch=True):
        calls.append((identifier, touch))
        return deepcopy(value)
    monkeypatch.setattr(ai, 'get_report', read)
    # A research interpretation must remain independent of all market readers.
    monkeypatch.setattr(ai, 'get_market', lambda: pytest.fail('research must not read current market'))
    return value, calls


def metric_values(context):
    return {row['metric_id']: row['value'] for row in context['metrics']}


def test_research_is_frozen_self_contained_without_ttl_touch_or_raw_export(saved):
    source, calls = saved
    result = ai.build_context_preview('stock_research', report_id='fixture-report')
    context = result['context']
    assert calls == [('fixture-report', False)]
    assert result['eligible'] and result['preview']['state'] == 'partial'
    assert result['bytes'] == len(dumps(context).encode()) < ai.MAX_CONTEXT_BYTES
    assert 'fixture-secret' not in dumps(result)
    assert '当前成员不能用于历史回填' not in dumps(result)
    assert context['source_report_id'] == 'fixture-report' and context['instrument_id'] == SYMBOL
    assert context['dates']['data_cutoff'] == TODAY
    assert context['dates']['materials_finished_at_ms'] == 2000
    assert metric_values(context)['valuation:pe_ttm'] == 12.5
    assert metric_values(context)['valuation:pb_mrq'] is None
    ratio = next(row for row in context['metrics'] if row['metric_id'] == 'business:revenue_ratio')
    assert ratio['value'] == .5 and ratio['unit'] == 'ratio'
    assert '1代表100%' in ratio['reason']
    assert context['allowed_ids']['claim_ids'] == ['business-claim', 'title-claim']
    # The saved input is its own complete object after parent research expires.
    restored = json.loads(dumps(context))
    source.clear()
    assert restored['evidence'][0]['data']['pe_ttm'] == 12.5
    assert restored['gaps'][0]['reason'] == '没有媒体新闻来源'
    assert restored['context_sha256'] == digest({key: value for key, value in restored.items()
                                                if key != 'context_sha256'})


def test_cache_access_times_do_not_change_context_digest(saved):
    value, _ = saved
    before = ai.build_context('research', report_id='fixture-report')
    value['cache'] = {'last_accessed_at_ms': 7777, 'expires_at_ms': 8888}
    assert ai.build_context('research', report_id='fixture-report') == before


def test_research_permissions_and_foreign_identity_drop_claims_instead_of_dangling_refs(saved):
    value, _ = saved
    value['evidence'][0]['ai_processing_allowed'] = False
    value['evidence'][1]['instrument_id'] = '600002.SH'
    value['evidence'][2]['state'] = 'requires_license'
    context = ai.build_context('stock', report_id='fixture-report')
    assert context['eligibility']['eligible'] is False
    assert context['allowed_ids'] == {'claim_ids': [], 'evidence_ids': [], 'metric_ids': []}
    assert len(context['exclusions']) == 3
    assert '来源限制' in context['exclusions'][0]['reason']


def test_duplicate_evidence_ids_are_all_rejected(saved):
    value, _ = saved
    value['evidence'].append(deepcopy(value['evidence'][0]))
    context = ai.build_context('research', report_id='fixture-report')
    assert 'valuation' not in context['allowed_ids']['evidence_ids']
    assert len(context['exclusions']) == 2


def test_saved_context_survives_actual_parent_cleanup_without_new_market_pins(ws):
    from app.services import research_cache
    now = core.now_ms()
    value = report()
    with core.connect() as connection:
        connection.execute('INSERT INTO research_report(report_id,job_id,instrument_id,data_cutoff,created_at_ms,'
                           'report_json,snapshot_refs_json,last_accessed_at_ms,expires_at_ms) VALUES(?,?,?,?,?,?,?,?,?)',
                           ('fixture-report', 'fixture-job', SYMBOL, TODAY, now, dumps(value), '{}', now, now + 10000))
        pins_before = connection.execute('SELECT COUNT(*) FROM retention_pin').fetchone()[0]
    context = ai.build_context('research', report_id='fixture-report')
    with core.connect() as connection:
        row = connection.execute('SELECT last_accessed_at_ms,expires_at_ms FROM research_report').fetchone()
        assert tuple(row) == (now, now + 10000)
        assert connection.execute('SELECT COUNT(*) FROM retention_pin').fetchone()[0] == pins_before
        connection.execute('UPDATE research_report SET expires_at_ms=?', (now - 1,))
    assert research_cache.cleanup_expired(now)['removed_reports'] == 1
    with pytest.raises(HTTPException) as error:
        ai.build_context('research', report_id='fixture-report')
    assert error.value.status_code == 404
    restored = json.loads(dumps(context))
    assert restored['source_report_id'] == 'fixture-report'
    assert restored['evidence'][0]['data']['pe_ttm'] == 12.5


def test_hundreds_of_large_or_redundant_items_are_explicitly_bounded(saved):
    value, _ = saved
    for index in range(400):
        item = deepcopy(value['evidence'][2])
        item['evidence_id'] = 'announcement-extra-' + str(index)
        item['data']['title'] = '合成测试公告' + str(index)
        item['content_hash'] = digest(item['data'])
        value['evidence'].append(item)
    context = ai.build_context('research', report_id='fixture-report')
    assert len(dumps(context).encode()) <= ai.MAX_CONTEXT_BYTES
    assert len([row for row in context['evidence'] if row['kind'] == 'announcement']) == 6
    assert context['excluded_count'] == 395
    assert len(context['exclusions']) == 8 and context['exclusion_details_truncated']
    assert sum(row['count'] for row in context['exclusion_summary']) == 395
    assert all(row['evidence_ids'][0] in context['allowed_ids']['evidence_ids'] for row in context['metrics'])


def test_large_evidence_is_excluded_whole_and_technical_uses_saved_complete_period_only(saved):
    value, _ = saved
    value['evidence'][0]['data']['scope'] = '超限测试' * 4000
    value['evidence'][0]['content_hash'] = digest(value['evidence'][0]['data'])
    value['technical_assessment'] = {
        'analysis_contract': {'price_basis': 'none', 'indicator_continuity': 'market_sessions'},
        'periods': [{'period': 'W', 'label': '周线', 'state': 'unknown', 'reason': '完整周预热不足',
                     'observed_date': DAYS[-2], 'expected_date': DAYS[-2],
                     'observations': {'close': 10.5, 'ma_fast': None, 'slope_pct': None},
                     'current_period': {'close': 999999, 'forming': True}}]}
    context = ai.build_context('research', report_id='fixture-report')
    assert 'valuation' not in context['allowed_ids']['evidence_ids']
    assert '999999' not in dumps(context)
    assert metric_values(context)['technical:W:close'] == 10.5
    assert metric_values(context)['technical:W:ma_fast'] is None
    assert '完整周预热不足' in dumps(context)


def test_corrupted_saved_evidence_is_not_sent_as_verified_fact(saved):
    value, _ = saved
    value['evidence'][1]['data']['revenue_cny'] = 999999
    context = ai.build_context('research', report_id='fixture-report')
    assert 'business' not in context['allowed_ids']['evidence_ids']
    assert 'business-claim' not in context['allowed_ids']['claim_ids']
    assert any('哈希不一致' in row['reason'] for row in context['exclusions'])


@pytest.mark.parametrize('mode,status', [('v1', 422), ('wrong_day', 409), ('wrong_symbol', 422)])
def test_parent_report_binding_rejects_old_or_mismatched_inputs(saved, mode, status):
    value, _ = saved
    if mode == 'v1': value['schema_version'] = 1
    if mode == 'wrong_symbol': value['instrument']['instrument_id'] = '600001'
    with pytest.raises(HTTPException) as error:
        ai.build_context('research', report_id='fixture-report', as_of='2026-01-12' if mode == 'wrong_day' else None)
    assert error.value.status_code == status


class PublishedMarket(Market):
    def status(self):
        return {'ready': True, 'snapshot_id': 'fixture', 'data_end': TODAY,
                'collected_at': '2026-01-13T16:00:00+08:00'}

    def panel(self, day):
        assert day == TODAY
        return comparable_rows(self)

    def turnover_history(self, day):
        return [{'date': current, 'amount': amount, 'source': 'fuyao', 'scope': 'same-three-exchange-sample'}
                for current, amount in ((DAYS[-2], 4000), (TODAY, 5000))]


@pytest.fixture
def published(monkeypatch):
    market = PublishedMarket()
    # These fields only describe dated source observations in the fixture.
    for entry in market.ref['_supplement']['annual_pools'].values():
        for item in entry['items']: item['is_st'] = False
    market.history[B][-1]['close'] = 9
    market.history[OTHER][-1]['close'] = 11
    market.ref['_supplement']['annual_pools'][TODAY]['items'][-1]['last_price'] = 11
    market.ref['topics'] = [{'name': '未来题材不能泄入历史', 'observed_date': '2099-01-01',
                             'instrument_ids': [B, OTHER]}]
    heads = {'published_heads': {'daily_raw': 'raw-v1', 'calendar': 'cal-v1'},
             'market_supplement_head': {'snapshot_id': 'fixture', 'sha256': 'a' * 64}}
    calls = []
    def load(snapshot_id, *, dates=None, fields=None):
        calls.append((snapshot_id, dates, fields))
        values = {key: {day: deepcopy(row) for day, row in market.ref['_supplement'].get(key, {}).items()
                        if day in dates} for key in fields}
        values.update(target_date=TODAY, snapshot_id='fixture')
        return values, ('verified-supplement-index-v1', '/private/fixture-path', 'fixture', 'a' * 64, dates, fields)
    monkeypatch.setattr(ai, 'supplement_access', lambda: nullcontext())
    monkeypatch.setattr(ai, '_source_heads', lambda: deepcopy(heads))
    monkeypatch.setattr(ai, 'get_market', lambda: market)
    monkeypatch.setattr(ai, 'load_supplement_with_fingerprint', load)
    monkeypatch.setattr(ai, 'build_briefs', lambda day: {'fingerprints': {}, 'blocks': [], 'gaps': []})
    monkeypatch.setattr(ai, 'brief_fingerprints', lambda day: {})
    return market, heads, calls


def test_market_uses_exact_dated_projection_and_comparable_cohort_without_current_members(published):
    market, _, calls = published
    result = ai.build_context_preview('market_review', as_of=TODAY)
    context = result['context']
    assert result['eligible'] and result['bytes'] < ai.MAX_CONTEXT_BYTES
    assert calls == [('fixture', (DAYS[-2], TODAY), ai.SUPPLEMENT_FIELDS[:-1]),
                     ('fixture', (TODAY,), ('sector_index_history',))]
    assert '未来题材不能泄入历史' not in dumps(context)
    assert '/private/' not in dumps(context)
    metrics = metric_values(context)
    assert metrics['turnover:amount'] == 5000 and metrics['turnover:amount_change'] == 1000
    assert metrics['cohort:ordinary_10:previous_limit_up_avg_return_pct'] == pytest.approx(0)
    assert metrics['cohort:ordinary_10:positive_ratio_pct'] == 50
    assert market.history_calls[0][1:] == (DAYS[-2], TODAY)
    cohort = next(row for row in context['evidence'] if row['evidence_id'] == 'cohort:ordinary_10')
    assert cohort['dates'] == {'trade_date': TODAY, 'cohort_date': DAYS[-2]}
    assert cohort['data']['current_identity_unknown_count'] == 1
    assert any(row['capability'] == 'historical_memberships' for row in context['gaps'])
    assert all(row['unit'] == 'CNY' for row in context['metrics'] if row['metric_id'] == 'turnover:amount')


def test_missing_supplement_is_partial_and_cannot_turn_unknown_pools_into_zero(published, monkeypatch):
    monkeypatch.setattr(ai, 'load_supplement_with_fingerprint', lambda *a, **kw: ({}, None))
    context = ai.build_context('market', as_of=TODAY)
    assert context['eligibility']['state'] == 'partial'
    values = metric_values(context)
    assert values['previous_limit_up:previous_limit_up_avg_return_pct'] is None
    assert values['previous_limit_up:promotion_rate_pct'] is None
    assert values['turnover:amount'] == 5000
    assert any(row['capability'] == 'market_supplement_pools' for row in context['gaps'])


@pytest.mark.parametrize('changed', ['main', 'supplement'])
def test_independent_head_changes_conflict_before_freezing(published, monkeypatch, changed):
    _, heads, _ = published
    after = deepcopy(heads)
    if changed == 'main': after['published_heads']['daily_raw'] = 'raw-v2'
    else: after['market_supplement_head']['sha256'] = 'b' * 64
    responses = iter([heads, after])
    monkeypatch.setattr(ai, '_source_heads', lambda: deepcopy(next(responses)))
    with pytest.raises(HTTPException) as error:
        ai.build_context('market', as_of=TODAY)
    assert error.value.status_code == 409


def test_repeated_market_preview_has_stable_digest_despite_computation_time(published):
    before = ai.build_context('market', as_of=TODAY)
    after = ai.build_context('market', as_of=TODAY)
    assert before == after


def test_wrong_day_index_quote_never_substitutes_observation_day(published):
    market, _, _ = published
    market.ref['indices'] = {'000001.SH': [{'date': DAYS[-2], 'close': 999, 'source': 'fuyao'}]}
    context = ai.build_context('market', as_of=TODAY)
    assert metric_values(context)['index:000001.SH:index_daily_return_pct'] is None
    assert metric_values(context)['index:000001.SH:index_20d_return_pct'] is None


def test_turnover_wrong_scope_is_unknown_and_valid_zero_is_preserved(published, monkeypatch):
    market, _, _ = published
    def history(day):
        return [{'date': DAYS[-2], 'amount': 1000, 'source': 'fuyao', 'scope': 'old-scope'},
                {'date': TODAY, 'amount': 0, 'source': 'fuyao', 'scope': 'new-scope'}]
    monkeypatch.setattr(market, 'turnover_history', history)
    context = ai.build_context('market', as_of=TODAY)
    assert metric_values(context)['turnover:amount'] == 0
    assert metric_values(context)['turnover:amount_change'] is None
    assert any('口径不一致' in row['reason'] for row in context['gaps'])


def test_sector_prices_survive_missing_historical_members_without_becoming_flow():
    previous = DAYS[-2]
    from test_sector_history import payload, topic
    raw = payload()
    for item, day in zip(raw['data']['item'], (previous, TODAY)):
        item['date_ms'] = sector_history._midnight(day)
    normalized = sector_history.normalize_sector_history(raw, topic(), TODAY, previous)
    record = {'date': TODAY, 'source': 'fuyao', 'normalization_version': sector_history.NORMALIZATION_VERSION,
              'coverage': {'expected_previous_date': previous}, 'complete': True,
              'items': [normalized], 'failures': []}
    context = ai._base('market_review', 'fixture', TODAY)
    ai._sectors(context, {'target_date': TODAY, 'sector_index_history': {TODAY: record}}, TODAY)
    row = context['evidence'][0]['data']
    assert row['change_pct'] == pytest.approx(5) and row['amount'] == 0
    assert row['member_count'] is None and row['limit_up_count'] is None
    assert row['price_unit'] == '点' and row['amount_unit'] == 'CNY'
    assert 'net_inflow' not in dumps(context)


def test_full_market_input_keeps_dated_sector_observation_within_budget(published):
    market, _, _ = published
    from test_sector_history import payload, topic
    raw = payload()
    for item, day in zip(raw['data']['item'], (DAYS[-2], TODAY)):
        item['date_ms'] = sector_history._midnight(day)
    normalized = sector_history.normalize_sector_history(raw, topic(), TODAY, DAYS[-2])
    market.ref['_supplement']['sector_index_history'] = {TODAY: {
        'date': TODAY, 'source': 'fuyao', 'normalization_version': sector_history.NORMALIZATION_VERSION,
        'coverage': {'expected_previous_date': DAYS[-2]}, 'complete': True,
        'items': [normalized], 'failures': []}}
    context = ai.build_context('market', as_of=TODAY)
    assert 'sector:881101.TI' in context['allowed_ids']['evidence_ids']
    assert metric_values(context)['sector:881101.TI:amount'] == 0
    assert len(dumps(context).encode()) <= ai.MAX_CONTEXT_BYTES


def test_realistic_repeated_source_metadata_and_final_ids_fit_without_losing_dates(published):
    """A real copy had 12 proof records/39 metrics and grew after final IDs.

    Only that structural shape is reproduced here; no real market values or
    source payloads enter the repository fixture.
    """
    context = ai._market_context(TODAY)
    records = [{'trade_date': TODAY if index % 2 else DAYS[-2], 'pool': 'up' if index % 3 else 'down',
                'source': 'fixture-verified-provider', 'fetched_at': '2026-01-14T16:00:00+08:00',
                'first_known_at': None, 'source_published_at': None,
                'source_sha256': str(index) * 64, 'record_fingerprint': str(index) * 64}
               for index in range(10)]
    context['source_provenance']['source_records'] = [*records, deepcopy(records[0]), deepcopy(records[1])]
    while len(context['metrics']) < 39:
        metric = deepcopy(context['metrics'][0])
        metric['metric_id'] = 'fixture-extra-metric:' + str(len(context['metrics']))
        context['metrics'].append(metric)
    # Simulate the combined source metadata/IDs footprint that exceeded 32KiB;
    # the padding is synthetic metadata, never a copied supplier response.
    old_size = ai._finalize(deepcopy(context), list(context['exclusions']))
    context['source_fingerprints']['fixture_manifest_metadata'] = 'x' * max(0, 34100 - old_size)
    old_metrics = deepcopy(context['metrics'])
    old_evidence_ids = [row['evidence_id'] for row in context['evidence']]
    result = ai._bounded_context(context)
    assert len(dumps(result).encode()) <= ai.MAX_CONTEXT_BYTES
    assert result['allowed_ids']['evidence_ids'] == old_evidence_ids
    assert result['source_provenance']['source_record_count'] == 12
    assert result['source_provenance']['source_records_duplicate_count'] == 2
    assert result['source_provenance']['source_records'] == records
    for old, metric in zip(old_metrics, result['metrics']):
        assert {**result['metric_defaults'], **metric} == old
        assert metric['dates'] == old['dates'] and metric['unit'] == old['unit']
    assert result['context_sha256'] == digest({key: value for key, value in result.items() if key != 'context_sha256'})


def test_final_size_projects_whole_optional_observation_and_removes_all_dangling_refs(published):
    context = ai._market_context(TODAY)
    original_dates = deepcopy(context['dates'])
    original_proofs = deepcopy(context['source_provenance']['source_records'])
    context['source_fingerprints']['fixture_metadata'] = 'x' * (
        ai.MAX_CONTEXT_BYTES - len(dumps(context).encode()) + 1000)
    result = ai._bounded_context(context)
    assert len(dumps(result).encode()) <= ai.MAX_CONTEXT_BYTES
    assert result['excluded_count'] > 0
    assert any('完整输入' in row['reason'] for row in result['exclusions'])
    allowed = set(result['allowed_ids']['evidence_ids'])
    assert all(set(row['evidence_ids']) <= allowed for row in result['metrics'])
    assert all(set(row['evidence_ids']) <= allowed for row in result['claims'])
    assert result['dates'] == original_dates
    assert result['source_provenance']['source_records'] == original_proofs
    assert {'market_distribution', 'turnover', 'previous_limit_up'} <= allowed
    assert any(row['capability'] == 'historical_memberships' for row in result['gaps'])


@pytest.mark.parametrize('kind,report_id,day', [
    ('invalid', None, None), ('research', None, None), ('market', 'wrong-report', TODAY),
    ('market', None, '2026-02-30'), ('market', None, '2026-01-11'),
])
def test_invalid_context_requests_do_not_trigger_network(published, kind, report_id, day):
    with pytest.raises(HTTPException) as error:
        ai.build_context(kind, report_id=report_id, as_of=day)
    assert error.value.status_code == 422


@pytest.mark.parametrize('source,flag,expected_absent',[
    ('fuyao','egress_allowed',{'valuation','business'}),
    ('cninfo','ai_processing_allowed',{'announcement'}),
    ('valuation','egress_allowed',{'valuation'}),
    ('company_profile','ai_processing_allowed',{'business'}),
    ('announcements','egress_allowed',{'announcement'}),
])
def test_explicit_source_coverage_denial_reaches_dependent_evidence(saved,source,flag,expected_absent):
    value,_=saved
    value['source_coverage']=[{'source_id':source,'state':'available',flag:False}]
    context=ai.build_context('stock',report_id='fixture-report')
    accepted=set(context['allowed_ids']['evidence_ids'])
    assert not accepted.intersection(expected_absent)
    assert len(context['exclusions'])==len(expected_absent)
    assert all(set(claim['evidence_ids'])<=accepted for claim in context['claims'])


@pytest.mark.parametrize('flag',['egress_allowed','ai_processing_allowed'])
def test_explicit_report_denial_excludes_all_materials_and_technical(saved,flag):
    value,_=saved
    value[flag]=False
    value['technical_assessment']={'periods':[{'period':'D','label':'测试日线','observations':{'close':10}}]}
    context=ai.build_context('stock',report_id='fixture-report')
    assert not context['eligibility']['eligible']
    assert not context['evidence'] and not context['metrics'] and not context['claims']
    assert context['exclusions'][0]['source']=='report'


def effective_model_metric(projected, metric):
    result = {**projected.get('metric_defaults', {}), **metric}
    if 'scope' not in result and len(result.get('evidence_ids', [])) == 1:
        evidence = next(row for row in projected['evidence']
                        if row['evidence_id'] == result['evidence_ids'][0])
        if evidence.get('metric_scope'):
            result['scope'] = evidence['metric_scope']
    return result


def assert_projected_metrics_equal(context, projected):
    assert projected['allowed_ids'] == context['allowed_ids']
    by_id = {row['metric_id']: row for row in projected['metrics']}
    for row in context['metrics']:
        original = {**context.get('metric_defaults', {}), **row}
        result = effective_model_metric(projected, by_id[row['metric_id']])
        for key in ('metric_id', 'label', 'value', 'unit', 'dates', 'as_of', 'price_basis', 'scope', 'evidence_ids'):
            assert result.get(key) == original.get(key), (row['metric_id'], key)
        assert result.get('formula') == original.get('formula')
        assert result.get('reason') == original.get('reason')


def test_model_input_preserves_stock_facts_units_dates_and_does_not_mutate_context(saved, monkeypatch):
    context = ai.build_context('stock', report_id='fixture-report')
    original = deepcopy(context)
    monkeypatch.setattr(ai, 'get_report', lambda *a, **kw: pytest.fail('projection cannot fetch reports'))
    monkeypatch.setattr(ai, 'get_market', lambda: pytest.fail('projection cannot fetch market data'))
    projected = ai.model_input(context)
    assert context == original
    assert_projected_metrics_equal(context, projected)
    assert projected['subject'] == context['subject'] and projected['instrument_id'] == SYMBOL
    assert projected['dates'] == context['dates']
    assert projected['gaps'] == ai._model_data(context['gaps'])
    assert [row['evidence_id'] for row in projected['evidence']] == context['allowed_ids']['evidence_ids']
    product = next(row for row in projected['evidence'] if row['evidence_id'] == 'business')
    assert product['data']['revenue_ratio'] == .5
    assert product['dates']['published_at_ms'] is None
    assert 'fixture-secret' not in dumps(projected)
    assert 'context_sha256' not in dumps(projected) and 'source_fingerprints' not in projected
    projected['evidence'][0]['data']['pe_ttm'] = 999
    assert context == original, 'projection must not share mutable nested facts with saved context'


def test_model_input_preserves_market_unknowns_scopes_and_all_distinct_source_dates(published):
    context = ai.build_context('market', as_of=TODAY)
    original = deepcopy(context)
    projected = ai.model_input(context)
    assert_projected_metrics_equal(context, projected)
    assert context == original
    assert projected['as_of'] == TODAY
    assert projected['price_basis'] == 'none'
    assert projected['dates']['previous_market_date'] == DAYS[-2]
    original_dates = [{key: row[key] for key in ('source', 'trade_date', 'pool', 'fetched_at',
                      'source_published_at', 'first_known_at') if key in row}
                      for row in context['source_provenance']['source_records']]
    projected_dates = projected['source_dates']['sources']
    assert {dumps(row) for row in projected_dates} == {dumps(row) for row in original_dates}
    assert projected['source_dates']['known_time_complete'] is False
    assert 'source_provenance' not in projected
    assert all('fingerprint' not in key and 'sha256' not in key
               for row in projected_dates for key in row)
    index = next(row for row in projected['metrics'] if row['metric_id'] == 'index:000001.SH:index_20d_return_pct')
    assert index['value'] is None and index['unit'] == '%'


def test_model_input_handles_old_context_without_metric_defaults(saved):
    context = ai.build_context('stock', report_id='fixture-report')
    defaults = context.pop('metric_defaults')
    context['metrics'] = [{**defaults, **row} for row in context['metrics']]
    projected = ai.model_input(context)
    assert_projected_metrics_equal(context, projected)
    assert projected['metric_defaults']['as_of'] == TODAY
    assert projected['metric_defaults']['price_basis'] == 'none'


def test_model_input_keeps_actual_exclusion_ranges_and_original_coverage_distinct(saved):
    value, _ = saved
    value['source_coverage'] = [{'source_id': 'announcements', 'accepted_count': 88,
                                 'state': 'available', 'reason': None}]
    context = ai.build_context('stock', report_id='fixture-report')
    context['exclusion_summary'] = [
        {'source': 'cninfo', 'count': 87, 'reason': '公告条目预算排除'},
        {'source': 'local_research_technical', 'count': 3, 'reason': '技术观察预算排除'}]
    context['excluded_count'] = 90
    context['exclusions'] = [{'evidence_id': 'excluded-secret-id', 'source': 'cninfo', 'reason': '预算排除'}]
    context['exclusion_details_truncated'] = True
    projected = ai.model_input(context)
    scope = projected['material_scope']
    assert scope['excluded_record_count'] == 90
    assert {row['source']: row['record_count'] for row in scope['excluded_ranges']} == {
        'cninfo': 87, 'local_research_technical': 3}
    assert scope['exclusion_details_truncated']
    assert projected['source_coverage'][0]['accepted_count'] == 88
    assert next(row for row in scope['included'] if row['kind'] == 'announcement')['count'] == 1
    assert '原报告覆盖' in scope['notice']
    assert 'excluded-secret-id' not in dumps(projected)


def test_model_input_removes_audit_paths_hashes_and_only_redundant_null_metadata(saved):
    context = ai.build_context('stock', report_id='fixture-report')
    evidence = context['evidence'][0]
    evidence['data'].update(raw_sha256='sensitive-audit-sha', response_json_sha256='sensitive-response-sha',
                            debug_path='/private/fixture-audit', raw_artifact='fixture-raw-response',
                            api_key='fixture-key', amount=None, member_count=None,
                            published_at_ms=None, document_url=None)
    projected = ai.model_input(context)
    data = projected['evidence'][0]['data']
    assert data['amount'] is None and data['member_count'] is None
    assert data['published_at_ms'] is None
    assert not {'raw_sha256', 'response_json_sha256', 'debug_path', 'raw_artifact', 'api_key', 'document_url'} & data.keys()
    assert '/private/' not in dumps(projected) and 'fixture-key' not in dumps(projected)


@pytest.mark.parametrize('field', ['evidence_ids', 'claim_ids', 'metric_ids'])
def test_model_input_does_not_silently_narrow_or_expand_original_allowed_ids(saved, field):
    context = ai.build_context('stock', report_id='fixture-report')
    context['allowed_ids'][field].append('not-actually-sent')
    original = deepcopy(context)
    with pytest.raises(ValueError, match='invalid_context_references'):
        ai.model_input(context)
    assert context == original


def test_model_input_keeps_denied_materials_excluded(saved):
    value, _ = saved
    value['source_coverage'] = [{'source_id': 'fuyao', 'egress_allowed': False, 'state': 'available'}]
    context = ai.build_context('stock', report_id='fixture-report')
    projected = ai.model_input(context)
    assert_projected_metrics_equal(context, projected)
    assert projected['allowed_ids']['evidence_ids'] == ['announcement']
    assert any(row.get('egress_allowed') is False for row in projected['source_coverage'])
    assert not {'business', 'valuation'} & set(projected['allowed_ids']['evidence_ids'])


def test_model_input_substantially_reduces_repetitive_audit_bytes_without_dropping_facts(published):
    context = ai.build_context('market', as_of=TODAY)
    context['source_fingerprints']['fixture_long_sha_ledger'] = ['a' * 64 for _ in range(40)]
    for index, evidence in enumerate(context['evidence']):
        evidence['data']['fixture_window_fingerprint'] = str(index) * 64
    before = len(dumps(context).encode())
    projected = ai.model_input(context)
    after = len(dumps(projected).encode())
    assert after < before * .85
    assert_projected_metrics_equal(context, projected)
    assert len(projected['evidence']) == len(context['evidence'])
    assert len(projected['claims']) == len(context['claims'])
    assert len(projected['metrics']) == len(context['metrics'])


def test_market_preview_prioritizes_saved_day_briefs_without_network(published, monkeypatch):
    import httpx
    blocks = []
    for kind in ('auction', 'noon', 'noon_close'):
        blocks.append({'evidence': {'evidence_id': 'day_brief:' + kind,
                        'source': 'saved_' + kind, 'kind': kind + '_brief', 'title': '已保存时点摘要',
                        'dates': {'trade_date': TODAY}, 'data': {'scope': '原冻结样本，非净流入', 'unknown_count': None,
                            'coverage_notes': ['仅保留本次真实来源、时间与覆盖说明；缺失不是零。' * 5 for _ in range(3)]}},
                       'metrics': [{'metric_id': 'day_brief:' + kind + ':count', 'label': '已核验样本',
                                    'value': None, 'unit': '家', 'dates': {'trade_date': TODAY}}]})
        for index in range(7):
            blocks[-1]['metrics'].append({'metric_id': 'day_brief:' + kind + ':coverage_' + str(index),
                'label': '时点覆盖测试样本', 'value': None, 'unit': '家', 'dates': {'trade_date': TODAY},
                'scope': '原时点固定名单与严格同日样本；不代表市场全集，不把缺报、资金流或成员身份补齐。',
                'reason': '测试夹具保留源日期、单位和未知原因，不能为压缩而删除这些范围说明。'})
    monkeypatch.setattr(ai, 'build_briefs', lambda day: {'fingerprints': {'fixture': 1}, 'blocks': blocks, 'gaps': []})
    monkeypatch.setattr(ai, 'brief_fingerprints', lambda day: {'fixture': 1})
    monkeypatch.setattr(httpx.Client, 'request', lambda *a, **kw: pytest.fail('preview cannot fetch remote data'))
    context = ai.build_context_preview('market', as_of=TODAY)['context']
    assert context['allowed_ids']['evidence_ids'][:3] == ['day_brief:auction', 'day_brief:noon', 'day_brief:noon_close']
    assert all(metric_values(context)['day_brief:' + kind + ':count'] is None
               for kind in ('auction', 'noon', 'noon_close'))
    assert '_content_budget_bytes' not in context
    assert len(dumps(context).encode()) <= ai.MAX_CONTEXT_BYTES
    assert {'market_distribution', 'index:000001.SH', 'index:399001.SZ', 'index:399006.SZ',
            'index:000688.SH', 'turnover', 'previous_limit_up'} <= set(context['allowed_ids']['evidence_ids'])
    assert context['version'] == 'ai-frozen-context-v2'


def test_day_brief_revision_change_during_rest_of_market_build_conflicts(published, monkeypatch):
    monkeypatch.setattr(ai, 'build_briefs', lambda day: {'fingerprints': {'revision': 1}, 'blocks': [], 'gaps': []})
    monkeypatch.setattr(ai, 'brief_fingerprints', lambda day: {'revision': 2})
    with pytest.raises(HTTPException) as error:
        ai.build_context('market', as_of=TODAY)
    assert error.value.status_code == 409


def test_larger_new_stock_context_keeps_saved_day_week_month_technical_with_materials(saved):
    value, _ = saved
    for index in range(5):
        item = deepcopy(value['evidence'][-1])
        item['evidence_id'] = 'announcement-extra-' + str(index)
        item['data']['title'] = '合成公告材料说明，日期和正文范围必须明确。' * 25
        item['content_hash'] = digest(item['data'])
        value['evidence'].append(item)
    value['technical_assessment'] = {'analysis_contract': {'price_basis': 'none',
        'indicator_continuity': 'market_sessions', 'period_policy': 'complete_comparable_periods',
        'parameters': {'coverage_notice': '固定市场交易日、完整周期、来源单位与预热限制均按原报告保存。' * 15}},
        'periods': [{'period': period, 'label': label, 'state': 'unknown',
                     'observed_date': TODAY, 'expected_date': TODAY,
                     'observations': {'close': 10, 'ma_fast': None, 'ma_slow': None, 'slope_pct': None},
                     'reason': '严格周期预热不足，不退到更早完成周期。',
                     'warnings': ['严格周期、同源、同单位与交易日窗口限制。' * 5]}
                    for period, label in (('D', '日线'), ('W', '周线'), ('M', '月线'))]}
    old_session = {'version': 'ai-frozen-context-v1', 'evidence': [{'legacy': True}]}
    old_copy = deepcopy(old_session)
    context = ai.build_context('stock', report_id='fixture-report')
    assert {'technical:D', 'technical:W', 'technical:M'} <= set(context['allowed_ids']['evidence_ids'])
    assert len([row for row in context['evidence'] if row['kind'] == 'announcement']) == 6
    assert context['version'] == 'ai-frozen-context-v2'
    assert old_session == old_copy
    assert len(dumps(context).encode()) <= ai.MAX_CONTEXT_BYTES
    assert all(row['data']['analysis_contract']['period_policy'] == 'complete_comparable_periods'
               for row in context['evidence'] if row['evidence_id'].startswith('technical:'))
