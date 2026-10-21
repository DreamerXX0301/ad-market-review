"""Opt-in live free-source acceptance in a NEW isolated data directory.

Uses existing encrypted source credentials via explicit environment paths. Never
prints credentials, signed URLs, remote bodies or changes the main workspace.
"""
from pathlib import Path
import argparse
import json
import os
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',required=True)
    parser.add_argument('--initial-end',required=True)
    parser.add_argument('--wait-task')
    args=parser.parse_args()
    target=Path(args.target).resolve()
    if target.exists():raise SystemExit('验收目标必须为不存在的新目录，避免覆盖研究数据')
    if args.wait_task:
        import httpx
        deadline=time.monotonic()+1800
        while time.monotonic()<deadline:
            r=httpx.get('http://127.0.0.1:8788/api/v1/tasks/'+args.wait_task,timeout=20)
            r.raise_for_status();state=r.json()['status']
            if state in ('succeeded','partial','no_change'):break
            if state in ('failed','cancelled','paused'):raise SystemExit('前置任务未完成，不叠加取数负载')
            time.sleep(3)
        else:raise SystemExit('前置任务仍在运行，验收未启动')
    os.environ['SHIXU_DATA_DIR']=str(target)
    from app.core import ensure_ready,business_date,get_market,atomic_json,connect
    from app.services import task_service as tasks
    from app.services.sync_service import execute_sync
    from app.cli import validate
    ensure_ready();evidence={'isolated_directory':str(target),'runs':[],'checks':{}}
    def execute(kind,day=None):
        task=tasks.create_task(kind,{'source_id':'fuyao','window':'near-three-years'},day)
        claimed=tasks.claim_next('live-acceptance')
        if not claimed or claimed['job_id']!=task['task_id']:raise RuntimeError('验收任务未独占领取')
        started=time.monotonic()
        try:
            result=execute_sync(task['task_id'],lambda stage,pct:tasks.progress(task['task_id'],stage,pct))
            status=get_market().status();task=tasks.task_view(task['task_id'])
            summary={'kind':kind,'requested_end':day,'job_id':task['task_id'],'status':task['status'],'seconds':round(time.monotonic()-started,2),'data_begin':status.get('data_begin'),'data_end':status.get('data_end'),'row_count':status.get('rows'),'symbol_count':status.get('symbols'),'generation':status.get('snapshot_id'),'coverage':result.get('coverage'),'checksums':validate()['ok']}
            evidence['runs'].append(summary);print(json.dumps(summary,ensure_ascii=False),flush=True)
            return summary
        except BaseException as error:
            tasks.finish(task['task_id'],'failed','真实取数验收未完成',type(error).__name__)
            raise
        finally:atomic_json(target/'acceptance-evidence.json',evidence)
    first=execute('initialise',args.initial_end)
    second=execute('increment')
    repeated=execute('increment')
    evidence['checks']={'initial_target_exact':first['data_end']==args.initial_end,'increment_advanced':second['data_end']>first['data_end'],'new_rows_positive':second['row_count']>first['row_count'],'repeat_no_change':repeated['status']=='no_change','repeat_same_generation':second['generation']==repeated['generation'],'repeat_same_rows':second['row_count']==repeated['row_count'],'all_file_checksums':all(r['checksums'] for r in evidence['runs'])}
    atomic_json(target/'acceptance-evidence.json',evidence)
    print(json.dumps({'checks':evidence['checks'],'ok':all(evidence['checks'].values())},ensure_ascii=False),flush=True)
    if not all(evidence['checks'].values()):raise SystemExit(1)

if __name__=='__main__':main()
