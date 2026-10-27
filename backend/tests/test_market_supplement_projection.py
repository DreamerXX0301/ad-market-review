"""Capability-scoped readers retain all required dates and source checks.

These fixtures publish actual content-addressed objects in pytest's private
directory. They never use a running workspace, remote provider or credentials.
"""
from copy import deepcopy
import json

import pytest

from app.services import market_service as service
from app.services import market_supplement as supplement
from app.services import sector_activity, sector_history


A, B = '600001.SH', '600002.SH'
DAYS = ['2025-12-30', '2025-12-31', '2026-01-05', '2026-01-06',
        '2026-01-07', '2026-01-08', '2026-01-09', '2026-01-12', '2026-01-13']
DAY, ANCHOR = DAYS[-2:]
CODE = '881001.TI'
POOL_FIELDS = ('public_pools', 'annual_pools', 'annual_down_pools')


def pool(day, symbols):
    return {'date': day, 'source': 'fuyao', 'complete': True,
            'pagination_complete': True, 'ambiguous_code_rows': 0,
            'raw_codes': [symbol[:6] for symbol in symbols], 'rejected_codes': [],
            'items': [{'instrument_id': symbol, 'thscode': symbol,
                       'date': day, 'last_price': 10., 'continue_day_cnt': 3,
                       'note': '仅为测试的原始证据' * 800} for symbol in symbols]}


def index_record(day, previous):
    row = {'topic_id': 'fuyao-' + CODE, 'topic_code': CODE, 'thscode': CODE,
           'name': '测试行业', 'kind': 'industry', 'source': 'fuyao',
           'date': day, 'quote_date': day, 'previous_date': previous,
           'close': 105., 'previous_close': 100., 'change_pct': 5.,
           'amount': 300., 'normalization_version': sector_history.NORMALIZATION_VERSION,
           'request_signature': sector_history.request_signature(CODE, day, previous)}
    return {'date': day, 'source': 'fuyao', 'complete': True, 'items': [row],
            'failures': [], 'coverage': {'expected_previous_date': previous},
            'normalization_version': sector_history.NORMALIZATION_VERSION}


class LocalMarket:
    def status(self):
        return {'ready': True, 'snapshot_id': 'market-projection-fixture',
                'data_begin': DAYS[0], 'data_end': ANCHOR, 'quality': 'partial'}

    def calendar(self):
        return list(DAYS)

    def reference(self):
        return {}

    def panel(self, day):
        return [{'instrument_id': symbol, 'code': symbol[:6], 'name': symbol,
                 'date': day, 'close': 10., 'volume': 100., 'amount': 1000.,
                 'change_pct': 1., 'limit_up': None, 'topics': []} for symbol in (A, B)]


@pytest.fixture
def published(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR'):
        monkeypatch.delenv(key, raising=False)
    root = tmp_path / 'published' / 'market-supplement'
    history = {day: index_record(day, previous) for previous, day in zip(DAYS, DAYS[1:])}
    source = {'version': supplement.VERSION, 'snapshot_id': 'market-projection-fixture',
              'target_date': ANCHOR, 'quality': 'partial',
              'annual_pools': {day: pool(day, [A]) for day in DAYS if day != DAYS[4]},
              'annual_down_pools': {day: pool(day, [B]) for day in DAYS},
              'public_pools': {DAY: pool(DAY, [A])},
              'sector_index_history': history,
              'fund_flows': {ANCHOR: {'date': ANCHOR, 'source': 'eastmoney-public',
                  'complete': True, 'items': [{'topic_code': 'BK0001', 'date': ANCHOR, 'net_inflow': 99.}],
                  'history': [{'topic_code': 'BK0001', 'date': day, 'net_inflow': 123.} for day in DAYS],
                  'coverage': {'directory_count': 1, 'accepted_count': 1}, 'failures': []}}}
    cache = supplement.SupplementCache(root)
    cache.publish(source)
    market = LocalMarket()
    monkeypatch.setattr(service, 'context', lambda day=None: (market, market.status(), day or ANCHOR))
    monkeypatch.setattr(service, 'resolve_reference', lambda *_: (
        {'topics': []}, {'selected_date_available': False, 'notice': '测试无历史成员',
                         'available_dates': [], 'source_snapshot_day': None}))
    sector_activity.clear_sector_activity_cache()
    yield root, source, cache
    sector_activity.clear_sector_activity_cache()


def object_path(root, field, day, *, nested=False):
    head = json.loads((root / 'head.json').read_text())
    index = root / head['artifact']
    pointer = json.loads(index.read_text())[field][day]
    path = root / pointer['record_ref']
    if nested:
        path = root / json.loads(path.read_text())['__supplement_fields_v1']['items']['record_ref']
    return path


@pytest.mark.parametrize('day', [DAYS[2], DAY])
def test_pool_projection_keeps_complete_panel_rows_and_all_retained_pool_dates(published, day):
    _, source, _ = published
    full = service.panel(day)
    selected = service.panel(day, supplement_fields=POOL_FIELDS)
    assert selected[4] == full[4]
    assert selected[3]['_supplement'] == {key: value for key, value in source.items()
                                       if key not in ('fund_flows', 'sector_index_history')}
    assert service.dated_events(selected[3], day) == service.dated_events(full[3], day)
    coverage = service.data_coverage('ladder', full[1], day, full[3], DAYS)
    assert service.data_coverage('ladder', selected[1], day, selected[3], DAYS) == coverage
    assert DAYS[0] in coverage['available_dates']  # Not restricted to T/T-1 or this year.
    rows = {row['instrument_id']: row for row in selected[4]}
    assert rows[A]['limit_up_ytd_observed'] > 0
    if day == DAY:
        assert rows[A]['limit_up_ytd'] is rows[B]['limit_up_ytd'] is None
        assert rows[A]['limit_up_ytd_coverage']['complete'] is False
        assert rows[B]['limit_up_ytd_observed'] == 0  # Unknown exact count, known lower bound.
    else:
        assert rows[A]['limit_up_ytd'] == 1 and rows[B]['limit_up_ytd'] == 0
    assert selected[3]['_supplement_cache_source'][1] != full[3]['_supplement_cache_source'][1]


@pytest.mark.parametrize('field', ['fund_flows', 'sector_index_history'])
@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_unused_capability_failure_does_not_clear_verified_pool_panel(published, field, damage):
    root, _, _ = published
    before = service.panel(DAY, supplement_fields=POOL_FIELDS)
    path = object_path(root, field, ANCHOR)
    if damage == 'missing':
        path.unlink()
    else:
        path.write_text('{}')
    # This is an intentional capability-isolation improvement: the legacy
    # complete-document reader fails while the required pool evidence survives.
    assert service.panel(DAY)[3]['_supplement_cache_source'] is None
    after = service.panel(DAY, supplement_fields=POOL_FIELDS)
    assert after[4] == before[4]
    assert after[3]['_supplement'] == before[3]['_supplement']
    assert after[3]['_supplement_cache_source'][1] == before[3]['_supplement_cache_source'][1]


@pytest.mark.parametrize('damage', ['pool', 'nested_payload', 'index'])
def test_required_pool_or_index_failure_after_warm_load_stays_unknown(published, damage):
    root, _, _ = published
    assert service.panel(DAY, supplement_fields=POOL_FIELDS)[3]['_supplement_cache_source']
    if damage == 'index':
        path = root / json.loads((root / 'head.json').read_text())['artifact']
    else:
        path = object_path(root, 'annual_pools', DAY, nested=damage == 'nested_payload')
    path.write_text('{}')
    result = service.panel(DAY, supplement_fields=POOL_FIELDS)
    assert result[3]['_supplement_cache_source'] is None
    assert result[3]['_supplement']['quality'] == 'unknown'
    assert result[3]['_supplement']['failure_reason']
    assert all(row['limit_up'] is None and row['limit_up_ytd'] is None for row in result[4])


def test_sector_page_retains_seven_days_and_full_descriptor_dates_without_fund_data(published, monkeypatch):
    root, source, _ = published
    with monkeypatch.context() as legacy:
        legacy.setattr(service, '_SECTOR_SUPPLEMENT_FIELDS', None)
        before = service.market_page('sectors', DAY)
    sector_activity.clear_sector_activity_cache()
    object_path(root, 'fund_flows', ANCHOR).unlink()
    after = service.market_page('sectors', DAY)
    assert after == before
    assert after['quote_coverage']['available_dates'] == sorted(source['sector_index_history'])
    activity = after['items'][0]['sector_activity_7d']['change_pct']
    assert activity['count'] == 7 and activity['dates'] == DAYS[1:-1]
    assert after['sector_activity_7d']['window_start'] == DAYS[1]


def test_flow_keeps_later_anchor_history_and_sector_fallback(published):
    _, source, cache = published
    flow = service.market_page('flow', DAY)
    assert flow['items'][0]['net_inflow'] == 123.
    assert flow['coverage']['historical_series_anchor'] == ANCHOR
    assert flow['items'][0]['date'] == DAY
    assert flow['summary']['net_flow_available'] is True
    changed = deepcopy(source)
    changed['fund_flows'] = {}
    cache.publish(changed)
    fallback = service.market_page('flow', DAY)
    assert fallback['items'][0]['quote_date'] == DAY
    assert fallback['items'][0]['change_pct'] == 5.
    assert fallback['items'][0]['net_inflow'] is None
    assert fallback['summary']['net_flow_available'] is False
    assert fallback['items'][0]['sector_activity_7d']['change_pct']['count'] == 7
