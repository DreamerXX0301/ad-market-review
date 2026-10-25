"""Missing technical values explain the unchanged strict warmup decision."""
from copy import deepcopy

import pytest

from app.analytics.indicators import enrich_bars
from app.domain.conditions import FIELDS, evaluate, metrics_for, metrics_from_enriched
from app.domain.history_events import HistoryContext
from app.services.history_events import contexts_for_batch, prepare_shared
from test_history_event_panels import days, quotes, fixture_market, event


@pytest.mark.parametrize('mode', ['initial', 'action', 'gap'])
@pytest.mark.parametrize('field', ['macd_dif', 'macd_dea', 'macd_hist', 'kdj_k', 'kdj_d', 'kdj_j'])
def test_numeric_unknown_reports_original_reset_and_current_required_bars(mode,field):
    calendar=days(42 if mode!='initial' else 17);rows=quotes(calendar)
    if mode=='action':rows[25]['corporate_action']=True
    if mode=='gap':rows.pop(24)
    enriched=enrich_bars(rows,calendar);row=enriched[-1]
    original=deepcopy(row);metrics=metrics_from_enriched(row,enriched[-2])
    result=evaluate({'field':field,'op':'>','value':0},metrics)
    assert result['result'] is None and result['actual'] is None
    assert row==original, 'diagnostics cannot change indicator inputs'
    assert f"当前连续可比报价{row['indicator_warmup_bars']}根" in result['reason']
    assert '需至少35根' in result['reason']
    assert ('初始预热' if mode=='initial' else '公司行动' if mode=='action' else '缺少1个市场交易日报价') in result['reason']
    assert result['indicator_evidence'][field]['required_warmup_bars']==35
    assert result['indicator_evidence'][field]['indicator_warmup_reason']==row['indicator_warmup_reason']
    assert {key:metrics[key] for key in FIELDS if key in row and key not in ('macd_dead_cross','kd_dead_cross')}=={key:row[key] for key in FIELDS if key in row and key not in ('macd_dead_cross','kd_dead_cross')}


@pytest.mark.parametrize('field', ['macd_cross','kd_cross','macd_dead_cross','kd_dead_cross'])
def test_cross_at_35_bars_stays_unknown_and_explains_the_36_bar_requirement(field):
    calendar=days(36);rows=quotes(calendar)
    enriched=enrich_bars(rows,calendar)
    at35=metrics_from_enriched(enriched[34],enriched[33])
    check=evaluate({'field':field,'op':'==','value':True},at35)
    assert check['result'] is None and check['actual'] is None
    assert '当前连续可比报价35根，需至少36根' in check['reason']
    at36=metrics_from_enriched(enriched[35],enriched[34])
    assert at36[field] is enriched[35][field]
    known=evaluate({'field':field,'op':'==','value':True},at36)
    assert known['result'] is (enriched[35][field] is True)
    assert known['reason'] is None and 'indicator_evidence' not in known


def test_missing_right_hand_indicator_uses_its_reason_without_changing_comparison():
    calendar=days(17);rows=quotes(calendar);rows[0]['corporate_action']=True
    metrics=metrics_for(rows,calendar[-1],calendar)
    result=evaluate({'field':'close','op':'>','right_field':'macd_dif'},metrics)
    assert result['actual'] is not None and result['right_actual'] is None
    assert result['result'] is None and '公司行动' in result['reason']
    assert result['indicator_evidence']['macd_dif']['indicator_warmup_bars']==17


def test_completed_week_reason_names_action_reset_and_week_count():
    calendar=days(350);rows=quotes(calendar);rows[-81]['corporate_action']=True
    metrics=metrics_for(rows,calendar[-1],calendar,'W')
    result=evaluate({'field':'macd_dif','op':'>','right_field':'macd_dea'},metrics)
    assert result['result'] is None and result['actual'] is result['right_actual'] is None
    assert '公司行动' in result['reason'] and '需至少35根' in result['reason']
    assert f"当前连续可比报价{metrics['indicator_warmup_bars']}根" in result['reason']


@pytest.mark.parametrize('mode', ['missing_field','no_quote','zero_volume','missing_volume','known'])
def test_turnover_missing_source_and_invalid_quote_are_separate_without_replacement(mode):
    calendar=days(50);rows=quotes(calendar)
    if mode=='no_quote':rows.pop()
    elif mode=='zero_volume':rows[-1]['volume']=0
    elif mode=='missing_volume':rows[-1]['volume']=None
    elif mode=='known':rows[-1]['turnover_ratio_pct']=4.5
    metrics=metrics_for(rows,calendar[-1],calendar)
    check=evaluate({'field':'turnover_ratio_pct','op':'<','value':10},metrics)
    assert check['observation_date']==calendar[-1] and check['observation_period']=='D'
    if mode=='known':
        assert check['result'] is True and check['actual']==4.5 and check['reason'] is None
    else:
        assert check['actual'] is None and check['result'] is None
        assert ('成交额不是换手率' if mode=='missing_field' else '正成交量报价') in check['reason']


@pytest.mark.parametrize('mode', ['action','gap'])
@pytest.mark.parametrize('field', ['macd_dif','kdj_k','macd_cross','kd_cross','macd_dead_cross','kd_dead_cross'])
def test_prepared_and_scalar_history_preserve_values_and_identical_diagnostics(tmp_path,mode,field):
    calendar=days(100);rows=quotes(calendar)
    if mode=='action':rows[-18]['corporate_action']=True
    else:rows.pop(-19)
    boolean=field.endswith('cross')
    leaf={'field':field,'op':'==' if boolean else '>','value':True if boolean else 0}
    node=event(1,5,where=leaf);market=fixture_market(tmp_path,rows,calendar)
    try:
        shared=prepare_shared(market,['600001.SH'],[node],calendar,calendar[-1])
        prepared=contexts_for_batch(market,['600001.SH'],[node],calendar,calendar[-1],shared=shared)['600001.SH']
        scalar=HistoryContext(rows,calendar,calendar[-1],retain_start=calendar[-6])
        for day in prepared.daily:
            left=evaluate(leaf,prepared.daily[day]);right=evaluate(leaf,scalar.daily[day])
            assert left['actual'] is right['actual'] is None
            assert left['result'] is right['result'] is None
            assert left['reason']==right['reason']
            assert left['indicator_evidence']==right['indicator_evidence']
    finally:market.close()
