"""Bounded, read-only explanations; observations never execute a trade.

All replay inputs are already-read immutable series. No market object, provider,
database, per-day panel or sentiment recomputation is used here.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from app.services.sentiment_context import INDEX_NAMES, valid_market_calendar

VERSION = 'market-opportunity-insights-v1'
LOOKBACK_SESSIONS = 20
NOTICE = '当前留存材料的有界回放，不代表历史当时已发布；仅观察修复，不确认底部或完成交易。'
TRACK_LABELS = {'candidate': '短底试探候选已形成', 'continuing': '修复得到延续',
                'diverging': '修复出现分歧', 'failed': '修复候选已失效',
                'unknown': '本日证据待核验', 'none': '窗口内暂无量价修复候选'}
TRACK_CONDITIONS = ['候选形成沿用前日冰点、广度回升至少15个百分点且越过35、成交额增加至少20%及四指数真实风险参照。',
                    '后续任一指数收盘低于该候选日低点，记本候选失效；恢复上涨不复活旧候选。',
                    '四指数可比收盘均未破候选低点且广度温度≥60，记修复延续；其余完整观察记分歧。',
                    '缺失、重复、来源冲突或覆盖不足时只记待核验；候选低点不替代个股原定失效条件。']


def _dated_rows(rows, wanted):
    result = defaultdict(list)
    for row in rows:
        if isinstance(row, dict) and row.get('date') in wanted:
            result[row['date']].append(row)
    return result


def candidate_tracking(day, calendar, history, turnover_history, reference, universe_upper_bound=None):
    """Replay at most 20 sessions, plus one predecessor solely for comparison."""
    from app.services.market_outlook import _bounded_reading, _breadth_scope, _index_references, _number

    valid = valid_market_calendar(calendar, day)
    position = calendar.index(day) if valid else -1
    sessions = calendar[max(0, position - LOOKBACK_SESSIONS + 1):position + 1] if valid else []
    inputs = calendar[max(0, position - LOOKBACK_SESSIONS):position + 1] if valid else []
    wanted = set(inputs)
    breadth = _dated_rows(history, wanted)
    amounts = _dated_rows(turnover_history, wanted)
    # Filter each already-read index series once; never scan it for each day.
    bounded_reference = {'indices': {symbol: [row for row in reference.get('indices', {}).get(symbol, [])
                                              if isinstance(row, dict) and row.get('date') in wanted]
                                     for symbol in INDEX_NAMES}}
    readings, refs = {}, {}
    for target in inputs:
        rows = breadth.get(target, [])
        row = rows[0] if len(rows) == 1 else {}
        bound = _bounded_reading(row, universe_upper_bound)
        known = (row.get('scope') is not None and len(rows) == 1
                 and bound['coverage_pct'] is not None and bound['coverage_pct'] >= 80)
        readings[target] = {**bound, 'known': known, 'up': row.get('up'),
                            'scope': _breadth_scope(row.get('scope')), 'source': row.get('source')}
        refs[target] = _index_references(bounded_reference, target)

    def expanded(target, previous):
        left, right = amounts.get(target, []), amounts.get(previous, [])
        if len(left) != 1 or len(right) != 1:
            return None
        a, b = left[0], right[0]
        amount, prior = _number(a.get('amount')), _number(b.get('amount'))
        if (amount is None or amount < 0 or prior is None or prior <= 0
                or not a.get('scope') or a.get('scope') != b.get('scope')
                or a.get('source') != b.get('source')):
            return None
        return Decimal(str(amount)) >= Decimal(str(prior)) * Decimal('1.20')

    candidate, base, observations, failed, chain_gap = None, [], [], False, False
    last_observed = None
    for target in sessions:
        offset = inputs.index(target)
        previous = inputs[offset - 1] if offset else None
        current, prior = readings[target], readings.get(previous, {})
        qualifies = (current['known'] and prior.get('known')
                     and current['scope'] == prior['scope'] and current['source'] == prior['source']
                     and prior['value'] <= 15 and prior['up'] < 500
                     and current['value'] > 35 and current['value'] - prior['value'] >= 15
                     and expanded(target, previous) is True and len(refs[target]) == len(INDEX_NAMES))
        if qualifies:
            candidate, base, observations, failed, chain_gap = target, refs[target], [], False, False
            last_observed = target
        if candidate is None:
            continue
        broken = []
        if target == candidate:
            state, reason = 'candidate', '前日冰点后广度与成交额共同修复，候选日四指数低点参照齐备。'
        elif failed:
            state, reason = 'failed', '此前收盘已经破坏本候选低点，后续上涨不改写原失效记录。'
        else:
            by_id = {row['instrument_id']: row for row in refs[target]}
            prices_known = len(by_id) == len(INDEX_NAMES) and all(
                symbol in by_id and (not row.get('source') or by_id[symbol].get('source') == row.get('source'))
                for row in base for symbol in [row['instrument_id']])
            broken = [row['instrument_id'] for row in base
                      if row['instrument_id'] in by_id
                      and (not row.get('source') or by_id[row['instrument_id']].get('source') == row.get('source'))
                      and by_id[row['instrument_id']]['close'] < row['low']]
            if broken:
                state, reason = 'failed', '已有指数收盘跌破候选日实际低点，本候选失效。'
                failed, last_observed = True, target
            elif (not current['known'] or not prices_known or chain_gap
                  or current['scope'] != readings[candidate]['scope']
                  or current['source'] != readings[candidate]['source']):
                state, reason = 'unknown', '本日或候选之后的严格交易日证据有缺口，不能沿用旧状态判断修复仍有效。'
                chain_gap = True
            else:
                state = 'continuing' if current['value'] >= 60 else 'diverging'
                reason = ('四指数收盘均未破候选日低点，且广度温度达到60；仅说明修复延续。' if state == 'continuing' else
                          '四指数收盘未破候选日低点，但广度未达60，修复仍有分歧。')
                last_observed = target
        observations.append({'date': target, 'state': state, 'label': TRACK_LABELS[state],
                             'temperature': current['value'] if current['known'] else None,
                             'broken_indices': broken, 'reason': reason})
    detection_complete = all(readings[target]['known'] and len(amounts.get(target, [])) == 1
                             and _number(amounts[target][0].get('amount')) is not None
                             and amounts[target][0]['amount'] >= 0 for target in inputs)
    detection_complete = detection_complete and all(len(refs[target]) == len(INDEX_NAMES) for target in sessions)
    for target in sessions:
        offset = inputs.index(target)
        if not offset:
            detection_complete = False
        else:
            prior = inputs[offset - 1]
            detection_complete = (detection_complete and expanded(target, prior) is not None
                                  and readings[target]['scope'] == readings[prior]['scope']
                                  and readings[target]['source'] == readings[prior]['source'])
    history_complete = len(sessions) == LOOKBACK_SESSIONS and detection_complete
    state = observations[-1]['state'] if observations else 'none' if valid else 'unknown'
    if not observations and not detection_complete:
        state = 'unknown'
    return {'status': state, 'label': TRACK_LABELS[state], 'candidate_date': candidate,
            'last_observed_date': last_observed, 'as_of': day, 'lookback_sessions': LOOKBACK_SESSIONS,
            'window_begin': sessions[0] if sessions else None, 'window_end': sessions[-1] if sessions else None,
            'history_complete': history_complete,
            'confirmed': False, 'reason': observations[-1]['reason'] if observations else
            '窗口或首日前置日的必要证据未齐，不能证明窗口内没有候选。' if state == 'unknown' else
            '仅查最近20个严格市场交易日；窗口外候选不自动延续，缺档不拼接。',
            'conditions': list(TRACK_CONDITIONS), 'index_references': base,
            'observations': observations, 'notice': NOTICE}


def entry_routes(stage, axes, turnover, bottom):
    """Separate trend eligibility from the extra evidence needed for relay."""
    by_id = {row['id']: row for row in axes}
    hard = turnover['state'] == 'unknown' or stage['code'] in ('unknown', 'ice', 'ebb')
    trend_missing = []
    if by_id['index']['state'] != 'strong':
        trend_missing.append('四指数严格窗口尚未同时偏强。')
    if by_id['breadth']['state'] != 'strong':
        trend_missing.append('同口径广度尚未达到多数上涨。')
    if turnover['state'] == 'unknown':
        trend_missing.append('相邻市场交易日成交额比较未核验。')
    relay_missing = list(trend_missing)
    if by_id['short_term']['state'] != 'strong':
        relay_missing.append('昨日涨停全队列价格反馈尚未确认偏强。')
    if not by_id['short_term']['ordinary_group_ready']:
        relay_missing.append('普通10%制度组身份、来源或可比样本不足。')
    repair_missing = [] if bottom['status'] == 'candidate' else ['前日冰点、当日修复、至少20%放量及四指数风险参照尚未同时具备。']
    hard_reason = ('相邻成交额等必要证据未齐。' if turnover['state'] == 'unknown' or stage['code'] == 'unknown' else
                   '当前仍为冰点或退潮，先核对风险条件。')
    rows = []
    for key, label, missing, observation in (
        ('trend', '趋势开仓 / 加仓复核', trend_missing, '指数中期与当日广度同向' if not trend_missing else '趋势复核条件尚未齐备'),
        ('relay', '超短接力复核', relay_missing, '价格反馈与普通10%证据齐备' if not relay_missing else '接力价格或制度组证据待核验'),
        ('repair', '短底 / 反弹试探', repair_missing, '冰点后放量修复候选已出现' if not repair_missing else '尚未形成合格量价修复候选')):
        status = 'blocked' if hard else 'wait' if missing else 'conditional'
        rows.append({'id': key, 'label': label, 'status': status,
                     'status_label': {'blocked': '禁止开仓', 'wait': '等待对应条件', 'conditional': '满足条件后复核'}[status],
                     'observation': observation, 'reason': hard_reason if hard else '；'.join(missing) if missing else
                     '已出现对应观察条件；下一交易日仍须人工核验方向、原定失效条件与真实可成交。',
                     'missing_conditions': missing + ([hard_reason] if hard else []),
                     'triggers': ['下一交易日核对所属方向与真实可成交。', '已有持仓另核对原定失效条件和承受能力。'] if key == 'trend' else
                                 ['独立核对普通10%制度组晋级与高位负反馈，不用全队列均值替代。'] if key == 'relay' else
                                 ['检查修复延续、回踩与候选日低点风险参照。'],
                     'invalidations': ['所属方向或原有趋势结构走弱，或原定失效条件触发。'] if key != 'repair' else
                                      ['后续指数收盘破坏候选低点，或广度修复失败。'],
                     'trigger_state': 'unobserved'})
    return rows


def build_insights(*, day, snapshot_id, calendar, breadth, previous, history, temperature,
                   stage, axes, turnover, turnover_history, reference, bottom, routes,
                   universe_upper_bound=None):
    from app.services.market_outlook import _number

    by_id = {row['id']: row for row in axes}
    adjacent = temperature['previous_date'] is not None
    delta = temperature['delta']
    up, prior_up = _number(breadth.get('up')), _number((previous or {}).get('up')) if adjacent else None
    changes = [
        {'id': 'breadth', 'label': '广度温度变化', 'state': 'unknown' if delta is None else 'improving' if delta > 0 else 'weakening' if delta < 0 else 'steady',
         'date': day, 'previous_date': temperature['previous_date'], 'value': temperature['value'],
         'previous_value': temperature['previous_value'], 'delta': delta, 'unit': '/100', 'delta_unit': '个百分点',
         'evidence': f"温度 {temperature['value']:.2f}，较前日{delta:+.2f}个百分点。" if delta is not None else '严格相邻同口径广度变化待核验。',
         'interpretation': '上涨参与范围扩大。' if delta is not None and delta > 0 else '上涨参与范围收缩。' if delta is not None and delta < 0 else '观察相邻同口径变化，不跨缺档比较。',
         'scope': temperature['scope'], 'reason': None if adjacent else '相邻同口径读数未齐。'},
        {'id': 'up_count', 'label': '上涨家数变化', 'state': 'unknown' if up is None or prior_up is None else 'improving' if up > prior_up else 'weakening' if up < prior_up else 'steady',
         'date': day, 'previous_date': temperature['previous_date'], 'value': up, 'previous_value': prior_up,
         'delta': up - prior_up if up is not None and prior_up is not None else None, 'unit': '家', 'delta_unit': '家',
         'evidence': f'上涨{int(up)}家，前日{int(prior_up)}家。' if up is not None and prior_up is not None else '相邻上涨家数比较待核验。',
         'interpretation': '家数与上涨占比同时观察，不能代表账户盈亏。', 'scope': temperature['scope'], 'reason': None if adjacent else '前日不可比。'},
        {'id': 'turnover', 'label': '成交活跃度变化', 'state': 'unknown' if turnover['change_pct'] is None else 'improving' if turnover['change_pct'] > 0 else 'weakening' if turnover['change_pct'] < 0 else 'steady',
         'date': day, 'previous_date': turnover['previous_date'], 'value': turnover['amount'],
         'previous_value': turnover['previous_amount'], 'delta': turnover['change_pct'], 'unit': 'CNY', 'delta_unit': '%',
         'evidence': f"沪深京成交额较前日{turnover['change_pct']:+.2f}%。" if turnover['change_pct'] is not None else turnover['reason'] or '相邻同口径成交额待核验。',
         'interpretation': '放量仅描述成交活跃，须结合价格方向；不当作净流入。', 'scope': turnover['scope'], 'reason': turnover['reason']},
        {'id': 'short_term', 'label': '昨板今日价格反馈', 'state': by_id['short_term']['state'],
         'date': day, 'previous_date': None, 'value': next((row.get('value') for row in by_id['short_term']['evidence'] if '中位' in row.get('label', '')), None),
         'previous_value': None, 'delta': None, 'unit': '%', 'delta_unit': '%', 'evidence': by_id['short_term']['state_label'],
         'interpretation': '昨日冻结队列的今日表现；未逐日重算历史，不声称较前日改善。',
         'scope': by_id['short_term']['scope'], 'reason': '队列来源或可比样本不足。' if by_id['short_term']['state'] == 'unknown' else None},
    ]
    opportunities, risks, gaps = [], [], []
    if delta is not None and delta > 0:
        opportunities.append(f'广度温度较前日回升{delta:.2f}个百分点，更多样本参与上涨。')
    if bottom['status'] == 'candidate':
        opportunities.append('冰点后放量修复，短底试探候选已形成；后续承接待观察。')
    if by_id['index']['state'] == 'strong':
        opportunities.append('四指数中期环境偏强，趋势持仓可进入独立条件复核。')
    elif by_id['index']['state'] == 'weak':
        risks.append('四指数中期仍弱，单日修复尚未改变原有中期环境。')
    elif by_id['index']['state'] == 'unknown':
        gaps.append('四指数严格窗口未齐，指数趋势判断待核验。')
    if by_id['short_term']['state'] == 'strong':
        opportunities.append('昨日涨停全队列今日价格反馈偏强；制度组晋级另行核验。')
    elif by_id['short_term']['state'] == 'weak':
        risks.append('昨日涨停队列价格反馈偏弱，关注强势股负反馈。')
    elif by_id['short_term']['state'] == 'unknown':
        gaps.append('昨日涨停队列来源或价格覆盖不足，价格反馈待核验。')
    if not by_id['short_term']['ordinary_group_ready']:
        gaps.append('普通10%制度组证据不足，仅影响对应接力复核，不据此判断趋势偏弱。')
    if turnover['state'] == 'unknown':
        gaps.append(turnover['reason'] or '相邻同口径成交额比较未核验。')
    if stage['code'] == 'unknown':
        gaps.append('广度范围、可比率、日历或跨输入一致性不足，阶段及执行准备待核验。')
    if stage['code'] in ('ice', 'ebb'):
        risks.append('当前仍处冰点或退潮，风险条件优先。')
    if stage['code'] == 'climax':
        risks.append('高热广度与事件同时出现，追高及后续分歧风险上升，趋势条件仍单独核对。')
    if bottom['status'] == 'candidate':
        risks.append('候选日低点仅是市场风险参照，单日反弹可能失败，不替代持仓原定失效条件。')
    # T amount inconsistencies found against the panel also invalidate replay T.
    position = calendar.index(day) if temperature['calendar_valid'] else -1
    begin = calendar[max(0, position - LOOKBACK_SESSIONS)] if position >= 0 else day
    replay_amounts = [dict(row) for row in turnover_history if isinstance(row, dict)
                      and isinstance(row.get('date'), str) and begin <= row['date'] <= day]
    if not turnover['comparison_complete']:
        for row in replay_amounts:
            if row.get('date') == day:
                row['amount'] = None
    replay_history = history
    if (not temperature['evidence_consistent'] or temperature['coverage_pct'] is None
            or temperature['coverage_pct'] < 80):
        # Preserve earlier candidates, but a conflicting T input cannot become
        # a new candidate merely because the retained history alone looks good.
        replay_history = [row for row in history if isinstance(row, dict) and row.get('date') != day]
    tracking = candidate_tracking(day, calendar, replay_history, replay_amounts, reference, universe_upper_bound)
    code, label, summary = stage['code'], stage['label'], stage['summary']
    if bottom['status'] == 'candidate':
        code, label = 'repair_trial', '冰点后放量强修复，出现短底试探候选'
        summary = '广度与量能共同改善；后续检查修复、所属方向和真实成交条件，中期趋势另行核对。'
    elif stage['code'] not in ('unknown', 'ice', 'ebb') and tracking['status'] in ('continuing', 'diverging', 'failed'):
        code, label, summary = tracking['status'], tracking['label'], tracking['reason']
    elif routes[0]['status'] == 'conditional':
        code, label, summary = 'trend_review', '趋势与广度同向，可复核趋势条件', '趋势复核与超短接力资料分开；已有持仓仍须核对自身方向和原定失效条件。'
    return {'version': VERSION, 'date': day, 'snapshot_id': snapshot_id,
            'headline': {'code': code, 'label': label, 'summary': summary,
                         'observation_state': 'unknown' if stage['code'] == 'unknown' else 'observed',
                         'execution_state': 'unobserved'},
            'changes': changes, 'opportunities': opportunities, 'risks': risks, 'gaps': gaps,
            'routes': routes, 'candidate_tracking': tracking}
