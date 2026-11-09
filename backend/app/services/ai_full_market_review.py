"""Bounded, exact-date local evidence for the overview AI shortcut."""
from datetime import date
import sqlite3

from fastapi import HTTPException

from app.core import digest, dumps
from app.services import ai_read_tools as tools
from app.services.supplement_access import supplement_access

TOOL_NAME = 'read_market_overview'
TOOL_ARGS = {'scope': 'selected_day'}
TOOL_DESCRIPTION = (
    '读取盘面总览所选观察日的本地复盘证据。覆盖指数、情绪、竞价、盘中、午评、收盘、'
    '行业与概念、涨停梯队、消息与事件、龙虎榜、30日涨幅榜、异动与监管。各项区分完整、'
    '部分、未知及时间边界。样本有界，不能外推完整名单。'
)
PROMPT = (
    '这个会话从盘面总览进入。全面复盘时优先使用 read_market_overview(scope=selected_day)。'
    '用同日已核验资料串联竞价、盘中、午评与正式收盘，交叉核对指数、量能、情绪、板块、梯队、'
    '消息事件、龙虎榜、30日涨幅榜及异动与监管。列出影响结论的缺档并注明实际时点与来源。'
    '未来日程只表示安排。成交额不是资金净流入，供应商监控标签不是官方监管措施。'
    '页面绑定不为无关问题授权。'
)

_MODULES = (
    ('overview', '盘面总览'), ('indices', '指数行情'), ('emotion', '市场情绪'),
    ('auction', '盘前竞价'), ('intraday', '盘中观察'), ('noon', '午评快照'),
    ('close', '收盘复盘'), ('sectors', '行业与概念'), ('ladder', '涨停梯队'),
    ('news_events', '消息与事件'), ('dragon_tiger', '龙虎榜'),
    ('gainers', '30日涨幅榜'), ('abnormal', '异动与监管'),
)
_DETAILS = ('indices', 'emotion', 'sectors', 'dragon_tiger', 'gainers', 'abnormal')
_PHASE_KINDS = {
    'auction': ('auction_brief', 'auction_discovery_brief'),
    'noon': ('noon_brief',),
    'close': ('closing_pools_brief', 'noon_close_brief'),
}
_STOCK = ('instrument_id', 'name', 'change_pct', 'close', 'amount')
_DIRECTION = ('topic_code', 'name', 'kind', 'change_pct', 'average_auction_pct',
              'valid_count', 'member_limit_up_count', 'limit_up_count', 'members_observed_date')
_BREADTH = ('up', 'down', 'flat', 'unknown', 'valid', 'observed', 'total', 'missing')
_GAP_STATES = frozenset(('not_due', 'not_collected', 'rest_day', 'unknown', 'unavailable', 'expired'))


def _pick(value, *fields):
    values = tools._pick_fields(value, fields)
    for key, item in tuple(values.items()):
        if not isinstance(item, str):
            continue
        if key == 'url' and len(item) > 320:
            values.pop(key)
        elif key != 'url' and len(item) > 140:
            values[key] = item[:139] + '…'
    return values


def _sample(table, fields, limit):
    table = table or {}
    rows = table.get('items', [])
    count = table.get('observed_count')
    return {'observed_count': count, 'sample_count': min(len(rows), limit),
            'sample_truncated': bool(table.get('truncated') or len(rows) > limit),
            'items': [_pick(row, *fields) for row in rows[:limit]]}


def _evidence_ref(row):
    return {'source': row.get('source'), 'dates': row.get('dates', {})}


def _directions(data, limit=2):
    groups = data.get('directions') or {}
    return {kind: [_pick(row, *_DIRECTION) for row in rows[:limit]]
            for kind, rows in groups.items() if kind in ('industries', 'concepts') and rows}


def _phase(phase, raw, availability):
    records = [row for row in raw['evidence'] if row['kind'] in _PHASE_KINDS[phase]]
    data = {'phase': _pick(availability, 'phase_state', 'expected_phase_at')}
    for row in records:
        value = row['data']
        projected = {'state': value.get('state'), **_evidence_ref(row)}
        kind = row['kind']
        if kind == 'auction_brief':
            projected.update(summary=value.get('summary'), directions=_directions(value, 1),
                             prior_limit_up=(value.get('carryover') or {}).get('prior_limit_up'))
        elif kind == 'auction_discovery_brief':
            projected.update(coverage=value.get('coverage'), directions=_directions(value, 1),
                             captured_at_ms=value.get('captured_at_ms'))
        elif kind == 'noon_brief':
            projected.update(breadth=value.get('breadth'), amount=value.get('amount'),
                             events={key: _pick(event, 'state', 'count')
                                     for key, event in (value.get('events') or {}).items()},
                             directions=_directions(value, 1))
        elif kind == 'closing_pools_brief':
            projected.update(height=value.get('height'), pools={key: _pick(pool, 'state', 'count')
                             for key, pool in (value.get('pools') or {}).items()})
        else:
            projected.update(afternoon=value.get('afternoon'), amount=_pick(value.get('amount'), 'state', 'delta'),
                             directions=_directions(value, 1))
        data[kind] = projected
    if not records:
        state = availability.get('phase_state')
        if state in _GAP_STATES:
            return data, state, availability.get('reason')
        return data, 'unknown', '已有留存记录，本轮没有通过原时点核验的正文'
    if phase == 'close' and not any(row['kind'] == 'closing_pools_brief' for row in records):
        return data, 'partial', '仅有午间至收盘的留存对照，原正式收盘锚点缺失'
    partial = any(row['data'].get('source_gaps') or row['data'].get('state') in ('partial', 'unknown')
                  for row in records)
    return data, 'partial' if partial else 'available', '只覆盖已核验的原时点样本' if partial else None


def _intraday(raw):
    phases = raw['summary'].get('phases', {})
    by_slot = {row['kind'].split(':', 1)[-1]: row for row in raw['evidence']}
    rows = []
    for slot, phase in phases.items():
        record = by_slot.get(slot)
        projected = {'slot': slot, 'time': phase.get('time'), 'state': phase['phase_state']}
        if record:
            value = record['data']
            projected.update(_pick(record['dates'], 'collected_end_at'))
            projected.update(breadth=_pick(value.get('breadth'), 'up', 'down', 'flat'),
                             turnover=_pick(value.get('turnover'), 'amount', 'valid'),
                             interval_amount=_pick(value.get('interval_amount'), 'amount', 'valid_count'),
                             ladder=_pick(value.get('ladder'), 'state', 'sealed_count', 'highest_board',
                                          'first_board_count', 'height_state'))
            if value.get('sector_samples'):
                projected['sector_sample'] = _pick(value['sector_samples'][0], 'name', 'instrument_id', 'change_pct')
            if value.get('indices'):
                projected['index_sample'] = _pick(value['indices'][0], 'name', 'instrument_id', 'change_pct')
            projected['board_changes'] = _pick(value.get('board_changes'), 'state', 'baseline_slot')
        else:
            projected['reason'] = phase.get('reason')
        rows.append(projected)
    return {'slots': rows, 'archived_slot_count': len(by_slot)}, raw['state'], None


def _overview(raw):
    picked = {'market_distribution', 'turnover', 'previous_limit_up'}
    data = {}
    for row in raw['evidence']:
        if row['kind'] in picked:
            relevant = {item['metric_id'].split(':')[-1]: _pick(item, 'value', 'unit')
                        for item in raw['metrics'] if row['evidence_id'] in item.get('evidence_ids', [])}
            value = row['data']
            data[row['kind']] = {**_pick(value, 'state', 'state_label', 'amount', 'amount_change',
                                         'amount_change_pct', 'amount_previous_date', 'sample_count',
                                         'observed_count', 'unknown_count', 'coverage_pct'),
                                 'metrics': relevant, **_evidence_ref(row)}
    return data


def _emotion(data):
    outlook = data.get('market_outlook') or {}
    sentiment = data.get('sentiment') or {}
    return {
        'breadth': _pick(data.get('breadth'), *_BREADTH),
        'emotion': _pick(data.get('emotion'), 'phase', 'cycle', 'temperature', 'previous_temperature',
                         'temperature_delta', 'comparable_count', 'comparable_coverage', 'event_pools_complete'),
        'sentiment': {**_pick(sentiment, 'atmosphere'),
                      'market': _pick(sentiment.get('market'), 'state_label', 'coverage_pct'),
                      'short_term': _pick(sentiment.get('short_term'), 'state_label', 'cohort_date',
                                          'sample_count', 'promotion_bounds_pct')},
        'retained_event_counts': outlook.get('event_counts'),
    }


def _detail(module, raw):
    value = next((row['data'] for row in raw['evidence']), {})
    summary = raw['summary']
    if module == 'indices':
        data = {'indices': _sample(value.get('indices'), (*_STOCK, 'date', 'quote_available', 'quote_reason'), 4)}
    elif module == 'emotion':
        data = _emotion(value)
    elif module == 'sectors':
        data = {'sectors': _sample(value.get('sectors'), (*_DIRECTION, 'amount', 'quote_date',
                     'quote_source', 'membership_observed_date', 'members_complete'), 5),
                'quote_coverage': _pick(value.get('quote_coverage'), 'selected_date_available', 'complete', 'accepted_count', 'failed_count'),
                'member_coverage': _pick(value.get('membership_coverage'), 'selected_date_available', 'partial', 'source_snapshot_day')}
        if not data['sectors']['observed_count'] and value and not data['quote_coverage'].get('selected_date_available'):
            return data, 'unknown', '所选日暂无可核验板块样本'
    elif module == 'gainers':
        distribution = value.get('ranking_distribution') or {}
        data = {'stocks': _sample(value.get('stocks'), (*_STOCK, 'rank', 'change_30d_pct',
                         'return_30d_start_quote_date', 'return_30d_quality'), 5),
                'ranking': _pick(summary.get('page_summary'), 'start', 'end', 'covered_stocks', 'unknown_count', 'universe_count'),
                'distribution': _pick(distribution, 'classification_state', 'sample_count', 'sample_limit')}
    elif module == 'abnormal':
        data = {**_pick(value, 'monitor_scope', 'monitor_observed_date', 'monitor_fetched_at', 'quote_as_of', 'source'),
                'stocks': _sample(value.get('stocks'), (*_STOCK, 'event_type', 'observed_date', 'start_date',
                                  'end_date', 'evidence_type', 'official_regulation_end_date'), 4)}
        if value and (value.get('monitor_scope') != 'historical' or value.get('monitor_observed_date') != raw['date']):
            return {}, 'unknown', value.get('unavailable_reason') or '没有该观察日的已留存监控名单'
    else:
        periods = value.get('periods') or {}
        data = {'availability': _pick(value.get('availability'), 'available', 'date', 'sources', 'reason'),
                'coverage': _pick(value.get('coverage'), 'complete', 'retained_trading_days', 'anchor_date'),
                'periods': {period: {kind: _sample(table, (*_STOCK, 'label_name', 'net_amount'), 1)
                            for kind, table in boards.items()} for period, boards in periods.items()}}
        if value and data['availability'].get('available') is not True:
            return data, 'unknown', data['availability'].get('reason') or '该观察日未留存可核验龙虎榜'
    return data, _read_state(raw), _read_reason(raw)


def _ladder(raw):
    counts = _pick(raw['summary'], 'complete', 'confirmed_limit_up_count', 'confirmed_limit_up_observed_count',
                   'first_board_count', 'first_board_observed_count', 'unknown_height_count',
                   'highest_board_count', 'highest_board_observed_count')
    value = next((row['data'] for row in raw['evidence']), {})
    data = {'counts': counts, 'heights': [{**_pick(group, 'height', 'count', 'observed_count'),
                        'stocks': _sample(group.get('stocks'), ('instrument_id', 'name'), 1)}
                        for group in (value.get('board_groups') or [])[:8]],
            'themes': _sample(value.get('theme_counts'), (*_DIRECTION, 'limit_up_observed_count', 'complete'), 3)}
    return data, _read_state(raw), _read_reason(raw)


def _read_state(raw):
    state = raw.get('state')
    if not raw['evidence']:
        return state if state in _GAP_STATES else 'unknown'
    if state in _GAP_STATES - {'unknown'}:
        return state
    return 'partial' if raw.get('gaps') or state == 'partial' or raw['summary'].get('quality') == 'partial' else 'available'


def _read_reason(raw):
    return next((item['reason'] for item in raw.get('gaps', ()) if item.get('reason')), None)


def _news(day):
    from app.services import news_events
    from app.services.ai_news_context import fill_news_context

    raw = tools._result('news_events', day)
    fill_news_context(raw, {'as_of': day, 'news_context': {'kind': 'day'}})
    calendar = news_events.read_calendar(day, days=7)
    if tools._source_denied(raw) or tools._source_denied(calendar):
        return raw, {}, 'unknown', '消息与事件来源限制AI处理'
    if calendar.get('date') != day:
        raise HTTPException(409, '事件日历观察日期变化')
    entries = raw['evidence']
    due = [row for row in calendar['events'] if row.get('event_date') == day]
    calendar_day_covered = calendar.get('status') == 'available' and any(
        isinstance(row.get('coverage_start'), str) and isinstance(row.get('coverage_end'), str)
        and row['coverage_start'] <= day <= row['coverage_end'] for row in calendar['sources'])
    stages = raw['summary'].get('stages', [])
    data = {'archived_news_count': raw['summary'].get('archived_count'),
            'stages': [_pick(stage, 'stage', 'status', 'archived_count', 'captured_at') for stage in stages],
            'news_samples': [{**_pick(row, 'source', 'title'), **_pick(row['dates'], 'published_at', 'first_seen_at'),
                              **_pick(row.get('data'), 'url')} for row in entries[:4]],
            'calendar': {'status': calendar.get('status'), 'same_day_count': len(due) if calendar_day_covered else None,
                         'same_day_observed_count': len(due),
                         'events': [_pick(row, 'title', 'event_date', 'published_at', 'status', 'source', 'url') for row in due[:3]]}}
    raw['source_bindings']['calendar'] = {'sources': calendar['sources'], 'observed_day': day, 'sha256': digest(due)}
    raw['source_bindings']['news'] = {'sha256': digest((entries, stages))}
    if not entries and not (calendar_day_covered or due):
        return raw, data, 'unknown', _read_reason(raw) or calendar.get('reason')
    partial = any(stage.get('status') != 'complete' for stage in stages) or not calendar_day_covered
    return raw, data, 'partial' if partial else 'available', (_read_reason(raw) or calendar.get('reason')) if partial else None


def _read(day, name, read):
    result = tools._result(name, day)
    try:
        read(result)
    except HTTPException as exc:
        if exc.status_code == 409:
            raise
        result = tools._result(name, day)
        tools._gap(result, name, '所选日来源暂不可读取')
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
        result = tools._result(name, day)
        tools._gap(result, name, '所选日资料未通过读取核验')
    if tools._source_denied(result):
        result = tools._result(name, day)
        tools._gap(result, name, '所选日来源限制AI处理')
    if result['date'] != day:
        raise HTTPException(409, '盘面复盘观察日期不符')
    return result


def _inputs(day, now, market, selected):
    from app.services.ai_intraday_read import fill_intraday

    def overview_read(raw):
        status = market.status()
        begin, end = status.get('data_begin'), status.get('data_end')
        if not isinstance(end, str) or day > end or isinstance(begin, str) and day < begin:
            tools._gap(raw, 'overview', '所选日没有已发布完成日行情')
        else:
            tools._published_overview(raw, day, market=market)

    with supplement_access():
        overview = _read(day, 'overview', overview_read)
        details = {module: _read(day, module, lambda raw: tools._market_detail(raw, day, module, '', now, market=market))
                   for module in _DETAILS}
        ladder = _read(day, 'ladder', lambda raw: tools._limit_up(raw, day, now, market=market))
    briefs = _read(day, 'briefs', lambda raw: tools._add_blocks(raw, day, selected, tuple(_PHASE_KINDS)))
    intraday = _read(day, 'intraday', lambda raw: fill_intraday(raw, day, 'all', '', now))
    return {'overview': overview, 'ladder': ladder, 'intraday': intraday, 'briefs': briefs, **details}


def _sample_lists(value):
    """Find only optional whole rows. Slot identities and core counts stay."""
    if isinstance(value, dict):
        if isinstance(value.get('items'), list) and 'sample_count' in value and value['items']:
            yield value['items'], value
        for key, child in value.items():
            if key in ('industries', 'concepts', 'news_samples', 'events') and isinstance(child, list) and child:
                yield child, None
            elif key != 'indices':
                yield from _sample_lists(child)
    elif isinstance(value, list):
        for child in value:
            yield from _sample_lists(child)


def _fit(result):
    # Leave space for the common reader to add its evidence ID namespace.
    limit = tools.MAX_RESULT_BYTES - 1024
    omitted = 0
    while len(dumps(result).encode()) > limit:
        choices = [item for row in result['evidence'] for item in _sample_lists(row['data'])]
        if not choices:
            raise ValueError('全面盘面复盘必要资料超过容量')
        rows, table = max(choices, key=lambda item: (len(item[0]) > 1, len(dumps(item[0][-1]).encode())))
        rows.pop()
        if table is not None:
            table.update(sample_count=len(rows), sample_truncated=True)
        omitted += 1
    if omitted:
        result['summary']['sample_rows_omitted_for_budget'] = omitted


def fill_full_review(result, page_context):
    day = page_context.get('as_of')
    if (page_context.get('market_scope') != 'full_review' or not isinstance(day, str)
            or date.fromisoformat(day).isoformat() != day or not 1900 <= date.fromisoformat(day).year < 9999):
        raise ValueError('全面盘面复盘需要明确的页面观察日')
    result['date'] = day
    now = tools.local_now()
    if day > now.date().isoformat():
        tools._gap(result, 'observation_date', '所选日期尚未到，保留未知', 'not_due')
        result['state'] = 'not_due'
        return
    heads, supplement = tools._heads(), tools._supplement_head()
    availability, selected, fingerprints = tools._availability(day, now, allow_market_calendar=False)
    market = tools._market(heads)
    original_status = market.status()
    if tools._denied(original_status) or original_status.get('ready') and tools._denied(market.reference()):
        tools._gap(result, 'source_permission', '所选行情来源限制AI处理')
        return
    raw = _inputs(day, now, market, selected)
    try:
        news, news_data, news_state, news_reason = _news(day)
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
        news, news_data, news_state, news_reason = tools._result('news_events', day), {}, 'unknown', '消息与事件归档未通过读取核验'
    if (heads != tools._heads() or supplement != tools._supplement_head()
            or fingerprints != {label: tools.briefs.fingerprints(day, source_path=path) for label, path in tools._paths()}):
        raise HTTPException(409, '全面盘面复盘期间来源发生变化，请重试')

    result['summary'] = {'modules': [], 'amount_unit': 'CNY', 'change_unit': '%',
                         'scope': '所选日已留存资料，各项返回有界样本；盘中时点与正式收盘分别核验。'}
    sources = set()
    bindings = {}
    for module, label in _MODULES:
        module_raw = raw.get(module, raw['briefs'])
        if module == 'overview':
            data, state, reason = _overview(module_raw), _read_state(module_raw), _read_reason(module_raw)
        elif module in _PHASE_KINDS:
            data, state, reason = _phase(module, module_raw, availability['phases'][module])
        elif module == 'intraday':
            data, state, reason = _intraday(module_raw)
        elif module == 'ladder':
            data, state, reason = _ladder(module_raw)
        elif module == 'news_events':
            module_raw, data, state, reason = news, news_data, news_state, news_reason
        else:
            data, state, reason = _detail(module, module_raw)
        if state == 'error':
            state = 'unknown'
        if state in _GAP_STATES and not reason:
            reason = _read_reason(module_raw) or '该模块暂无所选日可核验资料'
        if state == 'partial' and not reason:
            reason = '仅覆盖已核验样本，来源完整性仍有限'
        state_item = {'module': module, 'label': label, 'state': state}
        if reason:
            state_item['reason'] = reason[:120]
        result['summary']['modules'].append(state_item)
        if not data or not module_raw['evidence'] and module not in ('intraday', 'news_events'):
            continue
        facts = [row for row in module_raw['evidence'] if module not in _PHASE_KINDS or row['kind'] in _PHASE_KINDS[module]]
        module_sources = list(dict.fromkeys([*module_raw['source'], *[row['source'] for row in facts]]))
        bindings[module] = {'input_sha256': digest((module_raw['source_bindings'], facts))}
        if module_sources:
            sources.update(module_sources)
            data['sources'] = module_sources[:4]
        result['evidence'].append({'evidence_id': 'overview:' + module, 'kind': module, 'title': label,
                                   'source': module_sources[0] if module_sources else 'current_instance',
                                   'dates': {'trade_date': day}, 'data': data})
    result['source'] = sorted(sources)
    result['source_bindings']['overview'] = {'market_snapshot_id': original_status.get('snapshot_id'),
                                             'module_sources': bindings}
    states = {item['state'] for item in result['summary']['modules']}
    result['state'] = 'available' if states == {'available'} else 'partial' if states & {'available', 'partial'} else 'unknown'
    _fit(result)
