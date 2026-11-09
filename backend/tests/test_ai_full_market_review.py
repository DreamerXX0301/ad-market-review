from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services import ai_full_market_review as review, ai_read_tools as tools


DAY = '2026-11-09'


def _row(module, data):
    return {'evidence_id': module, 'kind': module, 'source': 'local_fixture',
            'title': module, 'dates': {'trade_date': DAY}, 'data': data}


def _raw(module, data):
    raw = tools._result(module, DAY)
    raw['evidence'] = [_row(module, data)]
    return raw


def _read(day=DAY, scope='full_review'):
    return tools.execute_read_tool(review.TOOL_NAME, review.TOOL_ARGS,
                                   page_context={'as_of': day, 'market_scope': scope})


@pytest.fixture
def sources(monkeypatch):
    monkeypatch.setattr(tools, 'local_now', lambda: datetime.fromisoformat(DAY + 'T18:00:00+08:00'))
    monkeypatch.setattr(tools, '_heads', lambda: {'daily_raw': {'market_snapshot_id': 'retained'}})
    monkeypatch.setattr(tools, '_supplement_head', lambda: 'retained-supplement')
    monkeypatch.setattr(tools, '_market', lambda _: SimpleNamespace(status=lambda: {'snapshot_id': 'retained'}))
    monkeypatch.setattr(tools, '_paths', lambda: ())
    phases = {phase: {'phase_state': 'available'} for phase in review._PHASE_KINDS}
    monkeypatch.setattr(tools, '_availability', lambda *_a, **_kw: ({'phases': phases}, {}, {}))
    raw = {module: _raw(module, {}) for module in review._DETAILS}
    raw['overview'] = _raw('turnover', {'amount': 100})
    raw['emotion']['evidence'][0]['data'] = review.project_emotion_review({
        'breadth': {'up': 20, 'down': 30, 'scope': '沪深样本'},
        'emotion': {'phase': '修复', 'previous_date': '2026-11-06'},
        'sentiment': {'short_term': {'previous_date': '2026-11-06', 'metrics': [{'key': 'promoted_count', 'value': 9}]}},
        'broken_board_analysis': {'date': DAY, 'current': {'date': DAY, 'broken_count': 44, 'touch_count': 101,
            'concept_distribution': [{'name': '融资融券', 'observed_count': 25}, {'name': '样本题材', 'observed_count': 15}],
            'items': [{'outcome': 'broken', 'date': DAY, 'name': '炸板样本', 'instrument_id': '000001.SZ'}]},
            'comparison': {'previous_date': '2026-11-06', 'broken_rate_delta_pp': 15},
            'history': [{'date': '2026-11-06', 'broken_rate_pct': 28}]}}, DAY)
    stock = {'name': '最高板样本', 'instrument_id': '000001.SZ', 'board_count': 6}
    raw['ladder'] = _raw('ladder', {'review_details': review.project_ladder_review({}, [stock],
        [{'height': 6, 'count': 1, 'stocks': {'items': [stock]}}])})
    raw['sectors']['evidence'][0]['data'] = {'sectors': {'observed_count': 1, 'items': [{'name': '样本'}]}}
    raw['dragon_tiger']['evidence'][0]['data'] = {'availability': {'available': True, 'date': DAY}}
    raw['abnormal']['evidence'][0]['data'] = {'monitor_scope': 'historical', 'monitor_observed_date': DAY,
                                            'stocks': {'observed_count': 1, 'items': [{'name': '监管样本'}]}}
    raw['briefs'] = tools._result('briefs', DAY)
    raw['briefs']['evidence'] = [_row(kind, {'state': 'observed_sample'})
                                for kinds in review._PHASE_KINDS.values() for kind in kinds]
    slots = ('0935', '0945', '1000', '1030', '1100', '1330', '1400', '1430')
    raw['intraday'] = tools._result('intraday', DAY)
    raw['intraday']['state'] = 'partial'
    raw['intraday']['summary']['phases'] = {slot: {'phase_state': 'available', 'time': slot} for slot in slots}
    raw['intraday']['evidence'] = [_row('intraday:' + slot, {'breadth': {'up': 200}}) for slot in slots]
    monkeypatch.setattr(review, '_inputs', lambda day, *_a: raw if day == DAY else pytest.fail('wrong observation day'))
    monkeypatch.setattr(review, '_news', lambda day: (_raw('news', {}), {'archived_news_count': 1}, 'available', None))
    return raw, slots


def test_full_review_keeps_all_modules_and_intraday_time_slots(sources):
    _, slots = sources
    result = _read()
    assert result['ok'] and result['date'] == DAY and result['state'] == 'partial'
    assert len(result['summary']['modules']) == 13
    data = {row['kind']: row['data'] for row in result['evidence']}
    assert set(data) == {row['module'] for row in result['summary']['modules']}
    assert [row['slot'] for row in data['intraday']['slots']] == list(slots)
    assert data['abnormal']['stocks']['items'][0]['name'] == '监管样本'
    assert all(row['dates']['trade_date'] == DAY for row in result['evidence'])
    assert data['emotion']['sentiment']['short_term']['metrics']['promoted_count'] == 9
    broken = data['emotion']['broken_boards']
    assert (broken['broken_count'], broken['touch_count']) == (44, 101)
    assert broken['directions']['concept'] == [{'name': '样本题材', 'observed_count': 15}]
    assert broken['comparison']['broken_rate_pct'] == 28
    assert broken['representatives'][0]['name'] == '炸板样本'
    assert data['ladder']['heights'][0]['representatives'][0]['name'] == '最高板样本'


def test_unknown_broken_denominator_stays_unknown_and_other_date_samples_are_ignored():
    analysis = {'date': DAY, 'current': {'date': DAY, 'state': 'partial', 'touch_count': None,
        'observed_broken_count': 2, 'broken_rate_pct': None,
        'items': [{'outcome': 'broken', 'date': '2026-11-06', 'name': '其他日期个股'}]},
        'comparison': {'previous_date': '2026-11-06', 'broken_rate_delta_pp': None},
        'history': [{'date': '2026-11-06', 'broken_rate_pct': 5}]}
    result = review.project_broken_boards_review(analysis, DAY)
    assert result['observed_broken_count'] == 2
    assert result['touch_count'] is None and result['broken_rate_pct'] is None
    assert 'broken_rate_pct' not in result['comparison']
    assert result['representatives'] == []
    assert review.project_broken_boards_review(analysis, '2026-11-08')['state'] == 'unknown'


def test_sector_review_ranks_both_sides_from_same_day_quotes():
    data = {'items': [
        {'name': '历史行业', 'kind': 'industry', 'change_pct': 20, 'quote_date': '2026-11-06'},
        {'name': '当日行业', 'kind': 'industry', 'change_pct': 2, 'quote_date': DAY},
        {'name': '当日弱概念', 'kind': 'concept', 'change_pct': -3, 'quote_date': DAY},
        {'name': '未报价概念', 'kind': 'concept', 'change_pct': None, 'quote_date': DAY}]}
    result = review.project_sector_review(data, DAY)
    assert result['industry']['priced_count'] == result['concept']['priced_count'] == 1
    assert result['industry']['rising'][0]['name'] == '当日行业'
    assert result['concept']['falling'][0]['name'] == '当日弱概念'
    assert not result['industry']['falling'] and not result['concept']['rising']


def test_historical_monitor_mismatch_is_a_gap_and_discards_wrong_day_facts(sources):
    raw, _ = sources
    raw['abnormal']['evidence'][0]['data']['monitor_observed_date'] = '2026-11-10'
    result = _read()
    assert next(row for row in result['summary']['modules'] if row['module'] == 'abnormal')['state'] == 'unknown'
    assert '监管样本' not in tools.dumps(result)
    assert not any(row['kind'] == 'abnormal' for row in result['evidence'])


def test_scope_future_and_read_change_are_rejected_before_returning_evidence(sources, monkeypatch):
    assert _read(scope='arbitrary')['state'] == 'error'
    assert _read(day='2026-11-10')['state'] == 'not_due'
    calls = iter(({'daily_raw': {'market_snapshot_id': 'retained'}}, {'daily_raw': {'market_snapshot_id': 'changed'}}))
    monkeypatch.setattr(tools, '_heads', lambda: next(calls))
    result = _read()
    assert result['state'] == 'error' and not result['evidence']


def test_result_budget_omits_whole_optional_rows_without_losing_modules_or_slots(sources, monkeypatch):
    original = review._news

    def large_news(day):
        raw, data, state, reason = original(day)
        data['news_samples'] = [{'title': '重要消息' * 1800} for _ in range(3)]
        return raw, data, state, reason

    monkeypatch.setattr(review, '_news', large_news)
    result = _read()
    assert len(result['evidence']) == 13
    assert result['summary']['sample_rows_omitted_for_budget'] > 0
    assert len(tools.dumps(result).encode()) <= tools.MAX_RESULT_BYTES
    assert len(next(row for row in result['evidence'] if row['kind'] == 'intraday')['data']['slots']) == 8
