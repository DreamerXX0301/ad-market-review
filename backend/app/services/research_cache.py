"""Seven-day research JSON retention; never deletes market artifacts or jobs."""
import json
from fastapi import HTTPException
from app.core import connect,now_ms

TTL_MS=7*24*60*60*1000


def list_reports():
    now=now_ms()
    with connect() as connection:
        records=connection.execute('SELECT * FROM research_report WHERE expires_at_ms>? ORDER BY created_at_ms DESC LIMIT 100',(now,)).fetchall()
    items=[]
    for row in records:
        report=json.loads(row['report_json'])
        items.append({'report_id':row['report_id'],'job_id':row['job_id'],'instrument_id':row['instrument_id'],'instrument':report['instrument'],'data_cutoff':row['data_cutoff'],'created_at_ms':row['created_at_ms'],'last_accessed_at_ms':row['last_accessed_at_ms'],'expires_at_ms':row['expires_at_ms'],'status':report.get('status','completed')})
    return {'items':items,'total':len(items),'cache_ttl_days':7}


def get_report(identifier,*,touch=True):
    now=now_ms()
    with connect() as connection:
        if touch:connection.execute('BEGIN IMMEDIATE')
        row=connection.execute('SELECT * FROM research_report WHERE (report_id=? OR job_id=?) AND expires_at_ms>?',(identifier,identifier,now)).fetchone()
        if not row:raise HTTPException(404,'研究记录暂不可用，请选择标的后手动运行研究')
        access=now if touch else row['last_accessed_at_ms'];expires=now+TTL_MS if touch else row['expires_at_ms']
        if touch:
            connection.execute('UPDATE research_report SET last_accessed_at_ms=?,expires_at_ms=? WHERE report_id=?',(access,expires,row['report_id']))
            connection.execute("UPDATE retention_pin SET expires_at_ms=? WHERE owner_kind='research' AND owner_id=?",(expires,row['report_id']))
    return {**json.loads(row['report_json']),'cache':{'ttl_days':7,'last_accessed_at_ms':access,'expires_at_ms':expires}}


def cleanup_expired(now=None,limit=100):
    """Worker hook: bounded research-only cleanup, with external owner pins intact.

    A live external pin explicitly owned by this report/job protects its JSON.
    Other modules' pins on shared input snapshots are never altered; no input
    snapshot or physical file is deleted by this function.
    """
    now=now_ms() if now is None else int(now);limit=max(1,min(int(limit),1000))
    removed=released=protected=freed=0
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        expired=connection.execute("SELECT r.report_id,r.job_id,length(CAST(r.report_json AS BLOB)) size FROM research_report r WHERE r.expires_at_ms<=? ORDER BY CASE WHEN EXISTS(SELECT 1 FROM retention_pin p WHERE p.owner_kind<>'research' AND p.owner_id IN (r.report_id,r.job_id) AND (p.expires_at_ms IS NULL OR p.expires_at_ms>?)) THEN 1 ELSE 0 END,r.expires_at_ms,r.report_id LIMIT ?",(now,now,limit)).fetchall()
        for row in expired:
            released+=connection.execute("DELETE FROM retention_pin WHERE owner_kind='research' AND owner_id=?",(row['report_id'],)).rowcount
            external=connection.execute("SELECT 1 FROM retention_pin WHERE owner_kind<>'research' AND owner_id IN (?,?) AND (expires_at_ms IS NULL OR expires_at_ms>?) LIMIT 1",(row['report_id'],row['job_id'],now)).fetchone()
            if external:protected+=1;continue
            removed+=connection.execute('DELETE FROM research_report WHERE report_id=? AND expires_at_ms<=?',(row['report_id'],now)).rowcount
            freed+=row['size']
    return {'removed_reports':removed,'released_research_pins':released,'protected_reports':protected,'removed_json_bytes':freed,'scope':'research_only','cache_ttl_days':7}
