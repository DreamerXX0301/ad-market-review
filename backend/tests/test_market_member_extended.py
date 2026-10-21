"""Member metrics are selected-day, batched facts; turnover remains sparse."""
from copy import deepcopy
from hashlib import sha256

import pytest

from app.services import market_topic_members as service
from app.services.member_turnover import turnover_for, turnover_index
from app.services.topic_member_metrics import member_metrics
from test_market_topic_members import archives, topic

A, B, C, D, E = '600001.SH', '000001.SZ', '300001.SZ', '688001.SH', '920001.BJ'
DAY = '2026-10-20'


class MonthlyMarket:
    def __init__(self):
        self.days = ['2025-12-31', '2026-09-30', '2026-10-08', '2026-10-09',
                     '2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15',
                     '2026-10-16', '2026-10-19', DAY, '2026-10-21']
        self.master = {symbol: {'instrument_id': symbol, 'code': symbol[:6],
                              'name': symbol, 'kind': 'stock', 'list_date': '2020-01-01'}
                       for symbol in (A, B, C, D, E)}
        self.data = {}; self.events = {}; self.requests = []; self.action_requests = []
        self.reference_data = {}; self.coverage = {}
        for symbol in self.master:
            self.data[symbol] = []
            for i, day in enumerate(self.days):
                close = 10 if i == 0 else 15 if i == 1 else 20 if day == DAY else 16+i*.1
                self.data[symbol].append({'instrument_id': symbol, 'date': day, 'open': close,
                                          'high': close+.1, 'low': close-.1, 'close': close,
                                          'volume': 100, 'amount': close*100})

    def calendar(self): return list(self.days)
    def status(self): return {'snapshot_id': 'market-current', 'data_end': self.days[-1], 'coverage': self.coverage}
    def reference(self): return deepcopy(self.reference_data)
    def instruments(self, **kwargs): return deepcopy(list(self.master.values()))
    def history_for_instruments(self, symbols, start=None, end=None):
        self.requests.append({'symbols': set(symbols), 'start': start, 'end': end})
        # Deliberately return future rows too, to verify the common guard.
        return {symbol: deepcopy(self.data.get(symbol, [])) for symbol in symbols}
    def actions_for_instruments(self, symbols):
        self.action_requests.append(set(symbols))
        return {symbol: deepcopy(self.events.get(symbol, [])) for symbol in symbols}
    def history_panel(self, *args, **kwargs): raise AssertionError('no all-market history scans')
    def bars(self, *args, **kwargs): raise AssertionError('no per-security history scans')

    def quotes(self, day=DAY):
        return {symbol: {**next(row for row in rows if row['date'] == day), 'change_pct': 2}
                for symbol, rows in self.data.items() if any(row['date'] == day for row in rows)}


def calculate(market, day=DAY, symbols=(A,), supplement=None):
    return member_metrics(market, day, symbols, market.master, market.quotes(day), supplement or {})


def pool(day, symbols, *, price=20, rate=12.3):
    return {'date': day, 'source': 'eastmoney-public', 'complete': True,
            'items': [{'instrument_id': symbol, 'date': day, 'close': price, 'turnover_rate': rate}
                      for symbol in symbols]}


def test_mtd_is_last_month_close_to_selected_day_not_thirty_sessions():
    market = MonthlyMarket(); row = calculate(market)[A]
    assert row['return_ytd_pct'] == pytest.approx(100)
    assert row['return_mtd_pct'] == pytest.approx((20/15-1)*100)
    assert row['return_mtd_coverage']['start'] == '2026-09-30'
    assert row['return_mtd_coverage']['end'] == DAY and row['return_mtd_coverage']['complete']
    assert row['change_30d_pct'] is None
    assert market.requests == [{'symbols': {A}, 'start': '2025-12-31', 'end': DAY}]
    assert market.action_requests == [{A}]


def test_mtd_missing_endpoint_new_listing_missing_quote_and_future_rows_remain_unknown():
    market = MonthlyMarket()
    market.data[A] = [row for row in market.data[A] if row['date'] != '2026-09-30']
    market.master[B]['list_date'] = '2026-10-08'
    market.data[C] = [row for row in market.data[C] if row['date'] != DAY]
    output = calculate(market, symbols=(A, B, C))
    assert all(output[symbol]['return_mtd_pct'] is None and output[symbol]['return_mtd_reason'] for symbol in (A, B, C))
    assert output[A]['return_ytd_pct'] is not None
    assert output[C]['return_mtd_coverage']['end'] == DAY


def test_mtd_uses_same_verified_action_basis_and_quarantines_bad_action():
    market = MonthlyMarket()
    market.events[A] = [{'date': '2026-10-16', 'dividend_per_share': 1.0,
                         'per_share_bonus': 0, 'allotment_ratio': 0,
                         'allotment_price': 0, 'currency': 'CNY'}]
    row = calculate(market)[A]
    assert row['return_mtd_pct'] == pytest.approx((20/14-1)*100)
    assert row['return_mtd_coverage']['price_basis'] == 'forward_event_affine'
    market.events[A][0]['currency'] = 'USD'
    row = calculate(market)[A]
    assert row['return_mtd_pct'] is None and row['return_mtd_reason']


def test_nontrading_month_end_needs_matching_retained_calendar_proof():
    market = MonthlyMarket(); market.days = ['2026-01-30', '2026-02-02', '2026-02-03']
    for symbol in market.master:
        market.data[symbol] = [{'instrument_id': symbol, 'date': day, 'open': value,
                               'high': value, 'low': value, 'close': value, 'volume': 100}
                              for day, value in zip(market.days, (10, 11, 12))]
    assert calculate(market, day='2026-02-03')[A]['return_mtd_pct'] is None
    proof = {'basis': 'source_derived_cross_checked', 'count': 3,
             'start': market.days[0], 'end': market.days[-1],
             'dates_sha256': sha256('\n'.join(market.days).encode()).hexdigest(),
             'rules': {'all_four_indices_equal_bulk': True, 'official_overlap_equal': True}}
    market.coverage = {'calendar_provenance': proof}
    row = calculate(market, day='2026-02-03')[A]
    assert row['return_mtd_pct'] == pytest.approx(20) and row['return_mtd_coverage']['start'] == '2026-01-30'
    proof['dates_sha256'] = 'tampered'
    assert calculate(market, day='2026-02-03')[A]['return_mtd_pct'] is None


def test_member_endpoint_enriches_only_selected_members_and_board_tags(archives):
    market = MonthlyMarket()
    archives('market-current', DAY, [topic([A, C, D, E], observed=DAY)])
    supplement = {'public_pools': {DAY: pool(DAY, [A])}}
    reference = {'_supplement': supplement}
    result = service.list_members(market, {'snapshot_id': 'market-current', 'data_end': DAY},
                                  DAY, reference, list(market.quotes().values()), topic_id='fuyao-881001.TI')
    by_id = {row['instrument_id']: row for row in result['items']}
    assert {symbol: row['board'] for symbol, row in by_id.items()} == {A: 'main', C: 'chinext', D: 'star', E: 'beijing'}
    assert by_id[A]['board_label'] == '主板' and '不是历史身份档' in by_id[A]['board_scope']
    assert by_id[A]['return_mtd_pct'] == pytest.approx((20/15-1)*100)
    assert by_id[A]['turnover_rate'] == 12.3 and by_id[A]['turnover_rate_source'] == 'eastmoney-public'
    assert by_id[C]['turnover_rate'] is None and by_id[C]['turnover_rate_reason']
    assert result['metric_coverage']['turnover_known'] == 1
    assert market.requests[0]['symbols'] == {A, C, D, E} and len(market.requests) == 1


def test_unknown_board_does_not_default_to_main(archives):
    market = MonthlyMarket(); market.master[A]['instrument_id'] = '999999.SH'; market.master[A]['code'] = '999999'
    archives('market-current', DAY, [topic([A], observed=DAY)])
    result = service.list_members(market, {'snapshot_id': 'market-current', 'data_end': DAY},
                                  DAY, {}, list(market.quotes().values()), topic_id='fuyao-881001.TI')
    row = result['items'][0]
    assert row['board'] == 'unknown' and row['board_label'] == '未知' and row['board_reason']


def test_event_metrics_retain_partial_year_lower_bound_and_only_observed_latest(archives):
    market = MonthlyMarket(); latest = '2026-10-19'
    price = market.quotes(latest)[A]['close']
    supplement = {'annual_pools': {latest: {'date': latest, 'source': 'fuyao', 'pool_kind': 'limitup',
                  'items': [{'instrument_id': A, 'date': latest, 'last_price': price}],
                  'complete': True, 'pagination_complete': True, 'raw_codes': [A[:6]],
                  'ambiguous_code_rows': 0, 'rejected_codes': []}},
                  'public_pools': {'2026-10-21': pool('2026-10-21', [A], price=17.1)}}
    row = calculate(market, supplement=supplement)[A]
    assert row['limit_up_ytd'] is None and row['limit_up_ytd_observed'] == 1
    assert row['last_limit_up_date'] == latest and row['sessions_since_limit_up'] == 1
    assert row['last_limit_up_complete'] is False and row['last_limit_up_reason']
    assert row['return_mtd_coverage']['end'] == DAY


@pytest.mark.parametrize('change', ['record_date', 'row_date', 'price', 'missing_quote'])
def test_turnover_rejects_stale_or_mismatched_source_without_filling_zero(change):
    record = pool(DAY, [A]); quote = {'date': DAY, 'close': 20}
    if change == 'record_date': record['date'] = '2026-10-21'
    elif change == 'row_date': record['items'][0]['date'] = '2026-10-21'
    elif change == 'price': record['items'][0]['close'] = 30
    else: quote = None
    result = turnover_for(A, DAY, quote, turnover_index({}, {'public_pools': {DAY: record}}, DAY))
    assert result['turnover_rate'] is None and result['turnover_rate_reason']


def test_turnover_does_not_use_cny_turnover_or_volume_as_a_ratio():
    result = turnover_for(A, DAY, {'date': DAY, 'close': 20, 'turnover': 150000, 'volume': 10000}, {})
    assert result['turnover_rate'] is None and result['turnover_rate_unit'] == '%'


def test_same_day_fuyao_historical_pool_ratio_is_allowed_but_latest_is_not_backfilled():
    reference = {'pool_trade_date': '2026-10-21',
                 'breakpool': [{'thscode': A, 'last_price': 20, 'turnover_ratio_pct': 88}],
                 'pool_history': {DAY: {'limitdown': [{'thscode': A, 'last_price': 20, 'turnover_ratio_pct': 5}]}}}
    index = turnover_index(reference, {}, DAY)
    row = turnover_for(A, DAY, {'date': DAY, 'close': 20}, index)
    assert row['turnover_rate'] == 5 and row['turnover_rate_source'] == 'fuyao'
    assert row['turnover_rate_date'] == DAY


def test_turnover_conflicting_same_source_rows_unknown_and_native_zero_is_valid():
    record = pool(DAY, [A]); record['items'].append({**record['items'][0], 'turnover_rate': 99})
    row = turnover_for(A, DAY, {'date': DAY, 'close': 20}, turnover_index({}, {'public_pools': {DAY: record}}, DAY))
    assert row['turnover_rate'] is None and '冲突' in row['turnover_rate_reason']
    row = turnover_for(A, DAY, {'date': DAY, 'close': 20, 'turnover_ratio_pct': 0}, {})
    assert row['turnover_rate'] == 0 and row['turnover_rate_date'] == DAY


def scalar_daily_states(symbol, closes, calendar, compiled):
    """Original pre-optimization loop, including original proof order/reasons."""
    from app.services.chart_limits import _evidence, _combine
    output = {}
    for day in calendar:
        states = {}
        for kind in ('up', 'down'):
            proofs = []
            for source in compiled[day]['kinds'][kind]:
                record = source['record']; indexed = source['indexed']; original = record.get('items')
                narrowed = {**record, 'items': indexed.get(symbol[:6]) or original[:1]} if isinstance(original, list) and original else record
                proofs.append(_evidence(narrowed, symbol, day, closes.get(day), kind=kind, **source['options']))
            states[kind] = _combine(proofs)
        if any(states[kind]['status'] == 'conflict' for kind in states) or (states['up']['state'] is True and states['down']['state'] is True):
            states['up'] = {**states['up'], 'state': None, 'status': 'conflict', 'reason': '同日涨跌停或来源价格证据冲突'}
        output[day] = states['up']
    return output


@pytest.mark.parametrize('mode', ['complete', 'partial', 'raw_only', 'isolated', 'quarantine_identity',
                                  'wrong_date', 'wrong_kind', 'wrong_price', 'wrong_exchange',
                                  'empty', 'missing_items', 'bad_raw_codes', 'duplicate_raw_codes',
                                  'cross_provider_conflict', 'both_directions', 'public_absence'])
def test_compiled_daily_proof_matches_original_scalar_fields_and_reasons(mode):
    from app.services import topic_member_metrics as metrics
    record = {'date': DAY, 'pool_kind': 'limitup', 'source': 'fuyao', 'scope': 'fixture source scope',
              'items': [{'instrument_id': A, 'date': DAY, 'last_price': 20}],
              'complete': True, 'pagination_complete': True, 'ambiguous_code_rows': 0,
              'raw_codes': [A[:6]], 'rejected_codes': [], 'quarantine': []}
    supplement = {'annual_pools': {DAY: record}}
    if mode == 'partial': record.update(complete=False, pagination_complete=False)
    elif mode == 'raw_only': record.update(complete=False, raw_codes=[A[:6], B[:6]])
    elif mode == 'isolated': record.update(complete=False, raw_codes=[A[:6], B[:6]], rejected_codes=[B[:6]])
    elif mode == 'quarantine_identity': record.update(complete=False, quarantine=[{'thscode': B, 'ticker': C[:6]}])
    elif mode == 'wrong_date': record['date'] = '2026-10-21'
    elif mode == 'wrong_kind': record['pool_kind'] = 'limitdown'
    elif mode == 'wrong_price': record['items'][0]['last_price'] = 99
    elif mode == 'wrong_exchange': record['items'][0]['instrument_id'] = A[:6]+'.BJ'
    elif mode == 'empty': record['items'] = []
    elif mode == 'missing_items': record.pop('items')
    elif mode == 'bad_raw_codes': record.update(complete=False, raw_codes=None)
    elif mode == 'duplicate_raw_codes': record.update(complete=False, raw_codes=[A[:6], A[:6]])
    elif mode == 'cross_provider_conflict': supplement['public_pools'] = {DAY: pool(DAY, [A], price=99)}
    elif mode == 'both_directions': supplement['annual_down_pools'] = {DAY: {**deepcopy(record), 'pool_kind': 'limitdown'}}
    elif mode == 'public_absence': supplement = {'public_pools': {DAY: pool(DAY, [A])}}
    before = deepcopy(supplement)
    calendar = ['2026-10-19', DAY]
    compiled = metrics._sources({}, supplement, calendar)
    for symbol in (A, B, C, D, '000000.SH', '000003.SH'):
        for closes in ({DAY: 20}, {DAY: None}, {}):
            assert metrics._daily_states(symbol, closes, calendar, compiled) == scalar_daily_states(symbol, closes, calendar, compiled)
    assert supplement == before


def test_absence_and_missing_quote_proofs_are_compiled_once_not_per_member(monkeypatch):
    from app.services import topic_member_metrics as metrics
    original = metrics._evidence; calls = []
    def counted(*args, **kwargs):
        calls.append((args[1], args[2], args[3]))
        return original(*args, **kwargs)
    monkeypatch.setattr(metrics, '_evidence', counted)
    record = {'date': DAY, 'pool_kind': 'limitup', 'items': [{'instrument_id': A, 'date': DAY, 'last_price': 20}],
              'complete': False, 'pagination_complete': True, 'ambiguous_code_rows': 0,
              'raw_codes': [A[:6], B[:6]], 'rejected_codes': [B[:6]], 'quarantine': []}
    sources = metrics._sources({}, {'annual_pools': {DAY: record}}, [DAY])
    prepared_calls = len(calls)
    assert prepared_calls == 2
    for number in range(100, 1100):
        symbol = f'{number:06d}.SH'
        assert metrics._daily_states(symbol, {DAY: 20}, [DAY], sources)[DAY]['state'] is False
        assert metrics._daily_states(symbol, {}, [DAY], sources)[DAY]['state'] is None
    assert len(calls) == prepared_calls
    assert metrics._daily_states(B, {DAY: 20}, [DAY], sources)[DAY]['state'] is None
    assert metrics._daily_states(A, {DAY: 99}, [DAY], sources)[DAY]['status'] == 'conflict'
    assert len(calls) == prepared_calls + 2


def test_compiled_sources_preserve_hot_topic_candidate_tuple_contract():
    from app.services import topic_member_metrics as metrics
    record = {'date': DAY, 'pool_kind': 'limitup', 'items': [{'instrument_id': A, 'date': DAY, 'last_price': 20}],
              'complete': True, 'pagination_complete': True, 'ambiguous_code_rows': 0,
              'raw_codes': [A[:6]], 'rejected_codes': [], 'quarantine': []}
    prepared = metrics._sources({}, {'annual_pools': {DAY: record}}, [DAY])[DAY]
    assert isinstance(prepared['up'], list) and prepared['down'] == []
    source, indexed, options = prepared['up'][0]
    assert source is record and indexed[A[:6]] == record['items']
    assert options == {'source': 'fuyao', 'path': 'annual_pools.'+DAY, 'granular': True, 'allow_absence': True}
    assert prepared['kinds']['up'][0]['record'] is source
    assert {code for _, indexed, _ in prepared['up'] for code in indexed} == {A[:6]}
    assert prepared['default']['state'] is False and prepared['missing']['state'] is None
