"""Bounded public disclosure/holder requests, derived from AKShare's source APIs.

References: akfamily/akshare stock_feature/stock_disclosure_cninfo.py,
stock_feature/stock_gdhs.py and stock/stock_info_em.py. No CAPTCHA bypass,
arbitrary hosts, document downloads or credential forwarding is performed.
"""
from datetime import datetime
import html
import math
import re
import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import httpx
from app.adapters.providers import ProviderError

CHINA=ZoneInfo('Asia/Shanghai')
BASES={'cninfo':'https://www.cninfo.com.cn','holders':'https://datacenter-web.eastmoney.com','quote':'https://push2.eastmoney.com'}


def number(value):
    if isinstance(value,bool):return None
    try:result=float(value)
    except (TypeError,ValueError):return None
    return result if math.isfinite(result) else None


def day(value):
    try:
        if isinstance(value,(int,float)):return datetime.fromtimestamp(value/1000,CHINA).date().isoformat()
        return datetime.fromisoformat(str(value).replace('Z','+00:00')).date().isoformat()
    except (ValueError,TypeError,OverflowError,OSError):return None


class ResearchPublicClient:
    def __init__(self,transport=None,sleep=time.sleep):
        self.transport=transport;self.sleep=sleep

    def request(self,host,path,*,params=None,data=None):
        if host not in BASES or not path.startswith('/') or '://' in path:raise ProviderError('研究公开来源路径不受支持')
        for attempt in range(2):
            try:
                with httpx.Client(transport=self.transport,timeout=6,follow_redirects=False,headers={'User-Agent':'Mozilla/5.0','Accept':'application/json'}) as client:
                    response=client.post(BASES[host]+path,data=data) if data is not None else client.get(BASES[host]+path,params=params)
                if response.status_code in (401,403,429):raise ProviderError('公开研究来源拒绝访问或限流（HTTP '+str(response.status_code)+'）',response.status_code)
                if response.status_code!=200:raise ValueError('non-200')
                if len(response.content)>4*1024*1024:raise ProviderError('公开研究来源单页超过容量边界')
                result=response.json()
                if not isinstance(result,dict):raise ValueError('shape')
                return result
            except ProviderError:raise
            except (httpx.HTTPError,ValueError):
                if attempt:self.sleep(0);raise ProviderError('公开研究来源连接或格式异常，已有限重试') from None
                self.sleep(.25)

    def announcements(self,symbol,start,end,*,max_pages=12):
        max_pages=max(1,min(int(max_pages),12))
        code=symbol[:6]
        mapping=self.request('cninfo','/new/data/szse_stock.json')
        matches={row.get('orgId') for row in mapping.get('stockList',[]) if row.get('code')==code}
        if len(matches)!=1 or None in matches:raise ProviderError('巨潮证券身份未唯一匹配，未按名称猜测公告')
        org=next(iter(matches));records=[];raw=[];seen=set();declared=None;warnings=[]
        for page in range(1,max_pages+1):
            payload={'pageNum':str(page),'pageSize':'30','column':'szse','tabName':'fulltext','plate':'','stock':code+','+org,'searchkey':'','secid':'','category':'','trade':'','seDate':start+'~'+end,'sortName':'','sortType':'','isHLtitle':'false'}
            try:body=self.request('cninfo','/new/hisAnnouncement/query',data=payload)
            except ProviderError as error:
                if not raw:raise
                warnings.append('后续公告分页未取得：'+str(error));break
            raw.append(body)
            total=body.get('totalAnnouncement');items=body.get('announcements')
            if isinstance(total,bool) or not isinstance(total,int) or total<0 or not isinstance(items,list):raise ProviderError('巨潮公告分页结构未通过核验')
            if declared is not None and total!=declared:raise ProviderError('巨潮公告分页总量变化，不能声明完整一年')
            declared=total
            for row in items:
                ident=str(row.get('announcementId') or '');published=day(row.get('announcementTime'))
                if not ident or ident in seen or row.get('secCode')!=code or row.get('orgId')!=org or not published or not start<=published<=end:
                    warnings.append('存在重复、身份或日期不符公告，已隔离');continue
                seen.add(ident)
                title=html.unescape(re.sub('<[^>]+>','',str(row.get('announcementTitle') or '')))
                url='https://www.cninfo.com.cn/new/disclosure/detail?'+urlencode({'stockCode':code,'announcementId':ident,'orgId':org,'announcementTime':published})
                adjunct=row.get('adjunctUrl');pdf='https://static.cninfo.com.cn/'+adjunct.lstrip('/') if isinstance(adjunct,str) and not adjunct.startswith(('http:', 'https:', '//')) and '..' not in adjunct and adjunct.lower().endswith('.pdf') else None
                records.append({'announcement_id':ident,'instrument_id':symbol,'title':title,'published_at_ms':row['announcementTime'],'published_date':published,'source':'cninfo','url':url,'document_url':pdf,'official_document':True,'raw':row})
            if page*30>=total:break
            if not items:raise ProviderError('巨潮公告后续页为空，未标记完整')
        complete=declared==len(records) and not warnings
        return {'items':sorted(records,key=lambda row:(row['published_at_ms'],row['announcement_id']),reverse=True),'coverage':{'complete':complete,'requested_start':start,'requested_end':end,'declared_total':declared,'accepted_count':len(records),'pages':len(raw),'max_pages':max_pages,'reason':None if complete else '公告窗口未完整取得：分页上限或隔离记录；不声称完整一年','warnings':warnings},'raw_pages':raw}

    def shareholders(self,symbol,as_of):
        params={'sortColumns':'END_DATE','sortTypes':'-1','pageSize':'500','pageNumber':'1','reportName':'RPT_HOLDERNUM_DET','columns':'SECURITY_CODE,SECURITY_NAME_ABBR,CHANGE_SHARES,CHANGE_REASON,END_DATE,INTERVAL_CHRATE,AVG_MARKET_CAP,AVG_HOLD_NUM,TOTAL_MARKET_CAP,TOTAL_A_SHARES,HOLD_NOTICE_DATE,HOLDER_NUM,PRE_HOLDER_NUM,HOLDER_NUM_CHANGE,HOLDER_NUM_RATIO,PRE_END_DATE','quoteColumns':'f2,f3','filter':'(SECURITY_CODE="'+symbol[:6]+'")','source':'WEB','client':'WEB'}
        raw=self.request('holders','/api/data/v1/get',params=params)
        result=raw.get('result');items=result.get('data') if isinstance(result,dict) else None
        if raw.get('success') is not True or not isinstance(items,list):raise ProviderError('股东人数响应未通过来源核验')
        accepted=[]
        for row in items:
            report=day(row.get('END_DATE'));published=day(row.get('HOLD_NOTICE_DATE'));count=number(row.get('HOLDER_NUM'))
            if row.get('SECURITY_CODE')!=symbol[:6] or not report or not published or report>as_of or published>as_of or count is None or count<0 or not count.is_integer():continue
            accepted.append({'report_period':report,'published_date':published,'holder_count':count,'previous_holder_count':number(row.get('PRE_HOLDER_NUM')),'change_ratio_pct':number(row.get('HOLDER_NUM_RATIO')),'period_market_cap_cny':number(row.get('TOTAL_MARKET_CAP')),'unit':'户','source':'eastmoney','raw':row})
        return {'items':sorted(accepted,key=lambda row:row['report_period'],reverse=True)[:8],'coverage':{'complete':result.get('pages')==1,'scope':'单一证券最近已披露股东户数；不是实时股东名册','reason':None if accepted else '没有取得日期与证券身份均可核验的股东人数'},'raw':raw}

    def market_cap(self,symbol,as_of):
        if symbol.endswith('.BJ'):raise ProviderError('该公开个股信息接口的北交所市场映射尚未核验')
        params={'fltt':'2','invt':'2','fields':'f57,f58,f116,f117,f84,f85,f86','secid':('1.' if symbol.endswith('.SH') else '0.')+symbol[:6]}
        raw=self.request('quote','/api/qt/stock/get',params=params);data=raw.get('data')
        stamp=number(data.get('f86')) if isinstance(data,dict) else None
        observed=day(stamp*1000) if stamp is not None else None
        if raw.get('rc')!=0 or not isinstance(data,dict) or data.get('f57')!=symbol[:6] or not observed or observed>as_of:raise ProviderError('市值证券或来源快照日期未通过核验')
        total=number(data.get('f116'));floating=number(data.get('f117'))
        total=total if total is not None and total>=0 else None
        floating=floating if floating is not None and floating>=0 else None
        if total is None and floating is None:raise ProviderError('来源没有返回可核验市值字段')
        return {'total_market_cap_cny':total,'float_market_cap_cny':floating,'total_shares':number(data.get('f84')),'float_shares':number(data.get('f85')),'source_date':observed,'source':'eastmoney','unit':'CNY','coverage':{'complete':total is not None and floating is not None,'reason':None if total is not None and floating is not None else '部分市值字段未披露或无效'},'raw':raw}
