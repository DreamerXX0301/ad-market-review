"""Selected result groups keep all counts but do not decode other candidates."""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from app.services import strategies, sector_strategies
from test_workspace import ws, make_strategy
from test_sector_strategies import workspace, isolated, cached, create


def test_stock_group_reads_and_post_idempotency_keep_original_complete_result(ws, monkeypatch):
    _, client = ws
    strategy = make_strategy(client)
    sid = strategy['strategy_id']; path = '/api/v1/strategies/'+sid+'/runs'
    selected = client.post(path+'?response_decision=matched', json={'request_key': 'same-projection'})
    assert selected.status_code == 200, selected.text
    selected = selected.json(); rid = selected['run_id']
    complete = strategies.get_run(rid)
    assert len(complete['items']) == 2 and len(selected['items']) == selected['matched_count'] == 1
    assert sum(complete[key+'_count'] for key in ('matched', 'unknown', 'unmatched')) == 2
    decoded = []; original = strategies._candidate
    def spy(row, master):
        decoded.append(row['decision'])
        return original(row, master)
    monkeypatch.setattr(strategies, '_candidate', spy)
    for decision in ('matched', 'unknown', 'unmatched'):
        decoded.clear()
        actual = client.get('/api/v1/strategy-runs/'+rid+'?decision='+decision).json()
        expected = {**complete, 'items': [row for row in complete['items'] if row['decision'] == decision]}
        assert actual == expected
        assert decoded == [decision] * len(actual['items'])
    again = client.post(path+'?response_decision=matched', json={'request_key': 'same-projection'})
    assert again.status_code == 200 and again.json() == selected
    assert client.get('/api/v1/strategy-runs/'+rid+'?decision=invalid').status_code == 422


def test_sector_group_counts_and_total_preserve_whole_universe(workspace, monkeypatch):
    cached(workspace)
    strategy = create(); sid = strategy['strategy_id']
    selected = sector_strategies.run_strategy(sid, {'request_key': 'same-sector-projection'}, response_decision='matched')
    complete = sector_strategies.get_run(selected['run_id'])
    assert complete['total'] == selected['total'] == 2
    assert len(complete['items']) == 2 and len(selected['items']) == 1
    decoded = []; original = sector_strategies._candidate
    def spy(row):
        decoded.append(row['decision'])
        return original(row)
    monkeypatch.setattr(sector_strategies, '_candidate', spy)
    for decision in ('matched', 'unknown', 'unmatched'):
        decoded.clear()
        actual = sector_strategies.get_run(selected['run_id'], decision)
        expected = {**complete, 'items': [row for row in complete['items'] if row['decision'] == decision]}
        assert actual == expected and decoded == [decision] * len(actual['items'])
    assert sector_strategies.run_strategy(sid, {'request_key': 'same-sector-projection'}, response_decision='matched') == selected
    with pytest.raises(HTTPException):
        sector_strategies.run_strategy(sid, {}, response_decision='invalid')
