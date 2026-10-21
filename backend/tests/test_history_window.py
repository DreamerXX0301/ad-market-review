"""Explicit windows and source-derived calendar guards, never real sources."""
from copy import deepcopy

import pandas as pd
import pytest

from app.services import history_window as windows
from app.services.three_year_calendar import INDEX_CODES, _midnight, validate_history_calendar


@pytest.mark.parametrize('value,years', [('near-one-year',1),('near-three-years',3),('near-five-years',5),('near-ten-years',10)])
def test_only_named_frozen_windows_are_normalized(value, years):
    assert windows.resolve_initial_window({'window':value})['years']==years
    expected='2023-03-01' if years==1 else f'{2024-years}-02-28'
    assert windows.window_start('2024-02-29',value)==expected


@pytest.mark.parametrize('params', [None, {}])
def test_default_metadata_is_one_shared_contract(params):
    default=windows.resolve_initial_window(params)
    status=windows.initialization_window_status()
    assert default['window']==status['default_window']==windows.DEFAULT_WINDOW
    assert status['default_years']==default['years']
    retained=windows.initialization_window_status({'coverage':{'initial_window':'near-five-years','calendar_provenance':{'basis':'source_derived_cross_checked'}}})
    assert retained['retained_years']==5 and retained['calendar_basis']=='source_derived_cross_checked'


@pytest.mark.parametrize('params',[{'window':'all'},{'window':10},{'window':None},{'years':10},{'history_years':5},'near-five-years'])
def test_unsupported_or_arbitrary_years_are_rejected(params):
    with pytest.raises(ValueError):windows.resolve_initial_window(params)


def inputs(tmp_path,years=10):
    wanted=f'{2026-years}-10-20'; target='2026-10-20'
    days=pd.bdate_range(wanted,target).strftime('%Y-%m-%d').tolist()
    path=tmp_path/'bulk.parquet'
    pd.DataFrame({'date_ms':[_midnight(day) for day in days]}).to_parquet(path)
    rows=[{'date_ms':_midnight(day),'open_price':10.,'high_price':11.,'low_price':9.,'close_price':10.,'volume':0.,'turnover':0.} for day in days]
    payloads={symbol:{'data':{'thscode':symbol,'interval':'1d','item':deepcopy(rows)}} for symbol in INDEX_CODES}
    if years==10:
        payloads['000688.SH']['data']['item']=[row for row in rows if row['date_ms']>=_midnight('2019-12-31')]
    return path,wanted,target,days,payloads,[day for day in days if day>='2025-10-20']


@pytest.mark.parametrize('years',[5,10])
def test_full_core_calendar_and_real_star_suffix_are_separate(tmp_path,years):
    path,wanted,target,days,payloads,official=inputs(tmp_path,years)
    actual,proof=validate_history_calendar(path,wanted,target,payloads,official,years=years)
    assert actual==days and proof['years']==years and proof['official_history'] is False
    assert all(proof['indices'][symbol]['full_window'] for symbol in INDEX_CODES[:3])
    assert proof['indices']['000688.SH']['full_window']==(years==5)
    if years==10:assert proof['indices']['000688.SH']['start']=='2019-12-31'


def test_star_published_suffix_can_be_short_but_cannot_prove_older_dates(tmp_path):
    path,wanted,target,days,payloads,official=inputs(tmp_path)
    payloads['000688.SH']['data']['item']=[row for row in payloads['000688.SH']['data']['item'] if row['date_ms']>=_midnight('2020-07-23')]
    _,proof=validate_history_calendar(path,wanted,target,payloads,official,years=10)
    assert proof['indices']['000688.SH']['start']=='2020-07-23'
    assert proof['indices']['000688.SH']['full_window'] is False


@pytest.mark.parametrize('fault',['core_gap','star_gap','star_arbitrary_late','official_gap','short_bulk','wrong_ohlc'])
def test_new_windows_keep_fail_closed_dates_and_quote_guards(tmp_path,fault):
    path,wanted,target,days,payloads,official=inputs(tmp_path)
    if fault=='core_gap':payloads['000001.SH']['data']['item'].pop(10)
    elif fault=='star_gap':payloads['000688.SH']['data']['item'].pop(10)
    elif fault=='star_arbitrary_late':payloads['000688.SH']['data']['item']=[row for row in payloads['000688.SH']['data']['item'] if row['date_ms']>=_midnight('2021-01-01')]
    elif fault=='official_gap':official.pop(10)
    elif fault=='short_bulk':pd.DataFrame({'date_ms':[_midnight(day) for day in days[-1500:]]}).to_parquet(path)
    else:payloads['399006.SZ']['data']['item'][10]['low_price']=100
    with pytest.raises(ValueError):validate_history_calendar(path,wanted,target,payloads,official,years=10)


def test_official_five_year_default_hides_ten_but_keeps_legacy_resolution():
    assert windows.DEFAULT_WINDOW=='near-five-years'
    status=windows.initialization_window_status({'coverage':{'initial_window':'near-ten-years'}})
    assert [row['years'] for row in status['supported_windows']]==[1,3,5]
    assert status['retained_years']==10
    assert windows.resolve_initial_window({'window':'near-ten-years'})['years']==10
    with pytest.raises(ValueError,match='5年'):
        windows.resolve_initial_window({'window':'near-ten-years'},for_new_task=True)
    assert status['unavailable_windows'][0]['window']=='near-ten-years'
    assert '仅近5年' in status['notice'] and '容量' in status['notice']


def test_new_five_year_calendar_has_no_out_of_window_index_prewarm(tmp_path):
    path,wanted,target,days,payloads,official=inputs(tmp_path,5)
    actual,proof=validate_history_calendar(path,wanted,target,payloads,official,years=5,index_prewarm_days=0)
    assert actual==days
    assert proof['version']=='source-calendar-history-window-v2'
    assert proof['rules']['index_prewarm_days']==0
    assert proof['rules']['index_request_plan']=='bounded-window-v2'
    payloads['000001.SH']['data']['item'].insert(0,{**payloads['000001.SH']['data']['item'][0],'date_ms':_midnight('2021-10-19')})
    with pytest.raises(ValueError,match='超出请求区间'):
        validate_history_calendar(path,wanted,target,payloads,official,years=5,index_prewarm_days=0)
