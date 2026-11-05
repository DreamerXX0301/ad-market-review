"""Bounded AI facts from the archived closing anchor's immutable generation."""
from datetime import datetime
from hashlib import sha256
import json
import re

from fastapi import HTTPException

from app.core import dumps
from app.services.auction_followup import model_input_allowed

MAX_ANCHOR = 384 * 1024


def metadata(connection, day):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='market_intraday_brief'").fetchone():
        return None
    row = connection.execute('''SELECT source_sha256,generated_at_ms,length(CAST(body_json AS BLOB)) AS payload_bytes,
        json_extract(body_json,'$.anchor.id') AS snapshot_id FROM market_intraday_brief
        WHERE trade_date=? AND slot='close' AND json_valid(body_json) LIMIT 1''', (day,)).fetchone()
    return dict(row) if row else None


def build_block(connection, day, expected):
    from app.services import ai_market_brief as briefs
    from app.services.intraday_close import closing_data, close_frame, frame_row
    from app.services.intraday_board_changes import build_changes, attach_current_states
    from app.services.ai_intraday_read import _read_snapshot, _source_rows, _stock_samples, _change_summary, _omit_sample
    from app.services.noon_collection import CHINA
    from app.analytics.market_storage import safe_object, verify_immutable_file
    from app.services.noon_membership_context import _read_json, MAX_REFERENCE_BYTES, MAX_MANIFEST_BYTES
    from app.core import published_dir
    from app.services.ai_intraday_evidence import membership_inputs, attach_change_distributions

    if (type(expected['payload_bytes']) is not int or not 0 < expected['payload_bytes'] <= MAX_ANCHOR
            or not re.fullmatch('[a-f0-9]{64}', str(expected['source_sha256']))):
        raise ValueError('原收盘锚点超出读取预算')
    row = connection.execute('''SELECT body_json FROM market_intraday_brief
        WHERE trade_date=? AND slot='close' AND source_sha256=? AND generated_at_ms=?''',
        (day, expected['source_sha256'], expected['generated_at_ms'])).fetchone()
    stored = json.loads(row['body_json']) if row else {}
    anchor = stored.get('anchor') or {}
    if ((anchor.get('date'), anchor.get('slot'), anchor.get('id')) != (day, 'close', expected['snapshot_id'])
            or not model_input_allowed(stored) or not model_input_allowed(anchor)):
        raise ValueError('原收盘锚点日期或AI权限不符')
    context = closing_data(day, expected['snapshot_id'])
    root = published_dir().resolve()
    for name, key, cap in (('manifest.json', 'content_sha256', MAX_MANIFEST_BYTES),
                           ('reference.json', 'reference_sha256', MAX_REFERENCE_BYTES)):
        path = safe_object(root, expected['snapshot_id'] + '/' + name)
        size = path.stat().st_size
        if not 0 < size <= cap:
            raise ValueError('原收盘来源超过读取预算')
        verify_immutable_file(path, context['source_binding'][key], size)
        if not model_input_allowed(_read_json(path, cap)):
            raise ValueError('原收盘来源明确禁止AI处理')
    if not model_input_allowed(context['reference']):
        raise ValueError('原收盘来源禁止AI处理')
    if anchor.get('collected_end_at') != context['source_binding']['collected_end_at']:
        raise ValueError('原收盘锚点时点与正式代际不符')
    now = datetime.now(CHINA)
    published = datetime.fromisoformat(context['published_at'])
    if published > now:
        raise ValueError('收盘资料尚未发布')
    frame = close_frame(context)
    previous = previous_row = None
    records = _source_rows(connection, day, ('1430',))
    record = records.get('1430')
    if record and record['status'] in ('succeeded', 'partial'):
        try:
            previous, previous_row = _read_snapshot(connection, record, day, '1430', published, include_row=True)
        except (ValueError, TypeError, KeyError, HTTPException):
            pass
    frame['board_changes'] = build_changes(frame, previous, current_row=frame_row(frame), previous_row=previous_row)
    attach_current_states(frame)
    sectors, members = membership_inputs(context['reference'].get('topics', ()), day)
    attach_change_distributions(frame, previous, sectors, members, {})
    for pool in context['pools'].values():
        for stock in pool['items']:
            stock['_ai_observed_date'] = day
    pools = {}
    for key, pool in context['pools'].items():
        rows = frame['board_pool_evidence'][key]['items']
        for stock in rows:
            stock['_ai_observed_date'] = day
        pools[key] = {'state': pool['state'], 'count': pool['count'],
            'source_count': pool['source_count'], 'samples': _stock_samples(rows, '', 3),
            'omitted_count': max(0, len(rows) - min(3, len(rows)))}
    data = {'state': 'observed' if all(pool['state'] == 'observed' for pool in pools.values()) else 'partial',
        'scope': '原指定日正式代际来源池与同日正成交量收盘核验；partial为下界，unknown不是0；仅返回有限股票样本。',
        'units': {'change_pct': '%', 'counts': '家'}, 'pools': pools,
        'height': {key: frame['ladder'].get(key) for key in ('highest_board', 'height_state', 'first_board_count',
                                                          'multi_board_count', 'unknown_height_count')},
        'board_changes': _change_summary(frame['board_changes'], '', False)}
    while len(dumps(data).encode()) > briefs.MAX_BRIEF_DATA_BYTES:
        if _omit_sample(data):
            continue
        largest = max(pools.values(), key=lambda pool: len(dumps(pool['samples']).encode()))
        if not largest['samples']:
            raise ValueError('收盘必要事实超出有界摘要预算')
        largest['samples'].pop()
        largest['omitted_count'] += 1
        data['samples_omitted_for_budget'] = True
    binding = {**context['source_binding'], 'anchor_source_sha256': expected['source_sha256'],
               'anchor_body_sha256': sha256(row['body_json'].encode()).hexdigest()}
    metrics = [(key, label, pools[key]['count'], '家') for key, label in (
        ('limit_up', '原收盘代际已核验封板',), ('limit_break', '原收盘代际已核验炸板'),
        ('limit_down', '原收盘代际已核验跌停'))]
    return briefs._block('closing_pools', day, binding, data, metrics)
