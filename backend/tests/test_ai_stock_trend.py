from datetime import date, timedelta
import json

import pytest

from app.analytics.market import MarketData
from app.services import ai_read_tools, ai_stock_trend as trend


SYMBOL = '600001.SH'
DAYS = [(date(2026, 1, 1) + timedelta(days=offset)).isoformat() for offset in range(70)]
DAY = DAYS[-1]
STATUS = {'snapshot_id': 'market-test', 'data_end': DAY}
IDENTITY = {'instrument_id': SYMBOL, 'name': '合成证券'}


class Market:
    def __init__(self, **chart_changes):
        self.chart_changes = chart_changes
        self.history_requests = []

    def calendar(self):
        return DAYS

    def reference(self):
        return {'pool_trade_date': DAY,
                'limitup': [{'thscode': SYMBOL, 'date': DAY, 'last_price': 10.3}]}

    def chart(self, symbol, period, day, basis, *, indicator_continuity, ai_projection):
        assert (symbol, day, basis, indicator_continuity, ai_projection) == (SYMBOL, DAY, 'none', 'quotes', True)
        return {'instrument': IDENTITY, 'as_of': day, 'period': period, 'snapshot_id': STATUS['snapshot_id'],
                'price_basis': 'none', 'indicator_continuity': 'quotes' if period == 'D' else 'market_sessions',
                'bars': [{'date': target, 'open': 10.1, 'high': 10.4, 'low': 10.0, 'close': 10.3,
                          'volume': 12345678, 'amount': 123456789.12345, 'ma20': 10.2345678,
                          'change_pct': 2.4, 'price_continuity': True,
                          'complete': target != DAY, 'week_closed': target != DAY, 'month_closed': target != DAY,
                          'weekly_quote_coverage_complete': True, 'monthly_quote_coverage_complete': True,
                          'weekly_missing_market_sessions': 0, 'monthly_missing_market_sessions': 0} for target in DAYS],
                **self.chart_changes}

    def history_for_instruments(self, symbols, *, start, end):
        self.history_requests.append((symbols, start, end))
        return {SYMBOL: [{'date': end, 'instrument_id': SYMBOL, 'close': 10.3, 'volume': 100}]}


@pytest.fixture
def local(monkeypatch):
    calls = []
    def supplement(snapshot, *, dates, fields):
        calls.append((snapshot, dates, fields))
        return {'snapshot_id': snapshot}
    monkeypatch.setattr(trend, 'load_supplement', supplement)
    topics = [{'topic_id': 'fuyao-881001.TI', 'topic_code': '881001.TI', 'kind': 'industry',
               'name': '合成行业', 'observed_date': DAYS[-2], 'instrument_ids': [SYMBOL]}]
    monkeypatch.setattr(trend, 'resolve_reference', lambda *_: ({'topics': topics},
                        {'source_snapshot_id': 'topic-test', 'source_snapshot_day': DAY}))
    monkeypatch.setattr(trend, 'sector_prices_for_date', lambda *_: {'items': [
        {'topic_id': 'fuyao-881001.TI', 'topic_code': '881001.TI', 'kind': 'industry',
         'quote_date': DAY, 'previous_date': DAYS[-2], 'change_pct': 1.2, 'source': 'fuyao'}]})
    return calls


def envelope():
    return ai_read_tools._result('read_stock_trend', DAY)


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
def test_bounded_chart_projection_uses_selected_source_and_verified_events(local, period):
    market = Market()
    result = envelope()
    trend.project_stock_trend(result, market, STATUS, DAY, IDENTITY, period, 60)
    data = result['evidence'][0]['data']
    assert data['period'] == period and data['price_basis'] == 'none'
    assert data['returned_bars'] == 60 and data['bars'][0][0] == DAYS[10]
    last_bar = dict(zip(data['bar_columns'], data['bars'][-1]))
    if period != 'D':
        complete, closed, coverage, missing = trend.PERIOD_COVERAGE[period]
        assert last_bar[complete] is False and last_bar[closed] is False
        assert last_bar[coverage] is True and last_bar[missing] == 0
    assert data['recent_indicators'][-1]['ma20'] == 10.2345678
    assert data['recent_indicators'][-1]['kdj_k'] is None
    assert data['classification']['items'][0]['index_comparison']['change_pct'] == 1.2
    paired = data['classification']['items'][0]['index_comparison']
    assert paired.get('stock_minus_index_percentage_points') == (1.2 if period == 'D' else None)
    limits = data['closing_limit_events']
    assert limits['items'] == [{'date': DAY, 'event': '收盘涨停'}]
    assert limits['up_unknown'] == 29 and limits['down_unknown'] == 30
    assert market.history_requests == [([SYMBOL], DAYS[-30], DAY)]
    assert local == [(STATUS['snapshot_id'], DAYS[-30:], trend.SUPPLEMENT_FIELDS)]
    bounded = ai_read_tools._bounded(result)
    assert len(json.dumps(bounded, ensure_ascii=False).encode()) < 24 * 1024
    assert len(bounded['evidence'][0]['data']['bars']) == 60


def test_rejects_mismatched_market_generation_and_denied_chart(local):
    result = envelope()
    with pytest.raises(ValueError, match='发布代际'):
        trend.project_stock_trend(result, Market(snapshot_id='wrong'), STATUS, DAY, IDENTITY, 'D', 5)
    trend.project_stock_trend(result, Market(ai_processing_allowed=False), STATUS, DAY, IDENTITY, 'D', 5)
    assert result['evidence'] == [] and result['gaps'][0]['capability'] == 'source_permission'
    assert local == []


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
def test_raw_row_permission_is_checked_before_aggregation_or_indicator_warmup(period):
    class RestrictedMarket:
        def status(self):
            return STATUS

        def calendar(self):
            return DAYS

        def bars(self, *_):
            return [{'date': DAYS[0], 'egress_allowed': False}, {'date': DAY}]

    with pytest.raises(ValueError, match='禁止外发'):
        MarketData.chart(RestrictedMarket(), SYMBOL, period, DAY, 'none', ai_projection=True)
