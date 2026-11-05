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


def test_sector_comparison_metadata_only_marks_verified_raw_index_quotes():
    row = {'instrument_id': '884229.TI', 'name': '半导体设备', 'kind': 'industry',
           'latest_price': 103, 'prev_close': 100, 'change_pct': 3,
           'currency': 'CNY', 'price_basis': 'none'}
    assert not brief._sector(row).get('quote_basis'), 'auction averages never inherit index provenance'
    projected = brief._sector(row, quote_source='fuyao')
    assert {key: projected[key] for key in
            ('quote_source', 'quote_basis', 'latest_price', 'prev_close', 'currency', 'price_basis')} == {
        'quote_source': 'fuyao', 'quote_basis': 'provider_index', 'latest_price': 103,
        'prev_close': 100, 'currency': 'CNY', 'price_basis': 'none'}
    assert not brief.sector_comparison_metadata(row, source='other')
    assert not brief.sector_comparison_metadata({**row, 'change_pct': 9}, source='fuyao')
    assert not brief.sector_comparison_metadata({**row, 'prev_close': None}, source='fuyao')
    assert not brief.sector_comparison_metadata({**row, 'kind': 'stock'}, source='fuyao')


def test_noon_rotation_member_identity_reuses_frozen_proof_only(monkeypatch):
    from app.services import noon_membership_context
    from app.services.noon_sector_views import freeze_memberships
    from test_noon_membership_context import payload, topic, GENERATION, PRIOR, OBSERVED, CODE

    value = payload()
    sector = value['sectors'][0]
    sector.update(member_limit_up_count=1, member_limit_up_state='observed')
    value['sector_memberships'] = freeze_memberships(value, [topic()])
    quotes = {row['instrument_id']: row for row in value['stocks']}
    monkeypatch.setattr(noon_membership_context, '_load_topics', lambda *_: pytest.fail('no later member source'))
    result = brief._noon_member_metadata(value, sector, quotes)
    assert result == {'member_count': 2, 'member_date': OBSERVED, 'member_list_complete': True,
                      'member_reference_sha256': brief.sector_membership_fingerprint(
                          GENERATION, PRIOR, CODE, 'industry', OBSERVED, ['600002.SH', '600001.SH'])}
    sector['member_limit_up_count'] = 2
    assert not brief._noon_member_metadata(value, sector, quotes)
    sector['member_limit_up_count'] = 1
    value['sector_memberships']['items'][CODE]['sha256'] = '0' * 64
    assert not brief._noon_member_metadata(value, sector, quotes)
    value.pop('sector_memberships')
    assert not brief._noon_member_metadata(value, sector, quotes)


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
                'amount_change': 30, 'amount_previous_date': '2026-11-03',
                'indices': [
                    {'date': day, 'instrument_id': '000001.SH', 'name': '上证指数',
                     'close': 3000, 'change_pct': 1, 'amount_unit': 'CNY', 'amount': 30,
                     'amount_previous': 40, 'amount_previous_date': '2026-11-03'},
                    {'date': '2026-11-03', 'instrument_id': '000688.SH', 'name': '科创50',
                     'close': 1200, 'change_pct': 2, 'amount_unit': 'CNY', 'amount': 11},
                ],
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
    assert anchor['market_pulse']['turnover']['amount_comparison']['previous_amount'] == 70
    assert len(anchor['market_pulse']['indices']) == 1
    index = anchor['market_pulse']['indices'][0]
    assert index['latest_price'] == 3000 and index['amount'] == 30
    assert index['amount_comparison']['change_from_previous'] == -10
    assert anchor['retrospective']['events'][-2]['count'] == 1
    output = brief._brief('close', [('close', anchor)])
    assert '至少 119 只' in output['body'] and '至少 32 只' in output['body']
    assert '121' not in output['body'] and '33 只' not in output['body']
    overview['snapshot_id'] = 'prior-generation'
    with pytest.raises(ValueError, match='正式发布身份不符'):
        brief._close_anchor(day, {'generation': generation})


def test_old_noon_anchor_uses_exact_original_amounts_without_updating_stored_brief(monkeypatch):
    from contextlib import nullcontext
    from hashlib import sha256
    import sqlite3

    from test_noon_comparison import CODE, snapshot

    payload = snapshot()
    payload['version'] = 'noon-snapshot-v1'
    payload['indices'][0].update(name='上证指数', change_pct=1.25, latest_price=3000)
    rows = [{'instrument_id': f'{600001 + index}.SH', 'name': f'样本{index}',
             'latest_price': 11, 'prev_close': 10, 'change_pct': 10}
            for index in range(14)]
    sealed = [{**row, 'continue_day_cnt': index + 1} for index, row in enumerate(rows[:2])]
    payload.update(stocks=rows, events={'limit_up': {'state': 'observed', 'items': sealed},
                                       'limit_break': {'state': 'observed', 'items': rows[2:]}})
    topic_code = '884045.TI'
    topic = {'topic_code': topic_code, 'name': '测试行业', 'kind': 'industry',
             'source': 'fuyao', 'members_observed_date': '2026-10-21'}
    payload['sectors'] = [{'instrument_id': topic_code, **topic, 'latest_price': 103,
                           'prev_close': 100, 'change_pct': 3, 'currency': 'CNY', 'price_basis': 'none'}]
    payload['stock_classifications'] = {rows[2]['instrument_id']: {
        'industries': [topic], 'topics': [], 'state': 'observed_sample'}}
    anchor = {'id': 'noon-fixture', 'date': payload['date'], 'slot': 'noon',
              'collected_start_at': payload['collected_start_at'], 'collected_end_at': payload['collected_end_at'],
              'sectors': [{'instrument_id': topic_code, 'name': topic['name'], 'kind': topic['kind'], 'change_pct': 3}],
              **{key: {'indices': [{'instrument_id': CODE, 'name': '上证指数', 'change_pct': 1.25}]}
                 for key in ('market', 'market_pulse')}}
    stored = json.dumps({'anchor': anchor, 'brief': {'body': '已生成的原始简报'}})
    raw = json.dumps(payload)
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    monkeypatch.setattr(brief, 'connect', lambda: nullcontext(connection))
    connection.executescript('''CREATE TABLE market_intraday_brief (trade_date,slot,body_json);
        CREATE TABLE market_noon_snapshot (trade_date,snapshot_id,collected_start_at,collected_end_at,content_sha256,payload_json);''')
    connection.execute("INSERT INTO market_intraday_brief VALUES (?,'noon',?)", (anchor['date'], stored))
    connection.execute('INSERT INTO market_noon_snapshot VALUES (?,?,?,?,?,?)', (
        anchor['date'], anchor['id'], anchor['collected_start_at'], anchor['collected_end_at'],
        sha256(raw.encode()).hexdigest(), raw))
    view = brief.read_brief_view(anchor['date'])
    index = view['anchors'][0]['market_pulse']['indices'][0]
    assert index['latest_price'] == 3000 and index['amount'] == 120
    assert index['amount_comparison']['label'] == '较昨日午评'
    assert index['amount_comparison']['change_from_previous'] == 20
    assert view['anchors'][0]['sectors'][0]['quote_basis'] == 'provider_index'
    assert view['anchors'][0]['sectors'][0]['prev_close'] == 100
    ladder = view['anchors'][0]['ladder']
    assert [(item['board_count'], len(item['items'])) for item in ladder['groups']] == [(2, 1), (1, 1)]
    assert ladder['broken_board']['count'] == len(ladder['broken_board']['items']) == 12
    assert ladder['broken_board']['items'][0]['themes'] == [
        {'name': '测试行业', 'kind': 'industry', 'member_date': '2026-10-21'}]
    assert connection.execute('SELECT body_json FROM market_intraday_brief').fetchone()[0] == stored
    connection.execute("UPDATE market_noon_snapshot SET content_sha256='invalid'")
    assert brief.read_brief_view(anchor['date'])['anchors'][0] == anchor
    connection.close()


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
