"""Explicit full current-membership collection; does not invent historical constituents."""
from __future__ import annotations
from datetime import datetime,time,timezone
from pathlib import Path
import json
import shutil
from zoneinfo import ZoneInfo
from app.core import atomic_json,connect,data_dir,get_market,now_ms,work_dir,hash_file
from app.analytics.market import MarketData,date_ms
from app.services.source_service import client
from app.services.bootstrap import register_market
from app.adapters.providers import ProviderError


def _ms(day):
    return int(datetime.combine(datetime.fromisoformat(day).date(),time(),ZoneInfo('Asia/Shanghai')).timestamp()*1000)


def execute_topic_collection(job_id,progress=None):
    directory=work_dir()/job_id;raw=directory/'raw';raw.mkdir(parents=True,exist_ok=True)
    plan_path=directory/'topic-plan.json'
    if plan_path.exists():plan=json.loads(plan_path.read_text())
    else:
        status=get_market().status()
        if not status.get('ready'):raise ValueError('请先完成真实行情初始化，再同步题材成员')
        plan={'generation':status['snapshot_id'],'collected_at':datetime.now(timezone.utc).isoformat(),'observed_date':datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(),'storage_layout':'market-partitions-v1'}
        atomic_json(plan_path,plan)
    market=MarketData(data_dir(),snapshot_id=plan['generation']);status=market.status();reference=market.reference()
    with connect() as connection:job=connection.execute('SELECT target_date FROM job WHERE job_id=?',(job_id,)).fetchone()
    if job and job[0] and job[0]!=status['data_end']:raise ValueError('题材任务冻结日期与行情代际不一致；请手动新建任务，不能漂移后续链日期')
    adapter=client('fuyao')
    if progress:progress('题材目录',2,'读取完整概念和行业目录')
    warnings=[]
    def response(name,path,params):
        target=raw/(name+'.json')
        accepted=plan.setdefault('files',{})
        if target.exists() and name in accepted and hash_file(target)!=accepted[name]:raise ValueError('已保存题材批次校验和不符，请重新创建同步任务')
        if not target.exists():atomic_json(target,adapter.request(path,params))
        accepted[name]=hash_file(target);atomic_json(plan_path,plan)
        return json.loads(target.read_text())
    concepts=response('fuyao_concept_catalog','/api/a-share-index/catalog/ths-index-list',{'tag':'cn_concept'})['data']['item']
    industries=response('fuyao_industry_catalog','/api/a-share-index/catalog/ths-index-list',{'tag':'industry'})['data']['item']
    catalog={}
    for kind,entries in [('concept',concepts),('industry',industries)]:
        for item in entries:catalog[item['thscode']]={**item,'kind':kind}
    definitions=[];total=len(catalog);failures=[]
    for index,(code,item) in enumerate(catalog.items()):
        if progress:progress('题材成员',5+int(index/max(total,1)*67),f"同步题材成员 {index+1}/{total} · {item['name']}",{'actual_completed_batches':index,'actual_total_batches':total})
        name='members-'+code
        try:
            body=response(name,'/api/a-share-index/constituents/ths-stock-list',{'thscode':code})
            members=body['data'].get('item')
            if not isinstance(members,list):raise ProviderError('题材成员响应格式未通过')
            definitions.append({'file':name+'.json','topic_code':code,'name':item['name'],'kind':item['kind'],'observed_date':plan['observed_date'],'complete':True})
        except ProviderError as error:
            if error.status in (401,403):raise
            failures.append({'code':code,'name':item['name'],'reason':str(error)})
    if not definitions:raise ValueError('没有取得任何完整题材成员，不发布空题材库')
    if progress:progress('题材成员完成',73,f'已保存{len(definitions)}/{total}组题材成员',{'actual_completed_batches':total,'actual_total_batches':total})
    sectors=[];codes=list(catalog)
    for start in range(0,len(codes),50):
        if progress:progress('板块行情',74+int(start/max(len(codes),1)*6),'核验板块快照，仅用于当前盘面观察')
        try:
            body=response('sectors-'+str(start),'/api/a-share-index/prices/snapshot',{'thscodes':','.join(codes[start:start+50])})
            sectors.extend(body['data'].get('item',[]))
        except ProviderError as error:warnings.append('部分板块快照缺失：'+str(error))
    atomic_json(raw/'fuyao_sectors_snapshot_sample.json',{'data':{'item':sectors}})
    def envelope(name,items):atomic_json(raw/(name+'.json'),{'data':{'item':items}})
    envelope('fuyao_stock_master',[r for r in reference['master'].values() if r.get('kind')=='stock'])
    envelope('fuyao_calendar_year',[{'date_ms':_ms(d),'date':d.replace('-','')} for d in reference['calendar']])
    for symbol,bars in reference['indices'].items():
        envelope('fuyao_index_year_'+symbol.replace('.','_'),[{'date_ms':_ms(r['date']),'open_price':r['open'],'high_price':r['high'],'low_price':r['low'],'close_price':r['close'],'volume':r['volume'],'turnover':r['amount']} for r in bars])
    pool_coverage=reference.get('pool_coverage',{}).copy()
    for field,filename,endpoint in [('limitup','fuyao_limitup_latest','limit-up-pool'),('limitdown','fuyao_limitdown_latest','limit-down-pool'),('breakpool','fuyao_breakpool_latest','limit-break-pool')]:
        body=response('verified-'+filename,'/api/a-share/special-data/'+endpoint,{'date_ms':_ms(status['data_end']),'page':1,'size':200})
        rows=body['data'].get('item',[]);pagination=body['data'].get('pagination',{})
        if pagination.get('has_next') or pagination.get('total')!=len(rows):raise ValueError('事件池分页未完整，保留旧发布版本')
        for event in rows:
            history=market.bars(event['thscode'],status['data_end'])
            price=event.get('last_price')
            if price is not None and (not history or history[-1]['date']!=status['data_end'] or abs(history[-1]['close']-price)>.011):raise ValueError('事件池与冻结交易日行情价格不符')
        atomic_json(raw/(filename+'.json'),body);pool_coverage[field]={'complete':True,'observed_rows':len(rows)}
    envelope('fuyao_abnormal_current',reference.get('abnormal',[]))
    atomic_json(raw/'fuyao_ladder_30d.json',{'data':reference.get('ladder',{})})
    atomic_json(raw/'fuyao_pool_history.json',reference.get('pool_history',{}))
    base=Path(status['path'])
    import duckdb
    con=duckdb.connect(':memory:')
    try:con.execute('COPY (SELECT * FROM read_parquet(?) ORDER BY thscode,date_ms) TO ? (FORMAT PARQUET,COMPRESSION ZSTD)',[str(directory/'year.parquet'),market.parquet_input('raw-daily.parquet')])
    finally:con.close()
    if (base/'corporate-actions.parquet').exists():shutil.copyfile(base/'corporate-actions.parquet',raw/'fuyao_bulk_adjustments.parquet')
    if failures:warnings.append(f'{len(failures)}个题材成员未取得，本次不宣称全量覆盖')
    warnings.extend([w for w in status.get('warnings',[]) if '题材' not in w])
    warnings.append('题材为采集时点的当前成员；行业目录包含不同层级，不构造历史PIT归属。')
    missing=status.get('unexplained_missing')
    # SQLite stores only the confirmed lower bound when identity is unknown.
    # Re-publication must preserve unknown, rather than turning that bound into
    # a claimed exact missing-row count. The lower bound stays in coverage.
    bundle={'source':'fuyao','price_basis':'none','daily_file':'year.parquet','collected_at':plan['collected_at'],'quality_warnings':warnings,'unexplained_missing':missing,'coverage':status.get('coverage',{}),'pool_trade_date':reference.get('pool_trade_date',status['data_end']),'pool_history_file':'fuyao_pool_history.json','topic_files':definitions,'topic_coverage':f'{len(definitions)}/{total}个当前题材成员快照','sectors_snapshot_date_semantics':'current_unverified_trade_date','sectors_snapshot_observed_at':plan['collected_at'],'pool_coverage':pool_coverage}
    # 此任务只复用监管名单，不能用题材本次采集时间改写名单来源日期。
    # 显式保留未知，避免导入器把缺省字段回退为本次 collected_at。
    retained_monitor=isinstance(reference.get('abnormal'),list)
    bundle.update(abnormal_observed_date=reference.get('abnormal_observed_date') if retained_monitor else None,
                  abnormal_fetched_at=reference.get('abnormal_fetched_at') if retained_monitor else None)
    if plan.get('storage_layout'):bundle['storage_layout']=plan['storage_layout']
    atomic_json(directory/'bundle.json',bundle);atomic_json(directory/'collection-quality.json',{'topic_count':len(definitions),'catalog_count':total,'failures':failures,'sector_count':len(sectors)})
    if progress:progress('题材校验',81,'构建固定行情代际与完整成员库')
    imported=MarketData(data_dir()).import_audit(directory,lambda stage,p:progress(stage,81+int(p*.01)) if progress else None)
    result=register_market(imported,job_id,progress)
    from app.services.market_derived_cleanup import release_after_publication
    cleanup=release_after_publication(job_id,directory,imported,plan,source_kind='topics')
    return {**result,'quality':'partial','topic_count':len(definitions),'catalog_count':total,'message':'真实当前题材成员库已发布','derived_cleanup':cleanup}
