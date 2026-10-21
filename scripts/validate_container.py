#!/usr/bin/env python3
"""Opt-in real Docker check in a unique empty project; removes only its own containers/volumes."""
import argparse,json,os,socket,subprocess,tempfile,time,urllib.request,uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def run(image='validation',report=None):
    project='shixu-check-'+uuid.uuid4().hex[:10]
    checks=[];restored_volumes=[]
    with tempfile.TemporaryDirectory(prefix='shixu-container-secret-') as temporary:
        secret=Path(temporary)/'master.key'
        import base64,secrets
        secret.write_bytes(base64.urlsafe_b64encode(secrets.token_bytes(32)));secret.chmod(0o444)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env=os.environ|{'SHIXU_MASTER_KEY_FILE':str(secret),'SHIXU_IMAGE_TAG':image,'SHIXU_HTTP_PORT':str(port)}
        base=['docker','compose','-p',project,'-f',str(ROOT/'deploy/compose.yaml')]
        def command(args):
            result=subprocess.run(args,env=env,cwd=ROOT,text=True,capture_output=True,timeout=120)
            if result.returncode:raise RuntimeError(result.stderr[-4000:] or result.stdout[-4000:])
            return result.stdout.strip()
        def compose(*args):return command(base+list(args))
        def execute(service,code):return compose('exec','-T',service,'python','-c',code)
        def check(name,ok,evidence=None):
            checks.append({'name':name,'passed':bool(ok),'evidence':evidence})
            if not ok:raise AssertionError(name)
        try:
            compose('up','-d','--no-build','api','worker')
            last=None
            for _ in range(45):
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=2) as response:
                        last=response.status
                    if last==200:break
                except Exception:pass
                time.sleep(1)
            check('health_endpoint',last==200)
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/',timeout=5) as response:document=response.read().decode()
            check('built_frontend_served', 'id="root"' in document)
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/strategies',timeout=5) as response:data=json.load(response)
            check('fresh_storage_has_no_fake_candidates',data.get('total')==0)
            versions=json.loads(execute('api',"import sqlite3,duckdb,json,os; print(json.dumps({'sqlite':sqlite3.sqlite_version,'duckdb':duckdb.__version__,'uid':os.getuid()}))"))
            check('patched_sqlite',tuple(map(int,versions['sqlite'].split('.')))>=(3,51,3),versions)
            check('non_root_api',versions['uid']==10001)
            check('secret_readable_without_copying_key',execute('api',"import os; print(os.access('/run/secrets/shixu_master',os.R_OK))")=='True')
            write_check="from pathlib import Path\ntry:\n Path('/var/lib/shixu/published/forbidden').write_text('x'); print('WRITABLE')\nexcept OSError:\n print('READ_ONLY')"
            check('api_published_is_read_only',execute('api',write_check)=='READ_ONLY')
            execute('worker',"from pathlib import Path; Path('/var/lib/shixu/published/container-check.txt').write_text('isolated-check')")
            check('shared_immutable_reader_path',execute('api',"from pathlib import Path; print(Path('/var/lib/shixu/published/container-check.txt').read_text())")=='isolated-check')
            execute('worker',"from pathlib import Path; Path('/var/lib/shixu/published/container-check.txt').unlink(); Path('/var/lib/shixu/work/worker-private-check').write_text('private')")
            check('worker_work_not_shared_to_api',execute('api',"from pathlib import Path; print(Path('/var/lib/shixu/work/worker-private-check').exists())")=='False')
            execute('api',"from app.core import set_setting; set_setting('container-check',{'ok':True})")
            check('sqlite_business_shared_writable',json.loads(execute('worker',"import json; from app.core import setting; print(json.dumps(setting('container-check')))"))=={'ok':True})
            ids=compose('ps','-q','api','worker').splitlines()
            mounts=json.loads(command(['docker','inspect',*ids]))
            named=[m['Name'] for container in mounts for m in container['Mounts'] if m['Type']=='volume']
            check('all_volume_names_scoped_to_test_project',all(name.startswith(project+'_') for name in named),named)
            from docker_archive import backup_project,restore_project,inspect_volume
            exported=Path(temporary)/'exported-backup'
            saved=backup_project(project,exported,'shixu-workbench:'+image)
            check('docker_online_backup_without_master_key',saved['master_key_included'] is False)
            restored_project=project+'-restored'
            imported=restore_project(exported,restored_project,'shixu-workbench:'+image)
            restored_volumes=imported['volumes']
            check('docker_restore_new_project_only',imported['services_started'] is False and all(name.startswith(restored_project+'_') for name in restored_volumes))
            restored_value=command(['docker','run','--rm','-v',restored_project+'_business:/var/lib/shixu/business:ro','shixu-workbench:'+image,'python','-c',"import sqlite3; c=sqlite3.connect('file:/var/lib/shixu/business/app.sqlite3?mode=ro',uri=True); print(c.execute(\"SELECT value_json FROM platform_setting WHERE key='container-check'\").fetchone()[0])"])
            check('restored_named_volume_data_matches',json.loads(restored_value)=={'ok':True})
            result={'project':project,'image':'shixu-workbench:'+image,'checks':checks,'passed':sum(x['passed'] for x in checks),'total':len(checks),'versions':versions,'cleanup':'own compose project only'}
        except BaseException as exc:
            result={'project':project,'checks':checks,'passed':sum(x['passed'] for x in checks),'total':len(checks),'error':str(exc)}
            try:result['logs']=compose('logs','--tail','20')
            except Exception:pass
            raise
        finally:
            compose('down','-v','--remove-orphans')
            for volume in restored_volumes:
                details=json.loads(command(['docker','volume','inspect',volume]))[0]
                if (details.get('Labels') or {}).get('com.docker.compose.project')==project+'-restored':command(['docker','volume','rm',volume])
            if 'result' in locals() and report:Path(report).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--image-tag',default='validation');parser.add_argument('--report',default=str(ROOT/'deploy/container-checks.json'));args=parser.parse_args()
    result=run(args.image_tag,args.report);print(json.dumps(result,ensure_ascii=False,indent=2))
