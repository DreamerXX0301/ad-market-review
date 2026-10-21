"""Stable application facade; implementation lives in infrastructure modules."""
from __future__ import annotations
import json
import threading
from .infrastructure.config import APP_NAME,APP_ID,APP_VERSION,PROJECT_ROOT,data_dir,sqlite_path,published_dir,raw_dir,work_dir
from .infrastructure.storage import now_ms,new_id,json_clean,dumps,digest,hash_file,atomic_json
from .infrastructure.database import connect,ensure_ready,check_sqlite_runtime
from .infrastructure.publication import publish_snapshot
_markets = {}
_market_lock = threading.RLock()

def get_market():
    from app.analytics.market import MarketData
    with connect() as connection:
        row=connection.execute("SELECT s.lineage_json FROM published_head h JOIN dataset_snapshot s ON h.snapshot_id=s.snapshot_id WHERE h.module='daily_raw' ORDER BY h.changed_at_ms DESC LIMIT 1").fetchone()
    snapshot=json.loads(row[0]).get('market_snapshot_id','') if row else ''
    key=(str(data_dir()),snapshot)
    with _market_lock:
        if key not in _markets:
            _markets[key]=MarketData(data_dir(),snapshot_id=snapshot)
            # Retain only the current and previous generation; active callers
            # keep their own object reference until their request completes.
            while len(_markets)>2:_markets.pop(next(iter(_markets)))
        return _markets[key]

def clear_market_cache():
    with _market_lock:
        _markets.clear()

def business_date():
    with connect() as connection:
        row=connection.execute("SELECT business_date FROM business_clock WHERE singleton=1").fetchone()
    return row[0] if row else None

def setting(key,default=None):
    with connect() as connection:
        row=connection.execute("SELECT value_json FROM platform_setting WHERE key=?",(key,)).fetchone()
    return json.loads(row[0]) if row else default

def set_setting(key,value):
    with connect() as connection:
        connection.execute("INSERT INTO platform_setting(key,value_json,updated_at_ms) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at_ms=excluded.updated_at_ms",(key,dumps(value),now_ms()))
