"""Dated index quotes and dated constituents are independent API capabilities.

Reader fixtures below are controlled normalized results, not provider/cache
proofs. No network, running workspace, credential or source request is used.
"""
from copy import deepcopy

import pytest

from app.services import market_service as service
from app.services import market_topic_members as member_service

DAY='2026-10-20'
PREVIOUS='2026-10-19'
LATER='2026-10-21'
A='600001.SH'
B='600002.SH'


class LocalMarket:
    def calendar(self):return ['2026-10-16',PREVIOUS,DAY,LATER]
    def instruments(self,limit=10000):return [{'instrument_id':A,'name':'合成甲','kind':'stock'},
                                             {'instrument_id':B,'name':'合成乙','kind':'stock'}]
    def bars(self,*args,**kwargs):raise AssertionError('sector API must not fetch individual histories')
    def history_panel(self,*args,**kwargs):raise AssertionError('sector API must not materialize market histories')


def topic(*,code='881001.TI',kind='industry',observed=DAY,members=None):
    return {'topic_id':'fuyao-'+code,'topic_code':code,'name':'存档行业' if kind=='industry' else '存档概念',
            'kind':kind,'source':'fuyao','observed_date':observed,'members_complete':True,
            'instrument_ids':[A,B] if members is None else members}


def member_coverage(available=False):
    return {'label':'已留存成员范围','start':DAY if available else None,'end':DAY if available else None,
            'available_dates':[DAY] if available else [],'basis':'选择日已知成员','notice':'测试成员日期口径',
            'selected_date_available':available,'partial':True,
            'source_snapshot_id':'market-members' if available else None,
            'source_snapshot_day':DAY if available else None}


def index_row(*,code='881001.TI',kind='industry',change=-2.5,amount=300.):
    return {'topic_id':'fuyao-'+code,'topic_code':code,'thscode':code,
            'name':'行业甲' if kind=='industry' else '概念甲','kind':kind,'date':DAY,'quote_date':DAY,
            'previous_date':PREVIOUS,'close':97.5,'previous_close':100.,'change_pct':change,
            'amount':amount,'volume':100.,'amount_unit':'CNY',
            'amount_reason':'来源未提供成交额' if amount is None else None,
            'calculation':'来源板块指数','date_evidence':'controlled normalized reader fixture',
            'normalization_version':'fuyao-sector-index-history-v1',
            'request_signature':'test-fixture-not-provider-proof','fetched_at':LATER+'T09:00:00+08:00'}


def quote_report(items):
    return {'date':DAY,'source':'fuyao','items':deepcopy(items),
            'coverage':{'requested_date':DAY,'expected_previous_date':PREVIOUS,
                        'current_catalog':True,'historical_catalog_complete':False,
                        'catalog_observed_at':LATER+'T09:00:00+08:00',
                        'directory_count':3,'accepted_count':len(items),'failed_count':3-len(items),
                        'kind_counts':{'industry':1,'concept':2}},
            'failures':[],'complete':False,'normalization_version':'fuyao-sector-index-history-v1',
            'scope':'当前目录中通过指定日历史行情核验的板块；不是历史完整目录'}


@pytest.fixture
def install(monkeypatch):
    def configure(items,local_topics=None):
        selected=local_topics or []
        local={'topics':deepcopy(selected),'industry_catalog':[{'thscode':'881001.TI','name':'行业甲'}],
               'concept_catalog':[{'thscode':'885001.TI','name':'概念甲'},{'thscode':'885002.TI','name':'当前目录新增概念'}]}
        coverage=member_coverage(bool(selected))
        current={**deepcopy(local),'topics':[topic(observed=LATER)],'_supplement':{}}
        rows=[{'instrument_id':A,'date':DAY,'name':'合成甲','close':10.,'change_pct':5.,'amount':100.,'limit_up':True},
              {'instrument_id':B,'date':DAY,'name':'合成乙','close':9.,'change_pct':-3.,'amount':200.,'limit_up':False}]
        status={'ready':True,'snapshot_id':'market-current','data_begin':'2021-10-20','data_end':DAY,'quality':'partial'}
        market=LocalMarket();calls=[];report=quote_report(items)
        def panel(day=None):
            calls.append(day)
            assert day==DAY
            return market,status,DAY,deepcopy(current),deepcopy(rows)
        monkeypatch.setattr(service,'panel',panel)
        monkeypatch.setattr(service,'sector_prices_for_date',lambda *_:deepcopy(report),raising=False)
        monkeypatch.setattr(service,'resolve_reference',lambda *_:(deepcopy(local),deepcopy(coverage)))
        monkeypatch.setattr(member_service,'resolve_reference',lambda *_:(deepcopy(local),deepcopy(coverage)))
        return market,status,current,coverage,report,calls
    return configure


def test_real_dated_index_rows_show_even_when_historical_members_are_unavailable(install):
    _,_,current,coverage,_,calls=install([index_row(),index_row(code='885001.TI',kind='concept',change=1.25)])
    before=deepcopy(current)
    result=service.market_page('sectors',DAY)
    assert calls==[DAY] and current==before
    assert len(result['items'])==2
    assert result['summary']['index_quote_count']==2
    assert result['summary']['quoted_industries']==result['summary']['quoted_concepts']==1
    assert result['membership_coverage']==coverage
    assert result['data_coverage']['selected_date_available'] is True
    for row in result['items']:
        assert row['quote_date']==DAY and row['quote_available'] is True
        assert row['quote_source']=='fuyao' and row['quote_basis']=='provider_index'
        assert row['calculation']=='来源板块指数'
        assert row['members_available'] is False and row['members_complete'] is False
        assert row['membership_match_type']=='unavailable' and row['membership_unavailable_reason']
        for key in ('member_count','observed_count','up_count','down_count','limit_up_count','limit_up_observed_count'):
            assert row[key] is None, f'{key}: missing constituents must not become an observed zero'


def test_zero_change_and_zero_amount_are_valid_provider_values(install):
    install([index_row(change=0.,amount=0.)])
    row=service.market_page('sectors',DAY)['items'][0]
    assert row['quote_available'] is True
    assert row['change_pct']==0 and row['amount']==0
    assert row['quote_basis']=='provider_index'


def test_provider_missing_amount_never_borrows_constituent_turnover(install):
    install([index_row(amount=None)],local_topics=[topic()])
    row=service.market_page('sectors',DAY)['items'][0]
    assert row['change_pct']==-2.5 and row['amount'] is None
    assert row['quote_basis']=='provider_index' and row['members_available'] is True
    assert row['member_count']==2 and row['observed_count']==2
    assert row.get('amount_reason'), 'missing provider amount must keep its reason'


def test_current_directory_is_not_presented_as_complete_historical_universe(install):
    _,_,_,_,report,_=install([index_row()])
    result=service.market_page('sectors',DAY)
    assert result['quote_coverage']==report['coverage']
    assert result['quote_coverage']['current_catalog'] is True
    assert result['quote_coverage']['historical_catalog_complete'] is False
    assert result['data_coverage']['start']==result['data_coverage']['end']==DAY
    assert result['data_coverage']['available_dates']==[DAY]
    assert result['data_coverage']['partial'] is True
    assert result['membership_coverage']['selected_date_available'] is False


def test_no_index_and_no_members_remains_unknown_not_synthetic_zero(install):
    install([])
    result=service.market_page('sectors',DAY)
    assert result['items']==[]
    assert result['data_coverage']['selected_date_available'] is False
    assert result['unavailable_reason']
    assert result['summary']['index_quote_count']==0


def test_archived_member_sample_is_only_a_disclosed_fallback_without_provider_index(install):
    install([],local_topics=[topic()])
    row=service.market_page('sectors',DAY)['items'][0]
    assert row['quote_basis']=='archived_member_sample'
    assert row['calculation']=='存档成员样本等权涨幅'
    assert row['change_pct']==1. and row['amount']==300.
    assert row['members_available'] is True
    assert row['membership_observed_date']==DAY


def test_price_row_does_not_unlock_later_constituents_or_member_metrics(install,monkeypatch):
    install([index_row(code='885001.TI',kind='concept')])
    monkeypatch.setattr(member_service,'member_metrics',lambda *a,**k:pytest.fail('unknown members must not call individual metrics'))
    row=service.market_page('sectors',DAY)['items'][0]
    assert row['member_lookup']=={'topic_id':'fuyao-885001.TI','kind':'concept'}
    members=service.market_topic_members(DAY,**row['member_lookup'])
    assert members['items']==[] and members['total']==0
    assert members['members_complete'] is False and members['unavailable_reason']
    assert members['data_coverage']['selected_date_available'] is False


def test_index_turnover_is_never_relabelled_as_net_inflow(install):
    install([index_row(amount=123456.)])
    row=service.market_page('sectors',DAY)['items'][0]
    assert row['amount']==123456.
    assert row.get('net_inflow') is None
