"""Same-day generation changes cannot publish mixed-input selections/reviews."""
import json
import pytest
from fastapi import HTTPException

from app import core
from app.services import strategies,tracking
from test_workspace import ws,make_strategy,add_one,advance


def publish_revision(market,module,suffix):
    path=core.published_dir()/('guard-'+module+'-'+suffix+'.json')
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps({'fixture':suffix}))
    return core.publish_snapshot(module=module,job_id='fixture-job',start=market.days[0],end=market.days[-1],
        artifacts=[{'object_key':path.name,'role':module,'sha256':core.hash_file(path),'byte_count':path.stat().st_size,'row_count':1}],
        quality='partial',scope='all',snapshot_id='guard-'+module+'-'+suffix)


@pytest.mark.parametrize('module',['analysis_bars','market_review'])
def test_same_day_generation_switch_rejects_new_run_and_preserves_saved_run(ws,monkeypatch,module):
    market,client=ws
    if module=='market_review':publish_revision(market,module,'initial')
    strategy=make_strategy(client)
    saved=client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs',json={}).json()
    before=client.get('/api/v1/strategy-runs/'+saved['run_id']).json()
    # A genuine rule edit admits a new run; switching a published input during
    # that admitted computation must still fail without rewriting the old run.
    changed_rule=client.patch('/api/v1/strategies/'+strategy['strategy_id'],
        json={'definition':{'field':'volume_ratio','op':'>=','value':2.5}})
    assert changed_rule.status_code==200,changed_rule.text
    old_evaluate=strategies.evaluate;changed=False
    def evaluate(*args,**kwargs):
        nonlocal changed
        result=old_evaluate(*args,**kwargs)
        if not changed:changed=True;publish_revision(market,module,'replacement')
        return result
    monkeypatch.setattr(strategies,'evaluate',evaluate)
    response=client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs',json={})
    assert response.status_code==409 and '代际' in response.json()['detail']
    assert client.get('/api/v1/strategy-runs/'+saved['run_id']).json()==before
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM selection_run').fetchone()[0]==1
        assert connection.execute('SELECT count(*) FROM selection_candidate').fetchone()[0]==len(saved['items'])
        assert connection.execute("SELECT count(*) FROM job WHERE kind='selection' AND status='failed'").fetchone()[0]==1


@pytest.mark.parametrize('stage',['before_impl','after_bars'])
def test_tracking_generation_change_writes_no_mixed_review_and_keeps_old_events(ws,monkeypatch,stage):
    market,client=ws;strategy=make_strategy(client,policy={'alerts':[{'field':'close','op':'>','value':0}]})
    _,round_=add_one(client,strategy)
    tracking.review_tracking({'strategy_id':strategy['strategy_id']})
    with core.connect() as connection:before=[tuple(row) for row in connection.execute('SELECT * FROM tracking_event ORDER BY event_id')]
    advance(market,71)
    if stage=='before_impl':
        original=tracking._review_tracking_impl
        def implementation(*args,**kwargs):
            publish_revision(market,'analysis_bars','replaced-before-impl')
            return original(*args,**kwargs)
        monkeypatch.setattr(tracking,'_review_tracking_impl',implementation)
    else:
        original=market.bars;changed=False
        def bars(*args,**kwargs):
            nonlocal changed
            result=original(*args,**kwargs)
            if not changed:changed=True;publish_revision(market,'analysis_bars','replaced-after-bars')
            return result
        monkeypatch.setattr(market,'bars',bars)
    with pytest.raises(HTTPException) as caught:tracking.review_tracking({'strategy_id':strategy['strategy_id']})
    assert caught.value.status_code==409
    with core.connect() as connection:
        assert [tuple(row) for row in connection.execute('SELECT * FROM tracking_event ORDER BY event_id')]==before
        assert connection.execute('SELECT status FROM tracking_round WHERE round_id=?',(round_['round_id'],)).fetchone()[0]=='tracking'
