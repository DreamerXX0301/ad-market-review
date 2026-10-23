"""Trusted index fingerprints avoid reserializing expanded source documents."""
from copy import deepcopy
import json

import pytest

from app.services import market_service as market
from app.services import market_supplement as supplement
from app.services.market_supplement import SupplementCache


def publish(root,document):
    cache=SupplementCache(root);cache.publish(document)
    return cache


def document():
    return {'version':supplement.VERSION,'snapshot_id':'market-fingerprint','created_at_ms':1,
            'annual_pools':{'2026-10-22':{'date':'2026-10-22','items':[{'instrument_id':'000001.SZ','note':'原始证据'*600}]}},
            'public_pools':{'2026-10-22':{'date':'2026-10-22','items':[]}},'quality':'partial'}


def test_verified_index_fingerprint_tracks_same_timestamp_revisions_and_same_load_identity(tmp_path,monkeypatch):
    root=tmp_path/'supplement';cache=publish(root,document());monkeypatch.setattr(supplement,'_root',lambda:root)
    first,fingerprint=supplement.load_supplement_with_fingerprint('market-fingerprint')
    assert first==document()==supplement.load_supplement('market-fingerprint')
    assert fingerprint and hash(fingerprint)
    with market._gainers_lock:market._gainers_cache.clear()
    calls=[]
    monkeypatch.setattr(market,'gainers_rows',lambda *_args:(calls.append(1) or [],{}))
    with monkeypatch.context() as guard:
        guard.setattr(market,'digest',lambda *_:pytest.fail('verified expanded source must not be serialized again'))
        reference={'_supplement':first,'_supplement_cache_source':(first,fingerprint)}
        market.cached_gainers(object(),{'snapshot_id':'market-fingerprint','path':str(tmp_path)},reference,'2026-10-22',[],5)
        market.cached_gainers(object(),{'snapshot_id':'market-fingerprint','path':str(tmp_path)},reference,'2026-10-22',[],5)
        assert len(calls)==1
        changed=deepcopy(document());changed['annual_pools']['2026-10-22']['items'][0]['instrument_id']='000002.SZ'
        cache.publish(changed)
        second,new_fingerprint=supplement.load_supplement_with_fingerprint('market-fingerprint')
        assert second['created_at_ms']==first['created_at_ms'] and new_fingerprint!=fingerprint
        market.cached_gainers(object(),{'snapshot_id':'market-fingerprint','path':str(tmp_path)},
            {'_supplement':second,'_supplement_cache_source':(second,new_fingerprint)},'2026-10-22',[],5)
        assert len(calls)==2
    # A fingerprint copied alongside a different manual dictionary is not trusted.
    digests=[];real_digest=market.digest
    monkeypatch.setattr(market,'digest',lambda value:(digests.append(value),real_digest(value))[1])
    market.cached_gainers(object(),{'snapshot_id':'market-fingerprint','path':str(tmp_path)},
        {'_supplement':deepcopy(second),'_supplement_cache_source':(first,fingerprint)},'2026-10-22',[],5)
    assert digests
    with market._gainers_lock:market._gainers_cache.clear()


@pytest.mark.parametrize('corruption',['object','nested_payload','index','missing'])
def test_invalid_source_never_receives_a_trusted_fingerprint_after_warm_load(tmp_path,monkeypatch,corruption):
    root=tmp_path/'supplement';publish(root,document());monkeypatch.setattr(supplement,'_root',lambda:root)
    _,fingerprint=supplement.load_supplement_with_fingerprint('market-fingerprint');assert fingerprint
    head=json.loads((root/'head.json').read_text());index=root/head['artifact'];stored=json.loads(index.read_text())
    target=root/stored['annual_pools']['2026-10-22']['record_ref']
    if corruption=='nested_payload':
        pointer=json.loads(target.read_text())['__supplement_fields_v1']['items'];target=root/pointer['record_ref']
    elif corruption=='index':target=index
    if corruption=='missing':target.unlink()
    else:target.write_text('{}')
    value,identity=supplement.load_supplement_with_fingerprint('market-fingerprint')
    assert value['quality']=='unknown' and value['failure_reason'] and identity is None
    assert supplement.load_supplement('market-fingerprint')==value


def test_fingerprint_keeps_source_roots_projections_and_missing_snapshot_separate(tmp_path,monkeypatch):
    first=tmp_path/'first';second=tmp_path/'second';publish(first,document());publish(second,document())
    monkeypatch.setattr(supplement,'_root',lambda:first)
    whole,first_key=supplement.load_supplement_with_fingerprint('market-fingerprint')
    selected,projected_key=supplement.load_supplement_with_fingerprint('market-fingerprint',dates=iter(['2026-10-22']),fields=['annual_pools'])
    assert 'public_pools' not in selected and whole['annual_pools']==selected['annual_pools'] and first_key!=projected_key
    assert supplement.load_supplement_with_fingerprint('different')==({},None)
    monkeypatch.setattr(supplement,'_root',lambda:second)
    other,second_key=supplement.load_supplement_with_fingerprint('market-fingerprint')
    assert other==whole and first_key!=second_key
    monkeypatch.setattr(supplement,'_root',lambda:tmp_path/'empty')
    assert supplement.load_supplement_with_fingerprint()==({},None)
