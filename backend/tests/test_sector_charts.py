"""Single-board chart fixtures: worker-only writes; GET never requests sources."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from math import sin
from pathlib import Path

import pandas as pd
import pytest

from app.analytics.indicators import aggregate_weekly, enrich_bars
from app.analytics.monthly import aggregate_monthly, enrich_monthly_bars
from app.services import sector_charts as charts

CODE = '885728.TI'
DAY = '2026-10-20'


class Market:
    def __init__(self, start='2021-10-20'):
        self.days = pd.bdate_range(start, DAY).strftime('%Y-%m-%d').tolist()
        self.state = {'snapshot_id': 'market-fixture', 'data_begin': self.days[0], 'data_end': DAY}
        self.ref = {'concept_catalog': [{'thscode': CODE, 'name': '人工智能'}],
                    'industry_catalog': [{'thscode': '881101.TI', 'name': '行业样本'}],
                    'topics': [{'topic_code': CODE, 'observed_date': '2026-10-21',
                                'instrument_ids': ['600000.SH']}]}

    def status(self): return dict(self.state)
    def calendar(self): return list(self.days)
    def reference(self): return deepcopy(self.ref)


class Adapter:
    def __init__(self, market, transform=None):
        self.calls = []; self.market = market; self.transform = transform

    def request(self, path, params):
        self.calls.append((path, dict(params)))
        rows = []
        for i, day in enumerate(self.market.days):
            stamp = charts._midnight(day)
            if not params['start'] <= stamp <= params['end']: continue
            close = 100+sin(i/4)*5+i*.01
            rows.append({'date_ms': stamp, 'open_price': close-.2, 'high_price': close+1,
                         'low_price': close-1, 'close_price': close,
                         'volume': 1000+i, 'turnover': (1000+i)*close})
        payload = {'code': 0, 'data': {'thscode': params['thscode'], 'interval': '1d',
                                     'adjust': None, 'item': rows}}
        return self.transform(payload) if self.transform else payload


@pytest.fixture
def local(tmp_path, monkeypatch):
    published = tmp_path/'published'
    monkeypatch.setattr(charts, 'published_dir', lambda: published)
    monkeypatch.setattr(charts, '_source_today', lambda: DAY)
    market = Market()
    return market, Adapter(market), published


def collect(local, **kwargs):
    market, adapter, _ = local
    plan = charts.plan_sector_chart(market, CODE, DAY)
    return charts.collect_sector_chart(market, plan, adapter, **kwargs)


def pointer(local):
    return local[2]/'market-supplement/accepted/fuyao/sector-chart'/(DAY+'-'+CODE+'.json')


def test_plan_freezes_real_catalog_identity_window_and_date(local):
    market, _, _ = local
    plan = charts.plan_sector_chart(market, CODE.lower(), DAY)
    assert plan['start'] == '2021-10-20' and plan['end'] == DAY
    assert plan['topic_code'] == CODE
    assert plan['instrument']['kind'] == 'index' and plan['instrument']['sector_kind'] == 'concept'
    assert plan['snapshot_id'] == 'market-fixture'
    assert plan['normalization_version'] == charts.NORMALIZATION_VERSION
    # Earlier observation still uses the actually retained lower bound, not
    # an invented five years of earlier official market sessions.
    older = charts.plan_sector_chart(market, CODE, '2024-10-18')
    assert older['start'] == '2021-10-20'


@pytest.mark.parametrize('code,day', [('BK0574', DAY), ('999999.TI', DAY),
                                     (CODE, '2026-10-21'), (CODE, '2026-10-18'),
                                     (CODE, '2020-10-20'), (CODE, '2026-02-30')])
def test_unknown_identity_or_non_retained_date_is_rejected_without_io(local, code, day):
    market, adapter, published = local
    with pytest.raises(ValueError): charts.plan_sector_chart(market, code, day)
    assert adapter.calls == [] and not published.exists()


def test_get_missing_cache_is_offline_and_does_not_create_directories(local):
    market, adapter, published = local
    result = charts.read_sector_chart(market, CODE, DAY, 'D')
    assert result['available'] is False and result['bars'] == [] and result['unavailable_reason']
    assert result['trading_day_distance']['state'] == 'unknown'
    assert adapter.calls == [] and not published.exists()


def test_worker_downloads_one_board_and_cache_reuse_verifies_raw_without_network(local):
    market, adapter, _ = local
    result = collect(local)
    assert result['available'] and not result['cache_hit'] and result['quality'] == 'complete'
    assert len(adapter.calls) == 1
    assert adapter.calls[0][1] == {'thscode': CODE, 'interval': '1d',
        'start': charts._midnight('2021-10-20'), 'end': charts._midnight(DAY)+86400000-1}
    assert pointer(local).is_file()
    fetched = result['record']['fetched_at']
    plan = charts.plan_sector_chart(market, CODE, DAY)
    class Offline:
        def request(self, *args): raise AssertionError('cached worker must not request source')
    repeated = charts.collect_sector_chart(market, plan, Offline())
    assert repeated['cache_hit'] and repeated['record']['fetched_at'] == fetched
    assert repeated['record']['bars'] == result['record']['bars']


def test_collection_clips_only_source_window_and_keeps_expected_leading_gap(local, monkeypatch):
    market, adapter, _ = local
    plan = charts.plan_sector_chart(market, CODE, DAY)
    monkeypatch.setattr(charts, '_source_today', lambda: '2026-10-21')
    result = collect(local)
    assert adapter.calls[0][1]['start'] == charts._midnight('2021-10-21')
    assert len(adapter.calls) == 1
    assert result['quality'] == 'partial'
    assert result['coverage']['requested_start'] == '2021-10-20'
    assert result['coverage']['missing_dates'] == ['2021-10-20']
    receipt = result['record']['source_request']
    assert receipt['clipped'] and receipt['start'] == '2021-10-21'
    assert receipt['expected_start'] == '2021-10-20' and receipt['observed_on'] == '2026-10-21'
    assert charts.plan_sector_chart(market, CODE, DAY) == plan
    assert result['record']['request_signature'] == plan['request_signature']
    assert '滚动' in result['coverage']['notice']


def test_cached_request_receipt_stays_valid_when_current_clock_advances(local, monkeypatch):
    market, adapter, _ = local
    monkeypatch.setattr(charts, '_source_today', lambda: '2026-10-21')
    first = collect(local)
    # Neither GET nor a valid cached collection should inspect today's boundary.
    def forbidden_clock(): raise AssertionError('cached history must use saved request window')
    monkeypatch.setattr(charts, '_source_today', forbidden_clock)
    view = charts.read_sector_chart(market, CODE, DAY)
    repeated = collect(local)
    assert view['available'] and repeated['cache_hit'] and len(adapter.calls) == 1
    assert view['coverage'] == first['coverage'] == repeated['coverage']
    assert view['fetched_at'] == first['record']['fetched_at']


def test_request_receipt_rejects_rows_outside_actual_clipped_window(local, monkeypatch):
    market, adapter, _ = local
    plan = charts.plan_sector_chart(market, CODE, DAY)
    payload = adapter.request('fixture', {'start': charts._midnight(plan['start']),
                              'end': charts._midnight(DAY), 'thscode': CODE})
    receipt = charts._source_request(plan, '2026-10-21')
    with pytest.raises(ValueError, match='窗口外'):
        charts.normalize_sector_chart(payload, plan, market.calendar(), receipt)


def test_wrong_saved_source_receipt_is_not_trusted_even_with_rehashed_artifact(local):
    market, _, published = local
    collect(local)
    saved = json.loads(pointer(local).read_text())
    path = published/'market-supplement'/saved['artifact']
    accepted = json.loads(path.read_text()); accepted['source_request']['start'] = '2022-01-01'
    path.write_text(json.dumps(accepted)); saved['sha256'] = sha256(path.read_bytes()).hexdigest()
    pointer(local).write_text(json.dumps(saved))
    view = charts.read_sector_chart(market, CODE, DAY)
    assert not view['available'] and '校验失败' in view['unavailable_reason']


def test_uncached_date_outside_provider_rolling_window_is_not_requested(local, monkeypatch):
    monkeypatch.setattr(charts, '_source_today', lambda: '2031-10-21')
    with pytest.raises(ValueError, match='近五年可查询范围'): collect(local)
    assert local[1].calls == [] and not pointer(local).exists()


def test_legacy_exact_window_cache_still_revalidates_raw_without_new_clock(local, monkeypatch):
    market, _, published = local
    collect(local)
    saved = json.loads(pointer(local).read_text()); path = published/'market-supplement'/saved['artifact']
    accepted = json.loads(path.read_text()); accepted.pop('source_request')
    path.write_text(json.dumps(accepted)); saved['sha256'] = sha256(path.read_bytes()).hexdigest()
    pointer(local).write_text(json.dumps(saved))
    monkeypatch.setattr(charts, '_source_today', lambda: '2031-10-21')
    view = charts.read_sector_chart(market, CODE, DAY)
    assert view['available'] and view['coverage']['missing_count'] == 0


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
def test_chart_reuses_shared_analytics_and_never_uses_member_quotes(local, period):
    market, _, _ = local
    result = collect(local)
    chart = charts.read_sector_chart(market, CODE, DAY, period)
    assert chart['available'] and chart['period'] == period
    assert chart['bars'] and chart['waves']['method']
    assert chart['instrument']['instrument_id'] == CODE and chart['price_unit'] == '点'
    assert chart['price_basis_options'] == [{'value': 'none', 'label': '指数原始点位（不复权）', 'available': True, 'reason': None}]
    assert '未核验' in chart['volume_unit']
    raw = result['record']['bars']
    if period == 'D': expected = enrich_bars(raw, market.calendar())
    elif period == 'W': expected = enrich_bars(aggregate_weekly(raw, market.calendar(), DAY))
    else: expected = enrich_monthly_bars(aggregate_monthly(raw, market.calendar(), DAY))
    for actual, reference in zip(chart['bars'], expected):
        for field in ('date', 'open', 'high', 'low', 'close', 'volume', 'amount',
                      'ma5', 'ma60', 'ma250', 'macd_dif', 'kdj_k'):
            assert actual.get(field) == reference.get(field)
    assert len(chart['bars']) == len(expected)
    assert chart['breadth_scope'] is None
    assert chart['trading_day_distance']['anchor_date'] == DAY
    assert chart['trading_day_distance']['state'] == 'available'
    for row in chart['bars']:
        assert row['trading_days_to_as_of'] == market.days.index(DAY) - market.days.index(row['date'])


def test_missing_source_dates_are_partial_and_reset_daily_indicators(local):
    market, _, published = local
    def missing(payload):
        payload['data']['item'].pop(-3)
        return payload
    adapter = Adapter(market, missing)
    result = collect((market, adapter, published))
    assert result['quality'] == 'partial'
    assert result['coverage']['missing_count'] == 1
    chart = charts.read_sector_chart(market, CODE, DAY)
    assert chart['available'] and chart['bars'][-1]['macd_dif'] is None
    assert chart['indicator_gaps'][-1]['reason']
    assert '不区分' in chart['coverage']['notice']


def test_read_generation_changes_make_session_distances_unknown(local, monkeypatch):
    market, _, _ = local
    collect(local)
    original = charts._read_chart_cache

    def concurrent_publication(*args, **kwargs):
        cached = original(*args, **kwargs)
        market.state['snapshot_id'] = 'another-market-generation'
        return cached

    monkeypatch.setattr(charts, '_read_chart_cache', concurrent_publication)
    chart = charts.read_sector_chart(market, CODE, DAY)
    assert chart['available'] and chart['bars']
    assert chart['trading_day_distance']['state'] == 'unknown'
    assert '代际变化' in chart['trading_day_distance']['reason']
    assert all(row['trading_days_to_as_of'] is None for row in chart['bars'])


def test_short_history_does_not_invent_index_inception_or_pre_window_prices(local):
    market, _, published = local
    def recent(payload):
        payload['data']['item'] = payload['data']['item'][-20:]
        return payload
    result = collect((market, Adapter(market, recent), published))
    assert result['coverage']['observed_count'] == 20 and result['quality'] == 'partial'
    assert result['coverage']['missing_count'] == len(market.days)-20
    chart = charts.read_sector_chart(market, CODE, DAY)
    assert len(chart['bars']) == 20 and chart['bars'][-1]['ma60'] is None
    assert chart['bars'][-1]['macd_dif'] is None


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
def test_missing_volume_amount_propagate_unknown_instead_of_zero_or_json_nan(local, period):
    market, _, published = local
    def nullable(payload):
        payload['data']['item'][-1]['volume'] = None
        payload['data']['item'][-1]['turnover'] = None
        return payload
    result = collect((market, Adapter(market, nullable), published))
    assert result['coverage']['amount_unknown_count'] == result['coverage']['volume_unknown_count'] == 1
    chart = charts.read_sector_chart(market, CODE, DAY, period)
    assert chart['available'] and chart['bars'][-1]['volume'] is None
    assert chart['bars'][-1]['amount'] is None
    assert chart['quality'] == 'partial'
    json.dumps(chart, allow_nan=False)


@pytest.mark.parametrize('fault', ['future', 'outside', 'duplicate', 'wrong_code', 'wrong_period',
                                   'adjusted', 'bad_ohlc', 'wrong_date', 'missing_target', 'empty'])
def test_invalid_provider_cannot_publish_chart_cache(local, fault):
    market, _, published = local
    def invalid(value):
        rows = value['data']['item']
        if fault == 'future': rows[-1]['date_ms'] = charts._midnight('2026-10-21')
        elif fault == 'outside': rows[0]['date_ms'] = charts._midnight('2021-10-19')
        elif fault == 'duplicate': rows.append(dict(rows[-1]))
        elif fault == 'wrong_code': value['data']['thscode'] = '881101.TI'
        elif fault == 'wrong_period': value['data']['interval'] = '1m'
        elif fault == 'adjusted': value['data']['adjust'] = 'qfq'
        elif fault == 'bad_ohlc': rows[-1]['high_price'] = .5
        elif fault == 'wrong_date': rows[-1]['date'] = '2026-10-21'
        elif fault == 'missing_target': rows.pop()
        else: value['data']['item'] = []
        return value
    with pytest.raises(ValueError): collect((market, Adapter(market, invalid), published))
    assert not pointer(local).exists()


def test_freeze_change_or_pause_rejects_before_artifact_accept(local):
    market, adapter, _ = local
    plan = charts.plan_sector_chart(market, CODE, DAY)
    market.state['snapshot_id'] = 'market-new'
    with pytest.raises(ValueError, match='冻结计划'):
        charts.collect_sector_chart(market, plan, adapter)
    assert adapter.calls == []
    plan = charts.plan_sector_chart(market, CODE, DAY)
    def guard():
        if adapter.calls: raise RuntimeError('manual pause')
    with pytest.raises(RuntimeError): charts.collect_sector_chart(market, plan, adapter, guard)
    assert not pointer(local).exists()


@pytest.mark.parametrize('part', ['normalized', 'raw'])
def test_get_and_worker_do_not_trust_corrupt_immutable_artifact(local, part):
    market, adapter, published = local
    collect(local)
    saved = json.loads(pointer(local).read_text())
    normalized = published/'market-supplement'/saved['artifact']
    accepted = json.loads(normalized.read_text())
    path = normalized if part == 'normalized' else published/'market-supplement'/accepted['raw_artifact']
    path.write_text('{}')
    before = len(adapter.calls)
    chart = charts.read_sector_chart(market, CODE, DAY)
    assert not chart['available'] and '校验失败' in chart['unavailable_reason']
    assert len(adapter.calls) == before
    with pytest.raises(ValueError): collect(local)
    assert len(adapter.calls) == before


def test_valid_raw_supports_explicit_historical_projection_with_original_cache_date(local):
    market, adapter, _ = local
    collect(local)
    before = len(adapter.calls)
    older = charts.read_sector_chart(market, CODE, '2026-10-19')
    assert older['available'] and older['as_of'] == '2026-10-19'
    assert older['bars'][-1]['date'] == '2026-10-19'
    assert older['cache_as_of'] == DAY and older['projected_from_later_cache']
    assert len(adapter.calls) == before


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
def test_historical_projection_recomputes_from_raw_without_future_prices(local, period):
    market, _, published = local
    def future_spike(payload):
        last = payload['data']['item'][-1]
        last.update(open_price=99999, high_price=100000, low_price=99998, close_price=99999)
        return payload
    result = collect((market, Adapter(market, future_spike), published))
    day = '2026-10-19'
    rows = [row for row in result['record']['bars'] if row['date'] <= day]
    if period == 'D': expected = enrich_bars(rows, market.calendar())
    elif period == 'W': expected = enrich_bars(aggregate_weekly(rows, market.calendar(), day))
    else: expected = enrich_monthly_bars(aggregate_monthly(rows, market.calendar(), day))
    view = charts.read_sector_chart(market, CODE, day, period)
    assert view['available'] and view['as_of'] == day and view['cache_as_of'] == DAY
    assert view['cache_source_request']['end'] == DAY
    assert view['coverage']['requested_end'] == day and view['coverage']['end'] == day
    assert len(view['bars']) == len(expected)
    for actual, reference in zip(view['bars'], expected):
        for key in ('date', 'open', 'high', 'low', 'close', 'volume', 'amount',
                    'ma5', 'ma60', 'ma250', 'macd_dif', 'macd_dea', 'kdj_k', 'kdj_d', 'kdj_j'):
            assert actual.get(key) == reference.get(key)
    assert all(row['date'] <= day and row['high'] < 99998 for row in view['bars'])
    assert view['raw_sha256'] == result['record']['raw_sha256']


def test_historical_projection_requires_exact_observation_day_not_nearest_price(local):
    market, _, published = local
    def missing_target(payload):
        payload['data']['item'] = [row for row in payload['data']['item']
                                  if row['date_ms'] != charts._midnight('2026-10-19')]
        return payload
    collect((market, Adapter(market, missing_target), published))
    view = charts.read_sector_chart(market, CODE, '2026-10-19')
    assert not view['available'] and view['bars'] == []


def test_exact_historical_cache_is_preferred_over_later_cache(local):
    market, adapter, _ = local
    day = '2026-10-19'
    exact = charts.collect_sector_chart(market, charts.plan_sector_chart(market, CODE, day), adapter)
    collect(local)
    view = charts.read_sector_chart(market, CODE, day)
    assert view['available'] and view['cache_as_of'] == day
    assert not view['projected_from_later_cache']
    assert view['raw_sha256'] == exact['record']['raw_sha256']


def test_historical_projection_does_not_trust_corrupt_later_raw(local):
    market, _, published = local
    result = collect(local)
    (published/'market-supplement'/result['record']['raw_artifact']).write_text('{}')
    view = charts.read_sector_chart(market, CODE, '2026-10-19')
    assert not view['available']


def test_historical_projection_revalidates_original_start_and_keeps_original_receipt(local, monkeypatch):
    market, adapter, _ = local
    monkeypatch.setattr(charts, '_source_today', lambda: '2026-10-21')
    result = collect(local)
    def no_current_clock(): raise AssertionError('read original receipt, not a new source boundary')
    monkeypatch.setattr(charts, '_source_today', no_current_clock)
    view = charts.read_sector_chart(market, CODE, '2026-10-19')
    assert view['available'] and view['coverage']['missing_dates'] == ['2021-10-20']
    assert view['cache_source_request'] == result['record']['source_request']
    assert view['coverage']['requested_start'] == '2021-10-20'
    assert len(adapter.calls) == 1


def test_historical_projection_does_not_reuse_another_board(local):
    market, _, _ = local
    collect(local)
    assert not charts.read_sector_chart(market, '881101.TI', '2026-10-19')['available']


def test_historical_job_create_uses_projection_without_source_or_new_task(local, monkeypatch):
    from app.services import sector_chart_jobs, source_service
    collect(local)
    def forbidden_source(*args): raise AssertionError('historical cache must not construct a provider')
    monkeypatch.setattr(source_service, 'client', forbidden_source)
    result = sector_chart_jobs.create_chart_job(local[0], CODE, '2026-10-19')
    assert result == {'cache_hit': True, 'topic_code': CODE, 'as_of': '2026-10-19'}
    assert not (local[2]/'market-supplement/accepted/fuyao/sector-chart'/('2026-10-19-'+CODE+'.json')).exists()


def test_valid_raw_is_recomputed_even_if_normalized_numbers_were_modified(local):
    market, _, published = local
    original = collect(local)
    saved = json.loads(pointer(local).read_text());path = published/'market-supplement'/saved['artifact']
    accepted = json.loads(path.read_text());accepted['bars'][-1]['close'] = 999999
    path.write_text(json.dumps(accepted));saved['sha256'] = sha256(path.read_bytes()).hexdigest()
    pointer(local).write_text(json.dumps(saved))
    chart = charts.read_sector_chart(market, CODE, DAY)
    assert chart['available'] and chart['bars'][-1]['close'] == original['record']['bars'][-1]['close']


def test_pointer_path_cannot_escape_shared_cache_root(local):
    market, _, _ = local
    collect(local)
    saved = json.loads(pointer(local).read_text());saved['artifact'] = '../escape.json'
    pointer(local).write_text(json.dumps(saved))
    chart = charts.read_sector_chart(market, CODE, DAY)
    assert not chart['available']


def test_pointer_symlink_cannot_read_outside_cache_root(local, tmp_path):
    market, _, _ = local
    collect(local)
    outside = tmp_path/'outside.json';outside.write_text('{}')
    pointer(local).unlink();pointer(local).symlink_to(outside)
    chart = charts.read_sector_chart(market, CODE, DAY)
    assert not chart['available'] and '校验失败' in chart['unavailable_reason']


def test_unsupported_period_is_explicit(local):
    with pytest.raises(ValueError, match='日K'):
        charts.read_sector_chart(local[0], CODE, DAY, '1m')


def test_indicator_gaps_do_not_span_valid_bars_with_same_reason():
    fields = ('macd_dif', 'macd_dea', 'macd_hist', 'kdj_k', 'kdj_d', 'kdj_j')
    def row(day, unknown):
        return {'date': day, 'indicator_warmup_reason': '相同预热原因',
                **{field: None if unknown else 1.0 for field in fields}}
    bars = [row('2026-10-12', True), row('2026-10-13', True),
            row('2026-10-14', False), row('2026-10-15', True), row('2026-10-16', True)]
    gaps = charts._indicator_gaps(bars)
    assert [(gap['start_date'], gap['end_date'], gap['bars']) for gap in gaps] == [
        ('2026-10-12', '2026-10-13', 2), ('2026-10-15', '2026-10-16', 2)]


def test_existing_backup_inventory_includes_single_chart_pointer_and_raw_dependencies(local):
    _, _, published = local
    collect(local)
    script = Path(__file__).resolve().parents[2]/'scripts/backup.py'
    spec = importlib.util.spec_from_file_location('sector_chart_backup_fixture', script)
    backup = importlib.util.module_from_spec(spec);spec.loader.exec_module(backup)
    files = backup.supplement_files(published)
    saved = json.loads(pointer(local).read_text())
    normalized = published/'market-supplement'/saved['artifact']
    accepted = json.loads(normalized.read_text())
    assert str(pointer(local).relative_to(published)) in files
    assert files['market-supplement/'+saved['artifact']] == saved['sha256']
    assert files['market-supplement/'+accepted['raw_artifact']] == accepted['raw_sha256']
    # Backup must refuse corrupt raw dependencies, rather than copying an
    # apparently intact pointer with a broken source-response proof.
    (published/'market-supplement'/accepted['raw_artifact']).write_text('{}')
    with pytest.raises(ValueError, match='checksum'):
        backup.supplement_files(published)
