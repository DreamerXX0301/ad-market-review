"""Bounded public disclosure/holder requests, derived from AKShare's source APIs.

References: akfamily/akshare stock_feature/stock_disclosure_cninfo.py,
stock_feature/stock_gdhs.py and stock/stock_info_em.py. No CAPTCHA bypass,
arbitrary hosts, document downloads or credential forwarding is performed.
"""
from datetime import datetime
import html
import json
import math
import re
import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import httpx
from app.adapters.providers import ProviderError

CHINA=ZoneInfo('Asia/Shanghai')
BASES={'tencent_quote':'https://qt.gtimg.cn','cninfo':'https://www.cninfo.com.cn','holders':'https://datacenter-web.eastmoney.com','quote':'https://push2.eastmoney.com','business':'https://emweb.securities.eastmoney.com'}


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
    def __init__(self,transport=None,sleep=time.sleep,*,budget=None):
        self.transport=transport;self.sleep=sleep;self.budget=budget

    def request(self,host,path,*,params=None,data=None):
        if host not in BASES or not path.startswith('/') or '://' in path:raise ProviderError('研究公开来源路径不受支持')
        for attempt in range(2):
            timeout=self.budget.before_attempt(host) if self.budget else 6
            try:
                with httpx.Client(transport=self.transport,timeout=timeout,follow_redirects=False,headers={'User-Agent':'Mozilla/5.0','Accept':'application/json'}) as client:
                    with client.stream('POST' if data is not None else 'GET',BASES[host]+path,
                                       data=data,params=None if data is not None else params) as response:
                        if response.status_code in (401,403,429):
                            if self.budget:self.budget.refuse(host)
                            raise ProviderError('公开研究来源拒绝访问或限流（HTTP '+str(response.status_code)+'）',response.status_code)
                        if response.status_code!=200:raise ValueError('non-200')
                        content=bytearray()
                        # No coalescing chunk_size: a slowly trickling response
                        # must still hit cancellation/deadline checks per chunk.
                        for chunk in response.iter_bytes():
                            count=len(content)+len(chunk)
                            if self.budget:self.budget.check_response(count)
                            elif count>4*1024*1024:raise ProviderError('公开研究来源单页超过容量边界')
                            content.extend(chunk)
                        if self.budget:self.budget.check_response(len(content))
                if host == 'tencent_quote':return {'quote_text': content.decode('gb18030')}
                result=json.loads(content)
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

    def tencent_market_cap(self, symbol, *, as_of_ms=None):
        """Independently dated CNY quote; never reconstruct historical market cap."""
        if not re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)', symbol):
            raise ProviderError('市值须提供完整证券代码及市场')
        market = symbol[-2:]
        code = market.lower() + symbol[:6]
        raw = self.request('tencent_quote', '/q=' + code)
        observed_limit = as_of_ms if as_of_ms is not None else int(time.time() * 1000)
        quote = re.fullmatch(r'v_' + code + r'="([^"\r\n]+)";', raw['quote_text'].strip())
        fields = quote[1].split('~') if quote else []
        if (len(fields) <= 82 or fields[2] != symbol[:6] or fields[0] != {'SH': '1', 'SZ': '51', 'BJ': '62'}[market]
                or fields[82] != 'CNY' or not re.fullmatch(r'[0-9]{14}', fields[30])):
            raise ProviderError('腾讯市值证券、币种或快照格式未通过核验')
        try:
            observed = datetime.strptime(fields[30], '%Y%m%d%H%M%S').replace(tzinfo=CHINA)
        except ValueError:
            raise ProviderError('腾讯市值来源时间无效') from None
        stamp = int(observed.timestamp() * 1000)
        floating, total = number(fields[44]), number(fields[45])
        if (stamp <= 0 or stamp > observed_limit or total is None or floating is None
                or not 0 < floating <= total):
            raise ProviderError('腾讯市值来源时间或市值字段未通过核验')
        return {'total_market_cap_cny': round(total * 100_000_000),
                'float_market_cap_cny': round(floating * 100_000_000),
                'source_date': observed.date().isoformat(), 'source_timestamp_ms': stamp,
                'source': 'tencent', 'unit': 'CNY', 'coverage': {'complete': True, 'reason': None},
                'raw': raw}

    def company_profile(self,symbol,as_of,*,max_rows=200):
        """Current business composition, independently verified identity and units.

        Contract reference (2026-10-23): AKShare stock_fundamental/stock_zygc.py,
        stock_zygc_em. REPORT_DATE is the reporting period, not publication time.
        No inference of supplier/customer relationships or historical availability.
        """
        if not re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)',symbol):raise ProviderError('主营构成须提供完整证券及市场')
        if symbol.endswith('.BJ'):raise ProviderError('北交所主营构成来源映射尚未核验')
        raw=self.request('business','/PC_HSF10/BusinessAnalysis/PageAjax',params={'code':symbol[-2:]+symbol[:6]})
        rows=raw.get('zygcfx')
        if not isinstance(rows,list):raise ProviderError('主营构成响应字段未通过核验')
        if len(rows)>2000:raise ProviderError('主营构成响应行数超过核验上限')
        accepted=[];isolated=0
        for row in rows:
            if not isinstance(row,dict):isolated+=1;continue
            period=day(row.get('REPORT_DATE'));category=str(row.get('MAINOP_TYPE',''))
            name=row.get('ITEM_NAME')
            name=html.unescape(re.sub('<[^>]+>','',name)).strip()[:500] if isinstance(name,str) else None
            if row.get('SECUCODE')!=symbol or row.get('SECURITY_CODE')!=symbol[:6] or not period or period>as_of or category not in ('1','2','3') or not isinstance(name,str) or not name.strip():
                isolated+=1;continue
            accepted.append({'instrument_id':symbol,'report_period':period,'category_code':category,
                'category':{'1':'按行业','2':'按产品','3':'按地区'}[category],'name':name,
                'revenue_cny':number(row.get('MAIN_BUSINESS_INCOME')),'revenue_ratio':number(row.get('MBI_RATIO')),
                'cost_cny':number(row.get('MAIN_BUSINESS_COST')),'profit_cny':number(row.get('MAIN_BUSINESS_RPOFIT')),
                'gross_margin_ratio':number(row.get('GROSS_RPOFIT_RATIO')),'source':'eastmoney',
                'published_at_ms':None,'historical_usable':False,'unit':'金额CNY；比例为来源原始比率（1代表100%）'})
        if not accepted:raise ProviderError('没有取得证券身份、分类和报告期均可核验的主营构成')
        # One latest reporting period only. Keep each classification separate;
        # never add product/region/industry subtotals into an inflated revenue.
        latest=max(row['report_period'] for row in accepted)
        selected=sorted([row for row in accepted if row['report_period']==latest],key=lambda row:(row['category_code'],-(row['revenue_cny'] or 0),row['name']))
        dedup={}
        for row in selected:
            key=(row['category_code'],row['name'])
            if key in dedup and dedup[key]!=row:raise ProviderError('主营构成存在同一分类项目的冲突记录，未合并')
            dedup[key]=row
        items=list(dedup.values())[:max(1,min(int(max_rows),200))]
        return {'items':items,'report_period':latest,'source':'eastmoney','fetched_at_ms':int(time.time()*1000),
                'published_at_ms':None,'historical_usable':False,
                'url':'https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/Index?type=web&code='+symbol[-2:]+symbol[:6],
                'coverage':{'complete':False,'accepted_count':len(items),'isolated_count':isolated,
                    'reason':'仅当前取得的主营构成快照；缺少原始披露日期，不声明历史时点可见或完整供应链',
                    'truncated':len(dedup)>len(items)},'raw':raw}
