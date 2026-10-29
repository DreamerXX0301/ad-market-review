"""Auction board labels are bounded read projections, not frozen market facts."""
from copy import deepcopy
from hashlib import sha256
import json

from fastapi import HTTPException
import pytest

from app import core, worker
from app.services import auction_service as service, source_service
from test_auction_snapshots import DAY, PREVIOUS, claim, isolated


CASES = [
    ('600001.SH', 'main', '主板'), ('000001.SZ', 'main', '主板'),
    ('300001.SZ', 'chinext', '创业板'), ('301001.SZ', 'chinext', '创业板'),
    ('688001.SH', 'star', '科创板'), ('689001.SH', 'star', '科创板'),
    ('430001.BJ', 'beijing', '北交所'), ('830001.BJ', 'beijing', '北交所'),
    ('920001.BJ', 'beijing', '北交所'), ('123456.SH', 'unknown', '未知'),
    ('600001.SZ', 'unknown', '未知'), ('300001.SH', 'unknown', '未知'),
    ('688001.SZ', 'unknown', '未知'), ('920001.SH', 'unknown', '未知'),
    ('600001.BJ', 'unknown', '未知'), ('123456.BJ', 'unknown', '未知'),
]


def snapshot_row():
    with core.connect() as connection:
        return dict(connection.execute('SELECT * FROM market_auction_snapshot').fetchone())


def test_auction_get_derives_boards_from_validated_original_without_other_market_reads(isolated, monkeypatch):
    codes = [case[0] for case in CASES]
    isolated.previous['event_cohorts'] = {
        'limit_up': {'date': PREVIOUS, 'items': [{'instrument_id': code} for code in codes]}}
    worker.dispatch(claim(isolated))
    original = snapshot_row()
    stored = json.loads(original['payload_json'])
    calls = deepcopy(isolated.source.calls)
    monkeypatch.setattr(service, 'get_market', lambda: pytest.fail('GET must not read current instruments'))
    monkeypatch.setattr(source_service, 'client', lambda *_args: pytest.fail('GET must not fetch'))
    response = isolated.client.get('/api/v1/market/auction', params={'date': DAY})
    assert response.status_code == 200
    view = response.json()
    assert set(view['stock_boards']) == set(codes)
    for code, board, label in CASES:
        fields = view['stock_boards'][code]
        assert fields['board'] == board and fields['board_label'] == label
        assert '不是历史身份档' in fields['board_scope'] and '不用于推断历史涨跌停制度' in fields['board_scope']
        assert bool(fields['board_reason']) is (board == 'unknown')
        assert set(fields) == {'board', 'board_label', 'board_scope', 'board_reason'}
    assert view['snapshot']['content_sha256'] == original['content_sha256']
    assert view['snapshot']['payload'] == {key: value for key, value in stored.items() if key != 'evidence'}
    assert 'stock_boards' not in view['snapshot']['payload']
    assert all('board' not in row for row in view['snapshot']['payload']['quotes']['items'])
    assert service.read_view(DAY)['stock_boards'] == view['stock_boards']
    assert snapshot_row() == original and isolated.source.calls == calls


def test_board_projection_cannot_expand_beyond_100_frozen_codes(isolated, monkeypatch):
    codes = [f'{600100 + offset:06}.SH' for offset in range(101)]
    isolated.previous['event_cohorts'] = {
        'limit_up': {'date': PREVIOUS, 'items': [{'instrument_id': code} for code in codes]}}
    worker.dispatch(claim(isolated))
    original = snapshot_row()
    calls = []
    from app.services import market_broken_boards
    classify = market_broken_boards._board_fields
    monkeypatch.setattr(market_broken_boards, '_board_fields', lambda code: (calls.append(code), classify(code))[1])
    view = service.read_view(DAY)
    frozen = [row['instrument_id'] for row in view['snapshot']['payload']['universe']['items']]
    assert len(frozen) == len(view['stock_boards']) == len(calls) == 100
    assert calls == frozen and set(view['stock_boards']) == set(frozen)
    assert set(codes) - set(frozen) == {'600200.SH'}
    assert snapshot_row() == original

    # A forged oversize body with a recomputed body SHA still fails the original
    # universe validation; board classification cannot bless it as a new fact.
    payload = json.loads(original['payload_json'])
    payload['universe']['items'].append({'instrument_id': '600200.SH'})
    payload['quotes']['items'].append({'instrument_id': '600200.SH'})
    forged = dict(original, payload_json=core.dumps(payload))
    forged['content_sha256'] = sha256(forged['payload_json'].encode()).hexdigest()
    with pytest.raises(HTTPException) as error:
        service._snapshot_view(forged)
    assert error.value.status_code == 409 and len(calls) == 100


def test_absent_snapshot_has_empty_board_projection_and_read_does_not_create_work(isolated):
    with core.connect() as connection:
        before = {table: connection.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                  for table in ('job', 'job_event', 'market_auction_capture', 'market_auction_snapshot')}
    view = service.read_view(DAY)
    assert view['snapshot'] is None and view['stock_boards'] == {}
    assert isolated.source.calls == []
    with core.connect() as connection:
        assert before == {table: connection.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in before}
