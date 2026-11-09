"""Independent, low-priority and bounded capture in the single market worker."""
from __future__ import annotations

from datetime import datetime, time, timedelta
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time as runtime

from app.core import connect
from app.services.sync_clock import CHINA, local_now

AUCTION_START = time(9)
AUCTION_ADMISSION_END = time(9, 2)
PROTECTED_START = time(9, 23)
PROTECTED_END = time(9, 30)
NOON_START = time(12, 30)
NOON_ADMISSION_END = time(12, 35)
CLOSE_START = time(17)
CLOSE_ADMISSION_END = time(23, 59)
ADMISSION_END = {'auction': AUCTION_ADMISSION_END, 'noon': NOON_ADMISSION_END,
                 'after_close': CLOSE_ADMISSION_END}
CHILD_TIMEOUT_SECONDS = 12
CHECK_SECONDS = 15
TRADE_CALENDAR_RECEIPT = 'news-trading-calendar-v1'
TRADE_CALENDAR_KEYS = ('automatic-review-calendar-v1', 'intraday-automation-calendar-v1', TRADE_CALENDAR_RECEIPT)
EVENT_CALENDAR_RECEIPT = 'official-event-calendar-collection-v1'
LOGGER = logging.getLogger('shixu.worker')


def capture_slot(observed):
    clock = observed.time()
    if AUCTION_START <= clock < AUCTION_ADMISSION_END:
        return 'auction'
    if NOON_START <= clock < NOON_ADMISSION_END:
        return 'noon'
    if CLOSE_START <= clock < CLOSE_ADMISSION_END:
        return 'after_close'
    return None


def _calendar_slot(slot):
    return 'after_close' if slot == 'after_close' else 'before_close'


def _busy(connection):
    return connection.execute("SELECT 1 FROM job WHERE status IN ('queued','running') LIMIT 1").fetchone() is not None


def _active_auction(connection, day):
    return connection.execute("""SELECT 1 FROM market_auction_capture c JOIN job j USING(job_id)
        WHERE c.trade_date=? AND j.status IN ('queued','running')""", (day,)).fetchone() is not None


def _verified_trade_day(connection, day):
    # A same-day retained market snapshot was captured only after that
    # collector verified the trading calendar. No network or DuckDB fallback.
    captured = connection.execute("""SELECT 1 FROM market_auction_snapshot WHERE trade_date=?
        UNION ALL SELECT 1 FROM market_noon_snapshot WHERE trade_date=?
        UNION ALL SELECT 1 FROM market_intraday_capture WHERE trade_date=? AND snapshot_id IS NOT NULL
        LIMIT 1""", (day, day, day)).fetchone()
    if captured is not None:
        return True
    candidates = connection.execute("""SELECT value_json FROM platform_setting WHERE key IN (?,?,?)
        ORDER BY updated_at_ms DESC""", TRADE_CALENDAR_KEYS)
    for candidate in candidates:
        try:
            saved = json.loads(candidate[0])
        except (TypeError, ValueError):
            continue
        if (isinstance(saved, dict) and saved.get('state') == 'ready' and saved.get('source_id') == 'fuyao'
                and isinstance(saved.get('source_sha256'), str) and len(saved['source_sha256']) == 64
                and isinstance(saved.get('days'), list) and saved['days']
                and all(isinstance(value, str) for value in saved['days'])):
            days = saved['days']
            if days[0] <= day <= days[-1]:
                return day in days
    return None


def _published_increment(connection, day):
    return connection.execute("""SELECT 1 FROM dataset_snapshot s JOIN job j USING(job_id)
        WHERE s.module='daily_raw' AND s.state='published' AND s.data_end=? AND j.kind='increment'
        AND j.status IN ('succeeded','partial','no_change') LIMIT 1""", (day,)).fetchone() is not None


def _receipt(connection, key):
    row = connection.execute('SELECT value_json FROM platform_setting WHERE key=?', (key,)).fetchone()
    value = json.loads(row[0]) if row else {}
    return value if isinstance(value, dict) else {}


def _previous_published_day(connection, day):
    row = connection.execute("""SELECT data_end FROM dataset_snapshot
        WHERE module='daily_raw' AND state='published' AND data_end<?
        ORDER BY data_end DESC LIMIT 1""", (day,)).fetchone()
    return row[0] if row else None


def _collect(kind, *arguments):
    # An isolated source-only child puts an upper bound on DNS, TLS and slow
    # response bodies. subprocess.run reaps it before the market loop resumes.
    allowed = {'PATH', 'PYTHONPATH', 'SYSTEMROOT', 'TMPDIR', 'TEMP', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}
    environment = {name: value for name, value in os.environ.items() if name in allowed}
    input_args = {'stdin': subprocess.DEVNULL}
    if kind == 'trade_calendar':
        from app.services.source_service import client
        key = client('fuyao').key
        if not key:
            raise ValueError('没有启用可核验的交易日历来源')
        # Only the authorized official calendar child receives the key on an
        # anonymous pipe. The CLS and event calendar children receive no key.
        input_args = {'input': key.encode()}
    result = subprocess.run(
        [sys.executable, '-B', '-m', 'app.services.news_fetch', kind, *arguments],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=CHILD_TIMEOUT_SECONDS,
        check=True, env=environment, **input_args,
    )
    return json.loads(result.stdout)


class NewsScheduler:
    def __init__(self, *, clock=local_now, collector=_collect):
        self.clock = clock
        self.collector = collector
        self.flash_enabled = os.environ.get('SHIXU_NEWS_CLS_LOCAL_AUTHORIZED') == '1'
        self.calendar_enabled = os.environ.get('SHIXU_EVENT_CALENDAR_ENABLED') == '1'
        self.next_check = 0.0
        self.today = None
        self.attempts = set()
        self.pruned = False

    def tick(self):
        observed = self.clock()
        if PROTECTED_START <= observed.time() < PROTECTED_END:
            return
        elapsed = runtime.monotonic()
        if elapsed < self.next_check:
            return
        self.next_check = elapsed + CHECK_SECONDS
        day = observed.date().isoformat()
        if self.today != day:
            self.today, self.attempts, self.pruned = day, set(), False
        slot = capture_slot(observed)
        should_prune = observed.time() >= NOON_START and not self.pruned
        flash_wanted = slot is not None and self.flash_enabled and ('flash', slot) not in self.attempts
        calendar_wanted = slot is not None and self.calendar_enabled and ('calendar', _calendar_slot(slot)) not in self.attempts
        if not (should_prune or flash_wanted or calendar_wanted):
            return
        with connect() as connection:
            if _active_auction(connection, day) or _busy(connection):
                return
            close_ready = slot == 'after_close' and _published_increment(connection, day)
            slot_ready = slot is not None and (slot != 'after_close' or close_ready)
            flash_possible = flash_wanted and slot_ready and (close_ready or observed.weekday() < 5)
            trade_day = (True if close_ready else _verified_trade_day(connection, day)) if flash_possible else False
            trade_state = _receipt(connection, TRADE_CALENDAR_RECEIPT) if trade_day is None else {}
            calendar_ready = calendar_wanted and slot_ready
            calendar_state = _receipt(connection, EVENT_CALENDAR_RECEIPT) if calendar_ready else {}
            previous_day = _previous_published_day(connection, day) if flash_possible else None
        if trade_day is None:
            trade_day = self._refresh_trade_day(day, slot, trade_state)
        if trade_day:
            self._flash(day, slot, previous_day)
        if calendar_ready:
            self._calendar(day, slot, calendar_state)
        if should_prune:
            self._prune(day)

    def _admit(self, day, slot):
        observed = self.clock()
        if observed.date().isoformat() != day or capture_slot(observed) != slot:
            return None
        with connect() as connection:
            if _active_auction(connection, day) or _busy(connection):
                return None
        return observed

    def _refresh_trade_day(self, day, slot, previous):
        """One fallback per day, independent from all existing market schedules."""
        attempt = ('trade_calendar', day)
        if attempt in self.attempts or previous.get('date') == day or self._admit(day, slot) is None:
            return False
        self.attempts.add(attempt)
        try:
            result = self.collector('trade_calendar')
        except (OSError, ValueError, subprocess.SubprocessError):
            result = {'state': 'failed'}
        if self._admit(day, slot) is None:
            return False
        try:
            self._write_receipt(day, slot, TRADE_CALENDAR_RECEIPT, {**result, 'date': day})
        except sqlite3.Error as error:
            LOGGER.warning('消息独立交易日历未能留存 %s', type(error).__name__)
            return False
        return result['state'] == 'ready' and day in result['days']

    def _flash(self, day, slot, previous_day):
        from app.services import news_events
        attempt = ('flash', slot)
        if news_events.get_snapshot_meta(day, slot) is not None:
            self.attempts.add(attempt)
            return
        observed = self._admit(day, slot)
        if observed is None:
            return
        self.attempts.add(attempt)
        cutoff = observed.replace(microsecond=0)
        older = news_events.last_snapshot(before_at=cutoff.isoformat(), statuses=('complete', 'partial'))
        floor = cutoff - timedelta(days=7)
        if older:
            start = datetime.fromisoformat(older['cutoff_at'])
        elif previous_day:
            start = datetime.combine(datetime.fromisoformat(previous_day).date(), CLOSE_START, CHINA)
        else:
            start = cutoff - timedelta(days=1)
        gap = None
        if not older:
            gap = '首次或中断后采集，本档以前没有可衔接的消息简报'
        if start < floor:
            start = floor
            gap = '上个简报距离超过七天，较早消息覆盖待核验'
        if self._admit(day, slot) is None:
            return
        try:
            result = self.collector('flash', start.isoformat(), cutoff.isoformat())
        except (OSError, ValueError, subprocess.SubprocessError):
            result = {'status': 'unavailable', 'items': [], 'reason': '消息源暂未取得，下一档从最近成功档继续'}
        captured = self._admit(day, slot)
        if captured is None:
            return
        if gap and result['status'] == 'complete':
            result.update(status='partial', reason=gap)
        deadline = datetime.combine(observed.date(), ADMISSION_END[slot], CHINA)
        try:
            news_events.publish_snapshot(day, slot, cutoff_at=cutoff.isoformat(), window_start_at=start.isoformat(),
                                         captured_at=captured.isoformat(), latest_write_at=deadline.isoformat(), **result)
        except (sqlite3.Error, ValueError, TimeoutError) as error:
            LOGGER.warning('消息简报未能归档 %s %s', slot, type(error).__name__)

    def _calendar(self, day, slot, previous):
        key = _calendar_slot(slot)
        attempt = ('calendar', key)
        if previous.get('date') == day and (previous.get('ready') is True or previous.get(key) is True):
            self.attempts.add(attempt)
            return
        observed = self._admit(day, slot)
        if observed is None:
            return
        self.attempts.add(attempt)
        from app.services import news_events
        try:
            result = self.collector('calendar', day)
        except (OSError, ValueError, subprocess.SubprocessError):
            result = None
        if self._admit(day, slot) is None:
            return
        state = previous.copy() if previous.get('date') == day else {'date': day}
        state[key] = True
        deadline = datetime.combine(observed.date(), ADMISSION_END[slot], CHINA)
        try:
            if result is not None:
                news_events.publish_calendar(latest_write_at=deadline.isoformat(), **result)
                state['ready'] = True
            self._write_receipt(day, slot, EVENT_CALENDAR_RECEIPT, state)
        except (sqlite3.Error, ValueError, TimeoutError) as error:
            LOGGER.warning('官方事件日历未能归档 %s', type(error).__name__)

    def _write_receipt(self, day, slot, key, state):
        if self._admit(day, slot) is None:
            return
        with connect() as connection:
            connection.execute('PRAGMA busy_timeout=250')
            connection.execute('BEGIN IMMEDIATE')
            observed = self.clock()
            if observed.date().isoformat() != day or capture_slot(observed) != slot:
                return
            connection.execute("""INSERT INTO platform_setting(key,value_json,updated_at_ms) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at_ms=excluded.updated_at_ms""",
                (key, json.dumps(state), int(observed.timestamp() * 1000)))

    def _prune(self, day):
        observed = self.clock()
        if observed.date().isoformat() != day or observed.time() < NOON_START:
            return
        from app.services.news_events import prune_snapshots
        try:
            prune_snapshots(day)
            self.pruned = True
        except (sqlite3.Error, ValueError) as error:
            LOGGER.warning('消息简报到期清理稍后重试 %s', type(error).__name__)
