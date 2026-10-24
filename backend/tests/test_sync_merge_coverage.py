"""Coverage identity stays on Shanghai dates, including non-midnight sources."""
import duckdb
import pandas as pd
import pytest

from app.services.sync_service import merge_daily, midnight_ms


DAYS = ['2026-10-21', '2026-10-22', '2026-10-23']


def bar(symbol, day, *, offset=0, close=10.0):
    return {'thscode': symbol, 'currency': 'CNY', 'interval': '1d',
            'adjusted': 'none', 'date_ms': midnight_ms(day)+offset,
            'open_price': close, 'high_price': close+1, 'low_price': close-1,
            'close_price': close, 'volume': 100.0, 'turnover': 1000.0}


def write_rows(path, rows):
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def run_merge(tmp_path, incoming, previous, master, **kwargs):
    target = tmp_path/'merged.parquet'
    old = write_rows(tmp_path/'old.parquet', previous) if previous is not None else None
    new = write_rows(tmp_path/'incoming.parquet', incoming)
    result = merge_daily(new, old, target, DAYS[0], DAYS[-1], DAYS, master, **kwargs)
    return result, target


@pytest.mark.parametrize('offset', [0, 1, 8*3600000-1, 8*3600000,
                                  16*3600000, 86400000-1])
def test_missing_counts_use_shanghai_day_for_millisecond_boundaries(tmp_path, offset):
    # Listing and delisting boundaries are inclusive. Multiple distinct quotes
    # on one local day do not create another expected market day.
    old = [bar('600001.SH', day, offset=offset) for day in DAYS]
    old += [bar('600001.SH', DAYS[1], offset=(offset+1) % 86400000)]
    incoming = [bar('000001.SZ', DAYS[1], offset=offset),
                bar('920001.BJ', DAYS[2], offset=offset)]
    master = [{'thscode': '600001.SH', 'list_date': DAYS[0]},
              {'thscode': '000001.SZ', 'list_date': DAYS[1], 'end_date': DAYS[1]},
              {'thscode': '920001.BJ', 'list_date': DAYS[1]},
              {'thscode': '600002.SH', 'list_date': None}]
    result, target = run_merge(tmp_path, incoming, old, master)
    assert result['confirmed_missing_lower_bound'] == 1
    assert result['unexplained_missing'] is None
    assert result['expected_coverage_state'] == 'unknown'
    assert result['identity_unknown_symbols'] == ['600002.SH']
    assert result['rows'] == 6
    with duckdb.connect(':memory:') as con:
        rows = con.execute('SELECT thscode,date_ms FROM read_parquet(?)', [str(target)]).fetchall()
    assert rows == sorted(rows)


def test_revisions_outside_window_and_same_day_quotes_keep_exact_merge_keys(tmp_path):
    old = [bar('600001.SH', day) for day in DAYS]
    old += [bar('600001.SH', '2026-10-20'), bar('600001.SH', '2026-10-24')]
    incoming = [bar('600001.SH', DAYS[1], close=12),
                bar('600001.SH', DAYS[1], offset=1, close=11)]
    master = [{'thscode': '600001.SH', 'list_date': DAYS[0]}]
    result, target = run_merge(tmp_path, incoming, old, master)
    assert result['overlap_rows'] == 1 and result['revised_overlap_rows'] == 1
    assert result['rows'] == 4 and result['confirmed_missing_lower_bound'] == 0
    assert result['unexplained_missing'] == 0
    with duckdb.connect(':memory:') as con:
        rows = con.execute('SELECT date_ms,close_price FROM read_parquet(?)', [str(target)]).fetchall()
    assert rows == [(midnight_ms(DAYS[0]), 10), (midnight_ms(DAYS[1]), 12),
                    (midnight_ms(DAYS[1])+1, 11), (midnight_ms(DAYS[2]), 10)]


def test_no_change_keeps_derived_output_absent_after_full_coverage_validation(tmp_path):
    rows = [bar('600001.SH', day, offset=86400000-1) for day in DAYS]
    result, target = run_merge(tmp_path, rows[-1:], rows,
                               [{'thscode': '600001.SH', 'list_date': DAYS[0]}],
                               unchanged_rows=len(rows))
    assert result['derived_file_written'] is False and not target.exists()
    assert result['unexplained_missing'] == 0


def test_initial_coverage_retains_known_missing_count(tmp_path):
    rows = [bar('600001.SH', day) for day in DAYS]
    master = [{'thscode': '600001.SH', 'list_date': DAYS[0]},
              {'thscode': '000001.SZ', 'list_date': DAYS[1], 'end_date': DAYS[1]}]
    result, _ = run_merge(tmp_path, rows, None, master)
    assert result['confirmed_missing_lower_bound'] == result['unexplained_missing'] == 1
    assert result['expected_coverage_state'] == 'partial'


@pytest.mark.parametrize('corrupt_previous', [False, True])
@pytest.mark.parametrize('corruption', ['duplicate', 'invalid_ohlc'])
def test_corrupt_raw_data_still_fails_before_coverage_or_output(tmp_path, corrupt_previous, corruption):
    old = [bar('600001.SH', day) for day in DAYS]
    incoming = [bar('600001.SH', DAYS[-1])]
    rows = old if corrupt_previous else incoming
    if corruption == 'duplicate':
        rows.append(dict(rows[0]))
    else:
        rows[0]['low_price'] = rows[0]['high_price']+1
    with pytest.raises(ValueError, match='原始行情校验失败'):
        run_merge(tmp_path, incoming, old, [{'thscode': '600001.SH', 'list_date': DAYS[0]}])
    assert not (tmp_path/'merged.parquet').exists()


def test_retained_null_date_still_fails_before_output(tmp_path):
    old = [bar('600001.SH', day) for day in DAYS]
    old[1]['date_ms'] = None
    with pytest.raises(ValueError, match='原始行情校验失败'):
        run_merge(tmp_path, [bar('600001.SH', DAYS[-1])], old,
                  [{'thscode': '600001.SH', 'list_date': DAYS[0]}])
    assert not (tmp_path/'merged.parquet').exists()


def test_incoming_null_date_keeps_original_window_filter_semantics(tmp_path):
    # Incoming is filtered to the frozen range before raw validation. A NULL
    # timestamp cannot match that range; this optimization does not widen it.
    old = [bar('600001.SH', day) for day in DAYS]
    missing_date = bar('600001.SH', DAYS[-1]); missing_date['date_ms'] = None
    result, _ = run_merge(tmp_path, [missing_date, bar('600001.SH', DAYS[-1])], old,
                           [{'thscode': '600001.SH', 'list_date': DAYS[0]}])
    assert result['rows'] == 3 and result['overlap_rows'] == 1
    assert result['confirmed_missing_lower_bound'] == 0


def test_missing_complete_market_day_cannot_publish(tmp_path):
    rows = [bar('600001.SH', DAYS[0]), bar('600001.SH', DAYS[-1], offset=86400000-1)]
    with pytest.raises(ValueError, match='缺少完整市场交易日'):
        run_merge(tmp_path, rows, None, [{'thscode': '600001.SH', 'list_date': DAYS[0]}])
    assert not (tmp_path/'merged.parquet').exists()
