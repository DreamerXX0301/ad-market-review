from pathlib import Path
import json
import duckdb
import pytest
from app.analytics.market import MarketData, date_ms
from app.analytics.indicators import enrich_bars, aggregate_weekly


def sample_rows(n=40):
    from datetime import date,timedelta
    start=date(2026,1,1)
    return [{'date':(start+timedelta(days=i)).isoformat(),'open':10+i,'high':11+i,'low':9+i,'close':10+i,'volume':100*(i+1),'amount':1000*(i+1),'corporate_action':False} for i in range(n)]


def test_indicators_warmup_and_action_reset():
    rows=sample_rows();out=enrich_bars(rows)
    assert out[3]['ma5'] is None
    assert out[4]['ma5']==12
    assert out[33]['macd_dif'] is None and out[34]['macd_dif'] is not None
    rows[35]['corporate_action']=True
    reset=enrich_bars(rows)
    assert reset[35]['change_pct'] is None and reset[35]['ma30'] is None
    assert reset[39]['ma5']==47


def test_gap_reset_does_not_compare_to_arbitrary_old_day():
    rows=sample_rows(10);cal=[r['date'] for r in rows];rows.pop(7)
    out=enrich_bars(rows,cal)
    assert out[7]['change_pct'] is None and out[7]['ma5'] is None
    assert out[7]['volume_ratio'] is None


def test_weekly_ohlcv_and_partial():
    rows=[{'date':d,'open':o,'high':o+2,'low':o-1,'close':o+1,'volume':100,'amount':1000,'corporate_action':False} for d,o in [('2026-10-15',10),('2026-10-16',11),('2026-10-19',12)]]
    out=aggregate_weekly(rows,[r['date'] for r in rows])
    assert out[0]['open']==10 and out[0]['close']==12 and out[0]['volume']==200
    assert out[0]['complete'] is True and out[1]['complete'] is False


def test_source_date_is_shanghai_not_utc():
    assert date_ms(1760889600000)=='2025-10-20'


def test_fixed_generation_ignores_uncommitted_file_head(tmp_path):
    pub=tmp_path/'published';pub.mkdir()
    for name in ('first','second'):
        (pub/name).mkdir();(pub/name/'manifest.json').write_text(json.dumps({'summary':{'snapshot_id':name,'rows':1}}));(pub/name/'reference.json').write_text('{}')
    (pub/'market-head.json').write_text(json.dumps({'snapshot_id':'second','rows':1}))
    assert MarketData(tmp_path,'first').status()['snapshot_id']=='first'
    assert MarketData(tmp_path,'').status()['ready'] is False


def test_filesystem_flush_helpers_sync_actual_descriptors(tmp_path,monkeypatch):
    import os,stat
    from app.analytics import market
    file=tmp_path/'fixture.bin';file.write_bytes(b'fixture')
    observed=[];original=os.fsync
    def flush(descriptor):observed.append(stat.S_IFMT(os.fstat(descriptor).st_mode));return original(descriptor)
    monkeypatch.setattr(market.os,'fsync',flush)
    market.fsync_file(file);market.fsync_directory(tmp_path)
    assert observed==[stat.S_IFREG,stat.S_IFDIR]


@pytest.mark.skipif(not (Path(__file__).resolve().parents[2]/'data/published/market-e753fb724d34bcd88300c09c/manifest.json').exists(),reason='explicit audit snapshot not retained')
def test_real_snapshot_independent_and_exact():
    m=MarketData(Path(__file__).resolve().parents[2]/'data','market-e753fb724d34bcd88300c09c')
    assert m.status()['rows']==1325893
    assert len(m.calendar())==242 and m.calendar()[-1]=='2026-10-19'
    assert len(m.bars('600519.SH'))==242
    assert m.chart('000001.SH')['price_unit']=='点'
    assert len(m.chart('000001.SH')['bars'])==315
    assert len(m.topics_data())==3
    assert all('http' not in str(r) for r in m.topics_data())
    assert m.instrument('600519.SH')['name']=='贵州茅台'
    assert m.chart('600519.SH')['bars'][-1]['turnover_ratio_pct'] is None
