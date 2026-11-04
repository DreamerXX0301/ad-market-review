import json
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest

from app.services import intraday_brief as brief, market_service, noon_close_service

DAY = '2026-11-04'


def _clock(value, day=DAY):
    return int(datetime.fromisoformat(f'{day}T{value}+08:00').timestamp() * 1000)


def _auction_opening_inputs(day=DAY):
    stocks = [{'instrument_id': code, 'name': name, 'pre_close_price': 10, 'auction_pct': pct}
              for code, name, pct in [('600001.SH', '样本甲', 2), ('600002.SH', '样本乙', 4),
                                      ('600003.SH', '样本丙', -1), ('600004.SH', '昨收不符', 3)]]
    discovery = {'date': day, 'state': 'partial', 'captured_at_ms': _clock('09:26:00', day),
                 'recorded_at_ms': _clock('09:26:01', day), 'content_sha256': 'a' * 64,
                 'coverage': {'valid': 4, 'candidates': 6},
                 'stocks': stocks, 'directions': [
                     {'instrument_id': '884229.TI', 'name': '半导体设备', 'stock_count': 3,
                      'members_observed_date': '2026-11-03', 'stocks': [stocks[i] for i in (0, 1, 3)]}]}
    opening = {'id': 'verified-opening', 'slot': '1000', 'date': day,
               'collected_start_at': day + 'T10:00:00+08:00', 'collected_end_at': day + 'T10:01:12+08:00',
               'comparison_input': {'stocks': [
                   {'instrument_id': code, 'price_basis': 'none', 'currency': 'CNY',
                    'prev_close': close, 'change_pct': change}
                   for code, close, change in [('600001.SH', 10, 6), ('600002.SH', 10, 3),
                                                ('600003.SH', 10, 1), ('600004.SH', 11, 10),
                                                ('600005.SH', 10, 8)]]}}
    return discovery, opening


def _projection(slot, up, percent=None):
    return {'slot': slot, 'quality': 'complete', 'id': f'source-{slot}',
            'market': {'breadth': {'up': up, 'down': 3, 'flat': 0, 'valid': up + 3},
                       'turnover': {'amount': 120_000_000}, 'indices': []},
            'ladder': {'sealed_count': 0, 'state': 'observed'},
            'sectors': [] if percent is None else [
                {'instrument_id': '884229.TI', 'name': '半导体设备', 'change_pct': percent}]}


def test_cumulative_cutoff_excludes_future_and_late_published_earlier_slots():
    records = {'auction': {'time': 10, 'ready': 20}, '1000': {'time': 35, 'ready': 80},
               '1030': {'time': 50, 'ready': 51}, '1100': {'time': 60, 'ready': 61},
               'close': {'time': 100, 'ready': 101}}
    selected = brief._preceding(records, '1030')
    assert [slot for slot, _ in selected] == ['auction', '1030']
    output = brief._brief('1030', [(slot, _projection(slot, 4)) for slot, _ in selected])
    assert [row['status'] for row in output['context']] == ['complete', 'missing', 'complete']
    assert '11:00' not in output['body'] and '15:00' not in output['body']
    assert '10:00 开盘首轮' in output['body']


def test_auction_discovery_compares_exact_stocks_and_keeps_evidence_in_cumulative_brief():
    from app.services.auction_followup import compare

    discovery, opening = _auction_opening_inputs()
    result = compare(discovery, opening)
    assert (result['sample_count'], result['comparable'], result['strengthened'],
            result['faded'], result['turned_positive']) == (4, 3, 2, 1, 1)
    assert result['directions'][0]['comparable'] == 2
    assert result['directions'][0]['median_change_pp'] == 1.5
    assert {row['instrument_id'] for row in result['examples']} == {'600001.SH', '600002.SH', '600003.SH'}
    auction = {**_projection('auction', 10), 'auction_discovery': discovery}
    early = {**_projection('1000', 15), 'opening_quotes': opening}
    first = brief._brief('auction', [('auction', auction)])
    assert '竞价异动样本核验 4/6 只' in first['body']
    assert '首轮转涨' not in first['body']
    subsequent = brief._brief('1030', [('auction', auction), ('1000', early), ('1030', _projection('1030', 18))])
    assert '较竞价涨幅扩大 2 只，回落 1 只，竞价未涨、首轮转涨 1 只' in subsequent['body']
    assert '半导体设备 2/3 只可比，同股涨幅变化中位数 +1.50 个百分点' in subsequent['body']
    assert '昨收不符' not in subsequent['body']
    evidence = {row['id']: row for row in subsequent['evidence']}
    assert evidence['1000.auction_followup']['auction_discovery_sha256'] == discovery['content_sha256']
    assert compare({**discovery, 'recorded_at_ms': _clock('10:01:00')}, opening) is None
    assert compare(discovery, {**opening, 'date': '2026-11-05'}) is None
    records = {'auction': {'time': 10, 'ready': 20, 'discovery': {'recorded_at_ms': 40}},
               '1000': {'time': 35, 'ready': 36}, '1030': {'time': 50, 'ready': 51}}
    assert 'discovery' not in dict(brief._preceding(records, '1000'))['auction']
    assert 'discovery' in dict(brief._preceding(records, '1030'))['auction']


def test_model_must_reference_verified_ids_from_each_observed_stage(monkeypatch):
    original = brief._brief('1030', [('auction', _projection('auction', 15)),
                                     ('1000', _projection('1000', 18, 1.25)),
                                     ('1030', _projection('1030', 23, 3.50))])
    result = {'lens': 'sectors', 'ids': ['1030.rotation', 'auction.breadth', '1000.breadth']}

    def responder(request):
        payload = json.loads(request.content)
        assert payload['model'] == brief.MODEL
        assert payload['format']['properties']['ids']['minItems'] == 3
        assert '15:00' not in payload['messages'][1]['content']
        return httpx.Response(200, json={'message': {'content': json.dumps(result)}})

    client = httpx.Client
    monkeypatch.setattr(brief.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(responder), **kwargs))
    enriched = brief._model_selection(original)
    assert enriched['origin'] == 'local_ai'
    assert enriched['body'].index('竞价') < enriched['body'].index('开盘首轮')
    assert '半导体设备 +1.25% → +3.50%' in enriched['body']
    assert enriched['evidence'] == original['evidence']
    result['ids'][2] = 'close.breadth'
    with pytest.raises(ValueError):
        brief._model_selection(original)
    result['ids'][2] = '1030.breadth'
    with pytest.raises(ValueError):
        brief._model_selection(original)


def test_closing_projection_only_uses_verified_counts(monkeypatch):
    day, generation = '2026-11-04', 'market-test'
    verified = {'state': 'partial', 'observed_broken_count': 32}
    common = {'date': day, 'snapshot_id': generation}
    overview = {**common, 'quality': 'partial', 'total_amount': 100,
                'breadth': {'up': 3, 'down': 2, 'flat': 1, 'total': 6},
                'limit_up_count': 121, 'broken_count': 33, 'broken_board_analysis': {'current': verified}}
    ladder = {**common, 'main_coverage': {'complete': False, 'confirmed_count': 119},
              'items': [{'board_count': 3, 'first_limit_time': '14:00:01'}]}
    monkeypatch.setattr(brief, 'get_market', lambda: SimpleNamespace(status=lambda: {'snapshot_id': generation}))
    monkeypatch.setattr(market_service, 'overview', lambda _: overview)
    monkeypatch.setattr(market_service, 'market_page', lambda key, *_: ladder if key == 'ladder' else {**common, 'items': []})
    monkeypatch.setattr(noon_close_service, 'read_view', lambda _: {})
    anchor = brief._close_anchor(day, {'generation': generation})
    assert anchor['ladder']['sealed_count'] == 119
    assert anchor['ladder']['broken_board']['count'] == 32
    assert anchor['retrospective']['events'][-2]['count'] == 1
    output = brief._brief('close', [('close', anchor)])
    assert '至少 119 只' in output['body'] and '至少 32 只' in output['body']
    assert '121' not in output['body'] and '33 只' not in output['body']
    overview['snapshot_id'] = 'prior-generation'
    with pytest.raises(ValueError, match='正式发布身份不符'):
        brief._close_anchor(day, {'generation': generation})


def test_worker_retains_audited_fallback_without_repeating_failed_model(tmp_path, monkeypatch):
    from datetime import datetime
    from threading import Event
    from app import core
    from app.services.intraday_service import AUTOMATION_KEY
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR'):
        monkeypatch.delenv(key, raising=False)
    core.set_setting(AUTOMATION_KEY, {'enabled': True, 'revision': 1})
    calls = []
    monkeypatch.setattr(brief, '_metadata', lambda *_: {'1000': {'time': 1, 'ready': 1, 'sha': 'verified'}})
    monkeypatch.setattr(brief, '_read_projection', lambda *_: _projection('1000', 18))

    def unavailable(initial):
        calls.append(initial['slot'])
        raise httpx.ConnectError('本机模型暂不可用')

    monkeypatch.setattr(brief, '_model_selection', unavailable)
    clock = datetime(2026, 11, 4, 10, 1, tzinfo=brief.CHINA)
    brief._tick(clock, Event())
    brief._tick(clock, Event())
    payload = brief.read_brief_view('2026-11-04')
    assert calls == ['1000']
    assert payload['briefs'][0]['origin'] == 'deterministic'
    assert payload['briefs'][0]['context'][0]['status'] == 'missing'
    assert payload['anchors'] == []
