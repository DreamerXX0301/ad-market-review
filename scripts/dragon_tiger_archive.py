"""Bounded optional dragon-tiger backup graph, independent of app/credentials.

Follow only the small published head, at most 30 dated records and the six
accepted component-pointer namespaces. Do not enumerate an historical CAS tree.
"""
from __future__ import annotations
from datetime import date
import hashlib,json,re
from pathlib import Path

VERSION='dragon-tiger-v1'
CAPABILITIES=('fuyao-all','fuyao-org','fuyao-hot_money','eastmoney-buy','eastmoney-sell','eastmoney-disclosures')
CAS=re.compile(r'artifacts/[0-9a-f]{64}\.json\Z')
DAY=re.compile(r'\d{4}-\d{2}-\d{2}\Z')
SHA=re.compile(r'[0-9a-f]{64}\Z')


def inventory(published,*,safe_path,sha):
    root=safe_path(published,'dragon-tiger')
    if not root.exists():return {}
    if not root.is_dir():raise ValueError('Dragon-tiger root is not a directory')
    required={};documents={}
    def valid_day(day):
        try:return isinstance(day,str) and bool(DAY.fullmatch(day)) and date.fromisoformat(day).isoformat()==day
        except ValueError:return False
    def read(path):
        relative=path.relative_to(root).as_posix();key='dragon-tiger/'+relative
        if key in documents:return documents[key]
        if not path.is_file() or path.is_symlink() or path.stat().st_size>32*1024*1024:raise ValueError('Dragon-tiger referenced object is missing or invalid')
        before=sha(path)
        try:value=json.loads(path.read_text())
        except (OSError,ValueError) as error:raise ValueError('Invalid dragon-tiger JSON') from error
        if not isinstance(value,dict) or sha(path)!=before:raise ValueError('Dragon-tiger changed while reading metadata')
        required[key]=before;documents[key]=value
        return value
    def reference(pointer):
        if not isinstance(pointer,dict) or not isinstance(pointer.get('record_ref'),str) or not CAS.fullmatch(pointer['record_ref']) or not isinstance(pointer.get('sha256'),str) or not SHA.fullmatch(pointer['sha256']):raise ValueError('Invalid dragon-tiger object pointer')
        path=safe_path(root,pointer['record_ref']);value=read(path)
        if required['dragon-tiger/'+pointer['record_ref']]!=pointer['sha256']:raise ValueError('Dragon-tiger object checksum mismatch')
        encoded=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        if hashlib.sha256(encoded).hexdigest()!=path.stem:raise ValueError('Dragon-tiger content identity mismatch')
        return value
    head_path=safe_path(root,'head.json')
    if head_path.exists():
        head=read(head_path)
        if head.get('version')!=VERSION:raise ValueError('Invalid dragon-tiger head version')
        index=reference(head)
        if index.get('version')!=VERSION or not isinstance(index.get('days'),dict) or len(index['days'])>30:raise ValueError('Invalid dragon-tiger index version or date range')
        if index.get('anchor_date') is not None and not valid_day(index['anchor_date']):raise ValueError('Invalid dragon-tiger anchor date')
        for day,pointer in index['days'].items():
            if not valid_day(day):raise ValueError('Invalid dragon-tiger retained date')
            record=reference(pointer)
            if record.get('version')!=VERSION or record.get('accepted') is not True or record.get('date')!=day:raise ValueError('Invalid dragon-tiger dated record')
            sources=record.get('source_objects')
            if not isinstance(sources,list) or len(sources)>6:raise ValueError('Invalid dragon-tiger source closure')
            for source in sources:
                component=reference(source)
                if component.get('version')!=VERSION or component.get('date')!=day or component.get('capability') not in CAPABILITIES:raise ValueError('Dragon-tiger source component date or version differs')
    parts=safe_path(root,'parts');count=0
    if parts.exists():
        if not parts.is_dir():raise ValueError('Invalid dragon-tiger parts directory')
        for folder in parts.iterdir():
            if folder.name=='.DS_Store' and folder.is_file():continue
            if folder.is_symlink() or folder.name not in CAPABILITIES or not folder.is_dir():raise ValueError('Unknown dragon-tiger component namespace')
            for path in folder.iterdir():
                if path.name=='.DS_Store' and path.is_file():continue
                if path.name.endswith('.tmp') and path.is_file() and not path.is_symlink():continue
                count+=1
                if count>4096 or path.is_symlink() or not path.is_file() or path.suffix!='.json' or not valid_day(path.stem):raise ValueError('Invalid or unbounded dragon-tiger component pointer inventory')
                component=reference(read(path))
                if component.get('version')!=VERSION or component.get('date')!=path.stem or component.get('capability')!=folder.name:raise ValueError('Dragon-tiger component pointer scope differs')
    return required
