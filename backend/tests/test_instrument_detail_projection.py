"""Detail projection retains the full-chart quote and independent dated proof."""
from copy import deepcopy
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.analytics.market import MarketData
from app.core import json_clean
from app.routers import platform
from app.services import chart_limits, market_service, stock_memberships


class DetailMarket(MarketData):
    def __init__(self, count=320, symbol='600001.SH', switch=False):
        self.days = []
        day = date(2025, 1, 6)
        while len(self.days) < max(count, 1):
            if day.weekday() < 5:
                self.days.append(day.isoformat())
            day += timedelta(days=1)
        self.symbol = symbol
        self.kind = 'index' if symbol in ('000001.SH', '399001.SZ') else 'stock'
        self.rows = [dict(date=day, open=10+i*.03, high=11+i*.03,
                          low=9+i*.03, close=10.2+i*.03,
                          volume=100+(i % 9)*37, amount=1500+i*30,
                          corporate_action=False, corporate_action_coverage_known=True,
                          source='fixture', price_basis='none', raw_volume=None,
                          optional_quote_metadata={'retained': True})
                     for i, day in enumerate(self.days[:count])]
        self.switch = switch
        self.status_reads = 0
        self.raw_reads = []
        self.breadth_reads = []
        self.breadth = [dict(date=day, up=i+1, down=3, flat=2)
                        for i, day in enumerate(self.days)]
        self.action_rows = []
        self.ref = {'abnormal': [{'thscode': symbol, 'monitor_label': '来源监控'}]}

    def status(self):
        self.status_reads += 1
        return {'ready': True, 'data_end': self.days[-1], 'quality': 'partial',
                'snapshot_id': 'changed' if self.switch and self.status_reads > 1 else 'fixture',
                'warnings': ['来源范围保留']}

    def calendar(self): return list(self.days)

    def bars(self, symbol, as_of=None):
        assert symbol == self.symbol
        self.raw_reads.append(as_of)
        return [deepcopy(row) for row in self.rows if not as_of or row['date'] <= as_of]

    def instrument(self, symbol):
        assert symbol == self.symbol
        return {'instrument_id': symbol, 'code': symbol[:6], 'name': '合成测试',
                'kind': self.kind, 'list_date': '2020-01-01'}

    def actions(self, symbol, start=None, end=None):
        assert symbol == self.symbol
        return [deepcopy(row) for row in self.action_rows if not end or row['date'] <= end]

    def topics_data(self):
        return [{'topic_id': 'dated-topic', 'name': '当前目录', 'instrument_ids': [self.symbol]}]

    def reference(self): return deepcopy(self.ref)

    def breadth_history(self, as_of=None):
        self.breadth_reads.append(as_of)
        return [dict(row) for row in self.breadth if not as_of or row['date'] <= as_of]


def same_tail(market, as_of=None):
    """The pre-change full chart is the oracle, not another tail calculation."""
    original = deepcopy(market.rows)
    market.status_reads = 0
    full = MarketData.chart(market, market.symbol, as_of=as_of)
    market.status_reads = 0
    compact = market.detail_chart(market.symbol, as_of=as_of)
    assert json_clean(compact['bars']) == json_clean(full['bars'][-2:])
    for field in ('instrument', 'snapshot_id', 'period', 'as_of', 'price_basis', 'trading_day_distance'):
        assert compact[field] == full[field]
    assert market.rows == original
    assert set(compact) == {'instrument', 'snapshot_id', 'period', 'as_of', 'price_basis',
                            'bars', 'trading_day_distance'}
    return compact


@pytest.mark.parametrize('count', [0, 1, 2, 9, 34, 35, 36, 250, 320])
def test_complete_seed_keeps_every_last_quote_field_and_warmup_boundary(count):
    result = same_tail(DetailMarket(count))
    assert len(result['bars']) == min(count, 2)
    if count:
        assert result['bars'][-1]['indicator_warmup_bars'] == count
        assert result['bars'][-1]['optional_quote_metadata'] == {'retained': True}
        assert result['bars'][-1]['raw_volume'] is None
        assert 'chart_high_change_pct' in result['bars'][-1]
        assert 'display_indicator_warmup_reason' in result['bars'][-1]
        assert (result['bars'][-1]['macd_dif'] is None) == (count < 35)


@pytest.mark.parametrize('mode', ['gap', 'missing_tail', 'action', 'unknown_action', 'zero_volume',
                                  'nullable_optional', 'duplicate_calendar', 'missing_anchor'])
def test_missing_quotes_resets_nulls_and_unverified_calendar_keep_exact_fields(mode):
    market = DetailMarket()
    if mode == 'gap': del market.rows[-4]
    elif mode == 'missing_tail': del market.rows[-2:]
    elif mode == 'action':
        market.rows[-7]['corporate_action'] = True
        market.action_rows = [{'date': market.days[-7], 'dividend_per_share': .2,
                               'per_share_bonus': 0, 'allotment_ratio': 0, 'allotment_price': 0}]
    elif mode == 'unknown_action': market.rows[-1]['corporate_action_coverage_known'] = False
    elif mode == 'zero_volume': market.rows[-1]['volume'] = 0
    elif mode == 'nullable_optional': market.rows[-1].update(amount=None, source=None, raw_volume=None)
    elif mode == 'duplicate_calendar': market.days.append(market.days[-1])
    elif mode == 'missing_anchor': market.calendar = lambda: market.days[:-1]
    result = same_tail(market)
    if mode == 'missing_tail':
        assert result['bars'][-1]['trading_days_to_as_of'] == 2
        assert result['bars'][-1]['date'] != result['as_of']


def test_historical_anchor_never_uses_future_quotes_or_events():
    market = DetailMarket()
    anchor = market.days[200]
    before = same_tail(market, anchor)
    market.rows[-1].update(close=1e6, high=1e6, corporate_action=True)
    market.action_rows = [{'date': market.days[-1], 'dividend_per_share': 999999,
                           'per_share_bonus': 0, 'allotment_ratio': 0, 'allotment_price': 0}]
    assert same_tail(market, anchor) == before
    assert all(day == anchor for day in market.raw_reads)
    assert before['bars'][-1]['date'] == anchor


@pytest.mark.parametrize('symbol', ['000001.SH', '399001.SZ'])
@pytest.mark.parametrize('breadth_state', ['available', 'missing', 'empty_sample'])
def test_index_retains_original_advancers_only_for_the_shanghai_index(symbol, breadth_state):
    market = DetailMarket(symbol=symbol)
    if breadth_state == 'missing': market.breadth = []
    elif breadth_state == 'empty_sample': market.breadth[-1].update(up=0, down=0, flat=0)
    result = same_tail(market)
    if symbol == '000001.SH':
        expected = len(market.days) if breadth_state == 'available' else None
        assert result['bars'][-1]['advancers'] == expected
        assert len(market.breadth_reads) == 2
    else:
        assert 'advancers' not in result['bars'][-1]
        assert not market.breadth_reads


def test_generation_change_keeps_distance_unknown_instead_of_binding_new_calendar():
    result = same_tail(DetailMarket(switch=True))
    assert result['trading_day_distance']['state'] == 'unknown'
    assert all(row['trading_days_to_as_of'] is None for row in result['bars'])


def test_projection_skips_unused_chart_work_but_retains_the_entire_indicator_seed(monkeypatch):
    import app.analytics.market as module
    from app.analytics import chart_annotation_projection, chart_indicators, waves
    market = DetailMarket()
    def forbidden(*args, **kwargs): raise AssertionError('unused full-chart work')
    for name in ('prices', 'actions', 'chart'):
        monkeypatch.setattr(market, name, forbidden)
    monkeypatch.setattr(chart_annotation_projection, 'annotation_projection_evidence', forbidden)
    monkeypatch.setattr(chart_indicators, 'indicator_gaps', forbidden)
    monkeypatch.setattr(chart_indicators, 'quote_gaps', forbidden)
    monkeypatch.setattr(waves, 'candidate_waves', forbidden)
    enrich = module.enrich_bars
    calls = []
    def seeded(rows, calendar, **options):
        calls.append((len(rows), len(calendar), options))
        return enrich(rows, calendar, **options)
    monkeypatch.setattr(module, 'enrich_bars', seeded)
    result = market.detail_chart(market.symbol)
    assert len(result['bars']) == 2
    assert calls == [(320, 320, {'tail': 2, 'include_chart_extremes': True})]
    assert market.raw_reads == [market.days[-1]]


def test_invalid_source_read_is_not_hidden_by_projection(monkeypatch):
    market = DetailMarket()
    def invalid(*args, **kwargs): raise ValueError('published source SHA mismatch')
    monkeypatch.setattr(market, 'bars', invalid)
    for path in (market.detail_chart, market.chart):
        with pytest.raises(ValueError, match='source SHA mismatch'):
            path(market.symbol)


@pytest.fixture
def detail_client(monkeypatch):
    market = DetailMarket()
    active = [market]
    status = {'data_end': market.days[-1], 'snapshot_id': 'fixture', 'warnings': ['来源范围保留']}
    monkeypatch.setattr(market_service, 'context', lambda as_of=None: (active[0], status, as_of or status['data_end']))
    monkeypatch.setattr(stock_memberships, 'stock_membership_fields',
                        lambda m, symbol: {'current_topics': [{'name': '当次业务归属'}],
                                           'current_topics_scope': '本次直接读取'})
    calls = []
    def supplement(snapshot, *, dates, fields):
        calls.append((snapshot, list(dates), tuple(fields)))
        return {}
    monkeypatch.setattr(chart_limits, 'load_supplement', supplement)
    app = FastAPI(); app.include_router(platform.router)
    return market, active, calls, TestClient(app)


class FullChartOnly:
    """Exercise the original detail route through its compatibility fallback."""
    detail_chart = None
    def __init__(self, market): self.market = market
    def __getattr__(self, name): return getattr(self.market, name)


@pytest.mark.parametrize('scenario', ['current', 'historical', 'missing_tail', 'earth_to_sky',
                                      'one_price', 'generation', 'index', 'empty'])
def test_complete_detail_http_payload_matches_old_full_chart_and_focused_patterns(detail_client, scenario):
    market, active, calls, client = detail_client
    anchor = market.days[-1]
    if scenario == 'historical': anchor = market.days[200]
    elif scenario == 'missing_tail': del market.rows[-1]
    elif scenario == 'empty': market.rows.clear()
    elif scenario == 'generation': market.switch = True
    elif scenario == 'index': market.symbol = '000001.SH'; market.kind = 'index'
    elif scenario in ('earth_to_sky', 'one_price'):
        market.rows[-2].update(open=10, high=10, low=10, close=10)
        market.rows[-1].update(open=11, high=11, low=9 if scenario == 'earth_to_sky' else 11, close=11)
        market.ref.update(pool_trade_date=anchor,
                          limitup=[{'thscode': market.symbol, 'date': anchor, 'last_price': 11}],
                          pool_coverage={'limitup': {'complete': True}})
    active[0] = FullChartOnly(market)
    market.status_reads = 0
    expected = client.get('/api/v1/instruments/' + market.symbol, params={'as_of': anchor})
    assert expected.status_code == 200, expected.text
    active[0] = market
    market.status_reads = 0
    actual = client.get('/api/v1/instruments/' + market.symbol, params={'as_of': anchor})
    assert actual.status_code == 200, actual.text
    assert actual.json() == expected.json()
    payload = actual.json()
    assert payload['data_date'] == anchor
    if scenario in ('earth_to_sky', 'one_price'):
        kind = 'earth_to_sky' if scenario == 'earth_to_sky' else 'one_price_limit_up'
        assert payload['pattern_status'][kind]['state'] is True
        assert payload['pattern_events'] and payload['pattern_date'] == anchor
    elif scenario in ('missing_tail', 'empty'):
        assert payload['pattern_date'] is None and payload['pattern_events'] == []
    if scenario == 'index':
        assert payload['pattern_coverage']['applicable'] is False
        assert not calls
    else:
        assert all(dates == [anchor] for _, dates, _ in calls)
        assert all(set(fields) == {'annual_pools', 'annual_down_pools', 'public_pools'}
                   for _, _, fields in calls)
