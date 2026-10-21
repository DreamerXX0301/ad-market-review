"""Quote-only browsing matches canonical metrics without full-history work."""
from copy import deepcopy

import pytest

from app.domain.conditions import metrics_for
from app.services.tracking_quotes import tracking_quotes, clear_tracking_quote_cache

CALENDAR = [f'2026-01-{day:02}' for day in range(5, 15)]
SYMBOL = '600001.SH'


class Market:
    def __init__(self, path):
        self.data_dir = path; self.generation = 'market-a'; self.reads = 0
        self.data = [{'date': day, 'open': 10+i, 'high': 12+i, 'low': 9+i,
                      'close': 11+i, 'volume': 100., 'amount': 1000., 'corporate_action': False}
                     for i, day in enumerate(CALENDAR)]
    def status(self): return {'snapshot_id': self.generation}
    def history_for_instruments(self, symbols, start=None, end=None):
        self.reads += 1
        return {symbol: [deepcopy(row) for row in self.data if start <= row['date'] <= end] for symbol in symbols}
    def corporate_actions_between(self, symbol, start, end):
        return [{'date': row['date']} for row in self.data if start <= row['date'] <= end and row.get('corporate_action')]
    def actions_for_instruments(self, symbols):
        return {symbol: [{'date': row['date']} for row in self.data if row.get('corporate_action')] for symbol in symbols}


def round_(day=None):
    return {'instrument_id': SYMBOL, 'status': 'condition_ended' if day else 'tracking',
            'end_business_date': day}


@pytest.mark.parametrize('case', ['normal', 'gap', 'action', 'previous_action', 'invalid_current', 'invalid_previous', 'duplicate', 'missing_current'])
def test_quote_only_matches_full_canonical_display_fields(tmp_path, case):
    clear_tracking_quote_cache(); market = Market(tmp_path); day = CALENDAR[-1]
    if case == 'gap': market.data.pop(-2)
    if case == 'action': market.data[-1]['corporate_action'] = True
    if case == 'previous_action': market.data[-2]['corporate_action'] = True
    if case == 'invalid_current': market.data[-1]['high'] = None
    if case == 'invalid_previous': market.data[-2]['open'] = None
    if case == 'duplicate': market.data.append(deepcopy(market.data[-1]))
    if case == 'missing_current': market.data.pop()
    expected = metrics_for(market.data, day, CALENDAR)
    result = tracking_quotes(market, [round_()], CALENDAR, day)[(SYMBOL, day)]
    assert result == {key: expected.get(key) for key in ('close', 'change_pct')}
    assert market.reads == 1


def test_cache_is_bounded_to_immutable_generation_date_and_storage(tmp_path):
    clear_tracking_quote_cache(); market = Market(tmp_path)
    day = CALENDAR[-1]
    first = tracking_quotes(market, [round_()], CALENDAR, day)
    first[(SYMBOL, day)]['close'] = -1
    second = tracking_quotes(market, [round_()], CALENDAR, day)
    assert second[(SYMBOL, day)]['close'] > 0 and market.reads == 1
    market.generation = 'market-b'
    tracking_quotes(market, [round_()], CALENDAR, day)
    assert market.reads == 2
    tracking_quotes(market, [round_()], CALENDAR, CALENDAR[-2])
    assert market.reads == 3
    market.data_dir = tmp_path/'restored'
    tracking_quotes(market, [round_()], CALENDAR, day)
    assert market.reads == 4


def test_closed_round_uses_its_end_date_and_batches_multiple_pairs(tmp_path):
    clear_tracking_quote_cache(); market = Market(tmp_path)
    end = CALENDAR[-3]; current = CALENDAR[-1]
    result = tracking_quotes(market, [round_(), round_(end), round_()], CALENDAR, current)
    assert len(result) == 2 and market.reads == 1
    assert result[(SYMBOL, end)]['close'] == metrics_for(market.data, end, CALENDAR)['close']


def test_empty_list_never_reads_quotes(tmp_path):
    market = Market(tmp_path)
    assert tracking_quotes(market, [], CALENDAR, CALENDAR[-1]) == {}
    assert market.reads == 0


def test_missing_calendar_context_cannot_poison_later_same_generation_quote(tmp_path):
    clear_tracking_quote_cache();market=Market(tmp_path);day=CALENDAR[-1]
    unknown=tracking_quotes(market,[round_()],[],day,strict_close=True)[(SYMBOL,day)]
    assert unknown=={'close':market.data[-1]['close'],'change_pct':None}
    valid=tracking_quotes(market,[round_()],CALENDAR,day,strict_close=True)[(SYMBOL,day)]
    assert valid['close']==market.data[-1]['close'] and valid['change_pct'] is not None
    assert market.reads==2


@pytest.mark.parametrize('proof', [None, {SYMBOL: [{'date': None}]}, {SYMBOL: [{'date': 'not-a-date'}]}])
def test_unknown_action_coverage_does_not_become_known_no_action(tmp_path, proof):
    clear_tracking_quote_cache(); market = Market(tmp_path)
    market.actions_for_instruments = lambda symbols: proof
    day = CALENDAR[-1]
    value = tracking_quotes(market, [round_()], CALENDAR, day)[(SYMBOL, day)]
    assert value['close'] == market.data[-1]['close']
    assert value['change_pct'] is None


def test_valid_actions_outside_calendar_do_not_erase_current_comparison(tmp_path):
    clear_tracking_quote_cache(); market = Market(tmp_path)
    market.actions_for_instruments = lambda symbols: {SYMBOL: [{'date': '2001-01-01'}, {'date': '2026-12-31'}]}
    day = CALENDAR[-1]
    value = tracking_quotes(market, [round_()], CALENDAR, day)[(SYMBOL, day)]
    assert value == {key: metrics_for(market.data, day, CALENDAR)[key] for key in ('close', 'change_pct')}


@pytest.mark.parametrize('case',['duplicate','zero_volume'])
def test_strict_personal_close_cache_is_separate_from_default_tracking_display(tmp_path,case):
    clear_tracking_quote_cache();market=Market(tmp_path);day=CALENDAR[-1]
    if case=='duplicate':market.data.append(deepcopy(market.data[-1]))
    else:market.data[-1]['volume']=0
    default=tracking_quotes(market,[round_()],CALENDAR,day)[(SYMBOL,day)]
    strict=tracking_quotes(market,[round_()],CALENDAR,day,strict_close=True)[(SYMBOL,day)]
    assert default['close'] is not None
    assert strict=={'close':None,'change_pct':None}
    assert market.reads==2
    assert tracking_quotes(market,[round_()],CALENDAR,day,strict_close=True)[(SYMBOL,day)]==strict
    assert tracking_quotes(market,[round_()],CALENDAR,day)[(SYMBOL,day)]==default
    assert market.reads==2
