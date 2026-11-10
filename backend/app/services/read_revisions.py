"""Small receipts for revalidating retained pages without reading their contents."""
from datetime import date
import json

from fastapi import HTTPException

from app.core import digest, published_dir
from . import sync_clock
from .sync_progress import lock_record


_ARCHIVES = {
    'auction': '''SELECT s.snapshot_id,s.content_sha256,d.content_sha256 FROM market_auction_snapshot s
        LEFT JOIN market_auction_discovery d USING(trade_date) WHERE s.trade_date=:day''',
    'intraday': '''SELECT slot,snapshot_id,content_sha256,NULL FROM market_intraday_capture
        WHERE trade_date=:day AND snapshot_id IS NOT NULL
        UNION ALL SELECT slot,NULL,source_sha256,generated_at_ms FROM market_intraday_brief
        WHERE trade_date=:day ORDER BY 1,2''',
    'noon': '''SELECT s.snapshot_id,s.content_sha256,
        (SELECT max(revision) FROM market_noon_note WHERE snapshot_id=s.snapshot_id),
        (SELECT result_sha256 FROM market_noon_close_review WHERE trade_date=s.trade_date
         ORDER BY revision DESC LIMIT 1) FROM market_noon_snapshot s WHERE s.trade_date=:day''',
    'news': '''SELECT stage,status,captured_at,content_sha256 FROM market_news_snapshot
        WHERE trade_date=:day ORDER BY stage''',
}


def observation_day(value):
    if value is not None:
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() == value:
                return value
        except (TypeError, ValueError):
            pass
        raise HTTPException(422, '观察日期须为有效的 YYYY-MM-DD')
    return None


def _receipt(connection, query, parameters=()):
    rows = [tuple(row) for row in connection.execute(query, parameters)]
    return digest(rows) if rows else None


def _published_file_receipt(path, identifier):
    # These two publication heads are atomic pointers. The page reader still
    # checks the actual artifact and its SHA. Never expand the CAS here.
    try:
        with path.open('rb') as stream:
            encoded = stream.read(8193)
        if len(encoded) > 8192:
            return None
        head = json.loads(encoded)
        proof = (head.get(identifier), head.get('sha256')) if isinstance(head, dict) else ()
        return digest(proof) if proof and all(isinstance(value, str) and value for value in proof) else None
    except (OSError, ValueError):
        return None


def read_revisions(connection, observation_date):
    # The caller's read transaction keeps the lock and the publication heads in
    # the same SQLite snapshot, even when a worker is publishing concurrently.
    today = sync_clock.now_china().date().isoformat()
    if lock_record(connection):
        return {'today': today, 'market': None, 'market_locked': True, 'dates': {}, 'domains': {}}

    published = connection.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone()
    days = dict.fromkeys(day for day in (today, published[0] if published else None, observation_date) if day)
    market = _receipt(connection, '''SELECT module,scope_key,snapshot_id FROM published_head
        WHERE module IN ('daily_raw','calendar','security_identity','corporate_actions',
            'analysis_bars','indicators','topics','market_review') ORDER BY module,scope_key''')
    dates = {
        day: {kind: _receipt(connection, query, {'day': day})
              for kind, query in _ARCHIVES.items()}
        for day in days
    }

    # The recent receipt list is capped at five across all kinds. Aggregate
    # compact job metadata independently, so a result cannot age out of its
    # own domain just because other jobs finished later.
    job_rows = [tuple(row) for row in connection.execute('''SELECT
        CASE WHEN kind='source_check' THEN CASE json_extract(parameters_json,'$.operation')
            WHEN 'sector_chart' THEN 'sector_charts' WHEN 'sector_charts' THEN 'sector_charts'
            WHEN 'strategy_batch' THEN 'selection' WHEN 'strategy_batch_tracking' THEN 'tracking_review'
            ELSE 'source_check' END
            ELSE kind END AS domain_kind,status,count(*),max(finished_at_ms)
        FROM job GROUP BY domain_kind,status ORDER BY domain_kind,status''')]
    domains = {'tasks': digest(job_rows)}
    for domain, kind in (('strategy', 'selection'), ('tracking', 'tracking_review'),
                         ('research', 'research'), ('backtest', 'backtest'),
                         ('sector_charts', 'sector_charts')):
        terminal = [row for row in job_rows if row[0] == kind
                    and row[1] in ('succeeded', 'partial', 'no_change', 'failed', 'cancelled')]
        domains[domain] = digest(terminal) if terminal else None
    root = published_dir()
    domains['supplement'] = _published_file_receipt(root / 'market-supplement' / 'head.json', 'snapshot_id')
    domains['dragon_tiger'] = _published_file_receipt(root / 'dragon-tiger' / 'head.json', 'record_ref')
    from .market_suspensions import suspension_revision
    suspension = suspension_revision()
    domains['suspensions'] = digest(suspension) if suspension else None
    domains['news_calendar'] = _receipt(connection, '''SELECT source,captured_at,content_sha256
        FROM market_news_calendar_archive ORDER BY rowid DESC LIMIT 1''')
    return {'today': today, 'market': market, 'market_locked': False, 'dates': dates, 'domains': domains}
