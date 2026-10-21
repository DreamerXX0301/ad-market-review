"""Local episode shapes and retained-source comparison never invent causes."""
from copy import deepcopy

import duckdb
import pandas as pd

from app.services import gap_diagnosis as gap


DAYS = ['2026-01-02', '2026-01-05', '2026-01-06', '2026-01-07', '2026-01-08',
        '2026-01-09', '2026-01-12', '2026-01-13', '2026-01-14', '2026-01-15']
SYMBOL = '600001.SH'


def described(missing, *, date_budget=20000, episode_budget=10000):
    return gap.stock_episodes(DAYS, missing, set(DAYS)-set(missing), SYMBOL, 'fixture',
                              date_budget=date_budget, episode_budget=episode_budget)[0]


def test_contiguous_market_sessions_cross_weekends_and_have_actual_neighbors():
    stock = described([DAYS[1], DAYS[3], *DAYS[5:]])
    episodes = stock['episodes']
    assert stock['missing_sessions'] == 7 and stock['episode_count'] == 3
    assert stock['isolated_episode_count'] == 2 and stock['long_episode_count'] == 1
    assert stock['longest_episode_sessions'] == 5 and stock['long_session_count'] == 5
    assert episodes[2]['shape'] == 'long_consecutive'
    assert episodes[2]['previous_quote_date'] == DAYS[4] and episodes[2]['next_quote_date'] is None
    assert episodes[0]['previous_quote_date'] == DAYS[0] and episodes[0]['next_quote_date'] == DAYS[2]
    assert all(item['confirmed_suspension'] is None for item in episodes)
    assert episodes[2]['gap_id'] == described(DAYS[5:])['episodes'][0]['gap_id']


def test_raw_local_presence_distinguishes_conversion_difference_and_unknown_absence():
    stock = described([DAYS[1], DAYS[3]])
    gap.attach_local_evidence([stock], {(SYMBOL, DAYS[1]): True}, raw_verified=True)
    assert stock['episodes'][0]['diagnosis_type'] == 'local_normalization_difference'
    assert stock['episodes'][1]['diagnosis_type'] == 'source_unquoted_unknown'
    assert stock['episodes'][1]['source_comparison']['raw_absent_sessions'] == 1
    assert all(item['confirmed_suspension'] is None for item in stock['episodes'])
    summary = gap.summary([stock], source_signature='fixture')
    assert summary['shared_modules'] == ['daily_raw', 'analysis_bars']
    assert summary['total_missing_sessions'] == 2 and summary['details_complete'] is True
    assert '不证明' in summary['notice']


def test_incomplete_detail_budgets_preserve_exact_counts_and_keep_evidence_unknown():
    stock = described(DAYS[1:8], date_budget=2)
    gap.attach_local_evidence([stock], {(SYMBOL, day): True for day in DAYS}, raw_verified=True)
    episode = stock['episodes'][0]
    assert stock['missing_sessions'] == 7 and episode['missing_sessions'] == 7
    assert episode['dates'] == DAYS[1:3] and not episode['dates_complete']
    assert episode['diagnosis_type'] == 'unverified_raw'
    assert gap.summary([stock])['details_complete'] is False
    assert gap.summary([stock])['reported_missing_sessions'] == 2
    no_detail = described([DAYS[1], DAYS[3]], episode_budget=0)
    assert no_detail['episode_count'] == 2 and no_detail['episodes'] == []


def test_one_raw_join_checks_unique_valid_source_prices(tmp_path):
    stock = described([DAYS[1], DAYS[3]])
    stamp = lambda day: int(pd.Timestamp(day, tz='Asia/Shanghai').timestamp()*1000)
    row = {'thscode': SYMBOL, 'date_ms': stamp(DAYS[1]), 'open_price': 10., 'high_price': 11.,
           'low_price': 9., 'close_price': 10., 'volume': 1., 'turnover': 10.}
    path = tmp_path/'raw.parquet'
    pd.DataFrame([row, {**row, 'date_ms': stamp(DAYS[3]), 'close_price': -1.}]).to_parquet(path)
    with duckdb.connect(':memory:') as connection:
        presence = gap.raw_comparison(connection, path, [stock])
    assert presence[(SYMBOL, DAYS[1])]['valid_quote'] is True and presence[(SYMBOL, DAYS[3])]['valid_quote'] is False
    gap.attach_local_evidence([stock], presence, raw_verified=True)
    assert stock['episodes'][1]['diagnosis_type']=='local_quote_conflict'
    assert stock['episodes'][1]['source_comparison']['raw_conflict_sessions']==1


def test_overlay_requires_exact_inputs_and_never_infers_suspension_or_mutates_local_report():
    stock = described([DAYS[1]])
    gap.attach_local_evidence([stock], {}, raw_verified=True)
    report = {'snapshot_id': 'head', 'source_signature': 'sig', 'daily': {'gap_diagnosis': gap.summary([stock])}}
    original = deepcopy(report); diagnosis = report['daily']['gap_diagnosis']; identifier = stock['episodes'][0]['gap_id']
    evidence = {'snapshot_id': 'head', 'source_signature': 'sig', 'diagnosis_id': diagnosis['diagnosis_id'],
                'source': 'fixture', 'receipt_id': 'receipt', 'receipt_sha256': 'a'*64, 'checked_at_ms': 100,
                'gaps': {identifier: {'status': 'source_no_quote', 'missing_dates': [DAYS[1], DAYS[2]], 'confirmed_suspension': True}}}
    view = gap.overlay_remote_verification(report, evidence)
    episode = view['daily']['gap_diagnosis']['stocks'][0]['episodes'][0]
    assert episode['source_verification']['missing_dates'] == [DAYS[1]]
    assert 'confirmed_suspension' not in episode['source_verification'] and episode['confirmed_suspension'] is None
    assert report == original
    stale = gap.overlay_remote_verification(report, {**evidence, 'source_signature': 'old'})
    assert stale['daily']['gap_diagnosis']['source_verification_stale']
    assert stale['daily']['gap_diagnosis']['stocks'][0]['episodes'][0]['source_verification'] is None
