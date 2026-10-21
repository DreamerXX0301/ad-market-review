"""Controlled directory/history fixtures, no keys or live provider requests."""
import json
from datetime import date,timedelta

import httpx
import pytest

from app.adapters.providers import ProviderError
from app.adapters.public_market import PublicMarketClient
from app.adapters.public_fund_history import parse_directory,normalize_history,fetch_sector_fund_history


def directory():
    return '<ul class="pop_slider"><li>行业</li><li>概念</li><li>地域</li></ul>'+''.join('<div class="pop-cont">'+''.join(f'<a href="/bkzj/{code}.html">{name}</a>' for code,name in pairs)+'</div>' for pairs in ([('BK1000','行业甲'),('BK1001','行业乙'),('BK1002','行业丙')],[('BK2000','概念甲')],[('BK3000','地域甲')]))


def days():
    result=[];current=date(2026,9,1)
    while len(result)<22:
        if current.weekday()<5:result.append(current.isoformat())
        current+=timedelta(days=1)
    return result


def payload(code='BK1000',calendar=None):
    calendar=calendar or days()
    return {'rc':0,'data':{'code':code,'market':90,'name':'供应商名称','klines':[','.join([day,'-100','60','40','-25','-75','-1','0.6','0.4','-0.25','-0.75','10.1','-0.5']) for day in calendar]}}


def test_public_directory_scope_no_guessed_or_cross_tab_codes():
    assert [row['topic_code'] for row in parse_directory(directory())]==['BK1000','BK1001','BK1002']
    assert [row['topic_code'] for row in parse_directory(directory(),'concept')]==['BK2000']
    assert len(parse_directory(directory(),'both'))==4
    with pytest.raises(ProviderError,match='重复'):
        parse_directory(directory().replace('BK1001','BK1000'))
    with pytest.raises(ProviderError,match='标签结构'):
        parse_directory('<p>行业 <a href="/bkzj/BK1000.html">伪目录</a></p>')
    nested=directory().replace('<a href="/bkzj/BK1000.html">行业甲</a>', '<div><a href="/bkzj/BK1000.html">行业甲</a></div>')
    assert len(parse_directory(nested))==3
    with pytest.raises(ProviderError,match='链接格式'):
        parse_directory(directory().replace('/bkzj/BK1001.html','/changed/BK1001.html'))


def test_history_uses_explicit_dates_and_same_board_and_currency():
    calendar=days();target=calendar[-2]
    normalized=normalize_history(payload(),{'topic_code':'BK1000','name':'行业甲','kind':'industry'},target,calendar)
    assert normalized['item']['date']==target
    assert normalized['item']['net_inflow']==-100
    assert normalized['item']['net_inflow_pct']==-1
    assert normalized['item']['amount_unit']=='CNY'
    assert len(normalized['history'])==20 and normalized['history_complete'] is True
    assert all(row['date']<=target for row in normalized['history'])
    with pytest.raises(ProviderError,match='身份'):
        normalize_history(payload('BK1001'),{'topic_code':'BK1000','name':'行业甲','kind':'industry'},target,calendar)
    duplicated=payload();duplicated['data']['klines'].append(duplicated['data']['klines'][-1])
    with pytest.raises(ProviderError,match='重复日期'):
        normalize_history(duplicated,{'topic_code':'BK1000','name':'行业甲','kind':'industry'},target,calendar)


def test_history_gap_is_explicit_not_forward_filled_and_current_day_survives():
    calendar=days();target=calendar[-1]
    raw=payload(calendar=[day for day in calendar if day!=calendar[-3]])
    record=normalize_history(raw,{'topic_code':'BK1000','name':'行业甲','kind':'industry'},target,calendar)
    assert record['item']['date']==target
    assert record['history_complete'] is False
    assert record['missing_history_dates']==[calendar[-3]]
    assert len(record['history'])==19


def test_complete_directory_cache_reuse_and_dated_history_budget():
    requested=[];saved=[];calendar=days();target=calendar[-1]
    def handler(request):
        requested.append(str(request.url))
        if request.url.host=='data.eastmoney.com':return httpx.Response(200,text=directory(),headers={'content-type':'text/html; charset=utf-8'})
        assert request.url.params['secid']=='90.BK1002'
        assert request.url.params['lmt']=='0'
        return httpx.Response(200,json=payload('BK1002'))
    client=PublicMarketClient(transport=httpx.MockTransport(handler),sleep=lambda _:None)
    result=fetch_sector_fund_history(client,target,calendar=calendar,max_requests=2,cache_lookup=lambda code,day:payload(code) if code!='BK1002' else None,cache_save=lambda *args:saved.append(args),request_interval=0)
    assert len(requested)==2 and len(saved)==1
    assert result['complete'] is True and result['coverage']['cached_count']==2
    assert result['coverage']['history_20d_complete'] is True
    assert len(result['history'])==60 and result['coverage']['logical_request_count']==2
    assert all(row['date']==target for row in result['items'])
    assert result['intraday_sequence_available'] is False


def test_budget_and_provider_failure_keep_explicit_missing_codes():
    calendar=days();target=calendar[-1]
    def handler(request):
        if request.url.host=='data.eastmoney.com':return httpx.Response(200,text=directory())
        code=request.url.params['secid'].split('.')[1]
        # Valid response but no target-day record is not a valid empty flow.
        return httpx.Response(200,json=payload(code,calendar[:-1]))
    client=PublicMarketClient(transport=httpx.MockTransport(handler),sleep=lambda _:None)
    result=fetch_sector_fund_history(client,target,calendar=calendar,max_requests=2,request_interval=0)
    assert result['complete'] is False and result['items']==[]
    assert len(result['failures'])==3 and result['coverage']['unrequested_count']==2
    assert any('目标交易日' in row['reason'] for row in result['failures'])
    assert result['coverage']['unrequested_codes']==['BK1001','BK1002']
