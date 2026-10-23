"""Manual, resumable market supplements outside immutable base generations.

Each accepted day is content checked and reusable; only the final index activates
this run. API reads never fetch remote data. Failed dates retain explicit errors
and may be retried manually without redownloading complete accepted dates.
"""
from __future__ import annotations

from datetime import datetime, date
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import duckdb

from app.core import atomic_json, connect, published_dir, digest, dumps, get_market, hash_file, now_ms
from app.adapters.providers import ProviderError
from app.adapters.public_market import PublicMarketClient, normalize_limit_pool
from app.adapters.public_fund_budget import FundRequestBudget, validate_fund_budget_version
from app.services.task_service import TaskCancelled, TaskPaused
from app.services.limit_history import fetch_fuyao_pool, annual_coverage, pool_spec, _midnight, NORMALIZATION_VERSION

CHINA=ZoneInfo('Asia/Shanghai')
VERSION='market-supplement-v1'


def _load(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def _root(): return published_dir()/'market-supplement'


def load_supplement(snapshot_id=None):
    head=_load(_root()/'head.json',{})
    if snapshot_id and head.get('snapshot_id')!=snapshot_id:return {}
    if not head:return {}
    relative=head.get('artifact'); path=(_root()/str(relative)).resolve()
    if not path.is_relative_to(_root().resolve()) or not path.is_file() or hash_file(path)!=head.get('sha256'):return {'quality':'unknown','failure_reason':'补充数据索引缺失或校验失败'}
    result=_load(path,{})
    return result if result.get('version')==VERSION else {'quality':'unknown','failure_reason':'补充数据版本不匹配'}


class SupplementCache:
    def __init__(self, root=None):
        self.root=Path(root or _root()); self.root.mkdir(parents=True,exist_ok=True)

    def reuse(self, source, capability, day, signature, *, allow_partial=False):
        pointer=_load(self.root/'accepted'/source/capability/(day+'.json'),{})
        if pointer.get('signature')!=signature or pointer.get('version')!=VERSION:return None
        path=(self.root/str(pointer.get('artifact',''))).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file() or hash_file(path)!=pointer.get('sha256'):raise ValueError('补充批次缓存损坏，拒绝复用；请移出损坏文件后手动重试')
        result=_load(path,{})
        raw=(self.root/str(result.get('raw_artifact',''))).resolve()
        if not raw.is_relative_to(self.root.resolve()) or not raw.is_file() or hash_file(raw)!=result.get('raw_sha256'):raise ValueError('补充原始证据缓存缺失或损坏，拒绝复用')
        return result if result.get('complete') is True or allow_partial else None

    def accept(self, source, capability, day, signature, result, raw):
        raw_hash=digest(raw); raw_path=self.root/'raw'/(raw_hash+'.json')
        if raw_path.exists():
            try:valid=digest(_load(raw_path))==raw_hash
            except (ValueError,OSError):valid=False
            if not valid:raise ValueError('补充原始证据对象已损坏，拒绝将新响应关联到损坏缓存')
        else:atomic_json(raw_path,raw)
        result={**result,'fetched_at':result.get('fetched_at') or datetime.now(CHINA).isoformat(),'raw_sha256':hash_file(raw_path),'raw_artifact':str(raw_path.relative_to(self.root)),'validation_version':VERSION}
        content=digest(result); path=self.root/'artifacts'/(content+'.json')
        atomic_json(path,result)
        atomic_json(self.root/'accepted'/source/capability/(day+'.json'),{'version':VERSION,'signature':signature,'normalization_version':result.get('normalization_version',VERSION),'artifact':str(path.relative_to(self.root)),'sha256':hash_file(path)})
        return result

    def pool_raw_evidence(self, day, pool_kind):
        """Read old Fuyao evidence independently of its old context signature.

        Returns raw pages and provenance only, never trusted normalized items.
        A caller must run the current normalizer against the current market.
        """
        pool_spec(pool_kind)
        if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day:
            raise ValueError('原始证据日期无效')
        pointer=_load(self.root/'accepted/fuyao'/pool_kind/(day+'.json'))
        if pointer is None:return None
        if not isinstance(pointer,dict) or pointer.get('version')!=VERSION:
            raise ValueError('补充批次证据版本不匹配')
        def checked(relative, expected, label):
            path=(self.root/str(relative or '')).resolve()
            if not path.is_relative_to(self.root.resolve()) or not path.is_file() or hash_file(path)!=expected:
                raise ValueError(label+'缺失或SHA校验失败')
            return _load(path)
        record=checked(pointer.get('artifact'),pointer.get('sha256'),'补充批次缓存')
        if not isinstance(record,dict) or record.get('date')!=day or record.get('pool_kind','limitup')!=pool_kind or record.get('source')!='fuyao' or record.get('validation_version')!=VERSION:
            raise ValueError('补充批次证据的日期、来源或涨跌停类型不一致')
        raw=checked(record.get('raw_artifact'),record.get('raw_sha256'),'补充原始证据')
        if not isinstance(raw,list) or not 1<=len(raw)<=100 or any(not isinstance(page,dict) for page in raw):
            raise ValueError('补充原始证据分页结构无效或超出100页预算')
        return {'pages':raw,'signature':pointer.get('signature'),'fetched_at':record.get('fetched_at'),
                'validated_at':record.get('validated_at'),'validation_context_id':record.get('validation_context_id')}

    def publish(self, document, guard=None):
        content=digest(document); path=self.root/'indexes'/(content+'.json')
        atomic_json(path,document)
        if guard:guard()
        atomic_json(self.root/'head.json',{'snapshot_id':document['snapshot_id'],'version':VERSION,'artifact':str(path.relative_to(self.root)),'sha256':hash_file(path),'published_at_ms':now_ms()})


def _year_dates(calendar,target):
    # At most two batches per retained date in this one calendar year; never
    # discover arbitrary files or silently broaden to other historical years.
    return sorted({day for day in calendar if isinstance(day,str) and day[:4]==target[:4] and day<=target and date.fromisoformat(day).isoformat()==day})


def _pool_signature(day,source,closes,identity_hash,pool_kind=None):
    value={'quotes':closes,'master':identity_hash,'day':day,'source':source}
    if pool_kind=='limitdown':value['pool_kind']=pool_kind
    return digest(value)


def _revalidate_raw(cache,day,pool_kind,signature,closes,master,guard=None):
    evidence=cache.pool_raw_evidence(day,pool_kind)
    if evidence is None:return None
    record,_=fetch_fuyao_pool(None,day,closes,master,payload_pages=evidence['pages'],pool_kind=pool_kind)
    context=digest({'signature':signature,'normalization':NORMALIZATION_VERSION})
    record.update(fetched_at=evidence['fetched_at'],validation_context_id=context,
                  validated_at=evidence['validated_at'] if evidence.get('validation_context_id')==context else datetime.now(CHINA).isoformat())
    if guard:guard()
    return cache.accept('fuyao',pool_kind,day,signature,record,evidence['pages'])


def _publish_document(doc,calendar,target,cache,guard=None,on_publish=None,sector_history=False):
    doc['annual_coverage']=annual_coverage(doc,calendar,target)
    doc['annual_down_coverage']=annual_coverage(doc,calendar,target,pool_kind='limitdown')
    doc['quality']='partial' if doc['failures'] or not doc['annual_coverage']['complete'] or not doc['annual_down_coverage']['complete'] else 'complete'
    doc['message']=('本地原始证据离线重验；' if doc.get('revalidate_only') else '来源池已核验；')+'年内涨停 '+str(doc['annual_coverage']['covered_days'])+'/'+str(doc['annual_coverage']['expected_days'])+'，跌停 '+str(doc['annual_down_coverage']['covered_days'])+'/'+str(doc['annual_down_coverage']['expected_days'])+' 个交易日完整；当年之外未回补，缺口保留并可重试'
    if sector_history:
        coverage=doc['sector_index_history'].get(target,{}).get('coverage',{})
        doc['message']+='；板块日行情 '+str(coverage.get('accepted_count',0))+'/'+str(coverage.get('catalog_count',0))+' 个，历史成分另行核验'
    if on_publish:on_publish('发布补充数据索引',doc['message'])
    cache.publish(doc,guard)
    return doc


def revalidate_for_market(market,target,*,progress=None,guard=None,job_id=None):
    """Explicit offline repair: only hash-checked local raw pages may be used."""
    status=market.status();calendar=market.calendar()
    if target not in calendar or target!=status.get('data_end'):raise ValueError('离线重验目标必须是当前已发布行情日')
    root=_root();cache=SupplementCache(root);old=load_supplement()
    days=_year_dates(calendar,target);total=len(days)*2
    master=[row for row in market.instruments(limit=10000) if row.get('kind')=='stock']
    identity_hash=digest({row['code']:row['instrument_id'] for row in master})
    doc={'version':VERSION,'snapshot_id':status['snapshot_id'],'target_date':target,'job_id':job_id,'created_at_ms':now_ms(),
         'revalidate_only':True,'public_pools':{},'annual_pools':{},'annual_down_pools':{},
         'fund_flows':{day:record for day,record in old.get('fund_flows',{}).items() if day in calendar and day<=target},
         'sector_index_history':{day:record for day,record in old.get('sector_index_history',{}).items() if day in calendar and day<=target},
         'failures':[failure for failure in old.get('failures',[]) if failure.get('capability') not in ('year_limitup','year_limitdown','seal_details')]}
    stats={'planned_batches':total,'completed_batches':0,'revalidated_batches':0,'missing_batches':0,'failed_batches':0}
    def tick(stage,message):
        if guard:guard()
        if progress:progress(stage,min(99,int(stats['completed_batches']/max(1,total)*98)),message,
                             {'actual_completed_batches':stats['completed_batches'],'actual_total_batches':total,'revalidate_only':True})
    con=duckdb.connect(':memory:')
    try:
        con.execute("SET threads=1");con.execute("SET memory_limit='256MB'")
        con.execute('CREATE TABLE quotes AS SELECT instrument_id,date,close FROM read_parquet(?) WHERE date>=? AND date<=?',
                    [status['path']+'/daily.parquet',target[:4]+'-01-01',target])
        def prices(day):return dict(con.execute('SELECT instrument_id,close FROM quotes WHERE date=?',[day]).fetchall())
        tick('离线重验年内涨跌停','仅使用本地已留存原始证据，不读取凭据或发起网络请求')
        for day in days:
            closes=prices(day)
            for kind in ('limitup','limitdown'):
                if guard:guard()
                spec=pool_spec(kind)
                try:
                    signature=_pool_signature(day,'fuyao',closes,identity_hash,kind)
                    record=_revalidate_raw(cache,day,kind,signature,closes,master,guard)
                    if record is None:
                        stats['missing_batches']+=1
                        doc['failures'].append({'capability':spec['failure_capability'],'pool_kind':kind,'date':day,'reason':'未留存该日来源池原始证据；离线重验不补拉，保持未知'})
                    else:
                        doc[spec['document_key']][day]=record;stats['revalidated_batches']+=1
                except (ValueError,ProviderError) as error:
                    stats['failed_batches']+=1
                    doc['failures'].append({'capability':spec['failure_capability'],'pool_kind':kind,'date':day,'reason':'本地原始证据重验失败：'+str(error)})
                stats['completed_batches']+=1
                tick('离线重验年内涨跌停',day+' · '+spec['label']+' · '+str(stats['completed_batches'])+'/'+str(total))
        # Existing public seal details are retained only under an exact current
        # quote/identity signature; this repair never requests public providers.
        for day in old.get('public_pools',{}):
            if day not in calendar or day>target:continue
            try:
                record=cache.reuse('eastmoney','limitup',day,_pool_signature(day,'eastmoney-public',prices(day),identity_hash),allow_partial=True)
                if record is not None and record.get('date')==day:doc['public_pools'][day]=record
                else:doc['failures'].append({'capability':'seal_details','date':day,'reason':'封板明细当前签名未匹配；离线任务不联网补拉，保持未知'})
            except ValueError as error:doc['failures'].append({'capability':'seal_details','date':day,'reason':str(error)})
    finally:con.close()
    doc['revalidation']=stats
    return _publish_document(doc,calendar,target,cache,guard,tick)


def fetch_history_for_market(public, market, target, cache, *, progress=None, guard=None, request_budget_version=None):
    from app.adapters.public_fund_history import fetch_sector_fund_history
    validate_fund_budget_version(request_budget_version)
    budget=FundRequestBudget() if request_budget_version is not None else None
    calendar=market.calendar();expected=[d for d in calendar if d<=target][-20:]
    def identity(code):return digest({'source':'eastmoney-public','capability':'industry-fund-history','code':code,'target':target,'expected_dates':expected,'contract':'public-fund-history-v1'})
    def lookup(code,day):
        if guard:guard()
        result=cache.reuse('eastmoney','fund-board',day+'-'+code,identity(code),allow_partial=budget is not None)
        return _load(cache.root/result['raw_artifact']) if result else None
    def save(code,day,normalized,raw):
        if guard:guard()
        cache.accept('eastmoney','fund-board',day+'-'+code,identity(code),{**normalized,'date':day,'topic_code':code,'complete':normalized['history_complete']},raw)
    summaries=[];raw_rounds=[];used=0;result=None;retry_codes=None;prior_failures={};directory_signature=None
    for round_no in (1,2):
        remaining=200-used
        if remaining<2 or budget is not None and not budget.available():break
        if guard:guard()
        def on_progress(done,total,code):
            if guard:guard()
            if progress:progress(done,total,'第'+str(round_no)+'轮'+('补缺' if round_no==2 else '采集')+' · '+code)
        try:
            policy={'request_budget':budget,'guard':guard,'retry_codes':retry_codes,'prior_failures':prior_failures} if budget is not None else {}
            current=fetch_sector_fund_history(public,target,scope='industry',limit=None,max_requests=remaining,calendar=calendar,progress=on_progress,cache_lookup=lookup,cache_save=save,**policy)
        except ProviderError as error:
            # Directory I/O/shape failures happen before per-board calls. Count
            # that logical request too, and retain the failed round explicitly.
            current={'items':[],'history':[],'date':target,'source':'eastmoney-public','complete':False,'coverage':{'directory_count':None,'selected_count':0,'accepted_count':0,'failed_count':1,'unrequested_count':0,'cached_count':0,'logical_request_count':1,'complete':False,'history_20d_complete':False},'raw_pages':[],'failures':[{'stage':'directory','reason':str(error)}],'intraday_sequence_available':False}
        coverage=current.get('coverage',{});catalog_changed=False
        if budget is not None and coverage.get('directory_signature'):
            observed_signature=coverage['directory_signature']
            if directory_signature is None:
                directory_signature=observed_signature
            elif directory_signature!=observed_signature:
                catalog_changed=True;budget.stop_reason='directory_changed'
                failure={'stage':'directory_changed','reason':'同任务两轮行业目录代码、名称或分类变化；保留首个已确认目录的记录及缺口，不缩小分母认定完整','failure_kind':'schema','retryable':False}
                current['failures'].append(failure);current['complete']=False
                coverage.update(complete=False,history_20d_complete=False,retry_eligible=False,
                    failed_count=len(current['failures']),stop_reason='directory_changed',directory_scope_changed=True,
                    frozen_directory_signature=directory_signature,observed_directory_signature=observed_signature)
                # First-round records and gaps keep their original denominator.
                # The changed round remains in raw_rounds, not a replacement
                # scope silently able to hide the original failed industry.
                result={**result,'complete':False,'failures':[*result.get('failures',[]),failure],
                    'coverage':{**result.get('coverage',{}),'complete':False,'history_20d_complete':False,
                        'retry_eligible':False,'failed_count':len(result.get('failures',[]))+1,
                        'directory_scope_changed':True,'frozen_directory_signature':directory_signature,
                        'observed_directory_signature':observed_signature}}
        spent=coverage.get('logical_request_count')
        if isinstance(spent,bool) or not isinstance(spent,int) or not (0 if budget is not None else 1)<=spent<=remaining:raise ProviderError('资金流适配器请求计数超出本任务剩余预算')
        used+=spent
        summary={'round':round_no,'budget_before':remaining,'logical_request_count':spent,'budget_remaining':200-used,'cached_count':coverage.get('cached_count',0),'accepted_count':coverage.get('accepted_count',0),'failed_count':coverage.get('failed_count',0),'unrequested_count':coverage.get('unrequested_count',0),'complete':bool(coverage.get('complete')),'history_20d_complete':bool(coverage.get('history_20d_complete')),'failures':list(current.get('failures',[]))}
        if budget is not None:
            summary.update(actual_transport_attempt_count=coverage.get('actual_transport_attempt_count',0),source_circuit_open=coverage.get('source_circuit_open',False),stop_reason=coverage.get('stop_reason'),retry_eligible=coverage.get('retry_eligible',False),elapsed_seconds=coverage.get('elapsed_seconds'))
        summaries.append(summary)
        raw_rounds.append({'kind':'fund_history_round','round':round_no,'coverage':dict(coverage),'failures':list(current.get('failures',[])),'pages':current.pop('raw_pages',[])})
        # A second round may fail to read its directory after the first already
        # accepted dated records. Keep that data and its explicit missing scope.
        if not catalog_changed and (result is None or current.get('items') or not result.get('items')):result=current
        if coverage.get('complete') and coverage.get('history_20d_complete'):break
        if budget is not None and not coverage.get('retry_eligible'):break
        if budget is not None:
            prior_failures={failure['topic_code']:dict(failure) for failure in current.get('failures',[]) if failure.get('topic_code')}
            # A temporary directory failure has not attempted any history yet.
            # Its recovery may make first attempts; a confirmed directory uses
            # only the explicitly retryable failed codes on the second round.
            retry_codes=None if coverage.get('directory_count') is None else {code for code,failure in prior_failures.items() if failure.get('retryable')}
    if result is None:raise ProviderError('资金流请求预算不足，未启动额外重试')
    result['rounds']=summaries;result['raw_pages']=raw_rounds
    result['coverage']={**result.get('coverage',{}),'logical_request_count':used,'max_logical_requests':200,'max_transport_attempts':600,'round_count':len(summaries),'retry_policy':'同任务最多两轮；第二轮仅补失败且复用已验SHA缓存；两轮合计最多200个逻辑请求','rounds':summaries}
    if budget is not None:
        result['coverage'].update(budget.snapshot(),round_count=len(summaries),rounds=summaries,
            retry_policy='新冻结资金预算：两轮共享200逻辑/600传输上限、60秒异步网络截止；连续5次同类来源故障停发，第二轮仅重试暂时失败；未取得数据保留未知',
            local_cache_validation_seconds=sum(r['coverage'].get('local_cache_validation_seconds',0) for r in raw_rounds))
    return result


def refresh_for_market(market, target, *, year_history=False, recent_days=1, progress=None, guard=None, job_id=None, sector_history=False, fund_request_budget_version=None):
    validate_fund_budget_version(fund_request_budget_version)
    status=market.status(); calendar=market.calendar()
    if target not in calendar:raise ValueError('补充目标日期必须是已留存交易日')
    if target!=status['data_end']:raise ValueError('补充任务以当前冻结行情日为锚；历史盘面从该任务留存的逐日数据读取')
    root=_root(); cache=SupplementCache(root); old=load_supplement()
    doc={'version':VERSION,'snapshot_id':status['snapshot_id'],'target_date':target,'job_id':job_id,'created_at_ms':now_ms(),'public_pools':{},'annual_pools':{},'annual_down_pools':{},'fund_flows':{d:r for d,r in old.get('fund_flows',{}).items() if d in calendar and d<=target},'sector_index_history':{d:r for d,r in old.get('sector_index_history',{}).items() if d in calendar and d<=target},'failures':[]}
    original_progress=progress
    if sector_history and progress:
        progress=lambda stage,pct,message=None,details=None: original_progress(stage,int(pct*.55),message,details)
    master=[r for r in market.instruments(limit=10000) if r.get('kind')=='stock']
    identity_hash=digest({r['code']:r['instrument_id'] for r in master})
    history_dates=_year_dates(calendar,target)
    dates=history_dates if year_history else [target]
    recent=calendar[max(0,calendar.index(target)-max(1,min(int(recent_days),30))+1):calendar.index(target)+1]
    pool_batches=len(dates)*2
    local_batches=len(history_dates)*2
    planned=local_batches+pool_batches+len(recent)+1; done=0
    def tick(label,message=None):
        if guard:guard()
        remote_done=done-local_batches
        if done<local_batches:pct=int(done/max(1,local_batches)*10)
        elif remote_done<pool_batches:pct=10+int(remote_done/max(1,pool_batches)*50)
        elif remote_done<pool_batches+len(recent):pct=60+int((remote_done-pool_batches)/max(1,len(recent))*15)
        else:pct=98 if done>=planned else 75
        if progress:progress(label,pct,message or label,{'actual_completed_batches':done,'actual_total_batches':planned})
    con=duckdb.connect(':memory:')
    try:
        con.execute('CREATE TABLE quotes AS SELECT instrument_id,date,close FROM read_parquet(?)',[status['path']+'/daily.parquet'])
        def prices(day):return dict(con.execute('SELECT instrument_id,close FROM quotes WHERE date=?',[day]).fetchall())
        def signature_for(day,source,pool_kind=None):return _pool_signature(day,source,prices(day),identity_hash,pool_kind)
        def validate_cached(record,day,pool_kind=None):
            if not record:return
            if record.get('date')!=day:raise ValueError('补充批次缓存日期与请求日期不符，拒绝复用')
            if pool_kind and record.get('pool_kind','limitup')!=pool_kind:raise ValueError('补充批次缓存涨跌停类型与请求不符，拒绝复用')

        # Probe the bounded calendar, not only the previous head: older accepted
        # evidence can outlive an index that omitted it after a context change.
        for day in history_dates:
            closes=prices(day)
            for kind in ('limitup','limitdown'):
                if guard:guard()
                spec=pool_spec(kind);signature=_pool_signature(day,'fuyao',closes,identity_hash,kind)
                try:
                    record=cache.reuse('fuyao',kind,day,signature,allow_partial=True)
                    validate_cached(record,day,kind)
                    if record is None or record.get('normalization_version')!=NORMALIZATION_VERSION:
                        record=_revalidate_raw(cache,day,kind,signature,closes,master,guard)
                    if record is not None:doc[spec['document_key']][day]=record
                    elif day in old.get(spec['document_key'],{}):
                        doc['failures'].append({'capability':spec['failure_capability'],'pool_kind':kind,'date':day,'reason':'签名变化且本地原始证据缺失，该日保持未知；需手动回补'})
                except (ValueError,ProviderError) as error:
                    doc['failures'].append({'capability':spec['failure_capability'],'pool_kind':kind,'date':day,'reason':'签名变化或历史证据无法核验，该日未继承：'+str(error)})
                done+=1
                tick('核验本地年内涨跌停证据',day+' · '+spec['label']+' · '+str(done)+'/'+str(local_batches))
        for day in old.get('public_pools',{}):
            if day not in calendar or day>target:continue
            if guard:guard()
            try:
                record=cache.reuse('eastmoney','limitup',day,signature_for(day,'eastmoney-public'),allow_partial=True)
                validate_cached(record,day)
                if record is not None:doc['public_pools'][day]=record
                else:doc['failures'].append({'capability':'seal_details','date':day,'reason':'基础行情或证券身份签名变化，该日封板明细未继承；需手动补充'})
            except ValueError as error:doc['failures'].append({'capability':'seal_details','date':day,'reason':str(error)})
        if dates:
            from app.services.source_service import client
            adapter=client('fuyao')
            for day in dates:
                closes=prices(day)
                for pool_kind in ('limitup','limitdown'):
                    spec=pool_spec(pool_kind)
                    tick('回补年内'+spec['label']+'池',day+' · 校验分页、证券身份和收盘价')
                    signature=_pool_signature(day,'fuyao',closes,identity_hash,pool_kind);cached=None
                    try:
                        record=cache.reuse('fuyao',pool_kind,day,signature,allow_partial=True)
                        validate_cached(record,day,pool_kind)
                        if record and record.get('normalization_version')!=NORMALIZATION_VERSION:
                            cached=record
                            record=_revalidate_raw(cache,day,pool_kind,signature,closes,master,guard)
                        elif record is None or not record.get('complete'):
                            record,raw=fetch_fuyao_pool(adapter,day,closes,master,pool_kind=pool_kind)
                            record=cache.accept('fuyao',pool_kind,day,signature,record,raw)
                        doc[spec['document_key']][day]=record
                        doc['failures']=[failure for failure in doc['failures'] if not (failure.get('capability')==spec['failure_capability'] and failure.get('date')==day)]
                    except (ValueError,ProviderError) as error:
                        failure={'capability':spec['failure_capability'],'pool_kind':pool_kind,'date':day,'reason':str(error)}
                        captured=getattr(error,'raw_pages',None)
                        if captured:
                            evidence={'date':day,'pool_kind':pool_kind,'items':[],'complete':False,'state':'unknown','reason':str(error)}
                            if cached:evidence.update(fetched_at=cached.get('fetched_at'),validated_at=datetime.now(CHINA).isoformat())
                            rejected=cache.accept('fuyao',pool_kind+'-failed',day,signature,evidence,captured)
                            failure.update(raw_artifact=rejected['raw_artifact'],raw_sha256=rejected['raw_sha256'],fetched_at=rejected['fetched_at'])
                        doc['failures'].append(failure)
                    done+=1
        public=PublicMarketClient()
        for day in recent:
            tick('同步封板明细',day+' · 公开数据补充与逐价验证')
            closes=prices(day); signature=digest({'quotes':closes,'master':identity_hash,'day':day,'source':'eastmoney-public'})
            try:
                record=cache.reuse('eastmoney','limitup',day,signature)
                if record is None:
                    raw=public.limit_pool(day); record=normalize_limit_pool(raw,day,closes,master)
                    record=cache.accept('eastmoney','limitup',day,signature,record,raw)
                doc['public_pools'][day]=record
                doc['failures']=[failure for failure in doc['failures'] if not (failure.get('capability')=='seal_details' and failure.get('date')==day)]
            except (ValueError,ProviderError) as error:
                doc['failures'].append({'capability':'seal_details','date':day,'reason':str(error)})
            done+=1
        tick('核验行业日级资金',target+' · 按实际日期读取行业历史序列')
        fund_total=0
        def fund_progress(processed,total,code):
            nonlocal planned,fund_total
            fund_total=total;planned=local_batches+pool_batches+len(recent)+total+1
            if guard:guard()
            if progress:progress('核验行业日级资金',75+int(processed/max(1,total)*23),code+' · '+str(processed)+'/'+str(total),{'actual_completed_batches':done+processed,'actual_total_batches':planned,'fund_processed':processed,'fund_catalog_count':total})
        try:
            record=fetch_history_for_market(public,market,target,cache,progress=fund_progress,guard=guard,request_budget_version=fund_request_budget_version)
            if fund_request_budget_version is not None and progress:
                statistics=record.get('coverage',{})
                progress('行业资金请求阶段结束',98,'已留存实际请求计数；缺口保持未知',
                    {'fund_request_budget_version':fund_request_budget_version,
                     'fund_logical_requests':statistics.get('logical_request_count'),
                     'fund_actual_transport_attempts':statistics.get('actual_transport_attempt_count'),
                     'fund_request_elapsed_seconds':statistics.get('elapsed_seconds'),
                     'fund_source_circuit_open':statistics.get('source_circuit_open'),
                     'fund_stop_reason':statistics.get('stop_reason'),
                     'fund_whole_phase_hard_deadline_guaranteed':False})
            raw=record.pop('raw_pages')
            record=cache.accept('eastmoney','fund-flow',target,digest({'day':target,'scope':'industry-history'}),record,raw)
            if record.get('items'):
                doc['fund_flows'][target]=record
            for failure in record.get('failures',[]):doc['failures'].append({'capability':'fund_flow','date':target,**failure})
            if not record.get('items'):doc['failures'].append({'capability':'fund_flow','date':target,'reason':'没有取得目标日可核验行业资金流，保留未知'})
            fund_total=record.get('coverage',{}).get('selected_count',fund_total)
        except (ValueError,ProviderError) as error:
            doc['failures'].append({'capability':'fund_flow','date':target,'reason':str(error)})
        planned=local_batches+pool_batches+len(recent)+fund_total+1;done=planned
    finally:con.close()
    if sector_history:
        from app.services.sector_history import collect_sector_history
        from app.services.source_service import client
        base_batches=done
        def sector_progress(completed,total,code):
            if guard:guard()
            if original_progress:
                original_progress('补充行业与概念日行情',55+int(completed/max(1,total)*43),
                                  target+' · '+str(code)+' · '+str(completed)+'/'+str(total),
                                  {'actual_completed_batches':base_batches+completed,
                                   'actual_total_batches':base_batches+total,
                                   'sector_processed':completed,'sector_catalog_count':total})
        try:
            record=collect_sector_history(market,target,cache,client('fuyao'),progress=sector_progress,guard=guard)
            if guard:guard()
            if record.get('items'):
                # Keep the head small: each dated board-price table is an
                # immutable object, loaded only for the requested market day.
                lineage={'kind':'sector-index-raw-manifest','date':target,
                         'entries':[{key:row.get(key) for key in ('topic_code','raw_artifact','raw_sha256','request_signature')}
                                    for row in record['items']]}
                signature=digest({'day':target,'scope':'sector-index-history','normalization_version':record.get('normalization_version')})
                accepted=cache.accept('fuyao','sector-day',target,signature,record,lineage)
                pointer=_load(root/'accepted/fuyao/sector-day'/(target+'.json'))
                doc['sector_index_history'][target]={
                    'date':target,'source':'fuyao','normalization_version':accepted['normalization_version'],
                    'artifact':pointer['artifact'],'sha256':pointer['sha256'],
                    'complete':accepted.get('complete') is True,'coverage':accepted.get('coverage',{})}
            for failure in record.get('failures',[]):
                doc['failures'].append({'capability':'sector_index_history','date':target,**failure})
            if not record.get('items'):
                doc['failures'].append({'capability':'sector_index_history','date':target,'reason':'未取得该日可核验板块指数行情，保留原已发布数据及缺口'})
            planned=base_batches+record.get('coverage',{}).get('selected_count',0);done=planned
        except (ValueError,ProviderError) as error:
            doc['failures'].append({'capability':'sector_index_history','date':target,'reason':str(error)})
        progress=original_progress
    return _publish_document(doc,calendar,target,cache,guard,tick,sector_history)


def execute_supplement(job_id, progress=None):
    with connect() as connection:
        row=connection.execute('SELECT parameters_json,target_date FROM job WHERE job_id=?',(job_id,)).fetchone()
    if not row:raise ValueError('补充任务不存在')
    params=json.loads(row['parameters_json'])
    validate_fund_budget_version(params.get('fund_request_budget_version'))
    market=get_market();status=market.status();target=row['target_date'] or status.get('data_end')
    if params.get('market_snapshot_id') is not None and params['market_snapshot_id']!=status.get('snapshot_id'):
        raise ValueError('盘面补拉计划所用行情代际已变化，请核验新数据后重新提交')
    expected_generation=status.get('snapshot_id')
    def guard():
        with connect() as connection:state=connection.execute('SELECT status FROM job WHERE job_id=?',(job_id,)).fetchone()
        if not state or state[0]=='cancelled':raise TaskCancelled()
        if state[0]=='paused':raise TaskPaused()
        if get_market().status().get('snapshot_id')!=expected_generation:raise ValueError('基础行情代际已变化，补充任务保留缓存并需手动重试')
    guard()
    if params.get('revalidate_only') is True:
        return revalidate_for_market(market,target,progress=progress,guard=guard,job_id=job_id)
    return refresh_for_market(market,target,year_history=bool(params.get('year_history',True)),recent_days=params.get('recent_days',20),progress=progress,guard=guard,job_id=job_id,sector_history=params.get('sector_history') is True,fund_request_budget_version=params.get('fund_request_budget_version'))
