"""Read-only API queries over versioned Parquet; importing is worker-only."""
from __future__ import annotations
from collections import defaultdict, OrderedDict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import shutil
import tempfile
import threading
import duckdb

from .indicators import INDICATOR_VERSION, aggregate_weekly, enrich_bars
from .market_storage import VERSION as STORAGE_VERSION, OBJECT_ROOT, parquet_input, write_partitions, activity_window, write_reference, load_reference, REFERENCE_VERSION, ENCODER_VERSION, verify_immutable_file, safe_object

INDEX_NAMES = {'000001.SH':'上证指数','399001.SZ':'深证成指','399006.SZ':'创业板指','000688.SH':'科创50'}
MEMBERS = {'AI':('885728.TI','人工智能'), 'bank':('881155.TI','银行'), 'semiconductor':('881121.TI','半导体及元件')}
ALGORITHM = INDICATOR_VERSION


def hash_file(path):
    h = sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def fsync_file(path):
    with Path(path).open('rb') as stream:os.fsync(stream.fileno())


def fsync_directory(path):
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def validated_snapshot_head(data_dir,final,raw_sha,source_hashes):
    """Idempotent reuse still verifies every previously published market artifact."""
    manifest=load_json(final/'manifest.json',{})
    head=manifest.get('summary',{})
    if head.get('snapshot_id')!=final.name or manifest.get('source_sha256')!=raw_sha or manifest.get('supplemental_hashes')!=source_hashes:raise ValueError('已有行情代际manifest与本次输入不符，拒绝复用')
    files=head.get('files',[])
    if not isinstance(files,list) or not files:raise ValueError('已有行情代际缺少文件校验清单，拒绝复用')
    expected=set()
    for item in files:
        target=(data_dir/item['path']).resolve()
        if not (target.is_relative_to(final.resolve()) or target.is_relative_to((data_dir/'published'/OBJECT_ROOT).resolve())) or target in expected or not target.is_file() or target.stat().st_size!=item.get('bytes') or hash_file(target)!=item.get('sha256'):raise ValueError('已有行情代际文件缺失、损坏或校验和不符，拒绝复用')
        expected.add(target)
    actual={p.resolve() for p in final.iterdir() if p.is_file() and p.name!='manifest.json'}
    if actual!={target for target in expected if target.is_relative_to(final.resolve())}:raise ValueError('已有行情代际文件与manifest清单不同，拒绝复用')
    return head


def load_json(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).is_file() else default


def date_ms(value):
    return datetime.fromtimestamp(value / 1000, timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).date().isoformat()


def canonical(value):
    value = str(value).upper()
    if re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', value): return value
    if re.fullmatch(r'\d{6}', value): return value + ('.SH' if value.startswith(('5','6','9')) else '.BJ' if value.startswith(('4','8')) else '.SZ')
    raise ValueError('证券代码需要六位数字及.SH/.SZ/.BJ后缀')


def pool_coverage(payload):
    data=payload.get('data',{});rows=data.get('item',[]);p=data.get('pagination',{})
    total=p.get('total');finished=p.get('complete') is True or p.get('has_next') is False or (isinstance(p.get('pages'),int) and isinstance(p.get('page'),int) and p['page']>=p['pages'])
    identified=sum(isinstance(r,dict) and isinstance(r.get('thscode'),str) and bool(re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',r['thscode'].upper())) for r in rows)
    complete=isinstance(total,int) and not isinstance(total,bool) and total==len(rows) and finished and identified==len(rows) and not data.get('quarantine')
    return {'state':'complete' if complete else 'unknown','complete':complete,'observed_rows':len(rows),'identified_rows':identified,'quarantined_rows':len(data.get('quarantine',[])),'declared_total':total}


class MarketData:
    def __init__(self, data_dir, snapshot_id=None):
        self.data_dir = Path(data_dir).resolve()
        self._lock = threading.RLock()
        self._snapshot = None
        self._meta = None
        self._frame = None
        self._symbol_ranges = {}
        self._symbol_bars = OrderedDict()
        self._panels = {}
        self._fixed_snapshot = snapshot_id
        self._breadth = None
        self._turnover = None
        self._actions = None
        self._panel_inputs = None
        self._head_signature = None
        self._head_cached = {}
        self._daily_reader = None
        self._daily_reader_spill = None
        self._reader_has_actions = False
        self._daily_reader_stamp = None
        self._daily_columns = None
        self._daily_native_dtypes = None
        self._daily_text_nulls = None
        self._frame_source_stamp = None

    def _load(self):
        # calendar()/instrument()/reference() also enter here without an outer
        # lock. Publishing the snapshot before its reference was loaded exposed
        # a transient empty calendar to concurrent first requests, which could
        # become a persisted unknown watchlist baseline and a negative quote.
        with self._lock:
            target=(self.data_dir/'published'/'market-head.json' if self._fixed_snapshot is None else self.data_dir/'published'/self._fixed_snapshot/'manifest.json' if self._fixed_snapshot else None)
            try:
                stat=target.stat() if target is not None else None
            except FileNotFoundError:stat=None
            signature=(str(target),stat.st_ino,stat.st_mtime_ns,stat.st_ctime_ns,stat.st_size) if stat else None
            if signature is None:head={}
            elif signature==self._head_signature:head=self._head_cached
            else:
                document=load_json(target,{})
                head=document if self._fixed_snapshot is None else document.get('summary',{})
            snapshot = head.get('snapshot_id')
            if snapshot != self._snapshot:
                self._close_daily_reader()
                meta=load_reference(self.data_dir/'published'/snapshot/'reference.json',document=load_json(self.data_dir/'published'/snapshot/'reference.json',{})) if snapshot else {}
                # Materialize reference first, then publish the coherent head,
                # metadata and empty derived caches inside one reentrant lock.
                self._snapshot=snapshot;self._meta=meta
                self._frame = None; self._frame_source_stamp=None;self._daily_columns=None;self._daily_native_dtypes=None;self._daily_text_nulls=None;self._symbol_ranges={}; self._symbol_bars.clear(); self._panels.clear();self._breadth=None;self._turnover=None;self._actions=None;self._panel_inputs=None
            self._head_signature=signature;self._head_cached=head
            return head

    def status(self):
        with self._lock:
            head = self._load()
            if not head: return {'ready':False,'rows':0,'symbols':0,'data_begin':None,'data_end':None,'snapshot_id':None,'quality':'empty'}
            return {**head, 'ready':True, 'path':str(self.data_dir/'published'/head['snapshot_id']), 'manifest':load_json(self.data_dir/'published'/head['snapshot_id']/'manifest.json')}

    def import_audit(self, audit_dir, progress=None):
        source = Path(audit_dir).resolve()
        bundle=load_json(source/'bundle.json',{})
        if bundle.get('source','fuyao')!='fuyao' or bundle.get('price_basis','none')!='none':raise ValueError('此导入器仅接已验收Fuyao未复权股/CNY口径，不混接其他来源')
        daily=(source/bundle.get('daily_file','fuyao_raw_daily_2025-10-20_2026-10-19.parquet')).resolve()
        if not daily.is_relative_to(source):raise ValueError('数据包文件路径越界')
        if not daily.is_file(): raise ValueError('验收目录缺少年度未复权Parquet，不能用合成数据代替')
        def report(phase, percent):
            if progress: progress(phase, percent)
        report('核验年度文件', 5)
        raw_sha = hash_file(daily)
        supplemental = [source/'raw'/n for n in ['fuyao_stock_master.json','fuyao_calendar_year.json','fuyao_index_year_000001_SH.json','fuyao_index_ma250_prehistory.json','fuyao_index_year_399001_SZ.json','fuyao_index_year_399006_SZ.json','fuyao_index_year_000688_SH.json','fuyao_concept_catalog.json','fuyao_industry_catalog.json','fuyao_members_AI.json','fuyao_members_bank.json','fuyao_members_semiconductor.json','fuyao_limitup_latest.json','fuyao_limitdown_latest.json','fuyao_breakpool_latest.json','fuyao_ladder_30d.json','fuyao_abnormal_current.json','fuyao_sectors_snapshot_sample.json','fuyao_bulk_adjustments.parquet']]
        for topic in bundle.get('topic_files',[]):
            path=(source/'raw'/topic['file']).resolve()
            if not path.is_relative_to(source/'raw'):raise ValueError('题材文件路径越界')
            supplemental.append(path)
        history_file=bundle.get('pool_history_file','fuyao_pool_history.json')
        history_path=(source/'raw'/history_file).resolve()
        if not history_path.is_relative_to(source/'raw'):raise ValueError('历史事件文件路径越界')
        supplemental.append(history_path)
        hashes = {p.name:hash_file(p) for p in supplemental if p.is_file()}
        identity={'daily':raw_sha, 'files':hashes, 'algorithm':ALGORITHM}
        if bundle:identity['bundle']=bundle
        partitioned=bundle.get('storage_layout')==STORAGE_VERSION
        if partitioned:identity.update(reference_storage_version=REFERENCE_VERSION,storage_encoder=ENCODER_VERSION)
        snapshot = 'market-' + sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:24]
        root = self.data_dir/'published'; root.mkdir(parents=True,exist_ok=True)
        final = root/snapshot
        if final.is_dir():
            head = validated_snapshot_head(self.data_dir,final,raw_sha,hashes)
            for file in sorted(final.iterdir()):
                if file.is_file():fsync_file(file)
            fsync_directory(final)
            self._publish_head(root, head)
            report('已有同版本快照，幂等完成',100)
            return self.status()
        stage = root/('.staging-'+snapshot)
        if stage.exists(): shutil.rmtree(stage)
        stage.mkdir()
        if not partitioned:shutil.copyfile(daily,stage/'raw-daily.parquet')
        partition_files=[];storage_stats={}
        report('规范化交易日和数据单位', 20)
        # The sole worker owns imports. Keep ten-year normalization bounded and
        # spill to its writable work volume, rather than a small container /tmp.
        work = self.data_dir/'work'; work.mkdir(parents=True,exist_ok=True)
        spill = tempfile.TemporaryDirectory(prefix='import-spill-',dir=work)
        con = duckdb.connect(':memory:')
        try:
            con.execute("SET TimeZone='Asia/Shanghai'")
            con.execute("SET memory_limit='1GB'")
            con.execute('SET threads=4')
            con.execute('SET temp_directory=?',[spill.name])
            invalid_basis=con.execute("SELECT count(*) FROM read_parquet(?) WHERE adjusted IS DISTINCT FROM 'none' OR interval IS DISTINCT FROM '1d' OR currency IS DISTINCT FROM 'CNY'",[str(daily)]).fetchone()[0]
            if invalid_basis:raise ValueError('行情混有未验收的复权、周期或货币口径，拒绝发布')
            con.execute("CREATE TABLE normalized AS SELECT upper(thscode) instrument_id, strftime(to_timestamp(date_ms/1000.0) AT TIME ZONE 'Asia/Shanghai','%Y-%m-%d') date, open_price AS open,high_price AS high,low_price AS low,close_price AS close,volume,turnover AS amount,'fuyao' AS source,'none' AS price_basis FROM read_parquet(?) ORDER BY thscode,date_ms",[str(daily)])
            if not partitioned:con.execute("COPY normalized TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",[str(stage/'daily.parquet')])
            count, symbols, begin, end, duplicate = con.execute("SELECT count(*),count(distinct instrument_id),min(date),max(date),count(*)-count(distinct (instrument_id,date)) FROM normalized").fetchone()
            if duplicate: raise ValueError('年度行情有重复证券日期键，拒绝发布')
            bad=con.execute('SELECT count(*) FROM normalized WHERE instrument_id IS NULL OR date IS NULL OR NOT regexp_full_match(instrument_id,\'[0-9]{6}\\.(SH|SZ|BJ)\') OR open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR volume IS NULL OR amount IS NULL OR NOT isfinite(open) OR NOT isfinite(high) OR NOT isfinite(low) OR NOT isfinite(close) OR NOT isfinite(volume) OR NOT isfinite(amount) OR least(open,high,low,close)<=0 OR volume<0 OR amount<0 OR high<greatest(open,close,low) OR low>least(open,close,high)').fetchone()[0]
            if not count or bad:raise ValueError('行情为空或含无效OHLC/非有限数值，拒绝发布')
            if partitioned:
                report('复用历史月份，仅发布变化分片',40)
                con.read_parquet(str(daily)).create_view('raw_partition_source')
                for relation,role in [('raw_partition_source','raw-daily.parquet'),('normalized','daily.parquet')]:
                    files,stats=write_partitions(con,relation,role,root,stage,snapshot)
                    partition_files.extend(files);storage_stats[role]=stats
        finally:
            con.close()
            spill.cleanup()
        def response(name): return load_json(source/'raw'/f'{name}.json',{}).get('data',{})
        def items(name): return response(name).get('item',[])
        master = {r['thscode'].upper():{**r,'instrument_id':r['thscode'].upper(),'code':r.get('ticker',r['thscode'][:6]),'kind':'stock'} for r in items('fuyao_stock_master')}
        calendar = sorted(set(date_ms(r['date_ms']) for r in items('fuyao_calendar_year') if begin<=date_ms(r['date_ms']) <= end))
        if not calendar or calendar[0]!=begin or calendar[-1]!=end:raise ValueError('交易日历与行情实际首尾范围不一致，拒绝发布')
        indices = {}
        for symbol,name in INDEX_NAMES.items():
            rows = items('fuyao_index_year_'+symbol.replace('.','_'))
            if symbol == '000001.SH': rows = items('fuyao_index_ma250_prehistory') + rows
            unique = {date_ms(r['date_ms']):{'date':date_ms(r['date_ms']),'open':r['open_price'],'high':r['high_price'],'low':r['low_price'],'close':r['close_price'],'volume':r['volume'],'amount':r['turnover'],'source':'fuyao','price_basis':'none','corporate_action':False} for r in rows if date_ms(r['date_ms'])<=end}
            indices[symbol] = [unique[d] for d in sorted(unique)]
            master[symbol] = {'instrument_id':symbol,'thscode':symbol,'code':symbol[:6],'name':name,'kind':'index','exchange':symbol[-2:]}
        topics = []
        collected=bundle.get('collected_at','2026-10-20')
        topic_specs=bundle.get('topic_files',[{'file':'fuyao_members_'+suffix+'.json','topic_code':code,'name':name} for suffix,(code,name) in MEMBERS.items()])
        for spec in topic_specs:
            path=source/'raw'/spec['file']
            complete=spec.get('complete',True)
            if not path.is_file() and complete:raise ValueError('题材成员文件缺失，不能将未采集冒充完整空列表')
            members=load_json(path,{}).get('data',{}).get('item',[]);code=spec.get('topic_code') or spec['code']
            topics.append({'topic_id':'fuyao-'+code,'topic_code':code,'code':code,'name':spec['name'],'kind':spec.get('kind','industry' if code.startswith(('881','884')) else 'concept'),'source':'fuyao','observed_date':spec.get('observed_date',str(collected)[:10]),'historical_membership':False,'instrument_ids':[r['thscode'].upper() for r in members],'members_complete':complete,'members_state':'complete' if complete else 'unknown'})
        pool_history=load_json(history_path,bundle.get('pool_history',{}))
        if not isinstance(pool_history,dict):raise ValueError('历史事件需要按交易日分组')
        metadata = {'master':master,'calendar':calendar,'indices':indices,'topics':topics,'concept_catalog':items('fuyao_concept_catalog'),'industry_catalog':items('fuyao_industry_catalog'),'limitup':items('fuyao_limitup_latest'),'limitdown':items('fuyao_limitdown_latest'),'breakpool':items('fuyao_breakpool_latest'),'ladder':response('fuyao_ladder_30d'),'abnormal':items('fuyao_abnormal_current'),'sectors_snapshot':items('fuyao_sectors_snapshot_sample'),'pool_trade_date':bundle.get('pool_trade_date',end),'pool_fetched_at':collected,'pool_history':pool_history,'topic_coverage':bundle.get('topic_coverage','仅已下载成员组；不是全题材历史库'),'coverage':bundle.get('coverage',{})}
        metadata.update(abnormal_observed_date=bundle.get('abnormal_observed_date',str(collected)[:10]),abnormal_fetched_at=bundle.get('abnormal_fetched_at',collected))
        metadata.update(sectors_snapshot_observed_at=bundle.get('sectors_snapshot_observed_at',collected),sectors_snapshot_date_semantics=bundle.get('sectors_snapshot_date_semantics','current_unverified_trade_date'))
        metadata.update(sectors_snapshot_trade_date=bundle.get('sectors_snapshot_trade_date'),sectors_snapshot_coverage=bundle.get('sectors_snapshot_coverage',bundle.get('coverage',{})))
        metadata['pool_coverage']={key:pool_coverage(load_json(source/'raw'/file,{})) for key,file in [('limitup','fuyao_limitup_latest.json'),('limitdown','fuyao_limitdown_latest.json'),('breakpool','fuyao_breakpool_latest.json')]}
        metadata['pool_quarantine']={key:load_json(source/'raw'/file,{}).get('data',{}).get('quarantine',[]) for key,file in [('limitup','fuyao_limitup_latest.json'),('limitdown','fuyao_limitdown_latest.json'),('breakpool','fuyao_breakpool_latest.json')]}
        if partitioned:
            reference_files,reference_stats=write_reference(metadata,root,stage,snapshot)
            partition_files.extend(reference_files);storage_stats['reference.json']=reference_stats
        else:(stage/'reference.json').write_text(json.dumps(metadata,ensure_ascii=False,separators=(',',':')))
        if (source/'raw/fuyao_bulk_adjustments.parquet').is_file(): shutil.copyfile(source/'raw/fuyao_bulk_adjustments.parquet',stage/'corporate-actions.parquet')
        report('校验不可变快照', 80)
        artifacts = partition_files+[{'role':p.name,'path':str(Path('published')/snapshot/p.name),'sha256':hash_file(p),'bytes':p.stat().st_size} for p in sorted(stage.iterdir()) if p.is_file() and p.name not in ('raw-daily.manifest.json','daily.manifest.json')]
        warnings=bundle.get('quality_warnings',['2145个预期交易记录缺口未解释，未填0；退市历史不完整','未复权原始价格；公司行动附近价格指标重启预热，未声称复权已验收','换手率和完整历史题材不可用；最新名称不等于历史证券身份'])
        head = {'snapshot_id':snapshot,'data_begin':begin,'data_end':end,'rows':count,'symbols':symbols,'source':'fuyao','price_basis':'none','volume_unit':'股','amount_unit':'CNY','algorithm_version':ALGORITHM,'quality':'partial','files':artifacts,'collected_at':collected,'warnings':warnings,'unexplained_missing':bundle.get('unexplained_missing',2145 if not bundle else None),'coverage':bundle.get('coverage',{}),'manifest_path':str(Path('published')/snapshot/'manifest.json')}
        if partitioned:head.update(storage_layout=STORAGE_VERSION,storage_encoder=ENCODER_VERSION,storage_write=storage_stats,active_window=activity_window(end,begin))
        manifest = {'summary':head,'source_sha256':raw_sha,'supplemental_hashes':hashes,'quality':head['warnings'],'published_at':datetime.now(timezone.utc).isoformat()}
        (stage/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
        for file in sorted(stage.iterdir()):
            if file.is_file():fsync_file(file)
        fsync_directory(stage)
        os.replace(stage,final)
        fsync_directory(root)
        self._publish_head(root,head)
        report('真实数据快照已发布',100)
        return self.status()

    @staticmethod
    def _publish_head(root, head):
        temp = root/'market-head.json.tmp'; temp.write_text(json.dumps(head,ensure_ascii=False))
        fsync_file(temp)
        os.replace(temp,root/'market-head.json')
        fsync_directory(root)

    def _reference(self):
        self._load(); return self._meta or {}

    def calendar(self): return list(self._reference().get('calendar',[]))

    def parquet_input(self, role='daily.parquet', *, start=None, end=None):
        head=self._load()
        if not head:raise ValueError('尚无已发布行情')
        return parquet_input(self.data_dir/'published'/head['snapshot_id']/role,start=start,end=end)

    def instruments(self, query='', limit=50):
        query = query.strip().casefold()
        rows = self._reference().get('master',{}).values()
        return [dict(r) for r in rows if not query or query in (r.get('instrument_id','')+' '+r.get('name','')).casefold()][:max(1,min(int(limit),10000))]

    def instrument(self, instrument_id):
        symbol = canonical(instrument_id)
        return dict(self._reference().get('master',{}).get(symbol,{'instrument_id':symbol,'code':symbol[:6],'name':symbol,'kind':'stock'}))

    def topics_data(self): return [dict(r) for r in self._reference().get('topics',[])]
    def topics(self): return self.topics_data()
    def topic_members(self, topic_id): return next((list(t['instrument_ids']) for t in self.topics_data() if t['topic_id']==topic_id or t['topic_code']==topic_id),[])

    def actions(self, instrument_id, start=None, end=None):
        head = self._load()
        if not head: return []
        path = self.data_dir/'published'/head['snapshot_id']/'corporate-actions.parquet'
        if not path.exists(): return []
        with self._lock:
            if self._actions is None:
                con=duckdb.connect(':memory:')
                try:rows=con.execute('SELECT * FROM read_parquet(?) ORDER BY ex_date_ms',[str(path)]).fetchdf().to_dict('records')
                finally:con.close()
                self._actions=defaultdict(list)
                for r in rows:
                    try:day=date_ms(r['ex_date_ms'])
                    except (TypeError,ValueError,OverflowError,OSError):day=None
                    self._actions[str(r['thscode']).upper()].append({**r,'date':day})
            return [dict(r) for r in self._actions.get(canonical(instrument_id),[]) if (not start or r['date'] is None or r['date']>=start) and (not end or r['date'] is None or r['date']<=end)]

    def corporate_actions_between(self,instrument_id,start,end): return self.actions(instrument_id,start,end)

    def prices(self,instrument_id,as_of=None,price_basis='none'):
        if price_basis not in ('none','forward'):raise ValueError('价格口径仅支持none/forward')
        rows=self.bars(instrument_id,as_of)
        anchor=as_of or self.status().get('data_end')
        if price_basis=='none':return rows,{'usable':True,'price_basis':'none','anchor':anchor,'events_count':0,'quarantined':[]}
        if self.instrument(instrument_id).get('kind')=='index':return rows,{'usable':False,'price_basis':'none','anchor':anchor,'events_count':0,'quarantined':[],'reason':'指数无需个股除权复权'}
        from .adjustments import continuous_prices
        return continuous_prices(rows,self.actions(instrument_id,end=anchor),anchor,self.calendar())

    def _close_daily_reader(self):
        reader=getattr(self,'_daily_reader',None);spill=getattr(self,'_daily_reader_spill',None)
        self._daily_reader=None;self._daily_reader_spill=None;self._reader_has_actions=False;self._daily_reader_stamp=None
        try:
            if reader is not None:reader.close()
        finally:
            if spill is not None:spill.cleanup()

    def close(self):
        """Release only this object's in-memory read connection and private spill."""
        with self._lock:self._close_daily_reader()

    def __del__(self):
        try:self._close_daily_reader()
        except Exception:pass

    def _daily_source(self,head):
        path=self.data_dir/'published'/head['snapshot_id']/'daily.parquet'
        try:
            argument=parquet_input(path)
            if isinstance(argument,str):
                evidence=next((item for item in head.get('files',[]) if item.get('role')=='daily.parquet'),None)
                if evidence is not None:verify_immutable_file(path,evidence['sha256'],evidence['bytes'])
            paths=[argument] if isinstance(argument,str) else argument
            stamp=tuple((str(item),stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns) for item in paths for stat in [Path(item).stat()])
            return argument,stamp
        except BaseException:
            self._symbol_bars.clear();self._frame=None;self._frame_source_stamp=None;self._symbol_ranges={};self._daily_columns=None;self._daily_native_dtypes=None;self._daily_text_nulls=None;self._close_daily_reader();raise

    def verify_condition_sources(self):
        """Validate the fixed condition-input closure before a derived cache hit.

        No history rows or database files are opened. Signature-aware immutable
        SHA checks cover normalized prices, action evidence, and the reference
        descriptor/parts carrying the calendar and dated source-pool metadata.
        Legacy adapters without a complete manifest get no reusable token.
        """
        with self._lock:
            head=self._load()
            if not head:return None
            files=head.get('files')
            if not isinstance(files,list):return None
            selected=[row for row in files if isinstance(row,dict) and isinstance(row.get('role'),str)
                      and (row['role'] in ('daily.parquet','corporate-actions.parquet','reference.json')
                           or row['role'].startswith(('daily-partition/','reference-part/')))]
            roles=[row['role'] for row in selected]
            if not {'daily.parquet','corporate-actions.parquet','reference.json'}<=set(roles):return None
            if len(roles)!=len(set(roles)):raise ValueError('条件来源清单角色重复，不能复用派生指标')
            signatures=[]
            for row in selected:
                expected=row.get('sha256');size=row.get('bytes');relative=row.get('path')
                if (not isinstance(expected,str) or not re.fullmatch(r'[a-f0-9]{64}',expected)
                        or isinstance(size,bool) or not isinstance(size,int) or size<0 or not isinstance(relative,str)):
                    raise ValueError('条件来源清单缺少可信SHA或大小')
                path=safe_object(self.data_dir,relative)
                if not (path.is_relative_to(self.data_dir/'published'/head['snapshot_id'])
                        or path.is_relative_to(self.data_dir/'published'/OBJECT_ROOT)):
                    raise ValueError('条件来源超出固定行情目录')
                verify_immutable_file(path,expected,size)
                info=path.stat()
                signatures.append((row['role'],relative,expected,size,info.st_dev,info.st_ino,info.st_mtime_ns,info.st_ctime_ns))
            _,price_stamp=self._daily_source(head)
            return ('verified-condition-sources-v1',str(self.data_dir),head['snapshot_id'],price_stamp,tuple(signatures))

    def _prepare_daily_reader(self,head,argument,stamp):
        """Called under the existing RLock; never opens a worker database file."""
        import pyarrow as pa
        import pandas as pd
        if self._daily_reader is not None:return self._daily_reader
        meta=self._reference()
        if self._snapshot!=head.get('snapshot_id'):raise ValueError('行情代际已切换，请重试固定输入')
        spill=tempfile.TemporaryDirectory(prefix='shixu-query-');con=duckdb.connect(':memory:')
        try:
            con.execute("SET TimeZone='Asia/Shanghai'")
            con.execute("SET memory_limit='1GB'")
            con.execute('SET temp_directory=?',[spill.name]);con.execute('SET threads=4')
            # Only metadata persists between stock reads. External price pages
            # must not become an unbounded second copy of the history in RAM.
            con.execute('SET parquet_metadata_cache=true')
            con.execute('SET enable_external_file_cache=false')
            known={}
            for day,record in meta.get('pool_history',{}).items():
                if 'limitup' in record:
                    known[day]=({row['thscode'] for row in record['limitup'] if row.get('thscode')},
                                bool(record.get('coverage',{}).get('limitup',{}).get('complete',False)
                                     and all(row.get('thscode') for row in record['limitup'])))
            pool_day=meta.get('pool_trade_date')
            if pool_day:
                known[pool_day]=({row['thscode'] for row in meta.get('limitup',[]) if row.get('thscode')},
                                 bool(meta.get('pool_coverage',{}).get('limitup',{}).get('complete',False)))
            con.register('known_days',pa.table({'date':pa.array(list(known),type=pa.string()),
                                              'complete':pa.array([value[1] for value in known.values()],type=pa.bool_())}))
            events=[(item,day) for day,(items,_) in known.items() for item in items]
            con.register('known_events',pa.table({'instrument_id':pa.array([item[0] for item in events],type=pa.string()),
                                                'date':pa.array([item[1] for item in events],type=pa.string())}))
            con.read_parquet(argument).create_view('snapshot_daily')
            # Preserve the actual raw daily schema before evidence joins. The
            # shared Arrow frame may later serve raw-history callers without
            # leaking derived corporate-action or source-pool fields.
            self._daily_columns=tuple(con.table('snapshot_daily').columns)
            text_columns=tuple(name for name,kind in zip(self._daily_columns,con.table('snapshot_daily').types) if str(kind)=='VARCHAR')
            native_text_null=con.execute("SELECT value FROM (VALUES ('probe'::VARCHAR),(NULL::VARCHAR)) t(value)").fetchdf().to_dict('records')[1]['value']
            self._daily_text_nulls=(text_columns,native_text_null)
            native=con.execute('SELECT * FROM snapshot_daily LIMIT 0').fetchdf()
            self._daily_native_dtypes={}
            for name,dtype in native.dtypes.items():
                # Native DuckDB uses nullable integers/booleans only when a
                # result contains NULL; their record values stay identical
                # for non-NULL results. Arrow string NULLs must also regain
                # the native fetchdf representation, rather than becoming None.
                if dtype.kind in ('i','u'):dtype=pd.api.types.pandas_dtype(('UInt' if dtype.kind=='u' else 'Int')+str(dtype.itemsize*8))
                elif dtype.kind=='b':dtype=pd.BooleanDtype()
                self._daily_native_dtypes[name]=dtype
            action_path=self.data_dir/'published'/head['snapshot_id']/'corporate-actions.parquet'
            self._reader_has_actions=action_path.is_file()
            if self._reader_has_actions:
                con.execute("CREATE TEMP TABLE snapshot_actions AS SELECT DISTINCT thscode instrument_id,strftime(to_timestamp(ex_date_ms/1000.0) AT TIME ZONE 'Asia/Shanghai','%Y-%m-%d') date FROM read_parquet(?)",[str(action_path)])
            else:con.execute('CREATE TEMP TABLE snapshot_actions AS SELECT NULL::VARCHAR instrument_id,NULL::VARCHAR date WHERE FALSE')
            self._daily_reader=con;self._daily_reader_spill=spill;self._daily_reader_stamp=stamp
            return con
        except BaseException:
            con.close();spill.cleanup();self._reader_has_actions=False;raise

    def _read_daily_frame(self,head,symbol=None,as_of=None):
        """Read a fixed generation under one serial, metadata-cached reader.

        Quote pages are not retained in DuckDB. The independent Arrow whole-
        market cache and the 16-item stock cache keep their original bounds.
        """
        import pandas as pd
        import pyarrow as pa
        with self._lock:
            try:
                # Recheck immutable object signatures even on a warm view. A
                # replaced/corrupt partition cannot hide behind cached metadata.
                argument,stamp=self._daily_source(head)
                if self._daily_reader is not None and self._daily_reader_stamp!=stamp:self._close_daily_reader()
                con=self._prepare_daily_reader(head,argument,stamp)
                clauses=[];params=[]
                if symbol is not None:clauses.append('instrument_id=?');params.append(symbol)
                if as_of is not None:clauses.append('date<=?');params.append(as_of)
                where=' WHERE '+' AND '.join(clauses) if clauses else ''
                query=("WITH daily AS (SELECT * FROM snapshot_daily"+where+") "
                       "SELECT daily.*,actions.instrument_id IS NOT NULL corporate_action,"
                       +('TRUE' if self._reader_has_actions else 'FALSE')+" corporate_action_coverage_known,"
                       "CASE WHEN events.instrument_id IS NOT NULL THEN TRUE WHEN days.complete THEN FALSE ELSE NULL END limit_up "
                       "FROM daily LEFT JOIN snapshot_actions actions USING(instrument_id,date) "
                       "LEFT JOIN known_days days USING(date) LEFT JOIN known_events events USING(instrument_id,date) "
                       "ORDER BY daily.instrument_id,daily.date")
                mapper=lambda dtype:pd.ArrowDtype(dtype) if pa.types.is_string(dtype) or pa.types.is_boolean(dtype) else None
                return con.execute(query,params).to_arrow_table().to_pandas(types_mapper=mapper)
            except BaseException:
                self._close_daily_reader();raise

    def _daily_frame(self):
        with self._lock:
            head = self._load()
            if not head: return None
            if self._frame is None:
                frame = self._read_daily_frame(head)
                starts = frame.index[frame.instrument_id.ne(frame.instrument_id.shift()).fillna(True)].tolist()
                self._symbol_ranges = {str(frame.instrument_id.iat[left]): (left, right)
                                       for left, right in zip(starts, starts[1:] + [len(frame)])}
                self._frame = frame
                self._frame_source_stamp=self._daily_reader_stamp
                self._symbol_bars.clear();self._close_daily_reader()
            return self._frame

    def bars(self, instrument_id, as_of=None):
        symbol = canonical(instrument_id)
        with self._lock:
            head = self._load()
            meta = self._reference()
            if symbol in meta.get('indices', {}): rows = meta['indices'][symbol]
            elif not head: return []
            elif self._frame is None:
                key=(symbol,as_of)
                if key in self._symbol_bars:
                    _,stamp=self._daily_source(head)
                    if self._daily_reader_stamp is not None and self._daily_reader_stamp!=stamp:
                        self._symbol_bars.clear();self._close_daily_reader()
                if key not in self._symbol_bars:
                    self._symbol_bars[key]=self._read_daily_frame(head,symbol,as_of).to_dict('records')
                    while len(self._symbol_bars)>16:self._symbol_bars.popitem(last=False)
                self._symbol_bars.move_to_end(key);rows=self._symbol_bars[key]
            else:
                bounds = self._symbol_ranges.get(symbol)
                rows = self._frame.iloc[bounds[0]:bounds[1]].to_dict('records') if bounds else []
            return [dict(r) for r in rows if not as_of or r['date'] <= as_of]

    def history_panel(self, as_of=None, lookback=None):
        frame = self._daily_frame()
        if frame is None:return {}
        if as_of:frame=frame.loc[frame.date<=as_of]
        if lookback:frame=frame.groupby('instrument_id',sort=False).tail(int(lookback))
        histories={}
        for row in frame.to_dict('records'):histories.setdefault(row['instrument_id'],[]).append(row)
        return histories

    def history_for_instruments(self, instrument_ids, start=None, end=None):
        """Read only requested securities/dates, without materializing the full panel."""
        symbols=list(dict.fromkeys(canonical(symbol) for symbol in instrument_ids))
        if not symbols:return {}
        with self._lock:
            head=self._load()
            if not head:return {symbol:[] for symbol in symbols}
            if self._frame is not None:
                _,stamp=self._daily_source(head)
                if self._frame_source_stamp!=stamp:
                    self._frame=None;self._frame_source_stamp=None;self._symbol_ranges={};self._daily_columns=None;self._daily_native_dtypes=None;self._daily_text_nulls=None
            if self._frame is not None:
                import pandas as pd
                result={symbol:[] for symbol in symbols}
                dates=self._frame['date']
                text_columns,text_null=self._daily_text_nulls
                nullable_text=[name for name in text_columns if self._frame[name].hasnans] if text_null is not None else []
                populated_text=set()
                # Avoid thousands of tiny pandas filters/conversions on a
                # whole-market member page. Each temporary stays bounded to
                # at most 256 requested stocks, never another full panel.
                for offset in range(0,len(symbols),256):
                    bounds=[self._symbol_ranges[symbol] for symbol in symbols[offset:offset+256] if symbol in self._symbol_ranges]
                    if not bounds:continue
                    pieces=[]
                    for left,right in bounds:
                        days=dates.iloc[left:right]
                        try:
                            # The fixed frame is sorted by security/date. Clip
                            # before concat so a seven-day request never first
                            # copies 256 securities' complete retained prefix.
                            begin=left+int(days.searchsorted(start,side='left')) if start else left
                            finish=left+int(days.searchsorted(end,side='right')) if end else right
                            if begin<finish:pieces.append(self._frame.iloc[begin:finish])
                        except ValueError:
                            # Malformed legacy fixtures can contain null dates;
                            # preserve SQL's NULL exclusion under date bounds.
                            piece=self._frame.iloc[left:right]
                            if start:piece=piece.loc[piece.date>=start]
                            if end:piece=piece.loc[piece.date<=end]
                            if not piece.empty:pieces.append(piece)
                    if not pieces:continue
                    selected=pd.concat(pieces)
                    populated_text.update(name for name in nullable_text if selected[name].notna().any())
                    raw=selected.loc[:,list(self._daily_columns)].astype(self._daily_native_dtypes)
                    for row in raw.to_dict('records'):result[row['instrument_id']].append(row)
                # Native fetchdf infers string NULL representation over the
                # whole SQL result. All-NULL columns stay None; mixed text can
                # use NaN on pandas 3. Internal batching must not change that.
                if populated_text:
                    for rows in result.values():
                        for row in rows:
                            for name in populated_text:
                                if row[name] is None:row[name]=text_null
                return result
            clauses=['instrument_id IN (SELECT unnest(?))'];params=[symbols]
            if start:clauses.append('date>=?');params.append(start)
            if end:clauses.append('date<=?');params.append(end)
            con=duckdb.connect(':memory:')
            try:
                selected=con.execute('SELECT * FROM read_parquet(?) WHERE '+' AND '.join(clauses)+' ORDER BY instrument_id,date',[parquet_input(self.data_dir/'published'/head['snapshot_id']/'daily.parquet',start=start,end=end),*params]).fetchdf().to_dict('records')
            finally:con.close()
        result={symbol:[] for symbol in symbols}
        for row in selected:result[row['instrument_id']].append(row)
        return result

    def condition_histories(self, instrument_ids, start=None, end=None):
        """At most 32 securities' joined raw histories, with no market-sized dict.

        Weekly/custom slow conditions need the retained indicator seed and the
        same action/source flags as charts. The immutable compact frame stays
        shared, while only this batch becomes Python records.
        """
        symbols=list(dict.fromkeys(canonical(symbol) for symbol in instrument_ids))
        if len(symbols)>32:raise ValueError('逐股策略历史每批最多32只证券')
        with self._lock:
            frame=self._daily_frame();result={symbol:[] for symbol in symbols}
            if frame is None:return result
            for symbol in symbols:
                bounds=self._symbol_ranges.get(symbol)
                if not bounds:continue
                selected=frame.iloc[bounds[0]:bounds[1]]
                if start:selected=selected.loc[selected.date>=start]
                if end:selected=selected.loc[selected.date<=end]
                result[symbol]=selected.to_dict('records')
            return result

    def closing_prices_for_instruments(self, instrument_ids, start=None, end=None):
        """At most 256 securities' dated closes for source-event verification.

        Keep duplicate dates and missing closes intact. These are evidence
        inputs, not a last-quote lookup, and must preserve the raw reader's
        strict bounds without converting unrelated OHLC/amount fields.
        """
        symbols=list(dict.fromkeys(canonical(symbol) for symbol in instrument_ids))
        if len(symbols)>256:raise ValueError('逐股收盘证据每批最多256只证券')
        result={symbol:[] for symbol in symbols}
        if not symbols:return result
        with self._lock:
            head=self._load()
            if not head:return result
            if self._frame is not None:
                _,stamp=self._daily_source(head)
                if self._frame_source_stamp!=stamp:
                    self._frame=None;self._frame_source_stamp=None;self._symbol_ranges={};self._daily_columns=None;self._daily_native_dtypes=None;self._daily_text_nulls=None
            if self._frame is not None:
                import pandas as pd
                import numpy as np
                dates=self._frame['date'];pieces=[]
                for symbol in symbols:
                    bounds=self._symbol_ranges.get(symbol)
                    if not bounds:continue
                    left,right=bounds;days=dates.iloc[left:right]
                    try:
                        begin=left+int(days.searchsorted(start,side='left')) if start else left
                        finish=left+int(days.searchsorted(end,side='right')) if end else right
                        if begin<finish:pieces.append(np.arange(begin,finish))
                    except ValueError:
                        selected=self._frame.iloc[left:right]
                        if start:selected=selected.loc[selected.date>=start]
                        if end:selected=selected.loc[selected.date<=end]
                        if not selected.empty:pieces.append(selected.index.to_numpy())
                if not pieces:return result
                # Gather only three bounded columns once per cohort. Building
                # thousands of tiny DataFrames costs more than verifying the
                # prices, and projecting the whole five-year frame would copy
                # unrelated securities under pandas versions without CoW.
                positions=np.concatenate(pieces)
                columns=('instrument_id','date','close')
                selected=pd.DataFrame({name:self._frame[name].take(positions).array for name in columns})
                selected=selected.astype({name:self._daily_native_dtypes[name] for name in columns})
                values=selected.to_dict('list')
                for symbol,day,close in zip(values['instrument_id'],values['date'],values['close']):
                    result[symbol].append({'date':day,'close':close})
                return result
            clauses=['instrument_id IN (SELECT unnest(?))'];params=[symbols]
            if start:clauses.append('date>=?');params.append(start)
            if end:clauses.append('date<=?');params.append(end)
            con=duckdb.connect(':memory:')
            try:
                selected=con.execute('SELECT instrument_id,date,close FROM read_parquet(?) WHERE '+' AND '.join(clauses)+' ORDER BY instrument_id,date',
                    [parquet_input(self.data_dir/'published'/head['snapshot_id']/'daily.parquet',start=start,end=end),*params]).fetchdf().to_dict('records')
            finally:con.close()
        for row in selected:result[row['instrument_id']].append({'date':row['date'],'close':row['close']})
        return result

    def actions_for_instruments(self, instrument_ids):
        """Batch action evidence; None means no action file, never zero actions."""
        symbols=list(dict.fromkeys(canonical(symbol) for symbol in instrument_ids))
        if not symbols:return {}
        with self._lock:
            head=self._load()
            if not head:return None
            path=self.data_dir/'published'/head['snapshot_id']/'corporate-actions.parquet'
            if not path.is_file():return None
            con=duckdb.connect(':memory:')
            try:selected=con.execute('SELECT * FROM read_parquet(?) WHERE upper(thscode) IN (SELECT unnest(?)) ORDER BY ex_date_ms',[str(path),symbols]).fetchdf().to_dict('records')
            finally:con.close()
        result={symbol:[] for symbol in symbols}
        for row in selected:
            try:day=date_ms(row['ex_date_ms'])
            except (TypeError,ValueError,OverflowError,OSError):day=None
            result[str(row['thscode']).upper()].append({**row,'date':day})
        return result

    def history_condition_metrics(self, instrument_ids, start, end, fields):
        """Compact daily event inputs, sharing one causal vector recurrence.

        EMA/KDJ starts at the original retained seed, while only the requested
        event window is kept as arrays.  No full-market Python history or
        per-security indicator dictionaries are retained by this interface.
        The returned object is ephemeral and must never enter business JSON.
        """
        import numpy as np
        import pandas as pd
        from .panel_inputs import PanelInputs
        # The scalar monotonic-high helper remains the bounded fallback until
        # its complete dated history interface has the same compact contract.
        if 'close_breakout_high' in fields:return None
        requested=set(fields)|{'indicator_warmup_bars','price_continuity','indicator_reset_reason','indicator_warmup_reason'}
        for field in fields:
            if '_ma' in field:requested.add('ma'+field.rsplit('_ma',1)[1])
            if field=='bullish_ma_alignment':requested.update('ma'+str(n) for n in (5,10,20,30,60))
            if field in ('volume_expand','volume_shrink'):requested.add('volume_ratio')
            if field in ('macd_dead_cross','macd_cross'):requested.update(('macd_dif','macd_dea'))
            if field in ('kd_dead_cross','kd_cross'):requested.update(('kdj_k','kdj_d'))
            if field=='intraday_high_change_pct':requested.add(field+'_reason')
        symbols=list(dict.fromkeys(canonical(symbol) for symbol in instrument_ids))
        with self._lock:
            head=self._load();calendar=self.calendar();positions={day:i for i,day in enumerate(calendar)}
            prepared={'calendar':calendar,'positions':positions,'as_of':end,
                      'snapshot_id':head['snapshot_id'] if head else None,
                      'fields':set(fields),'retain_start':start,'symbol_index':{},
                      'unavailable':True}
            if not head or not symbols or end not in positions:return prepared
            # A missing action file is unknown, not a fabricated zero-action
            # history. Avoid loading ten years simply to return that unknown.
            if not (self.data_dir/'published'/head['snapshot_id']/'corporate-actions.parquet').is_file():return prepared
            left=max(0,positions.get(start,0)-1);right=positions[end]+1
            if len(symbols)*(right-left)*(len(requested)+9)*8>256*1024*1024:
                return None  # Preserve the bounded scalar fallback for a wide/complex event.
            frame=self._daily_frame()
            if self._panel_inputs is None:self._panel_inputs=PanelInputs(frame,calendar)
            base=self._panel_inputs;strict=base.history_view(frame)
            symbols=[symbol for symbol in symbols if symbol in base.symbol_index]
            if not symbols:return prepared
            take=np.asarray([base.symbol_index[symbol] for symbol in symbols],dtype=np.int64)
            days=calendar[left:right];width=len(symbols)
            quotes={key:matrix[left:right][:,take] for key,matrix in base.values.items()}
            for key in ('open','amount'):quotes[key]=np.full((len(days),width),np.nan)
            selected=frame.loc[(frame.date>=days[0])&(frame.date<=end)&frame.instrument_id.isin(symbols),['instrument_id','date','open','amount']]
            row_days=pd.Categorical(selected.date,categories=days).codes
            row_columns=pd.Categorical(selected.instrument_id,categories=symbols).codes
            for key in ('open','amount'):quotes[key][row_days,row_columns]=selected[key].to_numpy(dtype=float)
            action_records=self.actions_for_instruments(symbols)
            valid_actions=np.asarray([isinstance(action_records,dict) and isinstance(action_records.get(symbol),list)
                                      and all(isinstance(row.get('date'),str) and str(row.get('thscode','')).upper()==symbol for row in action_records[symbol])
                                      for symbol in symbols],dtype=bool)
            daily=[]
            for day in days:
                values={key:array[take] for key,array in strict.latest(day,requested_fields=requested,arrays=True).items()}
                values['present'] &= valid_actions
                daily.append(values)
            action_coverage=base.action_coverage
            if not isinstance(action_coverage,bool):action_coverage=action_coverage[left:right][:,take]
            prepared.update(unavailable=False,symbols=symbols,symbol_index={symbol:i for i,symbol in enumerate(symbols)},
                            days=days,quotes=quotes,daily=daily,actions=base.actions[left:right][:,take],
                            action_coverage=action_coverage)
            return prepared

    def iter_histories(self, as_of=None):
        frame = self._daily_frame()
        if frame is None:return
        if as_of:frame=frame.loc[frame.date<=as_of]
        for symbol,group in frame.groupby('instrument_id',sort=False):
            yield symbol,group.to_dict('records')

    def amount_top10_history(self, days):
        """Project only dated amount facts; do not compute seven full panels."""
        from .amount_rank import amount_ranking_fact
        with self._lock:
            head = self._load()
            snapshot = head.get('snapshot_id') if head else None
            requested = list(dict.fromkeys(days))
            if not head or not requested:
                return {'snapshot_id': snapshot, 'days': {}}
            calendar = set(self.calendar())
            requested = [day for day in requested if day in calendar]
            frame = self._daily_frame()
            if frame is None:
                return {'snapshot_id': snapshot, 'days': {}}
            selected = frame.loc[frame.date.isin(requested), ['instrument_id', 'date', 'amount']]
            found = {str(day): amount_ranking_fact(group.to_dict('records'), str(day))
                     for day, group in selected.groupby('date', sort=False)}
            return {'snapshot_id': snapshot, 'days': found}

    def panel(self, as_of=None):
        head = self._load()
        if not head:return []
        as_of = as_of or head['data_end']
        if as_of not in self.calendar():raise ValueError('该日期不是已留存完成的交易日')
        with self._lock:
            if as_of in self._panels:return [dict(r) for r in self._panels[as_of]]
            meta = self._reference(); calendar = self.calendar()
            from .panel_inputs import PanelInputs
            frame = self._daily_frame()
            if self._panel_inputs is None:self._panel_inputs=PanelInputs(frame,calendar)
            indicators=self._panel_inputs.latest(as_of)
            latest=frame.loc[frame.date==as_of].to_dict('records')
            history_day=meta.get('pool_history',{}).get(as_of,{})
            pool_rows=meta.get('limitup',[]) if as_of==meta.get('pool_trade_date') else history_day.get('limitup',[])
            pool = {r['thscode']:r for r in pool_rows if r.get('thscode')}
            complete=(meta.get('pool_coverage',{}).get('limitup',{}) if as_of==meta.get('pool_trade_date') else history_day.get('coverage',{}).get('limitup',{})).get('complete',False)
            topic_map = defaultdict(list)
            if as_of==head['data_end']:
                for t in meta.get('topics',[]):
                    for symbol in t['instrument_ids']:topic_map[symbol].append({'topic_id':t['topic_id'],'name':t['name']})
            result=[]
            for source in latest:
                bar={**source,**indicators[source['instrument_id']]}
                symbol=bar['instrument_id']
                inst = meta.get('master',{}).get(symbol,{})
                up = pool.get(symbol)
                bar.update({'instrument_id':symbol,'code':symbol[:6],'name':inst.get('name',symbol),'exchange':symbol[-2:],'topics':topic_map[symbol],'topic_ids':[t['topic_id'] for t in topic_map[symbol]],'topic_state':'current_partial' if as_of==head['data_end'] else 'unknown','turnover_ratio_pct':None,'limit_up':True if up else False if complete else None,'continue_day_cnt':up.get('continue_day_cnt') if up else None,'limit_up_reason':up.get('limit_up_reason') if up else None,'year_limit_up_count':None,'snapshot_id':head['snapshot_id']})
                from app.domain.universe import identity_flags
                bar.update(identity_flags(inst,as_of))
                result.append(bar)
            self._panels[as_of]=result
            while len(self._panels)>4:self._panels.pop(next(iter(self._panels)))
            return [dict(r) for r in result]

    def chart(self,instrument_id,period='D',as_of=None,price_basis='none',indicator_continuity='market_sessions'):
        period=period.upper()
        if period not in ('D','W','M'):raise ValueError('仅支持日K、周K和月K')
        if indicator_continuity not in ('quotes','market_sessions'):raise ValueError('图表指标连续口径仅支持quotes/market_sessions')
        status=self.status(); as_of=as_of or status.get('data_end')
        calendar=self.calendar()
        raw=self.bars(instrument_id,as_of)
        forward,adjustment=self.prices(instrument_id,as_of,'forward')
        from .chart_annotation_projection import annotation_projection_evidence
        annotation_projection = annotation_projection_evidence(raw, as_of, status.get('snapshot_id'))
        if price_basis not in ('none','forward'):raise ValueError('价格口径仅支持none/forward')
        rows=forward if price_basis=='forward' else raw
        actual_basis='forward_event_affine' if price_basis=='forward' and adjustment.get('usable') else 'none'
        if period=='W':rows=aggregate_weekly(rows,calendar,as_of)
        if period=='M':
            from .monthly import aggregate_monthly,enrich_monthly_bars
            bars=enrich_monthly_bars(aggregate_monthly(rows,calendar,as_of),include_chart_extremes=True)
        else:bars=enrich_bars(rows,calendar if period=='D' else None,include_chart_extremes=True)
        from .chart_indicators import display_indicators,indicator_gaps,quote_gaps
        bars,actual_continuity=display_indicators(bars,rows,indicator_continuity,period)
        gaps=indicator_gaps(bars)
        missing_quotes=quote_gaps(raw,calendar)
        continuity_notice=('日K图表MA/MACD/KDJ按实际报价连续计算；不补停牌或缺失K线，不据缺行认定停牌。策略、跟踪与回测信号仍按严格市场交易日口径。'
                           if actual_continuity=='quotes' else '图表指标采用严格市场交易日/完整周期口径；停牌或未解释的缺报价会重新预热，周/月不会跳过缺失周期。')
        breadth_scope=None
        if canonical(instrument_id)=='000001.SH':
            breadth={r['date']:r for r in self.breadth_history(as_of)}
            for bar in bars:
                r=breadth.get(bar['date']);bar['advancers']=r['up'] if r and r['up']+r['down']+r['flat']>0 else None
            breadth_scope='沪深已留存且有前交易日可比价的样本，除权未知剔除；非沪深京全量'
        warnings=[w for w in status.get('warnings',[]) if not (actual_basis=='forward_event_affine' and '未复权' in w and ('指标' in w or '重启' in w))]
        if price_basis=='forward':warnings.append('本地事件前复权以当前观察日为锚，仅用于价格研究；不等于已处理回测持仓分红、送股或税费。' if adjustment.get('usable') else adjustment.get('reason','复权条件不足，保留未复权价格'))
        from .waves import candidate_waves
        if period=='M':warnings.append('月K由已留存日行情聚合；首月或本月不完整、缺报价和公司行动会阻断指标预热。MACD/KDJ需要35根连续完整可比月柱。')
        same_snapshot = self.status().get('snapshot_id') == status.get('snapshot_id')
        if not same_snapshot:
            annotation_projection.update(available=False, reason='读取期间行情代际变化，不能证明原锚至本图的公司行动区间，请刷新')
        result = {'instrument':self.instrument(instrument_id),'period':period,'as_of':as_of,'bars':bars,'snapshot_id':status.get('snapshot_id'),'annotation_projection_evidence':annotation_projection,'requested_price_basis':price_basis,'price_basis':actual_basis,'price_basis_options':[{'value':'none','label':'未复权','available':True,'reason':None},{'value':'forward','label':'本地前复权','available':bool(adjustment.get('usable')),'reason':adjustment.get('reason')}],'adjustment':adjustment,'indicator_gaps':gaps,'requested_indicator_continuity':indicator_continuity,'indicator_continuity':actual_continuity,'indicator_continuity_notice':continuity_notice,'quote_gaps':missing_quotes,'waves':candidate_waves(bars,threshold_pct=2.0 if period=='D' else 4.0 if period=='W' else 8.0) if self.instrument(instrument_id).get('kind') in ('index','stock') else None,'volume_unit':'股','amount_unit':'CNY','price_unit':'点' if self.instrument(instrument_id)['kind']=='index' else 'CNY','quality':status.get('quality'),'warnings':warnings,'indicator_version':ALGORITHM+'+explicit-chart-basis-v2+display-continuity-v1','breadth_scope':breadth_scope}

        from .chart_session_distance import attach_chart_session_distance
        return attach_chart_session_distance(result, calendar, same_snapshot=same_snapshot)

    def detail_chart(self,instrument_id,as_of=None):
        """Project the detail quote without building an unused full chart.

        The complete unadjusted history still seeds every strict recurrence.
        Only materialization is limited: detail consumes the last bar and its
        selected-day pattern proof, not forward prices, swings or gap lists.
        """
        from .chart_indicators import display_indicators
        from .chart_session_distance import attach_chart_session_distance
        status=self.status();as_of=as_of or status.get('data_end')
        calendar=self.calendar();raw=self.bars(instrument_id,as_of)
        bars=enrich_bars(raw,calendar,tail=2,include_chart_extremes=True)
        bars,_=display_indicators(bars,raw,'market_sessions','D')
        if canonical(instrument_id)=='000001.SH':
            dates={bar['date'] for bar in bars}
            breadth={row['date']:row for row in self.breadth_history(as_of) if row['date'] in dates}
            for bar in bars:
                row=breadth.get(bar['date'])
                bar['advancers']=row['up'] if row and row['up']+row['down']+row['flat']>0 else None
        same_snapshot=self.status().get('snapshot_id')==status.get('snapshot_id')
        result={'instrument':self.instrument(instrument_id),'period':'D','as_of':as_of,
                'bars':bars,'snapshot_id':status.get('snapshot_id'),'price_basis':'none'}
        return attach_chart_session_distance(result,calendar,same_snapshot=same_snapshot)

    def indices(self,as_of=None):
        rows=[]
        for symbol in INDEX_NAMES:
            chart=self.chart(symbol,as_of=as_of)
            if chart['bars']:rows.append({**chart['instrument'],**chart['bars'][-1],'snapshot_id':chart['snapshot_id'],'price_unit':'点'})
        return rows

    def index_quotes(self,as_of=None):
        """Bootstrap quotes require index bars only, never the all-stock frame."""
        from math import isfinite
        status=self.status();as_of=as_of or status.get('data_end');result=[]
        calendar=self.calendar()
        valid_calendar=calendar==sorted(set(calendar))
        previous_day=calendar[calendar.index(as_of)-1] if valid_calendar and as_of in calendar and calendar.index(as_of)>0 else None
        valid_amount=lambda value:isinstance(value,(int,float)) and not isinstance(value,bool) and isfinite(value) and value>=0
        for symbol in INDEX_NAMES:
            values=sorted((dict(row) for row in self._reference().get('indices',{}).get(symbol,[]) if not as_of or row['date']<=as_of),key=lambda row:row['date'])
            bars=enrich_bars(values,calendar)
            if not bars:continue
            comparison={'amount_change':None,'amount_previous_date':previous_day,'amount_previous':None,'amount_change_reason':None,'amount_change_scope':'本指数相邻市场交易日原始日K成交额之差，单位CNY；各指数不能相加作为全市场总额'}
            current=[row for row in values if row['date']==as_of]
            previous=[row for row in values if row['date']==previous_day]
            if previous_day is None:
                comparison['amount_change_reason']='尚无可核验的前一市场交易日成交额'
            elif len(current)!=1 or len(previous)!=1:
                comparison['amount_change_reason']='相邻市场交易日指数成交额缺失或重复，不回溯其他报价日期比较'
            elif not all(valid_amount(row.get('amount')) for row in (current[0],previous[0])):
                comparison['amount_change_reason']='相邻市场交易日指数成交额不是有效的非负有限金额'
            elif current[0].get('source')!=previous[0].get('source') or any(row.get('currency','CNY')!='CNY' for row in (current[0],previous[0])):
                comparison['amount_change_reason']='相邻市场交易日指数成交额来源或币种口径不一致'
            else:
                comparison['amount_change']=current[0]['amount']-previous[0]['amount']
                comparison['amount_previous']=previous[0]['amount']
            result.append({**self.instrument(symbol),**bars[-1],**comparison,'snapshot_id':status.get('snapshot_id'),'price_unit':'点','amount_unit':'CNY'})
        return result

    def breakout_states(self,as_of=None):
        """Extra dated strategy feature on arrays, without full Python histories."""
        from .rolling_high import matrix_breakout_states
        status=self.status();as_of=as_of or status.get('data_end')
        panel={row['instrument_id']:row for row in self.panel(as_of)}
        with self._lock:
            values=self._panel_inputs
            counts=[panel.get(symbol,{}).get('indicator_warmup_bars',0) for symbol in values.symbols]
            result=matrix_breakout_states(values.values['close'],values.values['high'],counts,values.positions[as_of],values.symbols,volume=values.values['volume'])
            known_actions=(self.data_dir/'published'/status['snapshot_id']/'corporate-actions.parquet').is_file()
            if not known_actions:
                for row in result.values():row['breakout_valid']=False
            return result

    def turnover_history(self,as_of=None):
        """CNY turnover of retained A-share stocks on all three exchanges.

        Keep breadth's separate Shanghai/Shenzhen sample definition unchanged.
        A generation-sized aggregation is cached once, then dates are sliced.
        """
        with self._lock:
            self._load()
            if self._turnover is None:
                frame=self._daily_frame()
                if frame is None:return []
                master=self._reference().get('master',{})
                stocks={symbol for symbol,info in master.items() if info.get('kind','stock')=='stock' and symbol.endswith(('.SH','.SZ','.BJ'))}
                sample=frame.loc[frame.instrument_id.isin(stocks),['date','amount']].copy()
                import numpy as np
                valid=np.isfinite(sample.amount)&(sample.amount>=0)
                sample['invalid_amount']=~valid
                grouped=sample.groupby('date',sort=True).agg(amount=('amount','sum'),invalid_amount=('invalid_amount','sum'),observed=('amount','size'))
                self._turnover=[{'date':day,'amount':float(row['amount']) if row['invalid_amount']==0 else None,'observed':int(row['observed']),'invalid_amount':int(row['invalid_amount']),'scope':'沪深京已留存A股股票成交额','source':'fuyao','quality':'partial'} for day,row in grouped.iterrows()]
            return [dict(row) for row in self._turnover if not as_of or row['date']<=as_of]

    def breadth_history(self,as_of=None):
        self._load()
        if self._breadth is not None:return [dict(r) for r in self._breadth if not as_of or r['date']<=as_of]
        frame=self._daily_frame()
        if frame is None:return []
        frame=frame.copy();frame['prev_close']=frame.groupby('instrument_id').close.shift(1)
        previous_date=frame.groupby('instrument_id').date.shift(1)
        calendar=self.calendar(); previous=dict(zip(calendar[1:],calendar[:-1]))
        comparable=previous_date.eq(frame.date.map(previous)) & frame.prev_close.notna() & ~frame.corporate_action
        frame['up']=(frame.close>frame.prev_close)&comparable
        frame['down']=(frame.close<frame.prev_close)&comparable
        frame['flat']=(frame.close==frame.prev_close)&comparable
        frame['unknown']=~comparable
        frame=frame.loc[frame.instrument_id.str.endswith(('.SH','.SZ'))]
        grouped=frame.groupby('date').agg(up=('up','sum'),down=('down','sum'),flat=('flat','sum'),unknown=('unknown','sum'),amount=('amount','sum'),observed=('instrument_id','count'))
        self._breadth=[{'date':day,**{k:int(v) if k!='amount' else float(v) for k,v in row.items()},'broad_rise_line':3000,'broad_fall_line':500,'scope':'沪深已留存可比标的；除权未知剔除','source':'fuyao','quality':'partial'} for day,row in grouped.iterrows()]
        return [dict(r) for r in self._breadth if not as_of or r['date']<=as_of]

    def reference(self): return dict(self._reference())
