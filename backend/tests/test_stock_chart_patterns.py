"""The detail badge must be strictly selected-day data, not the latest old candle."""
from copy import deepcopy

from app.services import stock_chart_patterns as service


def test_detail_uses_one_focused_observation_day_and_keeps_original(monkeypatch):
    chart = {'instrument': {'kind': 'stock'}, 'period': 'D', 'as_of': '2026-01-09', 'snapshot_id': 'test-generation',
             'bars': [{'date': '2026-01-08', 'close': 9}, {'date': '2026-01-09', 'close': 10}]}
    original = deepcopy(chart)
    event = {'kind': 'one_price_limit_up', 'date': '2026-01-09', 'snapshot_id': 'test-generation'}
    def focused(market, payload, *, focus_day):
        assert market is sentinel and focus_day == '2026-01-09'
        assert payload['period'] == 'D' and payload['as_of'] == focus_day
        assert payload['bars'] == [chart['bars'][-1]]
        return {'bars': [{'pattern_events': [event], 'pattern_status': {'one_price_limit_up': {'state': True}}}],
                'pattern_coverage': {'scope': 'observation_day'}}
    sentinel = object()
    monkeypatch.setattr(service, 'enrich_chart_limits', focused)
    result = service.stock_pattern_fields(sentinel, chart, '2026-01-09')
    assert result['pattern_date'] == result['pattern_as_of'] == '2026-01-09'
    assert result['pattern_events'] == [event]
    assert result['pattern_coverage']['scope'] == 'observation_day'
    assert chart == original


def test_detail_does_not_carry_old_pattern_into_missing_market_day(monkeypatch):
    monkeypatch.setattr(service, 'enrich_chart_limits', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('must not evaluate old quote')))
    chart = {'instrument': {'kind': 'stock'}, 'bars': [{'date': '2026-01-08', 'pattern_events': [{'kind': 'one_price_limit_up'}]}]}
    result = service.stock_pattern_fields(object(), chart, '2026-01-09')
    assert result['pattern_date'] is None and result['pattern_as_of'] == '2026-01-09'
    assert result['pattern_events'] == []
    assert all(item['state'] is None and item['status'] == 'unknown' for item in result['pattern_status'].values())


def test_index_detail_does_not_load_individual_limit_pools(monkeypatch):
    monkeypatch.setattr(service, 'enrich_chart_limits', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('index has no shape')))
    result = service.stock_pattern_fields(object(), {'instrument': {'kind': 'index'}, 'bars': [{'date': '2026-01-09'}]}, '2026-01-09')
    assert result['pattern_events'] == [] and result['pattern_coverage']['applicable'] is False
    assert all(item['status'] == 'not_applicable' for item in result['pattern_status'].values())
