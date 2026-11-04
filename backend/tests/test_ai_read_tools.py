"""Synthetic read-only tool fixtures; never use credentials or live sources."""
from copy import deepcopy
from datetime import datetime
import json
import sqlite3

import pytest

from app.core import digest, dumps
from app.services import ai_contexts as contexts, ai_read_tools as tools
from app.services.sync_clock import CHINA
from test_ai_market_brief import database, auction_payload, noon_payload, save, save_close, DAY
from test_ai_contexts import report, SYMBOL


class EmptyMarket:
    def status(self):
        return {'ready': False}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.delenv('SHIXU_AI_READ_SQLITE_PATH', raising=False)
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path/'data'))
    monkeypatch.setenv('SHIXU_PUBLISHED_DIR', str(tmp_path/'published'))
    monkeypatch.setattr(tools, 'local_now', lambda: datetime.fromisoformat(DAY + 'T11:03:00+08:00'))
    monkeypatch.setattr(tools, '_market', lambda *args: EmptyMarket())


def call(section, **kwargs):
    return tools.execute_read_tool('read_market_data', {'date': 'today', 'section': section}, **kwargs)


def test_tool_schemas_are_closed_responses_functions():
    schemas = tools.tool_schemas()
    assert {s['name'] for s in schemas} == {'read_market_data', 'read_intraday_data', 'read_stock_research', 'check_data_availability', 'read_stock_quote', 'read_stock_trend', 'read_limit_up_data', 'read_market_detail'}
    assert all(s['type'] == 'function' and s['strict'] is True for s in schemas)
    assert all(s['parameters']['additionalProperties'] is False for s in schemas)
    assert schemas[0]['parameters']['properties']['section']['enum'] == list(tools.SECTIONS)
    assert 'path' not in dumps(schemas) and 'sql' not in dumps(schemas).lower()


def test_today_uses_current_china_clock_instead_of_old_page_or_fallback(database):
    result = call('auction', page_context={'as_of': '2020-01-01'}, fallback_context={'as_of': '2021-01-01'})
    assert result['date'] == DAY and result['state'] == 'not_collected'
    phases = result['summary']['phases']
    assert phases['noon']['phase_state'] == 'not_due'
    assert phases['close']['phase_state'] == 'not_due'
    assert result['summary']['now'] == DAY + 'T11:03:00+08:00'
    assert result['evidence'] == [] and result['metrics'] == []


@pytest.mark.parametrize('clock,states', [
    ('09:24:59', ['not_due', 'not_due', 'not_due']),
    ('09:25:00', ['not_collected', 'not_due', 'not_due']),
    ('11:30:00', ['not_collected', 'not_collected', 'not_due']),
    ('15:00:00', ['not_collected', 'not_collected', 'not_collected']),
])
def test_phase_thresholds_do_not_confuse_not_due_with_missing(database, monkeypatch, clock, states):
    monkeypatch.setattr(tools, 'local_now', lambda: datetime.fromisoformat(DAY + 'T' + clock + '+08:00'))
    result = call('availability')
    assert [result['summary']['phases'][key]['phase_state'] for key in tools.PHASES] == states


def test_historical_weekday_without_calendar_stays_unknown(database):
    result = tools.execute_read_tool('check_data_availability', {'date': '2026-10-29'})
    assert result['summary']['calendar_state'] == 'unknown'
    assert all(r['phase_state'] == 'unknown' for r in result['summary']['phases'].values())


def test_verified_saved_calendar_can_prove_rest_day_and_scheduled_capture_is_separate(database, monkeypatch):
    database.execute('CREATE TABLE platform_setting(key TEXT PRIMARY KEY,value_json TEXT)')
    calendar = {'state': 'ready', 'days': ['2026-10-23', '2026-10-26', DAY], 'source_id': 'fuyao', 'source_sha256': 'a'*64}
    database.execute('INSERT INTO platform_setting VALUES(?,?)', ('automatic-review-calendar-v1', dumps(calendar)))
    database.execute('INSERT INTO platform_setting VALUES(?,?)', ('automatic-review-config-v1', dumps({'noon': {'enabled': True, 'time': '12:05'}})))
    database.commit()
    result = tools.execute_read_tool('check_data_availability', {'date': '2026-10-25'})
    assert all(r['phase_state'] == 'rest_day' for r in result['summary']['phases'].values())
    monkeypatch.setattr(tools, 'local_now', lambda: datetime.fromisoformat(DAY+'T11:40:00+08:00'))
    noon = call('noon')['summary']['phases']['noon']
    assert noon['phase_state'] == 'not_collected' and noon['scheduled_capture_at'] == '12:05'
    assert '计划采集时间尚未到' in noon['reason']


def test_auction_is_read_with_frozen_dates_counts_and_single_sample_scope(database):
    save(database, 'market_auction_snapshot', 'auction', auction_payload())
    before = database.execute('SELECT payload_json FROM market_auction_snapshot').fetchone()[0]
    result = call('auction')
    assert result['ok'] and result['state'] == 'available'
    assert result['evidence'][0]['dates']['trade_date'] == DAY
    assert result['evidence'][0]['data']['summary']['valid'] == 1
    assert '单股样本' in result['evidence'][0]['data']['direction_scope']
    assert result['source_bindings']['intraday']['current_instance']['auction']['content_sha256'] == digest(auction_payload())
    assert database.execute('SELECT payload_json FROM market_auction_snapshot').fetchone()[0] == before
    assert all(row['metric_id'].startswith('read:'+DAY+':') for row in result['metrics'])
    assert all(row['evidence_ids'] == [result['evidence'][0]['evidence_id']] for row in result['metrics'])


def test_configured_read_source_has_priority_and_does_not_need_migration(tmp_path, database, monkeypatch):
    path = tmp_path/'formal-read.sqlite3'
    target = sqlite3.connect(path)
    target.execute('CREATE TABLE market_auction_snapshot(snapshot_id TEXT,trade_date TEXT,collected_start_at TEXT,'
                   'collected_end_at TEXT,content_sha256 TEXT,payload_json TEXT)')
    payload = auction_payload()
    target.execute('INSERT INTO market_auction_snapshot VALUES(?,?,?,?,?,?)', (
        'formal-auction', DAY, payload['collected_start_at'], payload['collected_end_at'], digest(payload), dumps(payload)))
    target.commit(); target.close()
    save(database, 'market_auction_snapshot', 'old-instance-auction', auction_payload())
    monkeypatch.setenv('SHIXU_AI_READ_SQLITE_PATH', str(path))
    before = path.read_bytes()
    result = call('auction')
    assert result['summary']['phases']['auction']['source_ids']['snapshot_id'] == 'formal-auction'
    assert result['source'] == ['configured_read_source']
    assert 'formal-read.sqlite3' not in dumps(result)
    assert path.read_bytes() == before
    check = tools._connect(path)
    assert check.execute('PRAGMA query_only').fetchone()[0] == 1
    assert check.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    assert check.execute('PRAGMA recursive_triggers').fetchone()[0] == 1
    assert check.execute('PRAGMA busy_timeout').fetchone()[0] == 10000
    with pytest.raises(sqlite3.OperationalError):
        check.execute('DELETE FROM market_auction_snapshot')
    check.close()


def test_malformed_or_explicitly_denied_auction_does_not_send_body(database):
    payload = auction_payload(); payload['ai_processing_allowed'] = False
    save(database, 'market_auction_snapshot', 'auction', payload)
    result = call('auction')
    assert result['state'] == 'unknown' and not result['evidence'] and result['gaps']


def test_close_parent_revision_amount_and_membership_dates_remain_exact(database):
    save(database, 'market_noon_snapshot', 'noon', noon_payload())
    save_close(database, revision=1); save_close(database, revision=2)
    result = call('close')
    assert result['state'] == 'available'
    evidence = result['evidence'][0]
    assert evidence['data']['amount']['delta'] == 1000
    assert '不是净流入' in evidence['data']['scope']
    assert result['source_bindings']['intraday']['current_instance']['noon_close']['revision'] == 2
    assert evidence['data']['amount']['unknown_count'] == 1
    noon = call('noon')
    unknown = next(r for r in noon['metrics'] if r['metric_id'].endswith(':limit_up'))
    assert unknown['value'] is None and unknown['unit'] == '家'


def test_source_revision_change_returns_bounded_error_instead_of_mixed_data(database, monkeypatch):
    original = tools._availability
    def changing(day, now):
        result = original(day, now)
        save(database, 'market_auction_snapshot', 'late-arrival', auction_payload())
        return result
    monkeypatch.setattr(tools, '_availability', changing)
    result = call('availability')
    assert not result['ok'] and result['state'] == 'error' and not result['evidence']


def test_overview_can_use_today_auction_without_claiming_old_close_is_today(database):
    save(database, 'market_auction_snapshot', 'auction', auction_payload())
    result = call('overview')
    assert result['ok'] and result['state'] == 'partial' and len(result['evidence']) == 1
    assert result['summary']['phases']['noon']['phase_state'] == 'not_due'
    assert any(g['capability'] == 'completed_market' for g in result['gaps'])


def test_missing_fund_flow_never_becomes_turnover(database, monkeypatch):
    import app.services.market_supplement as supplement
    monkeypatch.setattr(supplement, '_load_supplement_with_fingerprint', lambda *a, **kw: ({}, None))
    monkeypatch.setattr(tools, '_market', lambda *args: type('M', (), {'status': lambda s: {}, 'calendar': lambda s: []})())
    result = call('fund_flow')
    assert result['ok'] and result['state'] == 'unknown' and not result['metrics']
    assert any('不以成交额替代' in g['reason'] for g in result['gaps'])


def test_completed_overview_keeps_core_indices_and_does_not_pack_all_intraday_bodies(database, monkeypatch):
    import app.services.market_sentiment as sentiment
    import app.services.market_supplement as supplement
    save(database, 'market_auction_snapshot', 'auction', auction_payload())
    save(database, 'market_noon_snapshot', 'noon', noon_payload())
    save_close(database)
    class Market:
        def status(self): return {'ready': True, 'snapshot_id': 'immutable-fixture'}
        def calendar(self): return ['2026-10-29', DAY]
        def reference(self): return {}
        def panel(self, day): return []
        def turnover_history(self, day): return []
    monkeypatch.setattr(tools, '_market', lambda *a: Market())
    monkeypatch.setattr(supplement, '_load_supplement_with_fingerprint', lambda *a, **kw: ({}, None))
    metric = {'key': 'return_pct', 'label': '合成涨幅', 'value': 1, 'unit': '%'}
    description = {'market': {'metrics': [metric]}, 'short_term': {'metrics': [metric], 'groups': []},
                   'index_environment': {'indices': [{'instrument_id': code, 'name': code, 'metrics': [metric]}
                       for code in ('000001.SH','399001.SZ','399006.SZ','000688.SH')]}}
    monkeypatch.setattr(sentiment, 'describe_sentiment', lambda *a: description)
    result = call('overview')
    ids = {row['evidence_id'].split(':',3)[-1] for row in result['evidence']}
    assert {'market_distribution', 'turnover', 'previous_limit_up', *('index:'+c for c in ('000001.SH','399001.SZ','399006.SZ','000688.SH'))} <= ids
    assert not any(value.startswith('day_brief:') for value in ids)
    assert all(row['phase_state'] == 'available' for row in result['summary']['phases'].values())
    assert len(dumps(result).encode()) <= tools.MAX_RESULT_BYTES


def test_tools_never_migrate_connect_network_or_create_missing_databases(tmp_path, monkeypatch):
    import app.infrastructure.database as db
    import httpx
    monkeypatch.setenv('SHIXU_SQLITE_PATH', str(tmp_path/'absent.sqlite3'))
    monkeypatch.setenv('SHIXU_AI_READ_SQLITE_PATH', str(tmp_path/'absent-formal.sqlite3'))
    monkeypatch.setattr(db, 'ensure_ready', lambda: pytest.fail('read tools may not migrate'))
    monkeypatch.setattr(httpx.Client, 'request', lambda *a, **kw: pytest.fail('read tools may not network'))
    result = call('auction')
    assert result['ok'] and not result['evidence']
    assert not (tmp_path/'absent.sqlite3').exists() and not (tmp_path/'absent-formal.sqlite3').exists()


def test_supplement_head_change_rejects_fund_flow_even_with_stable_main_head(database, monkeypatch):
    import app.services.market_supplement as supplement
    import app.services.market_service as service
    monkeypatch.setattr(tools, '_market', lambda *a: type('M', (), {'status': lambda s: {}, 'calendar': lambda s: [DAY]})())
    values = iter([{'sha256':'before'}, {'sha256':'after'}])
    monkeypatch.setattr(tools, '_supplement_head', lambda: next(values))
    monkeypatch.setattr(supplement, '_load_supplement_with_fingerprint', lambda *a, **kw: ({}, ('verified',)))
    monkeypatch.setattr(service, 'fund_flow_for_date', lambda *a: {'items':[{'date':DAY,'name':'合成行业','net_inflow':1}], 'coverage':{}})
    result = call('fund_flow')
    assert not result['ok'] and result['state'] == 'error' and not result['evidence']


def save_report(database, value, *, expires=9999999999999):
    database.execute('CREATE TABLE IF NOT EXISTS research_report(report_id TEXT, instrument_id TEXT,data_cutoff TEXT,'
                     'created_at_ms INTEGER,report_json TEXT,expires_at_ms INTEGER,last_accessed_at_ms INTEGER)')
    database.execute('INSERT INTO research_report VALUES(?,?,?,?,?,?,?)',
        (value['report_id'], SYMBOL, value['data_cutoff'], 1234, dumps(value), expires, 5678))
    database.commit()


def test_current_stock_research_reads_same_instance_without_touching_ttl(database):
    value = report(); save_report(database, value)
    before = tuple(database.execute('SELECT expires_at_ms,last_accessed_at_ms FROM research_report').fetchone())
    result = tools.execute_read_tool('read_stock_research', {'instrument_id': ''}, page_context={'instrument_id': SYMBOL, 'as_of': '2020-01-01'})
    assert result['ok'] and result['date'] == value['data_cutoff'] and result['evidence']
    pe = next(r for r in result['metrics'] if r['metric_id'].endswith('valuation:pe_ttm'))
    pb = next(r for r in result['metrics'] if r['metric_id'].endswith('valuation:pb_mrq'))
    assert pe['value'] == 12.5 and pe['unit'] == '倍' and pb['value'] is None
    evidence = next(e for e in result['evidence'] if pe['evidence_ids'] == [e['evidence_id']])
    dates = {**result['summary'].get('metric_defaults', {}).get('dates', {}),
             **evidence.get('metric_dates', {}), **pe.get('dates', {})}
    assert dates == {'published_at_ms': None, 'fetched_at_ms': 1234, 'report_period': None, 'market_data_cutoff': value['data_cutoff']}
    assert 'fixture-secret' not in dumps(result)
    assert before == tuple(database.execute('SELECT expires_at_ms,last_accessed_at_ms FROM research_report').fetchone())


def test_company_research_returns_financial_and_ownership_facts_with_separate_dates(database):
    value = report()
    for kind, source, data in (
        ('financial_cashflow', 'fuyao', {'report_period': '2025-12-31', 'act_cash_flow_net': 100,
                                       'invest_cash_flow_net': -25, 'financing_cash_flow_net': -12}),
        ('shareholders', 'eastmoney', {'items': [{'report_period': '2025-12-31',
            'published_date': '2026-01-04', 'holder_count': 18000, 'change_ratio_pct': -1.25}]}),
        ('market_cap', 'tencent', {'total_market_cap_cny': 1000, 'float_market_cap_cny': 800}),
        ('financial_income', 'tencent', {'operating_income': 999}),
    ):
        value['evidence'].append({'evidence_id': kind, 'kind': kind, 'source': source, 'title': kind,
            'data': data, 'content_hash': tools.digest(data), 'instrument_id': SYMBOL,
            'report_period': data.get('report_period')})
    save_report(database, value)
    result = tools.execute_read_tool('read_stock_research', {'instrument_id': SYMBOL})
    metrics = {m['metric_id'].rsplit(':', 1)[-1]: m for m in result['metrics']}
    assert metrics['invest_cash_flow_net']['value'] == -25 and metrics['financing_cash_flow_net']['unit'] == 'CNY'
    assert metrics['holder_count']['value'] == 18000 and metrics['holder_count']['unit'] == '户'
    holder = next(e for e in result['evidence'] if e['kind'] == 'shareholders')
    dates = {**result['summary'].get('metric_defaults', {}).get('dates', {}),
             **holder.get('metric_dates', {}), **metrics['holder_count'].get('dates', {})}
    assert dates['report_period'] == '2025-12-31' and dates['published_date'] == '2026-01-04'
    assert metrics['float_market_cap_cny']['value'] == 800
    assert 'operating_income' not in metrics


def test_expired_stock_report_only_falls_back_to_same_stock_frozen_old_context(database, monkeypatch):
    value = report(); save_report(database, value, expires=1)
    monkeypatch.setattr(contexts, 'get_report', lambda *a, **kw: deepcopy(value))
    fallback = contexts.build_context('stock', report_id=value['report_id'])
    before = deepcopy(fallback)
    result = tools.execute_read_tool('read_stock_research', {'instrument_id': SYMBOL}, fallback_context=fallback)
    assert result['state'] == 'partial' and result['date'] == fallback['as_of']
    assert '旧材料' in result['summary']['notice'] and fallback == before
    other = tools.execute_read_tool('read_stock_research', {'instrument_id': '600002.SH'}, fallback_context=fallback)
    assert not other['evidence'] and other['state'] == 'unknown'


def test_stock_explicit_source_deny_is_not_bypassed(database):
    value = report(); value['source_coverage'] = [{'source_id': 'fuyao', 'egress_allowed': False}]
    save_report(database, value)
    result = tools.execute_read_tool('read_stock_research', {'instrument_id': SYMBOL})
    assert all(row['source'] != 'fuyao' for row in result['evidence'])


def test_stock_technical_contract_is_shared_without_losing_period_dates_units_or_parent_json(database):
    value = report()
    contract = {'price_basis': 'none', 'price_unit': 'CNY', 'period_policy': '完整周/月；日线严格交易日'}
    value['technical_assessment'] = {'analysis_contract': contract, 'periods': [
        {'period': period, 'observed_date': DAY, 'observations': {'close': 12.5}, 'current_period': {'forming': True}}
        for period in ('D', 'W', 'M')]}
    save_report(database, value)
    before = database.execute('SELECT report_json FROM research_report').fetchone()[0]
    result = tools.execute_read_tool('read_stock_research', {'instrument_id': SYMBOL})
    technical = [e for e in result['evidence'] if e['kind'] == 'technical_observation']
    assert {e['data']['period'] for e in technical} == {'D','W','M'}
    assert result['summary']['technical_analysis_contract'] == contract
    assert result['summary']['technical_contract_periods'] == ['D','W','M']
    assert all(e['data']['observed_date'] == DAY and e['data']['observations']['close'] == 12.5
               and 'current_period' not in e['data'] for e in technical)
    assert database.execute('SELECT report_json FROM research_report').fetchone()[0] == before


def test_bounded_tool_result_removes_whole_rows_and_all_references():
    result = tools._result('read_stock_research', DAY)
    for index in range(30):
        eid = 'row'+str(index)
        result['evidence'].append({'evidence_id': eid, 'source': 'cninfo', 'kind': 'announcement', 'dates': {'trade_date': DAY},
                                   'data': {'title': '合成长标题'*500, 'amount': 123.45, 'unit': 'CNY'}})
        result['metrics'].append({'metric_id': eid+':amount', 'value': 123.45, 'unit': 'CNY', 'dates': {'trade_date': DAY}, 'evidence_ids': [eid]})
    output = tools._bounded(result)
    assert len(dumps(output).encode()) <= tools.MAX_RESULT_BYTES
    assert output['summary']['bounded_exclusions'] and len(output['evidence']) < 30
    ids = {row['evidence_id'] for row in output['evidence']}
    assert output['summary']['metric_defaults']['dates']['trade_date'] == DAY
    assert all(set(row['evidence_ids']) <= ids and row['unit'] == 'CNY' for row in output['metrics'])


@pytest.mark.parametrize('name,args', [('run_sql', {'sql': 'SELECT * FROM key'}),
    ('read_market_data', {'date': 'today', 'section': 'auction', 'path': '/private'}),
    ('read_market_data', {'date': 'yesterday', 'section': 'auction'}),
    ('read_stock_research', {'instrument_id': '600001', 'touch': True})])
def test_unknown_tool_path_sql_and_invalid_arguments_fail_closed(name, args):
    result = tools.execute_read_tool(name, args)
    assert result['ok'] is False and result['state'] == 'error'
    assert set(result) == {'ok','state','tool_name','date','source','summary','evidence','metrics','gaps','source_bindings'}
    assert '/private' not in dumps(result) and len(dumps(result).encode()) < tools.MAX_RESULT_BYTES
