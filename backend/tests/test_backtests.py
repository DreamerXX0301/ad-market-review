from datetime import date,timedelta
import math
import pytest
from app.analytics.backtest_engine import simulate,config_checked,availability,run_backtest


def execution_fixture(n=3):
    dates=[(date(2026,1,1)+timedelta(days=i)).isoformat() for i in range(n+1)]
    symbol='600000.SH'
    quotes={'_previous':{symbol:{'close':10}}}
    for i,d in enumerate(dates[1:]):
        o=10.1+i*.2
        quotes[d]={symbol:{'date':d,'open':o,'high':o+.2,'low':o-.1,'close':o+.1,'volume':100000,'amount':1000000}}
    signals={dates[0]:[{'instrument_id':symbol}]}
    config=config_checked({'commission_bps':0,'min_commission':0,'sell_tax_bps':0,'slippage_bps':0,'max_positions':1,'position_pct':100,'hold_days':1,'stop_loss_pct':0,'take_profit_pct':0})
    return dates,quotes,signals,config


def test_next_open_tplus1_and_cash_pnl_reconcile():
    dates,quotes,signals,config=execution_fixture()
    result=simulate(dates,quotes,signals,config)
    t=result['trades'][0]
    assert t['entry_date']==dates[1] and t['exit_date']==dates[2]
    assert t['entry_price']==10.1 and t['exit_price']==10.3
    assert t['quantity']==9900
    assert t['net_pnl']==1980
    assert result['stats']['final_equity']==101980
    assert result['stats']['trade_count']==1 and result['stats']['win_rate']==100
    assert result['stats']['payoff_ratio'] is None
    assert result['equity'][0]['equity']==100000 and len(result['equity'])==4
    assert result['stats']['trading_days']==3
    assert result['stats']['annual_return']==pytest.approx(((101980/100000)**(252/3)-1)*100)


def test_period_end_no_force_sale_and_undefined_closed_stats():
    dates,q,s,c=execution_fixture();c['hold_days']=30
    result=simulate(dates,q,s,c)
    assert not result['trades'] and result['stats']['holding_count']==1
    assert result['stats']['win_rate'] is None and result['stats']['payoff_ratio'] is None
    final_positions=[p for p in result['positions'] if p['date']==dates[-1]]
    assert result['stats']['final_equity']==pytest.approx(result['equity'][-1]['cash']+sum(p['market_value'] for p in final_positions))


def test_open_limit_up_rejected_and_last_day_signal_recorded():
    dates,q,s,c=execution_fixture();symbol='600000.SH'
    q[dates[1]][symbol].update(open=11,high=11,low=11,close=11)
    s[dates[-1]]=[{'instrument_id':symbol}]
    result=simulate(dates,q,s,c)
    assert not result['fills']
    assert any('开盘涨停' in o['reason'] for o in result['orders'])
    assert result['orders'][-1]['status']=='not_filled'


def test_sell_limit_down_keeps_pending_and_tries_next_day():
    dates,q,s,c=execution_fixture();symbol='600000.SH'
    q[dates[2]][symbol].update(open=9.18,high=9.3,low=9.18,close=9.2)
    q[dates[3]][symbol].update(open=9.3,high=9.5,low=9.2,close=9.4)
    result=simulate(dates,q,s,c)
    assert result['trades'][0]['exit_date']==dates[3]
    assert any('开盘跌停' in o['reason'] for o in result['orders'])
    assert result['stats']['trade_count']==1


def test_drawdown_uses_initial_capital_and_fees_are_not_lost():
    dates,q,s,c=execution_fixture();c.update(commission_bps=0,min_commission=10,hold_days=30)
    for day in dates[1:]:q[day]['600000.SH'].update(open=10,high=10.1,low=9.9,close=10)
    result=simulate(dates,q,s,c)
    assert result['stats']['total_fees']==10
    assert result['stats']['final_equity']==99990
    assert result['stats']['max_drawdown']==pytest.approx(.01)
    assert all(r['cash']>=0 for r in result['equity'])


@pytest.mark.parametrize('value',[True,None,'100000',float('nan'),float('inf'),-1])
def test_nonfinite_or_coerced_capital_is_rejected(value):
    with pytest.raises(ValueError):config_checked({'initial_capital':value})


def test_missing_or_coerced_range_parameters_rejected():
    with pytest.raises(ValueError):config_checked({'hold_days':1.5})
    with pytest.raises(ValueError):config_checked({'exit_ma':17})


class CalendarOnly:
    def calendar(self):return [(date(2025,1,1)+timedelta(days=i)).isoformat() for i in range(242)]


def test_bounds_do_not_promise_unavailable_history():
    m=CalendarOnly()
    for field,value in [('ma250',10),('turnover_ratio_pct',5),('topic_ids','topic-ai'),('limit_up_1y',True)]:
        strategy={'period':'D','definition':{'all':[{'field':field,'op':'contains' if field=='topic_ids' else '==' if isinstance(value,bool) else '>','value':value}]}}
        assert availability(m,strategy)['available'] is False


def test_daily_warmup_bound_is_strict_and_weekly_long_ma_rejected():
    m=CalendarOnly();s={'period':'D','definition':{'field':'close','op':'>','right_field':'ma60'}}
    bounds=availability(m,s)
    assert bounds['warmup_bars']==60 and bounds['min_date']==m.calendar()[60]
    s['period']='W'
    assert availability(m,s)['available'] is False


@pytest.mark.parametrize('listing',[None,'','not-a-date'])
def test_all_missing_listing_dates_are_unavailable_before_simulation(listing):
    class MissingListing(CalendarOnly):
        def instruments(self,limit=10000):return [{'instrument_id':'600000.SH','kind':'stock','name':'测试股票','list_date':listing}]
    bounds=availability(MissingListing(),{'definition':{'field':'close','op':'>','value':0}})
    assert bounds['available'] is False and '上市日期' in bounds['reason']
    assert bounds['eligible_count']==0
    assert bounds['listing_date_coverage']['known_listing_date_count']==0


def test_partial_listing_dates_remain_explicit_and_known_listing_can_backtest():
    class PartialListing(CalendarOnly):
        def instruments(self,limit=10000):return [{'instrument_id':'600000.SH','kind':'stock','name':'测试甲','list_date':'2020-01-01'},
                                                {'instrument_id':'600001.SH','kind':'stock','name':'测试乙','list_date':None}]
    bounds=availability(PartialListing(),{'definition':{'field':'close','op':'>','value':0}})
    assert bounds['available'] is True and bounds['eligible_count'] is None
    assert bounds['listing_date_coverage']['known_listing_date_count']==1
    assert bounds['listing_date_coverage']['missing_listing_date_count']==1
    assert any('上市日期' in value for value in bounds['warnings'])


@pytest.mark.parametrize('definition',[
    {'field':'board','op':'in','value':['star']},
    {'field':'board','op':'in','value':['beijing']},
    {'field':'board','op':'not_in','value':['main','chinext']},
    {'field':'is_st','op':'==','value':True},
    {'field':'is_st','op':'!=','value':False},
    {'all':[{'field':'board','op':'in','value':['main','star']},{'field':'is_st','op':'==','value':True}]},
    {'any':[{'field':'board','op':'in','value':['star']},{'field':'is_st','op':'==','value':True}]},
])
def test_forced_unsupported_stock_universe_is_unavailable_before_execution(definition):
    result=availability(CalendarOnly(),{'period':'D','definition':definition})
    assert result['available'] is False
    assert '撮合市场限制' in result['reason']
    assert result['execution_universe']['supported_boards']==[]
    assert result['execution_universe']['historical_st_complete'] is False


def test_mixed_stock_universe_keeps_only_supported_range_and_explicit_warning():
    definition={'all':[{'field':'board','op':'in','value':['main','star']},{'field':'close','op':'>','value':10}]}
    result=availability(CalendarOnly(),{'period':'D','definition':definition})
    assert result['available'] is True
    assert result['execution_universe']['supported_boards']==['main']
    assert any('不代表完整策略范围' in warning for warning in result['warnings'])
    assert any('不能冒充历史逐日身份' in warning for warning in result['warnings'])
    # An OR price branch may still admit supported markets. Do not reject it
    # simply because another branch targets an unsupported board.
    alternate=availability(CalendarOnly(),{'period':'D','definition':{'any':[{'field':'board','op':'in','value':['beijing']},{'field':'close','op':'>','value':10}]}})
    assert alternate['available'] is True
    assert alternate['execution_universe']['supported_boards']==['main','chinext']


def test_conflicting_stock_universe_is_reported_separately_from_unavailable_markets():
    definition={'all':[{'field':'board','op':'in','value':['main']},{'field':'board','op':'in','value':['chinext']}]}
    result=availability(CalendarOnly(),{'period':'D','definition':definition})
    assert result['available'] is False and '相互矛盾' in result['reason']


def test_mixed_universe_report_preserves_scope_and_excludes_unsupported_matching():
    class MixedMarket(CalendarOnly):
        def instruments(self,limit=10000):return [{'instrument_id':symbol,'name':name,'list_date':'2020-01-01'} for symbol,name in [('600000.SH','主板测试'),('688001.SH','科创测试')]]
        def status(self):return {'symbols':2}
        def iter_histories(self,end):
            bars=[{'date':day,'open':10,'high':10.3,'low':9.8,'close':10.1,'volume':100000,'amount':1010000,'corporate_action':False} for day in self.calendar() if day<=end]
            for item in self.instruments():yield item['instrument_id'],[dict(row) for row in bars]
    market=MixedMarket();cal=market.calendar()
    strategy={'period':'D','definition':{'all':[{'field':'board','op':'in','value':['main','star']},{'field':'close','op':'>','value':10}]}}
    report=run_backtest(market,strategy,{'start_date':cal[-20],'end_date':cal[-1]})
    assert report['execution_universe']['supported_boards']==['main']
    assert report['data_quality']['eligible_symbols']==1
    assert report['data_quality']['exclusions']['unsupported_board']==1
    assert {row['instrument_id'] for row in report['signals']}=={'600000.SH'}
    assert all(row['instrument_id']=='600000.SH' for row in report['fills'])
    assert any('不代表完整策略范围' in warning for warning in report['warnings'])


@pytest.fixture
def creation_context(tmp_path,monkeypatch):
    """Only factory/idempotency fixtures; no user data, source calls or simulation."""
    from app.analytics import backtest_store as store
    from app.core import connect,now_ms,dumps
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path))
    for variable in ('SHIXU_SQLITE_PATH','SHIXU_PUBLISHED_DIR','SHIXU_RAW_DIR','SHIXU_WORK_DIR'):monkeypatch.delenv(variable,raising=False)
    market=CalendarOnly();cal=market.calendar()
    strategies={identifier:{'strategy_id':identifier,'name':identifier,'version':1,'period':'D','definition':{'field':'close','op':'>','value':10}} for identifier in ('fixture-a','fixture-b')}
    with connect() as c:
        for identifier,strategy in strategies.items():
            c.execute('INSERT INTO strategy(strategy_id,name,created_at_ms) VALUES(?,?,?)',(identifier,identifier,now_ms()))
            c.execute("INSERT INTO strategy_version(strategy_id,version,period,definition_json,definition_sha256,evaluator_version,recorded_at_ms) VALUES(?,1,'D',?,?,'fixture',?)",(identifier,dumps(strategy['definition']),'f'*64,now_ms()))
            c.execute('UPDATE strategy SET current_version=1 WHERE strategy_id=?',(identifier,))
    monkeypatch.setattr(store,'context',lambda identifier:(market,strategies[identifier],{},'fixture-generation'))
    return store,{'strategy_id':'fixture-a','start_date':cal[-20],'end_date':cal[-1],'request_key':'fixture-click'}


def test_unsupported_universe_does_not_create_a_backtest_job(creation_context,monkeypatch):
    from app.core import connect
    store,payload=creation_context
    market,strategy,inputs,generation=store.context('fixture-a')
    restricted={**strategy,'definition':{'field':'board','op':'in','value':['star','beijing']}}
    monkeypatch.setattr(store,'context',lambda identifier:(market,restricted,inputs,generation))
    with pytest.raises(ValueError,match='撮合市场限制'):store.create_backtest(payload)
    with connect() as c:
        assert c.execute("SELECT count(*) FROM job WHERE kind='backtest'").fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM backtest_run').fetchone()[0]==0


def test_all_missing_listing_dates_refuse_job_creation_without_equity(creation_context,monkeypatch):
    from app.core import connect,published_dir
    store,payload=creation_context
    _,strategy,inputs,generation=store.context('fixture-a')
    class MissingListing(CalendarOnly):
        def instruments(self,limit=10000):return [{'instrument_id':'600000.SH','kind':'stock','list_date':None}]
    monkeypatch.setattr(store,'context',lambda identifier:(MissingListing(),strategy,inputs,generation))
    assert store.get_availability('fixture-a')['available'] is False
    with pytest.raises(ValueError,match='上市日期'):store.create_backtest(payload)
    with connect() as c:
        assert c.execute("SELECT count(*) FROM job WHERE kind='backtest'").fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM backtest_run').fetchone()[0]==0
        assert c.execute("SELECT count(*) FROM dataset_snapshot WHERE module='backtest_result'").fetchone()[0]==0
    assert not (published_dir()/'backtests').exists()


@pytest.mark.parametrize('changed',[
    {'strategy_id':'fixture-b'}, {'initial_capital':200000}, {'hold_days':8},
    {'start_date':CalendarOnly().calendar()[-19]}, {'end_date':CalendarOnly().calendar()[-2]},
])
def test_request_key_conflicting_frozen_inputs_are_rejected(creation_context,changed):
    store,payload=creation_context
    first=store.create_backtest(payload)
    assert store.create_backtest(payload)['backtest_id']==first['backtest_id']
    with pytest.raises(ValueError,match='输入快照不同'):store.create_backtest({**payload,**changed})


def test_request_key_used_by_another_job_is_explicit_error(creation_context):
    from app.core import connect,now_ms
    store,payload=creation_context
    with connect() as c:c.execute("INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,parameters_hash,status,created_at_ms) VALUES('fixture-other',?,'fixture-other','initialise','{}',?,'queued',?)",(payload['request_key'],'0'*64,now_ms()))
    with pytest.raises(ValueError,match='其他任务占用'):store.create_backtest(payload)
    with connect() as c:assert c.execute('SELECT count(*) FROM backtest_run').fetchone()[0]==0


def test_concurrent_identical_backtest_requests_create_only_one_job(creation_context):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.core import connect
    store,payload=creation_context;barrier=Barrier(2);original=store.context
    def frozen(identifier):
        result=original(identifier);barrier.wait(timeout=5);return result
    store.context=frozen
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(store.create_backtest,[dict(payload),dict(payload)]))
    assert results[0]['backtest_id']==results[1]['backtest_id']
    assert sum(r.get('reused',False) for r in results)==1
    with connect() as c:
        assert c.execute("SELECT count(*) FROM job WHERE kind='backtest'").fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM backtest_run').fetchone()[0]==1


def test_backtest_http_conflicts_are_409_instead_of_sqlite_500(creation_context):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers.backtests import router
    from app.core import connect,now_ms
    store,payload=creation_context;app=FastAPI();app.include_router(router);client=TestClient(app)
    first=client.post('/api/v1/backtests',json=payload)
    assert first.status_code==200
    conflict=client.post('/api/v1/backtests',json={**payload,'initial_capital':200000})
    assert conflict.status_code==409 and '输入快照不同' in conflict.json()['detail']
    with connect() as c:c.execute("INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,parameters_hash,status,created_at_ms) VALUES('http-other','http-other','http-other','initialise','{}',?,'queued',?)",('0'*64,now_ms()))
    other=client.post('/api/v1/backtests',json={**payload,'request_key':'http-other'})
    assert other.status_code==409 and '其他任务占用' in other.json()['detail']


def test_real_snapshot_backtest_sqlite_and_parquet_roundtrip(tmp_path,monkeypatch):
    """Real input, isolated output DB; never mutate the user's running workspace."""
    from pathlib import Path
    import os,json,shutil
    from app.analytics.market import MarketData
    from app.core import connect,publish_snapshot,now_ms,hash_file,dumps
    from app.analytics.backtest_store import create_backtest,execute_backtest,get_backtest,get_availability
    source=Path(__file__).resolve().parents[2]/'data/published'
    head=source/'market-head.json'
    if not head.exists():pytest.skip('real audit snapshot not imported')
    generation=json.loads(head.read_text())['snapshot_id']
    target=tmp_path/'published'/generation
    shutil.copytree(source/generation,target,copy_function=os.link)
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path))
    monkeypatch.delenv('SHIXU_SQLITE_PATH',raising=False)
    monkeypatch.delenv('SHIXU_PUBLISHED_DIR',raising=False)
    with connect() as c:
        c.execute("INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,parameters_hash,status,created_at_ms) VALUES('import-test','import-test','import-test','initialise','{}',?,'succeeded',?)",('0'*64,now_ms()))
    m=MarketData(tmp_path,generation);cal=m.calendar()
    for module,file in [('daily_raw','raw-daily.parquet'),('calendar','reference.json'),('analysis_bars','daily.parquet')]:
        path=target/file
        publish_snapshot(module=module,job_id='import-test',start=cal[0],end=cal[-1],artifacts=[{'object_key':str(path.relative_to(tmp_path/'published')),'role':module,'sha256':hash_file(path),'byte_count':path.stat().st_size,'row_count':m.status()['rows']}],lineage={'market_snapshot_id':generation},quality='partial',price_basis='none' if module!='calendar' else 'not_applicable')
    definition={'all':[{'field':'volume_ratio','op':'>=','value':3},{'field':'close_above_ma30','op':'==','value':True}]}
    with connect() as c:
        c.execute("INSERT INTO strategy(strategy_id,name,created_at_ms) VALUES('real-volume','真实三倍量测试',?)",(now_ms(),))
        c.execute("INSERT INTO strategy_version(strategy_id,version,period,definition_json,definition_sha256,evaluator_version,recorded_at_ms) VALUES('real-volume',1,'D',?,?,'v1',?)",(dumps(definition),'1'*64,now_ms()))
        c.execute("UPDATE strategy SET current_version=1 WHERE strategy_id='real-volume'")
    payload={'strategy_id':'real-volume','start_date':cal[-10],'end_date':cal[-1],'request_key':'same-click'}
    stocks=[row for row in m.instruments(limit=10000) if row.get('kind','stock')=='stock']
    if stocks and all(not row.get('list_date') for row in stocks):
        # This is a passing real integration path: current source identity gaps
        # must refuse the request rather than queue an empty/fictional account.
        bounds=get_availability('real-volume')
        assert bounds['available'] is False and '上市日期' in bounds['reason']
        assert bounds['eligible_count']==0
        with pytest.raises(ValueError,match='上市日期'):create_backtest(payload)
        with connect() as c:
            assert c.execute("SELECT count(*) FROM job WHERE kind='backtest'").fetchone()[0]==0
            assert c.execute('SELECT count(*) FROM backtest_run').fetchone()[0]==0
            assert c.execute("SELECT count(*) FROM dataset_snapshot WHERE module='backtest_result'").fetchone()[0]==0
        assert not (tmp_path/'published/backtests').exists()
        return
    queued=create_backtest(payload)
    assert create_backtest(payload)['backtest_id']==queued['backtest_id']
    with connect() as c:c.execute("UPDATE job SET status='running' WHERE job_id=?",(queued['job_id'],))
    report=execute_backtest(queued['job_id'])
    assert report['status']=='completed' and report['data_quality']['eligible_symbols']>0
    assert len(report['equity'])==11
    assert report['stats']['trade_count']==len(report['trades'])
    assert report['stats']['total_fees']==pytest.approx(sum(f['fee'] for f in report['fills']))
    assert report['stats']['net_profit']==pytest.approx(sum(t['net_pnl'] for t in report['trades'])+report['stats']['unrealized_pnl'],abs=.01)
    final=report['equity'][-1]
    positions=[p for p in report['positions'] if p['date']==cal[-1]]
    assert final['equity']==pytest.approx(final['cash']+sum(p['market_value'] for p in positions),abs=.01)
    assert all(t['entry_date']<t['exit_date'] for t in report['trades'])
    saved=get_backtest(queued['backtest_id'])
    assert saved['stats']==report['stats'] and saved['market_snapshot_id']==generation
    with connect() as c:
        assert c.execute('SELECT count(*) FROM snapshot_artifact WHERE snapshot_id=?',('result-'+queued['backtest_id'],)).fetchone()[0]==7
        assert c.execute('SELECT status FROM backtest_run WHERE backtest_id=?',(queued['backtest_id'],)).fetchone()[0]=='completed'
    assert create_backtest(payload)['status']=='completed'
    # A cancelled queued job must be visible as cancelled without being executed.
    cancelled=create_backtest({**payload,'hold_days':9,'request_key':'cancel-queued'})
    with connect() as c:c.execute("UPDATE job SET status='cancelled' WHERE job_id=?",(cancelled['job_id'],))
    assert get_backtest(cancelled['backtest_id'])['status']=='cancelled'
    from app.analytics import backtest_store
    from app.services.task_service import TaskPaused,TaskCancelled
    with connect() as c:c.execute("UPDATE job SET status='succeeded' WHERE job_id=?",(queued['job_id'],))
    paused=create_backtest({**payload,'hold_days':8,'request_key':'pause-running'})
    with connect() as c:c.execute("UPDATE job SET status='paused' WHERE job_id=?",(paused['job_id'],))
    def raise_pause(*args,**kwargs):raise TaskPaused()
    monkeypatch.setattr(backtest_store,'run_backtest',raise_pause)
    with pytest.raises(TaskPaused):execute_backtest(paused['job_id'])
    assert get_backtest(paused['backtest_id'])['status']=='paused'
    assert get_backtest(paused['backtest_id'])['calculation_status']=='running'
    cancelled_running=create_backtest({**payload,'hold_days':7,'request_key':'cancel-running'})
    def raise_cancel(*args,**kwargs):raise TaskCancelled()
    monkeypatch.setattr(backtest_store,'run_backtest',raise_cancel)
    with pytest.raises(TaskCancelled):execute_backtest(cancelled_running['job_id'])
    assert get_backtest(cancelled_running['backtest_id'])['status']=='cancelled'
