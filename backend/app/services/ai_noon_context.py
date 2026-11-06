"""A noon read contains only the same recovery source's known morning prefix."""
from datetime import datetime

from app.services.intraday_brief import ORDER, TIMELINE
from app.services.sync_clock import CHINA

_BRIEF_STAGE = {'auction_brief': ('auction', 0), 'auction_discovery_brief': ('auction', 1),
                'noon_brief': ('noon', 0), 'closing_pools_brief': ('close', 0), 'noon_close_brief': ('close', 1)}


def _timeline_evidence_order(row):
    kind = row['kind']
    if kind.startswith('intraday:'):
        return ORDER.get(kind.removeprefix('intraday:'), len(ORDER)), 0
    stage, within_stage = _BRIEF_STAGE.get(kind, (None, 0))
    return ORDER.get(stage, len(ORDER)), within_stage


def add_noon_context(result, day, selected, heads, now):
    from app.services.ai_read_tools import _add_blocks, _gap, _paths, _result
    from app.services.ai_intraday_read import fill_intraday

    metadata = selected.get('noon') or {}
    result['summary']['phases'] = {key: value for key, value in result['summary']['phases'].items()
                                   if key in ('auction', 'noon')}
    result['source_bindings']['intraday'] = {}
    if not metadata:
        result['summary']['noon_context'] = {'state': result['summary']['phases'].get('noon', {}).get('phase_state', 'unknown'),
            'reason': '本日午评尚未留存，未借其他日期或后续时点构造累计资料'}
        return
    try:
        cutoff = datetime.fromisoformat(metadata['collected_end_at'])
        if cutoff.tzinfo is None:
            raise ValueError('午评截止时点缺少时区')
        cutoff = cutoff.astimezone(CHINA)
        if (cutoff.date().isoformat() != day or cutoff > now
                or (metadata.get('recorded_at_ms') or 0) > int(now.timestamp() * 1000)):
            raise ValueError('午评截止日期或时间不符')
    except (ValueError, TypeError, KeyError):
        _gap(result, 'noon_context', '缺少可核验的本日午评截止时点，未借用其他时点', 'unknown')
        result['summary']['noon_context'] = {'state': 'unknown', 'reason': '午评实际截止时点尚不可核验'}
        if 'noon' in result['summary']['phases']:
            result['summary']['phases']['noon']['phase_state'] = 'unknown'
        return
    _add_blocks(result, day, selected, ('noon',))
    if not any(row['kind'] == 'noon_brief' for row in result['evidence']):
        result['summary']['noon_context'] = {'state': 'unknown', 'reason': '本份午评正文或AI处理权限未通过核验'}
        result['summary']['phases']['noon']['phase_state'] = 'unknown'
        return
    source = metadata['source']
    paths = [(label, path) for label, path in _paths() if label == source]
    original = heads.get(source, {})
    auction = original.get('auction')
    if auction:
        _add_blocks(result, day, {'auction': {**auction, 'source': source}}, ('auction',), cutoff=cutoff)
    morning = _result('read_intraday_data', day)
    morning_slots = tuple(key for key, _ in TIMELINE[1:ORDER['noon']])
    fill_intraday(morning, day, 'all', '', now, allowed_slots=morning_slots, cutoff=cutoff, source_paths=paths)
    result['evidence'].extend(morning['evidence'])
    result['gaps'].extend(morning['gaps'])
    result['source_bindings']['noon_morning'] = morning['source_bindings']
    result['summary']['board_change_scope'] = morning['summary']['board_change_scope']
    phases = {'auction': {'phase_state': 'available' if any(row['kind'] == 'auction_brief'
                for row in result['evidence']) else 'not_collected'}, **morning['summary']['phases'],
              'noon': {'phase_state': 'available'}}
    result['summary']['noon_context'] = {'cutoff_at': cutoff.isoformat(), 'phases': phases,
        'scope': '同日原竞价、截至午评实际结束时点已完成留存的上午观察和本份午评；缺档不补入后来或下午资料。'}
    # The consistency guard still compares the complete local identities; the
    # model receives only the prefix's bindings, never later close identities.
    has_auction = any(row['kind'] == 'auction_brief' for row in result['evidence'])
    if not has_auction and 'auction' in result['summary']['phases']:
        result['summary']['phases']['auction'].update(phase_state='not_collected', capture_window=None,
            source_ids={}, reason='截至午评实际时点没有可核验的竞价原文，不以更晚留存补入')
    result['source_bindings']['intraday'] = {source: {key: original[key] for key in ('auction', 'noon')
                                                       if original.get(key) and (key == 'noon' or has_auction)}}
    result['evidence'].sort(key=_timeline_evidence_order)


def add_close_context(result, day, selected, heads, now):
    from app.services.ai_read_tools import _add_blocks, _gap, _paths, _result
    from app.services.ai_intraday_read import fill_intraday

    _add_blocks(result, day, selected, ('close',))
    binding = result['source_bindings'].get('closing_pools') or {}
    try:
        cutoff = datetime.fromisoformat(binding['collected_end_at'])
        published = datetime.fromisoformat(binding['published_at'])
        if (cutoff.tzinfo is None or cutoff.date().isoformat() != day or cutoff > now or published > now):
            raise ValueError('正式收盘时点尚不可知')
    except (ValueError, KeyError, TypeError):
        result['summary']['close_context'] = {'state': 'unknown', 'reason': '缺少可核验的正式收盘锚点，未借当前代际构造全天前缀'}
        _gap(result, 'close_context', '正式收盘原锚点或发布时间不可核验，全天串联保持未知', 'unknown')
        return
    source = selected['close']['source']
    original = heads.get(source, {})
    preceding = {}
    for key in ('auction', 'noon'):
        metadata = original.get(key)
        if metadata:
            try:
                at = datetime.fromisoformat(metadata['collected_end_at'])
                ready = max(at.timestamp() * 1000, metadata.get('recorded_at_ms') or 0)
                if at.tzinfo is not None and at.date().isoformat() == day and ready <= cutoff.timestamp() * 1000:
                    preceding[key] = {**metadata, 'source': source}
            except (ValueError, TypeError, KeyError):
                pass
    _add_blocks(result, day, preceding, tuple(preceding), cutoff=cutoff)
    stages = _result('read_intraday_data', day)
    fill_intraday(stages, day, 'all', '', now, cutoff=cutoff,
                  source_paths=[(label, path) for label, path in _paths() if label == source])
    result['evidence'].extend(stages['evidence'])
    result['gaps'].extend(stages['gaps'])
    result['source_bindings']['close_intraday'] = stages['source_bindings']
    result['summary']['board_change_scope'] = stages['summary']['board_change_scope']
    phases = {'auction': {'phase_state': 'available' if any(row['kind'] == 'auction_brief' for row in result['evidence']) else 'not_collected'}}
    stage_phases = stages['summary']['phases']
    for key, _ in TIMELINE[1:-1]:
        if key == 'noon':
            phases[key] = {'phase_state': 'available' if any(row['kind'] == 'noon_brief' for row in result['evidence'])
                           else 'not_collected'}
        elif key in stage_phases:
            phases[key] = stage_phases[key]
    phases['close'] = {'phase_state': 'available'}
    result['summary']['close_context'] = {'cutoff_at': cutoff.isoformat(), 'published_at': published.isoformat(),
        'phases': phases, 'scope': '同日原竞价、截至正式收盘时点已完成留存的全部盘中观察、原午评和正式收盘；缺档不补入后来资料。'}
    result['source_bindings']['intraday'] = {source: {key: value for key, value in original.items()
        if key in ('auction', 'noon', 'noon_close', 'closing_pools') and value}}
    result['evidence'].sort(key=_timeline_evidence_order)
