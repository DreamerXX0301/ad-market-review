"""Optimized extrema must equal the previous nine-quote min/max exactly."""
from collections import deque
from math import copysign,isnan
import random

import pytest

from app.analytics import indicators
from app.core import dumps
from test_instrument_detail_projection import DetailMarket

class OriginalRange:
    def __init__(self):self.clear()
    def clear(self):self.rows=deque(maxlen=9)
    def observe(self,row):
        self.rows.append(row)
        if len(self.rows)<9:return None
        return min(r['low'] for r in self.rows),max(r['high'] for r in self.rows)

def same(left,right):
    if left is None or right is None:assert left is right;return
    for a,b in zip(left,right):
        assert type(a) is type(b)
        if isinstance(a,float) and isnan(a):assert isnan(b)
        else:
            assert a==b
            if a==0:assert copysign(1,a)==copysign(1,b)

@pytest.mark.parametrize('special',[None,-0.0,0,False,float('nan'),float('inf'),-float('inf'),10**400,'legacy'])
def test_ties_nonfinite_legacy_values_resets_and_window_exit(special):
    rng=random.Random(1030);fast=indicators._KDJRange();original=OriginalRange()
    for i in range(160):
        if i in (37,81):fast.clear();original.clear()
        value=rng.choice([1.,1,2.,-0.0,0.0])
        row={'low':value,'high':value+2}
        if i in (12,60,100):row['low']=special
        try:expected=original.observe(row)
        except (TypeError,ValueError,OverflowError) as error:
            with pytest.raises(type(error)):fast.observe(row)
        else:same(fast.observe(row),expected)

@pytest.mark.parametrize('mode',['normal','ties','missing','actions','duplicate'])
@pytest.mark.parametrize('tail',[None,2])
def test_all_indicator_values_match_the_original_range_after_full_seed(monkeypatch,mode,tail):
    market=DetailMarket(320);rows=market.rows
    if mode=='ties':
        for row in rows:row.update(open=10.,low=9.,high=11.,close=10.)
    if mode=='missing':rows.pop(75);rows.pop(150)
    if mode=='actions':rows[73]['corporate_action']=True;rows[266]['corporate_action']=True
    if mode=='duplicate':rows.insert(79,dict(rows[78]))
    source=dumps(rows);actual=indicators.enrich_bars(rows,market.days,tail=tail)
    monkeypatch.setattr(indicators,'_KDJRange',OriginalRange)
    expected=indicators.enrich_bars(rows,market.days,tail=tail)
    assert dumps(actual)==dumps(expected)
    assert dumps(rows)==source
