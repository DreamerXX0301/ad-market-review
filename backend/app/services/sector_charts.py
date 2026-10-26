"""Single-sector daily-history cache and offline D/W/M chart projection.

Only collect_sector_chart performs provider calls or writes cache artifacts.
Plans bind a real current-catalog identity and a frozen published date window;
constituent membership and individual-stock adjustments are never consulted.
"""
from __future__ import annotations

from datetime import datetime
from contextvars import ContextVar
from hashlib import sha256
import json
from pathlib import Path

from app.analytics.indicators import aggregate_weekly, enrich_bars, INDICATOR_VERSION
from app.analytics.monthly import aggregate_monthly, enrich_monthly_bars
from app.analytics.waves import candidate_waves
from app.core import digest, json_clean
from app.infrastructure.config import published_dir
from app.services.sector_history import _catalog, _day, _midnight, _number, CHINA
from app.services.three_year_calendar import years_before

NORMALIZATION_VERSION = 'fuyao-sector-chart-v1'
MAX_DAILY_BARS = 1600
MAX_ARTIFACT_BYTES = 8*1024*1024
MAX_PROJECTION_DAYS = 1600
MAX_PROJECTION_CANDIDATES = 8
INCREMENT_CONTRACT = 'fuyao-sector-chart-increment-v1'
MAX_LINEAGE_SEGMENTS = 1600
SCOPE = '当前同花顺目录板块的来源历史指数点位；按正式留存窗口截取，不依赖或证明历史成分股'
_read_files = ContextVar('sector_chart_verified_files', default=None)


class _VerifiedRead(tuple):
    """Private two-item read result, with the actual verified source closure."""
    def __new__(cls, record, pointer, files, pointer_path, pointer_bytes, calendar):
        value = super().__new__(cls, (record, pointer))
        value.files = tuple(sorted(files))
        value.root = str(_root().resolve())
        value.pointer_path = str(pointer_path.relative_to(_root().resolve()))
        value.pointer_sha256 = sha256(pointer_bytes).hexdigest()
        value.pointer_value_sha256 = digest(pointer)
        value.record_sha256 = digest(record)
        value.calendar_sha256 = digest(calendar)
        return value


class _Collected(dict):
    """Source result JSON stays unchanged; no histories escape as extra keys."""
    def __init__(self, value, verified=None):
        super().__init__(value)
        self.verified = verified


def _root():
    return published_dir()/'market-supplement'


def _signature(code, start, end):
    return digest({'source': 'fuyao', 'topic_code': code, 'interval': '1d',
                   'start': start, 'end': end, 'contract': NORMALIZATION_VERSION})


def _source_today():
    return datetime.now(CHINA).date().isoformat()


def _source_request(plan, observed_on=None):
    """Freeze the provider's rolling boundary without changing the chart plan.

    A saved response is always revalidated against its original request receipt,
    not today's clock. Thus an existing historical chart does not expire daily.
    """
    observed_on = _day(observed_on if observed_on is not None else _source_today())
    earliest = years_before(observed_on, 5)
    start = max(plan['start'], earliest)
    if observed_on < plan['as_of'] or start > plan['as_of']:
        raise ValueError('板块所选日期超出来源当前近五年可查询范围；已留存缓存仍可只读查看')
    request = {'source': 'fuyao', 'topic_code': plan['topic_code'], 'interval': '1d',
               'start': start, 'end': plan['as_of'], 'observed_on': observed_on,
               'earliest_supported_date': earliest, 'expected_start': plan['start'],
               'expected_end': plan['as_of'], 'clipped': start > plan['start'],
               'basis': 'provider-recent-five-years-at-collection-date'}
    request['signature'] = digest({'chart_request_signature': plan['request_signature'], **request})
    return request


def _plan_from_inputs(status, calendar, catalog, topic_code, as_of):
    """Shared pure planner, so batch/status reads scan the catalog only once."""
    if not isinstance(topic_code, str): raise ValueError('请选择明确的同花顺板块代码')
    code = topic_code.strip().upper()
    day = _day(as_of)
    begin = _day(status.get('data_begin')); end = _day(status.get('data_end'))
    if day not in calendar or day > end or day < begin:
        raise ValueError('板块K线日期必须为已发布行情范围内的交易日')
    topic = catalog.get(code)
    if topic is None:
        raise ValueError('板块代码不在当前已核验目录，不能按名称或其它供应商代码猜测')
    start = max(begin, years_before(day, 5))
    instrument = {'instrument_id': code, 'thscode': code, 'code': code.split('.')[0],
                  'name': topic['name'], 'kind': 'index', 'sector_kind': topic['kind'],
                  'topic_code': code, 'topic_id': topic['topic_id'], 'exchange': code.split('.')[1]}
    return {'source': 'fuyao', 'topic_code': code, 'instrument': instrument,
            'as_of': day, 'start': start, 'end': day,
            'snapshot_id': status.get('snapshot_id'), 'window': 'published-max-five-years',
            'request_signature': _signature(code, start, day),
            'normalization_version': NORMALIZATION_VERSION, 'scope': SCOPE}


def plan_sector_chart(market, topic_code, as_of):
    """Freeze one explicit board's actual published window before queueing."""
    from .sector_read_context import prepared_plan
    prepared = prepared_plan(market, topic_code, as_of)
    if prepared is not None:
        return prepared
    return _plan_from_inputs(market.status(), set(market.calendar()),
                             _catalog(market.reference())[0], topic_code, as_of)


def _validate_plan(market, plan):
    if not isinstance(plan, dict): raise ValueError('板块K线任务缺少冻结计划')
    current = plan_sector_chart(market, plan.get('topic_code'), plan.get('as_of'))
    if any(plan.get(field) != current[field] for field in
           ('source', 'topic_code', 'as_of', 'start', 'end', 'snapshot_id',
            'window', 'request_signature', 'normalization_version', 'instrument')):
        raise ValueError('板块K线冻结计划与所用行情代际或请求窗口不符')
    return current


def normalize_sector_chart(payload, plan, calendar, source_request=None):
    """Validate every provider row before computing causal chart indicators."""
    if not isinstance(payload, dict) or type(payload.get('code')) is not int or payload['code'] != 0:
        raise ValueError('板块K线来源业务响应未通过')
    data = payload.get('data')
    if (not isinstance(data, dict) or data.get('thscode') != plan['topic_code']
            or data.get('interval') != '1d' or data.get('adjust') is not None):
        raise ValueError('板块K线响应身份、日线或指数原始点位口径不符')
    if source_request is not None:
        if (not isinstance(source_request, dict)
                or source_request != _source_request(plan, source_request.get('observed_on'))):
            raise ValueError('板块K线实际请求窗口依据或签名不符')
    actual_start = source_request['start'] if source_request else plan['start']
    source = data.get('item')
    if not isinstance(source, list): raise ValueError('板块K线来源日线列表结构无效')
    if not source: raise ValueError('板块K线来源返回空列表，未当作完整结果；请核对来源近五年滚动边界')
    if len(source) > MAX_DAILY_BARS: raise ValueError('板块K线超过五年有界行数，未当作完整结果')
    expected = [day for day in sorted(set(calendar)) if plan['start'] <= day <= plan['as_of']]
    expected_set = set(expected); bars = {}
    for row in source:
        if not isinstance(row, dict): raise ValueError('板块K线行结构无效')
        stamp = row.get('date_ms')
        # Reuse the finite nonnegative-number rules without treating booleans as prices.
        _number(stamp)
        if int(stamp) != stamp: raise ValueError('板块K线时间戳不是完整毫秒')
        try: day = datetime.fromtimestamp(stamp/1000, CHINA).date().isoformat()
        except (ValueError, OverflowError, OSError): raise ValueError('板块K线日期无效') from None
        if (day not in expected_set or stamp != _midnight(day)
                or not actual_start <= day <= plan['as_of']):
            raise ValueError('板块K线有未来、日历外或请求窗口外报价')
        if row.get('thscode') not in (None, plan['topic_code']):
            raise ValueError('板块K线行代码与请求不符')
        if row.get('date') not in (None, day, day.replace('-', '')):
            raise ValueError('板块K线显式日期与时间戳冲突')
        if day in bars: raise ValueError('板块K线存在重复日线，拒绝按最后一条覆盖')
        opening, high, low, close = (_number(row.get(key), positive=True) for key in
                                      ('open_price', 'high_price', 'low_price', 'close_price'))
        if high < max(opening, low, close) or low > min(opening, high, close):
            raise ValueError('板块K线OHLC关系无效')
        bars[day] = {'date': day, 'open': opening, 'high': high, 'low': low, 'close': close,
                     'volume': _number(row.get('volume'), optional=True),
                     'amount': _number(row.get('turnover'), optional=True),
                     'source': 'fuyao', 'price_basis': 'none',
                     # This flag describes an index's non-adjusted point series,
                     # not proof about any individual constituent's actions.
                     'corporate_action': False, 'corporate_action_coverage_known': True}
    if plan['as_of'] not in bars: raise ValueError('板块历史尚无所选交易日日K，不能用之前一条顶替')
    ordered = [bars[day] for day in sorted(bars)]
    missing = [day for day in expected if day not in bars]
    amount_unknown = sum(row['amount'] is None for row in ordered)
    volume_unknown = sum(row['volume'] is None for row in ordered)
    complete = not missing and not amount_unknown and not volume_unknown
    coverage = {'requested_start': plan['start'], 'requested_end': plan['as_of'],
                'start': ordered[0]['date'], 'end': ordered[-1]['date'],
                'expected_count': len(expected), 'observed_count': len(ordered),
                'missing_dates': missing, 'missing_count': len(missing),
                'amount_unknown_count': amount_unknown, 'volume_unknown_count': volume_unknown,
                'complete': complete, 'membership_independent': True,
                'historical_catalog_complete': False, 'source': 'fuyao',
                'notice': '所列缺口不区分板块新设与来源缺数，不补造设立日或报价；指数不采用个股复权，成交量来源单位未核验。'}
    if source_request is not None:
        coverage['source_request'] = dict(source_request)
        if source_request['clipped']:
            coverage['notice'] += (' 来源近五年按获取日滚动，实际请求起点为'+source_request['start']+
                                   '；原计划起点仍为'+plan['start']+'，被裁剪的缺日报价保持未知。')
    return {'date': plan['as_of'], 'as_of': plan['as_of'], 'topic_code': plan['topic_code'],
            'source': 'fuyao', 'start': plan['start'], 'end': plan['as_of'],
            'instrument': dict(plan['instrument']), 'bars': ordered, 'coverage': coverage,
            'complete': complete, 'normalization_version': NORMALIZATION_VERSION,
            **({'source_request': dict(source_request)} if source_request is not None else {}),
            'request_signature': plan['request_signature'], 'scope': SCOPE}


def _cache_key(plan):
    return plan['as_of']+'-'+plan['topic_code']


def _verified_bytes(relative, expected_sha):
    root = _root().resolve()
    if not isinstance(relative, str): raise ValueError('板块K线缓存缺少文件依据')
    value = Path(relative)
    if value.is_absolute() or '..' in value.parts: raise ValueError('板块K线缓存路径越界')
    path = (root/value).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError('板块K线缓存缺失或超出范围')
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != expected_sha: raise ValueError('板块K线缓存SHA校验失败')
    return raw


def _verified_json(relative, expected_sha):
    raw = _verified_bytes(relative, expected_sha)
    captured = _read_files.get()
    if captured is not None:
        captured.add((relative, expected_sha))
        if len(captured) > MAX_LINEAGE_SEGMENTS*4:
            raise ValueError('板块来源链超出保护范围')
    return json.loads(raw)


def verified_collected_input(result, plan, calendar):
    """Reuse normalization only after rehashing every actual input file.

    The token was issued by _read_cached after raw-chain replay. It is never
    persisted or accepted from an API request, and mutable record edits reject
    reuse. Derivatives still bind the same pointer and source file SHA list.
    """
    value = getattr(result, 'verified', None)
    if not isinstance(value, _VerifiedRead):
        return None
    record, pointer = value
    if (value.root != str(_root().resolve()) or digest(record) != value.record_sha256
            or digest(pointer) != value.pointer_value_sha256
            or digest(calendar) != value.calendar_sha256
            or result.get('record') is not record
            or any(record.get(key) != plan.get(key) for key in
                   ('topic_code', 'as_of', 'start', 'end', 'request_signature'))):
        raise ValueError('板块已核验记录或读取作用域发生变化')
    pointer_path = (_root().resolve()/value.pointer_path).resolve()
    if (not pointer_path.is_relative_to(_root().resolve()) or not pointer_path.is_file()
            or pointer_path.stat().st_size > 64*1024
            or sha256(pointer_path.read_bytes()).hexdigest() != value.pointer_sha256):
        raise ValueError('板块已核验记录指针发生变化')
    for relative, checksum in value.files:
        _verified_bytes(relative, checksum)
    proofs = {'topic_code': plan['topic_code'], 'as_of': plan['as_of'], 'source': 'fuyao',
              'price_basis': 'none', 'artifact': pointer['artifact'], 'sha256': pointer['sha256'],
              'files': [{'artifact': relative, 'sha256': checksum} for relative, checksum in value.files]}
    return record, pointer, proofs


def _origin_plan(record, code):
    if (not isinstance(record, dict) or record.get('source') != 'fuyao'
            or record.get('normalization_version') != NORMALIZATION_VERSION
            or record.get('topic_code') != code):
        raise ValueError('板块K线原始链身份或版本不符')
    day = _day(record.get('date')); start = _day(record.get('start'))
    if record.get('as_of') != day or record.get('end') != day or start > day:
        raise ValueError('板块K线原始链窗口不符')
    signature = _signature(code, start, day)
    if record.get('request_signature') != signature:
        raise ValueError('板块K线原始链请求签名不符')
    return {'source': 'fuyao', 'topic_code': code, 'as_of': day, 'start': start, 'end': day,
            'request_signature': signature, 'instrument': record.get('instrument'),
            'normalization_version': NORMALIZATION_VERSION}


def _payload_from_bars(code, rows):
    return {'code': 0, 'data': {'thscode': code, 'interval': '1d', 'adjust': None,
        'item': [{'date_ms': _midnight(row['date']), 'open_price': row['open'],
                  'high_price': row['high'], 'low_price': row['low'], 'close_price': row['close'],
                  'volume': row['volume'], 'turnover': row['amount']} for row in rows]}}


def _replay_record(accepted, plan, calendar, projection_plan=None):
    """Iterate small receipts to one full base; never stack full histories.

    Thin nodes reference immutable previous/tail normalized objects. Each tail
    is independently checked against its actual provider raw JSON and receipt.
    """
    current = accepted; current_plan = plan; tails = []; seen = set()
    for _ in range(MAX_LINEAGE_SEGMENTS):
        origin = _origin_plan(current, plan['topic_code'])
        if any(origin[key] != current_plan[key] for key in ('start', 'as_of', 'request_signature')):
            raise ValueError('板块K线原始链与预期窗口不符')
        key = (current.get('raw_artifact'), current.get('raw_sha256'))
        if key in seen: raise ValueError('板块K线原始链存在循环')
        seen.add(key)
        payload = _verified_json(*key)
        if not isinstance(payload, dict): raise ValueError('板块K线原始响应结构无效')
        if payload.get('contract') != INCREMENT_CONTRACT:
            base = normalize_sector_chart(payload, current_plan, calendar, current.get('source_request'))
            break
        if (current.get('storage') != 'thin-increment-v1'
                or payload.get('source') != 'fuyao' or payload.get('topic_code') != plan['topic_code']
                or payload.get('as_of') != current_plan['as_of'] or payload.get('start') != current_plan['start']
                or payload.get('request_signature') != current_plan['request_signature']):
            raise ValueError('板块K线增量原始依据与窗口不符')
        previous_ref = payload.get('previous', {}); tail_ref = payload.get('tail', {})
        if not isinstance(previous_ref, dict) or not isinstance(tail_ref, dict):
            raise ValueError('板块K线增量原始引用结构无效')
        previous = _verified_json(previous_ref.get('artifact'), previous_ref.get('sha256'))
        previous_plan = _origin_plan(previous, plan['topic_code'])
        tail = _verified_json(tail_ref.get('artifact'), tail_ref.get('sha256'))
        tail_plan = _origin_plan(tail, plan['topic_code'])
        following = next((day for day in calendar if day > previous_plan['as_of']), None)
        if (previous_plan['as_of'] >= current_plan['as_of'] or tail_plan['start'] != following
                or tail_plan['as_of'] != current_plan['as_of']
                or payload.get('previous_as_of') != previous_plan['as_of']):
            raise ValueError('板块K线尾部不是前缓存之后的精确交易日窗口')
        tail_raw = _verified_json(tail.get('raw_artifact'), tail.get('raw_sha256'))
        normalized_tail = normalize_sector_chart(tail_raw, tail_plan, calendar, tail.get('source_request'))
        tails.append((normalized_tail, tail_ref, previous_ref))
        current = previous; current_plan = previous_plan
    else:
        raise ValueError('板块K线原始链超过1600段保护上限，保持未知')
    if not tails and projection_plan is None:
        record = base
    else:
        joined = {row['date']: row for row in base['bars']}
        for tail, _, _ in reversed(tails):
            for row in tail['bars']:
                if row['date'] in joined: raise ValueError('板块K线增量原始链有重复日期')
                joined[row['date']] = row
        selected = projection_plan or plan
        rows = [joined[day] for day in sorted(joined) if selected['start'] <= day <= selected['as_of']]
        record = normalize_sector_chart(_payload_from_bars(plan['topic_code'], rows), selected, calendar)
        if tails:
            record['raw_lineage'] = {'contract': INCREMENT_CONTRACT, 'segments': len(tails)+1,
                'full_base_as_of': base['as_of'], 'full_base_source_request': base.get('source_request'),
                'tail_source_request': tails[0][0].get('source_request'),
                'previous': tails[0][2], 'tail': tails[0][1]}
            record['coverage']['raw_lineage'] = record['raw_lineage']
        elif base.get('source_request') is not None:
            record['source_request'] = base['source_request']
    record.update(fetched_at=accepted.get('fetched_at'), raw_artifact=accepted.get('raw_artifact'),
                  raw_sha256=accepted.get('raw_sha256'))
    return record


def _read_cached(plan, calendar, capability='sector-chart', cache_key=None, projection_plan=None):
    """No cache constructor: GET must not mkdir, write or perform HTTP."""
    root = _root().resolve()
    pointer = root/'accepted/fuyao'/capability/((cache_key or _cache_key(plan))+'.json')
    if not pointer.is_file(): return None
    if not pointer.resolve().is_relative_to(root) or pointer.stat().st_size > 64*1024:
        raise ValueError('板块K线缓存指针路径或大小异常')
    pointer_bytes = pointer.read_bytes()
    saved = json.loads(pointer_bytes)
    if (not isinstance(saved, dict) or saved.get('version') != 'market-supplement-v1'
            or saved.get('signature') != plan['request_signature']):
        raise ValueError('板块K线缓存请求签名与所选窗口不符')
    files = set(); token = _read_files.set(files)
    try:
        accepted = _verified_json(saved.get('artifact'), saved.get('sha256'))
        if (not isinstance(accepted, dict) or accepted.get('date') != plan['as_of'] or accepted.get('topic_code') != plan['topic_code']
                or accepted.get('request_signature') != plan['request_signature']):
            raise ValueError('板块K线缓存日期、代码或请求依据不符')
        record = _replay_record(accepted, plan, calendar, projection_plan)
        return _VerifiedRead(record, saved, files, pointer, pointer_bytes, calendar)
    finally:
        _read_files.reset(token)


def _read_chart_cache(market, plan, calendar):
    """Project verified raw history, never already-computed future indicators."""
    exact = _read_cached(plan, calendar)
    if exact is not None:
        return exact
    root = _root().resolve()
    end = market.status()['data_end']
    later = sorted({day for day in calendar if plan['as_of'] < day <= end}, reverse=True)
    inspected = 0
    for candidate in later[:MAX_PROJECTION_DAYS]:
        pointer = root/'accepted/fuyao/sector-chart'/(candidate+'-'+plan['topic_code']+'.json')
        if not pointer.is_file(): continue
        inspected += 1
        if inspected > MAX_PROJECTION_CANDIDATES: break
        try:
            if not pointer.resolve().is_relative_to(root) or pointer.stat().st_size > 64*1024:
                continue
            saved = json.loads(pointer.read_text())
            accepted = _verified_json(saved.get('artifact'), saved.get('sha256'))
            if (not isinstance(accepted, dict) or accepted.get('source') != 'fuyao'
                    or accepted.get('normalization_version') != NORMALIZATION_VERSION
                    or accepted.get('date') != candidate or accepted.get('end') != candidate
                    or accepted.get('topic_code') != plan['topic_code']):
                continue
            origin = {**plan, 'as_of': candidate, 'end': candidate,
                      'start': _day(accepted.get('start'))}
            if origin['start'] > plan['as_of']: continue
            origin['request_signature'] = _signature(plan['topic_code'], origin['start'], candidate)
            verified = _read_cached(origin, calendar, projection_plan=plan)
            if verified is None: continue
            record, original_pointer = verified
            rows = [row for row in record['bars'] if plan['start'] <= row['date'] <= plan['as_of']]
            if not rows or rows[-1]['date'] != plan['as_of']: continue
            payload = {'code': 0, 'data': {'thscode': plan['topic_code'], 'interval': '1d',
                       'adjust': None, 'item': [{'date_ms': _midnight(row['date']),
                       'open_price': row['open'], 'high_price': row['high'],
                       'low_price': row['low'], 'close_price': row['close'],
                       'volume': row['volume'], 'turnover': row['amount']} for row in rows]}}
            projected = normalize_sector_chart(payload, plan, calendar)
            projected.update(fetched_at=record.get('fetched_at'), raw_artifact=record.get('raw_artifact'),
                             raw_sha256=record.get('raw_sha256'), cache_as_of=candidate,
                             cache_source_request=record.get('source_request'),
                             raw_lineage=record.get('raw_lineage'),
                             projected_from_later_cache=True)
            projected['coverage'].update(cache_as_of=candidate,
                cache_source_request=record.get('source_request'), projected_from_later_cache=True)
            projected['coverage']['notice'] += (' 本图从'+candidate+'已核验原始日K截至所选日，'
                '只用所选日及之前报价重新计算指标；原获取窗口单独保留，不表示历史成员已留存。')
            return projected, original_pointer
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return None


def collect_sector_chart(market, plan, adapter, guard=None, progress=None):
    """Worker-only single-code collection; ProviderClient owns bounded retries."""
    if guard: guard()
    frozen = _validate_plan(market, plan)
    if progress: progress('核验板块K线缓存', 5, frozen['instrument']['name'])
    cached = _read_cached(frozen, market.calendar())
    if cached is not None:
        record, pointer = cached
        if guard: guard()
        if progress: progress('板块K线已核验', 100, '已核验本地原始响应，未新增请求')
        return _Collected({'available': True, 'record': record, 'cache_hit': True,
                'artifact': pointer['artifact'], 'sha256': pointer['sha256'],
                'coverage': record['coverage'], 'quality': 'complete' if record['complete'] else 'partial',
                'message': '指定板块K线已从原始SHA缓存复验'}, cached)
    if guard: guard()
    source_request = _source_request(frozen)
    message = source_request['start']+' 至 '+source_request['end']
    if source_request['clipped']:
        message += '；原计划起点'+frozen['start']+'超出来源滚动范围，缺口保持未知'
    if progress: progress('下载板块历史日K', 15, message)
    if callable(getattr(adapter, 'set_chart_request_kind', None)): adapter.set_chart_request_kind('full')
    payload = adapter.request('/api/a-share-index/prices/historical', {
        'thscode': frozen['topic_code'], 'interval': '1d',
        'start': _midnight(source_request['start']), 'end': _midnight(source_request['end'])+86400000-1})
    if progress: progress('核验板块历史报价', 65, '逐日核验日期、身份、OHLC与留存市场日历')
    record = normalize_sector_chart(payload, frozen, market.calendar(), source_request)
    if guard: guard()
    # Reuse the existing immutable raw/normalized artifact store. Creating it is
    # confined to this worker entry point; the read path above never does so.
    from app.services.market_supplement import SupplementCache
    cache = SupplementCache(_root())
    accepted = cache.accept('fuyao', 'sector-chart', _cache_key(frozen), frozen['request_signature'], record, payload)
    if guard: guard()
    pointer_path = cache.root/'accepted/fuyao/sector-chart'/(_cache_key(frozen)+'.json')
    pointer = json.loads(pointer_path.read_text())
    if progress: progress('板块K线已保存', 100, '仅保存指定板块，日周月指标读取时本地计算')
    return _Collected({'available': True, 'record': accepted, 'cache_hit': False,
            'artifact': pointer['artifact'], 'sha256': pointer['sha256'],
            'coverage': record['coverage'], 'quality': 'complete' if record['complete'] else 'partial',
            'message': '指定板块历史日K已校验并保存'})


def _previous_cache(plan, calendar):
    root = _root().resolve(); inspected = 0; invalid = False
    for day in sorted({day for day in calendar if day < plan['as_of']}, reverse=True)[:MAX_PROJECTION_DAYS]:
        pointer = root/'accepted/fuyao/sector-chart'/(day+'-'+plan['topic_code']+'.json')
        if not pointer.is_file(): continue
        inspected += 1
        if inspected > MAX_PROJECTION_CANDIDATES: break
        try:
            if not pointer.resolve().is_relative_to(root) or pointer.stat().st_size > 64*1024:
                raise ValueError('板块旧缓存指针路径或大小异常')
            saved = json.loads(pointer.read_text())
            accepted = _verified_json(saved.get('artifact'), saved.get('sha256'))
            origin = _origin_plan(accepted, plan['topic_code'])
            if origin['as_of'] != day: raise ValueError('板块旧缓存日期不符')
            result = _read_cached(origin, calendar)
            if result is not None: return result
        except (OSError, ValueError, TypeError, AttributeError):
            invalid = True
    if invalid: raise ValueError('已存在板块旧缓存但原始SHA链未通过，未静默改成全量下载')
    return None


def collect_sector_chart_incremental(market, plan, adapter, guard=None, progress=None):
    """Append only (last verified cache day,T]; retain one full immutable base.

    Thin daily receipts reference the previous normalized object and one tail
    artifact. Each leaf remains independently SHA-verifiable in backup's normal
    accepted namespaces; old source gaps are retained rather than re-fetched.
    """
    if guard: guard()
    frozen = _validate_plan(market, plan); calendar = market.calendar()
    exact = _read_cached(frozen, calendar)
    if exact is not None:
        record, pointer = exact
        if guard: guard()
        return _Collected({'available': True, 'record': record, 'coverage': record['coverage'], 'cache_hit': True,
                'artifact': pointer['artifact'], 'sha256': pointer['sha256'],
                'quality': 'complete' if record['complete'] else 'partial', 'request_kind': 'cache',
                'reused_rows': len(record['bars']), 'downloaded_rows': 0,
                'message': '该目标日板块K线已从原始链复验，未新增请求'}, exact)
    previous = _previous_cache(frozen, calendar)
    if previous is None:
        result = collect_sector_chart(market, frozen, adapter, guard, progress)
        return _Collected({**result, 'request_kind': 'cache' if result['cache_hit'] else 'full',
                'reused_rows': 0, 'downloaded_rows': 0 if result['cache_hit'] else len(result['record']['bars'])},
                getattr(result, 'verified', None))
    base, base_pointer = previous
    following = next((day for day in calendar if day > base['as_of']), None)
    if following is None or following > frozen['as_of']:
        raise ValueError('板块增量尾部缺少有效交易日窗口')
    tail_plan = {**frozen, 'start': following,
                 'request_signature': _signature(frozen['topic_code'], following, frozen['as_of'])}
    tail_key = _cache_key(frozen)+'-'+following
    tail_cached = _read_cached(tail_plan, calendar, 'sector-chart-tail', tail_key)
    tail_cache_hit = tail_cached is not None
    from app.services.market_supplement import SupplementCache
    if tail_cached is None:
        if guard: guard()
        receipt = _source_request(tail_plan)
        if progress: progress('下载板块新增日K', 25, receipt['start']+' 至 '+receipt['end'])
        if callable(getattr(adapter, 'set_chart_request_kind', None)): adapter.set_chart_request_kind('tail')
        raw = adapter.request('/api/a-share-index/prices/historical', {
            'thscode': frozen['topic_code'], 'interval': '1d', 'start': _midnight(receipt['start']),
            'end': _midnight(receipt['end'])+86400000-1})
        tail = normalize_sector_chart(raw, tail_plan, calendar, receipt)
        if guard: guard()
        cache = SupplementCache(_root())
        cache.accept('fuyao', 'sector-chart-tail', tail_key, tail_plan['request_signature'], tail, raw)
        if guard: guard()
        tail_cached = _read_cached(tail_plan, calendar, 'sector-chart-tail', tail_key)
    tail, tail_pointer = tail_cached
    if guard: guard()
    raw_manifest = {'contract': INCREMENT_CONTRACT, 'source': 'fuyao', 'topic_code': frozen['topic_code'],
        'as_of': frozen['as_of'], 'start': frozen['start'], 'request_signature': frozen['request_signature'],
        'previous_as_of': base['as_of'],
        'previous': {'artifact': base_pointer['artifact'], 'sha256': base_pointer['sha256']},
        'tail': {'artifact': tail_pointer['artifact'], 'sha256': tail_pointer['sha256']}}
    # Compute coverage in memory; persist only a thin receipt, not all base bars.
    combined = [row for row in base['bars']+tail['bars'] if frozen['start'] <= row['date'] <= frozen['as_of']]
    dates = [row['date'] for row in combined]
    if len(dates) != len(set(dates)): raise ValueError('板块增量不得覆盖或重复旧日期')
    record = normalize_sector_chart(_payload_from_bars(frozen['topic_code'], combined), frozen, calendar)
    compact = {key: value for key, value in record.items() if key not in ('bars', 'coverage')}
    compact.update(storage='thin-increment-v1', fetched_at=tail.get('fetched_at'),
        coverage={key: value for key, value in record['coverage'].items() if key != 'missing_dates'})
    if guard: guard()
    cache = SupplementCache(_root())
    cache.accept('fuyao', 'sector-chart', _cache_key(frozen), frozen['request_signature'], compact, raw_manifest)
    if guard: guard()
    cached = _read_cached(frozen, calendar)
    verified, pointer = cached
    if guard: guard()
    if progress: progress('板块增量已保存', 100, '仅保存新增日线及小型原始链证明，旧内部缺口保留')
    return _Collected({'available': True, 'record': verified, 'coverage': verified['coverage'],
            'artifact': pointer['artifact'], 'sha256': pointer['sha256'],
            'cache_hit': tail_cache_hit,
            'request_kind': 'cache' if tail_cache_hit else 'tail',
            'reused_rows': sum(row['date'] >= frozen['start'] for row in base['bars']),
            'downloaded_rows': 0 if tail_cache_hit else len(tail['bars']),
            'quality': 'complete' if verified['complete'] else 'partial',
            'message': '板块K线已以尾部原始报价增量更新；完整原始SHA链可复验'}, cached)


def _indicator_gaps(bars):
    gaps = []
    previous_gap_position = None
    for position, row in enumerate(bars):
        fields = [field for field in ('macd_dif', 'macd_dea', 'macd_hist', 'kdj_k', 'kdj_d', 'kdj_j') if row.get(field) is None]
        if not fields: continue
        reason = row.get('indicator_warmup_reason') or '价格不可比或预热不足'
        if (gaps and previous_gap_position == position-1
                and gaps[-1]['reason'] == reason and gaps[-1]['affected_fields'] == fields):
            gaps[-1]['end_date'] = row['date']; gaps[-1]['bars'] += 1
        else:
            gaps.append({'start_date': row['date'], 'end_date': row['date'], 'bars': 1,
                         'reason': reason, 'affected_fields': fields})
        previous_gap_position = position
    return gaps


def read_sector_chart(market, topic_code, as_of, period='D'):
    """Offline chart compatible with MarketData.chart, including honest gaps."""
    period = str(period).upper()
    if period not in ('D', 'W', 'M'): raise ValueError('仅支持日K、周K和月K')
    plan = plan_sector_chart(market, topic_code, as_of)
    calendar = market.calendar()
    from app.analytics.chart_session_distance import attach_chart_session_distance
    base = {'instrument': plan['instrument'], 'period': period, 'as_of': plan['as_of'],
            'bars': [], 'snapshot_id': plan['snapshot_id'], 'available': False,
            'requested_price_basis': 'none', 'price_basis': 'none',
            'price_basis_options': [{'value': 'none', 'label': '指数原始点位（不复权）', 'available': True, 'reason': None}],
            'adjustment': {'usable': False, 'reason': '板块指数没有个股复权语义，使用来源指数点位'},
            'indicator_gaps': [], 'waves': None, 'volume_unit': '来源单位（未核验）',
            'amount_unit': 'CNY', 'price_unit': '点', 'quality': 'unknown',
            'warnings': [SCOPE, '成交量单位采用来源原值，未核验为股或手；成交额单位为CNY。'],
            'indicator_version': INDICATOR_VERSION+'+'+NORMALIZATION_VERSION,
            'breadth_scope': None, 'coverage': {}, 'scope': SCOPE,
            'unavailable_reason': '该板块所选日完整K线尚未下载，请手动同步这个板块；读取不会联网'}
    try: cached = _read_chart_cache(market, plan, calendar)
    except (OSError, ValueError, TypeError, AttributeError):
        base['unavailable_reason'] = '该板块所选日K线缓存校验失败，保持未知；未回填其它日期'
        return attach_chart_session_distance(base, calendar)
    if cached is None: return attach_chart_session_distance(base, calendar)
    record, pointer = cached
    # The shared aggregators add numeric volume/amount. NaN deliberately
    # propagates missing optional data through sums; never replace it with0.
    rows = [{**row, 'volume': row['volume'] if row['volume'] is not None else float('nan'),
             'amount': row['amount'] if row['amount'] is not None else float('nan')}
            for row in record['bars']]
    if period == 'W': rows = aggregate_weekly(rows, calendar, plan['as_of'])
    if period == 'M': bars = enrich_monthly_bars(aggregate_monthly(rows, calendar, plan['as_of']), include_chart_extremes=True)
    else: bars = enrich_bars(rows, calendar if period == 'D' else None, include_chart_extremes=True)
    bars = json_clean(bars)
    warnings = list(base['warnings'])
    if not record['complete']: warnings.append(record['coverage']['notice'])
    if period == 'M': warnings.append('月K按日线聚合，形成中、首月边界或缺日报价不计完整预热；MACD/KDJ需要35根可比月柱。')
    if period == 'W': warnings.append('周K按日线聚合，形成中或缺日报价会阻断完整周与指标预热。')
    result = {**base, 'available': True, 'unavailable_reason': None, 'bars': bars,
            'quality': 'complete' if record['complete'] else 'partial', 'warnings': warnings,
            'coverage': record['coverage'], 'fetched_at': record.get('fetched_at'),
            'cache_as_of': record.get('cache_as_of', plan['as_of']),
            'cache_source_request': record.get('cache_source_request', record.get('source_request')),
            'raw_lineage': record.get('raw_lineage'),
            'projected_from_later_cache': record.get('projected_from_later_cache', False),
            'raw_sha256': record.get('raw_sha256'), 'artifact': pointer['artifact'],
            'indicator_gaps': _indicator_gaps(bars),
            'waves': candidate_waves(bars, threshold_pct=2.0 if period == 'D' else 4.0 if period == 'W' else 8.0)}

    return attach_chart_session_distance(result, calendar, same_snapshot=market.status().get('snapshot_id') == plan['snapshot_id'])
