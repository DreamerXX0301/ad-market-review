"""Explicit local administration; no command silently schedules new work."""
import argparse
import json
from app.core import APP_VERSION,connect,ensure_ready,hash_file,published_dir,json_clean


def validate():
    ensure_ready();errors=[];artifacts=0
    with connect() as c:
        integrity=c.execute('PRAGMA integrity_check').fetchone()[0]
        foreign=[list(r) for r in c.execute('PRAGMA foreign_key_check')]
        snapshots=c.execute("SELECT * FROM dataset_snapshot WHERE state='published'").fetchall()
        files=c.execute("SELECT DISTINCT a.object_key,a.sha256 FROM snapshot_artifact a JOIN dataset_snapshot s ON a.snapshot_id=s.snapshot_id WHERE s.state='published'").fetchall()
        heads=[dict(r) for r in c.execute('SELECT * FROM published_head')]
        migrations=[dict(r) for r in c.execute('SELECT * FROM _schema_migration ORDER BY name')]
    if integrity!='ok':errors.append('SQLite integrity_check failed')
    if foreign:errors.append('SQLite foreign keys failed')
    for file in files:
        path=(published_dir()/file['object_key']).resolve()
        if not path.is_relative_to(published_dir().resolve()) or not path.is_file() or hash_file(path)!=file['sha256']:errors.append('artifact: '+file['object_key'])
        artifacts+=1
    for snapshot in snapshots:
        path=published_dir()/snapshot['manifest_path']
        if not path.is_file() or hash_file(path)!=snapshot['manifest_sha256']:errors.append('manifest: '+snapshot['snapshot_id'])
    return {'ok':not errors,'version':APP_VERSION,'integrity':integrity,'foreign_key_errors':foreign,'checked_artifacts':artifacts,'checked_snapshots':len(snapshots),'heads':heads,'migrations':migrations,'errors':errors}


def main():
    parser=argparse.ArgumentParser(description='市序本地管理')
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
