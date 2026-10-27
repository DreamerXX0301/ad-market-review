"""Dated dragon-tiger adapters; unknown opposing-side amounts stay unknown.

Fuyao supplies stock/label summaries. The fixed Eastmoney public data-centre
reports supply disclosure identities and each direction's actual five rows.
Neither provider's marketing labels identify a verified personal account.
"""
from __future__ import annotations
from collections import defaultdict
from contextlib import AbstractContextManager
from decimal import Decimal
import json
import math
import re
import time
import httpx

from .providers import ProviderError

VERSION='dragon-tiger-v1'
FUYAO_PATH='/api/a-share/special-data/dragon-tiger-list'
REPORTS={'buy':'RPT_BILLBOARD_DAILYDETAILSBUY','sell':'RPT_BILLBOARD_DAILYDETAILSSELL','disclosures':'RPT_DAILYBILLBOARD_DETAILSNEW'}
MAX_PAGES=20
MAX_PAGE_BYTES=8*1024*1024
CONCEPT_BASIS='来源随查询返回的概念资料；未证明指定日历史成员，不作为历史主线归因'


def number(value, *, nonnegative=False):
    if value is None:return None
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ProviderError('龙虎榜数值字段格式无效')
    if nonnegative and value<0:raise ProviderError('龙虎榜买卖金额或数量不能为负')
    return value


def integer(value, *, optional=False):
    if optional and value is None:return None
    if isinstance(value,bool) or not isinstance(value,int) or value<0:raise ProviderError('龙虎榜记录数量格式无效')
    return value


def _equal_net(buy,sell,net):
    return None in (buy,sell,net) or abs(Decimal(str(buy))-Decimal(str(sell))-Decimal(str(net)))<=Decimal('.011')


def identities(master):
    by_id={row['instrument_id']:row for row in master if row.get('kind')=='stock'}
    by_code=defaultdict(list)
    for symbol,row in by_id.items():by_code[row.get('code',row.get('ticker'))].append(symbol)
    return by_id,by_code


def normalize_board(payload,day,board_type,master):
    data=payload.get('data') if isinstance(payload,dict) else None
    if not isinstance(data,dict) or data.get('trade_date')!=day or data.get('board_type')!=board_type:raise ProviderError('龙虎榜实际日期或榜单类型与请求不符')
    if payload.get('code') not in (None,0):raise ProviderError('龙虎榜来源业务响应未通过')
    declared=integer(data.get('count'));source_stock_count=integer(data.get('stock_count'))
    if declared==0 and source_stock_count!=0:raise ProviderError('龙虎榜空列表与来源股票数冲突')
    by_id,by_code=identities(master)
    def row(raw,label=None):
        if not isinstance(raw,dict):raise ProviderError('龙虎榜股票记录格式无效')
        code=raw.get('ticker');provided=raw.get('thscode')
        if not isinstance(code,str) or not re.fullmatch(r'\d{6}',code):raise ProviderError('龙虎榜股票代码无效')
        if provided and (not isinstance(provided,str) or provided.split('.')[0]!=code):raise ProviderError('龙虎榜标准代码与六位代码不一致')
        symbol=provided if provided in by_id else None
        if symbol is None and not provided and len(by_code.get(code,[]))==1:symbol=by_code[code][0]
        period=raw.get('range_days')
        if isinstance(period,bool) or period not in (1,3):raise ProviderError('龙虎榜单日或三日区间无效，未混入其他范围')
        concepts=raw.get('concept_list')
        if concepts is not None and (not isinstance(concepts,list) or any(not isinstance(v,dict) for v in concepts)):raise ProviderError('龙虎榜来源概念格式变化')
        buy=number(raw.get('buy_value'),nonnegative=True);sell=number(raw.get('sell_value'),nonnegative=True);net=number(raw.get('net_value'))
        if not _equal_net(buy,sell,net):raise ProviderError('龙虎榜股票汇总买卖净额不一致')
        change=number(raw.get('change'))
        return {'instrument_id':symbol,'code':code,'name':raw.get('name') or (by_id[symbol].get('name') if symbol else None),
                'source_instrument_id':provided,'identity_state':'verified' if symbol else 'unknown','identity_reason':None if symbol else '未匹配已核验A股名录；原始记录保留',
                'range_days':period,'change_pct':None if change is None else change*100,'buy_value':buy,'sell_value':sell,'net_value':net,
                'org_net_value':number(raw.get('org_net_value')),'hot_money_net_value':number(raw.get('hot_money_net_value')),
                'org_buy_num':integer(raw.get('org_buy_num'),optional=True),'org_sell_num':integer(raw.get('org_sell_num'),optional=True),
                'label_net_value':number(raw.get('hot_money_item_net_value')),'label_name':label,
                'source_concepts':[{'name':v.get('name')} for v in concepts or [] if isinstance(v.get('name'),str)],'source_concepts_basis':CONCEPT_BASIS,
                'source_limit_reason':raw.get('limit_reason'),'source':'fuyao','date':day}
    output=[];seen=set()
    if board_type=='hot_money':
        groups=data.get('hot_money_items')
        if not isinstance(groups,list):raise ProviderError('龙虎榜游资标签列表缺失')
        parsed=0
        for group in groups:
            if not isinstance(group,dict) or not isinstance(group.get('name'),str) or not isinstance(group.get('rows'),list):raise ProviderError('龙虎榜游资标签格式无效')
            name=group['name'];rows=[]
            if name in seen:raise ProviderError('龙虎榜游资标签重复')
            seen.add(name)
            keys=set()
            for raw in group['rows']:
                item=row(raw,name);key=(item['code'],item['range_days'])
                if key in keys:raise ProviderError('同一游资标签内股票区间重复')
                keys.add(key);rows.append(item);parsed+=1
            output.append({'name':name,'rows':rows})
        if parsed!=declared:raise ProviderError('龙虎榜游资行数与来源总数不一致')
    else:
        rows=data.get('stock_items')
        if not isinstance(rows,list) or len(rows)!=declared:raise ProviderError('龙虎榜股票行数与来源总数不一致')
        for raw in rows:
            item=row(raw);key=(item['code'],item['range_days'])
            if key in seen:raise ProviderError('龙虎榜股票区间重复')
            seen.add(key);output.append(item)
    return {'items':output,'source_count':declared,'source_stock_count':source_stock_count,'state':'ready','source':'fuyao','date':day,'source_concepts_basis':CONCEPT_BASIS}


class DragonTigerPublicClient(AbstractContextManager):
    """One manual task owns one bounded connection; reads never instantiate it."""
    endpoint='https://datacenter-web.eastmoney.com/api/data/v1/get'
    def __init__(self,transport=None,sleep=time.sleep,guard=None):
        self.sleep=sleep;self.guard=guard;self.last_request=0.;self.transport_attempts=0
        self.client=httpx.Client(timeout=httpx.Timeout(20,connect=8),follow_redirects=False,transport=transport,
            limits=httpx.Limits(max_connections=1,max_keepalive_connections=1,keepalive_expiry=30))
    def __exit__(self,*args):self.client.close()
    def check(self):
        if self.guard:self.guard()
    def wait(self,seconds):
        while seconds>0:
            self.check();chunk=min(seconds,.2);self.sleep(chunk);seconds-=chunk
    def request(self,params):
        for attempt in range(3):
            self.check();self.wait(max(0,.6-(time.monotonic()-self.last_request)))
            try:
                self.transport_attempts+=1
                try:
                    with self.client.stream('GET',self.endpoint,params=params,headers={'User-Agent':'Mozilla/5.0 ShixuWorkbench/0.1','Referer':'https://data.eastmoney.com/','Accept':'application/json'}) as response:
                        if response.status_code in (429,500,502,503,504) and attempt<2:
                            self.wait(2**attempt);continue
                        if response.status_code!=200:raise ProviderError('龙虎榜公开来源返回HTTP '+str(response.status_code),status=response.status_code)
                        parts=[];size=0
                        for block in response.iter_bytes():
                            self.check();size+=len(block)
                            if size>MAX_PAGE_BYTES:raise ProviderError('龙虎榜公开来源单页超出8MiB')
                            parts.append(block)
                        payload=json.loads(b''.join(parts))
                finally:self.last_request=time.monotonic()
                if not isinstance(payload,dict):raise ProviderError('龙虎榜公开来源响应格式无效')
                return payload
            except ProviderError:raise
            except (httpx.HTTPError,ValueError):
                if attempt==2:raise ProviderError('龙虎榜公开来源连接或格式异常，有限重试后保留缺口') from None
                self.wait(2**attempt)
        raise ProviderError('龙虎榜公开来源请求未完成')
    def report(self,day,kind,*,allow_empty=False):
        if kind not in REPORTS:raise ValueError('龙虎榜公开报表范围无效')
        pages=[];rows=[];expected=None
        for page in range(1,MAX_PAGES+1):
            params={'reportName':REPORTS[kind],'columns':'ALL','filter':"(TRADE_DATE='"+day+"')",'pageNumber':page,'pageSize':500,
                    'sortTypes':'-1','sortColumns':'BUY' if kind=='buy' else 'SELL' if kind=='sell' else 'BILLBOARD_NET_AMT','source':'WEB','client':'WEB'}
            payload=self.request(params);pages.append({'request':params,'response':payload})
            if payload.get('success') is not True or payload.get('code')!=0:
                if allow_empty and payload.get('code')==9201 and page==1:
                    return {'date':day,'kind':kind,'rows':[],'pages':pages,'empty_basis':'同日扶摇三类榜明确为空，公开报表返回空查询结果'}
                raise ProviderError('龙虎榜公开来源业务响应未通过（code='+str(payload.get('code'))+'）')
            result=payload.get('result')
            if not isinstance(result,dict) or not isinstance(result.get('data'),list):raise ProviderError('龙虎榜公开来源缺少分页记录')
            count=integer(result.get('count'));count_pages=integer(result.get('pages'))
            if not 0<=count_pages<=MAX_PAGES or count>MAX_PAGES*500:raise ProviderError('龙虎榜公开来源超过分页预算')
            current=(count,count_pages)
            if expected is not None and current!=expected:raise ProviderError('龙虎榜公开来源分页范围变化')
            expected=current;batch=result['data']
            if any(not isinstance(row,dict) or str(row.get('TRADE_DATE',''))[:10]!=day for row in batch):raise ProviderError('龙虎榜公开来源实际日期与请求日不符')
            rows.extend(batch)
            if count==0 and allow_empty and not batch:return {'date':day,'kind':kind,'rows':[],'pages':pages,'empty_basis':'同日扶摇三类榜和公开报表均明确为空'}
            if not batch:raise ProviderError('龙虎榜公开来源空页不能视为完成')
            if page==count_pages:
                if len(rows)!=count:raise ProviderError('龙虎榜公开来源分页条数不完整')
                return {'date':day,'kind':kind,'rows':rows,'pages':pages}
        raise ProviderError('龙虎榜公开来源分页未完成')


def _period(reason):
    match=re.search(r'连续(三|3)个交易日',reason)
    if match:return 3,match.group(0)
    match=re.search(r'(当日|日(收盘价格|价格|涨幅|跌幅|换手率|振幅值)|上市首日)',reason)
    return (1,match.group(0)) if match else (None,None)


def normalize_seats(reports,day,master,all_board):
    by_id,_=identities(master);groups={};summaries={};warnings=[];partial_rows=0;complete_rows=0
    for raw in reports['disclosures']['rows']:
        key=_identity(raw,day)
        if key in summaries:raise ProviderError('龙虎榜同一披露记录重复')
        buy=number(raw.get('BILLBOARD_BUY_AMT'),nonnegative=True);sell=number(raw.get('BILLBOARD_SELL_AMT'),nonnegative=True);net=number(raw.get('BILLBOARD_NET_AMT'))
        if not _equal_net(buy,sell,net):raise ProviderError('龙虎榜披露汇总净额不一致')
        summaries[key]={'raw':raw,'summary':{'buy_value':buy,'sell_value':sell,'net_value':net}}
    for side in ('buy','sell'):
        for order,raw in enumerate(reports[side]['rows']):
            key=_identity(raw,day)
            if key not in summaries:raise ProviderError('龙虎榜席位缺少对应披露汇总')
            meta=summaries[key];reason=raw.get('EXPLANATION')
            if not isinstance(reason,str) or reason!=meta['raw'].get('EXPLANATION') or str(raw.get('CHANGE_TYPE'))!=str(meta['raw'].get('CHANGE_TYPE')):raise ProviderError('龙虎榜席位披露原因不一致')
            if key not in groups:
                period,evidence=_period(reason)
                groups[key]={'record_id':key[1],'reason':reason,'range_days':period,'range_evidence':evidence,'summary':meta['summary'],'buy':[],'sell':[],
                    'source':{'name':'东方财富公开数据','provider':'eastmoney','date':day,'instrument_id':key[0],'change_type':str(raw.get('CHANGE_TYPE'))}}
            buy=number(raw.get('BUY'),nonnegative=True);sell=number(raw.get('SELL'),nonnegative=True);reported=number(raw.get('NET'))
            complete=None not in (buy,sell,reported)
            if complete and not _equal_net(buy,sell,reported):raise ProviderError('龙虎榜席位买卖净额不一致')
            if complete:complete_rows+=1
            else:partial_rows+=1
            groups[key][side].append({'name':raw.get('OPERATEDEPT_NAME'),'source_id':raw.get('OPERATEDEPT_CODE'),'buy':buy,'sell':sell,'net':reported if complete else None,
                'raw_net':reported,'amounts_complete':complete,'source_order':order,'source':{'provider':'eastmoney','report':REPORTS[side],'date':day}})
    if set(groups)!=set(summaries):raise ProviderError('龙虎榜披露汇总与席位记录范围不完整')
    all_rows={(r['instrument_id'],r['range_days']):r for r in all_board['items'] if r['instrument_id']}
    instruments={};unmatched=[];reconciled=0;differences=[]
    for (symbol,record_id),record in sorted(groups.items()):
        for side in ('buy','sell'):
            field=side
            record[side].sort(key=lambda row:(row[field] is not None,row[field] or 0),reverse=True)
            if len(record[side])>5:raise ProviderError('龙虎榜披露方向超过前五范围')
            for rank,row in enumerate(record[side],1):row['rank']=rank;row.pop('source_order',None)
        record['coverage']={'buy_rows':len(record['buy']),'sell_rows':len(record['sell']),'basis':'来源完整分页中的实际披露行数；不足五行不补造'}
        if not record['buy'] or not record['sell']:warnings.append('部分披露方向未返回席位记录，保持未知；未将缺行补零')
        other=all_rows.get((symbol,record['range_days']))
        delta={key:None if other is None or other.get(key) is None or value is None else float(Decimal(str(value))-Decimal(str(other[key]))) for key,value in record['summary'].items()}
        match=other is not None and all(value is not None and abs(value)<=.011 for value in delta.values())
        record['reconciliation']={'provider':'fuyao','matched':match,'differences':delta,'scope':'同股票、日期、单日或三日区间；披露原因分别保留'}
        if symbol not in by_id:
            unmatched.append({'instrument_id':symbol,'record':record,'reason':'未匹配已核验A股名录'});continue
        if match:reconciled+=1
        else:differences.append({'instrument_id':symbol,'record_id':record_id,'differences':delta})
        instrument=instruments.setdefault(symbol,{'name':by_id[symbol].get('name') or summaries[(symbol,record_id)]['raw'].get('SECURITY_NAME_ABBR'),'code':symbol[:6],'periods':{'1':[],'3':[],'unknown':[]}})
        instrument['periods'][str(record['range_days']) if record['range_days'] is not None else 'unknown'].append(record)
    if partial_rows:warnings.append('部分席位未披露对手方向金额，买卖与净额保持未知，不按零计算')
    if differences:warnings.append('部分股票榜单汇总与席位来源汇总存在差异，两个来源原值分别保留')
    missing=[{'instrument_id':symbol,'range_days':period} for symbol,period in all_rows if not instruments.get(symbol,{}).get('periods',{}).get(str(period))]
    if missing:warnings.append('部分股票区间未取得对应公开席位记录，缺口保留')
    return {'instruments':instruments,'unmatched':unmatched,'warnings':warnings,'coverage':{'disclosure_count':len(groups),'verified_instrument_count':len(instruments),
        'verified_disclosure_count':sum(len(g) for row in instruments.values() for g in row['periods'].values()),'complete_amount_rows':complete_rows,'opposite_side_unknown_rows':partial_rows,
        'reconciled_disclosures':reconciled,'differences':differences,'missing_stock_periods':missing}}


def _identity(raw,day):
    if not isinstance(raw,dict) or str(raw.get('TRADE_DATE',''))[:10]!=day:raise ProviderError('龙虎榜席位实际日期不一致')
    symbol=raw.get('SECUCODE');code=raw.get('SECURITY_CODE');identifier=raw.get('TRADE_ID')
    if not isinstance(symbol,str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',symbol) or symbol[:6]!=code or identifier in (None,''):raise ProviderError('龙虎榜披露股票或记录标识无效')
    return symbol,str(identifier)
