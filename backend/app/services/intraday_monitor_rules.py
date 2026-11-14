"""Pure, sampled intraday observations against already-frozen raw-price levels.

This module never reads a database or requests quotes. A snapshot is a point in
time, so cumulative daily highs/lows cannot prove an intervening price path.
These observations deliberately do not change any existing closing-price rule.
"""
from copy import deepcopy
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import re


VERSION = 'intraday-monitor-rules-v1'
CHINA = timezone(timedelta(hours=8))
SLOTS = tuple(
    f'{minute // 60:02d}:{minute % 60:02d}'
    for start, end in ((570, 690), (790, 900))
    for minute in range(start, end + 1, 10)
)
SLOT_POSITIONS = {slot: index for index, slot in enumerate(SLOTS)}
BUFFER_ABSOLUTE = 0.01
BUFFER_RATIO = 0.001
SOURCE_CLOCK_SKEW_MS = 5000
KINDS = {'support', 'resistance', 'risk'}
SOURCES = {'manual_line', 'automatic_zone', 'automatic_level'}
TYPES = {'touch_support', 'support_reclaim', 'support_breakdown',
         'pressure_breakout', 'pressure_fallback', 'risk_breakdown'}
TITLES = {
    'touch_support': '盘中采样触及支撑',
    'support_reclaim': '盘中采样站回支撑上方',
    'support_breakdown': '盘中采样支撑失守',
    'pressure_breakout': '盘中采样越过压力',
    'pressure_fallback': '盘中采样回落压力区',
    'risk_breakdown': '盘中采样风险线失守',
}


def _day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value > 0)


def _stamp_day(value):
    if not _number(value):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, CHINA).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _empty_state(trade_date, slot_key):
    return {'version': VERSION, 'trade_date': trade_date, 'slot_key': slot_key,
            'levels': {}, 'last_quote': None}


def _quote_view(quote):
    return {key: deepcopy(quote.get(key)) for key in (
        'instrument_id', 'latest_price', 'received_at_ms', 'source_time_ms',
        'quality', 'price_basis')}


def _level_error(level, trade_date):
    if not isinstance(level, dict):
        return '冻结价位格式无效'
    if not isinstance(level.get('key'), str) or not level['key'].strip():
        return '冻结价位缺少带版本的规则标识'
    if level.get('kind') not in KINDS or level.get('source_kind') not in SOURCES:
        return '冻结价位类型或来源未能核验'
    if not isinstance(level.get('source_id'), str) or not level['source_id']:
        return '冻结价位缺少来源标识'
    revision = level.get('revision')
    if (isinstance(revision, bool) or not isinstance(revision, (str, int))
            or isinstance(revision, int) and revision < 0
            or isinstance(revision, str) and not revision.strip()):
        return '冻结价位版本无效'
    if not _number(level.get('lower')) or not _number(level.get('upper')) or level['lower'] > level['upper']:
        return '冻结价位区间无效'
    if level.get('price_basis') != 'none':
        return '盘中规则只接受已换算到观察日的原价位'
    if not _day(level.get('baseline_date')) or level['baseline_date'] > trade_date:
        return '冻结价位基准日无效或晚于观察日'
    if not isinstance(level.get('evidence', {}), dict):
        return '冻结价位依据格式无效'
    return None


def _signature(level):
    values = {key: level.get(key) for key in (
        'kind', 'source_kind', 'source_id', 'revision', 'lower', 'upper',
        'baseline_date', 'price_basis')}
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _position(price, lower, upper):
    """Keep the actual zone intact; only crossings use an exterior buffer.

    The buffer is max(0.01 yuan, 0.1% of the zone midpoint). A quote within the
    actual [lower, upper] is always inside, even if the zone is very narrow.
    The exterior buffer bands are neutral: no new event and no rearming.
    """
    buffer = max(BUFFER_ABSOLUTE, (lower + upper) / 2 * BUFFER_RATIO)
    if lower <= price <= upper:
        return 'inside', buffer
    if price < lower - buffer:
        return 'below', buffer
    if price > upper + buffer:
        return 'above', buffer
    return 'buffer', buffer


def _transition(kind, previous, position, slot_key):
    before = previous.get('position')
    current = deepcopy(previous)
    current['observed_position'] = position
    if position == 'buffer':
        return current, None, None
    current['position'] = position
    if before == position:
        return current, previous.get('last_type'), 'holding'

    event, phase = None, 'observed'
    if kind == 'support':
        if position == 'inside':
            current.update(episode=slot_key, touched_in_episode=True, approach=before)
            event = 'touch_support'
        elif position == 'above' and before in ('inside', 'below'):
            if before == 'below':
                current.update(episode=slot_key, touched_in_episode=False, approach=before)
            event = 'support_reclaim'
        elif position == 'below' and before in ('inside', 'above'):
            if before == 'above':
                current.update(episode=slot_key, touched_in_episode=False, approach=before)
            event = 'support_breakdown'
    elif kind == 'resistance':
        if position == 'above' and before in ('inside', 'below'):
            if before == 'below' or previous.get('last_type') == 'pressure_fallback':
                current.update(episode=slot_key, touched_in_episode=before == 'inside', approach=before)
            event = 'pressure_breakout'
        elif before == 'above' and position in ('inside', 'below'):
            event = 'pressure_fallback'
        elif position == 'inside':
            current.update(episode=slot_key, touched_in_episode=True, approach=before)
        elif before == 'inside' and position == 'below' and previous.get('last_type') == 'pressure_fallback':
            event = 'pressure_fallback'
            phase = 'holding'
    elif kind == 'risk' and position == 'below' and before in ('inside', 'above'):
        current.update(episode=slot_key, touched_in_episode=False, approach=before)
        event = 'risk_breakdown'

    current['last_type'] = event
    return current, event, phase


def evaluate_snapshot(levels, quote, previous_state=None, *, trade_date, slot_key):
    """Return a new JSON-safe state, sampled observations and explicit unknowns.

    First samples, changed level identities, and a missed trading slot seed a
    fresh position. They do not retroactively manufacture crossings. 11:30 to
    13:10 is a continuous pair of trading slots. An invalid/replayed sample
    leaves the last valid state intact; a later gap therefore cannot confirm a
    path through the missing interval.
    """
    prior = deepcopy(previous_state) if isinstance(previous_state, dict) else _empty_state(trade_date, slot_key)
    symbol = quote.get('instrument_id') if isinstance(quote, dict) else None

    def unknown(reason):
        return {'state': prior, 'observations': [], 'unknowns': [
            {'instrument_id': symbol, 'rule_key': None, 'slot_key': slot_key, 'reason': reason}]}

    if not _day(trade_date) or slot_key not in SLOT_POSITIONS:
        return unknown('观察交易日或十分钟交易时隙无效')
    if not isinstance(levels, list):
        return unknown('冻结价位列表无效')
    if not isinstance(quote, dict) or quote.get('valid') is not True:
        return unknown((quote.get('reason') if isinstance(quote, dict) else None) or '本次没有可核验的盘中报价')
    if not isinstance(symbol, str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', symbol):
        return unknown('盘中报价缺少明确的个股标识')
    if quote.get('price_basis') != 'none' or not _number(quote.get('latest_price')):
        return unknown('盘中最新价或原价口径未能核验')
    if quote.get('trade_date', trade_date) != trade_date or _stamp_day(quote.get('received_at_ms')) != trade_date:
        return unknown('盘中报价交易日与本次观察日不一致')
    source_time = quote.get('source_time_ms')
    if quote.get('quality') not in ('quote_time', 'batch_time_only'):
        return unknown('盘中报价时间质量未能核验')
    if quote['quality'] == 'quote_time' and source_time is None:
        return unknown('逐股报价缺少来源时间')
    if source_time is not None and _stamp_day(source_time) != trade_date:
        return unknown('盘中来源时间与本次交易日不一致')
    if source_time is not None and source_time > quote['received_at_ms'] + SOURCE_CLOCK_SKEW_MS:
        return unknown('来源时间晚于本地接收时间，不能确认采样顺序')

    last = prior.get('last_quote')
    same_day = prior.get('version') == VERSION and prior.get('trade_date') == trade_date
    same_stock = isinstance(last, dict) and last.get('instrument_id') == symbol
    old_slot = SLOT_POSITIONS.get(prior.get('slot_key'))
    if same_day and same_stock and old_slot is not None:
        if SLOT_POSITIONS[slot_key] <= old_slot:
            return unknown('重复或倒序交易时隙不重复判断')
        if quote['received_at_ms'] <= (last.get('received_at_ms') or 0):
            return unknown('本地接收时间未前进，不能确认采样顺序')
        if source_time is not None and last.get('source_time_ms') is not None and source_time <= last['source_time_ms']:
            return unknown('来源时间重复或倒退，不能把旧报价当作新采样')
    continuous = bool(same_day and same_stock and old_slot is not None
                      and SLOT_POSITIONS[slot_key] == old_slot + 1)
    old_levels = prior.get('levels') if continuous and isinstance(prior.get('levels'), dict) else {}
    state = _empty_state(trade_date, slot_key)
    state['last_quote'] = _quote_view(quote)
    state['continuity'] = 'continuous' if continuous else 'reseeded'
    observations, unknowns = [], []
    keys = [level['key'] for level in levels if isinstance(level, dict) and isinstance(level.get('key'), str)]
    duplicate_keys = {key for key, count in Counter(keys).items() if count > 1}
    seen = set()
    for level in levels:
        key = level.get('key') if isinstance(level, dict) and isinstance(level.get('key'), str) else None
        error = _level_error(level, trade_date)
        if key in duplicate_keys:
            error = '冻结价位规则标识重复，不能任取一条判断'
        if error:
            if key not in seen:
                unknowns.append({'instrument_id': symbol, 'rule_key': key, 'slot_key': slot_key, 'reason': error})
            seen.add(key)
            continue
        position, buffer = _position(quote['latest_price'], level['lower'], level['upper'])
        signature = _signature(level)
        old = old_levels.get(key)
        seeded = not isinstance(old, dict) or old.get('signature') != signature
        if seeded:
            current = {'position': position, 'observed_position': position, 'episode': slot_key,
                       'touched_in_episode': position == 'inside', 'approach': None, 'last_type': None}
            event, phase = None, None
        else:
            current, event, phase = _transition(level['kind'], old, position, slot_key)
        current.update(signature=signature, last_price=quote['latest_price'],
                       last_observed_at_ms=quote['received_at_ms'], buffer=buffer)
        state['levels'][key] = current
        if event:
            evidence = {
                'sampling_only': True, 'quality': quote['quality'], 'baseline_date': level['baseline_date'],
                'price_basis': 'none', 'level_evidence': deepcopy(level.get('evidence', {})),
                'previous_sample': deepcopy(last), 'current_sample': _quote_view(quote),
                'previous_position': old.get('position'), 'position': position,
                'touch_observed': bool(current.get('touched_in_episode')),
                'approach': current.get('approach'),
                'parameters': {'buffer_absolute': BUFFER_ABSOLUTE, 'buffer_ratio': BUFFER_RATIO,
                               'applied_buffer': buffer, 'inside_uses_original_bounds': True,
                               'source_clock_skew_ms': SOURCE_CLOCK_SKEW_MS},
            }
            observations.append({
                'instrument_id': symbol, 'trade_date': trade_date, 'slot_key': slot_key,
                'type': event, 'phase': phase, 'episode': current['episode'],
                'observed_at_ms': quote['received_at_ms'], 'source_time_ms': source_time,
                'price': quote['latest_price'], 'lower': level['lower'], 'upper': level['upper'],
                'source_kind': level['source_kind'], 'source_id': level['source_id'],
                'rule_key': key, 'label': level.get('label') or TITLES[event], 'title': TITLES[event],
                'revision': level['revision'], 'sampling_only': True, 'evidence': evidence,
            })
    return {'state': state, 'observations': observations, 'unknowns': unknowns}


def merge_observations(observations):
    """Group compatible sampled reasons without discarding any source evidence.

    Group bounds are the shared intersection, not an expanding union. This
    prevents A-overlaps-B-overlaps-C from merging unrelated A and C levels.
    Persistent merging across later slots belongs to the caller's event store.
    """
    valid, seen = [], set()
    for item in observations:
        if (not isinstance(item, dict) or item.get('type') not in TYPES
                or item.get('phase') not in ('observed', 'holding')
                or not isinstance(item.get('instrument_id'), str)
                or not _number(item.get('lower')) or not _number(item.get('upper'))
                or item['lower'] > item['upper']):
            continue
        identity = (item.get('instrument_id'), item.get('trade_date'), item.get('rule_key'),
                    item.get('revision'), item.get('type'), item.get('phase'),
                    item.get('episode'), item.get('observed_at_ms'))
        if identity in seen:
            continue
        seen.add(identity)
        valid.append(deepcopy(item))
    valid.sort(key=lambda item: (
        item['instrument_id'], str(item.get('trade_date') or ''), item['type'], item['phase'],
        item['lower'], item['upper'], str(item.get('rule_key') or ''), item.get('observed_at_ms') or 0))
    groups = []
    for item in valid:
        group = next((group for group in groups
                      if (group['instrument_id'], group.get('trade_date'), group['type'], group['phase'])
                      == (item['instrument_id'], item.get('trade_date'), item['type'], item['phase'])
                      and max(group['lower'], item['lower']) <= min(group['upper'], item['upper'])), None)
        if group is None:
            group = {key: deepcopy(item.get(key)) for key in (
                'instrument_id', 'trade_date', 'type', 'phase', 'episode', 'observed_at_ms',
                'source_time_ms', 'price', 'lower', 'upper', 'title')}
            group.update(sources=[], sampling_only=True)
            groups.append(group)
        group['lower'] = max(group['lower'], item['lower'])
        group['upper'] = min(group['upper'], item['upper'])
        group['episode'] = min(str(group.get('episode') or item.get('episode') or ''), str(item.get('episode') or ''))
        group['sources'].append(item)
        times = [source['observed_at_ms'] for source in group['sources'] if _number(source.get('observed_at_ms'))]
        group['first_observed_at_ms'] = min(times) if times else None
        group['last_observed_at_ms'] = max(times) if times else None
    return groups
