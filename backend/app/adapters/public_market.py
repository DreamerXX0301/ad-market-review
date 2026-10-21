"""AKShare-compatible public endpoints with bounded I/O and explicit validation.

AKShare documents these endpoints; this adapter calls the same fixed public hosts
without importing its unrelated scraping dependencies. Public availability is not
an SLA. Empty historical responses and capture timestamps are not trading dates.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import math
import re
import time
from zoneinfo import ZoneInfo
import httpx

from app.adapters.providers import ProviderError

CHINA = ZoneInfo('Asia/Shanghai')
BASES = {'eastmoney_pool': 'https://push2ex.eastmoney.com', 'eastmoney_flow': 'https://push2.eastmoney.com', 'eastmoney_history': 'https://push2his.eastmoney.com'}
SCOPE = '东方财富涨停股池口径；不保证覆盖ST、科创板及所有新股，不等于全A涨停全集'


def finite(value, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError('公开来源数值字段缺失或格式变化')
    if minimum is not None and value < minimum:
        raise ValueError('公开来源数值超出允许范围')
    return value


def clock(value):
    if value in (None, '', '-'): return None
    value = str(value).zfill(6)
    if not re.fullmatch(r'\d{6}', value): raise ValueError('封板时间格式变化')
    hour, minute, second = int(value[:2]), int(value[2:4]), int(value[4:])
    if hour > 23 or minute > 59 or second > 59: raise ValueError('封板时间越界')
    return value[:2] + ':' + value[2:4] + ':' + value[4:]


class PublicMarketClient:
    def __init__(self, transport=None, sleep=time.sleep):
        self.transport, self.sleep = transport, sleep

    def request(self, origin, path, params):
        if origin not in BASES or not path.startswith('/') or '://' in path:
            raise ProviderError('公开补充来源地址不在固定范围')
        for attempt in range(3):
            try:
                with httpx.Client(timeout=httpx.Timeout(20, connect=8), follow_redirects=False, transport=self.transport) as client:
                    with client.stream('GET', BASES[origin] + path, params=params, headers=({'User-Agent':'Mozilla/5.0','Accept':'application/json'} if origin=='eastmoney_history' else {'User-Agent': 'Mozilla/5.0 ShixuWorkbench/0.1', 'Referer': 'https://quote.eastmoney.com/', 'Accept': 'application/json'})) as response:
                        if response.status_code == 429 or response.status_code >= 500:
                            if attempt < 2:
                                self.sleep(2 ** attempt)
                                continue
                        if response.status_code != 200: raise ProviderError('公开补充来源返回HTTP ' + str(response.status_code), response.status_code)
                        chunks=[]; total=0
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > 8 * 1024 * 1024: raise ProviderError('公开补充来源单页超出8MiB限制')
                            chunks.append(chunk)
                        body = json.loads(b''.join(chunks))
                if not isinstance(body, dict) or body.get('rc') not in (0, None):
                    raise ProviderError('公开来源业务响应未通过')
                if not isinstance(body.get('data'), dict):
                    raise ProviderError('公开来源暂无可验证数据；未当作完整空结果')
                return body
            except ProviderError:
                raise
            except (httpx.HTTPError, ValueError):
                if attempt == 2: raise ProviderError('公开来源连接或格式异常，已重试3次；本次保留缺口') from None
                self.sleep(2 ** attempt)
        raise ProviderError('公开来源请求未完成')

    def limit_pool(self, day):
        return self.request('eastmoney_pool', '/getTopicZTPool', {'ut':'7eea3edcaed734bea9cbfc24409ed989','dpt':'wz.ztzt','Pageindex':0,'pagesize':10000,'sort':'fbt:asc','date':day.replace('-', '')})

    def sector_flow(self, day):
        """All pages must have stable totals, distinct IDs and exact f124 dates."""
        output=[]; envelopes=[]; expected=None
        for kind, category in [('industry', 2), ('concept', 3)]:
            rows=[]; total=None; page=1
            while True:
                payload=self.request('eastmoney_flow','/api/qt/clist/get',{'pn':page,'pz':100,'po':1,'np':1,'ut':'b2884a393a59ad64002292a3e90d46a5','fltt':2,'invt':2,'fid0':'f62','fs':f'm:90 t:{category}','stat':1,'fields':'f12,f14,f3,f62,f184,f124'})
                envelopes.append(payload); data=payload['data']; batch=data.get('diff'); declared=data.get('total')
                if not isinstance(batch,list) or isinstance(declared,bool) or not isinstance(declared,int) or declared<=0: raise ProviderError('资金流分页缺少可信总量或数据')
                if total is not None and total!=declared: raise ProviderError('资金流分页总量变化，不能标记完整')
                total=declared
                for raw in batch:
                    code=raw.get('f12'); name=raw.get('f14'); stamp=raw.get('f124')
                    if not isinstance(code,str) or not re.fullmatch(r'BK\d{4}',code) or not isinstance(name,str):raise ProviderError('资金流板块标识格式变化')
                    finite(stamp,minimum=1)
                    actual=datetime.fromtimestamp(stamp,CHINA).date().isoformat()
                    if actual!=day:raise ProviderError('资金流数据时间与目标盘面日不符，未回填历史日')
                    rows.append({'topic_id':'eastmoney-'+code,'topic_code':code,'name':name,'kind':kind,'change_pct':finite(raw.get('f3')),'net_inflow':finite(raw.get('f62')),'net_inflow_pct':finite(raw.get('f184')),'date':day,'observed_at':datetime.fromtimestamp(stamp,CHINA).isoformat(),'source':'eastmoney-public','calculation':'供应商按成交单大小分类的主力净流入；并非真实机构账户流向'})
                if len(rows)>=total:break
                if not batch or page>=30:raise ProviderError('资金流分页未完成')
                page+=1
            if len(rows)!=total or len({r['topic_code'] for r in rows})!=total:raise ProviderError('资金流分页总数或去重校验失败')
            output.extend(rows)
        return {'items':output,'date':day,'source':'eastmoney-public','complete':True,'raw_pages':envelopes,'intraday_sequence_available':False}


def normalize_limit_pool(payload, day, closes, master):
    data=payload.get('data',{}); raw=data.get('pool'); declared=data.get('tc')
    if not isinstance(raw,list) or isinstance(declared,bool) or not isinstance(declared,int) or declared<0:raise ProviderError('涨停补充池结构或总量变化')
    if len(raw)!=declared or len(raw)>=10000:raise ProviderError('涨停补充池总量与单页不符，拒绝截断结果')
    # This endpoint returns tc=0 for unsupported older dates too. Absence is unknown.
    if not raw:raise ProviderError('公开涨停池为空，无法区分历史过期与真实无涨停；保留未知')
    identities={r.get('code',r.get('ticker',str(r.get('instrument_id',''))[:6])):r.get('instrument_id',r.get('thscode')) for r in master}
    accepted=[]; quarantine=[]; seen=set()
    for item in raw:
        code=item.get('c'); symbol=identities.get(code)
        if not isinstance(code,str) or not re.fullmatch(r'\d{6}',code) or code in seen:raise ProviderError('公开涨停池代码无效或重复')
        seen.add(code)
        if not symbol or symbol not in closes:
            quarantine.append({'code':code,'reason':'目标交易日证券身份或收盘报价缺失，未按名称猜测'})
            continue
        price=finite(item.get('p'),minimum=.001)/1000
        if abs(price-closes[symbol])>.011:raise ProviderError('公开涨停池价格与目标交易日不符，整池拒绝接入')
        count=finite(item.get('zbc'),minimum=0); height=finite(item.get('lbc'),minimum=1)
        if int(count)!=count or int(height)!=height:raise ProviderError('公开涨停池次数不是整数')
        accepted.append({'instrument_id':symbol,'thscode':symbol,'code':code,'name':item.get('n'),'first_limit_time':clock(item.get('fbt')),'last_limit_time':clock(item.get('lbt')),'break_count':int(count),'turnover_rate':finite(item.get('hs'),minimum=0),'seal_money':finite(item.get('fund'),minimum=0),'amount':finite(item.get('amount'),minimum=0),'board_count':int(height),'source_industry':item.get('hybk'),'close':price,'source':'eastmoney-public','date':day,'price_check':'目标交易日未复权收盘价逐股核对通过'})
    if not accepted:raise ProviderError('公开涨停池没有可与本地目标日期核验的记录')
    return {'items':accepted,'date':day,'declared_total':declared,'verified_rows':len(accepted),'quarantine':quarantine,'complete':not quarantine,'scope':SCOPE,'provider_qdate':data.get('qdate'),'date_validation':'请求date + 每个已接受证券的目标日收盘价交叉核验；qdate仅留作查询响应诊断，不作为交易日','source':'eastmoney-public'}
