"""Listing-board filters share the verified stock universe and precede metrics."""
import pytest

from app.domain.universe import BOARDS
from app.services import topic_members
from test_topic_members import A, B, C, D, E, memberships
from test_workspace import ws


def members(client, selected, boards=None, **params):
    query = [('topic_ids', ident) for ident in selected]
    query += [('boards', value) for value in (boards or [])]
    query += list(params.items())
    response = client.get('/api/v1/topic-members', params=query)
    assert response.status_code == 200, response.text
    return response.json()


def symbols(data):
    return [item['instrument_id'] for item in data['items']]


def test_board_union_combines_with_category_union_intersection_and_search(memberships):
    market, client, ids, _, _ = memberships
    selected = [ids['概念甲'], ids['概念乙']]
    assert set(symbols(members(client, selected))) == {A, B, C, D, E}
    assert symbols(members(client, selected, ['main'])) == [B, A]
    combined = members(client, selected, ['star', 'chinext'])
    assert symbols(combined) == [C, D]
    assert combined['boards'] == ['chinext', 'star'], 'canonical order is stable'
    assert symbols(members(client, selected, ['main'], match='all')) == [B]
    assert members(client, selected, ['chinext'], match='all')['total'] == 0
    assert symbols(members(client, selected, ['main'], query='甲股票')) == [A]
    assert members(client, selected, ['star'], query='甲股票')['total'] == 0
    assert combined['business_date'] == combined['quote_date'] == market.entry
    assert combined['metric_coverage']['total'] == combined['total'] == 2


@pytest.mark.parametrize('board,ident', [('chinext', C), ('star', D), ('beijing', E)])
def test_each_known_board_and_missing_quote_are_independent(memberships, board, ident):
    _, client, ids, _, _ = memberships
    data = members(client, [ids['概念甲'], ids['概念乙']], [board])
    assert symbols(data) == [ident]
    row = data['items'][0]
    assert row['board'] == board and row['board_label'] == BOARDS[board]
    assert row['board_reason'] is None and '不代表历史' in row['board_scope']
    if ident in (D, E):
        assert row['quote_available'] is False and row['close'] is None


@pytest.mark.parametrize('identity_kind', ['missing', 'index'])
def test_unverified_stock_identity_stays_unknown_and_is_not_guessed_from_code(memberships, monkeypatch, identity_kind):
    market, client, ids, _, _ = memberships
    original = market.instruments
    def instruments(*args, **kwargs):
        result = original(*args, **kwargs)
        return [({**row, 'kind': 'index'} if row['instrument_id'] == A else row)
                for row in result if identity_kind != 'missing' or row['instrument_id'] != A]
    monkeypatch.setattr(market, 'instruments', instruments)
    selected = [ids['概念甲']]
    unrestricted = members(client, selected)
    unknown = next(row for row in unrestricted['items'] if row['instrument_id'] == A)
    assert unknown['board'] == 'unknown' and unknown['board_reason']
    assert unknown['close'] == 10, 'identity unknown must not erase a verified quote'
    assert A not in symbols(members(client, selected, ['main']))
    assert A not in symbols(members(client, selected, list(BOARDS)))
    assert members(client, selected, list(BOARDS))['total'] == unrestricted['total'] - 1


def test_filter_precedes_expensive_metrics_and_counts_entire_sorted_result(memberships, monkeypatch):
    market, client, ids, _, quotes = memberships
    added = [f'603{number:03}.SH' for number in range(30)]
    catalog = market.topics()
    catalog[0]['instrument_ids'].extend(added)
    original = market.instruments
    monkeypatch.setattr(market, 'instruments', lambda *args, **kwargs: original(*args, **kwargs) + [
        {'instrument_id': ident, 'code': ident[:6], 'name': f'批量股票{index}', 'kind': 'stock'}
        for index, ident in enumerate(added)])
    quotes.extend({'instrument_id': ident, 'date': market.entry, 'close': 10,
                   'change_pct': index, 'amount': 100, 'turnover_ratio_pct': 1}
                  for index, ident in enumerate(added))
    metric_calls = []
    def metrics(_market, _day, candidates, *_args, **_kwargs):
        metric_calls.append(set(candidates))
        return {ident: {'change_30d_pct': index} for index, ident in enumerate(added) if ident in candidates}
    monkeypatch.setattr(topic_members, 'member_metrics', metrics)
    data = members(client, [ids['概念甲']], ['main'], sort_by='change_30d_pct')
    assert metric_calls == [set(added) | {A, B}]
    assert D not in metric_calls[0], 'discarded boards cannot read or compute historical metrics'
    assert data['total'] == data['metric_coverage']['total'] == 32
    assert data['metric_coverage']['change_30d_complete'] == 30
    assert symbols(data) == list(reversed(added)) + [B, A]
    assert len(data['items'][25:]) == 7, 'the API must not truncate before client pagination'
    empty = members(client, [ids['概念甲']], ['beijing'])
    assert empty['total'] == 0 and metric_calls[-1] == set()


@pytest.mark.parametrize('boards', [['unknown'], ['main', 'main'], ['MAIN'], ['main', 'chinext', 'star', 'beijing', 'main']])
def test_invalid_board_scope_rejected_without_reading_market(memberships, boards):
    _, client, ids, calls, _ = memberships
    before = dict(calls)
    response = client.get('/api/v1/topic-members', params=[('topic_ids', ids['概念甲'])] + [('boards', board) for board in boards])
    assert response.status_code == 422
    assert calls == before
