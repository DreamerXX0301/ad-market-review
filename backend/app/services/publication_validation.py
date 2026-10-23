"""Read-only recognition of exact, completed workspace-purge file tombstones."""
import json

from app.core import digest, now_ms
from .workspace_purge import _file_info, _shared_snapshot


def authorized_removed_files(connection):
    """Publication rows remain immutable; absence needs the sealed unlink ledger."""
    authorized = {}
    snapshots = connection.execute("""SELECT s.* FROM dataset_snapshot s JOIN snapshot_gc g USING(snapshot_id)
        WHERE s.state='published' AND s.module='backtest_result' AND g.state='purged'""").fetchall()
    for snapshot in snapshots:
        sid = snapshot['snapshot_id']
        purges = connection.execute("""SELECT DISTINCT p.* FROM workspace_purge p
            JOIN workspace_purge_file f USING(purge_id) WHERE f.snapshot_id=? AND p.status='completed'
            AND p.business_deleted=1 AND p.superseded_by IS NULL""", (sid,)).fetchall()
        for purge in purges:
            try:
                scope = json.loads(purge['scope_json'])
                owned = scope['owned']
                if (digest(scope) != purge['scope_sha256'] or scope['target_kind'] != purge['target_kind']
                        or scope['target_id'] != purge['target_id'] or sid not in scope['snapshots']
                        or not all(isinstance(owned[key], list) and all(isinstance(value, str) for value in owned[key])
                                   and len(owned[key]) == len(set(owned[key])) for key in ('strategy', 'backtest', 'job', 'selection', 'round'))
                        or snapshot['scope_key'] not in owned['backtest'] or snapshot['job_id'] not in owned['job']
                        or (purge['target_kind'] == 'backtest' and (owned['backtest'] != [purge['target_id']] or owned['strategy']))
                        or (purge['target_kind'] == 'strategy' and owned['strategy'] != [purge['target_id']])):
                    continue
                job = connection.execute('SELECT * FROM job WHERE job_id=?', (purge['job_id'],)).fetchone()
                params = json.loads(job['parameters_json']) if job else {}
                # Completion commits before tasks.finish; a crash may leave the
                # exact cleanup job running although its file ledger is final.
                if (not job or job['kind'] != 'cleanup' or params.get('operation') != 'workspace_purge'
                        or params.get('purge_id') != purge['purge_id'] or digest(params) != job['parameters_hash']
                        or ('scope' in params and params['scope'] != scope)):
                    continue
                audits = {r['stage']: json.loads(r['details_json']) for r in connection.execute(
                    'SELECT stage,details_json FROM workspace_purge_audit WHERE purge_id=?', (purge['purge_id'],))}
                if (audits.get('confirmed', {}).get('scope_sha256') != purge['scope_sha256']
                        or audits.get('confirmed', {}).get('counts') != scope['counts']
                        or audits.get('business_deleted', {}).get('counts') != scope['counts']
                        or audits.get('completed', {}).get('files') != len(scope['files'])):
                    continue
                expired_pins = [r[0] for r in connection.execute(
                    'SELECT pin_id FROM retention_pin WHERE snapshot_id=? AND expires_at_ms<=?', (sid, now_ms()))]
                remaining = {'backtest': [], 'selection': [], 'round': [], 'job': owned['job'] + [job['job_id']]}
                if _shared_snapshot(connection, sid, remaining, expired_pins):
                    continue
                artifacts = connection.execute('SELECT * FROM snapshot_artifact WHERE snapshot_id=?', (sid,)).fetchall()
                expected = {r['object_key']: (r['sha256'], r['byte_count']) for r in artifacts}
                expected[snapshot['manifest_path']] = (snapshot['manifest_sha256'], None)
                if len(expected) != len(artifacts) + 1:
                    continue
                scoped = [f for f in scope['files'] if f['snapshot_id'] == sid]
                ledger = connection.execute('SELECT * FROM workspace_purge_file WHERE purge_id=? AND snapshot_id=?',
                                            (purge['purge_id'], sid)).fetchall()
                if (len(scoped) != len(expected) or len(ledger) != len(expected)
                        or {f['object_key'] for f in scoped} != set(expected)
                        or {f['object_key'] for f in ledger} != set(expected)):
                    continue
                sealed = {f['object_key']: f for f in scoped}
                candidates = {}
                for file in ledger:
                    key = file['object_key']; planned = sealed[key]; sha, size = expected[key]
                    if (file['sha256'] != sha or planned['sha256'] != sha
                            or file['byte_count'] != planned['byte_count'] or (size is not None and size != file['byte_count'])
                            or file['status'] not in ('removed', 'absent')
                            or file['removed_bytes'] != (file['byte_count'] if file['status'] == 'removed' else 0)):
                        break
                    if (connection.execute('SELECT 1 FROM snapshot_artifact WHERE object_key=? AND snapshot_id<>? LIMIT 1', (key, sid)).fetchone()
                            or connection.execute('SELECT 1 FROM dataset_snapshot WHERE manifest_path=? AND snapshot_id<>? LIMIT 1', (key, sid)).fetchone()):
                        break
                    _file_info(key, sha, file['byte_count'], snapshot_id=sid, backtest_id=snapshot['scope_key'],
                               manifest=key == snapshot['manifest_path'])
                    candidates[(sid, key)] = {'snapshot_id': sid, 'object_key': key, 'sha256': sha,
                                              'byte_count': file['byte_count'], 'purge_id': purge['purge_id'],
                                              'status': file['status']}
                else:
                    authorized.update(candidates)
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                # Incomplete or inconsistent evidence never excuses a missing file.
                continue
    return authorized
