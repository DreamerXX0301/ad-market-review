"""Display-only age bounds require distinct valid trades in the fixed snapshot."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.services.ladder_age import quote_age_evidence
from app.services.market_ladder import twenty_cm_state
from test_market_ladder import market,build,C,TODAY,DAYS

CALENDAR=['2026-10-12','2026-10-13','2026-10-14','2026-10-15','2026-10-16','2026-10-19','2026-10-20']
T=CALENDAR[-1]


def quote(day,symbol=C,**changes):
    return {'instrument_id':symbol,'date':day,'open':10.,'high':11.,'low':9.,'close':10.,'volume':100.,**changes}


def snapshot(tmp_path,rows,identifier='age-fixed'):
    folder=tmp_path/'published'/identifier;folder.mkdir(parents=True)
    (folder/'manifest.json').write_text(json.dumps({'summary':{'snapshot_id':identifier}}))
    pq.write_table(pa.Table.from_pylist(rows),folder/'daily.parquet')
    return SimpleNamespace(data_dir=tmp_path),{'snapshot_id':identifier,'path':str(folder)}


@pytest.mark.parametrize('prior_count,expected',[(4,None),(5,True),(6,True)])
def test_at_least_five_prior_dates_prove_only_a_listing_age_lower_bound(tmp_path,prior_count,expected):
    rows=[quote(day) for day in CALENDAR[:prior_count]]+[quote(T)]
    instance,status=snapshot(tmp_path,rows)
    proof=quote_age_evidence(instance,status,[C],CALENDAR,T)[C]
    identity={'kind':'stock','list_date':None}
    state,reason=twenty_cm_state(C,T,identity,CALENDAR,True,proof)
    assert state is expected and reason
    assert identity['list_date'] is None and proof['listing_date_known'] is False
    assert proof['prior_trade_dates_observed']==min(prior_count,5)
    assert proof['minimum_listing_session']==(6 if prior_count>=5 else None)
    assert len(proof['observed_trade_dates'])<=5


@pytest.mark.parametrize('mode',['duplicate','zero_volume','invalid_open','bad_high','null_close'])
def test_invalid_or_duplicate_date_is_not_counted_as_an_age_observation(tmp_path,mode):
    rows=[quote(day) for day in CALENDAR[:5]]+[quote(T)]
    if mode=='duplicate':rows.append(dict(rows[0]))
    elif mode=='zero_volume':rows[0]['volume']=0
    elif mode=='invalid_open':rows[0]['open']=0
    elif mode=='bad_high':rows[0]['high']=8
    else:rows[0]['close']=None
    instance,status=snapshot(tmp_path,rows)
    proof=quote_age_evidence(instance,status,[C],CALENDAR,T)[C]
    assert proof['prior_trade_dates_observed']==4 and proof['state']=='unknown'
    assert twenty_cm_state(C,T,{'list_date':None},CALENDAR,True,proof)[0] is None


def test_current_future_and_noncalendar_quotes_never_complete_five_prior_dates(tmp_path):
    rows=[quote(day) for day in CALENDAR[:4]]+[quote(T),quote('2026-10-21'),quote('2026-10-17'),quote(CALENDAR[4],'688001.SH')]
    instance,status=snapshot(tmp_path,rows)
    proof=quote_age_evidence(instance,status,[C],CALENDAR,T)[C]
    assert proof['prior_trade_dates_observed']==4
    assert all(day<T and day in CALENDAR for day in proof['observed_trade_dates'])


@pytest.mark.parametrize('listing',[T,'2026-10-21','not-a-date'])
def test_quote_age_never_overrides_a_conflicting_or_explicit_listing_date(tmp_path,listing):
    instance,status=snapshot(tmp_path,[quote(day) for day in CALENDAR])
    proof=quote_age_evidence(instance,status,[C],CALENDAR,T)[C]
    assert proof['state']=='proven'
    assert twenty_cm_state(C,T,{'list_date':listing},CALENDAR,True,proof)[0] is None


def test_fixed_snapshot_is_used_instead_of_later_head_and_bad_paths_stay_unknown(tmp_path):
    instance,old=snapshot(tmp_path,[quote(day) for day in CALENDAR],'age-old')
    _,new=snapshot(tmp_path,[quote(T)],'age-new')
    (tmp_path/'published/market-head.json').write_text(json.dumps(new))
    proof=quote_age_evidence(instance,old,[C],CALENDAR,T)[C]
    assert proof['state']=='proven' and proof['snapshot_id']=='age-old'
    mismatch={**old,'path':new['path']}
    assert quote_age_evidence(instance,mismatch,[C],CALENDAR,T)[C]['state']=='unknown'


def test_lower_bound_cannot_create_an_unconfirmed_event_or_bypass_the_regime_date(tmp_path):
    instance,status=snapshot(tmp_path,[quote(day) for day in CALENDAR])
    proof=quote_age_evidence(instance,status,[C],CALENDAR,T)[C]
    for confirmation in (None,False):
        assert twenty_cm_state(C,T,{'list_date':None},CALENDAR,confirmation,proof)[0] is None
    assert twenty_cm_state(C,'2020-08-21',{'list_date':None},CALENDAR,True,proof)[0] is False


def test_ladder_display_uses_fixed_quote_proof_without_changing_master_or_limit_event(market,tmp_path):
    for identity in market.master:identity['list_date']=None
    quotes=[quote(day) for day in DAYS]
    _,status=snapshot(tmp_path,quotes)
    status.update(data_end=TODAY)
    market.data_dir=tmp_path;market.status=lambda:deepcopy(status)
    before=deepcopy(market.master)
    result=build(market)
    assert [row['instrument_id'] for row in result['twenty_cm_items']]==[C]
    row=result['twenty_cm_items'][0]
    assert row['twenty_cm_age_evidence']['minimum_listing_session']==6
    assert row['limit_up_state'] is True and row['twenty_cm_age_evidence']['snapshot_id']=='age-fixed'
    assert market.master==before and len(market.history_calls)==1
