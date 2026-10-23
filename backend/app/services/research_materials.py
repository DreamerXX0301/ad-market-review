"""Factual research materials from verified public contracts, with visible gaps."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from app.core import now_ms,digest
from app.adapters.providers import ProviderError
from app.adapters.research_public import ResearchPublicClient,number,day
from app.adapters.research_budget import ResearchPublicBudget

CHINA=ZoneInfo('Asia/Shanghai')
STATEMENTS=[('income','利润与业绩','income-statements'),('balance','资产负债','balance-sheets'),('cashflow','现金流','cash-flow-statements')]
INDICATORS={'operating_income_yoy_growth_ratio':('营业收入同比','%'),'net_profit_yoy_growth_ratio':('净利润同比','%'),'sale_gross_margin':('销售毛利率','%'),'sale_net_interest_ratio':('销售净利率','%'),'index_weighted_avg_roe':('加权净资产收益率','%'),'assets_debt_ratio':('资产负债率','%'),'net_profit_cash_content':('净利润现金含量','%')}


def collect_materials(symbol,adapter=None,*,public=None,as_of_ms=None,progress=None,enhanced=False,plan=None,checkpoint=None):
    collection_started_at_ms=now_ms()
    explicit_as_of=as_of_ms is not None
    as_of_ms=collection_started_at_ms if as_of_ms is None else as_of_ms
    until=datetime.fromtimestamp(as_of_ms/1000,CHINA).date();start=until-timedelta(days=365)
    if enhanced:
        if not plan or plan.get('version')!='evidence-research-v2':raise ValueError('研究增强任务缺少已冻结计划，请手动创建新任务')
        until=datetime.fromisoformat(plan['windows']['announcements']['end']).date()
        start=datetime.fromisoformat(plan['windows']['announcements']['start']).date()
    materials=[];gaps=[];statements={};financial_metrics=[];valuation={};holder={};announcements={};market_cap={}
    public=public or ResearchPublicClient()
    def check():
        if checkpoint:checkpoint()
    def missing(capability,title,reason,source='fuyao'):
        gaps.append({'capability':capability,'title':title,'source':source,'state':'unavailable','reason':reason})
    def material(key,title,source,data,*,period=None,timestamp=None,unit=None,coverage=None,endpoint=None,published=None,fetched=None,validation_as_of=None):
        record={'material_id':key,'title':title,'source':source,'endpoint':endpoint,'kind':key,'report_period':period,'published_at_ms':published,'source_timestamp_ms':timestamp,'fetched_at_ms':now_ms() if fetched is None else fetched,'unit':unit,'coverage':coverage or {},'data':data,'official_document':False}
        record['response_json_sha256']=digest(data.get('raw',data.get('raw_pages',data)) if isinstance(data,dict) else data)
        if validation_as_of is not None:record['validation_as_of_ms']=validation_as_of
        materials.append(record)
    if adapter is not None:
        for index,(key,title,endpoint) in enumerate(STATEMENTS):
            if progress:progress('财报与估值',25+index*7,'查询'+title+'实际披露材料')
            try:
                check()
                body=adapter.request('/api/a-share/financials/'+endpoint,{'thscode':symbol,'period':'quarterly','limit':8})
                values=body.get('data',{}).get('item',[])
                accepted=[]
                for row in values:
                    period=day(row.get('period_end_ms'));published=number(row.get('report_date_ms'))
                    if row.get('thscode')!=symbol or row.get('currency')!='CNY' or period is None or period>until.isoformat() or published is None or published>as_of_ms:continue
                    accepted.append({**row,'report_period':period,'published_at_ms':published,'source':'fuyao','unit':'CNY；basic_eps为元/股'})
                if not accepted:raise ProviderError('没有取得证券、币种与披露日期均可核验的财报')
                accepted.sort(key=lambda row:row['period_end_ms'],reverse=True);statements[key]=accepted
                material('financial_'+key,title,'fuyao',body,period=accepted[0]['report_period'],timestamp=body['data'].get('timestamp'),unit='CNY；EPS为元/股',endpoint='/api/a-share/financials/'+endpoint,published=accepted[0]['published_at_ms'])
                if len(accepted)!=len(values):missing('financial_'+key,title,'部分财报日期、证券或币种未通过核验，已隔离')
            except (ProviderError,TypeError,AttributeError,KeyError) as error:missing('financial_'+key,title,str(error) if isinstance(error,ProviderError) else '财报响应字段发生变化')
        try:
            check()
            body=adapter.request('/api/a-share/valuations/snapshot',{'thscodes':symbol});fetched_at_ms=now_ms()
            data=body.get('data',{});raw_stamp=data.get('timestamp');stamp=number(raw_stamp)
            # This endpoint is latest-only. In a manual live collection its
            # metadata may normally update after the earlier financial calls.
            # Explicit historical cutoffs remain strict; neither boundary is
            # substituted for the actual source timestamp.
            validation_as_of_ms=as_of_ms if explicit_as_of else fetched_at_ms
            row=next((row for row in data.get('item',[]) if row.get('thscode')==symbol),None)
            if row is None or not isinstance(raw_stamp,(int,float)) or isinstance(raw_stamp,bool) or stamp is None or stamp!=int(stamp) or not 10**12<=stamp<10**14 or stamp>validation_as_of_ms:raise ProviderError('估值标的或上游元数据时间未通过核验')
            stamp=int(stamp)
            valuation={key:number(row.get(key)) for key in ('pe_ttm','pe_mrq','pb_mrq','ps_ttm','pcf_ttm')}
            valuation.update(source='fuyao',source_timestamp_ms=stamp,fetched_at_ms=fetched_at_ms,validation_as_of_ms=validation_as_of_ms,validation_basis='explicit_research_cutoff' if explicit_as_of else 'latest_fetch_completed',future_tolerance_ms=0,unit='倍',scope='最新估值快照；时间为上游指标最大有效元数据时间，各指标未必同时更新')
            material('valuation','PE/PB/PS/PCF最新快照','fuyao',body,timestamp=stamp,fetched=fetched_at_ms,validation_as_of=validation_as_of_ms,unit='倍',endpoint='/api/a-share/valuations/snapshot')
            if all(valuation[key] is None for key in ('pe_ttm','pe_mrq','pb_mrq','ps_ttm','pcf_ttm')):missing('valuation','最新估值','接口返回标的，但估值字段均为空，未补算')
        except (ProviderError,TypeError,AttributeError,KeyError) as error:missing('valuation','最新估值',str(error) if isinstance(error,ProviderError) else '估值响应字段发生变化')
        latest=statements.get('income',[{}])[0]
        quarter='4' if latest.get('fiscal_period')=='FY' else str(latest.get('fiscal_period','')).replace('Q','')
        if latest.get('fiscal_year') and quarter in ('1','2','3','4'):
            report=str(latest['fiscal_year'])+'-'+quarter
            try:
                check()
                body=adapter.request('/api/a-share/financials/indicators',{'thscode':symbol,'report':report});data=body.get('data',{})
                if data.get('thscode')!=symbol or data.get('report')!=report:raise ProviderError('财务指标报告期或标的不匹配')
                for ability in data.get('abilities',[]):
                    for entry in ability.get('indicators',[]):
                        if entry.get('index_id') in INDICATORS:
                            label,unit=INDICATORS[entry['index_id']]
                            financial_metrics.append({'field':entry['index_id'],'name':label,'value':number(entry.get('value')),'raw_value':entry.get('value'),'unit':unit,'report_period':report,'source':'fuyao'})
                material('financial_indicators','财务指标与业绩同比','fuyao',body,period=report,unit='按指标注明',endpoint='/api/a-share/financials/indicators')
            except (ProviderError,TypeError,AttributeError,KeyError) as error:missing('financial_indicators','财务指标',str(error) if isinstance(error,ProviderError) else '财务指标响应字段发生变化')
    else:
        missing('financial','财报与估值','扶摇未配置、已禁用或当前Key不可读取；不根据套餐猜测权限')
    budget=None;company_profile={}
    if enhanced:
        budget=ResearchPublicBudget(plan['source_budget'],checkpoint=checkpoint)
        public.budget=budget
    if progress:progress('公告与股东材料',60,'查询近一年公开披露及股东人数；分页有上限')
    for key,title,source,call in [
        ('announcements','近一年公告','cninfo',lambda:public.announcements(symbol,start.isoformat(),until.isoformat())),
        ('shareholders','股东人数','eastmoney',lambda:public.shareholders(symbol,until.isoformat())),
        ('market_cap','来源快照市值','eastmoney',lambda:public.market_cap(symbol,until.isoformat())),
    ]:
        try:
            check()
            value=call()
            endpoint={'announcements':'https://www.cninfo.com.cn/new/hisAnnouncement/query','shareholders':'https://datacenter-web.eastmoney.com/api/data/v1/get','market_cap':'https://push2.eastmoney.com/api/qt/stock/get'}[key]
            material(key,title,source,value,coverage=value.get('coverage'),unit='户 / CNY' if key=='shareholders' else 'CNY' if key=='market_cap' else '公告原文链接',endpoint=endpoint)
            if key=='announcements':announcements=value
            elif key=='shareholders':holder=value
            else:market_cap=value
            if value.get('coverage',{}).get('complete') is False:missing(key,title,value['coverage'].get('reason') or '来源只取得部分材料',source)
            if key=='shareholders' and not value.get('items'):missing(key,title,'没有取得可核验股东人数',source)
        except (ProviderError,TypeError,AttributeError,KeyError) as error:missing(key,title,str(error) if isinstance(error,ProviderError) else '公开来源响应字段发生变化',source)
    if enhanced:
        if progress:progress('公司与主营证据',75,'查询指定证券的当前主营构成；不推导客户或供应商')
        try:
            check()
            company_profile=public.company_profile(symbol,until.isoformat(),max_rows=plan['source_budget']['company_max_rows'])
            material('company_profile','当前取得主营构成','eastmoney',company_profile,period=company_profile.get('report_period'),
                     coverage=company_profile.get('coverage'),unit='CNY；比例为来源原始比率',endpoint='https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax')
            if company_profile.get('coverage',{}).get('complete') is not True:
                missing('company_profile','主营构成',company_profile.get('coverage',{}).get('reason') or '来源仅返回部分主营资料','eastmoney')
        except (ProviderError,TypeError,AttributeError,KeyError) as error:
            missing('company_profile','主营构成',str(error) if isinstance(error,ProviderError) else '主营构成接口未提供或响应字段发生变化','eastmoney')
    tags={'减持':('减持',),'回购':('回购',),'增持':('增持',),'业绩':('业绩预告','业绩快报','年度报告','半年度报告','季度报告'),'监管与风险':('监管','问询','风险提示','立案')}
    events=[]
    for row in announcements.get('items',[]):
        categories=[label for label,keywords in tags.items() if any(keyword in row['title'] for keyword in keywords)]
        if categories:events.append({**row,'categories':categories,'classification_basis':'公告标题关键词；不代表交易已完成或监管结论'})
    result={'materials':materials,'gaps':gaps,'financials':{'statements':statements,'metrics':financial_metrics,'scope':'已披露合并报表；季度值沿用来源口径，不擅自拆单季'},'valuation':valuation,'market_cap':market_cap,'shareholders':holder,'announcements':{key:value for key,value in announcements.items() if key!='raw_pages'},'events':events,'material_window':{'start':start.isoformat(),'end':until.isoformat(),'research_as_of_ms':as_of_ms,'as_of_mode':'explicit_cutoff' if explicit_as_of else 'manual_latest','collection_started_at_ms':collection_started_at_ms,'collection_finished_at_ms':now_ms(),'announcement_max_pages':12},'source_notes':['Fuyao公开财报与估值已逐接口请求；资讯事件库/新闻公告原文不属于该公开Key能力。','巨潮保留官方公告详情/PDF链接，本项目不下载或伪造全文；标题分类只用于材料检索。','财报披露日、股东统计期、估值元数据时间与本地行情截止日分别展示；不伪装成历史时点研究。','默认手动研究的最新估值按该次请求完成时刻核验，零未来时间容忍；来源元数据时间、抓取完成时刻与材料采集起止分别保留。']}
    if enhanced:result.update(company_profile=company_profile,public_request_usage=budget.view())
    return result
