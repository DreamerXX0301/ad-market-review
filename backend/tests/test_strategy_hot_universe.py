"""Current hot universes freeze once and never masquerade as PIT backtests."""
from copy import deepcopy
import json

import pytest

from app import core
from app.analytics.backtest_engine import availability, prepare
from app.services import strategy_hot_universe, strategies
from app.services.hot_topics import rank_hot_topics
from test_workspace import ws
from test_topic_members import memberships
from test_emotion_hot_topics import hot_source


CONFIG = {'window_sessions': 7, 'top_n': 10, 'kind': 'all'}


def create(client, **extra):
    response = client.post('/api/v1/strategies', json={'name': '近期热点筛选',
        'definition': {'field': 'close', 'op': '>', 'value': 0}, 'hot_universe': CONFIG, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def test_configuration_is_versioned_and_can_be_disabled_without_changing_old_version(ws):
    _, client = ws
    strategy = create(client)
    assert strategy['hot_universe'] == CONFIG
    changed = client.patch('/api/v1/strategies/'+strategy['strategy_id'], json={'hot_universe': None}).json()
    assert changed['version'] == 2 and changed['hot_universe'] is None
    with core.connect() as connection:
        versions = [dict(row) for row in connection.execute('SELECT * FROM strategy_version WHERE strategy_id=? ORDER BY version', (strategy['strategy_id'],))]
    assert json.loads(versions[0]['hot_universe_json']) == CONFIG
    assert versions[0]['evaluator_version'] == 'workspace-v14-current-hot-universe'
    assert versions[1]['hot_universe_json'] is None


@pytest.mark.parametrize('value', [False, [], {'window_sessions': 0}, {'window_sessions': 61}, {'window_sessions': True},
    {'top_n': 0}, {'top_n': 51}, {'top_n': 1.5}, {'kind': 'unknown'}, {'historical': True}])
def test_invalid_configuration_is_rejected_before_persistence(ws, value):
    _, client = ws
    result = client.post('/api/v1/strategies', json={'name':'坏配置', 'definition':{'field':'close','op':'>','value':0},'hot_universe':value})
    assert result.status_code == 422
    assert client.get('/api/v1/strategies').json()['total'] == 0


def test_rank_window_and_kind_use_only_dated_confirmed_events():
    calendar = ['2026-10-19', '2026-10-20', '2026-10-21', '2026-10-22', '2026-10-23']
    classes = [{'topic_id':'concept','name':'概念','kind':'concept','instrument_ids':['A']},
               {'topic_id':'industry','name':'行业','kind':'industry','instrument_ids':['B']}]
    events = [{'instrument_id':'A','date':calendar[0]}, {'instrument_id':'B','date':calendar[-1]},
              {'instrument_id':'B','date':calendar[-1]}, {'instrument_id':'A','date':'2026-10-26'}]
    result = rank_hot_topics(classes, events, calendar, calendar[-1], window_sessions=2, kind='all')
    assert [item['topic_id'] for item in result['items']] == ['industry']
    assert result['items'][0]['event_count'] == 1
    assert result['window']['start'] == calendar[-2] and result['window']['complete']
    assert rank_hot_topics(classes, events, calendar, calendar[-1], window_sessions=2, kind='concept')['items'] == []


def test_real_stored_pool_adapter_reads_one_bounded_batch_and_freezes_members(hot_source):
    market, _, identifiers, _, calls, calendar = hot_source
    evidence, by_stock = strategy_hot_universe.freeze({'window_sessions':7,'top_n':1,'kind':'industry'}, expected_day=market.entry)
    assert len(calls) == 1 and calls[0][1:] == (calendar[-7], market.entry)
    assert evidence['items'][0]['topic_id'] == identifiers['行业甲']
    assert evidence['complete'] is True
    assert evidence['member_ids'] == ['300001.SZ','600001.SH','688001.SH']
    assert by_stock['600001.SH'] == [identifiers['行业甲']]
    assert '_selected_memberships' not in evidence
    assert len(json.dumps(evidence)) < 10000


def test_hot_coverage_explains_missing_quotes_and_source_conflicts(hot_source,monkeypatch):
    market,_,_,document,_,calendar=hot_source
    original=market.history_for_instruments
    def history(symbols,*,start,end):
        result=original(symbols,start=start,end=end)
        result['600001.SH']=[row for row in result.get('600001.SH',[]) if row['date']!=calendar[-1]]
        return result
    monkeypatch.setattr(market,'history_for_instruments',history)
    document['annual_pools'][calendar[-1]]['items'][0]['last_price']=999
    evidence,_=strategy_hot_universe.freeze(CONFIG,expected_day=market.entry)
    assert evidence['complete'] is False
    assert evidence['coverage']['unverified_candidate_stock_days']==2
    assert evidence['coverage']['unverified_candidate_stock_day_reasons']=={'missing_raw_close':1,'source_conflict':1}


def test_hot_collection_uses_selected_dates_and_256_security_batches(hot_source,monkeypatch):
    from app.services import market_supplement,topics
    market,_,_,_,_,calendar=hot_source
    symbols=[f'{600100+index:06d}.SH' for index in range(600)]
    monkeypatch.setattr(market,'instruments',lambda **kwargs:[{'instrument_id':symbol,'code':symbol[:6],'kind':'stock'} for symbol in symbols])
    monkeypatch.setattr(market,'topics',lambda:[{'topic_id':'large-industry','name':'大样本行业','kind':'industry','members_complete':True,'instrument_ids':symbols}])
    topics.seed_topics()
    window=calendar[-7:];reads=[];batches=[]
    document={'annual_pools':{day:{'date':day,'pool_kind':'limitup','source':'fuyao','complete':True,'pagination_complete':True,
        'raw_codes':[symbol[:6] for symbol in symbols],'ambiguous_code_rows':0,'quarantine':[],
        'items':[{'instrument_id':symbol,'thscode':symbol,'date':day,'last_price':10} for symbol in symbols]} for day in window}}
    def supplement(snapshot,*,dates,fields):
        reads.append((tuple(dates),tuple(fields)))
        return document
    def history(selected,*,start,end):
        assert len(selected)<=256
        batches.append((list(selected),start,end))
        return {symbol:[{'date':day,'close':10} for day in window] for symbol in selected}
    monkeypatch.setattr(market_supplement,'load_supplement',supplement)
    monkeypatch.setattr(market,'history_for_instruments',history)
    monkeypatch.setattr(market,'bars',lambda *args,**kwargs:pytest.fail('must not read full per-stock histories'))
    evidence,by_stock=strategy_hot_universe.freeze({'window_sessions':7,'top_n':1,'kind':'industry'},expected_day=market.entry)
    assert reads==[(tuple(window),('public_pools','annual_pools','annual_down_pools'))]
    assert len(batches)==3 and [len(batch[0]) for batch in batches]==[256,256,88]
    assert all(batch[1:]==(window[0],market.entry) for batch in batches)
    assert len(by_stock)==600 and evidence['items'][0]['event_count']==4200
    assert len(json.dumps(evidence))<25000


@pytest.mark.parametrize('technical,hot_complete,member,expected', [
    (True,True,True,True),(True,True,False,False),(True,False,True,None),
    (False,False,True,False),(None,True,False,False),(None,True,True,None)])
def test_three_valued_and_does_not_turn_failed_technical_condition_into_match(technical,hot_complete,member,expected):
    evidence={'complete':hot_complete,'reason':None if hot_complete else '覆盖不足'}
    result=strategy_hot_universe.combine({'result':technical},'A',evidence,{'A':['topic']} if member else {})
    assert result['result'] is expected
    assert result['checks'][1]['evidence_ref'] == 'run.universe_definition.hot_universe'


def receipt(day, complete=True):
    return {'version':strategy_hot_universe.VERSION,'config':CONFIG,'business_date':day,'window':{'complete':complete},
            'coverage':{'complete':complete},'items':[{'topic_id':'hot','name':'热点','event_count':3}],
            'member_ids':['600001.SH'],'membership_sha256':'a'*64,'complete':complete,'reason':None if complete else '覆盖不足'}


def test_selection_computes_once_keeps_frozen_evidence_and_unknown_counts(ws,monkeypatch):
    market,client=ws
    calls=[]
    def frozen(config, *, expected_day):
        calls.append(config)
        return receipt(expected_day),{'600001.SH':['hot']}
    monkeypatch.setattr(strategy_hot_universe,'freeze',frozen)
    strategy=create(client)
    response=client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs',json={})
    assert response.status_code==200,response.text
    run=response.json()
    assert len(calls)==1 and run['matched_count']==1 and run['unmatched_count']==1
    assert run['universe_definition']['hot_universe']['member_ids']==['600001.SH']
    saved=deepcopy(run['universe_definition'])
    # Editing the strategy cannot alter the old run's rank/member receipt.
    client.patch('/api/v1/strategies/'+strategy['strategy_id'],json={'hot_universe':None})
    assert client.get('/api/v1/strategy-runs/'+run['run_id']).json()['universe_definition']==saved
    monkeypatch.setattr(strategy_hot_universe,'freeze',lambda config,expected_day:(receipt(expected_day,False),{'600001.SH':['hot']}))
    unknown=create(client,definition={'field':'change_pct','op':'>','value':0})
    run=client.post('/api/v1/strategies/'+unknown['strategy_id']+'/runs',json={}).json()
    assert run['unknown_count']==1 and run['unmatched_count']==1 and run['matched_count']==0


def test_member_edits_during_ranking_fail_without_committing_mixed_run(ws,monkeypatch):
    _,client=ws
    def frozen(config,*,expected_day):
        assert client.post('/api/v1/topics',json={'name':'并发修改','instrument_ids':['600001.SH']}).status_code==200
        return receipt(expected_day),{'600001.SH':['hot']}
    monkeypatch.setattr(strategy_hot_universe,'freeze',frozen)
    strategy=create(client)
    response=client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs',json={})
    assert response.status_code==409 and '成员已变化' in response.json()['detail']
    assert client.get('/api/v1/strategy-runs').json()['total']==0


def test_backtest_availability_and_execution_explicitly_reject_current_proxy(ws):
    market,_=ws
    strategy={'definition':{'field':'close','op':'>','value':0},'period':'D','hot_universe':CONFIG}
    result=availability(market,strategy)
    assert result['available'] is False and '不能回填历史回测' in result['reason']
    with pytest.raises(ValueError,match='不能回填历史回测'):
        prepare(market,strategy,market.days[50],market.entry)


def test_stock_detail_can_create_and_add_current_topics_without_quote_reads(ws,monkeypatch):
    market,client=ws
    from app.services import topic_members
    monkeypatch.setattr(topic_members,'get_market',lambda:market)
    client.post('/api/v1/topics/initialize',json={})
    topic=client.post('/api/v1/topics',json={'name':'个股新题材','instrument_ids':['000001.SZ'],'description':'公开资料研究'}).json()
    def forbidden(*args,**kwargs):raise AssertionError('membership refresh must not scan quote history')
    for name in ('panel','bars','history_panel','history_for_instruments'):
        monkeypatch.setattr(market,name,forbidden,raising=False)
    current=client.get('/api/v1/stock-topic-memberships/000001.SZ')
    assert current.status_code==200,current.text
    tag=next(tag for tag in current.json()['current_topics'] if tag['topic_id']==topic['topic_id'])
    assert tag['origin']=='manual' and tag['manual_override']=='include' and not tag['provider_member']
    assert current.json()['current_topics_observed_date']==market.entry
    result=client.post('/api/v1/topic-members/associate',json={'topic_ids':[topic['topic_id']],'instrument_ids':['600001.SH']})
    assert result.status_code==200 and result.json()['added_count']==1
    assert topic['topic_id'] in [row['topic_id'] for row in client.get('/api/v1/stock-topic-memberships/600001.SH').json()['current_topics']]
    assert client.get('/api/v1/stock-topic-memberships/999999.SH').status_code==404
