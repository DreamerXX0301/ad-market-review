"""Independent, low-priority and bounded capture in the single market worker."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from bisect import bisect_left
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time as runtime

from app.core import connect
from app.infrastructure.database import EVENT_CALENDAR_INSTALL_DEFAULT
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
    days = _calendar_days(connection, day)
    return day in days if days else None


def _calendar_days(connection, day):
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
                return days
    return None


def closed_market_window(connection, day):
    days = _calendar_days(connection, day)
    if not days:
        return None
    index = bisect_left(days, day)
    if index == 0 or index >= len(days) or days[index] != day:
        return None
    previous, current = date.fromisoformat(days[index - 1]), date.fromisoformat(day)
    distance = (current - previous).days
    if distance < 2:
        return None
    if distance == 3 and previous.weekday() == 4 and current.weekday() == 0:
        return 'weekend', previous
    return 'holiday', previous


def _previous_increment_finished(connection, previous_day, cutoff):
    floor = datetime.combine(previous_day, time(15), CHINA)
    row = connection.execute("""SELECT j.finished_at_ms FROM dataset_snapshot s JOIN job j USING(job_id)
        WHERE s.module='daily_raw' AND s.state='published' AND s.data_end=?
        AND j.kind='increment' AND j.status IN ('succeeded','partial','no_change')
        AND j.finished_at_ms>=s.published_at_ms AND j.finished_at_ms>=? AND j.finished_at_ms<?
        ORDER BY j.finished_at_ms LIMIT 1""", (previous_day.isoformat(), int(floor.timestamp() * 1000),
                                               int(cutoff.timestamp() * 1000))).fetchone()
    return datetime.fromtimestamp(row[0] / 1000, CHINA) if row else None


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
        [sys.executable, '-B', '-c', 'from app.services.news_fetch import main; main()',
         kind, *arguments],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=CHILD_TIMEOUT_SECONDS,
        check=True, env=environment, **input_args,
    )
    return json.loads(result.stdout)


class NewsScheduler:
    def __init__(self, *, clock=local_now, collector=_collect):
        self.clock = clock
        self.collector = collector
        self.flash_enabled = os.environ.get('SHIXU_NEWS_CLS_LOCAL_AUTHORIZED') == '1'
        calendar_override = os.environ.get('SHIXU_EVENT_CALENDAR_ENABLED')
        self.calendar_enabled = calendar_override == '1' if calendar_override else None
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
        calendar_wanted = (slot is not None and self.calendar_enabled is not False
                           and ('calendar', _calendar_slot(slot)) not in self.attempts)
        if not (should_prune or flash_wanted or calendar_wanted):
            return
        with connect() as connection:
            if _active_auction(connection, day) or _busy(connection):
                return
            if calendar_wanted and self.calendar_enabled is None:
                # Resolve installation policy only in an eligible idle tick,
                # after the existing capture and auction protections.
                self.calendar_enabled = connection.execute(
                    "SELECT 1 FROM platform_setting WHERE key=? AND value_json='true'",
                    (EVENT_CALENDAR_INSTALL_DEFAULT,),
                ).fetchone() is not None
                calendar_wanted = self.calendar_enabled
            close_ready = slot == 'after_close' and _published_increment(connection, day)
            slot_ready = slot is not None and (slot != 'after_close' or close_ready)
            flash_possible = flash_wanted and slot_ready and (close_ready or observed.weekday() < 5)
            trade_day = (True if close_ready else _verified_trade_day(connection, day)) if flash_possible else False
            calendar_ready = calendar_wanted and slot_ready
            calendar_state = _receipt(connection, EVENT_CALENDAR_RECEIPT) if calendar_ready else {}
            previous_day = _previous_published_day(connection, day) if flash_possible else None
            verify_gap = (trade_day is True and previous_day
                          and (observed.date() - date.fromisoformat(previous_day)).days > 1
                          and _calendar_days(connection, day) is None)
            refresh_calendar = trade_day is None or verify_gap
            trade_state = _receipt(connection, TRADE_CALENDAR_RECEIPT) if refresh_calendar else {}
        if refresh_calendar:
            verified = self._refresh_trade_day(day, slot, trade_state)
            if trade_day is None:
                trade_day = verified
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
        group = group_cutoff = gap = start = None
        if not older or older['trade_date'] < day:
            with connect() as connection:
                closed = closed_market_window(connection, day)
                if closed:
                    group, previous = closed
                    group_cutoff = min(cutoff, datetime.combine(observed.date(), AUCTION_ADMISSION_END, CHINA))
                    start = _previous_increment_finished(connection, previous, group_cutoff)
        if start is None:
            if older:
                start = datetime.fromisoformat(older['cutoff_at'])
                if group:
                    gap = '上个交易日的盘后增量完成时间待核验，从最近留存消息的截止时间衔接'
            else:
                if group or previous_day:
                    prior = previous if group else date.fromisoformat(previous_day)
                    start = datetime.combine(prior, CLOSE_START, CHINA)
                else:
                    start = cutoff - timedelta(days=1)
                gap = ('上个交易日的盘后增量完成时间和消息衔接均待核验，暂从上个交易日 17:00 采集'
                       if group else '首次或中断后采集，本档以前没有可衔接的消息简报')
        if start < floor:
            start = floor
            gap = '采集范围最多七天，较早消息覆盖待核验'
        if group and start >= group_cutoff:
            group = group_cutoff = None
        if self._admit(day, slot) is None:
            return
        try:
            result = self.collector('flash', start.isoformat(), cutoff.isoformat())
        except (OSError, ValueError, subprocess.SubprocessError):
            result = {'status': 'unavailable', 'items': [], 'reason': '消息源暂未取得，下一档从最近成功档继续'}
        captured = self._admit(day, slot)
        if captured is None:
            return
        if gap:
            if result['status'] == 'complete':
                result['status'] = 'partial'
            result['reason'] = '；'.join(filter(None, (result.get('reason'), gap)))
        deadline = datetime.combine(observed.date(), ADMISSION_END[slot], CHINA)
        try:
            news_events.publish_snapshot(day, slot, cutoff_at=cutoff.isoformat(), window_start_at=start.isoformat(),
                                         window_group=group, group_cutoff_at=group_cutoff,
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
