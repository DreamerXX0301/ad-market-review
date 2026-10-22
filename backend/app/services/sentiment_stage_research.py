"""Offline-only, unvalidated six-stage candidates and display state machine.

This module has no I/O and is deliberately not called by a product endpoint or
strategy engine. It consumes explicit, versioned observation evidence. Null
bounds mean an unbounded side, not zero. Unknown inputs interrupt confirmation.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import math
from statistics import median

from app.services.sentiment_context import finite, fingerprint, valid_market_calendar

VERSION = 'six-stage-research-draft-v0.1'
PHASES = ('冰点', '退潮', '分歧', '高潮', '发酵', '回暖')
DEFAULTS = {'minimum_cohort': 10, 'minimum_return_coverage_pct': 80,
            'confirmation_sessions': 2, 'exit_sessions': 2,
            'C': [-1, 35, 20], 'W': [.3, 55, 25], 'H': [1, 65, 40], 'N': [-.3, 45, 25],
            'C_continue': [-.5, 45, 25], 'W_continue': [0, 50, 20],
            'H_continue': [.7, 60, 35],
            'improvement_median_pp': .8, 'improvement_promotion_pp': 5,
            'deterioration_median_pp': .8, 'deterioration_promotion_pp': 10,
            'history_sessions': 60, 'minimum_history_values': 40,
            'hot_percentile': 90, 'hot_activity_ratio': 1.2,
            'hot_continue_percentile': 80, 'hot_continue_activity_ratio': 1.05}


def _all(*values):
    return False if False in values else None if None in values else True


def _any(*values):
    return True if True in values else None if None in values else False


def _not(value): return None if value is None else not value


def _bounds(value):
    if not isinstance(value, dict): return None
    low, high = value.get('lower'), value.get('upper')
    if low is not None and finite(low) is None or high is not None and finite(high) is None: return None
    low = -math.inf if low is None else low
    high = math.inf if high is None else high
    return (low, high) if low <= high else None


def _compare(value, operator, threshold):
    bounds = _bounds(value)
    if bounds is None: return None
    low, high = bounds
    if operator == '>=': return True if low >= threshold else False if high < threshold else None
    if operator == '<=': return True if high <= threshold else False if low > threshold else None
    if operator == '>': return True if low > threshold else False if high <= threshold else None
    if operator == '<': return True if high < threshold else False if low >= threshold else None
    raise ValueError('未知区间比较运算符')


def _difference(left, right):
    a, b = _bounds(left), _bounds(right)
    if a is None or b is None: return None
    return {'lower': finite(a[0]-b[1]), 'upper': finite(a[1]-b[0])}


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError): return None


def _eligibility(row, calendar, config, mode, cutoff):
    reasons = []
    if not isinstance(row, dict): return ['缺少严格交易日观察']
    if row.get('date') not in calendar: reasons.append('观察日不在唯一有序市场日历')
    if row.get('group_id') != 'ordinary_10': reasons.append('六阶段草案只研究普通10%已核验组')
    if row.get('cohort_source_complete') is not True: reasons.append('昨日来源全集未证明')
    if row.get('identity_complete') is not True: reasons.append('制度分组完整身份未证明')
    n = row.get('observed_count')
    if isinstance(n, bool) or not isinstance(n, int) or n < config['minimum_cohort']: reasons.append('冻结队列样本不足')
    coverage = finite(row.get('coverage_pct'))
    if coverage is None or not config['minimum_return_coverage_pct'] <= coverage <= 100: reasons.append('可比收益覆盖不足')
    if any(_bounds(row.get(key)) is None for key in ('median_bounds_pct', 'positive_ratio_bounds_pct', 'promotion_bounds_pct')):
        reasons.append('必要指标区间缺失或无效')
    for key in ('positive_ratio_bounds_pct', 'promotion_bounds_pct'):
        value = _bounds(row.get(key))
        if value and not 0 <= value[0] <= value[1] <= 100: reasons.append('比例上下界不在0至100范围')
    proof = row.get('provenance') if isinstance(row.get('provenance'), dict) else {}
    if not proof.get('input_fingerprint') or not proof.get('input_snapshot_ids'): reasons.append('缺少输入版本或指纹')
    if mode == 'as_known':
        known = _timestamp(proof.get('first_known_at'))
        if cutoff is None or known is None or known > cutoff: reasons.append('该截止时点没有完整首次可知证明')
    return reasons


def _feedback(row, thresholds, rising):
    if not row: return None
    direction = '>=' if rising else '<='
    return _all(_compare(row.get('median_bounds_pct'), direction, thresholds[0]),
                _compare(row.get('positive_ratio_bounds_pct'), direction, thresholds[1]),
                _compare(row.get('promotion_bounds_pct'), direction, thresholds[2]))


def observation_from_group(group, day, provenance, *, activity_ratio=None, activity_comparable=False, high_low=None):
    """Adapt a context group without inventing historical roles or activity."""
    keys = ('cohort_source_complete', 'identity_complete', 'observed_count', 'coverage_pct',
            'median_bounds_pct', 'positive_ratio_bounds_pct', 'promotion_bounds_pct')
    return {'date': day, 'group_id': group.get('id'), **{key: deepcopy(group.get(key)) for key in keys},
            'activity_ratio': activity_ratio, 'activity_comparable': activity_comparable,
            'high_low': high_low, 'provenance': {key: provenance.get(key) for key in
            ('input_fingerprint', 'input_snapshot_ids', 'first_known_at', 'computed_at', 'mode')}}


def evaluate_stage_candidate(observation, history, calendar, *, mode='retained_retrospective', cutoff=None, config=None, computed_at=None):
    """Evaluate only information on/before T; no circular/forced stage sequence."""
    if mode not in ('retained_retrospective', 'as_known'): raise ValueError('未知研究回放模式')
    if config is not None and (not isinstance(config, dict) or set(config)-set(DEFAULTS)):
        raise ValueError('研究参数必须为已声明字段')
    options = deepcopy(DEFAULTS); options.update(deepcopy(config or {}))
    for key in ('minimum_cohort', 'confirmation_sessions', 'exit_sessions', 'history_sessions', 'minimum_history_values'):
        if isinstance(options[key], bool) or not isinstance(options[key], int) or options[key] < 1:
            raise ValueError('研究样本与窗口参数必须为正整数')
    if options['confirmation_sessions'] < 2 or options['exit_sessions'] < 2: raise ValueError('确认与退出窗口不可少于两个交易日')
    if options['minimum_history_values'] > options['history_sessions']: raise ValueError('有效历史数不能超过市场日窗口')
    threshold_keys = ('C', 'W', 'H', 'N', 'C_continue', 'W_continue', 'H_continue')
    for key in threshold_keys:
        value = options[key]
        if (not isinstance(value, (list, tuple)) or len(value) != 3 or any(finite(item) is None for item in value)
                or not all(0 <= item <= 100 for item in value[1:])):
            raise ValueError('反馈阈值必须为收益、正反馈比例、晋级比例三项有效值')
    for key in set(options)-set(threshold_keys)-{'minimum_cohort', 'confirmation_sessions', 'exit_sessions', 'history_sessions', 'minimum_history_values'}:
        if finite(options[key]) is None or options[key] < 0: raise ValueError('研究阈值必须为非负有限值')
    clock = computed_at or datetime.now(timezone.utc).isoformat()
    day = observation.get('date') if isinstance(observation, dict) else None
    input_proof = observation.get('provenance') if isinstance(observation, dict) and isinstance(observation.get('provenance'), dict) else {}
    valid_calendar = valid_market_calendar(calendar, day)
    timeline = calendar[:calendar.index(day)+1] if valid_calendar else []
    cutoff_time = _timestamp(cutoff)
    eligible = _eligibility(observation, timeline, options, mode, cutoff_time)
    if not valid_calendar: eligible.append('市场日历无效或缺少T')
    prior_rows = [row for row in history if isinstance(row, dict) and row.get('date') in timeline and row['date'] < day] if valid_calendar else []
    counts = Counter(row['date'] for row in prior_rows)
    indexed = {row['date']: row for row in prior_rows if counts[row['date']] == 1}
    def previous(offset):
        if len(timeline) <= offset: return None
        value = indexed.get(timeline[-offset-1])
        return value if not _eligibility(value, timeline, options, mode, cutoff_time) else None
    current = None if eligible else observation
    past = [previous(offset) for offset in (1, 2, 3)]
    def feedback(row, key): return _feedback(row, options[key], key in ('W', 'H', 'W_continue', 'H_continue'))
    predicates = {key: feedback(current, key) for key in ('C', 'W', 'H', 'N')}
    earlier = [{key: feedback(row, key) for key in ('C', 'W', 'H', 'N')} for row in past]
    current_median = current.get('median_bounds_pct') if current else None
    current_positive = current.get('positive_ratio_bounds_pct') if current else None
    current_q = current.get('promotion_bounds_pct') if current else None
    prior = past[0]
    median_improvement = _difference(current_median, prior.get('median_bounds_pct') if prior else None)
    q_improvement = _difference(current_q, prior.get('promotion_bounds_pct') if prior else None)
    predicates['R'] = _all(_compare(median_improvement, '>=', options['improvement_median_pp']),
                           _compare(q_improvement, '>=', options['improvement_promotion_pp']))
    def prior_median_bound(key):
        bounds = [_bounds(row.get(key)) if row else None for row in past]
        if any(value is None for value in bounds): return None
        return {'lower': finite(median([value[0] for value in bounds])), 'upper': finite(median([value[1] for value in bounds]))}
    predicates['D'] = _all(_compare(_difference(prior_median_bound('median_bounds_pct'), current_median), '>=', options['deterioration_median_pp']),
                           _compare(_difference(prior_median_bound('promotion_bounds_pct'), current_q), '>=', options['deterioration_promotion_pp']))
    # Missing historical dates are not compressed away. They widen percentile
    # bounds, even when the minimum number of valid observations is available.
    history_dates = timeline[max(0, len(timeline)-1-options['history_sessions']):-1]
    historical = []
    for target in history_dates:
        row = indexed.get(target)
        historical.append(_bounds(row.get('median_bounds_pct')) if row and not _eligibility(row, timeline, options, mode, cutoff_time) else None)
    known_history = sum(value is not None for value in historical)
    percentile = None
    own = _bounds(current_median)
    if own and known_history >= options['minimum_history_values'] and historical:
        lower = sum(value is not None and value[1] <= own[0] for value in historical)/len(historical)*100
        upper = sum(value is None or value[0] <= own[1] for value in historical)/len(historical)*100
        percentile = {'lower': lower, 'upper': upper}
    activity = current.get('activity_ratio') if current and current.get('activity_comparable') is True else None
    activity = {'lower': activity, 'upper': activity} if finite(activity) is not None and activity >= 0 else None
    predicates['historical_high_percentile'] = _compare(percentile, '>=', options['hot_percentile'])
    predicates['activity_high'] = _compare(activity, '>=', options['hot_activity_ratio'])
    high_low = current.get('high_low') if current else None
    role_difference = None
    if isinstance(high_low, dict) and prior and high_low.get('role_date') == prior['date'] and high_low.get('frozen_roles') is True:
        high, low = high_low.get('high', {}), high_low.get('low', {})
        if (isinstance(high, dict) and isinstance(low, dict)
                and isinstance(high.get('sample_count'), int) and not isinstance(high.get('sample_count'), bool) and high['sample_count'] >= 5
                and isinstance(low.get('sample_count'), int) and not isinstance(low.get('sample_count'), bool) and low['sample_count'] >= 5):
            high_bounds, low_bounds = high.get('median_bounds_pct'), low.get('median_bounds_pct')
            role_difference = _any(_all(_compare(high_bounds, '>', 0), _compare(low_bounds, '<', 0), _compare(_difference(high_bounds, low_bounds), '>=', 1)),
                                   _all(_compare(low_bounds, '>', 0), _compare(high_bounds, '<', 0), _compare(_difference(low_bounds, high_bounds), '>=', 1)))
    price_promotion_divergence = _all(_compare(current_median, '>=', 0),
                                     _compare(_difference(prior.get('positive_ratio_bounds_pct') if prior else None, current_positive), '>=', 10),
                                     _compare(_difference(prior.get('promotion_bounds_pct') if prior else None, current_q), '>=', 10))
    divergence = _all(_any(*[value[key] for value in earlier[:2] for key in ('W', 'H')]),
                      _not(predicates['N']), _not(predicates['C']), _any(role_difference, price_promotion_divergence))
    predicates.update(high_low_divergence=role_difference, price_promotion_divergence=price_promotion_divergence)
    candidates = {
        '冰点': predicates['C'],
        '回暖': _all(predicates['W'], predicates['R'], _any(*[value[key] for value in earlier for key in ('C', 'N')])),
        '发酵': _all(predicates['W'], earlier[0]['W'], _compare(q_improvement, '>=', -5)),
        '高潮': _all(predicates['H'], predicates['historical_high_percentile'], predicates['activity_high']),
        '分歧': divergence,
        '退潮': _all(predicates['N'], predicates['D'], _not(predicates['C'])),
    }
    if eligible: candidates = {key: None for key in PHASES}
    matched = [key for key in PHASES if candidates[key] is True]
    primary = matched[0] if matched else None
    continuation = {'冰点': feedback(current, 'C_continue'), '回暖': feedback(current, 'W_continue'),
                    '发酵': feedback(current, 'W_continue'), '退潮': predicates['N'], '分歧': divergence,
                    '高潮': _all(feedback(current, 'H_continue'), _compare(activity, '>=', options['hot_continue_activity_ratio']),
                                _compare(percentile, '>=', options['hot_continue_percentile']))}
    if eligible: continuation = {key: None for key in PHASES}
    return {'usage': 'offline_research_only', 'validated': False, 'rule_version': VERSION,
            'config_fingerprint': fingerprint(options), 'config': options,
            'date': day, 'previous_date': timeline[-2] if len(timeline) >= 2 else None,
            'mode': mode, 'computed_at': clock,
            'input_fingerprint': input_proof.get('input_fingerprint'),
            'eligibility_reasons': eligible, 'eligible': not eligible,
            'matched_candidates': matched, 'primary_candidate': primary, 'candidate_states': candidates,
            'unknown_candidates': [key for key in PHASES if candidates[key] is None],
            'status': 'candidate' if primary else 'unknown' if eligible or any(value is None for value in candidates.values()) else 'mixed',
            'predicate_evidence': predicates, 'continuation_states': continuation,
            'evidence_bounds': deepcopy({'median_pct': current_median, 'positive_ratio_pct': current_positive,
                                'promotion_pct': current_q, 'median_improvement_pp': median_improvement,
                                'promotion_improvement_pp': q_improvement, 'activity_ratio': activity}),
            'prior_dates': [row.get('date') if row else None for row in past],
            'percentile_bounds': percentile, 'historical_window_count': len(history_dates), 'historical_valid_count': known_history,
            'immediate_negative_feedback': _any(predicates['C'], predicates['N']),
            'notice': '未验证的六阶段研究草案；不进入正式界面阶段、策略筛选或仓位建议。当前留存回看不等于历史当时已知。'}


def advance_stage_state(previous, evaluation, calendar):
    """A new result never rewrites a prior candidate/confirmation record."""
    previous = previous or {}; day = evaluation.get('date')
    valid = valid_market_calendar(calendar, day)
    expected_previous = calendar[calendar.index(day)-1] if valid and calendar.index(day) else None
    adjacent = expected_previous is not None and previous.get('date') == expected_previous
    same_rule = (previous.get('rule_version') == evaluation.get('rule_version')
                 and previous.get('config_fingerprint') == evaluation.get('config_fingerprint'))
    own = evaluation.get('primary_candidate'); clock = evaluation.get('computed_at')
    saved = previous.get('confirmed_phase') or previous.get('last_confirmed_phase')
    saved_date = previous.get('confirmed_trade_date') or previous.get('last_confirmed_trade_date')
    saved_time = previous.get('confirmed_at') or previous.get('last_confirmed_at')
    result = {'usage': 'offline_research_only', 'rule_version': evaluation.get('rule_version'),
              'config_fingerprint': evaluation.get('config_fingerprint'), 'date': day,
              'raw_candidate': own, 'candidate_count': 0, 'candidate_first_trade_date': None,
              'candidate_first_seen_at': None, 'exit_count': 0, 'confirmed_phase': None,
              'confirmed_trade_date': None, 'confirmed_at': None,
              'last_confirmed_phase': saved, 'last_confirmed_trade_date': saved_date, 'last_confirmed_at': saved_time,
              'status': 'unknown' if not evaluation.get('eligible') or evaluation.get('status') == 'unknown' else 'mixed',
              'immediate_negative_feedback': evaluation.get('immediate_negative_feedback')}
    if not valid or not evaluation.get('eligible'): return result
    if own:
        continuation = adjacent and same_rule and previous.get('raw_candidate') == own
        result.update(candidate_count=previous.get('candidate_count', 0)+1 if continuation else 1,
                      candidate_first_trade_date=previous.get('candidate_first_trade_date') if continuation else day,
                      candidate_first_seen_at=previous.get('candidate_first_seen_at') if continuation else clock,
                      status='candidate')
    required = evaluation.get('config', {}).get('confirmation_sessions', 2)
    if own and result['candidate_count'] >= required:
        already = adjacent and same_rule and previous.get('confirmed_phase') == own
        result.update(confirmed_phase=own, confirmed_trade_date=previous.get('confirmed_trade_date') if already else day,
                      confirmed_at=previous.get('confirmed_at') if already else clock, status='confirmed')
        return result
    confirmed = previous.get('confirmed_phase') if adjacent and same_rule else None
    if confirmed:
        keep = evaluation.get('continuation_states', {}).get(confirmed)
        if keep is True:
            result.update(confirmed_phase=confirmed, confirmed_trade_date=previous.get('confirmed_trade_date'),
                          confirmed_at=previous.get('confirmed_at'), status='confirmed')
        elif keep is False:
            exits = previous.get('exit_count', 0)+1
            result['exit_count'] = exits
            if exits < evaluation.get('config', {}).get('exit_sessions', 2):
                result.update(confirmed_phase=confirmed, confirmed_trade_date=previous.get('confirmed_trade_date'),
                              confirmed_at=previous.get('confirmed_at'), status='pending_review')
        else: result['status'] = 'unknown'
    return result


def replay_stage_candidates(observations, calendar, *, mode='retained_retrospective', cutoffs=None, config=None, computed_at=None):
    """Pure chronological replay. As-known mode requires each day's own cutoff."""
    if not valid_market_calendar(calendar):
        raise ValueError('回放要求唯一有序市场交易日历')
    counts = Counter(row.get('date') for row in observations if isinstance(row, dict))
    indexed = {row['date']: row for row in observations if isinstance(row, dict) and counts[row.get('date')] == 1}
    results = []; state = None; past = []
    retained = sorted(value for value in counts if value in calendar)
    requested = [value for value in calendar if retained and retained[0] <= value <= retained[-1]]
    for day in requested:
        row = indexed.get(day, {'date': day})
        evaluation = evaluate_stage_candidate(row, past, calendar, mode=mode,
                     cutoff=(cutoffs or {}).get(day), config=config, computed_at=computed_at)
        state = advance_stage_state(state, evaluation, calendar)
        results.append({'evaluation': evaluation, 'state': state})
        past.append(row)
    return {'usage': 'offline_research_only', 'rule_version': VERSION, 'mode': mode, 'items': results,
            'requested_count': len(requested), 'missing_observation_count': sum(value not in counts for value in requested),
            'unknown_count': sum(value['evaluation']['status'] == 'unknown' for value in results)}
