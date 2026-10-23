"""Partitioned market storage fixtures; no source traffic or user-data mutations."""
from copy import deepcopy
import json
from pathlib import Path

import duckdb
import pytest

from app.analytics.market import MarketData, hash_file
from app.analytics.market_storage import activity_window, parquet_input, read_descriptor, write_partitions


def publish(root,generation,rows):
    published=root/'published';stage=published/('.staging-'+generation);stage.mkdir(parents=True)
    con=duckdb.connect(':memory:')
    try:
        con.execute('CREATE TABLE prices(instrument_id VARCHAR,date VARCHAR,open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,volume DOUBLE,amount DOUBLE,source VARCHAR,price_basis VARCHAR)')
        con.executemany('INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?,?)',rows)
        files,stats=write_partitions(con,'prices','daily.parquet',published,stage,generation)
    finally:con.close()
    calendar=sorted({row[1] for row in rows})
    reference={'calendar':calendar,'master':{'600001.SH':{'instrument_id':'600001.SH','kind':'stock'}},'indices':{}}
    (stage/'reference.json').write_text(json.dumps(reference))
    files.append({'role':'reference.json','path':f'published/{generation}/reference.json','sha256':hash_file(stage/'reference.json'),'bytes':(stage/'reference.json').stat().st_size})
    head={'snapshot_id':generation,'rows':len(rows),'symbols':1,'data_begin':calendar[0],'data_end':calendar[-1],'files':files}
    (stage/'manifest.json').write_text(json.dumps({'summary':head}))
    stage.rename(published/generation);MarketData._publish_head(published,head)
    return MarketData(root,generation),stats


def prices():
    return [('600001.SH',day,10.,11.,9.,10.5,100.,1000.,'fuyao','none') for day in ['2026-01-05','2026-01-06','2026-02-05','2026-03-05']]


def test_cross_generation_reuses_months_and_preserves_exact_old_quotes(tmp_path):
    old,first=publish(tmp_path,'market-'+'1'*24,prices())
    old_paths=old.parquet_input();old_bytes={path:Path(path).read_bytes() for path in old_paths}
    new_rows=prices()+[('600001.SH','2026-03-06',11.,12.,10.,11.5,120.,1300.,'fuyao','none')]
    new,second=publish(tmp_path,'market-'+'2'*24,new_rows)
    assert first['partition_count']==3 and first['reused_object_bytes']==0
    assert second['partition_count']==3 and second['reused_object_bytes']>0
    assert set(old_paths[:2])<=set(new.parquet_input()) and old_paths[-1] not in new.parquet_input()
    assert [row['close'] for row in old.bars('600001.SH')]==[10.5]*4
    assert [row['close'] for row in new.bars('600001.SH')]==[10.5]*4+[11.5]
    assert [(row['date'],row['close']) for row in old.history_for_instruments(['600001.SH'])['600001.SH']]==[(row[1],row[5]) for row in prices()]
    assert all(Path(path).read_bytes()==data for path,data in old_bytes.items())
    assert len(new.parquet_input(start='2026-02-01',end='2026-02-28'))==1
    assert not (Path(new.status()['path'])/'daily.parquet').exists()


def test_partition_manifest_rejects_corruption_and_path_forgery(tmp_path):
    market,_=publish(tmp_path,'market-'+'3'*24,prices())
    descriptor=Path(market.status()['path'])/'daily.manifest.json'
    doc=json.loads(descriptor.read_text());path=Path(market.parquet_input()[0]);original=path.read_bytes()
    path.write_bytes(original[:-1]+bytes([original[-1]^1]))
    with pytest.raises(ValueError,match='SHA'):market.parquet_input()
    path.write_bytes(original)
    doc['partitions'][0]['object_key']='../outside.parquet';descriptor.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='路径'):read_descriptor(descriptor)


def test_legacy_single_file_and_calendar_leap_window_stay_compatible(tmp_path):
    con=duckdb.connect(':memory:');path=tmp_path/'daily.parquet'
    try:con.execute('COPY (SELECT 1 AS v) TO ? (FORMAT PARQUET)',[str(path)])
    finally:con.close()
    assert parquet_input(path)==str(path)
    assert activity_window('2024-02-29','2010-01-01')['active_begin']=='2019-02-28'
    assert activity_window('2026-10-23','2025-01-01')['active_begin']=='2025-01-01'


def test_reference_fields_and_index_months_reuse_losslessly(tmp_path):
    from app.analytics.market_storage import write_reference,load_reference
    root=tmp_path/'published';old=root/('market-'+'4'*24);old.mkdir(parents=True)
    metadata={'master':{'600001.SH':{'name':'测试甲','kind':'stock'}},'topics':[{'topic_id':'fixture','instrument_ids':['600001.SH']}],
              'limitup':[],'limitdown':[],'indices':{'000001.SH':[{'date':'2026-01-05','close':3000.},{'date':'2026-02-05','close':3100.}]}}
    first,stats=write_reference(metadata,root,old,old.name)
    assert load_reference(old/'reference.json')==metadata
    assert len(first)==len({item['path'] for item in first})
    new=root/('market-'+'5'*24);new.mkdir()
    updated=deepcopy(metadata);updated['indices']['000001.SH'].append({'date':'2026-02-06','close':3120.})
    second,stats2=write_reference(updated,root,new,new.name)
    assert load_reference(new/'reference.json')==updated and load_reference(old/'reference.json')==metadata
    assert stats2['reused_object_bytes']>0 and stats2['new_object_bytes']<stats['new_object_bytes']
    before=json.loads((old/'reference.json').read_text());after=json.loads((new/'reference.json').read_text())
    assert before['fields']==after['fields']
    assert before['indices']['000001.SH'][0]==after['indices']['000001.SH'][0]
    assert before['indices']['000001.SH'][1]!=after['indices']['000001.SH'][1]


def test_warm_descriptor_cache_does_not_expose_mutable_structure(tmp_path):
    market,_=publish(tmp_path,'market-'+'6'*24,prices())
    descriptor=Path(market.status()['path'])/'daily.manifest.json'
    original=read_descriptor(descriptor);expected=market.parquet_input()
    original['partitions'][0]['object_key']='../forged.parquet'
    assert market.parquet_input()==expected
    document=json.loads(descriptor.read_text());document['rows']+=1
    descriptor.write_text(json.dumps(document))
    with pytest.raises(ValueError,match='汇总'):market.parquet_input()


@pytest.mark.parametrize('replace_parent',[False,True])
def test_warm_descriptor_rejects_object_or_directory_symlink(tmp_path,replace_parent):
    market,_=publish(tmp_path,'market-'+'7'*24,prices())
    target=Path(market.parquet_input()[0]);original=target.parent if replace_parent else target
    moved=original.with_name(original.name+'-retained');original.rename(moved)
    original.symlink_to(moved,target_is_directory=replace_parent)
    try:
        with pytest.raises(ValueError,match='符号链接'):market.parquet_input()
    finally:original.unlink();moved.rename(original)


def test_warm_stock_reader_rejects_corrupted_partition_and_recovers(tmp_path):
    market,_=publish(tmp_path,'market-'+'8'*24,prices())
    expected=market.bars('600001.SH');reader=market._daily_reader
    target=Path(market.parquet_input()[0]);original=target.read_bytes()
    target.write_bytes(original[:-1]+bytes([original[-1]^1]))
    with pytest.raises(ValueError,match='SHA'):market.bars('600001.SH')
    assert market._daily_reader is None and not market._symbol_bars
    with pytest.raises(Exception,match='closed'):reader.execute('SELECT 1')
    target.write_bytes(original)
    assert market.bars('600001.SH')==expected
    assert market._daily_reader is not reader
    market.close()


def test_full_frame_raw_history_still_checks_partition_source_sha(tmp_path):
    market,_=publish(tmp_path,'market-'+'9'*24,prices())
    expected=market.history_for_instruments(['600001.SH']);market._daily_frame()
    assert market._daily_reader is None and market._daily_columns
    target=Path(market.parquet_input()[0]);original=target.read_bytes()
    target.write_bytes(original[:-1]+bytes([original[-1]^1]))
    with pytest.raises(ValueError,match='SHA'):market.history_for_instruments(['600001.SH'])
    assert market._frame is None and market._frame_source_stamp is None
    target.write_bytes(original)
    assert market.history_for_instruments(['600001.SH'])==expected
