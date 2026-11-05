"""Worker-owned snapshots for the six scheduled intraday observations."""
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import json
import re

from fastapi import HTTPException

from app.adapters.providers import ProviderError
from app.core import connect, data_dir, digest, dumps, get_market, new_id, published_dir
from app.services import task_service as tasks
from app.services.increment_access import require_business_unlocked
from app.services.intraday_discovery import SECTOR_LISTS, build_intraday_discovery
from app.services.intraday_feedback import attach_feedback_themes, feedback_stock_rows
from app.services.noon_analysis import INDEX_NAMES, normalize_pool, normalize_quotes, number
from app.services.noon_collection import (CHINA, WindowExpired, canonical, contains_secret,
    STOCK_CODE, explicit_empty_pool_page, items, paged, source_time, trading_days)
from app.services.noon_service import _configured, _guard_job
from app.services.sector_history import _catalog
from app.services.stock_limit_reference import attach_intraday_references

OPERATION = 'intraday_snapshot'
LEGACY_VERSION = 'intraday-snapshot-v1'
STRENGTH_VERSION = 'intraday-snapshot-v2'
FEEDBACK_VERSION = 'intraday-snapshot-v3'
POOL_VERSION = 'intraday-snapshot-v4'
VERSION = 'intraday-snapshot-v5'
MAX_BOARDS = 800
REQUEST_BUDGETS = {LEGACY_VERSION: 25, STRENGTH_VERSION: 27, FEEDBACK_VERSION: 28, POOL_VERSION: 30, VERSION: 30}
MAX_PAYLOAD = 2 * 1024 * 1024
AUTOMATION_KEY = 'intraday-automation-v1'
AUTOMATION_CALENDAR_KEY = 'intraday-automation-calendar-v1'
LEGACY_SLOTS = tuple(dict(key=key, label=label, start=start, end=end) for key, label, start, end in (
    ('opening', '开盘观察', '09:35', '10:00'),
    ('morning', '上午观察', '10:00', '11:30'),
    ('afternoon', '午后观察', '13:00', '14:30'),
    ('late', '尾盘观察', '14:30', '14:57'),
))
SLOTS = tuple(dict(key=key, label=label, start=start, end=end, scheduled_at=start, create_until=create_until)
              for key, label, start, create_until, end in (
    ('1000', '开盘首轮', '10:00', '10:01', '10:03'),
    ('1030', '早盘一小时', '10:30', '10:31', '10:33'),
    ('1100', '上午观察', '11:00', '11:01', '11:03'),
    ('1330', '午后首轮', '13:30', '13:31', '13:33'),
    ('1400', '午后观察', '14:00', '14:01', '14:03'),
    ('1430', '尾盘观察', '14:30', '14:31', '14:33'),
))
V5_BASELINES = {'1030': '1000', '1100': '1030', '1330': 'noon', '1400': '1330', '1430': '1400'}


def now_china():
    return datetime.now(CHINA)


def day_value(value):
    try:
        if isinstance(value, str) and date.fromisoformat(value).isoformat() == value:
            return value
    except ValueError:
        pass
    raise HTTPException(422, '盘中观察日期无效')


def slot_for(day, now, *, version=VERSION, creation=False):
    now = now.astimezone(CHINA)
    if day != now.date().isoformat() or now.weekday() >= 5:
        return None
    clock = now.strftime('%H:%M')
    slots = SLOTS if version == VERSION else LEGACY_SLOTS
    return next((slot for slot in slots if slot['start'] <= clock <
                 (slot.get('create_until', slot['end']) if creation else slot['end'])), None)


def _automation_config(connection):
    row = connection.execute('SELECT value_json FROM platform_setting WHERE key=?', (AUTOMATION_KEY,)).fetchone()
    if not row:
        return {'enabled': False, 'revision': 0}
    value = json.loads(row['value_json'])
    if (not isinstance(value, dict) or type(value.get('enabled')) is not bool
            or type(value.get('revision')) is not int or value['revision'] < 0):
        raise ValueError('盘中自动计划配置无效')
    return value


def read_automation():
    with connect() as connection:
        return {**_automation_config(connection), 'slots': SLOTS}


def configure_automation(payload):
    if (set(payload) != {'enabled', 'expected_revision'} or type(payload['enabled']) is not bool
            or type(payload['expected_revision']) is not int or payload['expected_revision'] < 0):
        raise HTTPException(422, '只接受盘中自动计划的启用状态和配置版本')
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        require_business_unlocked(connection)
        current = _automation_config(connection)
        if payload['expected_revision'] != current['revision']:
            raise HTTPException(409, '盘中自动计划已经变更，请重新读取配置')
        if payload['enabled'] != current['enabled']:
            current = {'enabled': payload['enabled'], 'revision': current['revision'] + 1}
            connection.execute('''INSERT INTO platform_setting(key,value_json,updated_at_ms) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at_ms=excluded.updated_at_ms''',
                (AUTOMATION_KEY, dumps(current), int(now_china().timestamp() * 1000)))
    return {**current, 'slots': SLOTS}


def decode_snapshot(row):
    if not row['snapshot_id']:
        return None
    raw = row['payload_json'].encode()
    if len(raw) > MAX_PAYLOAD or sha256(raw).hexdigest() != row['content_sha256']:
        raise HTTPException(409, '盘中快照校验失败')
    value = json.loads(raw)
    if (value.get('version') not in tuple(REQUEST_BUDGETS)
            or (value.get('id'), value.get('date'), value.get('slot')) != (
                row['snapshot_id'], row['trade_date'], row['slot'])):
        raise HTTPException(409, '盘中快照身份校验失败')
    return value


def _noon_schedule(connection, day, today):
    from app.services import automatic_tasks

    receipts = connection.execute("""SELECT e.details_json FROM market_noon_capture c
        JOIN job_event e USING(job_id)
        WHERE c.trade_date=? AND c.request_key=? AND e.stage='automatic_schedule' LIMIT 2""",
        (day, automatic_tasks.request_key('noon', day))).fetchall()
    if len(receipts) == 1:
        try:
            receipt = json.loads(receipts[0]['details_json'])
            clock = receipt.get('scheduled_time')
            if (receipt.get('version') == automatic_tasks.VERSION and receipt.get('schedule') == 'noon'
                    and receipt.get('date') == day and receipt.get('timezone') == 'Asia/Shanghai'
                    and isinstance(clock, str) and re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', clock)
                    and '11:31' <= clock < '13:00'):
                return clock, 'recorded'
        except (ValueError, TypeError, AttributeError):
            pass
    if day == today:
        try:
            noon = automatic_tasks._settings(connection)['noon']
            if noon['enabled']:
                return noon['time'], 'configuration'
        except (HTTPException, ValueError, TypeError, AttributeError):
            pass
    return None, None


def read_view(day=None):
    now = now_china()
    today = now.date().isoformat()
    day = day_value(day) if day is not None else today
    slot = slot_for(day, now, creation=True)
    with connect() as conn:
        rows = conn.execute('''SELECT c.*,j.status,j.created_at_ms FROM market_intraday_capture c
            JOIN job j USING(job_id) WHERE c.trade_date=? ORDER BY j.created_at_ms''', (day,)).fetchall()
        configured = bool(slot and _configured(conn) and tasks.initialization_state(conn)['main_published'])
        busy = configured and conn.execute("SELECT 1 FROM job WHERE status IN ('queued','running') LIMIT 1").fetchone()
        automation = _automation_config(conn)
        noon_scheduled_at, noon_schedule_source = _noon_schedule(conn, day, today)
    latest = rows[-1] if rows else None
    occupied = slot and any(row['slot'] == slot['key'] for row in rows)
    reason = ('仅在今天的交易观察时段开放采集' if not slot else
              '本时段已提交采集' if occupied else
              '请先初始化行情并配置扶摇数据源' if not configured else
              '已有任务运行或排队，请等工作进程空闲' if busy else None)
    saved = [value for row in rows if (value := decode_snapshot(row))]
    _restore_legacy_turnover(saved)
    _attach_saved_stock_references(saved)
    snapshots = [{k: v for k, v in value.items()
                  if k not in ('version', 'comparison_input', 'source_checks', 'sealed_pool_input',
                               'member_reference')} | {'discovery': build_intraday_discovery(value)}
                 for value in saved]
    from app.services.intraday_brief import sector_comparison_metadata
    for snapshot in snapshots:
        for key in ('sectors', 'strong_sectors', 'limit_up_sectors', 'wide_limit_up_sectors', 'index_only_sectors'):
            if isinstance(snapshot.get(key), list):
                snapshot[key] = [{**row, **sector_comparison_metadata(row, source='fuyao')}
                                 for row in snapshot[key]]
    retained_slots = {row['slot'] for row in rows}
    return dict(date=day, today=today, state=latest['status'] if latest and latest['status'] in ('queued', 'running')
                else 'available' if snapshots else latest['status'] if latest else 'not_collected',
                can_capture=bool(slot and configured and not occupied and not busy), reason=reason,
                latest_job=tasks.task_view(latest['job_id']) if latest else None,
                noon_scheduled_at=noon_scheduled_at, noon_schedule_source=noon_schedule_source,
                current_slot=slot, slots=(*SLOTS, *(slot for slot in LEGACY_SLOTS if slot['key'] in retained_slots)),
                snapshots=snapshots, automation=automation)


def _enqueue(connection, key, day, slot, now, *, version=VERSION, authorization=None):
    from app.services.market_suspensions import VERSION as SUSPENSION_VERSION

    identifier = new_id('job')
    parameters = dict(operation=OPERATION, version=version, date=day, slot=slot['key'], source_id='fuyao')
    if version == VERSION:
        parameters['suspension_evidence'] = SUSPENSION_VERSION
    if authorization:
        parameters['automation'] = authorization
    signature = digest(parameters)
    connection.execute('''INSERT INTO job(job_id,request_key,logical_key,kind,target_date,parameters_json,
        parameters_hash,requested_by,status,planned_batches,created_at_ms)
        VALUES(?,?,?,'source_check',?,?,?,?,'queued',?,?)''',
        (identifier, key, signature, day, dumps(parameters), signature, 'automatic' if authorization else 'manual',
         100, int(now.timestamp() * 1000)))
    connection.execute('INSERT INTO market_intraday_capture(trade_date,slot,job_id) VALUES(?,?,?)',
                       (day, slot['key'], identifier))
    tasks.append_event(connection, identifier, 'queued',
                       '盘中自动观察已提交' if authorization else '盘中观察已提交，等待唯一工作进程')
    return identifier


def create_capture(payload):
    if set(payload) - {'date', 'request_key'}:
        raise HTTPException(422, '盘中采集只接受日期与请求标识')
    now = now_china()
    day = day_value(payload.get('date', now.date().isoformat()))
    key = payload.get('request_key', new_id('intraday-request'))
    if not isinstance(key, str) or not 1 <= len(key) <= 200:
        raise HTTPException(422, '请求标识须为1至200字符')
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        require_business_unlocked(conn)
        prior = conn.execute('SELECT job_id,target_date,parameters_json FROM job WHERE request_key=?', (key,)).fetchone()
        if prior:
            if prior['target_date'] != day or json.loads(prior['parameters_json']).get('operation') != OPERATION:
                raise HTTPException(409, '请求标识已用于其他任务')
            identifier = prior['job_id']
        else:
            now = now_china()
            slot = slot_for(day, now, creation=True)
            if not slot:
                raise HTTPException(409, '当前不是盘中观察采集窗口')
            if conn.execute('SELECT 1 FROM market_intraday_capture WHERE trade_date=? AND slot=?',
                            (day, slot['key'])).fetchone():
                raise HTTPException(409, '本时段已提交过盘中快照')
            if not tasks.initialization_state(conn)['main_published'] or not _configured(conn):
                raise HTTPException(409, '请先初始化行情并配置扶摇数据源')
            if conn.execute("SELECT 1 FROM job WHERE status IN ('queued','running') LIMIT 1").fetchone():
                raise HTTPException(409, '已有任务运行或排队，请等工作进程空闲')
            identifier = _enqueue(conn, key, day, slot, now)
    return tasks.task_view(identifier)


def _eligible_for_automatic(connection, day, slot):
    from .sync_progress import lock_record

    return (not lock_record(connection)
            and not connection.execute("SELECT 1 FROM job WHERE status IN ('queued','running') LIMIT 1").fetchone()
            and not connection.execute('SELECT 1 FROM market_intraday_capture WHERE trade_date=? AND slot=?',
                                       (day, slot['key'])).fetchone()
            and _configured(connection) and tasks.initialization_state(connection)['main_published'])


def tick_automatic():
    """Called only by the exclusively locked worker after existing schedules."""
    from .automatic_tasks import _calendar, _json

    observed = now_china()
    day = observed.date().isoformat()
    slot = slot_for(day, observed, creation=True)
    if not slot:
        return None
    with connect() as connection:
        config = _automation_config(connection)
        if not config['enabled'] or not _eligible_for_automatic(connection, day, slot):
            return None
    _calendar('intraday', day, observed, setting_key=AUTOMATION_CALENDAR_KEY)
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        now = now_china()
        if (slot != slot_for(day, now, creation=True) or _automation_config(connection) != config
                or not _eligible_for_automatic(connection, day, slot)):
            return None
        calendar = _json(connection, AUTOMATION_CALENDAR_KEY, {})
        if (calendar.get('date') != day or calendar.get('source_id') != 'fuyao'
                or calendar.get('state') != 'ready' or day not in calendar.get('days', ())):
            return None
        proof = calendar.get('source_sha256')
        if not isinstance(proof, str) or not re.fullmatch(r'[a-f0-9]{64}', proof):
            return None
        key = f'intraday-auto:{day}:{slot["key"]}'
        if connection.execute('SELECT 1 FROM job WHERE request_key=?', (key,)).fetchone():
            return None
        return _enqueue(connection, key, day, slot, now,
                        authorization={'revision': config['revision'], 'calendar_sha256': proof})


class Capture:
    def __init__(self, job, day, slot, adapter, progress, max_requests):
        self.job, self.day, self.slot, self.adapter, self.progress = job, day, slot, adapter, progress
        self.checks, self.gaps = [], []
        self.start = self.end = now_china()
        self.attempts = 0
        self.max_requests = max_requests
        self.version = json.loads(job.get('parameters_json', '{}')).get('version')

    def guard_window(self):
        _guard_job(self.job)
        slot = slot_for(self.day, now_china(), version=self.version)
        if slot != self.slot:
            raise WindowExpired('原盘中观察窗口已结束，未使用其他时段行情')

    def gap(self, name, reason):
        if len(self.gaps) < 30:
            self.gaps.append(dict(domain=name, reason=reason))

    def pool(self, name, endpoint):
        def verified_empty(body):
            if not explicit_empty_pool_page(body):
                return False
            source = next((row for row in self.checks if row['domain'] == f'{name}_page_1'), {})
            stamp = source.get('source_time')
            return (source.get('state') == 'accepted' and isinstance(stamp, str)
                    and slot_for(self.day, datetime.fromisoformat(stamp), version=self.version) == self.slot)

        midnight = int(datetime.combine(date.fromisoformat(self.day), time(), CHINA).timestamp() * 1000)
        return paged(self, name, endpoint, {'date_ms': midnight},
                     verify_empty=verified_empty if self.version == VERSION else None)

    def request(self, name, endpoint, params=None, *, dated=False):
        self.guard_window()
        prefix, _, page = name.rpartition('_')
        if (len(self.checks) >= self.max_requests
                or prefix == 'stocks_page' and int(page) > 8
                or prefix in ('limit_up_page', 'limit_break_page') and int(page) > 2):
            self.gap(name, '达到本时段请求预算')
            return None
        check = dict(domain=name, state='unknown')
        self.checks.append(check)

        def before_attempt():
            self.guard_window()
            if self.attempts >= self.max_requests + 5:
                raise WindowExpired('超过盘中实际请求预算')
            self.attempts += 1

        try:
            body = self.adapter.request(endpoint, params or {}, before_attempt=before_attempt)
            self.guard_window()
            received = now_china()
            if not isinstance(body, dict) or not isinstance(body.get('data'), dict):
                raise ValueError('响应结构无效')
            if contains_secret(body, getattr(self.adapter, 'key', None)):
                raise ValueError('响应疑似回显凭据')
            content = canonical(body)
            if len(content) > 4 * 1024 * 1024:
                raise ValueError('响应超出容量预算')
            stamp = source_time(body['data'].get('timestamp'), self.day, received)
            if dated:
                if stamp['state'] != 'observed':
                    self.gap(name, '响应总时间未能核验为当日，数据未入榜')
                    return None
                if self.version == VERSION:
                    anchor = datetime.combine(date.fromisoformat(self.day),
                                              time.fromisoformat(self.slot['scheduled_at']), CHINA)
                    if datetime.fromisoformat(stamp['time']) < anchor - timedelta(seconds=60):
                        self.gap(name, '响应总时间早于当前观察时点，数据未入榜')
                        return None
            check.update(state='accepted', response_sha256=sha256(content).hexdigest(),
                         source_time=stamp.get('time'), received_at=received.isoformat())
            self.end = received
            if self.progress:
                self.progress('读取盘中行情', min(90, 5 + len(self.checks) * 3))
            return body
        except (ProviderError, ValueError) as error:
            if isinstance(error, WindowExpired):
                raise
            self.gap(name, '来源请求或响应核验失败')
            return None


def _memberships(reference, catalog, day):
    candidates, seen, duplicates = {}, set(), set()
    for topic in reference.get('topics') or []:
        if not isinstance(topic, dict):
            continue
        code = topic.get('topic_code')
        if (not isinstance(code, str) or code not in catalog or topic.get('source') != 'fuyao'
                or topic.get('kind') != catalog[code]['kind']):
            continue
        if code in seen:
            duplicates.add(code)
            continue
        seen.add(code)
        observed, members = topic.get('observed_date'), topic.get('instrument_ids')
        try:
            if day_value(observed) > day:
                continue
        except HTTPException:
            continue
        if not isinstance(members, list):
            continue
        valid = sorted({member for member in members if isinstance(member, str) and STOCK_CODE.fullmatch(member)})
        if valid:
            candidates[code] = {'instrument_ids': valid, 'date': observed,
                                'complete': topic.get('members_complete') is True and len(valid) == len(members)}
    return {code: value for code, value in candidates.items() if code not in duplicates}


def _planned_board_codes(catalog):
    return sorted(catalog, key=lambda code: (catalog[code]['kind'] != 'industry', code))[:MAX_BOARDS]


def _prior_closing_cohort(market, calendar, day, *, include_broken=False):
    """Read only exact previous-session events from a verified local publication."""
    from app.services.noon_context import _event_cohorts
    from app.services.noon_prior_events import GENERATION, _prices, _reference

    prior = max((value for value in calendar if value < day), default=None)
    result = {'date': prior, 'complete': False, 'items': []}
    if include_broken:
        result['broken'] = dict(result)
    if not prior:
        return result
    try:
        generation = market.status().get('snapshot_id')
        if not isinstance(generation, str) or not GENERATION.fullmatch(generation):
            return result
        root = published_dir().resolve()
        reference, record, summary, reference_sha = _reference(root, generation, prior)
        keys = ('limitup', 'limitdown', 'breakpool') if include_broken else ('limitup', 'limitdown')
        symbols = {row.get('thscode') or row.get('instrument_id') for key in keys
                   for row in record.get(key, ()) if isinstance(row, dict)
                   and isinstance(row.get('thscode') or row.get('instrument_id'), str)}
        prices, price_sha = _prices(root, generation, summary, prior,
                                    {code for code in symbols if STOCK_CODE.fullmatch(code)})
        pools = _event_cohorts({**reference, '_supplement': {}}, prior, prices)
        coverage = record.get('pool_coverage' if record is reference else 'coverage')
        conflicts = set()
        if include_broken:
            conflicts = ({row['instrument_id'] for row in pools['limit_up']['items']}
                         & {row['instrument_id'] for row in pools['limit_break']['items']})
        pairs = [('limitup', 'limit_up', result)]
        if include_broken:
            pairs.append(('breakpool', 'limit_break', result['broken']))
        for field, key, cohort in pairs:
            pool = pools[key]
            proof = coverage.get(field) if isinstance(coverage, dict) else None
            cohort_items = [row for row in pool['items'] if row['instrument_id'] not in conflicts]
            complete = (isinstance(proof, dict) and proof.get('complete') is True
                        and type(proof.get('declared_total')) is int
                        and proof['declared_total'] == pool['source_count'] == len(record.get(field, ()))
                        and proof.get('state') in (None, 'complete', 'observed', 'ready')
                        and proof.get('quarantined_rows') in (0, None) and pool['unverified_count'] == 0
                        and len(cohort_items) == len(pool['items']))
            cohort.update(complete=complete, items=cohort_items)
        result['source'] = {'generation': generation, 'reference_sha256': reference_sha, 'price_sha256': price_sha}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return result


def _noon_baseline(connection, day):
    row = connection.execute('''SELECT snapshot_id,collected_end_at,content_sha256,payload_json
        FROM market_noon_snapshot WHERE trade_date=?''', (day,)).fetchone()
    if not row or sha256(row['payload_json'].encode()).hexdigest() != row['content_sha256']:
        return None
    value = json.loads(row['payload_json'])
    if value.get('date') != day or value.get('version') != 'noon-snapshot-v1':
        return None
    pool = value.get('events', {}).get('limit_up', {})
    return dict(id=row['snapshot_id'], slot='noon', slot_label='午评', collected_end_at=row['collected_end_at'],
                comparison_input={key: value.get(key, ()) for key in ('stocks', 'sectors', 'indices')},
                sealed_pool_input={'state': pool.get('state'), 'items': pool.get('items', ())})


def _saved_slot(connection, day, key):
    if key == 'noon':
        return _noon_baseline(connection, day)
    row = connection.execute('''SELECT * FROM market_intraday_capture
        WHERE trade_date=? AND slot=? AND snapshot_id IS NOT NULL''', (day, key)).fetchone()
    return decode_snapshot(row) if row else None


def _optional_turnover_slot(connection, day, key):
    try:
        return _saved_slot(connection, day, key)
    except (HTTPException, ValueError, TypeError):
        return None


def _sample_amount_deltas(stocks, baseline, domain='stocks'):
    if not baseline:
        return {}
    prior = {row['instrument_id']: row for row in baseline['comparison_input'].get(domain, ())}
    deltas = {}
    for row in stocks:
        before = prior.get(row['instrument_id'])
        if (not before or row.get('currency') != 'CNY' or row.get('price_basis') != 'none'
                or any(row.get(key) != before.get(key) for key in ('currency', 'price_basis', 'prev_close'))
                or not number(row.get('latest_price'), positive=True)
                or not number(before.get('latest_price'), positive=True)
                or not number(row.get('prev_close'), positive=True)):
            continue
        start = number(before.get('amount'), nonnegative=True)
        end = number(row.get('amount'), nonnegative=True)
        if start is not None and end is not None and end >= start:
            deltas[row['instrument_id']] = end - start
    return deltas


def _amount_comparison(current, earlier, prior_day):
    common = current.keys() & earlier.keys()
    comparable_current = sum(current[key] for key in common) if common else None
    comparable_previous = sum(earlier[key] for key in common) if common else None
    return dict(previous_amount=comparable_previous, comparison_amount=comparable_current,
                change_from_previous=comparable_current - comparable_previous if common else None,
                previous_date=prior_day, comparable_count=len(common))


def _interval_amount(stocks, baseline, prior_stage, prior_baseline, prior_day, domain='stocks'):
    current = _sample_amount_deltas(stocks, baseline, domain)
    earlier = _sample_amount_deltas(
        prior_stage['comparison_input'].get(domain, ()), prior_baseline, domain) if prior_stage else {}
    comparison = _amount_comparison(current, earlier, prior_day)
    return {**comparison, 'state': 'observed' if current else 'unknown',
            'amount': sum(current.values()) if current else None,
            'comparable_count': len(current), 'previous_comparable_count': comparison['comparable_count'],
            'scope': '累计成交额差值，前一交易日仅比较两个交易日四个端点均有可核验报价的同一批证券'}


def _cumulative_amounts(rows):
    return {row['instrument_id']: value for row in rows
            if row.get('currency') == 'CNY' and row.get('price_basis') == 'none'
            and number(row.get('latest_price'), positive=True) is not None
            and (value := number(row.get('amount'), nonnegative=True)) is not None}


def _attach_market_amount(pulse, current, earlier, prior_day):
    pulse['turnover']['amount_comparison'] = _amount_comparison(
        _cumulative_amounts(current), _cumulative_amounts(earlier), prior_day)


def _attach_saved_stock_references(snapshots):
    from app.analytics.market import MarketData
    from app.services.intraday_brief import sector_membership_fingerprint
    from app.services.noon_membership_context import GENERATION, _day

    references, classifications, member_fingerprints = {}, {}, {}
    for snapshot in snapshots:
        identity = snapshot.get('member_reference') or {}
        generation, end = identity.get('generation'), identity.get('date')
        if (not GENERATION.fullmatch(str(generation)) or not _day(end) or end >= snapshot['date']):
            continue
        source = (generation, end)
        if source not in references:
            try:
                market = get_market()
                if market.status().get('snapshot_id') != generation:
                    market = MarketData(data_dir(), snapshot_id=generation)
                status = market.status()
                references[source] = (market.reference()
                                      if (status.get('snapshot_id'), status.get('data_end')) == source else {})
            except (OSError, ValueError, KeyError, TypeError):
                references[source] = {}
        reference = references[source]
        displayed = [*snapshot.get('stocks', ()), *snapshot.get('candidates', ())]
        displayed.extend(row for key in SECTOR_LISTS for board in snapshot.get(key, ())
                         for row in board.get('candidates', ()))
        displayed.extend(row for board in snapshot.get('board_stocks', {}).values() for row in board.get('items', ()))
        attach_intraday_references(snapshot, displayed, reference.get('master', {}), reference.get('calendar', ()))
        pending = [row for row in feedback_stock_rows(snapshot) if 'themes' not in row]
        boards = [row for key in SECTOR_LISTS for row in snapshot.get(key, ())]
        if (pending or boards) and reference:
            key = (generation, snapshot['date'])
            if key not in classifications:
                catalog, _, _, _ = _catalog(reference)
                codes = _planned_board_codes(catalog)
                members = _memberships(reference, {code: catalog[code] for code in codes}, snapshot['date'])
                sectors = [{'instrument_id': code, **catalog[code]} for code in members]
                classifications[key] = (sectors, members)
            attach_feedback_themes(pending, *classifications[key])
            sectors, members = classifications[key]
            kinds = {row['instrument_id']: row['kind'] for row in sectors}
            for board in boards:
                code = board.get('instrument_id')
                proof = members.get(code, {})
                symbols = proof.get('instrument_ids', [])
                if (not symbols or proof.get('complete') is not True
                        or board.get('kind') != kinds.get(code)
                        or board.get('member_date') != proof.get('date')
                        or board.get('member_list_complete') is not True
                        or type(board.get('member_count')) is not int or board['member_count'] != len(symbols)):
                    continue
                fingerprint_key = (generation, end, snapshot['date'], code)
                if fingerprint_key not in member_fingerprints:
                    member_fingerprints[fingerprint_key] = sector_membership_fingerprint(
                        generation, end, code, kinds[code], proof['date'], symbols)
                board['member_reference_sha256'] = member_fingerprints[fingerprint_key]


def _restore_legacy_turnover(snapshots):
    """Use the capture's calendar and exact slot to compare retained stock samples only."""
    old = []
    for snapshot in snapshots:
        if (snapshot['version'] != VERSION
                or 'amount_comparison' in snapshot.get('market_pulse', {}).get('turnover', {})):
            continue
        prior = snapshot.get('relay_feedback', {}).get('prior_session', {}).get('date')
        if isinstance(prior, str) and prior < snapshot['date'] and snapshot.get('market_pulse', {}).get('turnover'):
            old.append((prior, snapshot))
    if old:
        with connect() as connection:
            for prior, snapshot in old:
                earlier = _optional_turnover_slot(connection, prior, snapshot['slot']) or {}
                _attach_market_amount(snapshot['market_pulse'],
                                      snapshot['comparison_input']['stocks'],
                                      earlier.get('comparison_input', {}).get('stocks', ()), prior)


def _attach_turnover(snapshot, previous, prior_stage, prior_baseline, prior_day):
    current = snapshot['comparison_input']
    earlier = prior_stage['comparison_input'] if prior_stage else {}
    market = snapshot['market_pulse']
    _attach_market_amount(market, current['stocks'], earlier.get('stocks', ()), prior_day)
    indices = _cumulative_amounts(current['indices'])
    prior_indices = _cumulative_amounts(earlier.get('indices', ()))
    for row in market['indices']:
        code = row['instrument_id']
        row['amount'] = indices.get(code)
        row['amount_comparison'] = _amount_comparison(
            {code: indices[code]} if code in indices else {},
            {code: prior_indices[code]} if code in prior_indices else {}, prior_day)
        if previous is not None:
            stage_rows = [entry for entry in current['indices'] if entry['instrument_id'] == code]
            row['interval_amount'] = _interval_amount(stage_rows, previous, prior_stage, prior_baseline, prior_day,
                                                       domain='indices')


def execute_capture(job, progress=None):
    from app.services.intraday_analysis import build_intraday_rankings, build_intraday_strength, split_recent_listings
    from app.services.market_suspensions import VERSION as SUSPENSION_VERSION
    from app.services.source_service import client

    params = json.loads(job['parameters_json'])
    day = day_value(job['target_date'])
    version = params.get('version')
    slots = SLOTS if version == VERSION else LEGACY_SLOTS
    slot = next((value for value in slots if value['key'] == params.get('slot')), None)
    authorization = params.get('automation')
    expected = dict(operation=OPERATION, version=version, date=day, slot=params.get('slot'), source_id='fuyao')
    if version == VERSION and params.get('suspension_evidence') == SUSPENSION_VERSION:
        expected['suspension_evidence'] = SUSPENSION_VERSION
    if authorization is not None:
        if (version != VERSION or not isinstance(authorization, dict)
                or set(authorization) != {'revision', 'calendar_sha256'}
                or type(authorization['revision']) is not int or authorization['revision'] < 1
                or not isinstance(authorization['calendar_sha256'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', authorization['calendar_sha256'])):
            raise ValueError('盘中自动计划授权无效')
        expected['automation'] = authorization
    if not slot or version not in tuple(REQUEST_BUDGETS) or params != expected:
        raise ValueError('盘中任务冻结参数无效')
    has_feedback = version in (FEEDBACK_VERSION, POOL_VERSION, VERSION)
    has_break_pool = version in (POOL_VERSION, VERSION)
    baseline_slot = V5_BASELINES.get(slot['key'])
    calendar_receipt = None
    with connect() as conn:
        ledger = conn.execute('SELECT * FROM market_intraday_capture WHERE job_id=?', (job['job_id'],)).fetchone()
        if not ledger or ledger['trade_date'] != day or ledger['slot'] != slot['key']:
            raise ValueError('盘中任务与采集台账不一致')
        if ledger['snapshot_id']:
            return {'snapshot_id': ledger['snapshot_id'], 'quality': decode_snapshot(ledger)['quality'],
                    'message': '盘中快照已留存'}
        if version == VERSION:
            previous = _saved_slot(conn, day, baseline_slot) if baseline_slot else None
        else:
            prior = conn.execute('''SELECT * FROM market_intraday_capture
                WHERE trade_date=? AND snapshot_id IS NOT NULL AND slot IN ('opening','morning','afternoon','late')''',
                                 (day,)).fetchall()
            previous = max((decode_snapshot(row) for row in prior if row['slot'] != slot['key']),
                           key=lambda item: item['collected_end_at'], default=None)
        if authorization is not None:
            from .automatic_tasks import _json

            config = _automation_config(conn)
            if not config['enabled'] or config['revision'] != authorization['revision']:
                raise ValueError('盘中自动计划已变更，未发起采集')
            calendar_receipt = _json(conn, AUTOMATION_CALENDAR_KEY, {})
            if (calendar_receipt.get('date') != day or calendar_receipt.get('state') != 'ready'
                    or calendar_receipt.get('source_id') != 'fuyao'
                    or calendar_receipt.get('source_sha256') != authorization['calendar_sha256']):
                raise ValueError('盘中自动计划交易日历凭证不可核验')
    market = get_market()
    reference = market.reference()
    catalog, _, _, _ = _catalog(reference)
    codes = _planned_board_codes(catalog)
    capture = Capture(job, day, slot, client('fuyao'), progress, REQUEST_BUDGETS[version])
    capture.guard_window()
    if calendar_receipt:
        calendar = calendar_receipt.get('days', ())
        capture.checks.append(dict(domain='calendar', state='accepted', source_id='fuyao',
                                   response_sha256=calendar_receipt['source_sha256']))
    else:
        calendar = trading_days(capture.request('calendar', '/api/a-share/calendar/trading-days'))
    if day not in calendar:
        raise ValueError('未核验为交易日，未采集盘中报价')
    raw_stocks, declared, complete = paged(capture, 'stocks', '/api/a-share/prices/snapshot', market=True)
    pool_inputs = None
    break_inputs = ([], None, False) if has_break_pool else None
    if version != LEGACY_VERSION:
        pool_inputs = capture.pool('limit_up', '/api/a-share/special-data/limit-up-pool')
    raw_indices, raw_boards = [], []
    try:
        if has_feedback:
            response = capture.request('indices', '/api/a-share-index/prices/snapshot',
                                       {'thscodes': ','.join(INDEX_NAMES)}, dated=True)
            raw_indices = [row for row in (items(response) or []) if isinstance(row, dict)
                           and isinstance(row.get('thscode'), str) and row['thscode'] in INDEX_NAMES]
        if has_break_pool:
            break_inputs = capture.pool('limit_break', '/api/a-share/special-data/limit-break-pool')
        for offset in range(0, len(codes), 50):
            batch = codes[offset:offset + 50]
            response = capture.request(f'boards_{offset // 50 + 1}', '/api/a-share-index/prices/snapshot',
                                       {'thscodes': ','.join(batch)}, dated=True)
            raw_boards.extend(row for row in (items(response) or []) if isinstance(row, dict) and row.get('thscode') in batch)
    except WindowExpired:
        capture.gap('boards', '原观察窗口结束，剩余板块未采集')
    _guard_job(job)
    names = {row['instrument_id']: row['name'] for row in market.instruments(limit=10000) if row.get('name')}
    all_stocks = normalize_quotes(raw_stocks, names)
    stocks = all_stocks
    sectors = normalize_quotes(raw_boards, sectors={code: catalog[code] for code in codes})
    if not any(row['latest_price'] is not None for row in stocks + sectors):
        raise ValueError('本时段未取得可核验报价')
    if previous and previous['collected_end_at'] >= capture.start.isoformat():
        previous = None
    stocks, recent_listings = split_recent_listings(stocks, reference.get('master', {}), calendar, day)
    prev = previous['comparison_input'] if previous else {}
    ranked = build_intraday_rankings(stocks, sectors, previous_stocks=prev.get('stocks', ()),
                                     previous_sectors=prev.get('sectors', ()))
    stock_coverage = ranked['coverage']['stocks']
    sector_coverage = ranked['coverage']['sectors']
    quotes_by_code = {row['instrument_id']: row for row in all_stocks}
    pool = normalize_pool(*pool_inputs, quotes_by_code, '涨停') if pool_inputs is not None else None
    break_pool = normalize_pool(*break_inputs, quotes_by_code, '炸板') if break_inputs is not None else None
    if pool and pool['state'] != 'observed':
        capture.gap('limit_up', '本轮涨停池未完整核验，只展示可确认的封板样本')
    if break_pool and break_pool['state'] != 'observed':
        capture.gap('limit_break', '本轮炸板池未完整核验，只展示可确认的炸板样本')
    conflicts = ({row['instrument_id'] for row in pool['items']}
                 & {row['instrument_id'] for row in break_pool['items']}) if break_pool else set()
    if conflicts:
        capture.gap('limit_pools', '同一证券同时出现在封板和炸板池，相关样本未用于分类')
    indices = normalize_quotes(raw_indices, INDEX_NAMES)
    if has_feedback and sum(row['change_pct'] is not None and row['latest_price'] is not None
                                  and row['prev_close'] is not None for row in indices) != len(INDEX_NAMES):
        capture.gap('indices', '主要指数报价未全部核验，缺项保留未知')
    quality = 'complete' if (complete and stock_coverage['rankable'] + len(recent_listings) == declared and codes
        and sector_coverage['rankable'] == len(codes) == len(catalog) and not capture.gaps) else 'partial'
    snapshot_id = new_id('intraday')
    keep = ('instrument_id', 'name', 'kind', 'latest_price', 'open', 'prev_close', 'change_pct', 'amount', 'price_basis', 'currency')
    if version == VERSION:
        keep = tuple(key for key in keep if key != 'name') + ('high', 'low')
    inputs = {kind: [{k: row[k] for k in keep if k in row and (version != VERSION or row[k] is not None)} for row in rows]
              for kind, rows in (('stocks', stocks), ('sectors', sectors))}
    if version == VERSION:
        inputs['indices'] = [{key: row[key] for key in
                              ('instrument_id', 'latest_price', 'prev_close', 'amount', 'currency', 'price_basis')}
                             for row in indices]
    snapshot = dict(version=version, id=snapshot_id, date=day, slot=slot['key'], slot_label=slot['label'],
                    collected_start_at=capture.start.isoformat(), collected_end_at=capture.end.isoformat(),
                    quality=quality, stocks=ranked['stocks'], sectors=ranked['sectors'],
                    comparison_input=inputs, source_checks=capture.checks,
                    summary=dict(coverage=ranked['coverage'], stock_declared=declared,
                                 stock_enumeration_complete=complete, recent_listing_count=len(recent_listings),
                                 recent_listings=recent_listings[:5], board_planned=len(codes),
                                 board_returned=len(sectors), request_count=capture.attempts,
                                 comparison_slot=previous['slot'] if previous else None,
                                 gaps=capture.gaps,
                                 reason='部分行情未取得，结果按已核验样本展示' if quality == 'partial' else None))
    if version == VERSION:
        original = market.status()
        original_day = original.get('data_end')
        if isinstance(original_day, str) and original_day < day and original.get('snapshot_id'):
            snapshot['member_reference'] = {'generation': original['snapshot_id'], 'date': original_day}
        snapshot['scheduled_at'] = datetime.combine(date.fromisoformat(day),
                                                    time.fromisoformat(slot['scheduled_at']), CHINA).isoformat()
        if previous:
            snapshot['summary']['comparison_snapshot_id'] = previous['id']
        prior_day = max((value for value in calendar if value < day), default=None)
        prior_stage = prior_baseline = None
        if prior_day:
            with connect() as connection:
                prior_stage = _optional_turnover_slot(connection, prior_day, slot['key'])
                if prior_stage and baseline_slot:
                    prior_baseline = _optional_turnover_slot(connection, prior_day, baseline_slot)
        if (prior_baseline and prior_baseline['collected_end_at'] >= prior_stage['collected_start_at']):
            prior_baseline = None
        if baseline_slot:
            snapshot['summary']['interval_amount'] = _interval_amount(
                stocks, previous, prior_stage, prior_baseline, prior_day)
    if pool is not None:
        members = _memberships(reference, {code: catalog[code] for code in codes}, day)
        topic_rows = [{'instrument_id': code, **catalog[code]} for code in members] if break_pool is not None else []
        quoted_codes = {row['instrument_id'] for row in sectors}
        strength_sectors = [*sectors, *(row for row in topic_rows if row['instrument_id'] not in quoted_codes)]
        strength = build_intraday_strength(
            all_stocks, strength_sectors,
            members=members,
            sealed_ids={row['instrument_id'] for row in pool['items']},
            board_heights={row['instrument_id']: row['continue_day_cnt'] for row in pool['items']
                           if type(row['continue_day_cnt']) is int and row['continue_day_cnt'] > 0},
            pool_complete=pool['state'] == 'observed',
            excluded_candidate_ids={row['instrument_id'] for row in recent_listings},
            previous_stocks=prev.get('stocks', ()), previous_sectors=prev.get('sectors', ()),
            break_pool=break_pool,
            sealed_rows=pool['items'] if break_pool is not None else (),
            observed_sectors=ranked['sectors'] if version == VERSION else (),
        )
        snapshot.update(strength)
        snapshot['summary']['limit_up_pool_state'] = pool['state']
    if break_pool is not None:
        snapshot['summary']['limit_break_pool_state'] = break_pool['state']
    if has_feedback:
        from app.services.intraday_feedback import build_intraday_feedback

        prior_session = _prior_closing_cohort(market, calendar, day, include_broken=has_break_pool)
        if prior_session.get('source'):
            snapshot['source_checks'].append({'domain': 'prior_session', 'state': 'accepted', **prior_session['source']})
        snapshot.update(build_intraday_feedback(
            all_stocks, indices=indices, pool=pool, prior_session=prior_session,
            previous={'label': previous['slot_label'], 'collected_end_at': previous['collected_end_at']} if previous else None,
            previous_pool=previous.get('sealed_pool_input') if previous else None,
            break_pool=break_pool, prior_broken_session=prior_session.get('broken'),
            sectors=topic_rows, members=members if has_break_pool else None,
        ))
        if version == VERSION:
            _attach_turnover(snapshot, previous, prior_stage, prior_baseline, prior_day)
        comparable_pool = [{'instrument_id': row['instrument_id']} for row in pool['items']
                           if row['instrument_id'] not in conflicts]
        comparable_state = ('partial' if comparable_pool else 'unknown') if conflicts else pool['state']
        snapshot['sealed_pool_input'] = {'state': comparable_state, 'items': comparable_pool}
    raw = canonical(snapshot)
    if len(raw) > MAX_PAYLOAD:
        raise ValueError('盘中快照超过2MiB容量上限')
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute('SELECT status,lease_owner FROM job WHERE job_id=?', (job['job_id'],)).fetchone()
        if not current or current['status'] != 'running' or current['lease_owner'] != job.get('lease_owner'):
            raise tasks.TaskCancelled()
        conn.execute('''UPDATE market_intraday_capture SET snapshot_id=?,payload_json=?,content_sha256=?
            WHERE job_id=? AND snapshot_id IS NULL''', (snapshot_id, raw.decode(), sha256(raw).hexdigest(), job['job_id']))
        cutoff = (date.fromisoformat(day) - timedelta(days=27)).isoformat()
        conn.execute('DELETE FROM market_intraday_capture WHERE trade_date<? AND job_id IN '
                     "(SELECT job_id FROM job WHERE status NOT IN ('queued','running'))", (cutoff,))
    return {'snapshot_id': snapshot_id, 'quality': quality, 'message': '盘中快照已留存'}
