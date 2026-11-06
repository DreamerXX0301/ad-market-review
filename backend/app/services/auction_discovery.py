"""独立盘前发现快照，使用来源机会筛选候选、终态竞价核对价格。"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, time
from hashlib import sha256
import json
from math import isfinite
from statistics import median
from time import monotonic, sleep

from app.core import connect
from app.services.auction_analysis import normalize_snapshot
from app.services.auction_collection import CAPTURE_END, CHINA, STOCK_CODE, Collector, WindowExpired, canonical, contains_secret
from app.services.auction_integrity import TERMINAL_RETRY_POLICY, is_retryable_terminal_failure
from app.services.market_outlook import NON_DIRECTION_LABELS
from app.services.supplement_access import supplement_reader
from app.services import task_service as tasks

VERSION = 'auction-discovery-v1'
OPPORTUNITIES = '/api/a-share/special-data/auction-stock-opportunities'
QUOTES = '/api/a-share/auction/snapshot'
PAGES = 3
PAGE_SIZE = 20
OVERFLOW_LIMIT = 40
MAX_PAYLOAD = 3 * 1024 * 1024
SCOPE = '按来源异动找候选，以终态核验报价；题材和行业统计仅覆盖此批样本，归属允许重叠。'


class DiscoveryCollector(Collector):
    max_requests = PAGES + 1
    request_options = {'max_attempts': 1, 'timeout': 4.0}
    retry_delay = 2.0

    def __init__(self, day, adapter, guard, now, *, retry_budget):
        super().__init__(day, adapter, guard, now)
        self.retry_budget = retry_budget

    def final_quotes(self, codes):
        params = {'thscodes': ','.join(codes), 'stage': 'final'}
        name = 'final_quotes'
        body = self.request(name, QUOTES, params)
        if self.retry_budget is None or not is_retryable_terminal_failure(self.sources[-1]):
            return body
        self.guard()
        timeout = self.request_options['timeout']
        if self.retry_budget() < self.retry_delay + timeout:
            raise WindowExpired('竞价发现剩余时间不足，未重试终态报价')
        sleep(self.retry_delay)
        if self.retry_budget() < timeout:
            raise WindowExpired('竞价发现剩余时间不足，未重试终态报价')

        # Keep a single logical source so older readers can still verify v1.
        first = self.sources.pop()
        previous_count = len(self.sources)
        try:
            return self.request(name, QUOTES, params)
        finally:
            if len(self.sources) == previous_count:
                self.sources.append(first)
            else:
                retry = self.sources[-1]
                retry.setdefault('state', 'not_accepted')
                retry['first_failure'] = first
                if retry['state'] == 'received':
                    self.evidence[-1]['first_failure'] = first


class AuthorizationEnded(RuntimeError):
    pass


def _empty(day, state='not_collected', reason=None):
    return {'version': VERSION, 'date': day, 'state': state, 'reason': reason, 'scope': SCOPE,
            'captured_at_ms': None, 'coverage': {'pages': 0, 'events': 0, 'candidates': 0,
                                                  'valid': 0, 'has_more': None, 'total': None},
            'directions': [], 'stocks': [],
            'supplement': {'requested': 0, 'valid': 0, 'items': [], 'remaining': 0}}


def _opportunity_params(page):
    return {'market': 'a_share', 'sort_by': 'warn_time', 'sort_order': 'desc',
            'page': page, 'size': PAGE_SIZE}


def _time_of(value, day, received, start):
    if type(value) is not int:
        return None
    try:
        timestamp = datetime.fromtimestamp(value / 1000, CHINA)
    except (ValueError, OSError, OverflowError):
        return None
    return timestamp if (timestamp.date().isoformat() == day and start <= timestamp.time() < time(9, 30)
                         and timestamp <= received) else None


def _page(body, page, day, received):
    data = body.get('data') if isinstance(body, dict) and body.get('code') == 0 else None
    if not isinstance(data, dict) or data.get('trade_date') != day or data.get('mode') != 'real_time':
        return None
    if not _time_of(data.get('timestamp'), day, received, time(9, 25)):
        return None
    pagination, items = data.get('pagination'), data.get('item')
    if (not isinstance(pagination, dict) or type(pagination.get('page')) is not int
            or pagination['page'] != page or pagination.get('size') != PAGE_SIZE
            or type(pagination.get('has_next')) is not bool
            or not isinstance(items, list) or len(items) > PAGE_SIZE
            or pagination['has_next'] and len(items) != PAGE_SIZE):
        return None
    total = pagination.get('total')
    if total is not None and (type(total) is not int or total < len(items)):
        return None
    return items, pagination['has_next'], total


def _event(row, day, received):
    if not isinstance(row, dict):
        return None
    symbol, timestamp = row.get('thscode'), row.get('warn_timestamp')
    if (not isinstance(symbol, str) or not STOCK_CODE.fullmatch(symbol)
            or row.get('ticker') not in (None, symbol[:6])
            or not _time_of(timestamp, day, received, time(9, 15))):
        return None
    kind = row.get('warn_type')
    if type(kind) not in (str, int) or not str(kind).isdigit() or len(str(kind)) > 10:
        return None
    name = row.get('warn_name')
    label = ' '.join(name.split())[:80] if isinstance(name, str) else ''
    return symbol, timestamp, str(kind), label or f'异动 {kind}'


def _project(quote, heights, warnings):
    keys = ('instrument_id', 'name', 'auction_pct', 'auction_amount', 'auction_price', 'pre_close_price')
    return {**{key: quote[key] for key in keys},
            'previous_board_count': heights.get(quote['instrument_id']),
            'warnings': warnings}


def _directions(primary, quotes, stocks):
    if not stocks:
        return [], False, None
    from app.services.auction_topic_summary import build_topic_summary
    symbols = list(stocks)
    evidence = build_topic_summary({**primary,
                                    'universe': {'items': [{'instrument_id': code} for code in symbols]},
                                    'quotes': {'items': [quotes[code] for code in symbols]}})
    if not evidence.get('source_sha256'):
        return [], True, None
    groups = []
    for group in evidence['groups']:
        if group['kind'] == 'concept' and group['name'] in NON_DIRECTION_LABELS:
            continue
        members = [stocks[code] for code in group['instrument_ids'] if stocks[code]['auction_pct'] > 0]
        if len(members) < 2:
            continue
        members.sort(key=lambda row: (-row['auction_pct'], row['instrument_id']))
        amounts = [row['auction_amount'] for row in members if row['auction_amount'] is not None]
        total = sum(amounts)
        groups.append({'instrument_id': group['topic_code'], 'name': group['name'], 'kind': group['kind'],
                       'members_observed_date': group['members_observed_date'], 'stock_count': len(members),
                       'median_auction_pct': median(row['auction_pct'] for row in members),
                       'total_auction_amount': total if len(amounts) == len(members) and isfinite(total) else None,
                       'stocks': members})
    groups.sort(key=lambda row: (-row['stock_count'], -row['median_auction_pct'], row['kind'], row['instrument_id']))
    membership = {'source_snapshot_id': evidence['source_snapshot_id'], 'source_sha256': evidence['source_sha256']}
    coverage = evidence['coverage']
    return groups[:12], bool(coverage['unclassified'] or coverage['rejected_groups']), membership


def _build(day, primary, frozen, collector):
    result = _empty(day, 'unavailable')
    coverage = result['coverage']
    overflow = frozen.get('discovery_overflow', [])
    overflow_codes = [item['instrument_id'] for item in overflow[:OVERFLOW_LIMIT]]
    omitted = primary['universe']['omitted']
    result['supplement'].update(requested=len(overflow_codes), remaining=omitted)
    focus_codes = {item['instrument_id'] for item in primary['universe']['items']} | set(overflow_codes)
    heights = frozen.get('discovery_previous_heights', {})
    warnings, observed_events, gaps = {}, set(), []
    for page in range(1, PAGES + 1):
        try:
            body = collector.request(f'opportunities:{page}', OPPORTUNITIES, _opportunity_params(page))
        except (WindowExpired, AuthorizationEnded):
            gaps.append('采集授权或时限已结束，未继续读取异动。')
            break
        received = datetime.fromisoformat(collector.sources[-1]['received_at'])
        parsed = _page(body, page, day, received)
        if parsed is None:
            coverage['has_more'] = None
            gaps.append('来源异动日期、实时状态、权限或分页未通过核验。')
            break
        items, coverage['has_more'], coverage['total'] = parsed
        coverage['pages'] += 1
        for item in items:
            event = _event(item, day, received)
            if event is None:
                if '部分来源异动的身份或时间未通过核验。' not in gaps:
                    gaps.append('部分来源异动的身份或时间未通过核验。')
                continue
            symbol, timestamp, kind, label = event
            observed_events.add((symbol, timestamp, kind))
            if symbol not in focus_codes:
                warnings.setdefault(symbol, set()).add(label)
        if not coverage['has_more']:
            break
    coverage['events'] = len(observed_events)
    coverage['candidates'] = len(warnings)
    requested = list(dict.fromkeys([*overflow_codes, *warnings]))
    result['requested'] = requested
    quotes = {}
    if requested:
        try:
            body = collector.final_quotes(requested)
        except (WindowExpired, AuthorizationEnded):
            gaps.append('采集授权或时限已结束，未继续核验终态报价。')
        else:
            normalized = normalize_snapshot(body, requested, day, collector.sources[-1]['received_at'])
            quotes = {row['instrument_id']: row for row in normalized['items'] if row['state'] == 'observed'}
            if len(quotes) != len(requested):
                gaps.append('部分候选未取得同日有效竞价终态。')
            if any(row['auction_amount'] is None for row in quotes.values()):
                gaps.append('部分竞价金额未通过核验。')
    stocks = {symbol: _project(quotes[symbol], heights, sorted(labels)[:8])
              for symbol, labels in warnings.items() if symbol in quotes}
    coverage['valid'] = len(stocks)
    result['stocks'] = sorted(stocks.values(), key=lambda row: (-row['auction_pct'], row['instrument_id']))
    supplement = [_project(quotes[symbol], heights, []) for symbol in overflow_codes if symbol in quotes]
    result['supplement'].update(valid=len(supplement), items=supplement, remaining=max(0, omitted - len(supplement)))
    directions, missing_members, membership = _directions(primary, quotes, stocks)
    result.update(directions=directions, membership=membership)
    if missing_members:
        gaps.append('部分股票缺少已核验的原代际行业或题材归属。')
    if coverage['has_more']:
        gaps.append('已达到本轮异动页数上限，仍有未读取记录。')
    has_result = bool(stocks or supplement or coverage['pages'] and not warnings)
    if has_result:
        result['state'] = 'partial' if gaps else 'available'
    result['reason'] = ' '.join(gaps) or ('本轮未发现名单外且有终态报价的股票。' if not stocks else None)
    if collector.evidence:
        result['captured_at_ms'] = int(datetime.fromisoformat(collector.last_accepted_at).timestamp() * 1000)
    for flag in ('egress_allowed', 'ai_processing_allowed'):
        for source in collector.evidence:
            body = source['body']
            data = body['data']
            items = data.get('item')
            rows = items if isinstance(items, list) else []
            if any(row.get(flag) is False for row in (body, data, *rows) if isinstance(row, dict)):
                result[flag] = False
                break
    result.update(sources=collector.sources, evidence=collector.evidence)
    if collector.retry_budget is not None:
        result['terminal_retry'] = TERMINAL_RETRY_POLICY
    return result


def collect_after_primary(job, primary_snapshot_id, authorization, callback=None):
    """Run with a separate budget after the main result is committed."""
    from app.services import auction_automation as automatic, auction_service
    from app.services.auction_integrity import validate_discovery, validate_snapshot
    from app.services.source_service import client

    day = job['target_date']
    with connect() as connection:
        saved = connection.execute('SELECT * FROM market_auction_snapshot WHERE snapshot_id=? AND job_id=?',
                                   (primary_snapshot_id, job['job_id'])).fetchone()
        saved_job = connection.execute('SELECT parameters_json FROM job WHERE job_id=?', (job['job_id'],)).fetchone()
    if not saved or not saved_job:
        return
    primary = validate_snapshot(dict(saved))
    frozen = json.loads(saved_job[0])['frozen_inputs']
    deadline = monotonic() + 20
    window_end = datetime.combine(datetime.fromisoformat(day).date(), CAPTURE_END, CHINA)

    def retry_budget():
        return min(deadline - monotonic(), (window_end - auction_service.now_china()).total_seconds())

    def guard():
        if monotonic() >= deadline or not auction_service.capture_window(day)['allowed']:
            raise WindowExpired('竞价发现已达单轮时间预算')
        with connect() as connection:
            if not _owns_lease(connection, job, auction_service.now_china()):
                raise tasks.TaskCancelled()
            discovery = automatic.discovery_authorized(connection, authorization['discovery'])
            main = automatic._matches(automatic._config(connection), authorization['revision'], day)
            row = connection.execute("SELECT value_json FROM platform_setting WHERE key='source-credential-revision:fuyao'").fetchone()
            credential_revision = json.loads(row[0]) if row else 'legacy'
        if not discovery or not main or credential_revision != authorization['credential_revision']:
            raise AuthorizationEnded('竞价发现授权或来源配置已变更')

    adapter = None
    try:
        guard()
        adapter = client('fuyao')
        can_retry = authorization['discovery'].get('terminal_retry') == TERMINAL_RETRY_POLICY
        collector = DiscoveryCollector(day, adapter, guard, auction_service.now_china,
                                       retry_budget=retry_budget if can_retry else None)
        if callback:
            callback('正在核验竞价发现', 90)
        payload = _build(day, primary, frozen, collector)
    except (tasks.TaskPaused, tasks.TaskCancelled):
        return
    except Exception as error:
        if isinstance(error, (WindowExpired, AuthorizationEnded)):
            reason = '竞价发现已超时或本次授权已结束。'
        else:
            reason = '本次竞价发现未取得可核验结果。'
        payload = _empty(day, 'unavailable', reason)
        payload['supplement'].update(requested=len(frozen.get('discovery_overflow', [])),
                                     remaining=primary['universe']['omitted'])
        payload.update(sources=[], evidence=[], requested=[])

    encoded = canonical(payload)
    if len(encoded) > MAX_PAYLOAD or contains_secret(payload, getattr(adapter, 'key', None)):
        raise ValueError('竞价发现正文超过保存预算或回显凭据')
    row = {'trade_date': day, 'job_id': job['job_id'], 'primary_snapshot_id': primary_snapshot_id,
           'recorded_at_ms': int(auction_service.now_china().timestamp() * 1000),
           'content_sha256': sha256(encoded).hexdigest(), 'payload_json': encoded.decode()}
    validate_discovery(row)
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if not _owns_lease(connection, job, auction_service.now_china()):
            return
        connection.execute('''INSERT INTO market_auction_discovery
            (trade_date,job_id,primary_snapshot_id,recorded_at_ms,content_sha256,payload_json)
            VALUES(:trade_date,:job_id,:primary_snapshot_id,:recorded_at_ms,:content_sha256,:payload_json)''', row)
        tasks.append_event(connection, job['job_id'], 'auction_discovery_saved', '已独立记录本次竞价发现结果',
                           details={'state': payload['state'], 'valid': payload['coverage']['valid'],
                                    'pages': payload['coverage']['pages'],
                                    'supplement_valid': payload['supplement']['valid']})


def _owns_lease(connection, job, now):
    current = connection.execute('SELECT status,lease_owner,lease_expires_at_ms FROM job WHERE job_id=?',
                                 (job['job_id'],)).fetchone()
    return (current and current['status'] == 'running' and current['lease_owner'] == job.get('lease_owner')
            and current['lease_expires_at_ms'] is not None
            and current['lease_expires_at_ms'] >= int(now.timestamp() * 1000))


def _project_discovery_rows(payload, parent):
    from app.services.auction_integrity import validate_snapshot
    from app.services.auction_topic_summary import build_topic_summary
    from app.services.market_broken_boards import _board_fields

    quotes = [*payload['stocks'], *payload['supplement']['items']]
    if not quotes:
        return payload
    classifications = {}
    if parent is not None:
        try:
            primary = validate_snapshot(parent)
        except ValueError:
            pass
        else:
            summary = build_topic_summary({**primary,
                'universe': {'items': [{'instrument_id': row['instrument_id']} for row in quotes]},
                'quotes': {'items': [{**row, 'state': 'observed'} for row in quotes]}})
            frozen = payload.get('membership')
            if frozen is None or isinstance(frozen, dict) and all(
                    summary.get(key) is not None and summary[key] == frozen.get(key)
                    for key in ('source_snapshot_id', 'source_sha256')):
                classifications = summary.get('stock_classifications', {})

    stocks = {}
    for row in quotes:
        symbol = row['instrument_id']
        entry = classifications.get(symbol, {})
        industries = entry.get('industries', [])[:1]
        concepts = [tag for tag in entry.get('topics', []) if tag['name'] not in NON_DIRECTION_LABELS][:2]
        themes = [{'name': tag['name'], 'kind': tag['kind'], 'topic_code': tag['topic_code'],
                   'member_date': tag['members_observed_date']} for tag in [*industries, *concepts]]
        stocks[symbol] = {**row, **_board_fields(symbol), 'themes': themes,
                          'themes_state': 'observed_sample' if themes else 'unknown'}
    return {**payload, 'stocks': [stocks[row['instrument_id']] for row in payload['stocks']],
            'directions': [{**group, 'stocks': [stocks[row['instrument_id']] for row in group['stocks']]}
                           for group in payload['directions']],
            'supplement': {**payload['supplement'],
                           'items': [stocks[row['instrument_id']] for row in payload['supplement']['items']]}}


def read_view(day, *, connection=None, include_themes=False):
    from app.services.auction_integrity import validate_discovery
    from app.services.auction_retention import retention_status
    from app.services.auction_service import day_value

    day = day_value(day)
    with (connect() if connection is None else nullcontext(connection)) as reader:
        exists = reader.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_auction_discovery'").fetchone()
        row = reader.execute('SELECT * FROM market_auction_discovery WHERE trade_date=?', (day,)).fetchone() if exists else None
        if not row:
            if retention_status(reader, day)['purged']:
                return _empty(day, 'purged', '当日竞价资料已到期清理。')
            parent = reader.execute('''SELECT j.status,j.parameters_json FROM market_auction_snapshot s
                JOIN job j ON j.job_id=s.job_id WHERE s.trade_date=?''', (day,)).fetchone() if exists else None
            if parent and parent[0] in ('succeeded', 'partial', 'no_change', 'failed', 'cancelled'):
                proof = (json.loads(parent[1]).get('automation') or {}).get('discovery')
                if isinstance(proof, dict) and proof.get('version') == VERSION:
                    return _empty(day, 'unavailable', '当天的竞价发现未完成留存。')
            return _empty(day, reason='该日暂无竞价发现记录。')
        row = dict(row)
        parent = reader.execute('''SELECT * FROM market_auction_snapshot
            WHERE snapshot_id=? AND job_id=? AND trade_date=?''',
            (row['primary_snapshot_id'], row['job_id'], day)).fetchone() if include_themes else None
    try:
        payload = validate_discovery(row)
    except ValueError:
        return _empty(day, 'unavailable', '该日竞价发现记录未通过完整性核验。')
    if include_themes:
        payload = _project_discovery_rows(payload, dict(parent) if parent is not None else None)
    reason = payload.get('reason')
    terminal = next((source for source in payload['sources'] if source['name'] == 'final_quotes'), None)
    if terminal and terminal.get('state') == 'failed':
        remaining = (reason or '').replace('部分候选未取得同日有效竞价终态。', '', 1).strip()
        failure = '竞价终态报价重试后仍失败' if 'first_failure' in terminal else '竞价终态报价请求失败'
        reason = f'{failure}，本轮待查股票未核验。 {remaining}'.rstrip()
    return {**{key: value for key, value in payload.items()
               if key not in ('sources', 'evidence', 'requested', 'terminal_retry')},
            'reason': reason,
            **{key: row[key] for key in ('primary_snapshot_id', 'recorded_at_ms', 'content_sha256')}}


@supplement_reader
def read_history(day):
    from app.services.intraday_stock_detail import history_for_symbols

    view = read_view(day)
    symbols = list(dict.fromkeys(row['instrument_id'] for row in [*view['stocks'], *view['supplement']['items']]))
    if not symbols:
        return {'date': view['date'], 'slot': 'auction', 'as_of': None, 'items': {}}
    # validate_discovery bounds and checks the union against the frozen requests.
    return history_for_symbols(view['date'], 'auction', symbols)
