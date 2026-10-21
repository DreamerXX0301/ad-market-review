"""Policy application scopes and same-day append-only reviews; no live data."""
import pytest

from app import core
from test_workspace import ws, make_strategy, add_one, advance


def detail(client, round_):
    return client.get('/api/v1/tracking-rounds/'+round_['round_id']).json()


def update(client, strategy, duration, apply=True):
    response=client.patch('/api/v1/strategies/'+strategy['strategy_id'],json={
        'policy':{'duration_sessions':duration},'apply_to_active_tracking':apply,
    })
    assert response.status_code==200,response.text
    return response.json()


def add_all(client, strategy):
    response=client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs',json={})
    assert response.status_code==200,response.text
    run=response.json()
    added=client.post('/api/v1/tracking-rounds',json={'run_id':run['run_id'],'instrument_ids':[row['instrument_id'] for row in run['items'] if row['decision']=='matched']})
    assert added.status_code==200,added.text
    return added.json()['items']


def test_explicit_default_policy_apply_updates_progress_without_resetting_entry(ws):
    market,client=ws;strategy=make_strategy(client);_,round_=add_one(client,strategy)
    advance(market,73);before=detail(client,round_)
    saved=update(client,strategy,12)
    after=detail(client,round_)
    assert saved['policy_application']['applied_count']==1
    assert saved['default_policy_tracking_count']==1 and saved['custom_policy_tracking_count']==0
    assert after['policy']['duration_sessions']==12 and after['trading_age']==before['trading_age']==3
    assert after['remaining_sessions']==9 and after['policy_review_pending'] is True
    for field in ('entry_business_date','entry_price','entry_policy_version','entry_basis','entry_strategy_version'):
        assert after[field]==before[field]
    applications=[event for event in after['events'] if event['event_type']=='policy_change']
    assert len(applications)==1 and applications[0]['evidence']['scope']=='strategy_default'
    assert after['events'][0]==before['events'][0]


def test_future_only_is_compatible_and_old_entry_defaults_remain_eligible(ws):
    market,client=ws;strategy=make_strategy(client);_,round_=add_one(client,strategy)
    # Existing API clients omit the explicit application flag.
    response=client.patch('/api/v1/strategies/'+strategy['strategy_id'],json={'policy':{'duration_sessions':20}})
    assert response.status_code==200 and response.json()['policy_application']['scope']=='future_only'
    assert detail(client,round_)['policy']['duration_sessions']==30
    saved=update(client,strategy,10,False)
    assert saved['policy_application']['applied_count']==0
    assert saved['default_policy_tracking_count']==1
    assert detail(client,round_)['policy']['duration_sessions']==30
    saved=update(client,strategy,15)
    assert saved['policy_application']['applied_count']==1
    assert detail(client,round_)['policy']['duration_sessions']==15
    again=update(client,strategy,18)
    assert again['policy_application']['applied_count']==1
    assert again['default_policy_tracking_count']==1


def test_default_application_preserves_custom_rounds_and_other_strategies(ws):
    market,client=ws
    strategy=make_strategy(client,{'field':'close','op':'>','value':0})
    following,custom=add_all(client,strategy)
    path='/api/v1/tracking-rounds/'+custom['round_id']+'/policy'
    changed=client.patch(path,json={'policy':{'duration_sessions':9}})
    assert changed.status_code==200,changed.text
    other_strategy=make_strategy(client);_,other=add_one(client,other_strategy)
    before_custom=detail(client,custom);before_other=detail(client,other)
    saved=update(client,strategy,14)
    assert saved['policy_application']['applied_count']==1
    assert saved['policy_application']['skipped_custom_count']==1
    assert saved['default_policy_tracking_count']==saved['custom_policy_tracking_count']==1
    assert detail(client,following)['policy']['duration_sessions']==14
    assert detail(client,custom)==before_custom
    assert detail(client,other)==before_other


def test_ended_round_and_all_its_events_are_untouched(ws):
    market,client=ws;strategy=make_strategy(client);_,round_=add_one(client,strategy)
    removed=client.post('/api/v1/tracking-rounds/'+round_['round_id']+'/remove',json={})
    assert removed.status_code==200,removed.text
    before=detail(client,round_)
    saved=update(client,strategy,8)
    assert saved['policy_application']['applied_count']==0
    assert detail(client,round_)==before


def test_shortened_duration_waits_for_review_and_same_day_new_policy_is_audited(ws):
    market,client=ws;strategy=make_strategy(client);_,round_=add_one(client,strategy)
    advance(market,73)
    first=client.post('/api/v1/tracking-rounds/review',json={})
    assert first.status_code==200,first.text
    before=detail(client,round_);events=list(before['events'])
    assert len([event for event in events if event['event_type']=='review'])==4
    update(client,strategy,2)
    pending=detail(client,round_)
    assert pending['status']=='tracking' and pending['duration_reached'] is True
    assert pending['remaining_sessions']==0 and pending['policy_review_pending'] is True
    again=client.post('/api/v1/tracking-rounds/review',json={})
    assert again.status_code==200,again.text
    assert again.json()['reviewed']==1 and again.json()['expired']==1
    after=detail(client,round_)
    assert after['status']=='expired' and after['end_business_date']==market.days[73]
    assert all(event in after['events'] for event in events)
    reviews=[event for event in after['events'] if event['event_type']=='review' and event['business_date']==market.days[73]]
    assert len(reviews)==2 and [event['evidence']['policy_version'] for event in reviews]==[1,2]
    assert client.post('/api/v1/tracking-rounds/review',json={}).json()['reviewed']==0


def test_round_policy_same_day_rereview_appends_once_per_version(ws):
    market,client=ws;strategy=make_strategy(client);_,round_=add_one(client,strategy)
    advance(market,71)
    assert client.post('/api/v1/tracking-rounds/review',json={}).json()['alerts']==0
    response=client.patch('/api/v1/tracking-rounds/'+round_['round_id']+'/policy',json={
        'policy':{'duration_sessions':30,'alerts':[{'field':'close_above_ma5','op':'==','value':True}]},
    })
    assert response.status_code==200,response.text
    reviewed=client.post('/api/v1/tracking-rounds/review',json={})
    assert reviewed.status_code==200,reviewed.text
    assert reviewed.json()['reviewed']==1 and reviewed.json()['alerts']==1
    assert client.post('/api/v1/tracking-rounds/review',json={}).json()['reviewed']==0
    assert client.get('/api/v1/tracking-events?event_type=alert').json()['total']==1


@pytest.mark.parametrize('payload',[
    {'policy':{'duration_sessions':10},'apply_to_active_tracking':'true'},
    {'apply_to_active_tracking':True},
    {'policy':{'duration_sessions':0},'apply_to_active_tracking':True},
])
def test_invalid_policy_application_is_atomic(ws,payload):
    market,client=ws;strategy=make_strategy(client);_,round_=add_one(client,strategy)
    before=detail(client,round_)
    response=client.patch('/api/v1/strategies/'+strategy['strategy_id'],json=payload)
    assert response.status_code==422,response.text
    assert detail(client,round_)==before
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM tracking_policy_version WHERE strategy_id=?',(strategy['strategy_id'],)).fetchone()[0]==1
        assert connection.execute('SELECT count(*) FROM tracking_policy_application').fetchone()[0]==0


def test_shorter_default_cannot_rewrite_prior_day_expiry(ws):
    market,client=ws;strategy=make_strategy(client,policy={'duration_sessions':2});_,round_=add_one(client,strategy)
    advance(market,74);update(client,strategy,20)
    response=client.post('/api/v1/tracking-rounds/review',json={})
    assert response.status_code==200,response.text
    after=detail(client,round_)
    assert after['status']=='expired' and after['end_business_date']==market.days[72]
    assert next(event for event in after['events'] if event['event_type']=='expire')['evidence']['policy_version']==1


def test_batch_application_failure_rolls_back_policy_and_every_round(ws,monkeypatch):
    from app.services import strategies
    market,client=ws;strategy=make_strategy(client,{'field':'close','op':'>','value':0})
    rounds=add_all(client,strategy);before=[detail(client,round_) for round_ in rounds]
    original=strategies.apply_tracking_policy;calls=[]
    def fail_second(*args,**kwargs):
        calls.append(args[1]['round_id'])
        if len(calls)==2:raise RuntimeError('controlled second application failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(strategies,'apply_tracking_policy',fail_second)
    with pytest.raises(RuntimeError,match='controlled second'):
        client.patch('/api/v1/strategies/'+strategy['strategy_id'],json={'policy':{'duration_sessions':12},'apply_to_active_tracking':True})
    assert [detail(client,round_) for round_ in rounds]==before
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM tracking_policy_application').fetchone()[0]==0
        assert connection.execute('SELECT count(*) FROM tracking_policy_version WHERE strategy_id=?',(strategy['strategy_id'],)).fetchone()[0]==1
