"""Independent scoped D/W/M checks; synthetic local data, no provider access."""
from copy import deepcopy
from datetime import date, timedelta
import json

import pytest

from app import core
from app.analytics.backtest_engine import availability, prepare
from app.domain.conditions import validate, evaluate, metrics_for
from app.services import strategies, strategy_periods
from test_workspace import ws, make_strategy, add_one


def leaf(field='close', op='>', value=0, **extra):
    return {'field': field, 'op': op, 'value': value, **extra}


class PeriodMarket:
    def __init__(self):
        self.days = []
        cursor = date(2022, 12, 1)
        while cursor <= date(2026, 10, 20):
            if cursor.weekday() < 5: self.days.append(cursor.isoformat())
            cursor += timedelta(days=1)
        self.rows = [{'date': day, 'open': 20+i*.01, 'close': 20+i*.01,
                      'high': 20.1+i*.01, 'low': 19.9+i*.01, 'volume': 10000,
                      'amount': 200000, 'corporate_action': False,
                      'corporate_action_coverage_known': True} for i, day in enumerate(self.days)]
    def calendar(self): return list(self.days)
    def instruments(self, limit=10000):
        return [{'instrument_id': '600000.SH', 'name': '可核验测试主板', 'list_date': '2020-01-01'}]
    def status(self): return {'symbols': 1, 'snapshot_id': 'scoped-period-fixture'}
    def iter_histories(self, end):
        yield '600000.SH', [dict(row) for row in self.rows if row['date'] <= end]


def test_monthly_crud_stores_additive_signal_period_and_freezes_old_version(ws):
    _, client = ws
    response = client.post('/api/v1/strategies', json={'name': '月线策略', 'period': 'M', 'definition': leaf()})
    assert response.status_code == 200, response.text
    strategy = response.json(); assert strategy['period'] == 'M'
    sid = strategy['strategy_id']
    changed = client.patch('/api/v1/strategies/'+sid, json={'period': 'D', 'definition': leaf(period='M')})
    assert changed.status_code == 200 and changed.json()['period'] == 'D'
    with core.connect() as connection:
        versions = connection.execute('SELECT * FROM strategy_version WHERE strategy_id=? ORDER BY version', (sid,)).fetchall()
    assert versions[0]['period'] == 'D' and versions[0]['signal_period'] == 'M'
    assert versions[1]['signal_period'] == 'D'
    assert json.loads(versions[0]['definition_json']) == leaf()
    assert json.loads(versions[1]['definition_json']) == leaf(period='M')


@pytest.mark.parametrize('period', ['D', 'W'])
def test_legacy_null_signal_period_reads_original_period_without_rewriting(ws, period):
    _, client = ws
    response = client.post('/api/v1/strategies', json={'name': '旧版本'+period, 'period': period, 'definition': leaf()})
    strategy = response.json(); sid = strategy['strategy_id']
    with core.connect() as connection:
        old = dict(connection.execute('SELECT * FROM strategy_version WHERE strategy_id=?', (sid,)).fetchone())
        # Build a legacy-shaped immutable fixture version, never mutate an
        # existing evidence row or disable its append-only trigger.
        old.update(version=2, signal_period=None)
        columns = list(old)
        connection.execute('INSERT INTO strategy_version('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')', [old[key] for key in columns])
        connection.execute('UPDATE strategy SET current_version=2 WHERE strategy_id=?', (sid,))
    assert strategies.get_strategy(sid)['period'] == period
    with core.connect() as connection:
        assert connection.execute('SELECT signal_period FROM strategy_version WHERE strategy_id=? AND version=2', (sid,)).fetchone()[0] is None


def test_scoped_leaf_uses_its_own_period_and_missing_never_falls_back_to_daily():
    ast = validate({'all': [leaf(period='D'), leaf('macd_cross', '==', True, period='W'), leaf('ma5', period='M')]})
    metrics = {'close': 100, 'macd_cross': True, 'ma5': 100, 'indicator_period': 'D',
               '_period_metrics': {'D': {'close': 100, 'observed_date': '2026-10-20'},
                                  'W': {'macd_cross': True, 'observed_date': '2026-10-16'}}}
    checks = evaluate(ast, metrics)
    assert checks['result'] is None
    assert checks['checks'][1]['actual'] is True and checks['checks'][1]['observed_date'] == '2026-10-16'
    assert checks['checks'][2]['actual'] is None
    metrics['_period_metrics']['M'] = {'ma5': 88, 'observed_date': '2026-09-30'}
    assert evaluate(ast, metrics)['result'] is True


@pytest.mark.parametrize('field,op,value', [('close_breakout_high', 'new_high', 60),
    ('intraday_high_change_pct', '>=', 3), ('limit_up', '==', True),
    ('prev_limit_up', '==', False), ('close_below_entry', '==', True)])
def test_daily_evidence_fields_reject_weekly_or_monthly_override(field, op, value):
    for period in ('W', 'M'):
        with pytest.raises(ValueError): validate(leaf(field, op, value, period=period))
    assert validate(leaf(field, op, value, period='D'))['period'] == 'D'


def test_history_event_where_and_then_remain_daily_in_new_scoped_ast():
    event = {'history_event': {'window': {'from': 1, 'to': 30}, 'match': 'latest',
        'where': leaf('volume_ratio', '>=', 3, period='D'),
        'then': {'field': 'close', 'op': '>', 'right_event_field': 'high', 'period': 'D'}}}
    assert validate(event)['history_event']['then']['period'] == 'D'
    event['history_event']['where']['period'] = 'M'
    with pytest.raises(ValueError): validate(event)


def test_monthly_outer_strategy_history_then_reads_observation_daily_context():
    from app.domain.history_events import HistoryContext
    market = PeriodMarket(); day = '2026-10-13'
    tree = validate({'history_event': {'window': {'from': 1, 'to': 1}, 'match': 'latest',
        'where': leaf('volume', '>', 0, period='D'), 'then': leaf('close', '>', 0, period='D')}})
    context = HistoryContext(market.rows, market.days, day, 'scoped-period-fixture')
    monthly = metrics_for(market.rows, day, market.days, 'M')
    assert evaluate(tree, monthly, context.at(day))['result'] is True


def test_closed_month_and_week_values_are_causal_even_with_future_rows():
    market = PeriodMarket(); day = '2026-10-13'
    expected = {period: metrics_for(market.rows, day, market.days, period) for period in ('W', 'M')}
    altered = deepcopy(market.rows)
    for row in altered:
        if row['date'] > day:
            row.update(open=99999, high=99999, low=99999, close=99999, volume=999999)
    for period, data in expected.items():
        actual = metrics_for(altered, day, market.days, period)
        assert actual == data
        assert actual['observed_date'] <= day
    assert expected['W']['observed_date'] == '2026-10-09'
    assert expected['M']['observed_date'] == '2026-09-30'


def test_daily_turnover_keeps_observation_day_in_weekly_and_monthly_strategy():
    market=PeriodMarket();day='2026-10-13'
    for row in market.rows:row['turnover_ratio_pct']=7 if row['date']==day else 2
    for period in ('D','W','M'):
        assert metrics_for(market.rows,day,market.days,period)['turnover_ratio_pct']==7


@pytest.mark.parametrize('period,missing', [('W', '2026-10-09'), ('M', '2026-09-30')])
def test_missing_latest_closed_period_stays_unknown_instead_of_older_signal(period, missing):
    market = PeriodMarket(); day = '2026-10-13'
    rows = [row for row in market.rows if row['date'] != missing]
    result = metrics_for(rows, day, market.days, period)
    assert result['close'] is None
    assert evaluate(validate(leaf(period=period)), {'indicator_period': period, **result})['result'] is None


@pytest.mark.parametrize('period', ['W', 'M'])
@pytest.mark.parametrize('volume', [0, None])
def test_nontraded_latest_closed_period_does_not_fallback_or_signal(period, volume):
    market = PeriodMarket(); day = '2026-10-13'
    for row in market.rows:
        target = '2026-10-05' <= row['date'] <= '2026-10-09' if period == 'W' else row['date'].startswith('2026-09-')
        if target: row['volume'] = volume
    result = metrics_for(market.rows, day, market.days, period)
    assert result['close'] is None
    assert evaluate(validate(leaf(period=period)), {'indicator_period': period, **result})['result'] is None


def test_period_summary_cache_is_generation_scoped_and_returns_deepcopy(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    with strategy_periods._lock: strategy_periods._cache.clear()
    market = PeriodMarket(); day = '2026-10-13'
    first = strategy_periods.cached_metrics(market.rows, day, market.days, 'M', instrument_id='600000.SH', generation='first')
    expected = deepcopy(first); first['close'] = -1
    assert strategy_periods.cached_metrics(market.rows, day, market.days, 'M', instrument_id='600000.SH', generation='first') == expected
    changed = deepcopy(market.rows)
    for row in changed:
        if row['date'] == '2026-09-30': row.update(open=77, close=77, high=78, low=76)
    other = strategy_periods.cached_metrics(changed, day, market.days, 'M', instrument_id='600000.SH', generation='second')
    assert other['close'] == 77 and expected['close'] != 77
    with strategy_periods._lock: strategy_periods._cache.clear()


def test_monthly_strategy_keeps_current_daily_tracking_entry_price(ws):
    market, client = ws
    response = client.post('/api/v1/strategies', json={'name': '月观察日入池', 'period': 'M', 'definition': leaf()})
    assert response.status_code == 200
    run = client.post('/api/v1/strategies/'+response.json()['strategy_id']+'/runs', json={})
    assert run.status_code == 200, run.text
    item = next(row for row in run.json()['items'] if row['instrument_id'] == '600001.SH')
    assert item['decision'] == 'matched'
    daily = next(row for row in market.data['600001.SH'] if row['date'] == market.entry)
    assert item['close_price'] == daily['close']
    assert item['metrics']['indicator_period'] == 'M'
    assert item['metrics']['signal_date'] < market.entry


def test_tracking_policy_scoped_weekly_alert_preserves_signal_date(ws):
    market, client = ws
    strategy = make_strategy(client, policy={'allow_entry_day_alerts': True,
        'alerts': [leaf(period='W')], 'exits': []})
    _, tracked = add_one(client, strategy)
    result = client.post('/api/v1/tracking-rounds/review', json={})
    assert result.status_code == 200, result.text
    detail = client.get('/api/v1/tracking-rounds/'+tracked['round_id']).json()
    alert = next(event for event in detail['events'] if event['event_type'] == 'alert')
    check = alert['evidence']['alerts']['checks'][0]
    assert check['period'] == 'W' and check['observed_date'] < market.entry
    assert alert['business_date'] == market.entry and detail['status'] == 'tracking'


def test_monthly_backtest_emits_only_closed_month_signals():
    market = PeriodMarket()
    strategy = {'period': 'M', 'definition': leaf()}
    assert availability(market, strategy)['available'] is True
    _, signals, rows, quality = prepare(market, strategy, '2026-08-03', '2026-10-13')
    assert set(signals) == {'2026-08-31', '2026-09-30'}
    assert all(row['metrics']['observed_date'] == row['date'] for row in rows)
    assert quality['eligible_symbols'] == 1


def test_daily_backtest_monthly_clause_uses_last_closed_month_not_future_end():
    market = PeriodMarket()
    strategy = {'period': 'D', 'definition': leaf('close', '>', 0, period='M')}
    quotes, _, rows, _ = prepare(market, strategy, '2026-09-01', '2026-10-13')
    first = next(row for row in rows if row['date'] == '2026-09-01')
    assert first['checks']['period'] == 'M'
    assert first['checks']['observed_date'] == '2026-08-31'
    changed = deepcopy(market)
    for row in changed.rows:
        if row['date'] > '2026-09-01': row.update(open=99999, high=99999, low=99999, close=99999)
    _, _, later_rows, _ = prepare(changed, strategy, '2026-09-01', '2026-10-13')
    same = next(row for row in later_rows if row['date'] == '2026-09-01')
    assert same['checks'] == first['checks']
    assert quotes['2026-09-01']['600000.SH']['close'] != 99999


def test_mixed_availability_preheats_each_period_independently():
    market = PeriodMarket()
    strategy = {'period': 'D', 'definition': {'all': [leaf('ma250', period='D'), leaf(period='M')]}}
    bounds = availability(market, strategy)
    assert bounds['available'] is True, bounds['reason']
    assert bounds['min_date'] < '2025-01-01'
    too_long = {'period': 'M', 'definition': leaf('ma250')}
    assert availability(market, too_long)['available'] is False


def test_monthly_strategy_daily_new_high_does_not_require_sixty_months():
    market = PeriodMarket()
    for row in market.rows: row['high'] = row['close']
    strategy = {'period': 'M', 'definition': leaf('close_breakout_high', 'new_high', 60, period='D')}
    bounds = availability(market, strategy)
    assert bounds['available'] is True, bounds['reason']
    assert bounds['daily_lookback_sessions'] == 60
    _, _, rows, _ = prepare(market, strategy, '2026-08-03', '2026-10-13')
    assert rows and all(row['checks']['lookback_sessions'] == 60 for row in rows)
