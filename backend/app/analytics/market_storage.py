"""Immutable monthly market objects shared by lightweight generation manifests.

Only the sole worker writes objects. Readers resolve an exact generation, never
follow another head, and accept legacy single-file generations unchanged.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import date
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import stat as stat_mode
import threading

VERSION = 'market-partitions-v1'
ENCODER_VERSION = 'market-monthly-zstd-rg8192-v1'
ROW_GROUP_SIZE = 8192
OBJECT_ROOT = 'market-objects'
ROLES = ('raw-daily.parquet', 'daily.parquet')
_verified = OrderedDict()
_descriptors = OrderedDict()
_lock = threading.RLock()


@dataclass(frozen=True)
class MergePartitionProof:
    """In-process token issued by the exact old/anti-join/incoming merge.

    Never deserialize this capability from bundle metadata or an API payload.
    Both the merged file and all old raw objects bind actual SHA bytes.
    """
    output_sha256: str
    previous: tuple
    changed_months: tuple


def reusable_partitions(proof, output_sha, previous_folder, root, con, raw_relation, previous_files):
    if not isinstance(proof, MergePartitionProof) or proof.output_sha256 != output_sha:
        return {}
    folder = Path(previous_folder)
    if folder.parent.resolve() != Path(root).resolve(): return {}
    try:
        for role in ROLES:
            item=next((item for item in previous_files if item['role']==role),None)
            path=folder/manifest_name(role)
            if item is None or item['path']!=str(Path('published')/folder.name/path.name): return {}
            _verify(path,item['sha256'],item['bytes'])
        documents = {role: read_descriptor(folder/manifest_name(role)) for role in ROLES}
        raw = documents['raw-daily.parquet']
        expected = tuple(sorted((str(safe_object(root, row['object_key']).resolve()), row['sha256']) for row in raw['partitions']))
        if proof.previous != expected: return {}
        # A schema/type change falls back to canonical full encoding. In
        # particular do not retain old raw integer/decimal types under a new
        # floating source schema, even when numerically equal.
        source_schema = con.execute(f'DESCRIBE SELECT * FROM {raw_relation}').fetchall()
        old_schema = con.execute('DESCRIBE SELECT * FROM read_parquet(?)', [[str(safe_object(root,p['object_key'])) for p in raw['partitions']]]).fetchall()
        if source_schema != old_schema: return {}
        return {role: {row['month']: row for row in document['partitions'] if row['month'] not in proof.changed_months}
                for role, document in documents.items()}
    except (OSError, ValueError, KeyError, TypeError):
        # A reuse hint is optional. Corrupt/unavailable old objects never
        # substitute for the independently validated current source relation.
        return {}


def file_sha(path):
    value = sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): value.update(block)
    return value.hexdigest()


def manifest_name(role):
    if role not in ROLES: raise ValueError('Unsupported market partition role')
    return role.removesuffix('.parquet') + '.manifest.json'


def safe_object(root, key):
    root = Path(root).resolve(); relative = Path(key)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts: raise ValueError('行情对象路径越界')
    # Relative components cannot escape root. Check each actual component once;
    # repeatedly constructing every ancestor's own parents was quadratic and
    # dominated otherwise bounded per-stock reads across sixty month objects.
    target = root
    for component in relative.parts:
        target = target / component
        try:info=target.lstat()
        except FileNotFoundError:continue
        if stat_mode.S_ISLNK(info.st_mode):raise ValueError('行情对象不接受符号链接')
    return target


def _verify(path, expected, size, info=None):
    stat = info if info is not None else path.stat()
    if not stat_mode.S_ISREG(stat.st_mode) or stat.st_size != size: raise ValueError('行情分片大小与固定清单不符')
    signature = (str(path), expected, stat.st_size, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns)
    with _lock:
        if signature in _verified:
            _verified.move_to_end(signature); return
    if file_sha(path) != expected: raise ValueError('行情分片SHA与固定清单不符')
    with _lock:
        _verified[signature] = True
        while len(_verified) > 512: _verified.popitem(last=False)


def verify_immutable_file(path,expected,size):
    """Verify legacy immutable inputs with the same bounded signature cache."""
    _verify(Path(path),expected,size)


def _descriptor_structure(path):
    """Cache only fixed structure/paths; actual dependency bytes are rechecked."""
    path=Path(path);info=path.stat();root=path.parent.parent.resolve()
    signature=(str(path.absolute()),str(root),info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    with _lock:
        cached=_descriptors.get(signature)
        if cached is not None:
            _descriptors.move_to_end(signature);return cached
    document = json.loads(path.read_text())
    if document.get('version') != VERSION or document.get('role') not in ROLES: raise ValueError('行情分片清单版本或角色无效')
    parts = document.get('partitions')
    if not isinstance(parts, list) or not parts: raise ValueError('行情分片清单为空')
    seen = set(); previous = None; count = 0;paths=[];directories=set()
    for part in parts:
        if not isinstance(part, dict): raise ValueError('行情分片清单无效')
        key = part.get('object_key'); digest = part.get('sha256'); month = part.get('month')
        if not isinstance(month, str) or not re.fullmatch(r'\d{4}-\d{2}', month): raise ValueError('行情分片月份无效')
        if previous is not None and month <= previous: raise ValueError('行情分片月份重复或失序')
        previous = month
        if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest): raise ValueError('行情分片SHA无效')
        wanted = f'{OBJECT_ROOT}/{document["role"].removesuffix(".parquet")}/{digest[:2]}/{digest}.parquet'
        if key != wanted or key in seen: raise ValueError('行情分片对象路径与SHA不符')
        seen.add(key)
        if not isinstance(part.get('rows'), int) or isinstance(part['rows'], bool) or part['rows'] <= 0: raise ValueError('行情分片行数无效')
        if not isinstance(part.get('bytes'), int) or isinstance(part['bytes'], bool) or part['bytes'] <= 0: raise ValueError('行情分片字节无效')
        for field in ('data_begin', 'data_end'):
            value = part.get(field)
            if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value or value[:7] != month: raise ValueError('行情分片日期边界无效')
        if part['data_begin'] > part['data_end']: raise ValueError('行情分片日期顺序无效')
        target=root/key;paths.append(target)
        current=target.parent
        while current!=root:
            directories.add(current);current=current.parent
        count += part['rows']
    if count != document.get('rows') or parts[0]['data_begin'] != document.get('data_begin') or parts[-1]['data_end'] != document.get('data_end'): raise ValueError('行情分片汇总与清单不符')
    cached=(document,tuple(paths),tuple(directories))
    with _lock:
        _descriptors[signature]=cached
        while len(_descriptors)>64:_descriptors.popitem(last=False)
    return cached


def _descriptor_inputs(path):
    document,paths,directories=_descriptor_structure(path)
    # A warm descriptor never hides a replaced directory, symlink, changed
    # size or bytes. Each shared directory is checked once for the full scope.
    for directory in directories:
        info=directory.lstat()
        if stat_mode.S_ISLNK(info.st_mode):raise ValueError('行情对象不接受符号链接')
        if not stat_mode.S_ISDIR(info.st_mode):raise ValueError('行情对象目录无效')
    for part,target in zip(document['partitions'],paths):
        info=target.lstat()
        if stat_mode.S_ISLNK(info.st_mode):raise ValueError('行情对象不接受符号链接')
        _verify(target,part['sha256'],part['bytes'],info)
    return document,paths


def read_descriptor(path):
    """Validate one fixed manifest and its content-addressed dependency closure."""
    document,_=_descriptor_inputs(path)
    return deepcopy(document)


def parquet_input(path, *, start=None, end=None):
    """DuckDB accepts both this legacy path string and the new object-path list."""
    if isinstance(path, (list, tuple)):
        return [str(value) for value in path]
    path = Path(path)
    if path.suffix == '.parquet':
        if path.is_file(): return str(path)
        descriptor = path.with_name(manifest_name(path.name))
    else: descriptor = path
    document,paths = _descriptor_inputs(descriptor)
    # Empty date selections retain one file and rely on the caller's SQL filter
    # so DuckDB still knows the original schema instead of guessing an empty one.
    selected = [str(target) for part,target in zip(document['partitions'],paths) if (not start or part['data_end'] >= start) and (not end or part['data_begin'] <= end)]
    return selected or [str(paths[0])]


def descriptor_path(folder, role):
    legacy = Path(folder) / role
    return legacy if legacy.is_file() else Path(folder) / manifest_name(role)


def write_partitions(con, relation, role, root, stage, snapshot, *, reusable=None):
    """Canonical monthly encoding makes identical history reuse identical bytes.

    A single bounded partitioned COPY scans the input once. No object is
    overwritten; existing content must pass SHA before being reused.
    """
    root = Path(root); stage = Path(stage)
    raw = role == 'raw-daily.parquet'
    day = "strftime(to_timestamp(date_ms/1000.0) AT TIME ZONE 'Asia/Shanghai','%Y-%m-%d')" if raw else 'date'
    ordering = 'thscode,date_ms' if raw else 'instrument_id,date'
    months = con.execute(f'SELECT substr({day},1,7) AS month_key,count(*),min({day}),max({day}) FROM {relation} GROUP BY 1 ORDER BY 1').fetchall()
    parts = []; created = reused = 0
    reusable = reusable or {}
    accepted = {month: reusable[month] for month,count,begin,end in months
                if month in reusable and (reusable[month]['rows'],reusable[month]['data_begin'],reusable[month]['data_end']) == (count,begin,end)}
    dirty = [month for month, *_ in months if month not in accepted]
    temporary_root=stage/('partition-output-'+role)
    threads=con.execute("SELECT current_setting('threads')").fetchone()[0]
    # One writer gives canonical row groups/bytes across generations even when
    # the query connection normally has several execution threads.
    con.execute('SET threads=1')
    try:
        if dirty:
            parameters = {'destination': str(temporary_root)}
            if accepted:
                parameters.update({f'month_{i}': month for i,month in enumerate(dirty)})
            condition = (' WHERE substr('+day+',1,7) IN ('+','.join('$month_'+str(i) for i in range(len(dirty)))+')') if accepted else ''
            # COPY binds its destination before parameters inside the SELECT;
            # named parameters keep filtered month values unambiguous.
            con.execute(f'COPY (SELECT *,substr({day},1,7) AS market_month FROM {relation}{condition} ORDER BY market_month,{ordering}) TO $destination (FORMAT PARQUET, PARTITION_BY (market_month), COMPRESSION ZSTD, ROW_GROUP_SIZE {ROW_GROUP_SIZE})',parameters)
    finally:con.execute('SET threads='+str(threads))
    for month, count, begin, end in months:
        if month in accepted:
            part = accepted[month]
            _verify(safe_object(root,part['object_key']),part['sha256'],part['bytes'])
            parts.append(dict(part)); reused += part['bytes']
            continue
        directory=temporary_root/('market_month='+month)
        outputs=list(directory.glob('*.parquet'))
        if len(outputs)!=1:raise ValueError('月份分片写入器未生成唯一有界对象')
        temporary=outputs[0]
        digest = file_sha(temporary); size = temporary.stat().st_size
        key = f'{OBJECT_ROOT}/{role.removesuffix(".parquet")}/{digest[:2]}/{digest}.parquet'
        target = safe_object(root, key); target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            _verify(target, digest, size); temporary.unlink(); reused += size
        else:
            with temporary.open('rb') as stream: os.fsync(stream.fileno())
            # A single writer owns publication, but never replace an object even
            # if an accidental second writer races this immutable creation.
            try: os.link(temporary, target)
            except FileExistsError: _verify(target, digest, size); reused += size
            else:
                created += size
                descriptor = os.open(target.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try: os.fsync(descriptor)
                finally: os.close(descriptor)
            temporary.unlink()
        parts.append({'month': month, 'object_key': key, 'sha256': digest, 'bytes': size, 'rows': count, 'data_begin': begin, 'data_end': end})
    if temporary_root.exists(): shutil.rmtree(temporary_root)
    if not parts: raise ValueError('不能发布空行情分片')
    document = {'version': VERSION, 'role': role, 'partitions': parts, 'rows': sum(part['rows'] for part in parts), 'data_begin': parts[0]['data_begin'], 'data_end': parts[-1]['data_end']}
    output = stage / manifest_name(role)
    output.write_text(json.dumps(document, ensure_ascii=False, separators=(',', ':'), sort_keys=True))
    files = [{'role': role, 'path': str(Path('published') / snapshot / output.name), 'sha256': file_sha(output), 'bytes': output.stat().st_size}]
    prefix = role.removesuffix('.parquet') + '-partition/'
    files.extend({'role': prefix + part['month'], 'path': 'published/' + part['object_key'], 'sha256': part['sha256'], 'bytes': part['bytes']} for part in parts)
    return files, {'partition_count': len(parts), 'new_object_bytes': created, 'reused_object_bytes': reused,
                   'encoded_partition_count':len(dirty),'reused_without_encoding_count':len(accepted)}


def activity_window(end, retained_begin, *, years=5):
    day = date.fromisoformat(end)
    try: begin = day.replace(year=day.year-years)
    except ValueError: begin = day.replace(year=day.year-years, day=28)
    return {'version': 'market-active-window-v1', 'anchor': end, 'years': years, 'active_begin': max(retained_begin, begin.isoformat()),
            'active_end':end,'physical_begin':retained_begin,'physical_end':end,'retained_begin':retained_begin, 'recursive_seed_preserved': True,'automatic_collection':False,
            'protected_seed_begin':retained_begin if retained_begin<begin.isoformat() else None,'protected_seed_before':begin.isoformat() if retained_begin<begin.isoformat() else None,
            'reason': '窗口外历史作为递推指标、完整周月、历史事件及来源核验种子保留；无引用对象由独立GC回收，不截断旧指标'}

REFERENCE_VERSION='market-reference-parts-v2'


def write_reference(metadata,root,stage,snapshot):
    """Reuse stable reference fields and historical index months across versions."""
    root=Path(root);stage=Path(stage);artifacts={};created=reused=0
    def put(value):
        nonlocal created,reused
        raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        digest=sha256(raw).hexdigest();key=f'{OBJECT_ROOT}/reference/{digest[:2]}/{digest}.json'
        target=safe_object(root,key);target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():_verify(target,digest,len(raw));reused+=len(raw)
        else:
            temporary=stage/'reference-object.tmp';temporary.write_bytes(raw)
            with temporary.open('rb') as stream:os.fsync(stream.fileno())
            try:os.link(temporary,target)
            except FileExistsError:_verify(target,digest,len(raw));reused+=len(raw)
            else:
                created+=len(raw);descriptor=os.open(target.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
                try:os.fsync(descriptor)
                finally:os.close(descriptor)
            temporary.unlink()
        artifacts[key]={'role':'reference-part/'+digest,'path':'published/'+key,'sha256':digest,'bytes':len(raw)}
        return {'object_key':key,'sha256':digest,'bytes':len(raw)}
    fields={key:put(value) for key,value in metadata.items() if key not in ('indices','master')}
    master=metadata.get('master',{})
    master_columns={'keys':put(list(master)),'columns':{}}
    for field in sorted({key for row in master.values() for key in row}):
        values={symbol:row[field] for symbol,row in master.items() if field in row}
        if field=='list_date_evidence':
            groups={}
            for symbol,value in values.items():
                identity=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
                groups.setdefault(identity,{'value':value,'symbols':[]})['symbols'].append(symbol)
            master_columns['columns'][field]={'encoding':'shared-values','reference':put(list(groups.values()))}
        else:master_columns['columns'][field]={'encoding':'mapping','reference':put(values)}
    indices={}
    for symbol,rows in metadata.get('indices',{}).items():
        months={}
        for row in rows:months.setdefault(row['date'][:7],[]).append(row)
        indices[symbol]=[{'month':month,**put(values)} for month,values in sorted(months.items())]
    descriptor={'storage_version':REFERENCE_VERSION,'fields':fields,'master_columns':master_columns,'indices':indices}
    output=stage/'reference.json';output.write_text(json.dumps(descriptor,ensure_ascii=False,sort_keys=True,separators=(',',':')))
    return list(artifacts.values()),{'new_object_bytes':created,'reused_object_bytes':reused,'object_count':len(artifacts),'manifest_bytes':output.stat().st_size}


def load_reference(path,*,document=None):
    path=Path(path)
    if document is None:document=json.loads(path.read_text())
    if document.get('storage_version') not in ('market-reference-parts-v1',REFERENCE_VERSION):return document
    root=path.parent.parent.resolve();fields=document.get('fields');indices=document.get('indices')
    if not isinstance(fields,dict) or not isinstance(indices,dict) or 'indices' in fields:raise ValueError('参考资料轻清单结构无效')
    def get(item):
        if not isinstance(item,dict):raise ValueError('参考资料对象清单无效')
        digest=item.get('sha256');size=item.get('bytes');key=item.get('object_key')
        if not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest) or not isinstance(size,int) or isinstance(size,bool) or size<0:raise ValueError('参考资料对象SHA或大小无效')
        if key!=f'{OBJECT_ROOT}/reference/{digest[:2]}/{digest}.json':raise ValueError('参考资料对象路径与SHA不符')
        target=safe_object(root,key);_verify(target,digest,size)
        return json.loads(target.read_text())
    result={key:get(item) for key,item in fields.items()};result['indices']={}
    if 'master_columns' in document:
        master=document['master_columns']
        if not isinstance(master,dict) or not isinstance(master.get('columns'),dict):raise ValueError('证券目录分列清单无效')
        symbols=get(master.get('keys'));
        if not isinstance(symbols,list) or len(symbols)!=len(set(symbols)) or any(not isinstance(symbol,str) for symbol in symbols):raise ValueError('证券目录主键无效')
        rows={symbol:{} for symbol in symbols}
        for field,column in master['columns'].items():
            if not isinstance(field,str) or not isinstance(column,dict):raise ValueError('证券目录列无效')
            values=get(column.get('reference'))
            if column.get('encoding')=='shared-values':
                if not isinstance(values,list):raise ValueError('证券目录共享值无效')
                mapping={}
                for group in values:
                    if not isinstance(group,dict) or not isinstance(group.get('symbols'),list):raise ValueError('证券目录共享值分组无效')
                    for symbol in group['symbols']:
                        if symbol in mapping:raise ValueError('证券目录列主键重复')
                        mapping[symbol]=group.get('value')
                values=mapping
            elif column.get('encoding')!='mapping':raise ValueError('证券目录列编码无效')
            if not isinstance(values,dict) or not set(values)<=set(rows):raise ValueError('证券目录列主键越界')
            for symbol,value in values.items():rows[symbol][field]=value
        result['master']=rows
    for symbol,parts in indices.items():
        if not isinstance(parts,list):raise ValueError('指数参考分片无效')
        rows=[];prior=None
        for part in parts:
            month=part.get('month') if isinstance(part,dict) else None
            if not isinstance(month,str) or not re.fullmatch(r'\d{4}-\d{2}',month) or (prior is not None and month<=prior):raise ValueError('指数参考月份无效或重复')
            prior=month;values=get(part)
            if not isinstance(values,list) or any(not isinstance(row,dict) or not isinstance(row.get('date'),str) or row['date'][:7]!=month for row in values):raise ValueError('指数参考分片日期不符')
            rows.extend(values)
        result['indices'][symbol]=rows
    return result
