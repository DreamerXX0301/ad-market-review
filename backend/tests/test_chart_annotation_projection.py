"""Projection bounds come from the same local daily/action generation."""
from copy import deepcopy

import pytest

from app.analytics.chart_annotation_projection import annotation_projection_evidence
from app.analytics.market import MarketData
from test_market_compact_reads import retained_market


def test_explicit_bounds_require_known_action_source_and_do_not_fill_quote_gaps():
    rows = [{'date': day, 'corporate_action_coverage_known': True}
            for day in ('2026-10-20', '2026-10-22')]
    saved = deepcopy(rows)
    result = annotation_projection_evidence(rows, '2026-10-23', 'market-proof')
    assert result['available'] is True
    assert (result['start_date'], result['end_date']) == ('2026-10-20', '2026-10-23')
    assert result['snapshot_id'] == 'market-proof'
    assert rows == saved
    for invalid in ([], [{**rows[0], 'corporate_action_coverage_known': False}],
                    [{'date': rows[0]['date']}], rows + [rows[-1]],
                    [{'date': '2026-10-24', 'corporate_action_coverage_known': True}]):
        assert annotation_projection_evidence(invalid, '2026-10-23', 'market-proof')['available'] is False
    assert annotation_projection_evidence(rows, '2026-02-30', 'market-proof')['available'] is False
    assert annotation_projection_evidence(rows, '2026-10-23', None)['available'] is False


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
@pytest.mark.parametrize('basis', ['none', 'forward'])
def test_real_market_chart_reuses_daily_evidence_for_every_display_period(retained_market, monkeypatch, period, basis):
    root, days, symbols = retained_market
    market = MarketData(root)
    monkeypatch.setattr(market, '_daily_frame', lambda: pytest.fail('no all-market read'))
    result = market.chart(symbols[0], period=period, price_basis=basis)
    proof = result['annotation_projection_evidence']
    assert proof['available'] is True
    assert proof['snapshot_id'] == result['snapshot_id']
    assert (proof['start_date'], proof['end_date']) == (days[0], days[-1])
    assert result['period'] == period
    assert market._frame is None


def test_missing_local_actions_file_does_not_turn_empty_events_into_no_action_proof(retained_market):
    root, _, symbols = retained_market
    (root/'published/fixture/corporate-actions.parquet').unlink()
    result = MarketData(root).chart(symbols[0], price_basis='forward')
    assert result['annotation_projection_evidence']['available'] is False
    assert result['annotation_projection_evidence']['reason']
