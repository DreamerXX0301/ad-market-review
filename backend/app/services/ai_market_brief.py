"""Small AI observations from one day's saved auction/noon/close records.

Reads the business recovery copy or configured database in SQLite read-only
mode. No migrations, credentials, source clients, jobs, DuckDB, full-history
scans or fresh event classification are performed here.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3

from fastapi import HTTPException

from app.core import dumps
from app.infrastructure.config import sqlite_path
from app.services import auction_followup
from app.services.auction_integrity import validate_snapshot
from app.services.auction_topic_summary import build_topic_summary

MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
MAX_REVIEW_BYTES = 2 * 1024 * 1024
MAX_BRIEF_DATA_BYTES = 2304
TABLES = {'auction': 'market_auction_snapshot', 'noon': 'market_noon_snapshot',
          'noon_close': 'market_noon_close_review'}


def _day(day):
    try:
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, '时点摘要日期须为YYYY-MM-DD') from None
    return day


def _connect(source_path=None):
    path = Path(source_path).expanduser().resolve() if source_path is not None else sqlite_path()
    if not path.is_file():
        return None
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA recursive_triggers=ON')
    connection.execute('PRAGMA busy_timeout=10000')
    connection.execute('PRAGMA query_only=ON')
    return connection


def _metadata(connection, day):
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('market_auction_snapshot','market_noon_snapshot','market_noon_close_review')")}
    result = {}
    for kind, table in TABLES.items():
        if table not in tables:
            result[kind] = None
            continue
        if kind == 'noon_close':
            row = connection.execute('SELECT review_id,snapshot_id,trade_date,revision,state,'
                'generated_at_ms,noon_sha256,close_input_sha256,result_sha256,'
                'length(CAST(result_json AS BLOB)) AS payload_bytes FROM ' + table +
                ' WHERE trade_date=? ORDER BY revision DESC LIMIT 1', (day,)).fetchone()
        else:
            columns = {row[1] for row in connection.execute('PRAGMA table_info(' + table + ')')}
            recorded = ',recorded_at_ms' if 'recorded_at_ms' in columns else ''
            row = connection.execute('SELECT snapshot_id,trade_date,collected_start_at,collected_end_at,'
                'content_sha256,length(CAST(payload_json AS BLOB)) AS payload_bytes' + recorded + ' FROM ' + table +
                ' WHERE trade_date=?', (day,)).fetchone()
        result[kind] = dict(row) if row else None
    if result['auction'] and (discovery := auction_followup.discovery_metadata(connection, day)):
        if discovery['primary_snapshot_id'] == result['auction']['snapshot_id']:
            result['auction_discovery'] = discovery
            if opening := auction_followup.opening_metadata(connection, day):
                result['auction_opening'] = opening
    from app.services.ai_closing_read import metadata as closing_metadata
    if closing := closing_metadata(connection, day):
        result['closing_pools'] = closing
    return result


def fingerprints(day, *, source_path=None):
    """Exact-day small identities; no body reads or scans of retained dates."""
    _day(day)
    connection = _connect(source_path)
    if connection is None:
        return dict.fromkeys(TABLES)
    try:
        return _metadata(connection, day)
    finally:
        connection.close()


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _pick(row, *fields):
    return {key: row[key] for key in fields if key in row}


def _directions(rows, *, order, fields):
    result = {}
    for kind, name in (('industry', 'industries'), ('concept', 'concepts')):
        eligible = [row for row in rows if isinstance(row, dict) and row.get('kind') == kind
                    and isinstance(row.get('topic_code'), str) and row['topic_code']]
        eligible.sort(key=lambda row: (_number(row.get(order)) is None,
                                      -(_number(row.get(order)) or 0), str(row.get('topic_code', ''))))
        result[name] = [_pick(row, 'topic_code', 'name', 'source', 'members_observed_date', *fields)
                        for row in eligible[:3]]
    return result


def _block(kind, day, metadata, data, metric_rows):
    dates = {'trade_date': day}
    dates.update(_pick(metadata, 'collected_start_at', 'collected_end_at', 'generated_at_ms',
                       'noon_collected_start_at', 'noon_collected_end_at'))
    labels = {'auction': '盘前竞价候选摘要', 'auction_discovery': '竞价发现与开盘验证',
              'noon': '午间行情摘要', 'noon_close': '午间至收盘变化', 'closing_pools': '原正式收盘封板与炸板明细'}
    # Preserve the entire direction row or omit it with an explicit scope note;
    # never trim a date/unit/counter-example field to make the body fit.
    while len(dumps(data).encode()) > MAX_BRIEF_DATA_BYTES:
        directions = data.get('directions', {})
        choices = [key for key in ('concepts', 'industries') if directions.get(key)]
        if not choices:
            carryover = data.get('carryover', {})
            samples = [carryover.get(key, {}).get('stocks', []) for key in ('highest_candidates', 'price_reference')]
            samples.extend(row.get('stocks', []) for row in carryover.get('directions', []))
            samples = [rows for rows in samples if rows]
            if not samples:
                raise ValueError('单日时点必要摘要超过容量，未截断来源、单位或缺口')
            max(samples, key=lambda rows: len(dumps(rows).encode())).pop()
            data['carryover_samples_omitted_for_budget'] = data.get('carryover_samples_omitted_for_budget', 0) + 1
            continue
        key = max(choices, key=lambda value: len(dumps(directions[value]).encode()))
        directions[key].pop()
        data['directions_omitted_for_budget'] = True
    identifier = 'day_brief:' + kind
    metrics = [{'metric_id': identifier + ':' + key, 'label': label, 'value': _number(value),
                'unit': unit, 'as_of': day, 'price_basis': 'none', 'dates': {'trade_date': day},
                'scope': data['scope']} for key, label, value, unit in metric_rows]
    return {'evidence': {'evidence_id': identifier, 'source': 'saved_' + kind,
                          'kind': kind + '_brief', 'title': labels[kind], 'dates': dates,
                          'source_binding': metadata, 'data': data}, 'metrics': metrics}


def _auction(payload, metadata, day):
    summary = (payload.get('quotes') or {}).get('summary') or {}
    topics = build_topic_summary(payload)
    from app.services.auction_carryover_narrative import auction_carryover
    groups = topics.get('groups', [])
    directions = _directions(groups, order='average_auction_pct',
                             fields=('sample_count', 'valid_count', 'average_auction_pct', 'state'))
    data = {'state': (payload.get('quotes') or {}).get('state', 'unknown'),
            'scope': '原盘前冻结候选样本；高开和题材关联不是全市场资金方向或成交收益',
            'units': {'amount': 'CNY', 'median_pct': '%', 'counts': '家'},
            'summary': _pick(summary, 'requested', 'valid', 'missing', 'up', 'down', 'flat', 'median_pct', 'amount'),
            'directions': directions, 'direction_state': topics.get('state', 'unknown'),
            'direction_scope': '原代际平均竞价涨幅各前3；valid_count=1是单股样本，不推全市场；行业/概念重叠不加总',
            'source_gaps': payload.get('gaps', []),
            'binding': _pick(metadata, 'snapshot_id', 'content_sha256')}
    carryover = auction_carryover(payload, topics)
    stats = ('state', 'sample_count', 'valid_count', 'unknown_count', 'high_open', 'low_open', 'flat_open',
             'average_auction_pct', 'reference_checked_count', 'reference_count')
    compact = _pick(carryover, 'state', 'previous_date', 'scope', 'reason')
    for key, fields in (('prior_limit_up', stats), ('highest_candidates', (*stats, 'previous_board_count')),
                        ('price_reference', ('state', 'count', 'checked_count', 'unknown_count', 'confirmed_limit_price'))):
        if key in carryover:
            compact[key] = _pick(carryover[key], *fields)
            if key != 'prior_limit_up':
                compact[key]['stocks'] = [_pick(row, 'instrument_id', 'name', 'change_pct', 'previous_board_count')
                                           for row in carryover[key].get('stocks', ())[:2]]
    compact['directions'] = [{**_pick(row, 'topic_code', 'name', 'kind', 'members_observed_date', *stats),
        'stocks': [_pick(stock, 'instrument_id', 'name', 'change_pct', 'previous_board_count')
                   for stock in row.get('stocks', ())[:2]]} for row in carryover.get('directions', ())[:2]]
    data['carryover'] = compact
    metric_rows = [(key, label, summary.get(key), unit) for key, label, unit in (
        ('valid', '竞价有效候选', '家'), ('missing', '竞价缺报候选', '家'),
        ('up', '候选高开', '家'), ('down', '候选低开', '家'),
        ('median_pct', '候选竞价涨幅中位数', '%'), ('amount', '候选竞价成交额', 'CNY'))]
    return _block('auction', day, metadata, data, metric_rows)


def _discovery(connection, day, metadata, opening_metadata, *, include_auction_opening=True):
    from app.services.auction_discovery import read_view

    view = read_view(day, connection=connection)
    if (view.get('date') != day or any(view.get(key) != value for key, value in metadata.items())
            or not auction_followup.model_input_allowed(view)):
        raise ValueError('竞价发现与所选日冻结来源不符')
    stock_fields = ('instrument_id', 'name', 'auction_pct', 'auction_amount', 'previous_board_count')
    directions = _directions([
        {**row, 'topic_code': row['instrument_id'],
         'stocks': [_pick(stock, *stock_fields) for stock in row['stocks'][:2]]}
        for row in view.get('directions', ())
    ], order='stock_count', fields=('stock_count', 'median_auction_pct', 'total_auction_amount', 'stocks'))
    data = {'state': view['state'],
            'scope': '供应商竞价异动中已核验终态的样本，行业与题材可能重叠。成交额单位为元，涨幅单位为%。',
            'coverage': _pick(view.get('coverage') or {}, 'pages', 'events', 'candidates', 'valid', 'has_more'),
            'directions': directions, 'reason': view.get('reason'), 'captured_at_ms': view.get('captured_at_ms')}
    if not any(directions.values()):
        data['stock_examples'] = [_pick(stock, *stock_fields) for stock in view.get('stocks', ())[:3]]
    metric_rows = [('valid', '竞价发现已核验终态样本', data['coverage'].get('valid'), '家')]
    if opening_metadata:
        try:
            opening = auction_followup.read_opening(connection, day, opening_metadata)
            comparison = auction_followup.compare(view, opening)
        except (ValueError, TypeError, KeyError, HTTPException):
            comparison = None
        if comparison:
            compare_by_code = {row['instrument_id']: row for row in comparison['directions']}
            for group in directions.values():
                for row in group:
                    if compare := compare_by_code.get(row['topic_code']):
                        row['opening_comparison'] = _pick(compare, 'comparable', 'median_change_pp')
            data['opening_comparison'] = _pick(
                comparison, 'sample_count', 'comparable', 'strengthened', 'faded',
                'turned_positive', 'opening_at', 'examples', 'scope')
            metadata = {**metadata, 'opening': opening_metadata}
            metric_rows.extend((key, label, comparison[key], '家') for key, label in (
                ('comparable', '竞价与开盘首轮同股可比样本'), ('strengthened', '开盘首轮较竞价涨幅扩大'),
                ('faded', '开盘首轮较竞价涨幅回落'), ('turned_positive', '竞价未涨且开盘首轮转涨')))
        else:
            data['opening_state'] = 'unknown'
    elif include_auction_opening:
        data['opening_state'] = 'not_saved'
    block = _block('auction_discovery', day, metadata, data, metric_rows)
    if not include_auction_opening:
        block['evidence']['title'] = '竞价发现'
    return block


def _noon(payload, metadata, day):
    summary = payload.get('summary') or {}
    breadth, amount = summary.get('breadth') or {}, summary.get('amount') or {}
    events = {name: _pick((payload.get('events') or {}).get(name) or {}, 'state', 'count', 'declared_total', 'reason')
              for name in ('limit_up', 'limit_down', 'limit_break')}
    directions = _directions(payload.get('sectors', []), order='change_pct',
                             fields=('change_pct', 'member_limit_up_count', 'member_limit_up_state'))
    data = {'state': 'partial' if payload.get('gaps') else 'observed_sample',
            'scope': '同日午休采集样本；跨域非同秒快照，不等于收盘结果或资金流向',
            'units': {'amount': 'CNY', 'change_pct': '%', 'counts': '家'},
            'breadth': _pick(breadth, 'up', 'down', 'flat', 'valid', 'total', 'missing'),
            'amount': _pick(amount, 'value', 'valid', 'total'), 'events': events,
            'directions': directions,
            'direction_scope': '已保存行业/概念指数涨幅各前3；成员涨停仅复用原冻结统计，归属非互斥',
            'source_gaps': payload.get('gaps', []),
            'binding': _pick(metadata, 'snapshot_id', 'content_sha256')}
    metrics = [(key, label, breadth.get(key), '家') for key, label in (
        ('up', '午间上涨样本',), ('down', '午间下跌样本'), ('missing', '午间缺失报价'))]
    metrics += [('amount', '午间已留存累计成交额', amount.get('value'), 'CNY')]
    metrics += [(name, '午间' + label + '来源池', events[name].get('count'), '家')
                for name, label in (('limit_up', '涨停'), ('limit_down', '跌停'), ('limit_break', '炸板'))]
    return _block('noon', day, metadata, data, metrics)


def _close(payload, metadata, day):
    summary = payload.get('summary') or {}
    amount = summary.get('amount') or {}
    common = (summary.get('breadth') or {}).get('common') or {}
    afternoon = common.get('afternoon') or {}
    classes = payload.get('classification_counts') or {}
    groups = [*classes.get('industries', []), *classes.get('topics', [])]
    directions = _directions(groups, order='close_limit_up_count', fields=(
        'count', 'valid_count', 'close_limit_up_count', 'mean_afternoon_change_pct', 'unknown_count', 'state'))
    data = {'state': metadata.get('state', 'unknown'),
            'scope': '同代码午间与收盘交集的事后对照；成交额增量不是净流入，缺逐条时刻不称精确午后时序',
            'units': {'delta': 'CNY', 'mean_pct': '%', 'median_pct': '%', 'counts': '家'},
            'afternoon': _pick(afternoon, 'up', 'down', 'flat', 'valid', 'total', 'mean_pct', 'median_pct'),
            'amount': _pick(amount, 'state', 'delta', 'valid_count', 'unknown_count', 'complete', 'reason'),
            'classification': _pick(classes, 'state', 'classified_count', 'unclassified_count'),
            'directions': directions,
            'direction_scope': '重点名单收盘涨停各前3；非整板块统计，融资融券等宽标签不是主线或资金方向；行业/概念可重叠',
            'source_gaps': payload.get('gaps', []),
            'binding': _pick(metadata, 'snapshot_id', 'review_id', 'revision', 'noon_sha256', 'result_sha256', 'close_input_sha256')}
    metrics = [(key, label, afternoon.get(key), unit) for key, label, unit in (
        ('valid', '午间至收盘可比样本', '家'), ('up', '快照后上涨样本', '家'),
        ('down', '快照后下跌样本', '家'), ('median_pct', '快照后涨幅中位数', '%'))]
    metrics += [('amount_delta', '同股快照后成交额增量（非净流入）', amount.get('delta'), 'CNY')]
    block = _block('noon_close', day, metadata, data, metrics)
    # Only add metrics for direction rows that survived the bounded projection.
    # These are frozen focus-cohort counts, never whole-sector denominators.
    for kind, rows in block['evidence']['data']['directions'].items():
        for row in rows:
            for key, label in (('close_limit_up_count', '重点样本收盘涨停'),
                               ('valid_count', '午间至收盘可比重点样本')):
                block['metrics'].append({
                    'metric_id': 'day_brief:noon_close:' + kind + ':' + str(row.get('topic_code')) + ':' + key,
                    'label': str(row.get('name', row.get('topic_code'))) + label,
                    'value': _number(row.get(key)), 'unit': '家', 'as_of': day, 'price_basis': 'none',
                    'dates': {'trade_date': day, 'members_observed_date': row.get('members_observed_date')},
                    'scope': '原午间冻结归属重点样本；非整板块，宽标签非主线，归属非互斥'})
    return block


def build_briefs(day, *, source_path=None, kinds=None, cutoff=None, include_auction_opening=True):
    _day(day)
    observe = lambda: fingerprints(day) if source_path is None else fingerprints(day, source_path=source_path)
    before = observe()
    result = {'fingerprints': before, 'blocks': [], 'gaps': []}
    wanted = set(kinds) if kinds is not None else {*TABLES, 'closing_pools'}
    connection = _connect(source_path)
    if connection is None:
        for kind in TABLES:
            if kind not in wanted:
                continue
            result['gaps'].append({'capability': kind + '_brief', 'state': 'unknown', 'date': day,
                                   'reason': '所选日未留存此时点资料，不能倒造或称为今日数据'})
        return result
    try:
        for kind in TABLES:
            if kind not in wanted:
                continue
            metadata = before[kind]
            reason = None
            if metadata is None:
                reason = '所选日未采集或已清理此时点资料，不以更早日期替代'
            else:
                try:
                    if cutoff is not None and kind == 'auction':
                        from datetime import datetime
                        ready = max(int(datetime.fromisoformat(metadata['collected_end_at']).timestamp() * 1000),
                                    metadata.get('recorded_at_ms') or 0)
                        if ready > int(cutoff.timestamp() * 1000):
                            raise ValueError('竞价在午评截止之后才留存')
                    limit = MAX_REVIEW_BYTES if kind == 'noon_close' else MAX_PAYLOAD_BYTES
                    if not 0 < metadata['payload_bytes'] <= limit:
                        raise ValueError('已保存正文超出单日有界读取范围')
                    field = 'result_json' if kind == 'noon_close' else 'payload_json'
                    table = TABLES[kind]
                    row = connection.execute('SELECT * FROM ' + table + ' WHERE ' +
                        ('review_id=?' if kind == 'noon_close' else 'snapshot_id=?'),
                        (metadata['review_id' if kind == 'noon_close' else 'snapshot_id'],)).fetchone()
                    encoded = row[field]
                    expected = metadata['result_sha256' if kind == 'noon_close' else 'content_sha256']
                    if sha256(encoded.encode()).hexdigest() != expected:
                        raise ValueError('已保存正文SHA不一致')
                    payload = validate_snapshot(row) if kind == 'auction' else json.loads(encoded)
                    if payload.get('date') not in (None, day) or row['trade_date'] != day:
                        raise ValueError('正文日期与所选观察日不一致')
                    if (payload.get('egress_allowed') is False or payload.get('ai_processing_allowed') is False
                            or any(source.get('egress_allowed') is False or source.get('ai_processing_allowed') is False
                                   for source in payload.get('sources', []) if isinstance(source, dict))):
                        raise ValueError('原时点材料明确禁止外发或AI处理')
                    if kind == 'noon' and payload.get('version') != 'noon-snapshot-v1':
                        raise ValueError('午评版本未核验')
                    if kind == 'noon' and (payload.get('date') != day or any(
                            payload.get(key) != metadata[key] for key in ('collected_start_at', 'collected_end_at'))):
                        raise ValueError('午评采集日期或窗口未与原行对账')
                    if kind == 'noon_close' and (before['noon'] is None
                            or metadata['snapshot_id'] != before['noon']['snapshot_id']
                            or metadata['noon_sha256'] != before['noon']['content_sha256']):
                        raise ValueError('收盘回看与原午评身份不一致')
                    binding = metadata
                    if kind == 'noon_close':
                        binding = {**metadata, **{'noon_' + key: before['noon'][key]
                                  for key in ('collected_start_at', 'collected_end_at')}}
                    result['blocks'].append({'auction': _auction, 'noon': _noon, 'noon_close': _close}[kind](payload, binding, day))
                except (ValueError, TypeError, KeyError, IndexError):
                    reason = '所选日正文、日期、来源SHA或必要有界摘要未通过核验，保留未知'
            if reason:
                result['gaps'].append({'capability': kind + '_brief', 'source': 'saved_' + kind,
                                       'state': 'unknown', 'date': day, 'reason': reason})
            elif kind == 'auction' and (discovery := before.get('auction_discovery')):
                try:
                    if cutoff is not None and discovery['recorded_at_ms'] > int(cutoff.timestamp() * 1000):
                        continue
                    opening = before.get('auction_opening') if include_auction_opening else None
                    if cutoff is not None and opening and opening.get('finished_at_ms', 0) > int(cutoff.timestamp() * 1000):
                        opening = None
                    result['blocks'].append(_discovery(connection, day, discovery, opening,
                                                        include_auction_opening=include_auction_opening))
                except (ValueError, TypeError, KeyError, HTTPException):
                    result['gaps'].append({'capability': 'auction_discovery_brief', 'source': 'saved_auction_discovery',
                                           'state': 'unknown', 'date': day, 'reason': '竞价发现来源未通过核验'})
        if 'closing_pools' in wanted and (closing := before.get('closing_pools')):
            from app.services.ai_closing_read import build_block
            try:
                result['blocks'].append(build_block(connection, day, closing))
            except (OSError, ValueError, TypeError, KeyError, AttributeError, HTTPException):
                result['gaps'].append({'capability': 'closing_pools_brief', 'source': 'saved_closing_pools',
                    'state': 'unknown', 'date': day, 'reason': '原收盘锚点、同日正式代际或来源SHA未通过核验'})
    finally:
        connection.close()
    if before != observe():
        raise HTTPException(409, '构建期间时点资料版本变化，请重新预览')
    return result
