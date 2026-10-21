#!/usr/bin/env python3
"""Export one audited Git commit as a reproducible, source-only local bundle.

Only immutable Git blobs are read. No working-tree files, application imports,
credentials, Docker operations, publishing or network access are involved.
"""
from __future__ import annotations

import argparse
import gzip
from hashlib import sha256
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile

FORMAT = 'ad-market-review-source-v1'
MAX_FILE_BYTES = 16*1024*1024
MAX_SOURCE_BYTES = 128*1024*1024
MAX_FILES = 10000
_SEMVER = re.compile(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?')
_ROOT_FILES = {'.dockerignore','.gitignore','.gitattributes','.editorconfig',
               'README.md','CHANGELOG.md','AGENTS.md','LICENSE','LICENSE.md','VERSION',
               'SECURITY.md','CONTRIBUTING.md'}
_SOURCE_DIRS = {'backend','frontend','scripts','deploy','contracts','docs','.github'}
_FORBIDDEN_PARTS = {'.git','.idea','.venv','venv','node_modules','__pycache__',
    '.pytest_cache','.ruff_cache','dist','coverage','playwright-report',
    'backups','secrets','credentials','raw','work','reports'}
_FORBIDDEN_SUFFIXES = {'.enc','.key','.pem','.p12','.pfx','.sqlite','.sqlite3',
    '.db','.duckdb','.parquet','.arrow','.feather','.log','.pyc','.pyo','.zip',
    '.gz','.tgz','.tar','.7z','.DS_Store'}
_PRIVATE_PATH = re.compile(r'(?:/[U]sers/|/[h]ome/)[A-Za-z0-9_.-]+/|[A-Za-z]:\\[U]sers\\[A-Za-z0-9_.-]+\\')
_SECRET = re.compile(r'(?:tk_[0-9a-fA-F]{24,}|sk-fuyao-[A-Za-z0-9_-]{12,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')


def git(repo, *args):
    result = subprocess.run(['git','--no-replace-objects','-C',str(repo),*args],
        capture_output=True,check=False)
    if result.returncode: raise ValueError('Git提交或对象读取失败；未导出工作区内容')
    return result.stdout


def validate_path(name, mode='100644'):
    if (not isinstance(name,str) or not name or '\\' in name
            or any(ord(c)<32 or ord(c)==127 for c in name)):
        raise ValueError('源码树包含不安全文件名')
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or path.as_posix()!=name:
        raise ValueError('源码树包含越界路径')
    if mode not in ('100644','100755'):
        raise ValueError('源码包不接受符号链接、子模块或特殊对象：'+name)
    parts = path.parts
    if any(part in _FORBIDDEN_PARTS for part in parts) or parts[0]=='data':
        raise ValueError('源码树包含运行、密钥或依赖目录：'+name)
    # The data-center feature is code, not the root runtime data directory.
    if len(parts)==1:
        if name not in _ROOT_FILES: raise ValueError('未审核的根目录文件：'+name)
    elif parts[0] not in _SOURCE_DIRS: raise ValueError('未审核的源码目录：'+name)
    if parts[0]=='docs' and len(parts)>1 and parts[1] in ('validation','screenshots'):
        raise ValueError('历史运行证据不进入公开源码包：'+name)
    if path.name.startswith('.env') and name!='deploy/.env.example':
        raise ValueError('真实环境文件不进入源码包：'+name)
    if path.suffix.lower() in _FORBIDDEN_SUFFIXES or path.name=='.DS_Store':
        raise ValueError('运行数据、秘密或预构建包不进入源码包：'+name)
    if (parts[0]=='deploy' and path.suffix=='.json') or name=='frontend/api-contract-checks.json':
        raise ValueError('历史验收结果不进入公开源码包：'+name)
    return name


def validate_content(name, value):
    if len(value)>MAX_FILE_BYTES: raise ValueError('源码文件超过保护上限：'+name)
    try: text=value.decode('utf-8')
    except UnicodeDecodeError:
        # Assets must be deliberately added to a later reviewed policy rather
        # than silently allowing binary databases or private screenshots.
        raise ValueError('源码树包含未审核的二进制文件：'+name) from None
    if '\x00' in text: raise ValueError('源码树包含二进制内容：'+name)
    if _PRIVATE_PATH.search(text): raise ValueError('源码包含个人主目录绝对路径：'+name)
    if _SECRET.search(text): raise ValueError('源码疑似包含真实凭据或私钥：'+name)


def inspect_commit(repo, commit):
    repo=Path(repo).expanduser().resolve()
    if not isinstance(commit,str) or not re.fullmatch(r'[0-9a-f]{40}',commit):
        raise ValueError('必须指定完整40位小写Git提交SHA')
    resolved=git(repo,'rev-parse','--verify',commit+'^{commit}').decode().strip()
    if resolved!=commit: raise ValueError('提交未精确解析为给定SHA')
    entries=[];total=0
    for entry in git(repo,'ls-tree','-r','-z','--full-tree',commit).split(b'\x00'):
        if not entry: continue
        metadata, raw_name=entry.split(b'\t',1)
        mode,kind,oid=metadata.decode('ascii').split()
        try: name=raw_name.decode('utf-8')
        except UnicodeDecodeError: raise ValueError('源码文件名不是UTF-8') from None
        validate_path(name,mode)
        if kind!='blob': raise ValueError('源码树包含非普通Git blob')
        size=int(git(repo,'cat-file','-s',oid))
        total+=size
        if size>MAX_FILE_BYTES or total>MAX_SOURCE_BYTES or len(entries)>=MAX_FILES:
            raise ValueError('源码包超过文件数量或大小保护上限')
        value=git(repo,'cat-file','blob',oid)
        if len(value)!=size: raise ValueError('Git对象大小不一致')
        validate_content(name,value)
        entries.append({'path':name,'mode':mode,'git_blob':oid,'bytes':size,
                        'sha256':sha256(value).hexdigest(),'content':value})
    entries.sort(key=lambda r:r['path'])
    by_path={row['path']:row for row in entries}
    if 'VERSION' not in by_path: raise ValueError('指定提交缺少项目VERSION')
    version=by_path['VERSION']['content'].decode().strip()
    if not _SEMVER.fullmatch(version): raise ValueError('项目VERSION必须是不带v的语义版本')
    if 'frontend/package.json' in by_path:
        if json.loads(by_path['frontend/package.json']['content']).get('version')!=version:
            raise ValueError('前端版本与VERSION不一致')
    if 'backend/pyproject.toml' in by_path:
        import tomllib
        if tomllib.loads(by_path['backend/pyproject.toml']['content'].decode()).get('project',{}).get('version')!=version:
            raise ValueError('后端版本与VERSION不一致')
    timestamp=int(git(repo,'show','-s','--format=%ct',commit))
    if timestamp<0: raise ValueError('提交时间无效')
    return {'commit':commit,'version':version,'timestamp':timestamp,'entries':entries,'total_bytes':total}


def package_release(repo, commit, output):
    """Create a new local bundle; reject unsafe tree before output appears."""
    plan=inspect_commit(repo,commit)
    output=Path(output).expanduser().resolve()
    if output.exists(): raise ValueError('发行输出目录必须不存在，不覆盖旧包')
    output.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.shixu-source-',dir=output.parent))
    try:
        filename='ad-market-review-'+plan['version']+'-'+commit[:12]+'.tar.gz'
        archive=stage/filename;prefix='ad-market-review-'+plan['version']+'/'
        with archive.open('wb') as target:
            with gzip.GzipFile(fileobj=target,mode='wb',filename='',mtime=0,compresslevel=9) as compressed:
                with tarfile.open(fileobj=compressed,mode='w',format=tarfile.PAX_FORMAT) as writer:
                    for row in plan['entries']:
                        member=tarfile.TarInfo(prefix+row['path'])
                        member.size=row['bytes'];member.mode=0o755 if row['mode']=='100755' else 0o644
                        member.uid=member.gid=0;member.uname=member.gname='';member.mtime=plan['timestamp']
                        writer.addfile(member,io.BytesIO(row['content']))
        files=[{k:v for k,v in row.items() if k!='content'} for row in plan['entries']]
        checksum=sha256(archive.read_bytes()).hexdigest()
        manifest={'format':FORMAT,'version':plan['version'],'git_commit':commit,
            'commit_timestamp':plan['timestamp'],'source_only':True,'working_tree_used':False,
            'archive':{'file':filename,'sha256':checksum,'bytes':archive.stat().st_size},
            'migration_sha256':{r['path']:r['sha256'] for r in files
                if re.fullmatch(r'backend/app/migrations/\d+_[^/]+\.sql',r['path'])},
            'file_count':len(files),'source_bytes':plan['total_bytes'],'files':files,
            'test_results_included':False,'image_published':False}
        (stage/'source-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
        (stage/'SHA256SUMS').write_text(checksum+'  '+filename+'\n')
        output.mkdir(mode=0o755)
        for path in stage.iterdir(): os.replace(path,output/path.name)
        return {'output':str(output),'archive':filename,'sha256':checksum,
                'version':plan['version'],'git_commit':commit,'file_count':len(files)}
    finally:
        for path in stage.iterdir(): path.unlink()
        stage.rmdir()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--commit',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    print(json.dumps(package_release(args.repo,args.commit,args.output),ensure_ascii=False,indent=2))


if __name__=='__main__': main()
