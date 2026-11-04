from app.services.intraday_feedback import build_intraday_feedback


def quote(code, change=10, amount=100):
    return {'instrument_id': code, 'name': code, 'prev_close': 100,
            'latest_price': 100 + change, 'change_pct': change, 'currency': 'CNY', 'amount': amount}


def event(code, height):
    return {'instrument_id': code, 'continue_day_cnt': height}


def test_intraday_pulse_and_ladder_use_verified_sample_and_known_heights():
    current = [quote('000001.SZ'), quote('000002.SZ'), quote('000003.SZ', -1),
               {**quote('000004.SZ', 0), 'change_pct': 3}]
    pool = {'state': 'observed', 'items': [event('000001.SZ', 2), event('000002.SZ', None)]}
    report = build_intraday_feedback(current, indices=[quote('000001.SH', 1)], pool=pool)
    assert report['market_pulse']['breadth'] == {'up': 2, 'down': 1, 'flat': 0, 'valid': 3, 'observed': 4}
    assert report['market_pulse']['turnover'] == {'amount': 400, 'valid': 4}
    assert [row['change_pct'] for row in report['market_pulse']['indices']] == [1, None, None, None]
    ladder = report['ladder']
    assert ladder['state'] == 'observed' and ladder['sealed_count'] == 2
    assert ladder['height_state'] == 'partial' and ladder['unknown_height_count'] == 1
    assert ladder['highest_board'] == 2 and ladder['multi_board_count'] == 1
    assert ladder['first_board_count'] is None
    assert ladder['groups'][0]['items'][0]['instrument_id'] == '000001.SZ'


def test_relay_separates_prior_session_promotion_from_same_day_pool_changes():
    current = [quote('000001.SZ'), quote('000002.SZ'), quote('000003.SZ', -1)]
    current_pool = {'state': 'observed', 'items': [event('000001.SZ', 3), event('000002.SZ', 1)]}
    prior = {'date': '2026-10-30', 'complete': True, 'items': [
        {'instrument_id': '000001.SZ', 'previous_board_count': 2},
        {'instrument_id': '000002.SZ', 'previous_board_count': 3},
        {'instrument_id': '000003.SZ', 'previous_board_count': 1},
    ]}
    earlier = {'state': 'observed', 'items': [event('000001.SZ', 3), event('000003.SZ', 2),
                                             event('000004.SZ', 1)]}
    report = build_intraday_feedback(current, pool=current_pool, prior_session=prior,
                                      previous={'label': '上午观察'}, previous_pool=earlier,
                                      break_pool={'state': 'observed', 'items': []})['relay_feedback']
    session = report['prior_session']
    assert session['state'] == 'observed' and session['total'] == 3
    assert session['up'] == 2 and session['down'] == 1 and session['promotion_count'] == 1
    assert session['promotion_state'] == 'observed'
    assert {row['instrument_id']: row['state'] for row in session['items']} == {
        '000001.SZ': 'promoted', '000002.SZ': 'sealed', '000003.SZ': 'not_in_verified_pool',
    }
    compare = report['same_day']
    assert compare['state'] == 'partial' and compare['label'] == '上午观察'
    assert compare['held_count'] == compare['entered_count'] == compare['absent_count'] == 1
    assert compare['held_state'] == compare['entered_state'] == 'observed' and compare['absent_state'] == 'partial'
    assert compare['absent'][0]['instrument_id'] == '000003.SZ'
    assert compare['absent'][0]['state'] == 'not_in_verified_pool'


def test_partial_or_missing_pool_never_turns_absence_into_evidence():
    earlier = {'state': 'observed', 'items': [event('000001.SZ', 1)]}
    current = [quote('000001.SZ', -1), quote('000002.SZ')]
    report = build_intraday_feedback(current, pool={'state': 'partial', 'items': [event('000002.SZ', 1)]},
                                      previous_pool=earlier)
    assert report['ladder']['sealed_count'] == 1 and report['ladder']['state'] == 'partial'
    assert report['ladder']['multi_board_count'] is None
    same_day = report['relay_feedback']['same_day']
    assert same_day['entered_count'] == 1 and same_day['absent_count'] is None
    assert same_day['absent'] == []
    missing = build_intraday_feedback([])
    assert missing['ladder']['sealed_count'] is None
    empty_observed = build_intraday_feedback([], pool={'state': 'observed', 'items': []})
    assert empty_observed['ladder']['sealed_count'] == empty_observed['ladder']['first_board_count'] == 0


def test_true_break_pool_excludes_conflicting_seals_and_keeps_theme_proof_separate():
    current = [quote('000001.SZ'), quote('000002.SZ', 9), quote('000003.SZ', 4), quote('000004.SZ', 5)]
    sealed = {'state': 'observed', 'items': [event('000001.SZ', 1), event('000002.SZ', 2)]}
    broken = {'state': 'observed', 'items': [event('000002.SZ', None), {
        **event('000003.SZ', 5), 'open_times': 2, 'limit_up_reason': '来源记录的原因' + '字' * 100,
    }]}
    topics = [{'instrument_id': 'topic', 'name': '电池', 'kind': 'concept'}]
    membership = {'topic': {'instrument_ids': ['000001.SZ', '000003.SZ'], 'date': '2026-10-29'}}
    report = build_intraday_feedback(current, pool=sealed, break_pool=broken, sectors=topics, members=membership)
    ladder = report['ladder']
    assert ladder['state'] == 'partial' and ladder['sealed_count'] == 1
    assert ladder['highest_board'] == 1
    assert ladder['broken_board']['state'] == 'partial' and ladder['broken_board']['count'] == 1
    assert ladder['broken_board']['conflict_count'] == 1
    row = ladder['broken_board']['items'][0]
    assert row['instrument_id'] == '000003.SZ' and row['board_count'] is None and row['open_times'] == 2
    assert row['themes'] == [{'name': '电池', 'kind': 'concept', 'member_date': '2026-10-29'}]
    assert ladder['groups'][0]['items'][0]['themes'] == row['themes']
    assert len(row['source_reason']) == 80


def test_break_pool_unknown_is_not_zero_and_prior_breaks_do_not_count_as_promotions():
    current = [quote('000001.SZ'), quote('000002.SZ', -3)]
    pool = {'state': 'observed', 'items': [event('000001.SZ', 3)]}
    prior_breaks = {'date': '2026-11-02', 'complete': True, 'items': [
        {'instrument_id': '000001.SZ', 'previous_board_count': 2}, {'instrument_id': '000002.SZ'},
    ]}
    report = build_intraday_feedback(current, pool=pool, prior_broken_session=prior_breaks,
                                     break_pool={'state': 'unknown', 'items': []})
    assert report['ladder']['broken_board']['count'] is None
    prior = report['relay_feedback']['prior_broken_session']
    assert prior['total'] == 2 and prior['up'] == prior['down'] == 1 and prior['sealed_count'] == 1
    assert prior['items'][0]['state'] == 'sealed' and 'promotion_count' not in prior
    empty = build_intraday_feedback(current, pool=pool, break_pool={'state': 'observed', 'items': []})
    assert empty['ladder']['broken_board']['count'] == 0
