import json
import os
from app.analytics.market import MarketData


def test_fixed_manifest_cache_detects_atomic_replace_and_delete(tmp_path):
    folder=tmp_path/'published'/'market-fixture';folder.mkdir(parents=True)
    manifest=folder/'manifest.json'
    def payload(rows):return json.dumps({'summary':{'snapshot_id':'market-fixture','rows':rows}})
    manifest.write_text(payload(1))
    market=MarketData(tmp_path,'market-fixture')
    first=market._load()
    assert first['rows']==1 and market._load() is first
    stat=manifest.stat();new=folder/'replacement.json';new.write_text(payload(2))
    os.utime(new,ns=(stat.st_atime_ns,stat.st_mtime_ns));new.replace(manifest)
    assert market._load()['rows']==2
    assert market._load() is not first
    manifest.unlink()
    assert market._load()=={}
