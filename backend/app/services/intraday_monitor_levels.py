"""Daily frozen, raw-price references for the independent intraday sampler."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import math
from app.analytics.chart_price_volume import _active, _level, _segment
from app.analytics.chart_trendlines import VERSION as ZONE_ALGORITHM, _observation_zones
from app.analytics.support_resistance import _day, confirmed_pivots
from app.core import digest
from app.services.automatic_price_alerts import _quote, advance_short_baseline, build_baseline
from app.services.line_alerts import _affine, _events

DEFAULT_RULE_ID = 'intraday-default-short-week-v1'


def annotate_action_coverage(histories, actions):
    """Match MarketData.bars' action flags on the bounded batch-read path.

    ``actions_for_instruments`` returns None when the action source is absent,
    and an explicit list (possibly empty) per requested symbol when it exists.
    Raw Parquet batch rows do not already carry these reader-derived flags.
    """
    result = {}
    for symbol, rows in histories.items():
        events = actions.get(symbol) if isinstance(actions, dict) else None
        known = isinstance(events, list) and all(isinstance(event, dict) and _day(event.get('date')) for event in events)
        days = {event['date'] for event in events} if known else set()
        result[symbol] = [{**row, 'corporate_action_coverage_known': known,
                           'corporate_action': row.get('date') in days} for row in rows]
    return result

def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _configuration(rule):
    if rule is None:
        return {'rule_id': DEFAULT_RULE_ID, 'revision': 1, 'profile': 'short_week_v1', 'max_risk_pct': 5.0}
    if not isinstance(rule, dict):
        raise ValueError('逐股价位配置格式未能核验')
    profile, risk = rule.get('profile', 'structure'), rule.get('max_risk_pct', 5.0)
    if profile not in ('structure', 'short_week_v1') or not positive(risk) or not 0.5 <= risk <= 20:
        raise ValueError('逐股价位模式或风险距离配置未能核验')
    revision = rule.get('revision', 1)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError('逐股价位配置版本未能核验')
    return {'rule_id': rule.get('rule_id') or 'intraday-unbound-settings',
            'revision': revision, 'profile': profile, 'max_risk_pct': risk}


def _strict_segment(selected, calendar):
    # Chart zone helpers do not themselves reject provisional daily candles or
    # zero-volume stock rows. Make those real continuity breaks before reuse.
    bars = [row if positive(row.get('volume')) else {**row, 'close': None} for row in selected[-128:]]
    sessions = {day: index for index, day in enumerate(calendar)}
    segment, _ = _segment(bars, 'D', sessions, stock=True, basis='none', strict_daily_complete=True)
    return segment, sessions


def _previous_monitor(saved_baseline, configuration, symbol):
    if saved_baseline is None:
        return None
    if not isinstance(saved_baseline, dict):
        raise ValueError('已保存监控基准格式未能核验')
    saved = saved_baseline.get('monitoring_baseline')
    # The entire intraday baseline result can be passed back directly. The
    # embedded monitor is the same bounded shape as the closing-rule baseline.
    if isinstance(saved, dict):
        saved = saved
    else:
        saved = saved_baseline
    if (saved.get('rule_id'), saved.get('rule_revision')) != (configuration['rule_id'], configuration['revision']):
        # A verified new configuration is an explicit new monitoring epoch.
        if saved.get('rule_id') is not None and saved.get('rule_revision') is not None:
            return None
        raise ValueError('已保存监控基准缺少当前规则标识或版本，不猜测风险锚归属')
    if (saved.get('instrument_id') not in (None, symbol)
            or configuration['rule_id'] == DEFAULT_RULE_ID and saved.get('instrument_id') != symbol):
        raise ValueError('已保存风险锚的股票标识未能核验')
    if (saved.get('profile'), saved.get('max_risk_pct')) != (configuration['profile'], configuration['max_risk_pct']):
        raise ValueError('已保存监控基准与当前配置不一致')
    if saved.get('monitoring_baseline') is not True or not _day(saved.get('as_of')):
        raise ValueError('已保存监控基准的日期或准备状态未能核验')
    return deepcopy(saved)


def automatic_baseline(symbol, rows, *, previous_day, calendar, snapshot,
                       rule=None, saved_baseline=None, primary_snapshot=None):
    """Freeze only prior-session data, preserving a matching short risk anchor.

    ``rule`` is the existing per-stock configuration, if present. A supplied
    saved monitor must include rule_id/rule_revision from its SQLite metadata.
    Persist this result's ``monitoring_baseline`` for default intraday stocks as
    well, and pass it back next session to keep their risk line raise-only.
    """
    result = {'instrument_id': symbol, 'baseline_date': previous_day, 'snapshot_id': snapshot,
              'previous_close': None, 'levels': [], 'reason': None, 'risk_reason': None,
              'monitoring_baseline': None}
    if (not _day(previous_day) or not snapshot or not isinstance(calendar, (list, tuple))
            or not calendar or any(not _day(day) for day in calendar)
            or list(calendar) != sorted(set(calendar)) or previous_day not in calendar):
        return {**result, 'reason': '前一市场交易日、已发布行情代际或真实交易日历未能核验'}
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not _day(row.get('date')) for row in rows):
        return {**result, 'reason': '历史报价存在无效日期或记录'}
    selected = [deepcopy(row) for row in rows if row['date'] <= previous_day]
    dates = [row['date'] for row in selected]
    if dates != sorted(set(dates)):
        return {**result, 'reason': '前日历史报价存在重复或乱序，不能任取一条冻结'}
    if (not selected or selected[-1]['date'] != previous_day or not _quote(selected[-1])
            or selected[-1].get('noon_provisional') is True or not positive(selected[-1].get('volume'))
            or selected[-1].get('corporate_action_coverage_known') is not True
            or selected[-1].get('corporate_action')):
        return {**result, 'reason': '前一市场交易日缺少有效、公司行动覆盖已核验的原价报价'}
    result['previous_close'] = selected[-1]['close']
    try:
        configuration = _configuration(rule)
    except ValueError as error:
        return {**result, 'reason': str(error)}
    result['configuration'] = configuration
    segment, sessions = _strict_segment(selected, calendar)
    if not segment or segment[-1]['date'] != previous_day:
        return {**result, 'reason': '前一完整交易日没有连续可比的真实报价'}
    pivots = list(confirmed_pivots(segment, 'D', sessions))
    positions = {bar['date']: index for index, bar in enumerate(segment)}
    for zone in _observation_zones(segment, pivots, positions):
        kind = zone['line_type']
        inside = zone['lower_price'] <= result['previous_close'] <= zone['upper_price']
        proof = {'algorithm': ZONE_ALGORITHM, 'snapshot_id': snapshot, 'zone': zone,
                 'reference_inside': inside, 'window_start': segment[0]['date'],
                 'window_end': previous_day, 'pivot_confirmations': [deepcopy(pivot) for pivot in pivots
                     if positions[pivot['date']] >= len(segment) - 60
                     and zone['lower_price'] <= pivot['price'] <= zone['upper_price']]}
        result['levels'].append({
            'key': 'zone:' + digest([symbol, previous_day, proof]), 'kind': kind,
            'source_kind': 'automatic_zone', 'source_id': f'zone:{kind}', 'revision': 1,
            'label': '自动水平观察区' if inside else '自动水平支撑区' if kind == 'support' else '自动水平压力区',
            'lower': zone['lower_price'], 'upper': zone['upper_price'],
            'baseline_date': previous_day, 'price_basis': 'none', 'evidence': proof,
        })
    monitor = build_baseline(symbol, segment[-120:], previous_day, calendar, snapshot, primary_snapshot,
                             profile=configuration['profile'], max_risk_pct=configuration['max_risk_pct'])
    if configuration['profile'] == 'short_week_v1':
        try:
            previous = _previous_monitor(saved_baseline, configuration, symbol)
            if previous and previous['as_of'] > previous_day:
                raise ValueError('已保存监控基准晚于前一完整交易日，不能使用未来风险锚')
            monitor = advance_short_baseline(previous, monitor, selected, calendar)
        except ValueError as error:
            monitor['levels']['stop'] = None
            monitor['levels']['state'] = 'unknown'
            monitor['levels']['reason'] = result['risk_reason'] = str(error)
            monitor['risk_anchor'] = {'suspended': True, 'reason': str(error)}
    monitor.update(rule_id=configuration['rule_id'], rule_revision=configuration['revision'], instrument_id=symbol)
    result['monitoring_baseline'] = monitor
    levels = monitor['levels']
    short = configuration['profile'] == 'short_week_v1'
    candidates = [('support', 'support', '自动近端支撑' if short else '自动结构支撑'),
                  ('breakout', 'resistance', '自动突破参考'),
                  ('stop', 'risk', '短线风险线' if short else '结构失效参考')]
    # The monitoring builder intentionally omits graph-only display_support.
    # Recover the same genuine structural pivot for this independent intraday
    # scope; the 5% risk-distance filter must never turn a risk line into support.
    display_segment = segment[-120:]
    display_pivots = list(confirmed_pivots(display_segment, 'D', sessions))
    display = _active(display_pivots, display_segment, 'support')
    if display and (not isinstance(levels.get('support'), dict)
                    or display['price'] != levels['support'].get('price')):
        levels = {**levels, 'display_support': _level(display, result['previous_close'])}
        cutoff = display_segment[max(0, len(display_segment) - 20)]['date']
        label = '自动结构支撑（近20日）' if display['date'] >= cutoff else '自动结构支撑（历史120日）'
        candidates.append(('display_support', 'support', label))
    for key, kind, label in candidates:
        candidate = levels.get(key)
        if not isinstance(candidate, dict) or not positive(candidate.get('price')):
            continue
        if key == 'stop' and short and candidate.get('state') == 'breached':
            result['risk_reason'] = '已保存短线风险线已失守，保留原风险锚；明确重设配置后才开始新一轮'
            continue
        proof = {'algorithm': monitor['algorithm'], 'profile': configuration['profile'],
                 'max_risk_pct': configuration['max_risk_pct'], 'snapshot_id': snapshot,
                 'rule_id': configuration['rule_id'], 'rule_revision': configuration['revision'],
                 'candidate': deepcopy(candidate), 'risk_anchor': deepcopy(monitor.get('risk_anchor')) if key == 'stop' else None,
                 'scope': 'independent_intraday_reference', 'changes_existing_close_rules': False}
        result['levels'].append({
            'key': 'auto:' + digest([symbol, previous_day, key, proof]), 'kind': kind,
            'source_kind': 'automatic_level', 'source_id': f'level:{key}', 'revision': configuration['revision'],
            'label': label, 'lower': candidate['price'], 'upper': candidate['price'],
            'baseline_date': previous_day, 'price_basis': 'none', 'evidence': proof,
        })
    if short and not positive((monitor['levels'].get('stop') or {}).get('price')):
        result['risk_reason'] = result['risk_reason'] or monitor['levels'].get('reason') or '短线风险基准不足，未生成风险提醒线'
    if not result['levels']:
        result['reason'] = '前日完整可比样本内暂无已确认的自动支撑、压力或风险参考'
    return result


def manual_levels(lines, *, previous_day, actions):
    """Convert eligible saved lines without changing their closing-rule modes.

    The caller supplies explicitly authorized active rules; price preparation
    neither enables alert_modes nor changes any saved annotation.
    """
    levels, unknowns = [], []
    for line in lines:
        try:
            if not isinstance(line, dict) or not _day(previous_day):
                raise ValueError('手动线或前一完整交易日未能核验')
            if line.get('line_type') not in ('support', 'resistance') or not positive(line.get('price')):
                continue
            price = line['price']
            proof = {'annotation_id': line['annotation_id'], 'revision': line['revision'],
                     'saved_price': price, 'saved_price_basis': line['price_basis'],
                     'saved_anchor_date': line.get('anchor_date')}
            if line['price_basis'] == 'forward_event_affine':
                anchor = line.get('anchor_date')
                if not _day(anchor) or anchor > previous_day:
                    raise ValueError('手动线的固定复权锚晚于前一完整交易日')
                events = _events(actions, anchor, previous_day)
                price = _affine(price, anchor, previous_day, events)
                proof['action_events'] = events
            elif line['price_basis'] != 'none':
                raise ValueError('手动线价格口径不可用')
            proof.update(comparison_price=price, comparison_date=previous_day)
            levels.append({
                'key': f'manual:{line["annotation_id"]}:{line["revision"]}',
                'kind': line['line_type'], 'source_kind': 'manual_line',
                'source_id': line['annotation_id'], 'revision': line['revision'],
                'label': line.get('label') or ('手动支撑线' if line['line_type'] == 'support' else '手动压力线'),
                'lower': price, 'upper': price, 'baseline_date': previous_day,
                'price_basis': 'none', 'evidence': proof,
            })
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            unknowns.append({'source_id': line.get('annotation_id') if isinstance(line, dict) else None, 'reason': str(error)})
    return levels, unknowns


def comparable_quote(quote, baseline):
    if not quote.get('valid'):
        return quote
    previous = baseline.get('previous_close')
    if not positive(previous) or not positive(quote.get('previous_close')):
        return {**quote, 'valid': False, 'reason': '缺少前日已发布原价，不能核验盘中价格可比性'}
    difference = abs(Decimal(str(previous)) - Decimal(str(quote['previous_close'])))
    comparison = {'model': 'published-previous-close-agreement-v1',
                  'baseline_date': baseline.get('baseline_date'), 'snapshot_id': baseline.get('snapshot_id'),
                  'published_previous_close': previous, 'source_previous_close': quote['previous_close'],
                  'absolute_difference': str(difference), 'tolerance': '0.005',
                  'current_session_corporate_action_coverage_proven': False}
    if difference > Decimal('0.005'):
        return {**quote, 'valid': False, 'reason': '盘中前收与已发布原价不一致，可能涉及除权或修订，本轮暂停比较',
                'previous_close_comparison': comparison}
    return {**quote, 'previous_close_comparison': comparison}
