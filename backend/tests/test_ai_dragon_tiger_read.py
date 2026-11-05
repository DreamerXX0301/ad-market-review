"""Synthetic retained CAS fixtures; AI helpers never collect or call a model."""
from copy import deepcopy
from datetime import datetime
import socket

import pytest
from fastapi import HTTPException

from app import core
from app.adapters import dragon_tiger as adapter
from app.services import ai_dragon_tiger_read as reader, ai_read_tools as tools
from app.services import ai_scope_semantic as semantic
from app.services.ai_tool_budget import classify_tool, LOCAL_TOOL_BUDGET
from test_dragon_tiger import DAY, MASTER, Market, board, reports, local, isolated


@pytest.fixture(autouse=True)
def no_acquisition_or_network(monkeypatch):
    def deny(*_args, **_kwargs):
        raise AssertionError('AI retained-data test must not collect or access a network')
    monkeypatch.setattr(socket, 'getaddrinfo', deny)
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(reader.dragon_tiger, 'collect_for_market', deny)
    monkeypatch.setattr(reader.dragon_tiger, 'execute_job', deny)


def retained(market, *, raw_sources=False):
    boards = {kind: adapter.normalize_board(board(kind=kind), DAY, kind, MASTER)
              for kind in ('all', 'org', 'hot_money')}
    seats = adapter.normalize_seats(reports(), DAY, MASTER, boards['all'])
    store = reader.dragon_tiger.Store()
    sources = [store.save({'version': adapter.VERSION, 'date': DAY,
                          'capability': 'fuyao-all', 'payload': board()})] if raw_sources else []
    record = {'version': adapter.VERSION, 'accepted': True, 'date': DAY,
              'snapshot_id': 'fixture-collected-generation', 'boards': boards, 'seats': seats,
              'source_objects': sources, 'warnings': []}
    store.accept(record, market.calendar())
    return store, record


def test_exact_name_or_code_reads_two_periods_only_and_preserves_anonymous_seats_and_sources(local, monkeypatch):
    market, provider, public = local
    store, _ = retained(market, raw_sources=True)
    before = {str(path): path.read_bytes() for path in store.root.rglob('*') if path.is_file()}
    # An AI read must use its already-bound generation, even if the ordinary
    # facade now points elsewhere. Existing HTTP callers retain their defaults.
    monkeypatch.setattr(reader.dragon_tiger, 'get_market', lambda: (_ for _ in ()).throw(AssertionError('global facade')))
    from app.services import market_service
    monkeypatch.setattr(market_service, 'get_market', lambda: (_ for _ in ()).throw(AssertionError('global facade')))
    calls = []
    read_seats = reader.dragon_tiger.read_seats
    def tracked(symbol, day, period, *, market):
        calls.append((symbol, day, period))
        return read_seats(symbol, day, period, market=market)
    monkeypatch.setattr(reader.dragon_tiger, 'read_seats', tracked)
    result = reader.read_data(DAY, market=market, query='fixture SZ')
    assert calls == [('000678.SZ', DAY, 1), ('000678.SZ', DAY, 3)]
    seats = result['projection']['stock_seats']['periods']
    record = seats['1']['records']['items'][0]
    assert record['range_days'] == 1 and record['summary']['buy_value'] == 100
    assert record['source']['provider'] == 'eastmoney' and record['source']['date'] == DAY
    anonymous = [row for row in record['buy']['items'] if row['name'] == '机构专用']
    assert len(anonymous) == 2 and all(row['source_id'] == '0' for row in anonymous)
    assert anonymous[0]['rank'] != anonymous[1]['rank']
    assert seats['3']['records']['observed_count'] == 0 and seats['3']['availability']['available'] is False
    assert result['dragon_tiger_binding']['collection_snapshot_id'] == 'fixture-collected-generation'
    assert len(result['dragon_tiger_binding']['day_record_sha256']) == 64
    assert len(result['dragon_tiger_binding']['source_object_sha256']) == 1
    assert provider.calls == public.calls == []
    assert before == {str(path): path.read_bytes() for path in store.root.rglob('*') if path.is_file()}
    assert reader.read_data(DAY, market=market, query='000678')['projection']['stock_seats']['instrument_id'] == '000678.SZ'


def test_periods_reported_net_conflicts_hot_labels_and_unknown_periods_never_merge():
    source = board(); first = source['data']['stock_items'][0]
    source['data'].update(count=42, stock_count=42,
        stock_items=[dict(first, ticker=f'{700000+i:06d}', thscode=f'{700000+i:06d}.SH', name=f'fixture {i}') for i in range(40)]
                    + [dict(first, range_days=3, buy_value=700, sell_value=200, net_value=499),
                       dict(first, range_days=20)])
    normalized = adapter.normalize_board(source, DAY, 'all', MASTER)
    page = {'state': 'partial', 'boards': {'all': normalized}, 'availability': {'available': True}}
    result = reader.project_page(page)
    assert result['periods']['1']['all']['observed_count'] == 40
    assert result['periods']['1']['all']['returned_count'] == 15
    assert result['periods']['1']['all']['truncated'] is True
    row = result['periods']['3']['all']['items'][0]
    assert row['buy_value'] == 700 and row['sell_value'] == 200
    assert row['raw_net_value'] == 499 and row['net_value'] is None and row['amount_check'] == 'conflict'
    assert result['source_board_statistics']['all']['query_unclassified_rows'] == 1
    filtered = reader.project_page(page, 'fixture 39')
    assert filtered['periods']['1']['all']['items'][0]['name'] == 'fixture 39'
    assert filtered['periods']['1']['all']['observed_count'] == 1
    assert result['units']['amount'] == 'CNY' and '不能相加' in result['scope']


def test_general_query_does_not_fan_out_seat_reads_and_unknown_data_stays_unknown(local, monkeypatch):
    market, _, _ = local
    result = reader.read_data(DAY, market=market)
    assert result['projection']['state'] == 'unknown' and result['unavailable_reason']
    retained(market)
    monkeypatch.setattr(reader.dragon_tiger, 'read_seats', lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError('fanout')))
    result = reader.read_data(DAY, market=market, query='fixture')
    assert result['projection']['stock_seats']['state'] == 'unknown'
    assert result['projection']['periods']['1']['all']['observed_count'] == 3


@pytest.mark.parametrize('raw_source', [False, True])
def test_explicit_ai_deny_in_original_day_or_raw_component_is_not_lost_by_projection(local, raw_source):
    market, _, _ = local
    store, record = retained(market, raw_sources=raw_source)
    if raw_source:
        raw = store.object(record['source_objects'][0]); raw['payload']['ai_processing_allowed'] = False
        record['source_objects'] = [store.save(raw)]
    else:
        record['boards']['all']['items'][0]['egress_allowed'] = False
    store.accept(record, market.calendar())
    with pytest.raises(ValueError, match='权限|禁止'):
        reader.read_data(DAY, market=market, query='000678')


def test_original_component_sha_and_selected_day_revision_are_verified(local, monkeypatch):
    market, _, _ = local
    store, record = retained(market, raw_sources=True)
    original_index = reader.dragon_tiger.Store.index
    calls = 0
    def changing(self):
        nonlocal calls
        calls += 1
        value = original_index(self)
        if calls >= 3:
            value['days'][DAY] = {**value['days'][DAY], 'sha256': 'a' * 64}
        return value
    monkeypatch.setattr(reader.dragon_tiger.Store, 'index', changing)
    with pytest.raises(HTTPException) as error:
        reader.read_data(DAY, market=market)
    assert error.value.status_code == 409
    monkeypatch.setattr(reader.dragon_tiger.Store, 'index', original_index)
    path = store.root / record['source_objects'][0]['record_ref']
    path.write_text('{}')
    with pytest.raises(ValueError, match='SHA'):
        reader.read_data(DAY, market=market)


def test_wrong_actual_board_day_is_rejected_even_with_valid_immutable_wrapper(local):
    market, _, _ = local
    store, record = retained(market)
    record['boards']['all']['items'][0]['date'] = '2026-10-26'
    store.accept(record, market.calendar())
    with pytest.raises(ValueError, match='实际日期'):
        reader.read_data(DAY, market=market)


def test_ai_detail_wiring_is_bounded_and_counts_as_one_existing_data_read(local, monkeypatch):
    market, _, _ = local
    retained(market)
    heads = {'daily_raw': {'market_snapshot_id': 'fixture-market'}}
    monkeypatch.setattr(tools, '_completed_market', lambda *_: (market, market.status(), heads, {}))
    monkeypatch.setattr(tools, '_heads', lambda: deepcopy(heads))
    monkeypatch.setattr(tools, '_supplement_head', lambda: {})
    monkeypatch.setattr(tools, 'local_now', lambda: datetime.fromisoformat(DAY + 'T18:00:00+08:00'))
    args = {'date': DAY, 'section': 'dragon_tiger', 'query': '000678'}
    assert classify_tool('read_market_detail', args) == 'data'
    result = tools.execute_read_tool('read_market_detail', args)
    assert result['ok'] and result['state'] == 'available'
    data = result['evidence'][0]['data']
    assert data['stock_seats']['instrument_id'] == '000678.SZ'
    assert data['periods']['1']['all']['items'][0]['date'] == DAY
    assert result['source_bindings']['published_detail']['dragon_tiger']['date'] == DAY
    assert len(core.dumps(result).encode()) <= tools.MAX_RESULT_BYTES
    assert LOCAL_TOOL_BUDGET['max_data_reads'] == 3 and LOCAL_TOOL_BUDGET['max_reads'] == 5


@pytest.mark.parametrize('version', ['ai-agent-round-v4', semantic.ROUND_VERSION])
@pytest.mark.parametrize('section', ['dragon_tiger', 'auction', 'noon'])
def test_new_detail_section_does_not_expand_saved_tool_enum(version, section):
    schemas = tools.tool_schemas()
    detail = next(row for row in schemas if row['name'] == 'read_market_detail')
    assert section in detail['parameters']['properties']['section']['enum']
    args = {'date': DAY, 'section': section, 'query': ''}
    request = {'round_protocol': {'version': version}, 'tool_schemas': schemas}
    assert semantic.classify_frozen_read(request, 'read_market_detail', args) == 'data'
    detail['parameters']['properties']['section']['enum'].remove(section)
    with pytest.raises(ValueError, match='invalid_tool_arguments'):
        semantic.classify_frozen_read(request, 'read_market_detail', args)


@pytest.mark.parametrize('section', ['auction', 'noon'])
def test_review_detail_dispatch_does_not_require_completed_closing_data(local, monkeypatch, section):
    from app.services import ai_review_detail
    monkeypatch.setattr(tools, 'local_now', lambda: datetime.fromisoformat(DAY+'T12:05:00+08:00'))
    monkeypatch.setattr(tools, '_completed_market', lambda *_: (_ for _ in ()).throw(AssertionError('closing gate')))
    calls = []
    def saved_detail(result, day, phase, query, now):
        calls.append((day, phase, query, now.hour))
        result['summary']['query'] = query
        result['state'] = 'available'
    monkeypatch.setattr(ai_review_detail, 'fill_review_detail', saved_detail)
    args = {'date': DAY, 'section': section, 'query': 'fixture'}
    result = tools.execute_read_tool('read_market_detail', args)
    assert result['ok'] and result['state'] == 'available'
    assert calls == [(DAY, section, 'fixture', 12)]
    assert classify_tool('read_market_detail', args) == 'data'
