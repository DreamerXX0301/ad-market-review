"""Currency deltas must use adjacent sessions and a comparable retained scope."""
import pytest
from app.services.market_amount import amount_change_metadata

CALENDAR = ['2026-10-16', '2026-10-19', '2026-10-20']


def history(current=14600e8, previous=14412e8):
    return [{'date':day, 'amount':value, 'scope':'沪深', 'source':'fixture'}
            for day,value in zip(CALENDAR, (100e8, previous, current))]


@pytest.mark.parametrize('current,previous,delta', [(14600e8,14412e8,188e8),(14200e8,14412e8,-212e8),(14412e8,14412e8,0),(100e8,0,100e8)])
def test_signed_currency_delta_and_zero(current, previous, delta):
    result=amount_change_metadata(current,CALENDAR[-1],CALENDAR,history(current,previous))
    assert result['amount_change']==delta
    assert result['amount_previous_date']=='2026-10-19'
    assert result['amount_change_reason'] is None
    if previous==0:assert result['amount_change_pct'] is None


@pytest.mark.parametrize('mode',['no_previous','duplicate_previous','wrong_scope','wrong_source','nan','negative','bool','inconsistent_current','invalid_current'])
def test_missing_or_incompatible_turnover_never_uses_other_dates(mode):
    rows=history();current=14600e8
    if mode=='no_previous':rows.pop(1)
    elif mode=='duplicate_previous':rows.append(dict(rows[1]))
    elif mode=='wrong_scope':rows[1]['scope']='沪深京'
    elif mode=='wrong_source':rows[1]['source']='another'
    elif mode=='nan':rows[1]['amount']=float('nan')
    elif mode=='negative':rows[1]['amount']=-1
    elif mode=='bool':rows[1]['amount']=True
    elif mode=='inconsistent_current':rows[-1]['amount']=10
    else:current=None
    result=amount_change_metadata(current,CALENDAR[-1],CALENDAR,rows)
    assert result['amount_change'] is None and result['amount_change_reason']
    assert result['amount_previous_date']=='2026-10-19'


def test_first_retained_session_is_not_compared_to_future():
    result=amount_change_metadata(100e8,CALENDAR[0],CALENDAR,history())
    assert result['amount_change'] is None and result['amount_previous_date'] is None
