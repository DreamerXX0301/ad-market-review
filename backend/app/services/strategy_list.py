"""Request-local facts for strategy lists; no policy/result cache across reads."""
import json


def read_facts(connection, identifiers):
    result = {sid: {'active': [], 'applications': [], 'default_applications': set(),
                   'backtests': [], 'purges': [], 'related_jobs': set(), 'marker': None}
              for sid in identifiers}
    jobs = [dict(row) for row in connection.execute("SELECT * FROM job WHERE status NOT IN ('succeeded','no_change','partial','failed','cancelled') OR lease_owner IS NOT NULL OR lease_expires_at_ms IS NOT NULL")]
    # Bounded SQL parameter counts also support old installations with many
    # archived strategies, while only the requested page is materialized.
    for offset in range(0, len(identifiers), 400):
        ids = identifiers[offset:offset+400]
        marks = ','.join('?' for _ in ids)
        def rows(sql):
            return [dict(row) for row in connection.execute(sql.replace('{ids}', marks), ids)]
        for key, sql in (
            ('strategy', 'SELECT * FROM strategy WHERE strategy_id IN ({ids})'),
            ('version', 'SELECT v.* FROM strategy_version v JOIN strategy s ON s.strategy_id=v.strategy_id AND s.current_version=v.version WHERE s.strategy_id IN ({ids})'),
            ('policy', 'SELECT p.* FROM tracking_policy_version p JOIN strategy s ON s.strategy_id=p.strategy_id AND s.current_policy_version=p.policy_version WHERE s.strategy_id IN ({ids})'),
            ('marker', 'SELECT * FROM strategy_deletion WHERE strategy_id IN ({ids})'),
        ):
            for row in rows(sql): result[row['strategy_id']][key] = row
        for row in rows('''SELECT s.strategy_id,r.run_id,r.business_date,r.status FROM strategy s
            JOIN selection_run r ON r.rowid=(SELECT rowid FROM selection_run
                WHERE strategy_id=s.strategy_id ORDER BY created_at_ms DESC,rowid DESC LIMIT 1)
            WHERE s.strategy_id IN ({ids})'''):
            result[row.pop('strategy_id')]['latest'] = row
        for row in rows("SELECT round_id,strategy_id,current_policy_version,entry_snapshot_id,entry_business_date FROM tracking_round WHERE strategy_id IN ({ids}) AND status='tracking'"):
            result[row['strategy_id']]['active'].append(row)
        for row in rows("SELECT a.* FROM tracking_policy_application a JOIN tracking_round r USING(round_id) WHERE r.strategy_id IN ({ids}) AND r.status='tracking' ORDER BY a.effective_business_date,a.recorded_at_ms,a.rowid"):
            result[row['strategy_id']]['applications'].append(row)
        for row in rows("SELECT r.strategy_id,e.evidence_json FROM tracking_event e JOIN tracking_round r USING(round_id) WHERE r.strategy_id IN ({ids}) AND r.status='tracking' AND e.event_type='policy_change'"):
            evidence = json.loads(row['evidence_json']) if row['evidence_json'] else {}
            if evidence.get('scope') == 'strategy_default':
                result[row['strategy_id']]['default_applications'].add(evidence.get('application_id'))
        for row in rows('''SELECT b.*,j.status job_status,j.lease_owner,j.lease_expires_at_ms,
            d.backtest_id deleted_backtest FROM backtest_run b JOIN job j USING(job_id)
            LEFT JOIN backtest_deletion d USING(backtest_id) WHERE b.strategy_id IN ({ids})'''):
            result[row['strategy_id']]['backtests'].append(row)
        for row in rows('''SELECT p.*,coalesce(b.strategy_id,p.target_id) related_strategy_id,p.rowid purge_order
            FROM workspace_purge p LEFT JOIN backtest_run b ON p.target_kind='backtest' AND b.backtest_id=p.target_id
            WHERE (p.target_kind='strategy' OR b.strategy_id IS NOT NULL)
            AND coalesce(b.strategy_id,p.target_id) IN ({ids}) ORDER BY p.created_at_ms DESC,p.rowid DESC'''):
            result[row['related_strategy_id']]['purges'].append(row)
        if jobs:
            for row in rows("SELECT r.strategy_id,r.job_id FROM selection_run r JOIN job j USING(job_id) WHERE r.strategy_id IN ({ids}) AND (j.status NOT IN ('succeeded','no_change','partial','failed','cancelled') OR j.lease_owner IS NOT NULL OR j.lease_expires_at_ms IS NOT NULL)"):
                result[row['strategy_id']]['related_jobs'].add(row['job_id'])
    for item in result.values(): item['jobs'] = jobs
    return result
