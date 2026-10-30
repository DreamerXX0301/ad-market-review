"""Exact merge proofs may skip encoding; old generations remain immutable."""
from dataclasses import replace
from pathlib import Path
import json

import duckdb
import pyarrow as pa
import pytest

from app.analytics.market_storage import (write_partitions, reusable_partitions,
    MergePartitionProof, file_sha, manifest_name)
from app.services.sync_service import merge_daily, midnight_ms
from test_sync import transport, start_job


def raw(day, close=10.):
    return dict(thscode='600001.SH',currency='CNY',interval='1d',adjusted='none',
                date_ms=midnight_ms(day),open_price=10.,high_price=max(11.,close),low_price=9.,
                close_price=close,volume=100.,turnover=1000.)


def normalize(con):
    con.execute("CREATE OR REPLACE VIEW normalized AS SELECT upper(thscode) instrument_id,strftime(to_timestamp(date_ms/1000.0) AT TIME ZONE 'Asia/Shanghai','%Y-%m-%d') date,open_price AS open,high_price AS high,low_price AS low,close_price AS close,volume,turnover AS amount,'fuyao' AS source,'none' AS price_basis FROM source_rows")


@pytest.mark.parametrize('revision',[False,True])
def test_proven_month_reuse_matches_full_writer_for_tail_and_old_revision(tmp_path, revision):
    root=tmp_path/'published';old=root/'old';old.mkdir(parents=True)
    con=duckdb.connect(':memory:');con.execute('SET threads=1')
    try:
        original=[raw(day) for day in ['2026-01-05','2026-02-05','2026-03-05']]
        con.register('source_rows',pa.Table.from_pylist(original));normalize(con)
        files=[]
        for relation,role in [('source_rows','raw-daily.parquet'),('normalized','daily.parquet')]:
            emitted,_=write_partitions(con,relation,role,root,old,'old');files+=emitted
        raw_doc=json.loads((old/'raw-daily.manifest.json').read_text())
        previous=[str(root/item['object_key']) for item in raw_doc['partitions']]
        original_bytes={path:Path(path).read_bytes() for path in previous}
        incoming=[raw('2026-03-06',10.5)]+([raw('2026-01-05',10.2)] if revision else [])
        con.register('incoming_rows',pa.Table.from_pylist(incoming))
        source=tmp_path/'incoming.parquet';con.execute('COPY incoming_rows TO ? (FORMAT PARQUET)',[str(source)])
        merged=tmp_path/'merged.parquet'
        coverage=merge_daily(source,previous,merged,'2026-01-05','2026-03-06',
            ['2026-01-05','2026-02-05','2026-03-05','2026-03-06'],[{'thscode':'600001.SH','list_date':'2020-01-01'}])
        proof=coverage.partition_reuse
        assert proof.changed_months==(('2026-01','2026-03') if revision else ('2026-03',))
        con.unregister('source_rows');con.read_parquet(str(merged)).create_view('source_rows');normalize(con)
        reuse=reusable_partitions(proof,file_sha(merged),old,root,con,'source_rows',files)
        assert len(reuse['daily.parquet'])==(1 if revision else 2)
        for mode in ('candidate','baseline'):
            stage=root/mode;stage.mkdir()
            for relation,role in [('source_rows','raw-daily.parquet'),('normalized','daily.parquet')]:
                _,stats=write_partitions(con,relation,role,root,stage,mode,reusable=reuse.get(role) if mode=='candidate' else None)
                if mode=='candidate':assert stats['encoded_partition_count']==(2 if revision else 1)
                else:assert (stage/manifest_name(role)).read_bytes()==(root/'candidate'/manifest_name(role)).read_bytes()
        assert all(Path(path).read_bytes()==data for path,data in original_bytes.items())
        assert reusable_partitions(replace(proof,output_sha256='0'*64),file_sha(merged),old,root,con,'source_rows',files)=={}
        assert reusable_partitions({'output_sha256':file_sha(merged)},file_sha(merged),old,root,con,'source_rows',files)=={}
        con.execute('CREATE VIEW changed_types AS SELECT * REPLACE(CAST(volume AS BIGINT) AS volume) FROM source_rows')
        assert reusable_partitions(proof,file_sha(merged),old,root,con,'changed_types',files)=={}
        # Even a structurally valid descriptor must match its published SHA.
        path=old/'daily.manifest.json';path.write_text(path.read_text()+' ')
        assert reusable_partitions(proof,file_sha(merged),old,root,con,'source_rows',files)=={}
    finally:con.close()


def test_source_change_during_merge_refuses_publication(tmp_path,monkeypatch):
    from app.services import sync_service as sync
    con=duckdb.connect(':memory:')
    try:
        for label,day in [('old','2026-03-05'),('incoming','2026-03-06')]:
            con.register('source',pa.Table.from_pylist([raw(day)]))
            con.execute('COPY source TO ? (FORMAT PARQUET)',[str(tmp_path/(label+'.parquet'))])
            con.unregister('source')
    finally:con.close()
    original=sync.hash_file;reads=0
    def changing(path):
        nonlocal reads
        if Path(path).name=='old.parquet':
            reads+=1
            if reads>1:return '0'*64
        return original(path)
    monkeypatch.setattr(sync,'hash_file',changing)
    with pytest.raises(ValueError,match='来源发生变化'):
        merge_daily(tmp_path/'incoming.parquet',str(tmp_path/'old.parquet'),tmp_path/'merged.parquet',
            '2026-03-05','2026-03-06',['2026-03-05','2026-03-06'],[{'thscode':'600001.SH','list_date':'2020-01-01'}])


def test_worker_increment_passes_proof_into_the_actual_publisher(transport,monkeypatch):
    from datetime import datetime
    from app.analytics import market_storage
    from app.services import sync_service as sync
    from app.core import get_market
    sync.execute_sync(start_job())
    seen=[];original=market_storage.reusable_partitions
    def observed(proof,*args,**kwargs):
        seen.append(proof)
        return original(proof,*args,**kwargs)
    monkeypatch.setattr(market_storage,'reusable_partitions',observed)
    monkeypatch.setattr(sync,'now_china',lambda:datetime(2026,10,20,18,tzinfo=sync.CHINA))
    sync.execute_sync(start_job('increment'))
    assert len(seen)==1 and isinstance(seen[0],MergePartitionProof)
    status=get_market().status()
    assert status['data_end']=='2026-10-20'
    assert status['storage_write']['daily.parquet']['encoded_partition_count']==1
