"""Explicit local administration; no command silently schedules new work."""
import argparse
import json
import os
from pathlib import Path
from app.core import APP_VERSION,connect,ensure_ready,hash_file,published_dir,json_clean


def validate():
    ensure_ready();errors=[];artifacts=0
    from app.services.publication_validation import authorized_removed_files
    with connect() as c:
        c.execute('BEGIN')
        integrity=c.execute('PRAGMA integrity_check').fetchone()[0]
        foreign=[list(r) for r in c.execute('PRAGMA foreign_key_check')]
        snapshots=c.execute("SELECT * FROM dataset_snapshot WHERE state='published'").fetchall()
        files=c.execute("SELECT a.* FROM snapshot_artifact a JOIN dataset_snapshot s ON a.snapshot_id=s.snapshot_id WHERE s.state='published'").fetchall()
        heads=[dict(r) for r in c.execute('SELECT * FROM published_head')]
        migrations=[dict(r) for r in c.execute('SELECT * FROM _schema_migration ORDER BY name')]
        removed=authorized_removed_files(c)
    if integrity!='ok':errors.append('SQLite integrity_check failed')
    if foreign:errors.append('SQLite foreign keys failed')
    authorized=[];checked={}
    def check(key,sha,size,snapshot_id):
        raw=published_dir()/key;path=raw.resolve()
        safe=not Path(key).is_absolute() and '..' not in Path(key).parts and path.is_relative_to(published_dir().resolve()) and raw.absolute()==path
        if not safe:return False
        tombstone=removed.get((snapshot_id,key))
        if not os.path.lexists(raw) and tombstone:
            authorized.append(tombstone);return True
        identity=(str(path),sha,size)
        if identity not in checked:
            checked[identity]=path.is_file() and (size is None or path.stat().st_size==size) and hash_file(path)==sha
        return checked[identity]
    for file in files:
        if not check(file['object_key'],file['sha256'],file['byte_count'],file['snapshot_id']):errors.append('artifact: '+file['object_key'])
    artifacts=len({(f['object_key'],f['sha256']) for f in files})
    for snapshot in snapshots:
        if not check(snapshot['manifest_path'],snapshot['manifest_sha256'],None,snapshot['snapshot_id']):errors.append('manifest: '+snapshot['snapshot_id'])
    return {'ok':not errors,'version':APP_VERSION,'integrity':integrity,'foreign_key_errors':foreign,'checked_artifacts':artifacts,'checked_snapshots':len(snapshots),'heads':heads,'migrations':migrations,'authorized_removed':authorized,'errors':errors}


def main():
    parser=argparse.ArgumentParser(description='冒险的梦想家本地管理')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('migrate');sub.add_parser('validate')
    importer=sub.add_parser('import-audit');importer.add_argument('path')
    server=sub.add_parser('serve');server.add_argument('--host',default='127.0.0.1');server.add_argument('--port',type=int,default=8788)
    args=parser.parse_args()
    if args.command=='migrate':ensure_ready();result={'ok':True,'version':APP_VERSION}
    elif args.command=='validate':result=validate()
    elif args.command=='import-audit':
        from app.services.bootstrap import import_validated_audit
        result=import_validated_audit(args.path)
    elif args.command=='serve':
        import uvicorn
        uvicorn.run('app.main:app',host=args.host,port=args.port);return
    print(json.dumps(json_clean(result),ensure_ascii=False,indent=2))
    if result.get('ok') is False:raise SystemExit(1)

if __name__=='__main__':main()
