"""Bounded, on-demand AI reads. No SQL/path input, collection or TTL mutation.

The optional deployment SQLite is used only for saved intraday observations.
Published prices and research always belong to the current instance. Every
returned identity describes this invocation, independent of an old chat anchor.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from contextlib import contextmanager
from datetime import date, datetime, time
import json
import os
from pathlib import Path
import re
import sqlite3

from fastapi import HTTPException

from app.core import digest, dumps
from app.infrastructure.config import data_dir, sqlite_path
from app.services import ai_contexts as contexts
from app.services import ai_market_brief as briefs
from app.services.ai_tool_budget import DETAIL_SECTIONS, GAINERS_LOOKUP_MODES, INTRADAY_SLOTS, TREND_PERIODS, TREND_WINDOWS, classify_tool
from app.services.sync_clock import CHINA, local_now

MAX_RESULT_BYTES = 24 * 1024
MAX_REPORT_BYTES = 8 * 1024 * 1024
SECTIONS = ('availability', 'auction', 'noon', 'close', 'overview', 'fund_flow')
PHASES = {'auction': ('09:25', '竞价结束'), 'noon': ('11:30', '午盘'), 'close': ('15:00', '收盘')}
_BRIEF_KIND = {'auction': 'auction', 'noon': 'noon', 'close': 'noon_close'}
MAX_DETAIL_ROWS = 30


def tool_schemas():
    """Responses function tools; names/arguments never accept filesystem/SQL."""
    specs = [
        ('read_market_data', '读取本地已保存的指定日盘面；today按服务器北京时间解析。overview优先完成日广度/指数/成交额及各时点可用性；无完成日行情时附最新已存时点摘要。比较竞价/午间/收盘应分别读auction/noon/close。noon自动串同日竞价、截至午评实际结束已完成的上午观察与午评本体；close串联截至原正式收盘的竞价、盘中各档、午评及正式封板/炸板池，缺档保持未知。密集累计按phase_fact_columns解析values/rows和省略计数数组，null未知。auction包含已核验竞价发现和10:00同股开盘对照（如已保存），覆盖只限异动样本。未到时点与已到但未采集分开；成交额不是净流入。',
         {'date': {'type': 'string', 'description': '用户明确日期YYYY-MM-DD，或today；今天不沿用旧页面日期'},
          'section': {'type': 'string', 'enum': list(SECTIONS)}}, ['date', 'section']),
        ('read_intraday_data', '读取所选日期盘中观察的已保存快照，包含盘中新方向、延续、走弱、板块指数和个股样本、封炸板与成交额，以及相邻时点新增封板/跌停/炸板、封板打开和炸板回封的已核验变化；partial只是下界，unknown不是0，新入池不等于首次涨停。研究盘中情况或盘中轮动且未指定时段用all，一次返回各时点有界摘要。指定时点用对应键；北京时间09:35用0935，09:45用0945，10:00用1000，10:30用1030，11:00用1100，13:30用1330，14:00用1400，14:30用1430。竞价、午评和收盘用read_market_data。',
         {'date': {'type': 'string', 'description': 'YYYY-MM-DD或today'},
          'slot': {'type': 'string', 'enum': list(INTRADAY_SLOTS)},
          'query': {'type': 'string', 'description': '题材或股票名称、代码，最多80字；整体研究传空串'}},
         ['date', 'slot', 'query']),
        ('read_stock_research', '读取当前实例已保存且未过期的公司研究，包括已核验的主营、财报与现金流、估值市值、股东户数和公告线索。不联网、不延长缓存。instrument_id空串沿用当前页面个股；仅无当前报告时可读同股旧会话材料并明确旧日期。',
         {'instrument_id': {'type': 'string', 'description': '证券准确名称、六位代码或完整六位代码加.SH/.SZ/.BJ，最多80字符；名称和六位代码仅经本地名录精确消歧，空串沿用页面个股'}}, ['instrument_id']),
        ('check_data_availability', '只检查指定日已保存资料及北京时间观察阶段，不取新数据、不把工作日猜作已核验交易日。',
         {'date': {'type': 'string', 'description': 'YYYY-MM-DD或today'}}, ['date']),
        ('read_stock_quote', '按证券名称、六位代码或完整代码，读取本地指定完成交易日的个股原始报价和成交额；名称必须经本地名录消歧，不用不存在的代码或旧日价格代替。不需要先有研究报告，不联网。',
         {'date': {'type': 'string', 'description': 'YYYY-MM-DD或today'},
          'instrument_id': {'type': 'string', 'description': '证券名称、六位代码或完整代码，最多80字符；空串沿用当前页面证券'}}, ['date', 'instrument_id']),
        ('read_stock_trend', '读取一只已核实A股最近已发布K线与量价、技术指标、涨跌停及可核验的板块对照。不读取实时分时。近期走势默认日线30根；明确周月线默认12根。无指定截止日用latest，明确今天用today。',
         {'date': {'type': 'string', 'description': 'YYYY-MM-DD、today或latest'},
          'instrument_id': {'type': 'string', 'description': '证券准确名称、六位代码或完整代码。承接前一条用户股票主题时传其明确名称；空串仅用于绑定的页面标的'},
          'period': {'type': 'string', 'enum': list(TREND_PERIODS)},
          'count': {'type': 'string', 'enum': list(TREND_WINDOWS)}},
         ['date', 'instrument_id', 'period', 'count']),
        ('read_limit_up_data', '读取指定完成日已保存的涨停梯队、各连板高度名单、首板和最高板、反包/晋级失败/20cm及题材确认家数。复用与页面相同的日期及价格核验，来源不全时数量只给已确认下界，不能称全市场完整统计。',
         {'date': {'type': 'string', 'description': 'YYYY-MM-DD或today'}}, ['date']),
        ('read_market_detail', '读取系统已保存的盘面页面事实：指数、板块表现、情绪及次日观察条件、近30交易日涨幅榜、历史监管监控、龙虎榜、竞价及午评明细、炸板分析。auction/noon读取对应时点已存个股、方向、题材分布及封炸跌停池，query先筛完整已留存来源再有界返回，不要求今天已收盘。dragon_tiger分别返回单日和三日股票/机构/游资榜；query唯一精确命中股票名称或代码时另附对应席位，匿名席位不合并、金额不跨区间相加。abnormal保留监控标签、来源观察日与期限；供应商区间不是官方监管期限。gainers_related按真实分类名称关键词筛完整前1000并取股票并集，gainers_topic按真实topic_id等值筛选；两者query必填。其他分区query用于名称或代码筛选，空串不筛选。返回有界名单，区分已观察数、确认总数及截取范围；观察条件不是确定预测，板块成交额不是净流入。',
         {'date': {'type': 'string', 'description': 'YYYY-MM-DD或today；仅gainers_related/gainers_topic可用latest读取最新已发布完成日，明确问今天用today，不回退旧日。'},
          'section': {'type': 'string', 'enum': list(DETAIL_SECTIONS)},
          'query': {'type': 'string', 'description': '最多80字符。gainers_related传分类名称关键词，如电池；gainers_topic传真实topic_id。其他分区不筛选传空串。'}}, ['date', 'section', 'query']),
    ]
    return [{'type': 'function', 'name': name, 'description': description, 'strict': True,
             'parameters': {'type': 'object', 'properties': properties, 'required': required,
                            'additionalProperties': False}}
            for name, description, properties, required in specs]


def _connect(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        return None
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    for statement in ('PRAGMA foreign_keys=ON', 'PRAGMA recursive_triggers=ON',
                      'PRAGMA busy_timeout=10000', 'PRAGMA query_only=ON'):
        connection.execute(statement)
    return connection


def _paths():
    current = sqlite_path()
    configured = os.environ.get('SHIXU_AI_READ_SQLITE_PATH')
    result = [('configured_read_source', Path(configured).expanduser().resolve())] if configured else []
    if not result or result[0][1] != current:
        result.append(('current_instance', current))
    return result


@contextmanager
def _business_read_connection():
    """Inject into page collaborators without app.connect/migration side effects."""
    connection = _connect(sqlite_path())
    if connection is None:
        raise ValueError('没有可读取业务元数据')
    try:
        yield connection
    finally:
        connection.close()


def _day(value, now):
    if value == 'today':
        return now.date().isoformat()
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('日期须为YYYY-MM-DD或today')
    return value


def _result(name, day=None):
    return {'ok': True, 'state': 'unknown', 'tool_name': name, 'date': day,
            'source': [], 'summary': {}, 'evidence': [], 'metrics': [],
            'gaps': [], 'source_bindings': {}}


def _gap(result, capability, reason, state='unknown'):
    result['gaps'].append({'capability': capability, 'date': result['date'], 'state': state, 'reason': reason})


def _settings(path):
    connection = _connect(path)
    if connection is None:
        return {}
    try:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_setting'").fetchone():
            return {}
        rows = connection.execute("SELECT key,value_json FROM platform_setting WHERE key IN "
            "('automatic-review-calendar-v1','automatic-review-config-v1') AND length(CAST(value_json AS BLOB))<=131072").fetchall()
        result = {row['key']: json.loads(row['value_json']) for row in rows}
        return {key: value for key, value in result.items() if isinstance(value, dict)}
    finally:
        connection.close()


def _heads():
    connection = _connect(sqlite_path())
    if connection is None:
        return {}
    try:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='published_head'").fetchone():
            return {}
        rows = connection.execute("SELECT h.module,h.snapshot_id,s.lineage_json FROM published_head h "
            "JOIN dataset_snapshot s ON s.snapshot_id=h.snapshot_id WHERE h.module IN "
            "('calendar','daily_raw','analysis_bars','corporate_actions','security_identity')").fetchall()
        return {row['module']: {'snapshot_id': row['snapshot_id'],
                'market_snapshot_id': json.loads(row['lineage_json']).get('market_snapshot_id')}
                for row in rows}
    finally:
        connection.close()


def _supplement_head():
    from app.infrastructure.config import published_dir
    path = published_dir() / 'market-supplement' / 'head.json'
    if not path.is_file():
        return None
    if path.stat().st_size > 8192:
        raise ValueError('补充发布指针超过有界范围')
    body = json.loads(path.read_text())
    return {key: body.get(key) for key in ('snapshot_id', 'sha256', 'version', 'published_at_ms')}


def _market(heads=None):
    from app.analytics.market import MarketData
    heads = _heads() if heads is None else heads
    snapshot = heads.get('daily_raw', {}).get('market_snapshot_id')
    return MarketData(data_dir(), snapshot_id=snapshot or '')


def _availability(day, now, *, allow_market_calendar=True):
    selected, settings, all_heads = {}, [], {}
    for label, path in _paths():
        identity = briefs.fingerprints(day, source_path=path)
        all_heads[label] = identity
        settings.append(_settings(path))
        for phase, key in _BRIEF_KIND.items():
            metadata = identity.get(key) or (identity.get('closing_pools') if phase == 'close' else None)
            if phase not in selected and metadata:
                selected[phase] = {'source': label, **metadata}
    calendar_state, calendar_binding = 'unknown', None
    for saved in settings:
        calendar = saved.get('automatic-review-calendar-v1', {})
        days = calendar.get('days', [])
        if (calendar.get('state') == 'ready' and re.fullmatch(r'[0-9a-f]{64}', str(calendar.get('source_sha256', '')))
                and isinstance(days, list) and 0 < len(days) <= 4096
                and all(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) for value in days)):
            if day in days or min(days) <= day <= max(days):
                calendar_state = 'trading_day' if day in days else 'rest_day'
                calendar_binding = {key: calendar.get(key) for key in ('source_id', 'source_sha256', 'fetched_at')}
                break
    if selected:
        calendar_state = 'trading_day'
    if calendar_state == 'unknown' and allow_market_calendar:
        market = _market()
        if market.status().get('ready') and day in market.calendar():
            calendar_state = 'trading_day'
            calendar_binding = {'market_snapshot_id': market.status().get('snapshot_id')}
    phases = {}
    for phase, (clock, label) in PHASES.items():
        due = datetime.combine(date.fromisoformat(day), time.fromisoformat(clock), CHINA)
        metadata = selected.get(phase)
        if metadata:
            state, reason = 'available', '已有所选日保存记录；实际内容还须通过SHA及来源权限核验'
        elif calendar_state == 'rest_day':
            state, reason = 'rest_day', '已保存并核验的交易日历明确为休市日'
        elif now < due and day >= now.date().isoformat():
            state, reason = 'not_due', label + '观察时点尚未到，不能称为漏采'
        elif day == now.date().isoformat() or calendar_state == 'trading_day':
            state, reason = 'not_collected', '已到' + label + '观察时点，但所选日尚无已保存记录'
        else:
            state, reason = 'unknown', '没有所选日记录或覆盖该日的已核验交易日历，不能猜测休市或漏采'
        config_key = 'after_close' if phase == 'close' else phase
        config = next((s.get('automatic-review-config-v1', {}).get(config_key) for s in settings
                       if isinstance(s.get('automatic-review-config-v1', {}).get(config_key), dict)), {}) or {}
        scheduled = config.get('time') if config.get('enabled') is True else None
        if state == 'not_collected' and isinstance(scheduled, str) and re.fullmatch(r'\d{2}:\d{2}', scheduled) and now < datetime.combine(date.fromisoformat(day), time.fromisoformat(scheduled), CHINA):
            reason += '；已配置计划采集时间尚未到'
        phases[phase] = {'phase_state': state, 'now': now.isoformat(), 'expected_phase_at': due.isoformat(),
            'capture_window': ({key: metadata[key] for key in ('collected_start_at', 'collected_end_at',
                'noon_collected_start_at', 'noon_collected_end_at', 'generated_at_ms') if key in metadata} if metadata else None),
            'source_ids': ({key: metadata[key] for key in ('snapshot_id', 'review_id', 'revision') if key in metadata} if metadata else {}),
            'source': metadata.get('source') if metadata else None, 'scheduled_capture_at': scheduled, 'reason': reason}
    return {'now': now.isoformat(), 'timezone': 'Asia/Shanghai', 'calendar_state': calendar_state,
            'calendar_binding': calendar_binding, 'phases': phases}, selected, all_heads


def _add_blocks(result, day, selected, kinds, *, cutoff=None, include_auction_opening=True):
    for label, path in _paths():
        wanted = {kind for kind in kinds if selected.get(kind, {}).get('source') == label}
        if not wanted:
            continue
        mapping = {_BRIEF_KIND[phase]: phase for phase in wanted}
        if 'auction' in wanted:
            mapping['auction_discovery'] = 'auction'
        if 'close' in wanted:
            mapping['closing_pools'] = 'close'
        data = briefs.build_briefs(day, source_path=path, kinds=set(mapping), cutoff=cutoff,
                                   include_auction_opening=include_auction_opening)
        for block in data['blocks']:
            key = block['evidence']['evidence_id'].split(':')[-1]
            if key not in mapping:
                continue
            evidence = block['evidence']
            if key == 'closing_pools':
                result['source_bindings']['closing_pools'] = evidence['source_binding']
            identifier = evidence['evidence_id']
            result['evidence'].append(evidence)
            result['metrics'].extend({**metric, 'evidence_ids': [identifier]} for metric in block['metrics'])
        for gap in data['gaps']:
            if gap.get('capability', '').removesuffix('_brief') in mapping:
                result['gaps'].append(gap)


def _published_overview(result, day):
    before = _heads()
    supplement_before = _supplement_head()
    market = _market(before)
    status = market.status()
    if not status.get('ready') or day not in market.calendar():
        _gap(result, 'completed_market', '当前实例没有所选日完成日行情；不以旧日行情代称今天')
        return False
    from app.services.market_sentiment import describe_sentiment
    from app.services.market_supplement import _load_supplement_with_fingerprint
    calendar = market.calendar()
    position = calendar.index(day)
    previous = calendar[position - 1] if position else None
    dates = tuple(value for value in (previous, day) if value)
    pools, fingerprint = _load_supplement_with_fingerprint(status['snapshot_id'], dates=dates,
        fields=('public_pools', 'annual_pools', 'annual_down_pools'))
    reference = {**market.reference(), '_supplement': pools}
    description = describe_sentiment(market, reference, market.panel(day), day)
    context = contexts._base('market_review', '所选日已发布盘面', day)
    contexts._dimension(context, 'market_distribution', '当日严格可比A股分布', description['market'], day)
    for item in description['index_environment']['indices']:
        contexts._dimension(context, 'index:' + item['instrument_id'], item['name'], item, day)
    contexts._turnover(context, market, day, calendar)
    short = description['short_term']
    contexts._dimension(context, 'previous_limit_up', '前日已确认涨停队列反馈',
        {key: value for key, value in short.items() if key != 'groups'}, day,
        dates={'trade_date': day, 'cohort_date': previous})
    for group in short.get('groups', []):
        if group.get('cohort_price_feedback', {}).get('observed_count'):
            contexts._dimension(context, 'cohort:' + group['id'], group['label'] + '价格反馈',
                group['cohort_price_feedback'], day, dates={'trade_date': day, 'cohort_date': previous})
    sectors, sector_fp = _load_supplement_with_fingerprint(status['snapshot_id'], dates=(day,), fields=('sector_index_history',))
    contexts._sectors(context, sectors, day)
    result['evidence'].extend(context['evidence'])
    result['metrics'].extend(context['metrics'])
    result['gaps'].extend(context['gaps'])
    result['source'] = sorted(set(result['source']) | {'current_instance_published_market'})
    result['source_bindings']['published_market'] = {'heads': before, 'market_snapshot_id': status['snapshot_id'],
        'pool_sha256': digest(fingerprint) if fingerprint else None,
        'sector_sha256': digest(sector_fp) if sector_fp else None}
    if before != _heads() or supplement_before != _supplement_head():
        raise HTTPException(409, '读取期间发布代际变化，请重试')
    return True


def _fund_flow(result, day):
    from app.services.market_supplement import _load_supplement_with_fingerprint
    from app.services.market_service import fund_flow_for_date
    before = _heads()
    supplement_before = _supplement_head()
    market = _market(before)
    snapshot = market.status().get('snapshot_id')
    document, fingerprint = _load_supplement_with_fingerprint(snapshot, dates=(day,), fields=('fund_flows',))
    data = fund_flow_for_date(document, day, market.calendar()) if fingerprint else {}
    rows = [row for row in data.get('items', []) if row.get('date') == day
            and row.get('egress_allowed') is not False and row.get('ai_processing_allowed') is not False]
    if not rows:
        _gap(result, 'net_flow', '所选日没有通过日期与来源核验的已保存净流入材料；不以成交额替代')
        return
    if data.get('egress_allowed') is False or data.get('ai_processing_allowed') is False:
        _gap(result, 'net_flow', '来源明确禁止外发或AI处理')
        return
    rows = sorted(rows, key=lambda row: (not contexts._finite(row.get('net_inflow')), -(row.get('net_inflow') or 0)))
    chosen = rows[:3] + [row for row in rows[-3:] if row not in rows[:3]]
    coverage = data.get('coverage', {})
    data = {key: data.get(key) for key in ('date', 'source', 'calculation', 'date_evidence', 'complete')}
    data['coverage'] = {key: coverage.get(key) for key in ('requested_date', 'historical_series_anchor',
        'directory_count', 'accepted_count', 'failed_count', 'complete', 'scope_label') if key in coverage}
    data.update(items=chosen, observed_count=len(rows), selection_scope='已保存来源行业主力净流入最高/最低各3项；非完整历史目录，非真实机构账户流向',
                intraday_sequence_available=False, unit='CNY')
    identifier = 'fund_flow'
    result['evidence'].append({'evidence_id': identifier, 'source': 'published_fund_flow', 'kind': identifier,
        'title': '指定日来源行业主力净流入', 'dates': {'trade_date': day}, 'data': data})
    result['source'] = sorted(set(result['source']) | {'current_instance_published_fund_flow'})
    for index, row in enumerate(chosen):
        result['metrics'].append({'metric_id': 'fund_flow:' + str(index), 'label': str(row.get('name') or row.get('topic_code')) + '来源主力净流入',
            'value': row.get('net_inflow'), 'unit': 'CNY', 'as_of': day, 'evidence_ids': [identifier],
            'scope': data['selection_scope'], 'dates': {'trade_date': day}})
    result['source_bindings']['fund_flow'] = {'projection_sha256': digest(fingerprint), 'heads': before}
    if before != _heads() or supplement_before != _supplement_head():
        raise HTTPException(409, '读取期间发布代际变化，请重试')


def _pick_fields(value, keys):
    return {key: value[key] for key in keys if isinstance(value, dict) and key in value}


def _denied(value):
    return isinstance(value, dict) and (value.get('egress_allowed') is False or value.get('ai_processing_allowed') is False)


def _source_denied(value):
    if _denied(value): return True
    if isinstance(value, dict): return any(_source_denied(item) for item in value.values())
    if isinstance(value, (list, tuple)): return any(_source_denied(item) for item in value)
    return False


def _coverage(value):
    """Small coverage metadata, never expand a whole historical-date catalogue."""
    value = value if isinstance(value, dict) else {}
    result = _pick_fields(value, ('date', 'label', 'state', 'scope', 'basis', 'reason', 'notice', 'complete',
        'partial', 'start', 'end', 'selected_date_available', 'source_snapshot_id', 'source_snapshot_day',
        'candidate_count', 'confirmed_count', 'unknown_count', 'source_pool_complete', 'members_complete',
        'membership_available', 'examined_count', 'known_false_count', 'source_scope_complete',
        'catalog_historical', 'requested_count', 'accepted_count', 'failed_count', 'observed_count'))
    if isinstance(value.get('available_dates'), list):
        result['available_date_count'] = len(value['available_dates'])
    return result


def _table(rows, *, maximum=MAX_DETAIL_ROWS, total=None, scope='已保存页面样本；名单有界返回'):
    rows = [row for row in rows if isinstance(row, dict) and not _denied(row)]
    return {'items': rows[:maximum], 'observed_count': len(rows) if total is None else total,
            'returned_count': min(len(rows), maximum), 'truncated': len(rows) > maximum or total is not None and total > maximum,
            'scope': scope}


def _fit_detail(data):
    """Remove whole display rows only; exact counts and coverage are never cut."""
    def tables(value):
        if isinstance(value, dict):
            if isinstance(value.get('items'), list) and 'returned_count' in value:
                yield value
            for item in value.values(): yield from tables(item)
        elif isinstance(value, list):
            for item in value: yield from tables(item)
    while len(dumps(data).encode()) > 17 * 1024:
        choices = [value for value in tables(data) if value['items']]
        if not choices:
            raise ValueError('必要盘面元数据超出有界容量')
        selected = max(choices, key=lambda value: len(dumps(value['items']).encode()))
        selected['items'].pop(); selected['returned_count'] = len(selected['items']); selected['truncated'] = True
    return data


def _stock_fields(row, topic_query=''):
    value = _pick_fields(row, ('instrument_id', 'code', 'name', 'date', 'close', 'change_pct', 'amount',
        'rank', 'change_30d_pct', 'change_30d_reason', 'change_30d_coverage', 'turnover_rate',
        'return_30d_start_quote_date', 'return_30d_end_quote_date', 'return_30d_quality',
        'first_limit_time', 'last_limit_time', 'break_count', 'source_industry', 'details_source',
        'limit_up_state', 'previous_board_count', 'previous_board_date', 'feedback', 'reason',
        'unverified_reason', 'limit_up_ytd', 'limit_up_ytd_observed', 'limit_up_ytd_reason',
        'topics_observed_date', 'topics_scope'))
    if row.get('return_30d_observation_suspended') is True or isinstance(row.get('suspension'), dict):
        value['trading_status'] = 'suspended'
    height = (row.get('board_presentation') or {}).get('height', {})
    value['board_count'] = height.get('value') if height.get('state') == 'confirmed' else None
    value['board_count_state'] = height.get('state', 'unknown')
    value['reported_board_count'] = row.get('board_count')
    if height.get('state') != 'confirmed':
        value['board_count_reason'] = height.get('reason')
    topics = row.get('topics') if isinstance(row.get('topics'), list) else []
    if topic_query:
        topics = sorted(topics, key=lambda item: topic_query.casefold() not in ' '.join(
            str(item.get(key, '')) for key in ('topic_id', 'name')).casefold() if isinstance(item, dict) else True)
    value['topics'] = [_pick_fields(item, ('topic_id', 'name', 'kind', 'observed_date', 'source_observed_date', 'source'))
                       for item in topics[:3] if isinstance(item, dict)]
    value['topics_truncated'] = len(topics) > 3
    return value


def _sentiment_data(value):
    """Keep the three distinct statistics, without repeating full audit trees."""
    scalar_keys = ('label', 'state', 'state_label', 'scope', 'date', 'previous_date', 'group_id', 'id',
        'sample_count', 'observed_count', 'unknown_count', 'coverage_pct', 'cohort_source_complete',
        'identity_complete', 'promotion_unknown_count', 'previous_unverified_count', 'default_group_id',
        'promotion_bounds_pct', 'promotion_bounds_reason', 'current_identity_unknown_count', 'price_basis',
        'cohort_date', 'notice')
    def observation(item):
        item = item if isinstance(item, dict) else {}
        data = _pick_fields(item, scalar_keys)
        data['metrics'] = [_pick_fields(row, ('key', 'label', 'value', 'unit', 'formula', 'reason'))
                           for row in item.get('metrics', []) if isinstance(row, dict)]
        return data
    result = _pick_fields(value, ('model', 'legacy_cohort_model', 'date', 'scope', 'atmosphere', 'notice'))
    result['market'] = observation(value.get('market'))
    environment = value.get('index_environment') or {}
    result['index_environment'] = {**observation(environment), 'indices': _table([
        {**observation(row), **_pick_fields(row, ('instrument_id', 'name'))}
        for row in environment.get('indices', [])], maximum=8)}
    short = value.get('short_term') or {}
    groups = [{**observation(row), 'cohort_price_feedback': observation(row.get('cohort_price_feedback'))}
              for row in short.get('groups', []) if isinstance(row, dict)]
    result['short_term'] = {**observation(short), 'groups': _table(groups, maximum=8)}
    return result


def _bind_detail(result, day, data, heads, supplement):
    if _denied(data):
        _gap(result, 'source_permission', '所选盘面来源明确禁止AI处理或外发')
        return
    if data.get('date') != day:
        raise ValueError('盘面返回日期与请求不符')
    expected = heads.get('daily_raw', {}).get('market_snapshot_id')
    if expected and data.get('snapshot_id') != expected:
        raise HTTPException(409, '盘面返回代际与读取前发布头不符')
    # Bind the exact page projection and immutable market generation. No private
    # filesystem paths or provider credentials enter the model-visible body.
    result['source'] = ['current_instance_published_market']
    result['source_bindings']['published_detail'] = {'heads': heads, 'supplement_head': supplement,
        'market_snapshot_id': data.get('snapshot_id'), 'projection_sha256': digest(data)}
    if heads != _heads() or supplement != _supplement_head():
        raise HTTPException(409, '读取期间发布代际变化，请重试')


def _completion_gap_state(day, now, calendar):
    # A future weekday or an unverified calendar cannot prove the session phase.
    return ('not_due' if day == now.date().isoformat() and day in calendar
            and now < datetime.combine(date.fromisoformat(day), time(15), CHINA) else 'unknown')


def _completed_market(result, day, now):
    heads, supplement = _heads(), _supplement_head()
    market = _market(heads); status = market.status()
    calendar = market.calendar()
    requested_day = day
    if requested_day == 'latest':
        day = status.get('data_end') if isinstance(status.get('data_end'), str) else None
        result['date'] = day
    result['summary'] = {'requested_date': requested_day, 'latest_completed_date': status.get('data_end'),
                         'now': now.isoformat(), 'timezone': 'Asia/Shanghai'}
    if (not status.get('ready') or day not in calendar
            or not isinstance(status.get('data_end'), str) or day > status['data_end']
            or status.get('data_begin') and day < status['data_begin']):
        phase = _completion_gap_state(day, now, calendar)
        _gap(result, 'completed_market', '所选日没有已发布完成日行情；未到收盘时点时不把午间或竞价报价称为收盘报价', phase)
        result['state'] = phase
        return None
    if _denied(status) or _denied(market.reference()):
        _gap(result, 'source_permission', '所选行情来源明确禁止AI处理或外发')
        return None
    return market, status, heads, supplement


def _stock_identity(result, market, status, identifier, day, page):
    # Resolve only the local small identity catalogue before reading prices.
    term = identifier.strip() or page.get('instrument_id') or (page.get('instrument') or {}).get('instrument_id') or ''
    if not isinstance(term, str) or not 1 <= len(term) <= 80 or any(ord(c) < 32 for c in term):
        raise ValueError('证券名称或代码无效')
    if _denied(status) or _denied(market.reference()):
        _gap(result, 'source_permission', '所选行情名录明确禁止AI处理或外发'); return
    identities = [row for row in market.instruments(query=term, limit=10000)
                  if row.get('kind', 'stock') in ('stock', 'equity', 'a_share', 'A')
                  and re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', str(row.get('instrument_id', '')))]
    normalized = term.upper()
    matches = [row for row in identities if normalized in (row['instrument_id'].upper(), row['instrument_id'][:6])
               or term == row.get('name')]
    result['summary'].update({'requested_instrument': term, 'requested_date': day,
        'identity_state': 'resolved' if len(matches) == 1 else 'ambiguous' if len(matches) > 1 else 'not_found',
        'candidates': _table([_pick_fields(row, ('instrument_id', 'name', 'kind')) for row in identities], maximum=8),
        'latest_completed_date': status.get('data_end')})
    if len(matches) != 1:
        _gap(result, 'instrument_identity', '本地名录没有唯一准确证券身份；不能猜代码或用近似名称的其他证券代替')
        return
    identity = matches[0]
    result['summary']['instrument'] = _pick_fields(identity, ('instrument_id', 'name', 'kind', 'list_date', 'end_date'))
    result['summary']['identity_name_scope'] = '名称来自当前本地证券名录；同证券历史行情不据此证明当时名称或行业归属'
    return identity


def _stock_trend(result, args, day, page, now):
    from app.services.ai_stock_trend import project_stock_trend
    available = _completed_market(result, day, now)
    if available is None:
        return
    market, status, heads, supplement = available
    actual_day = result['date']
    identity = _stock_identity(result, market, status, args['instrument_id'], day, page)
    if identity is None:
        return
    if _denied(identity):
        _gap(result, 'source_permission', '所选证券行情来源明确禁止AI处理或外发')
        return
    project_stock_trend(result, market, status, actual_day, identity, args['period'], int(args['count']),
                        reference=market.reference())
    _bind_detail(result, actual_day, {'date': actual_day, 'snapshot_id': status.get('snapshot_id'),
                 'summary': result['summary'], 'evidence': result['evidence'], 'gaps': result['gaps']},
                 heads, supplement)


def _stock_quote(result, identifier, day, page, now):
    heads, supplement = _heads(), _supplement_head()
    market = _market(heads); status = market.status()
    identity = _stock_identity(result, market, status, identifier, day, page)
    if identity is None:
        return
    symbol = identity['instrument_id']
    calendar = market.calendar()
    if (not status.get('ready') or day not in calendar
            or not isinstance(status.get('data_end'), str) or day > status['data_end']
            or status.get('data_begin') and day < status['data_begin']):
        phase = _completion_gap_state(day, now, calendar)
        _gap(result, 'dated_quote', '所选日没有已发布的完成日行情；今日未收盘时尚无收盘报价，不把旧日或午间报价称为当日收盘', phase)
        result['state'] = phase
        return
    if _denied(status) or _denied(market.reference()) or _denied(identity):
        _gap(result, 'source_permission', '所选证券行情来源明确禁止AI处理或外发')
        return
    pos = calendar.index(day); previous = calendar[pos-1] if pos else None
    histories = market.history_for_instruments([symbol], start=previous or day, end=day).get(symbol, [])
    rows = [row for row in histories if row.get('date') == day and row.get('instrument_id') == symbol]
    if len(rows) != 1 or _denied(rows[0]):
        _gap(result, 'dated_quote', '所选日没有唯一可外发的同股原始报价；停牌或缺数未被猜作零成交')
        return
    quote = rows[0]
    if quote.get('price_basis', 'none') != 'none' or not contexts._finite(quote.get('close')) or quote['close'] <= 0:
        _gap(result, 'dated_quote', '同日原始收盘价格或未复权口径尚未核验')
        return
    price = _pick_fields(quote, ('open', 'high', 'low', 'close', 'volume', 'amount', 'price_basis', 'source'))
    for key in ('volume', 'amount'):
        if not contexts._finite(price.get(key)) or isinstance(price.get(key), bool) or price[key] < 0:
            price[key] = None; _gap(result, key, '同日该数值缺失或无效；未用零替代')
    price.update(instrument_id=symbol, name=identity.get('name'), date=day, price_basis='none',
        units={'open': 'CNY', 'high': 'CNY', 'low': 'CNY', 'close': 'CNY', 'volume': '股', 'amount': 'CNY'},
        observation_phase='completed_daily', notice='已保存完成日原始报价；成交额不是净流入，也不是实时逐笔成交')
    # Share the strict return/continuity proof rather than divide across missing
    # sessions or unverified corporate actions. A close/amount needs no return.
    from app.services.stock_summary import _daily_change
    price['change_pct'], price['change_pct_reason'] = _daily_change(market, symbol, day, previous, calendar, histories, quote)
    price['units']['change_pct'] = '%'
    result['summary'].update(observation_phase='completed_daily', quote_available=True)
    result['evidence'] = [{'evidence_id': 'stock_quote', 'kind': 'stock_quote', 'source': str(quote.get('source') or 'published_daily'),
        'title': (identity.get('name') or symbol) + '指定日原始行情', 'dates': {'trade_date': day, 'previous_trade_date': previous}, 'data': price}]
    result['metrics'] = [{'metric_id': 'stock_quote:' + key, 'label': label, 'value': price.get(key), 'unit': unit,
        'as_of': day, 'price_basis': 'none', 'dates': {'trade_date': day}, 'evidence_ids': ['stock_quote']}
        for key, label, unit in (('close', '收盘价', 'CNY'), ('amount', '当日成交额', 'CNY'), ('volume', '当日成交量', '股'), ('change_pct', '同日可比涨幅', '%'))]
    _bind_detail(result, day, {'date': day, 'snapshot_id': status.get('snapshot_id'), 'instrument': identity, 'quote': quote}, heads, supplement)


def _limit_up(result, day, now):
    available = _completed_market(result, day, now)
    if available is None: return
    market, status, heads, supplement = available
    from app.services.market_supplement import _load_supplement_with_fingerprint
    from app.services.market_service import dated_events
    from app.services.market_ladder import build_ladder
    calendar = market.calendar(); end = calendar.index(day)
    days = tuple(calendar[max(0, end-2):end+1])
    pools, fp = _load_supplement_with_fingerprint(status['snapshot_id'], dates=days,
        fields=('public_pools', 'annual_pools', 'annual_down_pools'))
    reference = {**market.reference(), '_supplement': pools, '_supplement_cache_source': (pools, fp)}
    relevant = [pools, *(reference.get('pool_history', {}).get(value, {}) for value in days)]
    if reference.get('pool_trade_date') in days:
        relevant.extend(reference.get(key) for key in ('limitup', 'limitdown', 'breakpool', 'pool_coverage'))
    if any(_source_denied(value) for value in relevant):
        _gap(result, 'source_permission', '指定日期事件来源明确禁止AI处理或外发；未发送名单或计数'); return
    data = build_ladder(market, status, day, reference, market.panel(day), dated_events(reference, day), include_patterns=False)
    data.update(date=day, snapshot_id=status['snapshot_id'])
    if _source_denied(data):
        _gap(result, 'source_permission', '来源明确禁止AI处理或外发'); return
    rows = [_stock_fields(row) for row in data.get('items', []) if not _denied(row)]
    heights = [row['board_count'] for row in rows if isinstance(row.get('board_count'), int) and not isinstance(row['board_count'], bool)]
    unknown = len(rows)-len(heights); coverage = _coverage(data.get('main_coverage'))
    exact = coverage.get('complete') is True and unknown == 0
    highest = max(heights) if heights else None
    highest_rows = [_pick_fields(row, ('instrument_id', 'name', 'board_count')) for row in rows if row.get('board_count') == highest] if highest else []
    result['summary'].update(confirmed_limit_up_count=len(rows) if coverage.get('complete') else None,
        confirmed_limit_up_observed_count=len(rows), first_board_count=heights.count(1) if exact else None,
        first_board_observed_count=heights.count(1), unknown_height_count=unknown,
        highest_board_count=highest if exact else None, highest_board_observed_count=highest,
        highest_board_stocks=_table(highest_rows), complete=exact, main_coverage=coverage,
        board_height_basis='同日收盘涨停及价格已核验后的来源连板数；非龙头评级；来源不全时仅为已确认样本下界')
    groups = [{'height': height, 'count': heights.count(height) if exact else None,
               'observed_count': heights.count(height), 'stocks': _table([_pick_fields(row, ('instrument_id', 'name', 'board_count'))
                    for row in rows if row.get('board_count') == height])} for height in sorted(set(heights), reverse=True)]
    projection = {'board_groups': groups, 'closing_limit_up': _table(rows),
        'unverified': _table([_stock_fields(row) for row in data.get('unverified_items', [])], maximum=8),
        'rebound': _table([_stock_fields(row) for row in data.get('rebound_items', [])], maximum=8),
        'failed': _table([_stock_fields(row) for row in data.get('failed_items', [])], maximum=8),
        'twenty_cm': _table([_stock_fields(row) for row in data.get('twenty_cm_items', [])], maximum=8),
        'classification_coverage': {key: _coverage(value) for key, value in data.get('classification_coverage', {}).items()},
        'theme_counts': _table([_pick_fields(row, ('topic_id', 'name', 'kind', 'limit_up_count', 'limit_up_observed_count',
            'complete', 'member_count', 'unknown_member_count', 'membership_observed_date', 'reason')) for row in data.get('theme_counts', [])], maximum=12),
        'theme_coverage': _coverage(data.get('theme_coverage')), 'units': {'counts': '家', 'height': '板', 'amount': 'CNY'},
        'scope': '已保存并核验的收盘涨停样本；数量完整性与名单截取分别标注，题材归属重叠不可加总'}
    if not exact: _gap(result, 'limit_up_coverage', coverage.get('reason') or '来源池、报价或来源高度未完整，已确认家数和高度仅描述已观察样本')
    result['evidence'] = [{'evidence_id': 'limit_up', 'source': 'local_verified_closing_ladder', 'kind': 'limit_up_details',
        'title': '指定日涨停梯队和分类', 'dates': {'trade_date': day}, 'data': _fit_detail(projection)}]
    _bind_detail(result, day, data, heads, supplement)
    result['source_bindings']['published_detail']['pool_projection_sha256'] = digest(fp) if fp else None


def _market_detail(result, day, section, query, now):
    if section in ('auction', 'noon'):
        from app.services.ai_review_detail import fill_review_detail
        fill_review_detail(result, day, section, query, now)
        return
    available = _completed_market(result, day, now)
    if available is None: return
    market, status, heads, supplement = available
    if day == 'latest':
        day = result['date']
    market.business_read_connection = _business_read_connection
    from app.services import market_service
    lookup_mode = GAINERS_LOOKUP_MODES.get(section)
    kind = 'gainers' if lookup_mode else 'broken-boards' if section == 'broken_boards' else section
    if section == 'dragon_tiger':
        from app.services.ai_dragon_tiger_read import read_data
        data = read_data(day, market=market, query=query)
    else:
        data = (market_service.index_overview(day, market=market) if section == 'indices' else
            market_service.market_page(kind, day, limit=1000 if section == 'sectors' else 200,
                query='' if lookup_mode or section == 'sectors' else query, market=market,
                **({'top_n': 1000} if kind == 'gainers' else {}),
                **({'gainers_lookup': {'mode': lookup_mode, 'query': query}} if lookup_mode else {}),
                **({'supplement_dates': (day,)} if section == 'sectors' else {})))
    if _source_denied(data):
        _gap(result, 'source_permission', '所选盘面来源明确禁止外发或AI处理'); return
    if data.get('date') != day: raise ValueError('盘面页面日期不符')
    if query and section == 'sectors':
        source_rows = data.get('items', [])
        complete = len(source_rows) == data.get('total')
        data = {**data, 'items': [row for row in source_rows if query.casefold() in ' '.join(
            str(row.get(key, '')) for key in ('topic_id', 'topic_code', 'name', 'source_code')).casefold()]}
        data['query_source_count'] = len(source_rows); data['query_source_complete'] = complete
        data['total'] = len(data['items'])
        if not complete:
            _gap(result, 'query_scope', '板块筛选仅覆盖页面有界返回的来源；未命中不能证明目录中不存在')
    result['summary'].update(section=section, data_coverage=_coverage(data.get('data_coverage')),
        source_snapshot_id=data.get('snapshot_id'), page_summary=data.get('summary', {}),
        query=query, quality=data.get('quality'), notice='系统已保存指定日事实；规则观察条件不是预测，交易样本不能外推全部账户盈亏')
    if section == 'sectors' and query:
        result['summary'].update(query_source_count=data['query_source_count'], query_source_complete=data['query_source_complete'])
    if data.get('unavailable_reason'): _gap(result, section, data['unavailable_reason'])
    if section == 'dragon_tiger':
        projection = data['projection']
    elif section == 'abnormal':
        from app.services.ai_abnormal_read import project_data
        projection = project_data(data, query, stock_projector=_stock_fields)
    elif lookup_mode:
        lookup = data['classification_lookup']
        rows = []
        for row in data.get('items', []):
            classifications = row['matched_classifications']
            rows.append({
                **_pick_fields(row, ('instrument_id', 'code', 'name', 'rank', 'change_30d_pct',
                                     'return_30d_start', 'return_30d_end', 'return_30d_price_basis',
                                     'return_30d_start_quote_date', 'return_30d_end_quote_date', 'return_30d_quality')),
                'quote_date': None if row.get('return_30d_observation_suspended') else day,
                'quote_source': row.get('source') or status.get('source'),
                **({'trading_status': 'suspended'} if row.get('return_30d_observation_suspended') else {}),
                'matched_classifications': classifications[:5],
                'matched_classification_count': len(classifications),
                'matched_classifications_truncated': len(classifications) > 5,
            })
        projection = {
            'classification_lookup': {**lookup, 'categories': _table(lookup['categories'])},
            'stocks': _table(rows, total=lookup['observed_count'], scope='已观察到的去重匹配股票，按原始排名有界返回'),
            'scope': '固定前1000内的已留存分类；相关是分类名称匹配，不代表完整产业链或股票经营相关性',
        }
        if lookup['matched_count'] is None:
            reason = ('所选日没有可排名证券，不能判断有哪些相关股票' if lookup['state'] == 'empty_sample'
                      else '所选日无可核验的匹配分类或成员覆盖不完整；已观察数不能当作确认总数，未命中不能当作零')
            _gap(result, 'gainers_classification_coverage', reason)
    elif section == 'indices':
        rows = [_pick_fields(row, ('instrument_id', 'code', 'name', 'date', 'open', 'high', 'low', 'close', 'change_pct',
            'amount', 'source', 'price_basis', 'quote_available', 'quote_reason', 'change_pct_reason')) for row in data.get('indices', [])]
        if query: rows = [row for row in rows if query.casefold() in ' '.join(str(row.get(key, '')) for key in ('instrument_id', 'code', 'name')).casefold()]
        projection = {'indices': _table(rows), 'units': {'price': '点', 'change_pct': '%', 'amount': 'CNY'},
                      'scope': '已保存完成日指数报价；不含实时逐笔行情'}
    elif section == 'sectors':
        fields = ('topic_id', 'topic_code', 'name', 'kind', 'change_pct', 'amount', 'quote_date', 'quote_source', 'quote_basis',
            'calculation', 'member_count', 'observed_count', 'members_available', 'members_complete', 'member_quote_coverage_complete',
            'limit_up_count', 'limit_up_observed_count', 'up_count', 'down_count', 'membership_observed_date', 'catalog_historical')
        projection = {'sectors': _table([_pick_fields(row, fields) for row in data.get('items', [])], total=data.get('total')),
            'quote_coverage': _coverage(data.get('quote_coverage')), 'membership_coverage': _coverage(data.get('membership_coverage')),
            'scope': '指定日已留存板块指数；缺历史成员不抹去指数行情，涨跌停及成员数缺档保持未知；成交额不是净流入'}
    elif section == 'emotion':
        outlook = data.get('market_outlook', {})
        watch = outlook.get('watchlist', {})
        projection = {'breadth': data.get('breadth'), 'emotion': data.get('emotion'), 'sentiment': _sentiment_data(data.get('sentiment') or {}),
            'market_outlook': {**_pick_fields(outlook, ('date', 'version', 'next_session', 'stage', 'plan', 'event_counts', 'notice', 'provenance')),
                'watchlist': {**_pick_fields(watch, ('coverage', 'empty_reason', 'topics_empty_reason')),
                    'stocks': _table([_stock_fields(row) for row in watch.get('stocks', [])], maximum=12),
                    'topics': _table([{**_pick_fields(row, ('id', 'name', 'kind', 'role', 'reason', 'evidence_date', 'membership_observed_date',
                            'confirmed_limit_up_count', 'observed_limit_up_count', 'complete')),
                        'stocks': _table([_stock_fields(stock) for stock in row.get('stocks', [])], maximum=4)} for row in watch.get('topics', [])], maximum=8)}},
            'scope': '同页可比样本、描述性情绪与基于留存日事实的次日观察候选/条件；不是保证收益、确定预测或买入指令'}
    elif section == 'broken_boards':
        analysis = data.get('broken_board_analysis', {})
        projection = {**_pick_fields(analysis, ('date', 'version', 'scope', 'classification', 'coverage', 'provenance', 'warnings'))}
        for key in ('current', 'previous_feedback'):
            value = analysis.get(key, {})
            projection[key] = {**{name: item for name, item in value.items() if name not in ('items', 'concept_distribution', 'industry_distribution')},
                               'items': _table([_stock_fields(row) for row in value.get('items', [])], maximum=20)}
            for group in ('concept_distribution', 'industry_distribution'):
                projection[key][group] = _table([_pick_fields(row, ('topic_id', 'name', 'kind', 'source',
                    'classification_level', 'observed_count', 'sample_share_pct', 'cohort_count',
                    'sample_avg_change_pct', 'sample_valid_count', 'sample_unknown_count', 'sample_date',
                    'membership_date', 'membership_snapshot_day', 'sample_reason'))
                    for row in value.get(group, [])], maximum=5)
    else:
        projection = {'stocks': _table([_stock_fields(row, query) for row in data.get('items', [])], total=data.get('total')),
            'scope': '同页已保存的筛选结果；近30交易日涨幅保留未排名项，监管名单使用观察日存档而非最新名单回填历史'}
        if section == 'gainers':
            # The page computes this once before top_n/search/pagination. Never
            # recompute category shares on this bounded visible stock subset.
            projection['ranking_distribution'] = data.get('ranking_distribution', {})
            projection['scope'] = '原近30交易日涨幅榜前1000名中筛选证券或题材；分类占比仍使用未筛选的原前1000样本，归属重叠不加总'
            if projection['ranking_distribution'].get('classification_state') == 'unavailable':
                _gap(result, 'dated_classification', '该日分类成员未核验；保留原涨幅排名，不用当前题材补出历史题材占比')
    projection['warnings'] = data.get('warnings', [])
    result['evidence'] = [{'evidence_id': 'market_detail:' + section, 'source': 'local_market_page', 'kind': section,
        'title': '指定日系统盘面：' + section, 'dates': {'trade_date': day}, 'data': _fit_detail(projection)}]
    _bind_detail(result, day, data, heads, supplement)
    if section == 'dragon_tiger':
        result['source_bindings']['published_detail']['dragon_tiger'] = data['dragon_tiger_binding']
    if section == 'sectors':
        result['source_bindings']['published_detail']['supplement_dates'] = [day]


def _research_instrument(result, instrument_id, page):
    """Resolve the same exact local identities as quotes, without reading prices."""
    term = instrument_id.strip() or page.get('instrument_id') or (page.get('instrument') or {}).get('instrument_id')
    if not isinstance(term, str) or not 1 <= len(term) <= 80 or any(ord(char) < 32 for char in term):
        raise ValueError('须提供个股名称或代码，或从个股页面进入')
    normalized = term.upper()
    if re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)', normalized):
        return normalized
    before = _heads()
    market = _market(before)
    status = market.status()
    if not status.get('ready'):
        _gap(result, 'instrument_identity', '本地证券名录尚不可用，不能核实名称或六位代码；不猜代码或联网补抓')
        return
    local = _result('read_stock_research')
    identity = _stock_identity(local, market, status, term, None, {})
    if before != _heads():
        raise HTTPException(409, '读取期间证券名录发布代际变化，请重试')
    result['summary'].update(_pick_fields(local['summary'], (
        'requested_instrument', 'identity_state', 'candidates', 'identity_name_scope')))
    result['gaps'].extend(local['gaps'])
    if identity is None:
        return
    if _denied(identity):
        _gap(result, 'source_permission', '所选证券名录明确禁止AI处理或外发')
        return
    result['source_bindings']['identity_catalog'] = {'heads': before,
        'instrument_id': identity['instrument_id'],
        'identity_sha256': digest(_pick_fields(identity, ('instrument_id', 'name', 'kind')))}
    return identity['instrument_id']


def _research(result, instrument_id, page, fallback, now):
    instrument = _research_instrument(result, instrument_id, page)
    if instrument is None:
        return
    connection = _connect(sqlite_path())
    row = None
    if connection is not None:
        try:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_report'").fetchone():
                preferred = page.get('source_report_id') or page.get('report_id') or ''
                row = connection.execute('SELECT report_id,report_json,data_cutoff,expires_at_ms FROM research_report '
                    'WHERE instrument_id=? AND expires_at_ms>? AND length(CAST(report_json AS BLOB))<=? '
                    'ORDER BY CASE WHEN report_id=? THEN 0 ELSE 1 END,created_at_ms DESC LIMIT 1',
                    (instrument, int(now.timestamp()*1000), MAX_REPORT_BYTES, preferred)).fetchone()
        finally:
            connection.close()
    if row is None:
        if fallback.get('instrument_id') == instrument and fallback.get('kind') == 'stock_research':
            projected = contexts.model_input(fallback)
            result.update(date=fallback['as_of'], state='partial', evidence=projected['evidence'], metrics=projected['metrics'])
            result['source'] = ['saved_session_fallback']
            result['summary'].update({'instrument_id': instrument, 'as_of': fallback['as_of'], 'material_scope': projected.get('material_scope'),
                                 'metric_defaults': projected.get('metric_defaults', {}),
                                 'notice': '当前实例没有未过期研究；仅使用该会话原已保存旧材料，不代表今天或最新资料'})
            result['gaps'].extend(projected['gaps'])
            _gap(result, 'current_research', '没有当前可用研究；旧会话回退材料日期明确，未延长TTL')
            result['source_bindings']['fallback'] = {'context_sha256': fallback.get('context_sha256'), 'source_report_id': fallback.get('source_report_id')}
            return
        _gap(result, 'current_research', '当前实例没有该个股未过期的已保存v2研究，不联网补抓或跨股借用材料')
        return
    report = json.loads(row['report_json'])
    if (report.get('schema_version') != 2 or report.get('instrument', {}).get('instrument_id') != instrument
            or report.get('data_cutoff') != row['data_cutoff']):
        _gap(result, 'research_integrity', '已存研究版本、证券或截止日期不一致，保留未知')
        return
    result['date'] = report['data_cutoff']
    result['source'] = ['current_instance_saved_research']
    result['source_bindings']['research'] = {'report_id': row['report_id'], 'report_sha256': digest(report), 'expires_at_ms': row['expires_at_ms']}
    result['summary'].update({'instrument_id': instrument, 'instrument': report['instrument'], 'as_of': result['date'],
                         'material_window': report.get('material_window'), 'notice': '仅当前已保存研究材料，不证明历史时点可知，不代表今天已重采集'})
    result['gaps'].extend(contexts._clean(report.get('gaps', [])))
    if report.get('egress_allowed') is False or report.get('ai_processing_allowed') is False:
        _gap(result, 'research_permission', '父报告明确禁止外发或AI处理')
        return
    coverage = report.get('source_coverage', [])
    blocked = {r.get('source_id') for r in coverage if isinstance(r, dict) and (r.get('state') in ('requires_license', 'external_only')
        or r.get('egress_allowed') is False or r.get('ai_processing_allowed') is False)}
    aliases = {'valuation_snapshot': 'valuation', 'announcement': 'announcements', 'business_composition': 'company_profile'}
    seen = Counter(r.get('evidence_id') for r in report.get('evidence', []) if isinstance(r, dict))
    counts, excluded = Counter(), Counter()
    for raw in report.get('evidence', []):
        if not isinstance(raw, dict):
            continue
        kind, source, identifier = raw.get('kind'), raw.get('source'), raw.get('evidence_id')
        if (not isinstance(identifier, str) or seen[identifier] != 1 or raw.get('instrument_id') != instrument
                or not contexts._research_source_allowed(source, kind) or kind not in contexts._RESEARCH_KINDS
                or raw.get('content_hash') != digest(raw.get('data')) or counts[kind] >= contexts._RESEARCH_CAPS[kind]
                or source in blocked or kind in blocked or aliases.get(kind) in blocked
                or (str(kind).startswith('financial_') and 'financial' in blocked)
                or raw.get('state') in ('requires_license', 'external_only')
                or raw.get('egress_allowed') is False or raw.get('ai_processing_allowed') is False):
            excluded[str(source)] += 1
            continue
        evidence = {key: deepcopy(raw[key]) for key in ('evidence_id', 'source', 'kind', 'title', 'data') if key in raw}
        evidence['dates'] = {key: raw.get(key) for key in ('published_at_ms', 'fetched_at_ms', 'report_period')}
        evidence['dates']['market_data_cutoff'] = result['date']
        result['evidence'].append(evidence)
        result['metrics'].extend({**metric, 'evidence_ids': [identifier]} for metric in contexts._research_metrics(evidence, result['date']))
        counts[kind] += 1
    technical = report.get('technical_assessment', {})
    for period in technical.get('periods', []):
        if not isinstance(period, dict) or period.get('period') not in ('D', 'W', 'M'):
            continue
        identifier = 'technical:' + period['period']
        result['evidence'].append({'evidence_id': identifier, 'source': 'local_research_technical', 'kind': 'technical_observation',
            'title': period['period'] + '已存技术观察', 'dates': {'market_data_cutoff': result['date']},
            'data': {**{k: v for k, v in period.items() if k != 'current_period'}, 'analysis_contract': technical.get('analysis_contract')}})
    result['summary'].update(source_coverage=contexts._model_data(coverage), excluded_by_source=dict(excluded))


def _bounded(result):
    for rows, field in ((result['evidence'], 'evidence_id'), (result['metrics'], 'metric_id')):
        identifiers = [row[field] for row in rows]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError('来源标识重复，不能混合引用')
    bindings = result['source_bindings']
    result = contexts._model_data(result)
    result['source_bindings'] = bindings
    defaults = result['summary'].setdefault('metric_defaults', {})
    metrics = result['metrics']
    for key in ('as_of', 'price_basis'):
        values = [row.get(key, defaults.get(key)) for row in metrics]
        if values and values[0] is not None and all(value == values[0] for value in values):
            defaults[key] = values[0]
            for row in metrics:
                row.pop(key, None)
    date_rows = [row.get('dates', defaults.get('dates', {})) for row in metrics]
    common_dates = {key: value for key, value in (date_rows[0].items() if date_rows else [])
                    if all(row.get(key) == value for row in date_rows)}
    if common_dates:
        defaults['dates'] = {**defaults.get('dates', {}), **common_dates}
        for row in metrics:
            row['dates'] = {key: value for key, value in row.get('dates', {}).items() if key not in common_dates}
            if not row['dates']:
                row.pop('dates')
    if not defaults:
        result['summary'].pop('metric_defaults')
    for evidence in result['evidence']:
        # Dates/identities already occur in the explicit, small source binding.
        # Preserve facts and remove only duplicated audit wrappers.
        evidence.pop('source_binding', None)
        if isinstance(evidence.get('data'), dict):
            evidence['data'].pop('binding', None)
        related = [row for row in result['metrics'] if row.get('evidence_ids') == [evidence['evidence_id']]]
        related_dates = [row.get('dates') for row in related]
        if related_dates and related_dates[0] and all(value == related_dates[0] for value in related_dates):
            evidence['metric_dates'] = related_dates[0]
            for row in related:
                row.pop('dates', None)
        scopes = {row.get('scope') for row in related}
        if len(scopes) == 1 and next(iter(scopes), None):
            evidence['metric_scope'] = next(iter(scopes))
            for row in related:
                row.pop('scope', None)
    technical = [row['data'] for row in result['evidence'] if row.get('kind') == 'technical_observation'
                 and isinstance(row.get('data'), dict) and row['data'].get('analysis_contract')]
    if len(technical) > 1 and all(row['analysis_contract'] == technical[0]['analysis_contract'] for row in technical):
        result['summary']['technical_analysis_contract'] = technical[0]['analysis_contract']
        result['summary']['technical_contract_periods'] = [row.get('period') for row in technical]
        for row in technical:
            row.pop('analysis_contract')
    if result['metrics']:
        result['summary']['metric_semantics'] = '指标继承summary.metric_defaults，以及其evidence_ids所指证据的metric_dates/metric_scope；各指标自己的字段优先。单位逐项保留。'
    namespace = 'read:' + str(result['date']) + ':' + digest(result['source_bindings'])[:8] + ':'
    ids = {row['evidence_id']: namespace + row['evidence_id'] for row in result['evidence']}
    for row in result['evidence']:
        row['evidence_id'] = ids[row['evidence_id']]
    for row in result['metrics']:
        row['metric_id'] = namespace + row['metric_id']
        row['evidence_ids'] = [ids[value] for value in row.get('evidence_ids', []) if value in ids]
    if result['summary'].get('noon_context') or result['summary'].get('close_context'):
        from app.services.ai_intraday_read import _omit_sample

        while len(dumps(result).encode()) > MAX_RESULT_BYTES:
            omitted = False
            for evidence in sorted(result['evidence'], key=lambda row: len(dumps(row['data']).encode()), reverse=True):
                data = evidence['data']
                if _omit_sample(data):
                    omitted = True
                    break
                choices = [('directions.' + key, rows) for key, rows in data.get('directions', {}).items() if rows]
                choices.extend(('pools.' + key, pool['samples']) for key, pool in data.get('pools', {}).items() if pool.get('samples'))
                carryover = data.get('carryover', {})
                choices.extend(('carryover.' + key, carryover.get(key, {}).get('stocks', []))
                               for key in ('highest_candidates', 'price_reference'))
                choices.extend(('carryover_direction_stocks', row.get('stocks', [])) for row in carryover.get('directions', []))
                choices.append(('opening_comparison_examples', data.get('opening_comparison', {}).get('examples', [])))
                choices = [(key, rows) for key, rows in choices if rows]
                if choices:
                    key, rows = max(choices, key=lambda item: len(dumps(item[1]).encode()))
                    rows.pop()
                    if key.startswith('pools.'):
                        pool = data['pools'][key.removeprefix('pools.')]
                        pool['omitted_count'] = pool.get('omitted_count', 0) + 1
                    counts = data.setdefault('omitted_for_budget', {})
                    counts[key] = counts.get(key, 0) + 1
                    omitted = True
                    break
            if omitted:
                continue
            # These metric rows duplicate numeric facts kept in the original
            # evidence data. Keep every phase before reducing that catalog.
            if result['metrics']:
                result['metrics'].pop()
                result['summary']['omitted_duplicate_metrics_for_budget'] = result['summary'].get('omitted_duplicate_metrics_for_budget', 0) + 1
                continue
            break
        if len(dumps(result).encode()) > MAX_RESULT_BYTES:
            from app.services.ai_prefix_budget import compact_prefix
            compact_prefix(result)
        if len(dumps(result).encode()) > MAX_RESULT_BYTES:
            error = _result(result['tool_name'], result['date'])
            error.update(ok=False, state='error')
            _gap(error, 'result_capacity', '累计前缀必要来源和计数超过24KiB；未删除时段或截断JSON，请按具体时点读取', 'unknown')
            return error
    excluded = Counter()
    while len(dumps(result).encode()) > MAX_RESULT_BYTES and result['evidence']:
        # Keep useful summary/phase metadata and whole factual observations.
        optional = [r for r in result['evidence'] if str(r.get('kind', '')).startswith('sector:')]
        optional = optional or [r for r in result['evidence'] if r.get('kind') == 'announcement']
        optional = optional or [r for r in result['evidence'] if str(r.get('kind', '')).startswith('cohort:')]
        if not optional and result['tool_name'] == 'read_stock_research':
            optional = [r for r in result['evidence'] if r.get('kind') == 'technical_observation']
            if not optional:
                counts = Counter(r['kind'] for r in result['evidence'])
                optional = [r for r in result['evidence'] if counts[r['kind']] > 1]

        removed = (optional or result['evidence'])[-1]
        identifier = removed['evidence_id']
        result['evidence'].remove(removed)
        if removed.get('kind') == 'technical_observation' and 'technical_contract_periods' in result['summary']:
            periods = result['summary']['technical_contract_periods']
            periods.remove(removed['data']['period'])
            if not periods:
                result['summary'].pop('technical_contract_periods')
                result['summary'].pop('technical_analysis_contract')
        result['metrics'] = [r for r in result['metrics'] if identifier not in r.get('evidence_ids', [])]
        excluded[str(removed.get('source'))] += 1
        result['summary']['bounded_exclusions'] = dict(excluded)
        result['summary']['bounded_notice'] = '容量内仅保留实际返回证据；其他材料整条排除，不能称已读取全部资料'
    if len(dumps(result).encode()) > MAX_RESULT_BYTES:
        result = _result(result['tool_name'], result['date'])
        result.update(ok=False, state='error')
        _gap(result, 'result_capacity', '必要来源、日期、单位与缺口超过24KiB，未截断或发送不完整材料')
    if len(dumps(result).encode()) > MAX_RESULT_BYTES:
        raise ValueError('必要元数据超出只读工具容量')
    if result['evidence'] and result['state'] == 'unknown':
        result['state'] = 'partial' if result['gaps'] else 'available'
    return result


def execute_read_tool(name, args, *, page_context=None, fallback_context=None, include_auction_opening=True):
    """Execute declared local reads; always return bounded JSON, never collect."""
    result = _result(name if name in {r['name'] for r in tool_schemas()} else 'unknown')
    try:
        if not isinstance(args, dict):
            raise ValueError('工具参数须为对象')
        if page_context is not None and not isinstance(page_context, dict) or fallback_context is not None and not isinstance(fallback_context, dict):
            raise ValueError('页面或回退资料格式无效')
        now = local_now()
        if name == 'read_stock_research':
            classify_tool(name, args)
            _research(result, args['instrument_id'], page_context or {}, fallback_context or {}, now)
        elif name == 'read_stock_trend':
            classify_tool(name, args)
            day = args['date'] if args['date'] == 'latest' else _day(args['date'], now)
            result['date'] = None if day == 'latest' else day
            from app.services.supplement_access import supplement_access
            with supplement_access():
                _stock_trend(result, args, day, page_context or {}, now)
        elif name == 'read_intraday_data':
            classify_tool(name, args)
            day = _day(args['date'], now)
            result['date'] = day
            from app.services.ai_intraday_read import fill_intraday
            fill_intraday(result, day, args['slot'], args['query'].strip(), now)
        elif name in ('read_stock_quote', 'read_limit_up_data', 'read_market_detail'):
            permitted = ({'date', 'instrument_id'} if name == 'read_stock_quote' else
                         {'date', 'section', 'query'} if name == 'read_market_detail' else {'date'})
            if set(args) != permitted:
                raise ValueError('工具参数与声明字段不符')
            latest = (name == 'read_market_detail' and args['section'] in GAINERS_LOOKUP_MODES
                      and args['date'] == 'latest')
            day = 'latest' if latest else _day(args['date'], now)
            result['date'] = None if latest else day
            from app.services.supplement_access import supplement_access
            # Pin only the local read itself. No database transaction or source
            # lock spans model HTTP, and GC cannot unlink inputs mid-projection.
            with supplement_access():
                if name == 'read_stock_quote':
                    if not isinstance(args['instrument_id'], str): raise ValueError('证券标识须为文本')
                    _stock_quote(result, args['instrument_id'], day, page_context or {}, now)
                elif name == 'read_limit_up_data':
                    _limit_up(result, day, now)
                else:
                    section, query = args['section'], args['query']
                    if (section not in DETAIL_SECTIONS or not isinstance(query, str) or len(query) > 80
                            or any(ord(c) < 32 for c in query)
                            or section in GAINERS_LOOKUP_MODES and not query.strip()):
                        raise ValueError('盘面分区或查询文本无效')
                    _market_detail(result, day, section, query.strip(), now)
        elif name in ('read_market_data', 'check_data_availability'):
            permitted = {'date', 'section'} if name == 'read_market_data' else {'date'}
            if set(args) - permitted:
                raise ValueError('工具不接受额外参数或路径')
            day = _day(args.get('date', 'today'), now)
            section = args.get('section', 'availability')
            if section not in SECTIONS:
                raise ValueError('盘面分区无效')
            result['date'] = day
            availability, selected, before = (_availability(day, now, allow_market_calendar=False)
                                                if section in ('noon', 'close') else _availability(day, now))
            result['summary'] = availability
            result['source_bindings']['intraday'] = before if include_auction_opening else {
                label: {key: value for key, value in source.items() if key != 'auction_opening'}
                for label, source in before.items()}
            if availability.get('calendar_binding'):
                result['source_bindings']['calendar'] = availability['calendar_binding']
            result['source'] = sorted({row['source'] for row in selected.values()})
            if name == 'check_data_availability' or section == 'availability':
                result['state'] = 'available'
            else:
                kinds = tuple(PHASES) if section == 'overview' else (section,) if section in PHASES else ()
                if section == 'noon':
                    from app.services.ai_noon_context import add_noon_context
                    add_noon_context(result, day, selected, before, now)
                elif section == 'close':
                    from app.services.ai_noon_context import add_close_context
                    add_close_context(result, day, selected, before, now)
                elif section != 'overview':
                    _add_blocks(result, day, selected, kinds, include_auction_opening=include_auction_opening)
                for phase in kinds:
                    if availability['phases'][phase]['phase_state'] != 'available':
                        _gap(result, phase, availability['phases'][phase]['reason'], availability['phases'][phase]['phase_state'])
                if section in PHASES:
                    result['state'] = availability['phases'][section]['phase_state']
                    if result['state'] == 'available' and not result['evidence']:
                        result['state'] = 'unknown'
                if section == 'overview':
                    if not _published_overview(result, day):
                        latest = next((phase for phase in reversed(PHASES) if phase in selected), None)
                        if latest:
                            _add_blocks(result, day, selected, (latest,))
                elif section == 'fund_flow':
                    _fund_flow(result, day)
            if before != {label: briefs.fingerprints(day, source_path=path) for label, path in _paths()}:
                raise HTTPException(409, '读取期间时点来源变化，请重试')
        else:
            raise ValueError('工具名称不在允许范围')
        return _bounded(result)
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, HTTPException):
        error = _result(result['tool_name'], result['date'])
        error.update(ok=False, state='error')
        _gap(error, 'read_error', '参数无效、来源发生变化或已保存资料未通过只读核验；未取新数据，也未写入或发送私有路径')
        return error
