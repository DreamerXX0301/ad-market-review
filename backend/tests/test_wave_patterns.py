"""A five-wave candidate is one structure, never arbitrary 1..N numbering."""
import pytest
from datetime import date, timedelta
from app.analytics.waves import wave_patterns, candidate_waves


def points(prices,down=False):
    return [{'date':f'2026-01-{i+1:02d}','confirmed_at':f'2026-01-{i+3:02d}','price':price,'kind':('high' if i%2==0 else 'low') if down else ('low' if i%2==0 else 'high')} for i,price in enumerate(prices)]


def test_up_impulse_and_its_own_abc_are_separate_phases():
    result=wave_patterns(points([10,15,12,22,17,25,20,23,18]))
    wave=next(row for row in result if row['start_date']=='2026-01-01')
    assert wave['direction']=='up' and wave['has_correction']
    assert [row['label'] for row in wave['points']]==['0','1?','2?','3?','4?','5?','A?','B?','C?']
    assert all(row['phase']=='correction' for row in wave['points'][-3:])


def test_downward_impulse_is_mirrored_and_upward_abc_is_not_a_new_up_impulse():
    result=wave_patterns(points([30,25,28,18,23,15,20,17,22],down=True))
    assert result[0]['direction']=='down' and result[0]['has_correction']


@pytest.mark.parametrize('prices',[
    [10,15,9,22,17,25], # 2 breaks origin
    [10,15,12,22,14,25], # 4 overlaps 1
    [10,18,17,20,19,29], # 3 shortest
    [10,15,12,22,17,21], # truncated 5 is not implemented
])
def test_invalid_or_unimplemented_impulses_are_not_numbered(prices):
    assert wave_patterns(points(prices))==[]


def test_no_forced_abc_when_c_does_not_pass_a_or_b_exceeds_origin():
    for tail in ([20,26,18],[20,23,21]):
        wave=wave_patterns(points([10,15,12,22,17,25]+tail))[0]
        assert wave['has_correction'] is False and len(wave['points'])==6


def test_incomplete_five_leg_structure_is_not_padded_with_future_points():
    assert wave_patterns(points([10,15,12,22,17]))==[]


def bars(prices, step=1):
    return [{'date': (date(2026, 1, 1) + timedelta(days=i * step)).isoformat(),
             'high': value + .01, 'low': value - .01, 'open': value, 'close': value}
            for i, value in enumerate(prices)]


@pytest.mark.parametrize('flag', ['week_closed', 'month_closed', 'complete',
                                'weekly_quote_coverage_complete', 'monthly_quote_coverage_complete'])
def test_forming_or_incomplete_confirmation_bar_is_not_a_completed_confirmation(flag):
    rows = bars([10, 11, 15, 12, 11], step=7 if flag.startswith('week') else 30)
    assert len(candidate_waves(rows)['points']) == 1
    rows[-1][flag] = False
    result = candidate_waves(rows)
    assert result['points'] == []
    assert result['excluded_bars'][0]['date'] == rows[-1]['date']
    rows[-1][flag] = True
    confirmed = candidate_waves(rows)['points'][0]
    assert confirmed['date'] == rows[2]['date']
    assert confirmed['confirmed_at'] == rows[-1]['date']


def skeleton_bars():
    pivots = [10, 15, 12, 22, 17, 25, 20, 23, 18]
    prices = [12, 11]
    for index, value in enumerate(pivots):
        prices.append(value)
        if index + 1 < len(pivots):
            following = pivots[index + 1]
            prices.extend([value + (following - value) / 3, value + 2 * (following - value) / 3])
    prices.extend([19, 20])
    return bars(prices)


@pytest.mark.parametrize('marker', [
    {'price_continuity': False},
    {'missing_market_sessions_before': 4},
    {'weekly_missing_market_sessions': 5, 'week_closed': True, 'complete': True},
    {'monthly_continuity_reset_reason': '缺少整月报价'},
    {'corporate_action': True},
])
def test_gap_or_daily_action_starts_a_new_run_without_cross_run_confirmation_or_patterns(marker):
    rows = skeleton_bars()
    baseline = candidate_waves(rows)
    assert baseline['patterns'] and baseline['patterns'][0]['has_correction']
    boundary = 15
    rows[boundary].update(marker)
    result = candidate_waves(rows)
    assert result['patterns'] == []
    assert result['segment_count'] == 2
    assert result['excluded_bars'] == [], 'a valid raw reset bar may start its own new run'
    assert result['continuity_breaks'][0]['date'] == rows[boundary]['date']
    for point in result['points']:
        position = next(i for i, row in enumerate(rows) if row['date'] == point['date'])
        confirmation = next(i for i, row in enumerate(rows) if row['date'] == point['confirmed_at'])
        assert (position < boundary and confirmation < boundary) or (position >= boundary and confirmation >= boundary)
    for segment in result['segments']:
        assert segment['end_date'] < rows[boundary]['date'] or segment['start_date'] >= rows[boundary]['date']
    assert len({point['segment_id'] for point in result['points']}) == 2


@pytest.mark.parametrize('period_flag', ['week_closed', 'month_closed'])
def test_aggregate_action_bar_mixes_raw_price_bases_and_is_excluded(period_flag):
    rows = skeleton_bars()
    for row in rows:row.update({period_flag: True, 'complete': True})
    rows[15]['corporate_action'] = True
    result = candidate_waves(rows)
    assert result['patterns'] == []
    assert result['excluded_bars'][0]['date'] == rows[15]['date']
    assert all(point['date'] != rows[15]['date'] and point['confirmed_at'] != rows[15]['date'] for point in result['points'])


def test_excluding_bad_middle_bar_does_not_rejoin_its_neighbours():
    rows = skeleton_bars()
    rows[15]['weekly_quote_coverage_complete'] = False
    result = candidate_waves(rows)
    assert result['patterns'] == [] and result['segment_count'] == 2
    assert all(segment['end_date'] < rows[15]['date'] or segment['start_date'] > rows[15]['date'] for segment in result['segments'])


def test_initial_indicator_warmup_is_not_a_price_gap():
    rows = bars([10, 11, 15, 12, 11])
    for row in rows:row.update(macd_dif=None, indicator_warmup_reason='初始预热不足')
    assert len(candidate_waves(rows)['points']) == 1


def test_standalone_pattern_builder_cannot_cross_segment_ids_or_append_foreign_abc():
    source = points([10, 15, 12, 22, 17, 25, 20, 23, 18])
    for index, point in enumerate(source):point['segment_id'] = 'first' if index < 3 else 'second'
    assert wave_patterns(source) == []
    for index, point in enumerate(source):point['segment_id'] = 'first' if index < 6 else 'second'
    result = wave_patterns(source)
    assert len(result) == 1 and not result[0]['has_correction']


@pytest.mark.parametrize('volume', [0, -1, float('nan'), None])
def test_no_trade_or_unknown_volume_cannot_count_as_stock_confirmation(volume):
    rows=bars([10,11,15,12,11])
    for row in rows:row['volume']=100
    assert len(candidate_waves(rows)['points'])==1
    rows[-1]['volume']=volume
    result=candidate_waves(rows)
    assert result['points']==[] and result['excluded_bars'][0]['date']==rows[-1]['date']


@pytest.mark.parametrize('close', [0, -1, float('nan'), None, 16, 9])
def test_invalid_or_out_of_range_close_cannot_be_pivot_or_confirmation(close):
    rows=bars([10,11,15,12,11])
    rows[-1]['close']=close
    result=candidate_waves(rows)
    assert result['points']==[] and result['excluded_bars'][0]['date']==rows[-1]['date']
