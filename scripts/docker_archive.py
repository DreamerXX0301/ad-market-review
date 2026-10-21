#!/usr/bin/env python3
"""Export/import this Compose project's five labelled volumes without starting services.

The short-lived helper mounts no master-key secret. Restore always creates new volumes.
"""
from __future__ import annotations
import argparse,json,os,re,subprocess,tempfile,uuid
from pathlib import Path
from backup import perform_backup
from restore import verify_backup,perform_restore
CATEGORIES=('business','published','raw','work','credentials')

def command(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=300)
    if result.returncode:raise RuntimeError(result.stderr[-3000:] or result.stdout[-3000:])
    return result.stdout.strip()
def project_name(value):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,48}',value):raise ValueError('Use a lowercase Compose project name (maximum49characters)')
    return value

def inspect_volume(name):
    result=subprocess.run(['docker','volume','inspect',name],capture_output=True,text=True)
    if result.returncode:return None
    return json.loads(result.stdout)[0]

def require_source_volumes(project):
    result={}
    for category in CATEGORIES:
        name=project+'_'+category;info=inspect_volume(name)
        if not info or (info.get('Labels') or {}).get('com.docker.compose.project')!=project or (info.get('Labels') or {}).get('com.docker.compose.volume')!=category:raise ValueError('Volume identity mismatch: '+name)
        result[category]=name
    return result

def backup_project(project,output,image):
    project=project_name(project);volumes=require_source_volumes(project);output=Path(output).expanduser().resolve()
    if output.exists():raise ValueError('Backup output must not exist')
    output.parent.mkdir(parents=True,exist_ok=True)
    args=['docker','run','--rm','--user','0:0','-v',str(output.parent)+':/export']
    for category in ('business','published','credentials'):args+=['-v',volumes[category]+':/var/lib/shixu/'+category+('' if category=='business' else ':ro')]
    code='''import json,os,sys
sys.path.insert(0,'/app/scripts')
from pathlib import Path
from backup import perform_backup
result=perform_backup('/var/lib/shixu',sys.argv[1])
root=Path(sys.argv[1]);manifest=root/'backup-manifest.json'
data=json.loads(manifest.read_text());data['source_compose_project']=sys.argv[2];manifest.write_text(json.dumps(data,ensure_ascii=False,indent=2))
for p in [root,*root.rglob('*')]:os.chown(p,int(sys.argv[3]),int(sys.argv[4]))
print(json.dumps(result,ensure_ascii=False))'''
    result=json.loads(command(args+[image,'python','-c',code,'/export/'+output.name,project,str(os.getuid()),str(os.getgid())]));verify_backup(output)
    return result|{'output':str(output),'source_compose_project':project}

def restore_project(bundle,project,image):
    project=project_name(project);manifest=verify_backup(bundle)
    if manifest.get('source_compose_project')==project:raise ValueError('Restore project must differ from source project')
    for category in CATEGORIES:
        if inspect_volume(project+'_'+category):raise ValueError('Target project volume already exists; choose a new project')
    existing=command(['docker','ps','-aq','--filter','label=com.docker.compose.project='+project])
    if existing:raise ValueError('Target project already has containers')
    token=uuid.uuid4().hex;created=[]
    with tempfile.TemporaryDirectory(prefix='shixu-volume-restore-') as temporary:
        staged=Path(temporary)/'data';result=perform_restore(bundle,staged)
        try:
            for category in CATEGORIES:
                name=project+'_'+category
                command(['docker','volume','create','--label','com.docker.compose.project='+project,'--label','com.docker.compose.volume='+category,'--label','com.shixu.restore_id='+token,name])
                info=inspect_volume(name)
                if (info.get('Labels') or {}).get('com.shixu.restore_id')!=token:raise ValueError('Concurrent volume ownership mismatch')
                created.append(name)
            args=['docker','run','--rm','--user','0:0','-v',str(staged)+':/restore:ro']
            for category in CATEGORIES:args+=['-v',project+'_'+category+':/target/'+category]
            code='''from pathlib import Path
import os,shutil
for name in ('business','published','raw','work','credentials'):
 source=Path('/restore')/name;dest=Path('/target')/name
 if source.exists():shutil.copytree(source,dest,dirs_exist_ok=True)
 for p in [dest,*dest.rglob('*')]:os.chown(p,10001,10001)
 dest.chmod(0o700 if name in ('business','credentials') else 0o750)
print('Restored labelled data volumes; no services started')'''
            command(args+[image,'python','-c',code])
            return {**result,'target':None,'compose_project':project,'volumes':created,'services_started':False,'master_key_restored':False}
        except BaseException:
            for name in created:
                info=inspect_volume(name)
                if info and (info.get('Labels') or {}).get('com.shixu.restore_id')==token:command(['docker','volume','rm',name])
            raise

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--image',default='shixu-workbench:local');sub=parser.add_subparsers(dest='mode',required=True)
    backup=sub.add_parser('backup');backup.add_argument('--project',required=True);backup.add_argument('--output',required=True)
    restore=sub.add_parser('restore');restore.add_argument('bundle');restore.add_argument('--new-project',required=True)
    args=parser.parse_args();result=backup_project(args.project,args.output,args.image) if args.mode=='backup' else restore_project(args.bundle,args.new_project,args.image)
    print(json.dumps(result,ensure_ascii=False,indent=2))
