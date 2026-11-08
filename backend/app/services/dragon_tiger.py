"""Manual dated dragon-tiger capture, immutable local reads and 30-session GC.

Only worker entry points instantiate providers. API views read one selected day
and a small index, never enumerate historical directories or open DuckDB.
"""
from __future__ import annotations
from datetime import date
import json
from pathlib import Path
import re

from fastapi import HTTPException
from app.core import atomic_json,connect,digest,dumps,get_market,hash_file,now_ms,published_dir
from app.adapters.dragon_tiger import VERSION,FUYAO_PATH,REPORTS,DragonTigerPublicClient,normalize_board,normalize_seats,project_hot_statistics,project_seat_ranges
from app.adapters.providers import ProviderError
from .supplement_access import supplement_access
from .supplement_storage import _read_object
from . import task_service as tasks

WINDOW=30
_CAPABILITIES=('fuyao-all','fuyao-org','fuyao-hot_money','eastmoney-buy','eastmoney-sell','eastmoney-disclosures')
_CAS=re.compile(r'artifacts/[0-9a-f]{64}\.json\Z')
_DAY=re.compile(r'\d{4}-\d{2}-\d{2}\Z')


def validate_policy(value):
    if value not in (None,VERSION):raise ValueError('龙虎榜冻结采集策略版本不受支持')
    return value


def _root():return published_dir()/'dragon-tiger'


def window(calendar,target):
    if (not isinstance(calendar,(list,tuple)) or not calendar or any(not isinstance(day,str) or not _DAY.fullmatch(day) or date.fromisoformat(day).isoformat()!=day for day in calendar)
        or any(a>=b for a,b in zip(calendar,calendar[1:])) or target not in calendar):raise ValueError('龙虎榜需要有效的已发布市场交易日历')
    return list(calendar[max(0,calendar.index(target)-WINDOW+1):calendar.index(target)+1])


def _sanitize(value,secret=None):
    if isinstance(value,dict):
        return {key:_sanitize(item,secret) for key,item in value.items() if re.sub('[^a-z]','',str(key).lower()) not in ('authorization','apikey','xapikey','token','presignedurl','presignedurlexpiresat')}
    if isinstance(value,list):return [_sanitize(item,secret) for item in value]
    if isinstance(value,str) and secret and secret in value:return value.replace(secret,'[redacted]')
    return value


class Store:
    def __init__(self,root=None):self.root=Path(root) if root is not None else _root()
    def object(self,pointer):
        if not isinstance(pointer,dict):raise ValueError('龙虎榜对象引用无效')
        return _read_object(self.root,{'record_ref':pointer.get('record_ref'),'sha256':pointer.get('sha256')})
    def save(self,value):
        if not isinstance(value,dict) or len(dumps(value).encode())>32*1024*1024:raise ValueError('龙虎榜对象超出32MiB或格式无效')
        relative='artifacts/'+digest(value)+'.json';path=self.root/relative
        if path.exists():
            result=self.object({'record_ref':relative,'sha256':hash_file(path)})
            if result!=value:raise ValueError('龙虎榜不可变对象内容冲突')
        else:atomic_json(path,value)
        return {'record_ref':relative,'sha256':hash_file(path)}
    def index(self):
        path=self.root/'head.json'
        if not path.is_file():return {'version':VERSION,'anchor_date':None,'days':{},'failures':{}}
        if path.is_symlink() or path.stat().st_size>4096:raise ValueError('龙虎榜索引头不可核验')
        head=json.loads(path.read_text())
        if head.get('version')!=VERSION:raise ValueError('龙虎榜索引版本不受支持')
        value=self.object(head)
        if value.get('version')!=VERSION or not isinstance(value.get('days'),dict) or len(value['days'])>WINDOW:raise ValueError('龙虎榜索引范围无效')
        if any(not _DAY.fullmatch(day) for day in value['days']):raise ValueError('龙虎榜索引日期无效')
        return value
    def day(self,index,day):
        pointer=index.get('days',{}).get(day)
        if pointer is None:return None
        value=self.object(pointer)
        if value.get('version')!=VERSION or value.get('date')!=day or value.get('accepted') is not True:raise ValueError('龙虎榜逐日对象版本或日期不一致')
        return value
    def part(self,capability,day,fetch,validate):
        if capability not in _CAPABILITIES:raise ValueError('龙虎榜采集能力不受支持')
        pointer_path=self.root/'parts'/capability/(day+'.json')
        if pointer_path.is_file():
            if pointer_path.is_symlink() or pointer_path.stat().st_size>4096:raise ValueError('龙虎榜缓存指针不可核验')
            pointer=json.loads(pointer_path.read_text());saved=self.object(pointer)
            if saved.get('version')!=VERSION or saved.get('date')!=day or saved.get('capability')!=capability:raise ValueError('龙虎榜缓存日期或能力不一致')
            validate(saved['payload'])
            return saved['payload'],{'record_ref':pointer['record_ref'],'sha256':pointer['sha256']},True
        payload=fetch();validate(payload)
        value={'version':VERSION,'date':day,'capability':capability,'fetched_at_ms':now_ms(),'payload':payload}
        pointer=self.save(value);atomic_json(pointer_path,pointer)
        return payload,pointer,False
    def _publish(self,index,guard=None):
        pointer=self.save(index)
        if guard:guard()
        atomic_json(self.root/'head.json',{'version':VERSION,**pointer})
    def accept(self,record,calendar,*,guard=None):
        pointer=self.save(record)
        with supplement_access(exclusive=True,guard=guard):
            index=self.index();old_anchor=index.get('anchor_date')
            anchor=max(old_anchor or record['date'],record['date']);keep=set(window(calendar,anchor))
            index={**index,'version':VERSION,'anchor_date':anchor,'snapshot_id':record.get('snapshot_id'),'updated_at_ms':now_ms(),
                   'days':{day:value for day,value in {**index.get('days',{}),record['date']:pointer}.items() if day in keep},
                   'failures':{day:value for day,value in index.get('failures',{}).items() if day in keep and day!=record['date']}}
            self._publish(index,guard)
        return index
    def failure(self,day,reason,*,guard=None):
        # A failed day updates diagnostics only. It never advances the anchor,
        # drops an accepted day, or starts collection/GC in an API request.
        with supplement_access(exclusive=True,guard=guard):
            index=self.index();failures={**index.get('failures',{}),day:{'date':day,'reason':reason,'at_ms':now_ms()}}
            index={**index,'last_attempt_date':day,'failures':dict(sorted(failures.items())[-WINDOW:]),'updated_at_ms':now_ms()}
            self._publish(index,guard)
    def cleanup(self,calendar,target,*,guard=None):
        """Mark current immutable references, then unlink own unreferenced CAS.

        Only a successful publication calls this. Temporary verified components
        for missing dates in the current request window remain resumable.
        """
        with supplement_access(exclusive=True,guard=guard):
            if guard:guard()
            head=json.loads((self.root/'head.json').read_text());index=self.index();keep={head['record_ref']}
            def mark(pointer):
                if not isinstance(pointer,dict) or not isinstance(pointer.get('record_ref'),str) or not _CAS.fullmatch(pointer['record_ref']):raise ValueError('龙虎榜清理引用无效')
                self.object(pointer);keep.add(pointer['record_ref'])
            for day,pointer in index['days'].items():
                mark(pointer);record=self.day(index,day)
                for source in record.get('source_objects',[]):mark(source)
            anchor=index.get('anchor_date')
            if anchor is None:return {'removed_files':0,'removed_bytes':0,'anchor_date':None,'reason':'尚无成功发布龙虎榜日，不清理旧资料'}
            keep_days=set(window(calendar,anchor))
            # Failed newer dates cannot move the expiry boundary. Their already
            # checked components remain resumable inside the frozen 30-day plan.
            keep_days.update(day for day in window(calendar,target) if day>anchor)
            expired=[];part_count=0
            for capability in _CAPABILITIES:
                folder=self.root/'parts'/capability
                if folder.is_symlink():raise ValueError('龙虎榜缓存目录不允许符号链接')
                if not folder.is_dir():continue
                for path in folder.iterdir():
                    part_count+=1
                    if part_count>4096 or path.is_symlink() or not path.is_file() or not _DAY.fullmatch(path.stem) or path.suffix!='.json':raise ValueError('龙虎榜缓存文件范围不可核验')
                    if date.fromisoformat(path.stem).isoformat()!=path.stem:raise ValueError('龙虎榜缓存日期不可核验')
                    if path.stem in keep_days:mark(json.loads(path.read_text()))
                    else:expired.append(path)
            objects=[];object_count=0;folder=self.root/'artifacts'
            if folder.is_symlink():raise ValueError('龙虎榜对象目录不允许符号链接')
            for path in folder.iterdir() if folder.is_dir() else ():
                object_count+=1
                if object_count>10000 or path.is_symlink() or not path.is_file() or not _CAS.fullmatch('artifacts/'+path.name):raise ValueError('龙虎榜清理对象范围超出预算')
                if 'artifacts/'+path.name not in keep:objects.append(path)
            if guard:guard()
            if json.loads((self.root/'head.json').read_text())!=head:raise ValueError('龙虎榜发布头变化，未执行清理')
            removed=0;removed_bytes=0
            for path in [*expired,*objects]:
                if guard:guard()
                size=path.stat().st_size;path.unlink();removed+=1;removed_bytes+=size
            return {'removed_files':removed,'removed_bytes':removed_bytes,'retained_dates':sorted(index['days']),'anchor_date':index.get('anchor_date'),'window_trading_days':WINDOW}


def _context(day=None, *, market=None):
    from .market_service import context
    return context(day, market=market)


def _coverage(index,calendar,target,selected):
    expected=window(calendar,target);retained=sorted(index.get('days',{}));missing=[day for day in expected if day not in index.get('days',{})];complete=not missing
    return {'anchor_date':index.get('anchor_date'),'retained_dates':retained,'expected_dates':expected,'missing_dates':missing,'retained_trading_days':WINDOW,
            'complete':complete,'can_backfill':selected==target and not complete,'available':bool(retained),'state':'ready' if complete else 'partial' if retained else 'unknown',
            'reason':'请切换至最新行情日，再补齐当前30个交易日窗口' if selected!=target else '最近30个已发布市场交易日的龙虎榜响应已留存；来源统计口径与未披露字段仍可能未知' if complete else '窗口内尚有龙虎榜响应缺口，可手动补齐；旧日期不重设滚动锚',
            'failures':list(index.get('failures',{}).values()),'latest_market_date':target}


def _hot_view(board):
    projected = project_hot_statistics(board)
    labels = [{'name': group['name'], 'stock_count_by_range': {
        str(period): len({row['instrument_id'] for row in group['rows']
                          if row.get('instrument_id') and row['range_days'] == period})
        for period in (1, 3)}} for group in projected['items']]
    return {'board': projected, 'labels': labels}


def read_view(day=None, *, market=None):
    market,status,day=_context(day,market=market);store=Store();reason=None
    with supplement_access():
        try:index=store.index();record=store.day(index,day)
        except (OSError,ValueError,TypeError,KeyError):index={'days':{},'failures':{},'anchor_date':None};record=None;reason='龙虎榜本地索引或逐日对象校验失败，保留未知'
        coverage=_coverage(index,market.calendar(),status['data_end'],day)
    if record is None:
        reason=reason or index.get('failures',{}).get(day,{}).get('reason') or '该日龙虎榜尚未留存；请在最新行情日手动补齐最近30个交易日'
        return {'date':day,'snapshot_id':status['snapshot_id'],'state':'unknown','availability':{'available':False,'reason':reason},'boards':{},'labels':[],'coverage':coverage,'warnings':[reason]}
    from app.services.pane_read_cache import read_block

    proof = index['days'][day].get('sha256')
    hot = read_block('dragon-tiger-labels', day, proof,
                     lambda: _hot_view(record['boards']['hot_money']), market=market)
    boards = {**record['boards'], 'hot_money': hot['board']}
    labels = hot['labels']
    warnings=list(dict.fromkeys([*record.get('warnings',[]),*boards['hot_money'].get('warnings',[])]))
    return {'date':day,'snapshot_id':status['snapshot_id'],'collection_snapshot_id':record.get('snapshot_id'),'state':'partial' if warnings else 'ready',
        'availability':{'available':True,'reason':None,'sources':['fuyao','eastmoney'],'date':day},'boards':boards,'labels':labels,'coverage':coverage,'warnings':warnings}


def read_seats(symbol,day=None,range_days=1, *, market=None):
    if not isinstance(symbol,str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',symbol):raise HTTPException(422,'请选择明确的A股代码')
    market,status,day=_context(day,market=market)
    identity=next((row for row in market.instruments(query=symbol,limit=10000) if row.get('instrument_id')==symbol and row.get('kind')=='stock'),None)
    if identity is None:raise HTTPException(422,'龙虎榜席位仅用于名录已核验A股，未知代码不按股票猜测')
    with supplement_access():
        try:index=Store().index();record=Store().day(index,day)
        except (OSError,ValueError,TypeError,KeyError):record=None
    base={'date':day,'snapshot_id':status['snapshot_id'],'instrument_id':symbol,'name':identity.get('name'),'range_days':range_days}
    if record is None:return {**base,'state':'unknown','records':[],'availability':{'available':False,'reason':'该日龙虎榜尚未留存或对象校验失败'}}
    seats=project_seat_ranges(record['seats']);item=seats['instruments'].get(symbol);records=item['periods'][str(range_days)] if item else []
    known_in_stock=any(row['instrument_id']==symbol and row['range_days']==range_days for row in record['boards']['all']['items'])
    unclassified_count=len(item['periods'].get('unknown',[])) if item else 0
    other_range=unclassified_count or any(row['instrument_id']==symbol and row['range_days'] is None for row in record['boards']['all']['items'])
    reason=None if records else '该股票另有累计或其他区间披露，当前单日/三日未取得对应席位；原始记录已留存，未混入当前榜' if other_range else '该股票区间在已留存榜单中没有对应席位，保持未知' if known_in_stock else '该股票未出现在该日该区间的已留存榜单'
    for row in records:
        reconciliation=row.get('reconciliation',{})
        if not reconciliation.get('matched') and not reconciliation.get('reason'):
            reconciliation['reason']='席位披露汇总与扶摇股票区间汇总存在差异；分别保留来源原值'
    return {**base,'state':'ready' if records or not (known_in_stock or other_range) else 'partial','records':records,'unclassified_record_count':unclassified_count,
            'availability':{'available':bool(records),'reason':reason,'source':'eastmoney'},'warnings':seats.get('warnings',[])}


def create_job(payload):
    if not isinstance(payload,dict) or set(payload)-{'date','request_key','backfill'}:raise HTTPException(422,'龙虎榜任务只接受日期、请求标识与补齐范围')
    backfill=payload.get('backfill',True)
    if not isinstance(backfill,bool):raise HTTPException(422,'backfill须为布尔')
    key=payload.get('request_key')
    if key is not None and (not isinstance(key,str) or not key.strip() or len(key)>200):raise HTTPException(422,'请求标识须为1至200字符')
    if key:
        with connect() as connection:old=connection.execute('SELECT job_id,kind,target_date,parameters_json FROM job WHERE request_key=?',(key,)).fetchone()
        if old:
            frozen=json.loads(old['parameters_json'])
            if old['kind']!='source_check' or frozen.get('operation')!='dragon_tiger' or frozen.get('backfill')!=backfill or payload.get('date') not in (None,old['target_date']):raise HTTPException(409,'该请求标识已用于不同任务')
            return tasks.task_view(old['job_id'])
    market,status,target=_context(payload.get('date'))
    if target!=status['data_end']:raise HTTPException(422,'龙虎榜补齐只能以最新已发布行情日为锚；历史日期仅供查看')
    dates=window(market.calendar(),target) if backfill else [target]
    with supplement_access():index=Store().index()
    if all(day in index.get('days',{}) for day in dates):raise HTTPException(409,'所选龙虎榜范围已成功留存，无需重复采集')
    from .source_service import client
    if not client('fuyao').key:raise HTTPException(422,'请先配置扶摇Key，再手动补齐龙虎榜')
    return tasks.create_task('source_check',{'operation':'dragon_tiger','dragon_tiger_policy':VERSION,'source_id':'fuyao','backfill':backfill,
        'dates':dates,'market_snapshot_id':status['snapshot_id'],'window_trading_days':WINDOW},target_date=target,request_key=key)


def collect_for_market(market,target,*,dates=None,backfill=False,progress=None,guard=None,job_id=None,adapter=None,public=None):
    status=market.status();calendar=market.calendar();allowed=window(calendar,target)
    dates=list(dates if dates is not None else allowed if backfill else [target])
    if target!=status['data_end'] or not dates or len(dates)>WINDOW or dates!=sorted(set(dates)) or any(day not in allowed for day in dates):raise ValueError('龙虎榜冻结日期必须属于最新行情30个市场交易日窗口')
    from .source_service import client
    from .sync_service import safe_payload
    store=Store();master=market.instruments(limit=10000);adapter=adapter or client('fuyao')
    statistics={'planned_days':len(dates),'accepted_days':0,'reused_days':0,'cached_components':0,'requested_components':0,'failures':[],'window_trading_days':WINDOW}
    owned=public is None;public=public or DragonTigerPublicClient(guard=guard)
    def check():
        if guard:guard()
    def tick(day,stage):
        check()
        if progress:progress('同步龙虎榜',min(98,int((statistics['accepted_days']+len(statistics['failures']))/len(dates)*98)),day+' · '+stage,dict(statistics))
    def component(capability,day,fetch,validate):
        check()
        def request():
            statistics['requested_components']+=1
            return _sanitize(safe_payload(fetch()),getattr(adapter,'key',None))
        result,pointer,cached=store.part(capability,day,request,validate)
        if cached:statistics['cached_components']+=1
        return result,pointer
    blocked=None;consecutive_failures=0
    try:
        for day in dates:
            tick(day,'核验本地批次')
            try:
                with supplement_access():existing=store.day(store.index(),day)
                if existing is not None:
                    statistics['accepted_days']+=1;statistics['reused_days']+=1;consecutive_failures=0;continue
                if blocked is not None:raise ProviderError(blocked)
                boards={};refs=[]
                for board_type in ('all','org','hot_money'):
                    tick(day,{'all':'全部榜','org':'机构榜','hot_money':'游资榜'}[board_type])
                    raw,pointer=component('fuyao-'+board_type,day,lambda b=board_type:adapter.request(FUYAO_PATH,{'date':day,'board_type':b}),lambda p,b=board_type:normalize_board(p,day,b,master))
                    boards[board_type]=normalize_board(raw,day,board_type,master);refs.append(pointer)
                # Hot-money metadata has an unverified upstream count basis.
                # Only actual empty labels together with its zero metadata and
                # the two verified empty stock boards may corroborate emptiness.
                empty=all(board['source_count']==0 and board['source_stock_count']==0 for board in boards.values()) and not boards['hot_money']['items'];reports={}
                for kind in ('buy','sell','disclosures'):
                    tick(day,'买卖席位 · '+kind)
                    def valid(report,k=kind):
                        if not isinstance(report,dict) or report.get('date')!=day or report.get('kind')!=k or not isinstance(report.get('rows'),list):raise ValueError('龙虎榜公开批次日期或类型不一致')
                        if any(str(row.get('TRADE_DATE',''))[:10]!=day for row in report['rows']):raise ValueError('龙虎榜公开批次实际日期不一致')
                    raw,pointer=component('eastmoney-'+kind,day,lambda k=kind:public.report(day,k,allow_empty=empty),valid)
                    reports[kind]=raw;refs.append(pointer)
                seats=normalize_seats(reports,day,master,boards['all'])
                record={'version':VERSION,'accepted':True,'date':day,'snapshot_id':status['snapshot_id'],'job_id':job_id,'captured_at_ms':now_ms(),
                        'boards':boards,'seats':seats,'source_objects':refs,
                        'warnings':[warning for board in boards.values() for warning in board.get('warnings',[])]+list(seats.get('warnings',[]))}
                check();store.accept(record,calendar,guard=guard);statistics['accepted_days']+=1;consecutive_failures=0
            except TaskError:raise
            except (ValueError,ProviderError) as error:
                reason=str(error);statistics['failures'].append({'date':day,'reason':reason});store.failure(day,reason,guard=guard);consecutive_failures+=1
                if isinstance(error,ProviderError) and (error.status in (401,403,429) or error.code in (2001,2003,4001)):
                    blocked='来源认证、权限或限流未通过；本任务后续日期停发，请修复后手动重试'
                elif consecutive_failures>=5:blocked='连续5个日期采集未完成，本任务已停止新增来源请求；缺口可手动重试'
            tick(day,'当前日期处理完成')
        if statistics['accepted_days']:
            try:statistics['retention']=store.cleanup(calendar,target,guard=guard)
            except (OSError,ValueError) as error:statistics['retention']={'status':'warning','reason':'龙虎榜已发布，清理未完成；后续手动任务可重试','error_type':type(error).__name__}
    finally:
        if owned:public.__exit__(None,None,None)
    statistics['quality']='partial' if statistics['failures'] else 'complete'
    statistics['message']='龙虎榜 '+str(statistics['accepted_days'])+'/'+str(len(dates))+' 个交易日响应已留存；仅保留30个市场交易日，来源统计与未披露字段可保持未知'
    return statistics


# The task-control exceptions must bypass collection failure handling.
TaskError=(tasks.TaskCancelled,tasks.TaskPaused)


def execute_job(job,progress=None):
    parameters=json.loads(job['parameters_json']);validate_policy(parameters.get('dragon_tiger_policy'))
    if parameters.get('dragon_tiger_policy')!=VERSION:raise ValueError('龙虎榜任务缺少已冻结的采集版本')
    if parameters.get('window_trading_days')!=WINDOW or not isinstance(parameters.get('dates'),list) or not parameters['dates'] or not isinstance(parameters.get('backfill'),bool):raise ValueError('龙虎榜任务缺少明确冻结日期或窗口')
    market=get_market();snapshot=parameters.get('market_snapshot_id')
    if market.status().get('snapshot_id')!=snapshot:raise ValueError('龙虎榜任务冻结行情代际已变化，请核验后重新提交')
    def guard():
        with connect() as connection:row=connection.execute('SELECT status,lease_owner FROM job WHERE job_id=?',(job['job_id'],)).fetchone()
        if row and row['status']=='paused':raise tasks.TaskPaused()
        if not row or row['status']!='running' or row['lease_owner']!=job['lease_owner']:raise tasks.TaskCancelled()
        if get_market().status().get('snapshot_id')!=snapshot:raise ValueError('龙虎榜任务执行中行情代际已变化')
    return collect_for_market(market,job['target_date'],dates=parameters.get('dates'),backfill=parameters.get('backfill',False),progress=progress,guard=guard,job_id=job['job_id'])
