"""The noon comparison freezes retained prior-session knowledge, not today's views."""
from copy import deepcopy
from hashlib import sha256
import json

from app import core
from app.services.noon_context import build_previous_review
from test_workspace import ws


def retain(day, stamp, name='旧版复盘', *, corrupt=False):
    payload = {'date': day, 'model': 'fixture-noon-context', 'atmosphere': {'summary': name},
               'watchlist': {'stocks': [{'instrument_id': '600001.SH', 'name': '合成关注股'}]},
               'provenance': {'rule_version': 'fixture-v1', 'input_fingerprint': str(stamp).zfill(64),
                              'mode': 'retained_retrospective'}}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    with core.connect() as db:
        db.execute('INSERT INTO market_sentiment_snapshot VALUES(?,?,?,?,?,?,?,?,?)',
                   ('sentiment_'+str(stamp).zfill(32), day, stamp, 'manual_retained_capture', payload['model'],
                    'fixture-v1', payload['provenance']['input_fingerprint'],
                    'b'*64 if corrupt else sha256(encoded.encode()).hexdigest(), encoded))
    return payload


def test_only_saved_exact_previous_session_before_capture_is_compared(ws):
    market, _ = ws
    day, previous = market.days[71], market.days[70]
    retain(previous, 1000)
    retain(previous, 5000, '后来补录的判断')
    retain(day, 6000, '今天的复盘不能冒充昨天')
    quotes = [{'instrument_id': '600001.SH', 'latest_price': 120, 'change_pct': 2.5}]
    value = build_previous_review(market, day, previous, 3000, quotes)
    frozen = deepcopy(value)
    assert value['state'] == 'retained' and value['recorded_at_ms'] == 1000
    assert value['payload']['atmosphere']['summary'] == '旧版复盘'
    assert value['observations'][0]['change_pct'] == 2.5
    assert value['observations'][0]['quote_at'] is None
    assert value['observations'][0]['previous_date'] == previous
    later = build_previous_review(market, day, previous, 7000, quotes)
    assert later['payload']['atmosphere']['summary'] == '后来补录的判断'
    assert value == frozen


def test_missing_previous_session_never_falls_back_to_latest_retained_day(ws, monkeypatch):
    market, _ = ws
    day, previous = market.days[71], market.days[70]
    retain(market.days[69], 1000, '更早的复盘')
    monkeypatch.setattr(market, 'calendar', lambda: market.days[:70])
    monkeypatch.setattr(market, 'panel', lambda _: (_ for _ in ()).throw(AssertionError('missing day must not read other prices')))
    value = build_previous_review(market, day, previous, 3000, [])
    assert value['state'] == 'missing' and value['date'] == previous
    assert value['payload'] is None and value['observations'] == []


def test_background_is_labeled_as_facts_and_exact_date_is_checked(ws, monkeypatch):
    market, _ = ws
    day, previous = market.days[71], market.days[70]
    value = build_previous_review(market, day, previous, 3000, [])
    assert value['state'] == 'background_only'
    assert value['payload']['mode'] == 'previous_session_price_facts'
    assert value['snapshot_id'] is None and value['recorded_at_ms'] is None
    monkeypatch.setattr(market, 'panel', lambda _: [{'instrument_id': '600001.SH', 'date': market.days[69], 'close': 10, 'change_pct': 1}])
    value = build_previous_review(market, day, previous, 3000, [])
    assert value['state'] == 'missing'


def test_corrupt_retained_content_is_not_presented_as_original_review(ws):
    market, _ = ws
    day, previous = market.days[71], market.days[70]
    retain(previous, 1000, corrupt=True)
    value = build_previous_review(market, day, previous, 3000, [])
    assert value['state'] == 'background_only'
    assert any('SHA' in gap for gap in value['gaps'])


def test_prior_manual_attribution_keeps_original_and_missing_quote_stays_unknown(ws):
    market, _ = ws
    day, previous = market.days[71], market.days[70]
    attribution = {'mainline_name': '合成旧主线', 'is_display_primary': True, 'role': 'core_candidate',
                   'evidence_status': 'user_judgment', 'source_name': '用户原文', 'source_reason': '昨天的原始判断',
                   'evidence_ref': 'fixture-note', 'source_published_at': None, 'evidence_sha256': None,
                   'catalyst_note': '', 'user_note': '下午观察条件原文'}
    with core.connect() as db:
        db.execute('''INSERT INTO market_mainline_revision
            (revision_id,trade_date,instrument_id,instrument_name,revision,action,source_kind,change_reason,
             attributions_json,recorded_at_ms,supersedes_revision_id) VALUES(?,?,?,?,1,'replace','manual_user_entry',?,?,?,NULL)''',
            ('fixture-noon-mainline', previous, '600001.SH', '合成关注股', '合成记录',
             json.dumps([attribution], ensure_ascii=False), 1000))
    value = build_previous_review(market, day, previous, 3000, {})
    assert value['state'] == 'retained' and value['payload'] is None
    assert value['mainlines'][0]['attributions'][0] == attribution
    item = value['observations'][0]
    assert item['mainline'] == '合成旧主线'
    assert item['latest_price'] is None and item['change_pct'] is None
    assert item['reason'] and '兑现' not in item['source_note']


def test_missing_source_calendar_does_not_guess_a_weekday(ws):
    market, _ = ws
    value = build_previous_review(market, market.days[71], None, 3000, [])
    assert value['state'] == 'calendar_unknown' and value['date'] is None


def test_previous_limit_cohort_needs_exact_dated_event_price_and_keeps_missing_unknown(ws, monkeypatch):
    market, _ = ws
    day, previous = market.days[71], market.days[70]
    close = market.data['600001.SH'][70]['close']
    monkeypatch.setattr(market, 'reference', lambda: {'pool_trade_date': previous,
        'limitup': [{'thscode': '600001.SH', 'last_price': close, 'continue_day_cnt': 2, 'name': '合成前日样本'},
                    {'thscode': '000001.SZ', 'last_price': 999, 'continue_day_cnt': 3}]}, raising=False)
    value = build_previous_review(market, day, previous, 3000,
                                  [{'instrument_id': '600001.SH', 'latest_price': 110, 'change_pct': 2}])
    cohort = value['cohort_feedback']
    assert cohort['source_count'] == 2 and cohort['observed_count'] == 1
    assert cohort['current_valid'] == 1 and cohort['up'] == 1
    assert cohort['items'][0]['previous_board_count'] == 2
    missing = build_previous_review(market, day, previous, 3000, [])['cohort_feedback']
    assert missing['current_valid'] == 0 and missing['up'] is None
    assert missing['items'][0]['change_pct'] is None


def test_prior_source_pools_are_exact_dated_and_cross_source_heights_must_agree(ws, monkeypatch):
    market, _ = ws
    day, previous = market.days[71], market.days[70]
    close = market.data['600001.SH'][70]['close']
    reference = {'pool_trade_date': market.days[69], 'limitup': [
        {'thscode': '600001.SH', 'last_price': close, 'continue_day_cnt': 9}],
        '_supplement': {'annual_pools': {previous: {'date': previous, 'items': [
            {'thscode': '600001.SH', 'last_price': close, 'date': previous, 'continue_day_cnt': 2}]}},
            'public_pools': {previous: {'date': previous, 'items': [
                {'instrument_id': '600001.SH', 'close': close, 'date': previous, 'board_count': 3}]}}}}
    monkeypatch.setattr(market, 'reference', lambda: reference, raising=False)
    result = build_previous_review(market, day, previous, 3000, [])
    source = result['event_cohorts']['limit_up']
    assert source['source_count'] == source['verified_count'] == 1
    assert source['raw_source_row_count'] == 2
    assert source['items'][0]['previous_board_count'] is None
    assert source['items'][0]['previous_close'] == close
    assert len(source['items'][0]['source_paths']) == 2
    assert result['cohort_feedback']['items'][0]['previous_board_count'] is None
    # Returned facts are frozen copies, including the cross-source merge.
    reference['_supplement']['public_pools'][previous]['items'][0]['board_count'] = 2
    assert source['items'][0]['previous_board_count'] is None
    changed = build_previous_review(market, day, previous, 3000, [])
    assert changed['event_cohorts']['limit_up']['items'][0]['previous_board_count'] == 2


def test_prior_break_and_down_samples_do_not_reinterpret_source_counts_as_up_board_height(ws, monkeypatch):
    market, _ = ws
    day, previous = market.days[71], market.days[70]
    positive = market.data['600001.SH'][70]['close']
    negative = market.data['000001.SZ'][70]['close']
    monkeypatch.setattr(market, 'reference', lambda: {'pool_trade_date': previous,
        'breakpool': [{'thscode': '600001.SH', 'date': previous, 'last_price': positive, 'continue_day_cnt': 4}],
        'limitdown': [{'thscode': '000001.SZ', 'date': previous, 'last_price': negative, 'continue_day_cnt': 3}]}, raising=False)
    result = build_previous_review(market, day, previous, 3000, [])['event_cohorts']
    assert result['limit_break']['verified_count'] == result['limit_down']['verified_count'] == 1
    assert result['limit_break']['items'][0]['previous_board_count'] is None
    assert result['limit_down']['items'][0]['previous_board_count'] is None
    assert result['limit_up']['state'] == 'unknown' and result['limit_up']['source_count'] is None


def test_prior_events_reject_conflicting_dates_prices_identity_and_nontrading_close(ws, monkeypatch):
    market, _ = ws
    day, previous = market.days[71], market.days[70]
    close = market.data['600001.SH'][70]['close']
    cases = [
        {'thscode': '600001.SH', 'date': day, 'last_price': close},
        {'thscode': '600001.SH', 'instrument_id': '000001.SZ', 'last_price': close},
        {'thscode': '600001.SH', 'last_price': close + .01},
        {'thscode': '600001.SH', 'last_price': close, 'close': close + 1},
    ]
    for event in cases:
        monkeypatch.setattr(market, 'reference', lambda event=event: {'pool_trade_date': previous, 'limitup': [event]}, raising=False)
        pool = build_previous_review(market, day, previous, 3000, [])['event_cohorts']['limit_up']
        assert pool['verified_count'] == 0 and pool['unverified_count'] == 1
    monkeypatch.setattr(market, 'reference', lambda: {'pool_trade_date': previous,
        'limitup': [{'thscode': '600001.SH', 'last_price': close, 'continue_day_cnt': 2}]}, raising=False)
    original = market.panel
    monkeypatch.setattr(market, 'panel', lambda date: [{**row, 'volume': 0} for row in original(date)])
    pool = build_previous_review(market, day, previous, 3000, [])['event_cohorts']['limit_up']
    assert pool['verified_count'] == 0


def test_price_conflicts_across_sources_and_up_down_pools_stay_unverified(ws, monkeypatch):
    market, _ = ws
    day, previous = market.days[71], market.days[70]
    close = market.data['600001.SH'][70]['close']
    event = {'thscode': '600001.SH', 'last_price': close, 'continue_day_cnt': 2}
    reference = {'pool_trade_date': previous, 'limitup': [event],
                 '_supplement': {'public_pools': {previous: {'date': previous, 'items': [
                     {'instrument_id': '600001.SH', 'date': previous, 'close': close + 1, 'board_count': 2}]}}}}
    monkeypatch.setattr(market, 'reference', lambda: reference, raising=False)
    pool = build_previous_review(market, day, previous, 3000, [])['event_cohorts']['limit_up']
    assert pool['verified_count'] == 0 and pool['unverified_count'] == 1
    reference.pop('_supplement')
    reference['limitdown'] = [dict(event)]
    pools = build_previous_review(market, day, previous, 3000, [])['event_cohorts']
    assert pools['limit_up']['verified_count'] == pools['limit_down']['verified_count'] == 0
    assert '来源冲突' in pools['limit_up']['reason']


def test_fixed_generation_supplement_read_is_projected_to_one_previous_day(ws, monkeypatch):
    from app.services import market_supplement
    market, _ = ws
    day, previous = market.days[71], market.days[70]
    calls = []
    monkeypatch.setattr(market, 'reference', lambda: {}, raising=False)
    monkeypatch.setattr(market, 'status', lambda: {'snapshot_id': 'fixed-fixture-generation'}, raising=False)
    def load(snapshot_id, *, dates, fields):
        calls.append((snapshot_id, dates, fields))
        return {'snapshot_id': snapshot_id}, ('verified-supplement-index-v1', 'fixture-root', snapshot_id, 'a'*64)
    monkeypatch.setattr(market_supplement, 'load_supplement_with_fingerprint', load)
    value = build_previous_review(market, day, previous, 3000, [])
    assert calls == [('fixed-fixture-generation', (previous,),
                      ('annual_pools', 'annual_down_pools', 'public_pools', 'sector_index_history'))]
    assert value['sector_background']['source_index_sha256'] == 'a'*64
    assert value['sector_background']['state'] == 'unknown'


def test_exact_day_sector_prices_survive_missing_members_and_do_not_use_same_name_fallback(ws, monkeypatch):
    from app.services import sector_history
    market, _ = ws
    day, previous, before = market.days[71], market.days[70], market.days[69]
    code = '881101.TI'
    row = {'topic_code': code, 'thscode': code, 'topic_id': 'fuyao-'+code,
           'name': '合成同名行业', 'kind': 'industry', 'date': previous, 'quote_date': previous,
           'previous_date': before, 'close': 105, 'previous_close': 100, 'change_pct': (105/100-1)*100,
           'amount': None, 'source': 'fuyao', 'normalization_version': sector_history.NORMALIZATION_VERSION,
           'request_signature': sector_history.request_signature(code, previous, before)}
    record = {'date': previous, 'source': 'fuyao', 'normalization_version': sector_history.NORMALIZATION_VERSION,
              'items': [row], 'failures': [], 'coverage': {'expected_previous_date': before}, 'complete': False}
    reference = {'topics': [{'topic_code': '881199.TI', 'name': '合成同名行业', 'observed_date': day,
                             'instrument_ids': ['600001.SH']}],
                 '_supplement': {'target_date': previous, 'sector_index_history': {previous: record}}}
    monkeypatch.setattr(market, 'reference', lambda: reference, raising=False)
    background = build_previous_review(market, day, previous, 3000, [])['sector_background']
    assert background['state'] == 'observed_sample' and len(background['items']) == 1
    actual = background['items'][0]
    assert actual['topic_code'] == code and actual['change_pct'] == row['change_pct']
    assert actual['instrument_id'] == code and actual['kind'] == 'industry'
    assert actual['amount'] is None and 'instrument_ids' not in actual
    # A different day or damaged price proof never becomes the previous day's row.
    row['quote_date'] = day
    assert build_previous_review(market, day, previous, 3000, [])['sector_background']['items'] == []


def test_empty_source_pool_without_completeness_is_unknown_and_missing_pool_is_not_zero(ws, monkeypatch):
    market, _ = ws
    day, previous = market.days[71], market.days[70]
    monkeypatch.setattr(market, 'reference', lambda: {'pool_trade_date': previous, 'limitup': []}, raising=False)
    value = build_previous_review(market, day, previous, 3000, [])
    empty = value['event_cohorts']['limit_up']
    assert empty['state'] == 'unknown' and empty['source_count'] == 0
    missing = value['event_cohorts']['limit_down']
    assert missing['state'] == 'unknown' and missing['source_count'] is None
    assert value['cohort_feedback']['up'] is None and value['cohort_feedback']['down'] is None


def test_prior_theme_keeps_original_dated_reason_and_conflicts_stay_unknown(ws, monkeypatch):
    market, _ = ws
    day, previous = market.days[71], market.days[70]
    close = market.data['600001.SH'][70]['close']
    text = '  合成机器人 + 海外订单原文  '
    event = {'thscode': '600001.SH', 'last_price': close, 'continue_day_cnt': 2, 'limit_up_reason': text}
    public = {'instrument_id': '600001.SH', 'date': previous, 'close': close, 'board_count': 2,
              'limit_up_reason': text}
    reference = {'pool_trade_date': previous, 'limitup': [event],
                 '_supplement': {'public_pools': {previous: {'date': previous, 'items': [public]}}}}
    monkeypatch.setattr(market, 'reference', lambda: reference, raising=False)
    value = build_previous_review(market, day, previous, 3000, [])
    for row in (value['event_cohorts']['limit_up']['items'][0], value['cohort_feedback']['items'][0]):
        assert row['theme_text'] == text
        assert row['theme_date'] == previous and row['theme_source'] == 'previous_session_pool'
    public['limit_up_reason'] = '另一个来源的不同归因原文'
    value = build_previous_review(market, day, previous, 3000, [])
    assert value['cohort_feedback']['items'][0]['theme_text'] is None
    assert '不一致' in value['cohort_feedback']['items'][0]['theme_reason']
    event.pop('limit_up_reason'); public.pop('limit_up_reason')
    value = build_previous_review(market, day, previous, 3000, [])
    assert value['cohort_feedback']['items'][0]['theme_text'] is None
    assert '不用当前成员' in value['cohort_feedback']['items'][0]['theme_reason']


def retain_noon(day, recorded_at_ms, *, corrupt=False, interval_conflict=False):
    payload = {'date': day, 'version': 'noon-snapshot-v1',
               'calendar': {'verified': True, 'previous_day': None},
               'collected_start_at': day+'T11:45:00+08:00',
               'collected_end_at': day+'T11:45:15+08:00', 'quote_as_of': None,
               'sources': [{'name': 'indices', 'source': 'fuyao', 'state': 'received'}],
               'indices': [{'instrument_id': '000001.SH', 'amount': 100, 'currency': 'CNY', 'quote_at': None}],
               'stocks': [{'instrument_id': '600001.SH', 'private_note': '只供测试，不应进入指数小摘要'}],
               'previous_review': {'nested': '不复制上一份复盘或递归午评'}}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    suffix = day.replace('-', '')
    with core.connect() as db:
        db.execute('''INSERT INTO job(job_id,request_key,logical_key,kind,target_date,parameters_json,
            parameters_hash,status,created_at_ms) VALUES(?,?,?,'source_check',?,'{}',?,'succeeded',?)''',
            ('noon-job-'+suffix, 'noon-request-'+suffix, 'noon-logical-'+suffix, day, 'b'*64, recorded_at_ms))
        db.execute('INSERT INTO market_noon_capture VALUES(?,?,?,?)',
                   (day, 'noon-job-'+suffix, 'noon-request-'+suffix, recorded_at_ms))
        db.execute('INSERT INTO market_noon_snapshot VALUES(?,?,?,?,?,?,?,?,?,?)',
                   ('noon-fixture-'+suffix, day, 'noon-job-'+suffix,
                    day+'T11:46:00+08:00' if interval_conflict else payload['collected_start_at'],
                    payload['collected_end_at'], recorded_at_ms, 'partial',
                    'a'*64 if corrupt else sha256(encoded.encode()).hexdigest(), encoded, '[]'))
    return payload


def test_previous_noon_is_a_sha_verified_exact_day_compact_frozen_summary(ws):
    market, _ = ws
    day, previous = market.days[71], market.days[70]
    original = retain_noon(previous, 1000)
    value = build_previous_review(market, day, previous, 3000, [])['previous_noon']
    assert value['state'] == 'available' and value['date'] == previous
    assert value['snapshot_id'] == 'noon-fixture-'+previous.replace('-', '')
    assert value['content_sha256'] and value['recorded_at_ms'] == 1000
    assert value['collected_start_at'] == original['collected_start_at']
    assert value['calendar_verified'] is True and value['source'] == 'fuyao'
    assert value['indices'] == [{'instrument_id': '000001.SH', 'amount': 100, 'currency': 'CNY',
                                 'quote_at': None, 'source': 'fuyao'}]
    assert 'stocks' not in value and 'previous_review' not in value and 'sources' not in value
    original['indices'][0]['amount'] = 999
    assert value['indices'][0]['amount'] == 100


def test_missing_or_later_recorded_previous_noon_never_uses_an_older_session(ws):
    market, _ = ws
    day, previous = market.days[71], market.days[70]
    retain_noon(market.days[69], 1000)
    missing = build_previous_review(market, day, previous, 3000, [])['previous_noon']
    assert missing['state'] == 'unavailable' and missing['indices'] == []
    retain_noon(previous, 5000)
    before = build_previous_review(market, day, previous, 3000, [])['previous_noon']
    assert before['state'] == 'unavailable' and before['snapshot_id'] is None
    after = build_previous_review(market, day, previous, 6000, [])['previous_noon']
    assert after['state'] == 'available' and after['recorded_at_ms'] == 5000


def test_previous_noon_bad_sha_or_inconsistent_collection_interval_is_unavailable(ws):
    market, _ = ws
    day, previous = market.days[71], market.days[70]
    retain_noon(previous, 1000, corrupt=True)
    value = build_previous_review(market, day, previous, 3000, [])['previous_noon']
    assert value['state'] == 'unavailable' and 'SHA' in value['reason']
    # A different fixture day permits checking interval consistency without modifying immutable rows.
    retain_noon(market.days[69], 1000, interval_conflict=True)
    value = build_previous_review(market, previous, market.days[69], 3000, [])['previous_noon']
    assert value['state'] == 'unavailable' and '区间不一致' in value['reason']
