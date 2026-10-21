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
import zlib

FORMAT = 'ad-market-review-source-v1'
MAX_FILE_BYTES = 16*1024*1024
MAX_SOURCE_BYTES = 128*1024*1024
MAX_FILES = 10000
MAX_PUBLIC_PNG_BYTES = 2*1024*1024
MAX_PUBLIC_JPEG_BYTES = 2*1024*1024
MAX_PNG_DECODED_BYTES = 64*1024*1024
_PUBLIC_PNG = re.compile(r'docs/images/[a-z0-9][a-z0-9_-]{0,63}\.png\Z')
_PUBLIC_JPEG = re.compile(r'docs/images/[a-z0-9][a-z0-9_-]{0,63}\.jpg\Z')
_PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
_PNG_CHUNKS = {b'IHDR',b'IDAT',b'IEND',b'sRGB',b'gAMA',b'pHYs',b'cHRM'}
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
    if (parts[0]=='docs' and len(parts)>1 and parts[1]=='images'
            and not (_PUBLIC_PNG.fullmatch(name) or _PUBLIC_JPEG.fullmatch(name))):
        raise ValueError('公开演示图只接受docs/images下安全命名的PNG或JPG：'+name)
    if path.name.startswith('.env') and name!='deploy/.env.example':
        raise ValueError('真实环境文件不进入源码包：'+name)
    if path.suffix.lower() in _FORBIDDEN_SUFFIXES or path.name=='.DS_Store':
        raise ValueError('运行数据、秘密或预构建包不进入源码包：'+name)
    if (parts[0]=='deploy' and path.suffix=='.json') or name=='frontend/api-contract-checks.json':
        raise ValueError('历史验收结果不进入公开源码包：'+name)
    return name


def validate_public_png(name, value):
    """Allow only reviewed, user-authorized public UI demos, never QA captures.

    A safe container cannot establish that pixels are public: the release owner
    must still review each image. Reject metadata, embedded/trailing payloads,
    animation, palette images and anything outside this narrow PNG namespace.
    """
    def reject():
        raise ValueError('公开演示PNG结构、元信息或大小不符合发布规则：'+name)

    if len(value)>MAX_PUBLIC_PNG_BYTES or not value.startswith(_PNG_SIGNATURE):reject()
    offset=len(_PNG_SIGNATURE);seen=set();compressed=[];header=None;ended=False
    while offset<len(value):
        if offset+12>len(value):reject()
        size=int.from_bytes(value[offset:offset+4],'big');kind=value[offset+4:offset+8]
        end=offset+12+size
        if end>len(value) or kind not in _PNG_CHUNKS:reject()
        data=value[offset+8:end-4]
        if zlib.crc32(kind+data)&0xffffffff!=int.from_bytes(value[end-4:end],'big'):reject()
        if not seen and kind!=b'IHDR':reject()
        if kind!=b'IDAT' and kind in seen:reject()
        if kind==b'IHDR':
            if size!=13:reject()
            width=int.from_bytes(data[:4],'big');height=int.from_bytes(data[4:8],'big')
            depth,color,compression,filtering,interlace=data[8:]
            channels={0:1,2:3,4:2,6:4}.get(color)
            if (not channels or not 0<width<=8192 or not 0<height<=8192
                    or depth not in ((1,2,4,8,16) if color==0 else (8,16))
                    or compression or filtering or interlace):reject()
            row_bytes=(width*channels*depth+7)//8
            decoded_bytes=(row_bytes+1)*height
            if decoded_bytes>MAX_PNG_DECODED_BYTES:reject()
            header=(row_bytes,height,decoded_bytes)
        elif kind==b'IDAT':
            compressed.append(data)
        elif kind==b'IEND':
            if size or not compressed or end!=len(value):reject()
            ended=True
        else:
            if compressed:reject()
            if (kind==b'sRGB' and (size!=1 or data[0]>3)
                    or kind==b'gAMA' and (size!=4 or not int.from_bytes(data,'big'))
                    or kind==b'pHYs' and (size!=9 or data[-1]>1)
                    or kind==b'cHRM' and size!=32):reject()
        seen.add(kind);offset=end
    if not ended or header is None:reject()
    row_bytes,height,decoded_bytes=header
    try:
        decoder=zlib.decompressobj();pixels=decoder.decompress(b''.join(compressed),decoded_bytes+1)
    except zlib.error:reject()
    if (len(pixels)!=decoded_bytes or not decoder.eof or decoder.unused_data
            or decoder.unconsumed_tail or any(pixels[row*(row_bytes+1)]>4 for row in range(height))):reject()


def validate_public_jpeg(name, value):
    """Check reviewed public JPEG screenshots without permitting metadata.

    Only standard JFIF, 8-bit grayscale/RGB frames and their coding tables/scans
    are accepted. Pixel privacy remains the release owner's visual review.
    """
    def reject():
        raise ValueError('公开演示JPEG结构、元信息或大小不符合发布规则：'+name)

    if len(value)>MAX_PUBLIC_JPEG_BYTES or not value.startswith(b'\xff\xd8'):reject()
    offset=2;jfif=False;frame=None;scans=0;quantizers=set();huffman=set();restart=0
    while offset<len(value):
        if value[offset]!=0xff:reject()
        while offset<len(value) and value[offset]==0xff:offset+=1
        if offset>=len(value):reject()
        marker=value[offset];offset+=1
        if marker==0xd9:
            if not jfif or frame is None or not scans or offset!=len(value):reject()
            return
        if marker not in (0xe0,0xdb,0xc4,0xc0,0xc2,0xdd,0xda):reject()
        if not jfif and marker!=0xe0:reject()
        if offset+2>len(value):reject()
        size=int.from_bytes(value[offset:offset+2],'big')
        if size<2 or offset+size>len(value):reject()
        data=value[offset+2:offset+size];offset+=size
        if marker==0xe0:
            if (jfif or frame is not None or scans or len(data)!=14 or data[:5]!=b'JFIF\x00'
                    or data[5]!=1 or data[6]>2 or data[7]>2 or data[12:]!=b'\x00\x00'
                    or not int.from_bytes(data[8:10],'big') or not int.from_bytes(data[10:12],'big')):reject()
            jfif=True
        elif marker==0xdb:
            cursor=0
            while cursor<len(data):
                table=data[cursor];cursor+=1;precision=table>>4
                if precision>1 or table&15>3:reject()
                count=64*(precision+1)
                if cursor+count>len(data):reject()
                values=data[cursor:cursor+count]
                if (not precision and 0 in values or precision and
                        any(values[i:i+2]==b'\x00\x00' for i in range(0,count,2))):reject()
                quantizers.add(table&15);cursor+=count
            if not data:reject()
        elif marker==0xc4:
            cursor=0
            while cursor<len(data):
                if cursor+17>len(data):reject()
                table=data[cursor];counts=data[cursor+1:cursor+17];cursor+=17
                symbols=sum(counts);available=1
                for count in counts:
                    available=available*2-count
                    if available<0:reject()
                if table>>4>1 or table&15>3 or not 0<symbols<=256 or cursor+symbols>len(data):reject()
                huffman.add(table);cursor+=symbols
            if not data:reject()
        elif marker in (0xc0,0xc2):
            if frame is not None or len(data)<6 or data[0]!=8 or data[5] not in (1,3):reject()
            height=int.from_bytes(data[1:3],'big');width=int.from_bytes(data[3:5],'big');count=data[5]
            if (len(data)!=6+3*count or not 0<width<=8192 or not 0<height<=8192
                    or width*height*count>MAX_PNG_DECODED_BYTES):reject()
            components={}
            for i in range(count):
                identity,sampling,quantizer=data[6+3*i:9+3*i]
                if (identity in components or not 1<=sampling>>4<=4
                        or not 1<=sampling&15<=4 or quantizer>3):reject()
                components[identity]=quantizer
            frame=(marker,components)
        elif marker==0xdd:
            if len(data)!=2:reject()
            restart=int.from_bytes(data,'big')
        elif marker==0xda:
            if frame is None or not data or not 1<=data[0]<=len(frame[1]) or len(data)!=4+2*data[0]:reject()
            count=data[0];selected=set();start,end,successive=data[-3:]
            if (frame[0]==0xc0 and (start,end,successive)!=(0,63,0)
                    or frame[0]==0xc2 and (not 0<=start<=end<=63 or successive>>4>13
                        or successive&15>13 or start==0 and end!=0 or start>0 and count!=1)):reject()
            for i in range(count):
                identity,tables=data[1+2*i:3+2*i]
                if identity not in frame[1] or identity in selected or tables>>4>3 or tables&15>3:reject()
                if frame[1][identity] not in quantizers:reject()
                if start==0 and tables>>4 not in huffman:reject()
                if end>0 and (0x10|(tables&15)) not in huffman:reject()
                selected.add(identity)
            scans+=1;entropy_start=offset
            while offset<len(value):
                if value[offset]!=0xff:offset+=1;continue
                marker_start=offset
                while offset<len(value) and value[offset]==0xff:offset+=1
                if offset>=len(value):reject()
                entropy_marker=value[offset]
                if entropy_marker==0x00:offset+=1;continue
                if 0xd0<=entropy_marker<=0xd7:
                    if not restart:reject()
                    offset+=1;continue
                offset=marker_start;break
            if offset==entropy_start:reject()
    reject()


def validate_content(name, value):
    if len(value)>MAX_FILE_BYTES: raise ValueError('源码文件超过保护上限：'+name)
    if _PUBLIC_PNG.fullmatch(name) or _PUBLIC_JPEG.fullmatch(name):
        # Metadata is rejected below; also retain byte-visible secret/path
        # checks even inside an otherwise permitted image container.
        visible=value.decode('latin1')
        if _PRIVATE_PATH.search(visible):raise ValueError('源码包含个人主目录绝对路径：'+name)
        if _SECRET.search(visible):raise ValueError('源码疑似包含真实凭据或私钥：'+name)
    if _PUBLIC_PNG.fullmatch(name):
        validate_public_png(name,value)
        return
    if _PUBLIC_JPEG.fullmatch(name):
        validate_public_jpeg(name,value)
        return
    try: text=value.decode('utf-8')
    except UnicodeDecodeError:
        # All other binary assets remain outside the reviewed release policy.
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
