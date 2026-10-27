"""Moving bounded decoding to a child preserves validation and cache isolation."""
from copy import deepcopy
from hashlib import sha256
import json
import math
import zlib

import pytest

from app.services import condition_proof_cache as proof, strict_structure_cache as strict
from app.services import selection_compute as compute, selection_compute_pool as pool, strategy_periods as periods,strategies
from test_condition_proof_cache import scope
from test_selection_compute_fallback import warm_structures,ControlledSession
from test_selection_shared_basis import inputs


@pytest.mark.parametrize('fault',['checksum','scope','trailing','size','bomb','row_type','negative','nan'])
def test_transferred_bad_strict_record_is_a_miss_not_a_calculation(fault,scope,monkeypatch):
    value={'rows':[{'date':scope[3],'low':1.0,'close':2.0,'valid':True,'blocker':None}]}
    bound=proof.encode_record(scope,'strict-input',value,strict.VERSION)
    expected,record=bound;compressed,checksum,size=record
    if fault=='checksum':checksum='0'*64
    elif fault=='scope':expected+='different'
    elif fault=='trailing':compressed+=b'extra-stream'
    elif fault=='size':size+=1
    elif fault=='bomb':
        plain=b'x'*(proof.MAX_PLAIN+1);compressed=zlib.compress(plain);checksum=sha256(plain).hexdigest();size=proof.MAX_PLAIN
    else:
        if fault=='row_type':value['rows'][0]['valid']=1
        elif fault=='negative':value['rows'][0]['low']=-1.0
        else:value['rows'][0]['close']=float('nan')
        plain=json.dumps({'scope':expected,'value':value},separators=(',',':')).encode()
        compressed=zlib.compress(plain);checksum=sha256(plain).hexdigest();size=len(plain)
    damaged=(expected,(compressed,checksum,size))
    def forbidden(*args,**kwargs):pytest.fail('invalid strict inputs must never reach arithmetic')
    monkeypatch.setattr(strict,'calculate',forbidden)
    result=compute.prepare_job({'kind':'strict-structures','identity':('fixture',),
        'periods':{'D':{'record':damaged,'include_higher_lows':True,'boll_configs':()}}})
    assert result['values']=={'D':None}


def test_optional_child_record_rejection_keeps_complete_serial_result(warm_structures,monkeypatch):
    market,day,symbols,token,generation,contexts,plan,expected=warm_structures
    def reject_record(function,requests):
        values=[function(request) for request in requests]
        for value in values:value['values']={period:None for period in value['values']}
        return values
    session=ControlledSession(reject_record);monkeypatch.setattr(pool,'current_session',lambda:session)
    histories,ready,daily=strategies.selection_metric_batch(market,symbols,day,plan,generation,
        price_basis='forward',source_token=token)
    assert histories==daily=={} and ready==expected


def test_summary_copy_preserves_types_aliases_cycles_and_caller_isolation():
    child={'values':[1,1.0,-0.0,None],'tuple':(1,2),'set':{3}}
    source={'one':child,'alias':child,'scalar':-0.0};source['self']=source
    first=periods._copy_summary(source);second=periods._copy_summary(source)
    assert first is not source and first['self'] is first
    assert first['one'] is first['alias'] and first['one'] is not child
    assert type(first['one']['values'][0]) is int and type(first['one']['values'][1]) is float
    assert math.copysign(1,first['one']['values'][2])==-1 and math.copysign(1,first['scalar'])==-1
    assert type(first['one']['tuple']) is tuple and type(first['one']['set']) is set
    first['one']['values'].append(4)
    assert len(source['one']['values'])==len(second['one']['values'])==4
