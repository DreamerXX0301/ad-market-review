"""Exact-session closing-pool conditions; fixtures never access live sources."""
from copy import deepcopy

import pytest

from app.analytics.backtest_engine import availability
from app.domain.conditions import evaluate, metrics_for, specs, validate
from app.services import strategy_limits
from app.services.strategy_limits import LimitConditionEvidence, batch_quote_indexes, quote_index
from test_workspace import ws, make_strategy, add_one, advance
from test_selection_generation_guard import publish_revision


SYMBOL = '600001.SH'
OTHER = '000001.SZ'
DAYS = ['2026-01-08', '2026-01-09', '2026-01-12', '2026-01-13']


def bars():
    return [{'date': day, 'open': 10, 'high': 11, 'low': 9,
             'close': 10, 'volume': 100, 'amount': 1000, 'limit_up': index != 2}
            for index, day in enumerate(DAYS)]


def event(day, symbol=SYMBOL, close=10, **extra):
    return {'instrument_id': symbol, 'ticker': symbol[:6], 'date': day,
            'last_price': close, **extra}


def pool(day, items=None, **extra):
    items = [event(day)] if items is None else items
    return {'date': day, 'items': items, 'source': 'fixture-verified-source',
            'pool_kind': 'limitup', 'complete': True,
            'pagination_complete': True, 'ambiguous_code_rows': 0,
            'raw_codes': [row['ticker'] for row in items], 'rejected_codes': [], **extra}


class EvidenceMarket:
    def __init__(self, reference=None):
        self.ref = reference or {}
        self.calls = []

    def reference(self): return self.ref
    def status(self): return {'snapshot_id': 'fixture-snapshot'}

    def history_for_instruments(self, symbols, start=None, end=None):
        self.calls.append((symbols, start, end))
        return {symbol: [row for row in bars() if start <= row['date'] <= end]
                for symbol in symbols}

    def bars(self, *args, **kwargs): raise AssertionError('no per-stock fallback')


def adapter(monkeypatch, supplement=None, reference=None):
    monkeypatch.setattr(strategy_limits, 'load_supplement', lambda snapshot, **_kwargs: deepcopy(supplement or {}))
    return LimitConditionEvidence(EvidenceMarket(reference))


def test_two_boolean_fields_have_one_today_entry_and_real_yes_no_values():
    fields = specs()
    assert sum(row['field'] == 'limit_up' for row in fields) == 1
    labels = {row['field']: row['label'] for row in fields}
    assert labels['limit_up'] == '今日是否涨停（收盘）'
    assert labels['prev_limit_up'] == '昨日是否涨停（前一市场交易日）'
    for field in ('limit_up', 'prev_limit_up'):
        for value in (False, True):
            node = validate({'field': field, 'op': '==', 'value': value})
            assert evaluate(node, {field: value})['result'] is True
        with pytest.raises(ValueError): validate({'field': field, 'op': '==', 'value': 'false'})


@pytest.mark.parametrize('actual', [None, 0, 1, '', 'false'])
@pytest.mark.parametrize('op,value', [('==', False), ('!=', True)])
def test_unknown_or_non_boolean_cannot_be_treated_as_no(actual, op, value):
    node = validate({'field': 'prev_limit_up', 'op': op, 'value': value})
    result = evaluate(node, {'prev_limit_up': actual, 'prev_limit_up_reason': '前一交易日证据待核验'})
    assert result['result'] is None
    assert result['actual'] is None and result['reason'] == '前一交易日证据待核验'


def test_domain_previous_is_exact_market_session_even_across_weekend():
    result = metrics_for(bars(), DAYS[2], DAYS)
    assert result['prev_limit_up'] is True and result['prev_limit_up_date'] == DAYS[1]
    assert result['limit_up'] is False and result['limit_up_date'] == DAYS[2]
    missing = [row for row in bars() if row['date'] != DAYS[1]]
    assert metrics_for(missing, DAYS[2], DAYS)['prev_limit_up'] is None
    suspended = bars(); suspended[1]['volume'] = 0
    assert metrics_for(suspended, DAYS[2], DAYS)['prev_limit_up'] is None
    outside = metrics_for(bars(), DAYS[2], [DAYS[0], DAYS[1], DAYS[3]])
    assert outside['limit_up'] is None and outside['prev_limit_up'] is None


def test_adapter_exact_dates_and_complete_source_absence(monkeypatch):
    prior, today = DAYS[1:3]
    evidence = adapter(monkeypatch, {'annual_pools': {
        prior: pool(prior), today: pool(today, [event(today, OTHER)])}})
    result = evidence.metrics(SYMBOL, today, DAYS, quote_index(bars()))
    assert result['prev_limit_up'] is True and result['prev_limit_up_status'] == 'confirmed'
    assert result['limit_up'] is False and result['limit_up_status'] == 'source_absent'
    assert result['prev_limit_up_date'] == prior and result['limit_up_date'] == today
    assert result['prev_limit_up_evidence']['snapshot_id'] == 'fixture-snapshot'
    assert result['prev_limit_up_evidence']['source_proofs'][0]['source'] == 'fixture-verified-source'


@pytest.mark.parametrize('mode', ['missing_quote', 'suspended', 'duplicate', 'no_pool', 'empty', 'quarantined', 'public_absent', 'future_only'])
def test_missing_or_unsupported_evidence_stays_unknown(monkeypatch, mode):
    today = DAYS[2]; rows = bars()
    supplement = {'annual_pools': {today: pool(today)}}
    if mode == 'missing_quote': rows = [row for row in rows if row['date'] != today]
    if mode == 'suspended': rows[2]['volume'] = 0
    if mode == 'duplicate': rows.append(dict(rows[2]))
    if mode == 'no_pool': supplement = {}
    if mode == 'empty': supplement = {'annual_pools': {today: pool(today, [])}}
    if mode == 'quarantined': supplement = {'annual_pools': {today: pool(today, [event(today, OTHER)], rejected_codes=[SYMBOL[:6]])}}
    if mode == 'public_absent': supplement = {'public_pools': {today: pool(today, [event(today, OTHER)])}}
    if mode == 'future_only': supplement = {'annual_pools': {DAYS[3]: pool(DAYS[3])}}
    result = adapter(monkeypatch, supplement).metrics(SYMBOL, today, DAYS, quote_index(rows))
    assert result['limit_up'] is None and result['limit_up_status'] == 'unknown'
    assert result['limit_up_reason']


@pytest.mark.parametrize('mode', ['wrong_price', 'wrong_date', 'wrong_identity', 'opposite_direction'])
def test_conflicting_source_does_not_get_hidden_by_valid_positive(monkeypatch, mode):
    today = DAYS[2]; bad = event(today)
    if mode == 'wrong_price': bad['last_price'] = 11
    if mode == 'wrong_date': bad['date'] = DAYS[3]
    if mode == 'wrong_identity': bad['instrument_id'] = SYMBOL[:6] + '.SZ'
    supplement = {'annual_pools': {today: pool(today, [bad])},
                  'public_pools': {today: pool(today)}}
    if mode == 'opposite_direction':
        supplement['annual_down_pools'] = {today: pool(today, pool_kind='limitdown')}
    result = adapter(monkeypatch, supplement).metrics(SYMBOL, today, DAYS, quote_index(bars()))
    assert result['limit_up'] is None and result['limit_up_status'] == 'conflict'


def test_base_pools_are_supported_but_breakpool_touch_is_not_closing_evidence(monkeypatch):
    today = DAYS[2]
    reference = {'pool_history': {DAYS[1]: {'limitup': [event(DAYS[1])]}},
                 'pool_trade_date': today, 'breakpool': [event(today)]}
    result = adapter(monkeypatch, reference=reference).metrics(SYMBOL, today, DAYS, quote_index(bars()))
    assert result['prev_limit_up'] is True and result['limit_up'] is None


def test_current_quote_missing_does_not_erase_independent_prior_session_proof(monkeypatch):
    prior, today = DAYS[1:3]
    rows = [row for row in bars() if row['date'] != today]
    evidence = adapter(monkeypatch, {'annual_pools': {prior: pool(prior)}})
    result = evidence.metrics(SYMBOL, today, DAYS, quote_index(rows))
    assert result['limit_up'] is None and result['prev_limit_up'] is True
    assert result['prev_limit_up_date'] == prior


def test_calendar_and_generation_mismatch_are_unknown_not_false(monkeypatch):
    today = DAYS[2]
    evidence = adapter(monkeypatch, {'snapshot_id': 'other-generation', 'annual_pools': {today: pool(today)}})
    assert evidence.metrics(SYMBOL, today, DAYS, quote_index(bars()))['limit_up'] is None
    evidence = adapter(monkeypatch, {'annual_pools': {today: pool(today)}})
    result = evidence.metrics(SYMBOL, today, DAYS[:2] + DAYS[3:], quote_index(bars()))
    assert result['limit_up'] is None and result['prev_limit_up'] is None
    assert '交易日历' in result['limit_up_reason']


def test_batch_quotes_one_bounded_read_or_reused_histories_without_stock_scans():
    market = EvidenceMarket()
    quotes = batch_quote_indexes(market, [SYMBOL, OTHER], DAYS[2], DAYS)
    assert market.calls == [([SYMBOL, OTHER], DAYS[1], DAYS[2])]
    assert set(quotes[SYMBOL]) == set(DAYS[1:3])
    reused = batch_quote_indexes(market, [SYMBOL], DAYS[2], DAYS, {SYMBOL: bars()})
    assert len(market.calls) == 1 and set(reused[SYMBOL]) == set(DAYS[1:3])
    assert batch_quote_indexes(object(), [SYMBOL], DAYS[2], DAYS) == {}


def workspace_sources(monkeypatch, market, supplements):
    monkeypatch.setattr(market, 'status', lambda: {'snapshot_id': 'fixture-snapshot'}, raising=False)
    monkeypatch.setattr(market, 'reference', lambda: {}, raising=False)
    monkeypatch.setattr(strategy_limits, 'load_supplement', lambda snapshot, **_kwargs: deepcopy(supplements))


@pytest.mark.parametrize('period', ['D', 'W'])
def test_strategy_execution_uses_daily_source_flags_and_freezes_evidence(ws, monkeypatch, period):
    market, client = ws; day = market.entry; prior = market.days[69]
    prior_price = market.data[SYMBOL][69]['close']
    price = market.data[SYMBOL][70]['close']
    supplement = {'annual_pools': {prior: pool(prior, [event(prior, close=prior_price)]),
                                  day: pool(day, [event(day, OTHER, market.data[OTHER][70]['close'])])}}
    workspace_sources(monkeypatch, market, supplement)
    calls = []
    def batch(symbols, start=None, end=None):
        calls.append((tuple(symbols), start, end))
        return {symbol: [row for row in market.data[symbol] if start <= row['date'] <= end] for symbol in symbols}
    monkeypatch.setattr(market, 'history_for_instruments', batch, raising=False)
    definition = {'all': [{'field': 'prev_limit_up', 'op': '==', 'value': True},
                          {'field': 'limit_up', 'op': '==', 'value': False}]}
    response = client.post('/api/v1/strategies', json={'name': '精确日证据', 'period': period, 'definition': definition})
    assert response.status_code == 200, response.text
    strategy = response.json()
    run = client.post('/api/v1/strategies/' + strategy['strategy_id'] + '/runs', json={})
    assert run.status_code == 200, run.text
    result = run.json(); matched = [row for row in result['items'] if row['decision'] == 'matched']
    assert [row['instrument_id'] for row in matched] == [SYMBOL]
    metrics = matched[0]['metrics']
    assert metrics['prev_limit_up_date'] == prior and metrics['limit_up_date'] == day
    assert metrics['indicator_period'] == period
    assert metrics['prev_limit_up_evidence']['source_proofs'][0]['state'] is True
    assert len(calls) == (1 if period == 'D' else 0)
    assert matched[0]['close_price'] == price
    # A source revision affects a later explicit run only, not saved selection evidence.
    supplement['annual_pools'][prior] = pool(prior, [])
    publish_revision(market,'market_review','daily-source-revised')
    next_run = client.post('/api/v1/strategies/' + strategy['strategy_id'] + '/runs', json={}).json()
    candidate = next(row for row in next_run['items'] if row['instrument_id'] == SYMBOL)
    assert candidate['decision'] == 'unknown' and candidate['metrics']['prev_limit_up_date'] == prior
    assert candidate['metrics']['prev_limit_up_status'] == 'unknown'
    saved = client.get('/api/v1/strategy-runs/' + result['run_id']).json()
    assert next(row for row in saved['items'] if row['instrument_id'] == SYMBOL)['metrics']['prev_limit_up'] is True


def test_tracking_previous_limit_alert_and_today_no_exit_use_same_dated_evidence(ws, monkeypatch):
    market, client = ws
    entry = market.entry; later = market.days[71]
    supplement = {'annual_pools': {entry: pool(entry, [event(entry, close=market.data[SYMBOL][70]['close'])]),
                                  later: pool(later, [event(later, OTHER, market.data[OTHER][71]['close'])])}}
    workspace_sources(monkeypatch, market, supplement)
    strategy = make_strategy(client, policy={
        'alerts': [{'field': 'prev_limit_up', 'op': '==', 'value': True}],
        'exits': [{'field': 'limit_up', 'op': '==', 'value': False}]})
    _, tracked = add_one(client, strategy)
    advance(market, 71)
    response = client.post('/api/v1/tracking-rounds/review', json={})
    assert response.status_code == 200, response.text
    assert response.json()['alerts'] == 1 and response.json()['ended'] == 1
    detail = client.get('/api/v1/tracking-rounds/' + tracked['round_id']).json()
    event_ = next(row for row in detail['events'] if row['event_type'] == 'condition_end')
    assert event_['business_date'] == later
    assert event_['evidence']['metrics']['prev_limit_up_date'] == entry
    assert event_['evidence']['metrics']['limit_up'] is False
    before = deepcopy(detail['events'])
    supplement.clear()
    assert client.post('/api/v1/tracking-rounds/review', json={}).json()['reviewed'] == 0
    assert client.get('/api/v1/tracking-rounds/' + tracked['round_id']).json()['events'] == before


def test_tracking_missing_previous_quote_does_not_alert_as_no(ws, monkeypatch):
    market, client = ws
    workspace_sources(monkeypatch, market, {})
    strategy = make_strategy(client, policy={'alerts': [{'field': 'prev_limit_up', 'op': '==', 'value': False}]})
    _, tracked = add_one(client, strategy)
    advance(market, 72)
    # The immediately preceding session is absent; an older quote must not stand in.
    market.data[SYMBOL] = [row for row in market.data[SYMBOL] if row['date'] != market.days[71]]
    response = client.post('/api/v1/tracking-rounds/review', json={})
    assert response.status_code == 200, response.text
    assert response.json()['alerts'] == 0
    detail = client.get('/api/v1/tracking-rounds/' + tracked['round_id']).json()
    review = next(row for row in detail['events'] if row['event_type'] == 'review' and row['business_date'] == market.days[72])
    assert review['evidence']['metrics']['prev_limit_up'] is None
    assert review['evidence']['metrics']['prev_limit_up_date'] == market.days[71]


@pytest.mark.parametrize('field', ['limit_up', 'prev_limit_up'])
def test_backtest_explicitly_rejects_unverified_historical_limit_conditions(field):
    class CalendarOnly:
        def calendar(self): return DAYS
    result = availability(CalendarOnly(), {'period': 'D', 'definition': {'field': field, 'op': '==', 'value': True}})
    assert result['available'] is False
    assert '涨停' in result['reason'] and ('证据' in result['reason'] or '来源' in result['reason'])
