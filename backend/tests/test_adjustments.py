import pytest
from app.analytics.adjustments import action_plan,forward_prices,compare_forward


def bars():return [{'date':day,'open':price,'high':price+1,'low':price-1,'close':price,'volume':100,'amount':1000} for day,price in [('2026-01-05',10),('2026-01-06',11),('2026-01-07',10),('2026-01-08',12)]]
def action(day='2026-01-07',cash=1,bonus=0,rights=0,rights_price=0):return {'date':day,'dividend_per_share':cash,'per_share_bonus':bonus,'allotment_ratio':rights,'allotment_price':rights_price,'currency':'CNY'}


def test_reference_factor_and_affine_are_distinct_but_both_anchor_last_bar():
    rows=bars();plan=action_plan(rows,[action()],rows[-1]['date'])
    assert plan['events'][0]['theoretical_ex_price']==10
    assert plan['events'][0]['factor']==pytest.approx(10/11)
    ratio=forward_prices(rows,plan)
    affine=forward_prices(rows,plan,'event_affine')
    assert ratio[0]['close']==pytest.approx(100/11) and affine[0]['close']==9
    assert ratio[-1]['close']==12 and affine[-1]['close']==12
    assert affine[0]['volume']==100 and affine[0]['amount']==1000


def test_bonus_rights_combined_formula_and_event_day_unchanged():
    rows=bars();plan=action_plan(rows,[action(cash=.2,bonus=.1,rights=.2,rights_price=3)],rows[-1]['date'])
    assert plan['events'][0]['theoretical_ex_price']==pytest.approx((11-.2+3*.2)/1.3)
    derived=forward_prices(rows,plan,'event_affine')
    assert derived[0]['close']==pytest.approx((10-.2+3*.2)/1.3)
    assert derived[2]['close']==10


def test_future_actions_do_not_leak_into_earlier_anchor():
    rows=bars();plan=action_plan(rows,[action('2026-01-08')],'2026-01-07')
    assert plan['future_excluded']==['2026-01-08'] and not plan['events']
    assert forward_prices(rows,plan)[0]['close']==10


@pytest.mark.parametrize('events',[ [action(),action()], [action(bonus=-.1)], [action(cash=100)], [action(rights=float('nan'))], [action(day='2026-01-09')]])
def test_duplicate_invalid_missing_events_are_quarantined(events):
    plan=action_plan(bars(),events,'2026-01-09')
    assert plan['usable'] is False
    with pytest.raises(ValueError):forward_prices(bars(),plan)


def test_missing_previous_market_quote_is_not_guessed():
    rows=bars();rows.pop(1)
    plan=action_plan(rows,[action()],'2026-01-08',[b['date'] for b in bars()])
    assert not plan['usable'] and '前一市场交易日' in plan['quarantined'][0]['reason']


def test_comparison_checks_all_prices_and_date_intersection():
    rows=bars();assert compare_forward(rows,rows)['matched'] is True
    assert compare_forward(rows,rows[:-1])['matched'] is False
    altered=[dict(r) for r in rows];altered[0]['high']+=.01
    assert compare_forward(rows,altered)['outside_tolerance']==1
