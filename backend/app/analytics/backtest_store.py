"""Frozen backtest jobs and immutable result publication, separate from simulation."""
from pathlib import Path
import json
import shutil
import pyarrow as pa
import pyarrow.parquet as pq

from app.infrastructure.file_operations import flush_directory, flush_file, move_path
from app.core import connect,data_dir,published_dir,new_id,now_ms,dumps,digest,hash_file,atomic_json,publish_snapshot
from .market import MarketData
from .backtest_engine import VERSION,availability,config_checked,run_backtest,engine_version
from app.domain.conditions import names
from app.services.task_service import TaskCancelled,TaskPaused,append_event
from app.services.workspace_deletion import require_strategy_present,backtest_block_reason


class BacktestRequestConflict(ValueError):
    """One idempotency key cannot stand for different frozen requests or job kinds."""


def insert(c,table,row):
    keys=list(row)
    c.execute('INSERT INTO '+table+' ('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')',[row[k] for k in keys])


def context(strategy_id):
    with connect() as c:
        c.execute('BEGIN')
        require_strategy_present(c,strategy_id)
        strategy=c.execute('SELECT s.name,s.strategy_id,v.* FROM strategy s JOIN strategy_version v ON v.strategy_id=s.strategy_id AND v.version=s.current_version WHERE s.strategy_id=? AND s.archived=0',(strategy_id,)).fetchone()
        if not strategy:raise ValueError('策略不存在或已归档')
        snapshots=[dict(r) for r in c.execute("SELECT s.* FROM published_head h JOIN dataset_snapshot s ON s.snapshot_id=h.snapshot_id WHERE h.module IN ('calendar','security_identity','daily_raw','corporate_actions','analysis_bars','indicators')")]
    item=dict(strategy);item['period']=item.get('signal_period') or item['period'];item['definition']=json.loads(item['definition_json']);item['trade_rules']=json.loads(item['trade_rules_json']);item['hot_universe']=json.loads(item['hot_universe_json']) if item.get('hot_universe_json') else None
    raw=next((r for r in snapshots if r['module']=='daily_raw'),None)
    if not raw:raise ValueError('请先手动初始化真实行情')
    generation=json.loads(raw['lineage_json']).get('market_snapshot_id')
    if not generation:raise ValueError('快照缺少行情代际，不能回测')
    return MarketData(data_dir(),snapshot_id=generation),item,{r['module']:r['snapshot_id'] for r in snapshots},generation


def get_availability(strategy_id):
    market,strategy,_,_=context(strategy_id)
    return availability(market,strategy)


def create_backtest(payload):
    requested_key=payload.get('request_key')
    if isinstance(requested_key,str):
        with connect() as c:
            previous=c.execute("SELECT j.kind,b.backtest_id FROM job j LEFT JOIN backtest_run b USING(job_id) WHERE j.request_key=?",(requested_key,)).fetchone()
            if previous and previous['kind']=='backtest' and not previous['backtest_id']:
                raise BacktestRequestConflict('此请求对应的回测已永久删除，请使用新的请求键')
            if c.execute('SELECT 1 FROM job j JOIN backtest_run b USING(job_id) JOIN backtest_deletion d USING(backtest_id) WHERE j.request_key=?',(requested_key,)).fetchone():
                raise BacktestRequestConflict('此请求对应的回测已移入回收站，请先恢复或使用新的请求键')
    market,strategy,inputs,generation=context(payload.get('strategy_id'))
    config=config_checked(payload);bounds=availability(market,strategy,config['exit_ma'])
    if not bounds['available']:raise ValueError(bounds['reason'])
    start=payload.get('start_date');end=payload.get('end_date');cal=market.calendar()
    if start not in cal or end not in cal or start>end or start<bounds['min_date'] or end>bounds['max_date']:raise ValueError('起止日必须在当前策略可回测范围内，且为已完成交易日')
    sessions=sum(start<=d<=end for d in cal)
    if sessions<10:raise ValueError('回测区间至少10个市场交易日')
    frozen={'name':strategy['name'],'strategy_id':strategy['strategy_id'],'version':strategy['version'],'period':strategy['period'],'definition':strategy['definition'],'trade_rules':bounds['trade_rules'],'market_snapshot_id':generation}
    if strategy.get('price_basis','none')=='forward':
        from app.domain.strategy_prices import VERSION as price_version
        frozen.update(price_basis='forward',price_basis_contract=price_version)
    execution_version=engine_version(frozen) if frozen.get('price_basis')=='forward' else VERSION
    predicates=[strategy['definition']]+[tree for tree in bounds['trade_rules'].values() if tree is not None]
    if any('higher_lows' in names(tree) for tree in predicates):
        from .higher_lows import VERSION as structure_version
        frozen['condition_contracts']={'higher_lows':structure_version}
    if any('boll_signal' in names(tree) for tree in predicates):
        from .boll_conditions import VERSION as boll_version
        frozen.setdefault('condition_contracts',{})['boll_signal']=boll_version
    requested_key=payload.get('request_key')
    if requested_key is not None and (not isinstance(requested_key,str) or not requested_key.strip() or len(requested_key)>200):raise ValueError('request_key必须为1至200字符的非空字符串')
    request_key=requested_key if requested_key is not None else new_id('manual-backtest')
    key=digest({'strategy':frozen,'config':config,'start':start,'end':end,'inputs':inputs,'engine_version':execution_version})
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        require_strategy_present(c,strategy['strategy_id'])
        if c.execute('SELECT archived FROM strategy WHERE strategy_id=?',(strategy['strategy_id'],)).fetchone()['archived']:
            raise BacktestRequestConflict('策略已归档，不能创建回测')
        previous=c.execute('SELECT j.kind,b.backtest_id,b.status,b.input_sha256,d.deleted_at_ms FROM job j LEFT JOIN backtest_run b ON b.job_id=j.job_id LEFT JOIN backtest_deletion d ON d.backtest_id=b.backtest_id WHERE j.request_key=?',(request_key,)).fetchone()
        if previous:
            if previous['deleted_at_ms'] is not None:raise BacktestRequestConflict('此请求对应的回测已移入回收站，请先恢复或使用新的请求键')
            if previous['kind']!='backtest' or not previous['backtest_id']:raise BacktestRequestConflict('request_key已被其他任务占用，请使用新的请求键')
            if previous['input_sha256']!=key:raise BacktestRequestConflict('request_key对应的策略、区间、参数或输入快照不同，或冻结引擎版本不同，请使用新的请求键')
            return {'backtest_id':previous['backtest_id'],'status':previous['status'],'reused':True}
        active=c.execute("SELECT b.backtest_id,b.status FROM backtest_run b JOIN job j ON b.job_id=j.job_id WHERE j.logical_key=? AND j.status IN ('queued','running','paused')",('backtest:'+key,)).fetchone()
        if active:return {'backtest_id':active[0],'status':active[1],'reused':True}
        job_id=new_id('job');bid=new_id('backtest');now=now_ms()
        params={'backtest_id':bid}
        insert(c,'job',{'job_id':job_id,'request_key':request_key,'logical_key':'backtest:'+key,'kind':'backtest','target_date':end,'parameters_json':dumps(params),'parameters_hash':digest(params),'status':'queued','phase':'等待回测','created_at_ms':now})
        predicates=[strategy['definition']]+[tree for tree in frozen['trade_rules'].values() if tree is not None]
        insert(c,'backtest_run',{'backtest_id':bid,'job_id':job_id,'strategy_id':strategy['strategy_id'],'strategy_version':strategy['version'],'requested_start':start,'requested_end':end,'input_snapshots_json':dumps(inputs),'required_features_json':dumps(sorted(set().union(*(names(tree) for tree in predicates)))),'warmup_contract_json':dumps({'bars':bounds['warmup_bars'],'period':strategy['period'],**({'signal_price_basis':'forward','price_basis_contract':frozen['price_basis_contract']} if frozen.get('price_basis')=='forward' else {})}),'range_acceptance_json':dumps({'accepted':True,'accepted_start':start,'accepted_end':end,'calendar_sessions':sessions,'dependency_gaps':0,'scope':'restricted_complete_quote_no_action_sample','full_market_coverage':False,**({'signal_price_basis':'forward','execution_price_basis':'none'} if frozen.get('price_basis')=='forward' else {})}),'strategy_snapshot_json':dumps(frozen),'execution_config_json':dumps(config),'engine_version':execution_version,'input_sha256':key,'status':'queued','created_at_ms':now})
        for snapshot in set(inputs.values()):
            c.execute("INSERT INTO retention_pin(pin_id,snapshot_id,owner_kind,owner_id,created_at_ms) VALUES(?,?,?,?,?) ON CONFLICT(snapshot_id,owner_kind,owner_id) DO NOTHING",(new_id('pin'),snapshot,'backtest',bid,now))
    return {'backtest_id':bid,'job_id':job_id,'status':'queued'}


def _metadata(row):
    frozen=json.loads(row['strategy_snapshot_json']);summary=json.loads(row['summary_json']) if row['summary_json'] else {}
    state=row['status']
    if state!='completed' and row.get('job_status') in ('cancelled','paused'):state=row['job_status']
    deleted=row.get('deleted_at_ms') is not None
    blocked=backtest_block_reason(row)
    purge=json.loads(row['active_purge_json']) if row.get('active_purge_json') else None
    restore_blocked='永久清理已确认，请查看清理任务' if purge and purge['status'] in ('queued','cleaning') else None
    restore_blocked=restore_blocked or ('请先恢复所属策略' if deleted and row.get('strategy_deleted') else None)
    return {'backtest_id':row['backtest_id'],'job_id':row['job_id'],'status':state,'calculation_status':row['status'],'task_status':row.get('job_status'),'strategy_id':row['strategy_id'],'strategy_name':frozen['name'],'strategy_version':row['strategy_version'],'trade_rules':frozen.get('trade_rules',{'entry':None,'exit':None}),'start_date':row['requested_start'],'end_date':row['requested_end'],'config':json.loads(row['execution_config_json']),'stats':summary.get('stats'),'error':summary.get('error'),'created_at_ms':row['created_at_ms'],'completed_at_ms':row['completed_at_ms'],'engine_version':row['engine_version'],
            'active_purge':purge,'deleted':deleted,'deleted_at_ms':row.get('deleted_at_ms'),'can_delete':not deleted and blocked is None,'delete_blocked_reason':blocked if not deleted else None,'can_restore':deleted and restore_blocked is None,'restore_blocked_reason':restore_blocked,
            **({'signal_price_basis':'forward','execution_price_basis':'none','price_basis_contract':frozen.get('price_basis_contract')} if frozen.get('price_basis')=='forward' else {})}


_READ_SELECT="""SELECT b.*,j.status job_status,j.lease_owner,j.lease_expires_at_ms,d.deleted_at_ms,sd.strategy_id strategy_deleted,(SELECT json_object('purge_id',p.purge_id,'status',p.status,'business_deleted',p.business_deleted) FROM workspace_purge p WHERE p.target_kind='backtest' AND p.target_id=b.backtest_id AND p.superseded_by IS NULL AND p.status IN ('queued','cleaning','failed') ORDER BY p.created_at_ms DESC,p.rowid DESC LIMIT 1) active_purge_json FROM backtest_run b JOIN job j ON j.job_id=b.job_id LEFT JOIN backtest_deletion d USING(backtest_id) LEFT JOIN strategy_deletion sd ON sd.strategy_id=b.strategy_id"""


def list_backtests(deleted=False,limit=100,offset=0):
    if not isinstance(deleted,bool) or isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=100 or isinstance(offset,bool) or not isinstance(offset,int) or offset<0:raise ValueError('回测列表分页参数无效')
    condition='d.backtest_id IS NOT NULL' if deleted else 'd.backtest_id IS NULL'
    with connect() as c:
        items=[_metadata(dict(r)) for r in c.execute(_READ_SELECT+' WHERE '+condition+' ORDER BY b.created_at_ms DESC,b.backtest_id LIMIT ? OFFSET ?',(limit,offset))]
        total=c.execute('SELECT count(*) FROM backtest_run b LEFT JOIN backtest_deletion d USING(backtest_id) WHERE '+condition).fetchone()[0]
    return {'items':items,'total':total,'limit':limit,'offset':offset,'has_more':offset+len(items)<total}


def get_backtest(backtest_id):
    with connect() as c:
        row=c.execute(_READ_SELECT+' WHERE b.backtest_id=? AND d.backtest_id IS NULL',(backtest_id,)).fetchone()
        if not row:raise KeyError('回测不存在')
        row=dict(row);result=_metadata(row)
        if row['status']=='completed':
            artifact=c.execute("SELECT object_key,sha256 FROM snapshot_artifact WHERE snapshot_id=? AND role='report'",(row['result_snapshot_id'],)).fetchone()
        else:artifact=None
    if artifact:
        path=(published_dir()/artifact['object_key']).resolve()
        if not path.is_relative_to(published_dir().resolve()) or not path.is_file() or hash_file(path)!=artifact['sha256']:raise ValueError('结果文件缺失或校验和不一致')
        result.update(json.loads(path.read_text()))
    return result


def delete_backtest(backtest_id):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute(_READ_SELECT+' WHERE b.backtest_id=?',(backtest_id,)).fetchone()
        if not row:raise KeyError('回测不存在')
        row=dict(row)
        if row['deleted_at_ms'] is None:
            reason=backtest_block_reason(row)
            if reason:raise BacktestRequestConflict(reason)
            stamp=now_ms()
            c.execute('INSERT INTO backtest_deletion(backtest_id,deleted_at_ms) VALUES(?,?)',(backtest_id,stamp))
        else:stamp=row['deleted_at_ms']
        return {'backtest_id':backtest_id,'deleted':True,'deleted_at_ms':stamp,'status':_metadata(row)['status']}


def restore_backtest(backtest_id):
    from app.services.workspace_purge import require_not_purging
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        require_not_purging(c,'backtest',backtest_id)
        row=c.execute(_READ_SELECT+' WHERE b.backtest_id=?',(backtest_id,)).fetchone()
        if not row:raise KeyError('回测不存在')
        row=dict(row)
        require_not_purging(c,'strategy',row['strategy_id'])
        if row['deleted_at_ms'] is not None and row['strategy_deleted']:
            raise BacktestRequestConflict('请先恢复所属策略，再恢复关联回测')
        c.execute('DELETE FROM backtest_deletion WHERE backtest_id=?',(backtest_id,))
        return {'backtest_id':backtest_id,'deleted':False,'deleted_at_ms':None,'status':_metadata(row)['status']}


def execute_backtest(job_id,progress=None):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM backtest_run WHERE job_id=?',(job_id,)).fetchone()
        if not row:raise ValueError('回测任务没有冻结输入')
        row=dict(row)
        if c.execute('SELECT 1 FROM backtest_deletion WHERE backtest_id=?',(row['backtest_id'],)).fetchone():raise ValueError('回测已移入回收站，不能继续执行')
        require_strategy_present(c,row['strategy_id'])
        if row['status']=='completed':return get_backtest(row['backtest_id'])
        if row['status']=='queued':c.execute("UPDATE backtest_run SET status='running' WHERE backtest_id=?",(row['backtest_id'],))
        elif row['status']!='running':raise ValueError('仅恢复授权running回测；已失败/取消需手动新建')
    try:
        frozen=json.loads(row['strategy_snapshot_json']);config=json.loads(row['execution_config_json'])
        execution_version=engine_version(frozen) if frozen.get('price_basis')=='forward' else VERSION
        if row['engine_version'] != execution_version:
            raise ValueError('回测冻结引擎版本'+row['engine_version']+'与当前'+execution_version+'不同；保留旧任务与结果，请使用当前版本手动新建回测')
        if frozen.get('price_basis')=='forward':
            from app.domain.strategy_prices import VERSION as price_version
            if frozen.get('price_basis_contract')!=price_version:raise ValueError('前复权信号价格契约与冻结输入不同，请保留旧任务并手动新建回测')
        predicates=[frozen['definition']]+[tree for tree in frozen.get('trade_rules',{}).values() if tree is not None]
        if any('higher_lows' in names(tree) for tree in predicates):
            from .higher_lows import VERSION as structure_version
            if frozen.get('condition_contracts',{}).get('higher_lows')!=structure_version:
                raise ValueError('底部抬高冻结条件版本不匹配，请保留旧任务并手动新建回测')
        if any('boll_signal' in names(tree) for tree in predicates):
            from .boll_conditions import VERSION as boll_version
            if frozen.get('condition_contracts',{}).get('boll_signal')!=boll_version:
                raise ValueError('BOLL冻结条件版本不匹配，请保留旧任务并手动新建回测')
        market=MarketData(data_dir(),snapshot_id=frozen['market_snapshot_id'])
        result=run_backtest(market,frozen,{**config,'start_date':row['requested_start'],'end_date':row['requested_end']},progress)
        if result.get('engine_version') != row['engine_version']:
            raise ValueError('回测计算结果的引擎版本与冻结输入不同，拒绝发布')
        bid=row['backtest_id'];result.update(backtest_id=bid,job_id=job_id,status='completed',strategy_id=frozen['strategy_id'],strategy_name=frozen['name'],strategy_version=frozen['version'],input_snapshots=json.loads(row['input_snapshots_json']),market_snapshot_id=frozen['market_snapshot_id'],input_sha256=row['input_sha256'])
        folder=published_dir()/'backtests'/bid
        stage=published_dir()/'backtests'/('.staging-'+bid)
        stage.mkdir(parents=True,exist_ok=True)
        artifacts=[]
        for kind in ('signals','orders','fills','positions','trades','equity'):
            records=result[kind]
            flattened=[{k:(dumps(v) if isinstance(v,(dict,list)) else v) for k,v in r.items()} for r in records]
            table=pa.Table.from_pylist(flattened) if flattened else pa.table({'_empty':pa.array([],type=pa.string())})
            target=stage/(kind+'.parquet')
            pq.write_table(table,target,compression='zstd')
            flush_file(target)
        result['signal_count']=len(result['signals']);result['signals_preview']=result.pop('signals')[:50]
        atomic_json(stage/'report.json',result)
        # Interrupted attempts may have renamed but not catalogued; files are deterministic.
        if folder.exists():
            for existing in folder.iterdir():
                candidate=stage/existing.name
                if not candidate.exists() or hash_file(existing)!=hash_file(candidate):raise ValueError('恢复回测遇到不同内容的已存在结果目录')
            shutil.rmtree(stage)
        else:
            move_path(stage,folder)
            flush_directory(folder.parent)
        counts={k:len(result.get(k,[])) for k in ('orders','fills','positions','trades','equity')};counts['signals']=result['signal_count']
        for path in sorted(folder.iterdir()):
            artifacts.append({'object_key':path.relative_to(published_dir()).as_posix(),'sha256':hash_file(path),'byte_count':path.stat().st_size,'row_count':counts.get(path.stem,1),'role':'report' if path.suffix=='.json' else path.stem})
        if progress:progress('发布回测明细和权益',95)
        snapshot=publish_snapshot(module='backtest_result',job_id=job_id,start=row['requested_start'],end=row['requested_end'],artifacts=artifacts,quality='partial',scope=bid,lineage={'input_snapshots':result['input_snapshots'],'input_sha256':row['input_sha256'],'market_snapshot_id':frozen['market_snapshot_id'],'data_quality':result['data_quality']},price_basis='none',algorithm=row['engine_version'],snapshot_id='result-'+bid,activate=False)
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            job_state=c.execute('SELECT status FROM job WHERE job_id=?',(job_id,)).fetchone()[0]
            if job_state=='cancelled':raise TaskCancelled()
            if job_state=='paused':raise TaskPaused()
            c.execute("INSERT INTO published_head(module,scope_key,snapshot_id,changed_at_ms) VALUES('backtest_result',?,?,?) ON CONFLICT(module,scope_key) DO UPDATE SET snapshot_id=excluded.snapshot_id,changed_at_ms=excluded.changed_at_ms",(bid,snapshot,now_ms()))
            c.execute("UPDATE backtest_run SET result_snapshot_id=?,summary_json=?,completed_at_ms=?,status='completed' WHERE backtest_id=?",(snapshot,dumps({'stats':result['stats'],'data_quality':result['data_quality']}),now_ms(),bid))
            c.execute("UPDATE job SET status='succeeded',phase='succeeded',accepted_batches=100,finished_at_ms=?,lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?",(now_ms(),job_id))
            append_event(c,job_id,'succeeded','回测结果与七项指标已发布；研究样本限制见报告',details={'backtest_id':bid,'closed_trades':result['stats']['trade_count']})
        return result
    except TaskPaused:
        # Keep frozen running calculation resumable under the same authorized job.
        raise
    except TaskCancelled:
        with connect() as c:c.execute("UPDATE backtest_run SET status='cancelled',summary_json=? WHERE backtest_id=? AND status<>'completed'",(dumps({'error':'用户取消回测，保留已发布行情'}),row['backtest_id']))
        raise
    except Exception as error:
        with connect() as c:c.execute("UPDATE backtest_run SET status='failed',summary_json=? WHERE backtest_id=? AND status<>'completed'",(dumps({'error':str(error)}),row['backtest_id']))
        raise
