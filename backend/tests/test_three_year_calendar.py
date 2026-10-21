"""Calendar provenance validation uses small controlled files, never live APIs."""
from copy import deepcopy
import pandas as pd
import pytest

from app.services.three_year_calendar import INDEX_CODES, index_dates, validate_three_year_calendar, years_before
from app.services.sync_service import midnight_ms


@pytest.fixture
def three_year_inputs(tmp_path):
    days = [d.strftime('%Y-%m-%d') for d in pd.bdate_range('2023-10-20', '2026-10-20')]
    path = tmp_path / 'bulk.parquet'
    pd.DataFrame({'date_ms': [midnight_ms(day) for day in days for _ in range(2)]}).to_parquet(path)
    rows = [{'date_ms': midnight_ms(day), 'open_price': 10., 'high_price': 11., 'low_price': 9.,
             'close_price': 10., 'volume': 0., 'turnover': 0.} for day in days]
    indices = {symbol: {'data': {'thscode': symbol, 'interval': '1d', 'item': deepcopy(rows)}} for symbol in INDEX_CODES}
    official = [day for day in days if day >= '2025-10-20']
    return path, days, indices, official


def test_three_year_calendar_has_explicit_inferred_provenance(three_year_inputs):
    path, days, indices, official = three_year_inputs
    actual, proof = validate_three_year_calendar(path, '2023-10-20', '2026-10-20', indices, official)
    assert actual == days
    assert proof['basis'] == 'source_derived_cross_checked' and proof['official_history'] is False
    assert proof['count'] == len(days) >= 650
    assert set(proof['indices']) == set(INDEX_CODES)
    assert proof['official_recent']['count'] == len(official)
    assert proof['official_recent']['matches_derived_overlap'] is True
    assert all(item['dates_sha256'] == proof['dates_sha256'] for item in proof['indices'].values())
    assert '不证明逐股无缺报' in proof['scope']


def test_calendar_year_subtraction_handles_leap_day():
    assert years_before('2024-02-29') == '2021-02-28'
    assert years_before('2026-10-20') == '2023-10-20'


@pytest.mark.parametrize('fault', ['missing_index', 'missing_index_day', 'extra_bulk_day', 'official_gap', 'official_extra'])
def test_disagreement_blocks_calendar_publication(three_year_inputs, fault):
    path, days, indices, official = three_year_inputs
    if fault == 'missing_index':
        del indices[INDEX_CODES[-1]]
    elif fault == 'missing_index_day':
        del indices[INDEX_CODES[-1]]['data']['item'][10]
    elif fault == 'extra_bulk_day':
        pd.DataFrame({'date_ms': [midnight_ms(day) for day in days + ['2024-10-19']]}).to_parquet(path)
    elif fault == 'official_gap':
        official.remove(official[10])
    else:
        official.append('2026-10-18')
    with pytest.raises(ValueError):
        validate_three_year_calendar(path, '2023-10-20', '2026-10-20', indices, official)


@pytest.mark.parametrize('fault', ['one_year_only', 'start_too_late', 'target_missing', 'official_too_short'])
def test_short_or_shifted_window_is_not_called_three_years(three_year_inputs, fault):
    path, days, indices, official = three_year_inputs
    if fault == 'official_too_short':
        official = official[-20:]
    else:
        filtered = [day for day in days if day >= ('2025-10-20' if fault == 'one_year_only' else '2023-11-06')] if fault != 'target_missing' else days[:-1]
        pd.DataFrame({'date_ms': [midnight_ms(day) for day in filtered]}).to_parquet(path)
    with pytest.raises(ValueError):
        validate_three_year_calendar(path, '2023-10-20', '2026-10-20', indices, official)


@pytest.mark.parametrize('fault', ['duplicate', 'future', 'bad_ohlc', 'nan_volume', 'negative_turnover', 'wrong_symbol', 'wrong_row_symbol', 'date_conflict', 'intraday', 'null_date'])
def test_every_index_row_is_validated(three_year_inputs, fault):
    _, _, indices, _ = three_year_inputs
    payload = indices['000001.SH']
    rows = payload['data']['item']
    if fault == 'duplicate': rows.append(deepcopy(rows[0]))
    elif fault == 'future': rows[-1]['date_ms'] = midnight_ms('2026-10-21')
    elif fault == 'bad_ohlc': rows[3]['high_price'] = 1.
    elif fault == 'nan_volume': rows[3]['volume'] = float('nan')
    elif fault == 'negative_turnover': rows[3]['turnover'] = -1.
    elif fault == 'wrong_symbol': payload['data']['thscode'] = '399001.SZ'
    elif fault == 'wrong_row_symbol': rows[3]['thscode'] = '399001.SZ'
    elif fault == 'date_conflict': rows[3]['date'] = '2026-10-21'
    elif fault == 'intraday': payload['data']['interval'] = '1m'
    else: rows[3]['date_ms'] = None
    with pytest.raises(ValueError):
        index_dates(payload, '000001.SH', '2023-10-20', '2026-10-20')


def test_prewarm_is_validated_but_not_in_calendar(three_year_inputs):
    path, days, indices, official = three_year_inputs
    old = deepcopy(indices['000001.SH']['data']['item'][0])
    old['date_ms'] = midnight_ms('2023-09-01')
    indices['000001.SH']['data']['item'].insert(0, old)
    actual, _ = validate_three_year_calendar(path, '2023-10-20', '2026-10-20', indices, official)
    assert actual == days and '2023-09-01' not in actual
    old['low_price'] = 100.
    with pytest.raises(ValueError, match='OHLC'):
        validate_three_year_calendar(path, '2023-10-20', '2026-10-20', indices, official)


def test_recent_official_calendar_is_compared_only_through_frozen_target(three_year_inputs):
    path, days, indices, official = three_year_inputs
    for payload in indices.values(): payload['data']['item'].pop()
    actual, proof = validate_three_year_calendar(path, '2023-10-19', '2026-10-19', indices, official)
    assert actual == days[:-1]
    assert proof['official_recent']['end'] == '2026-10-19'
    assert proof['official_recent']['count'] == len(official) - 1
