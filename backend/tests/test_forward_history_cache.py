"""A warm forward history cache contains immutable values, never decisions."""
import pytest

from app.domain.conditions import evaluate,metrics_for,validate
from app.domain.history_events import HistoryContext
from app.domain.strategy_prices import prepare_condition_prices,compact_source_token,price_cache_key
from app.services import forward_history_cache as cache
from app.services.history_events import cached_forward_contexts,contexts_for_batch
from test_forward_tracking_backtest import ForwardMarket
from test_forward_tracking_backtest import enable_forward_fixture
from test_workspace import ws


def event(threshold=1,to=28,field='volume_ratio'):
    return validate({'history_event':{'window':{'from':1,'to':to},'match':'latest',
        'where':{'field':field,'op':'==' if isinstance(threshold,bool) else '>','value':threshold},
        'then':{'field':'close','op':'>','right_event_field':'close'}}})


@pytest.fixture
def sample(tmp_path,monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path));cache.clear_cache()
    market=ForwardMarket(1200,1100,1185);symbol='600000.SH';day=market.days[-1]
    token=compact_source_token(market.verify_condition_sources())
    context=prepare_condition_prices(market.data[symbol],market.events[symbol],market.days,day,'forward',
        instrument_id=symbol,actions_coverage={'known':True,'source_token':token,'snapshot_id':'forward-fixture'})
    tree=event();scope=cache.window_scope([tree],market.days,day)
    history=HistoryContext(market.data[symbol],market.days,day,'forward-fixture',retain_start=scope['first'],price_context=context)
    key=cache.key_for(symbol,day,'forward-fixture',context['cache_key'],scope)
    yield market,symbol,day,token,context,tree,scope,history,key
    cache.clear_cache()


def test_full_checks_and_original_event_prices_match_and_mutations_cannot_escape(sample):
    market,symbol,day,token,prices,tree,scope,history,key=sample
    metrics=metrics_for(market.data[symbol],day,market.days,price_context=prices)
    expected=evaluate(tree,metrics,history.at(day))
    history.event_cache['must-not-save']='decision';cache.remember(key,history,scope)
    first=cache.restore(key,market.days,{d:i for i,d in enumerate(market.days)})
    assert first.event_cache=={}
    assert evaluate(tree,metrics,first.at(day))==expected
    selected=expected['selected_event']['date']
    first.quotes[selected]['close']=-1;first.raw_quotes[selected]['close']=-2
    first.daily[selected]['volume_ratio']=-3;first.event_cache['bad']='policy'
    first.calendar.append('2099-01-01');first.positions[day]=-1
    second=cache.restore(key,market.days,{d:i for i,d in enumerate(market.days)})
    assert second.event_cache=={} and evaluate(tree,metrics,second.at(day))==expected
    assert len(second.price_context)<8 and 'raw_rows' not in second.price_context and 'price_rows' not in second.price_context
    # A different threshold reuses pure data, then evaluates the new rule.
    changed=event(2)
    assert cache.window_scope([changed],market.days,day)==scope
    assert evaluate(changed,metrics,second.at(day))['result'] is False
    outer={'all':[tree,{'field':'topic_ids','op':'contains','value':'today-topic'}]}
    assert evaluate(outer,{**metrics,'topic_ids':['today-topic']},second.at(day))['result'] is True
    assert evaluate(outer,{**metrics,'topic_ids':[]},second.at(day))['result'] is False


def test_source_anchor_fields_scope_and_namespace_are_cache_boundaries(sample,monkeypatch,tmp_path):
    market,symbol,day,token,prices,tree,scope,history,key=sample
    cache.remember(key,history,scope)
    assert cached_forward_contexts([symbol],[tree],market.days,day,'forward-fixture',token)
    assert not cached_forward_contexts([symbol],[tree],market.days,day,'different-generation',token)
    assert not cached_forward_contexts([symbol],[tree],market.days,day,'forward-fixture',{'verified_closure_sha256':'f'*64})
    assert not cached_forward_contexts([symbol],[tree],market.days,market.days[-2],'forward-fixture',token)
    assert not cached_forward_contexts([symbol],[event(to=27)],market.days,day,'forward-fixture',token)
    assert not cached_forward_contexts([symbol],[event(field='macd_dif')],market.days,day,'forward-fixture',token)
    assert not cached_forward_contexts([symbol],[tree],market.days,day,'forward-fixture',None)
    with monkeypatch.context() as versioned:
        versioned.setattr(cache,'INDICATOR_VERSION','different-indicator-algorithm')
        assert not cached_forward_contexts([symbol],[tree],market.days,day,'forward-fixture',token)
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path/'other'))
    assert not cached_forward_contexts([symbol],[tree],market.days,day,'forward-fixture',token)


def test_pool_proofs_are_reloaded_and_cached_service_hit_does_not_read_histories(sample):
    market,symbol,day,token,prices,tree,scope,history,key=sample
    pool_tree=event(field='limit_up',threshold=True)
    pool_tree['history_event']['where']={'field':'limit_up','op':'==','value':True}
    scope=cache.window_scope([pool_tree],market.days,day)
    key=cache.key_for(symbol,day,'forward-fixture',prices['cache_key'],scope)
    for values in history.daily.values():values.update(limit_up=True,limit_up_evidence={'stale':True})
    cache.remember(key,history,scope)
    cached=cached_forward_contexts([symbol],[pool_tree],market.days,day,'forward-fixture',token)
    class Evidence:
        def historical_metrics(self,symbol,observed,quote):return {'limit_up':False,'limit_up_reason':'new-pool-proof'}
    class Offline:
        def __getattr__(self,name):raise AssertionError('cache hit must not read '+name)
    result=contexts_for_batch(Offline(),[symbol],[pool_tree],market.days,day,price_basis='forward',
        prepared_contexts=cached,limit_evidence=Evidence())
    assert all(values['limit_up'] is False and values['limit_up_reason']=='new-pool-proof' for values in result[symbol].daily.values())


def test_eviction_corruption_and_decompression_limits_are_misses(sample,monkeypatch):
    market,symbol,day,token,prices,tree,scope,history,key=sample
    cache.remember(key,history,scope)
    compressed,digest=cache._cache[key]
    cache._cache[key]=(bytes([compressed[0]^1])+compressed[1:],digest)
    assert cache.restore(key,market.days,{}) is None
    assert cache.cache_stats()['compressed_bytes']==0
    with monkeypatch.context() as limited:
        limited.setattr(cache,'MAX_BYTES',len(compressed)*2)
        for index in range(3):
            other=list(key);other[2]=f'{index:06d}.SH';cache.remember(tuple(other),history,scope)
        assert cache.cache_stats()['entries']==2
        assert cache.cache_stats()['compressed_bytes']<=cache.MAX_BYTES
    cache.clear_cache()
    cache.remember(key,history,scope)
    monkeypatch.setattr(cache,'MAX_ENTRIES',1)
    other=list(key);other[2]='600999.SH';other=tuple(other)
    cache.remember(other,history,scope)
    assert cache.restore(key,market.days,{}) is None
    assert cache.restore(other,market.days,{d:i for i,d in enumerate(market.days)}) is not None
    monkeypatch.setattr(cache,'MAX_DECOMPRESSED',10)
    assert cache.restore(other,market.days,{}) is None


def test_compressed_typical_window_holds_5582_securities_within_budget(sample):
    market,symbol,day,token,prices,tree,scope,history,key=sample
    for index in range(5582):
        other=list(key);other[2]=f'{index:06d}.SH'
        cache.remember(tuple(other),history,scope)
    stats=cache.cache_stats()
    assert stats['entries']==5582 and stats['compressed_bytes']<=64*1024*1024


def test_only_verified_forward_contexts_can_be_admitted(sample):
    market,symbol,day,token,prices,tree,scope,history,key=sample
    cache.remember(None,history,scope)
    history.price_basis='unavailable';cache.remember(key,history,scope)
    assert cache.cache_stats()['entries']==0


def test_forward_strategy_warm_history_skips_full_reads_and_keeps_full_checks(ws,monkeypatch):
    market,client=ws;enable_forward_fixture(market,monkeypatch);cache.clear_cache()
    created=client.post('/api/v1/strategies',json={'name':'暖历史窗口','price_basis':'forward','definition':event(.5)})
    assert created.status_code==200,created.text
    path='/api/v1/strategies/'+created.json()['strategy_id']+'/runs'
    cold=client.post(path,json={});assert cold.status_code==200,cold.text
    assert cache.cache_stats()['entries']==2
    def never(*args,**kwargs):pytest.fail('warm verified summary/history must not reread full quotes or actions')
    monkeypatch.setattr(market,'bars',never)
    monkeypatch.setattr(market,'actions_for_instruments',never)
    warm=client.post(path,json={});assert warm.status_code==200,warm.text
    for old,new in zip(cold.json()['items'],warm.json()['items']):
        assert {k:v for k,v in old.items() if k!='run_id'}=={k:v for k,v in new.items() if k!='run_id'}


def test_forward_limit_predicate_does_not_load_unused_raw_panel_and_none_keeps_fast_path(ws,monkeypatch):
    from app.services import strategies
    market,client=ws;enable_forward_fixture(market,monkeypatch)
    original=strategies.market_panel;calls=[]
    def panel(day):calls.append(day);return original(day)
    monkeypatch.setattr(strategies,'market_panel',panel)
    definition={'any':[{'field':'limit_up','op':'==','value':True},{'field':'close','op':'>','value':0}]}
    results=[]
    for basis in ('none','forward'):
        created=client.post('/api/v1/strategies',json={'name':'盘面spy'+basis,'price_basis':basis,'definition':definition})
        assert created.status_code==200,created.text
        calls.clear();run=client.post('/api/v1/strategies/'+created.json()['strategy_id']+'/runs',json={})
        assert run.status_code==200,run.text
        assert bool(calls)==(basis=='none')
        results.append([(row['instrument_id'],row['decision'],row['close_price']) for row in run.json()['items']])
    assert results[0]==results[1]
