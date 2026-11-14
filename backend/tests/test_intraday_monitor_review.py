"""Review regressions using isolated synthetic state and sampling evidence."""
from datetime import date, datetime, timedelta
import errno
from itertools import count
import json
import os
from pathlib import Path

import pytest

from app import core
from app.services import intraday_monitor as monitor
from app.services import intraday_monitor_storage as storage
from app.services.intraday_monitor_rules import evaluate_snapshot, merge_observations
from app.services.noon_collection import CHINA

SYMBOL = '600001.SH'
DAY = '2026-11-13'


@pytest.fixture
def isolated_monitor(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR'):
        monkeypatch.delenv(key, raising=False)
    core.ensure_ready()
    return tmp_path


def batch(identifier, day=DAY, slot='09:40', manifest=None):
    with core.connect() as connection:
        connection.execute('''INSERT INTO job(job_id,request_key,logical_key,kind,target_date,
            parameters_json,parameters_hash,status,created_at_ms)
            VALUES(?,?,?,'source_check',?,'{}',?,'succeeded',1)''',
            (identifier, identifier, identifier, day, 'a' * 64))
        connection.execute('''INSERT INTO intraday_monitor_batch
            (batch_id,job_id,trade_date,slot_key,config_revision,status,manifest_json)
            VALUES(?,?,?,?,1,'succeeded',?)''',
            (identifier, identifier, day, slot, json.dumps(manifest or {})))


def quote(price, day=DAY, slot='09:40'):
    stamp = int(datetime.fromisoformat(day + 'T' + slot).replace(tzinfo=CHINA).timestamp() * 1000)
    return dict(instrument_id=SYMBOL, trade_date=day, latest_price=price, change_pct=0,
                previous_close=price, open=price, high=price, low=price, volume=100,
                turnover=price * 100, received_at_ms=stamp, source_time_ms=stamp,
                quote_time_ms=None, valid=True, quality='batch_time_only', price_basis='none')


def observation(day, phase='observed'):
    return dict(instrument_id=SYMBOL, trade_date=day, slot_key='09:40',
                type='support_reclaim', phase=phase, episode='09:40',
                observed_at_ms=quote(10.2, day)['received_at_ms'], source_time_ms=None,
                price=10.2, lower=10., upper=10., source_kind='manual_line', source_id='stable-line',
                rule_key='manual:stable-line:1', revision=1, title='fixture', label='fixture', evidence={})


def test_same_manual_rule_and_clock_on_next_day_creates_new_card(isolated_monitor):
    previous = '2026-11-12'
    batch('monitor-previous-day', previous)
    batch('monitor-current-day', DAY)
    with core.connect() as connection:
        first = monitor._persist_observation(connection, observation(previous), 'monitor-previous-day',
                                              {'instrument_id': SYMBOL, 'name': 'fixture'}, quote(10.2, previous))
        second = monitor._persist_observation(connection, observation(DAY), 'monitor-current-day',
                                               {'instrument_id': SYMBOL, 'name': 'fixture'}, quote(10.2, DAY))
        assert first and second and first != second
        assert [row[0] for row in connection.execute('SELECT trade_date FROM intraday_monitor_event ORDER BY trade_date')] == [previous, DAY]
        assert connection.execute('SELECT count(*) FROM intraday_monitor_evidence').fetchone()[0] == 2


def level(key, lower, upper):
    return dict(key=key, kind='support', source_kind='automatic_zone' if key == 'wide' else 'manual_line',
                source_id=key, revision=1, lower=lower, upper=upper,
                baseline_date='2026-11-12', price_basis='none', evidence={})


def test_holding_wide_zone_with_a_different_point_does_not_invert_existing_card_bounds(isolated_monitor, monkeypatch):
    sequence = count(1)
    monkeypatch.setattr(monitor, 'new_id', lambda prefix: f'{prefix}-{next(sequence):05d}')
    levels = [level('wide', 10, 12), level('upper-manual', 11.5, 11.5), level('lower-manual', 10.5, 10.5)]
    state = None
    for index, (slot, price) in enumerate([('09:30', 12.5), ('09:40', 11.5), ('09:50', 10.5), ('10:00', 10.5)]):
        identifier = f'monitor-step-{index}'
        batch(identifier, slot=slot)
        current_quote = quote(price, slot=slot)
        result = evaluate_snapshot(levels, current_quote, state, trade_date=DAY, slot_key=slot)
        state = result['state']
        with core.connect() as connection:
            for item in merge_observations(result['observations']):
                monitor._persist_observation(connection, item, identifier,
                                               {'instrument_id': SYMBOL, 'name': 'fixture'}, current_quote)
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM intraday_monitor_event').fetchone()[0] >= 2
        assert not connection.execute('SELECT 1 FROM intraday_monitor_event WHERE upper_price<lower_price').fetchall()
        assert connection.execute('SELECT count(DISTINCT rule_key) FROM intraday_monitor_evidence').fetchone()[0] == 3


def test_cleanup_resumes_exact_batch_after_one_file_was_unlinked(isolated_monitor, monkeypatch):
    calendar = []
    cursor = date(2026, 7, 1)
    while len(calendar) < 100:
        if cursor.weekday() < 5:
            calendar.append(cursor.isoformat())
        cursor += timedelta(days=1)
    old, today = calendar[0], calendar[-1]
    identifier = 'monitor-expired-fixture'
    manifest = storage.save_capture(identifier, old,
                                    dict(quotes_by_id={SYMBOL: quote(10.2, old)}, sources=[], gaps=[]),
                                    calendar=calendar, generation='fixture-market', primary_snapshot_id='fixture-primary')
    batch(identifier, old, manifest=manifest)
    raw_path = core.raw_dir() / manifest['raw_file']['path']
    quote_path = core.published_dir() / manifest['quote_file']['path']
    original_unlink = type(raw_path).unlink
    failed = False
    def interrupted(path, *args, **kwargs):
        nonlocal failed
        if path == raw_path and not failed:
            failed = True
            raise OSError('synthetic interruption after first unlink')
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(type(raw_path), 'unlink', interrupted)
    try:
        storage.cleanup(calendar, today)
    except (OSError, ValueError):
        pass
    assert failed and not quote_path.exists() and raw_path.exists()
    result = storage.cleanup(calendar, today)
    assert result['removed_batches'] == 1 and not raw_path.exists()
    with core.connect() as connection:
        assert connection.execute('SELECT purged_at_ms FROM intraday_monitor_batch WHERE batch_id=?', (identifier,)).fetchone()[0] is not None


def test_quote_can_remain_visible_when_monitoring_levels_are_unavailable(isolated_monitor, monkeypatch):
    reason = '手动线复权依据缺失，自动参考价位样本不足'
    current = {**quote(10.2), 'monitoring_reason': reason,
               'level_unknowns': [{'source_id': 'manual-fixture', 'reason': reason}]}
    with core.connect() as connection:
        connection.execute('INSERT INTO intraday_monitor_state VALUES(?,?,?,?,?,?,?,?)',
                           (SYMBOL, DAY, '09:40', 0, '{}', json.dumps(current), '[]', 1))
    monkeypatch.setattr(monitor, 'now_china', lambda: datetime(2026, 11, 13, 9, 40, tzinfo=CHINA))
    monkeypatch.setattr(monitor, '_head', lambda _: None)
    monkeypatch.setattr(monitor, '_preconditions', lambda *_: (True, None))
    monkeypatch.setattr(monitor, '_worker_alive', lambda *_: True)
    monkeypatch.setattr(monitor, '_universe', lambda *_: (
        [dict(instrument_id=SYMBOL, name='fixture', code='600001', origins=['watchlist'],
              strategy_count=0, manual_count=1)],
        dict(tracking=0, watchlist=1, manual_lines=1, stocks=1, eligible=1, excluded=0)))
    row = monitor.read_view()['stocks'][0]
    assert row['latest_price'] == 10.2 and row['change_pct'] == 0
    assert row['state'] == 'unknown' and row['reason'] == reason
    assert row['level_count'] == 0


def test_capture_publishes_when_work_raw_and_published_are_different_mounts(isolated_monitor, monkeypatch):
    roots = [core.work_dir().resolve(), core.raw_dir().resolve(), core.published_dir().resolve()]
    original_rename, original_replace = Path.rename, os.replace
    def filesystem(path):
        value = Path(path).resolve()
        return next((root for root in roots if value == root or value.is_relative_to(root)), None)
    def same_mount(source, target):
        if filesystem(source) != filesystem(target):
            raise OSError(errno.EXDEV, 'synthetic separate compose volumes')
    def rename(source, target, *args, **kwargs):
        same_mount(source, target)
        return original_rename(source, target, *args, **kwargs)
    def replace(source, target, *args, **kwargs):
        same_mount(source, target)
        return original_replace(source, target, *args, **kwargs)
    monkeypatch.setattr(Path, 'rename', rename)
    monkeypatch.setattr(os, 'replace', replace)
    manifest = storage.save_capture('monitor-separate-mounts', DAY,
                                    dict(quotes_by_id={SYMBOL: quote(10.2)}, sources=[], gaps=[]),
                                    calendar=[DAY], generation='fixture-market', primary_snapshot_id='fixture-primary')
    assert storage.verify_file(manifest['quote_file']).is_file()
    assert storage.verify_file(manifest['raw_file']).is_file()


@pytest.mark.parametrize('interruption', ['before_registration', 'after_first_publication'])
def test_prepared_capture_recovers_exact_original_without_recollection(isolated_monitor, monkeypatch, interruption):
    class Interrupted(BaseException):
        pass
    identifier = 'monitor-prepared-recovery'
    registered = []
    def register(manifest):
        if interruption == 'before_registration' and not registered:
            raise Interrupted()
        registered.append(manifest)
    original_rename = Path.rename
    def rename(source, target, *args, **kwargs):
        value = original_rename(source, target, *args, **kwargs)
        if interruption == 'after_first_publication' and Path(target).suffix == '.parquet':
            raise Interrupted()
        return value
    monkeypatch.setattr(Path, 'rename', rename)
    captured = dict(quotes_by_id={SYMBOL: quote(10.2)}, sources=[], gaps=[], quality='complete')
    with pytest.raises(Interrupted):
        storage.save_capture(identifier, DAY, captured, calendar=[DAY], generation='fixture-market',
                             primary_snapshot_id='fixture-primary', register_manifest=register)
    monkeypatch.setattr(Path, 'rename', original_rename)
    assert (storage._staging(identifier) / 'manifest.json').is_file()
    existing = registered[-1] if registered else {}
    recovered = storage.recover_prepared(identifier, DAY, existing, register_manifest=registered.append)
    assert recovered['publication_state'] == 'published'
    assert storage.load_capture(recovered, identifier, DAY) == captured
    assert not storage._staging(identifier).exists()


def test_new_manual_source_cannot_join_a_finished_automatic_reclaim_episode(isolated_monitor):
    """A recent overlapping card is insufficient once its original episode ended."""
    def level(key, lower, upper, source_kind):
        return dict(key=key, kind='support', source_kind=source_kind, source_id=key,
                    revision=1, label=key, lower=lower, upper=upper,
                    baseline_date='2026-11-12', price_basis='none', evidence={})

    automatic = level('auto-wide-support', 10., 12., 'automatic_zone')
    manual = level('manual-new-support', 11., 11., 'manual_line')
    state = None
    stock = {'instrument_id': SYMBOL, 'name': 'fixture'}
    for slot, price, levels in (
        ('09:30', 11., [automatic]),
        ('09:40', 13., [automatic]),
        # The old automatic reclaim ends. A newly enabled manual line only
        # seeds below its price on this sample, and has not reclaimed yet.
        ('09:50', 10.5, [automatic, manual]),
        ('10:00', 11.5, [automatic, manual]),
    ):
        current = quote(price, slot=slot)
        evaluated = evaluate_snapshot(levels, current, state, trade_date=DAY, slot_key=slot)
        state = evaluated['state']
        identifier = 'monitor-ended-episode-' + slot.replace(':', '')
        batch(identifier, slot=slot)
        with core.connect() as connection:
            for observation in merge_observations(evaluated['observations']):
                monitor._persist_observation(connection, observation, identifier, stock, current,
                                             current_state=state)
    with core.connect() as connection:
        reclaims = connection.execute('''SELECT event_id,first_seen_at_ms FROM intraday_monitor_event
            WHERE type='support_reclaim' ORDER BY first_seen_at_ms''').fetchall()
        assert len(reclaims) == 2, 'the new manual reclaim must not reuse the ended automatic card'
        assert reclaims[0]['first_seen_at_ms'] == quote(13., slot='09:40')['received_at_ms']
        assert reclaims[1]['first_seen_at_ms'] == quote(11.5, slot='10:00')['received_at_ms']
        original = connection.execute('SELECT rule_key FROM intraday_monitor_evidence WHERE event_id=?',
                                      (reclaims[0]['event_id'],)).fetchall()
        assert {row[0] for row in original} == {'auto-wide-support'}
