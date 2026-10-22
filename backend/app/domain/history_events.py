"""Daily historical event binding with explicit priority and no source I/O."""
from bisect import bisect_right
from math import isfinite
import json
import re

VERSION = 'daily-history-event-v1'
UNSUPPORTED = {'topic_ids', 'board', 'is_st', 'prev_limit_up',
               'limit_up_1y', 'limit_up_count_1y', 'turnover_ratio_pct',
               'tracking_change_pct', 'trading_age', 'close_below_entry',
               'close_above_entry_high', 'close_below_entry_low'}


def supported_field(field):
    return field not in UNSUPPORTED


def nodes(tree):
    if 'history_event' in tree: return [tree['history_event']]
    return [node for child in tree.get('all', tree.get('any', [])) for node in nodes(child)]


def current_names(tree):
    from app.domain.conditions import names
    if 'history_event' in tree: return names(tree['history_event']['then'])
    if 'field' in tree: return names(tree)
    return set().union(*(current_names(child) for child in tree.get('all', tree.get('any', []))))


def warmup(where):
    """None means recursive EMA/KDJ needs the original retained seed history."""
    from app.domain.conditions import names
    fields = names(where)
    if any(field.startswith(('macd', 'kdj')) or field in ('kd_cross', 'kd_dead_cross') for field in fields): return None
    count = 2
    for field in fields:
        match = re.search(r'ma(\d+)$', field)
        if match: count = max(count, int(match[1]) + int(field.startswith('cross_')))
        if field == 'bullish_ma_alignment': count = max(count, 60)
    def daily_window(node):
        if node.get('field') == 'close_breakout_high': return node['value'] + 1
        return max((daily_window(child) for child in node.get('all', node.get('any', []))), default=0)
    return max(count, daily_window(where))


def daily_current_nodes(node):
    if 'field' in node: return [node] if node.get('period') == 'D' else []
    return [leaf for child in node.get('all', node.get('any', [])) for leaf in daily_current_nodes(child)]


def prepared_fields(trees):
    """Event fields plus explicitly daily 'today' inputs share the same seed.

    Unscoped saved clauses retain the strategy-period interpretation. Their
    inputs continue to come from the caller's current metrics, not this view.
    """
    from app.domain.conditions import names
    return set().union(*(names(event['where']) | {field for leaf in daily_current_nodes(event['then']) for field in names(leaf) if supported_field(field)} for tree in trees for event in nodes(tree)))


def preparation_warmup(event):
    """Explicit daily today clauses may need more history than event matching."""
    return warmup({'all': [event['where']] + daily_current_nodes(event['then'])})


def read_start(trees, calendar, as_of):
    events = [event for tree in trees for event in nodes(tree)]
    if not events or as_of not in calendar: return as_of
    periods = [preparation_warmup(event) for event in events]
    if None in periods: return calendar[0]
    distance = max(event['window']['to'] + count - 1 for event, count in zip(events, periods))
    return calendar[max(0, calendar.index(as_of)-distance)]


def retain_start(trees, calendar, as_of):
    events = [event for tree in trees for event in nodes(tree)]
    if not events or as_of not in calendar: return as_of
    return calendar[max(0, calendar.index(as_of)-max(event['window']['to'] for event in events))]


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value > 0


def valid_quote(row):
    return (row and all(positive(row.get(field)) for field in ('open', 'high', 'low', 'close', 'volume'))
            and row['high'] >= max(row['open'], row['close'], row['low'])
            and row['low'] <= min(row['open'], row['close'], row['high']))


class HistoryContext:
    """One security, prepared once and reusable for tracking/backtest dates.

    This object is ephemeral. Only evaluate()'s small, selected-event proof may
    enter a persisted result; the full daily dictionary is never serialized.
    """
    def __init__(self, rows, calendar, as_of, snapshot_id=None, enriched=None, retain_start=None):
        from app.domain.conditions import normalized_bar, metrics_from_enriched
        from app.analytics.indicators import enrich_bars
        self.calendar = calendar
        self.positions = {day: index for index, day in enumerate(calendar)}
        self.as_of = as_of
        self.snapshot_id = snapshot_id
        self.quotes = {}
        for source in rows:
            day = source.get('date', source.get('trade_date'))
            if not isinstance(day, str) or day > as_of or day not in self.positions: continue
            row = normalized_bar(source)
            self.quotes[day] = None if day in self.quotes else row
        valid = [row for _, row in sorted(self.quotes.items()) if valid_quote(row) and row.get('corporate_action_coverage_known') is True]
        valid_dates = {row['date'] for row in valid}
        self.invalid_dates = [day for day in calendar if day <= as_of and day not in valid_dates]
        values = enrich_bars(valid, calendar) if enriched is None else enriched
        self.daily = {}; previous = None
        for row in values:
            day = row['date']; raw = self.quotes.get(day)
            if day > as_of or not valid_quote(raw): continue
            if retain_start is None or day >= retain_start:
                self.daily[day] = metrics_from_enriched(row, previous)
                # Raw price-panel flags are not accepted source-pool evidence.
                # The service adapter fills this field only after exact-date,
                # identity, raw closing-price and traded-volume verification.
                self.daily[day].update(limit_up=None, limit_up_reason='历史涨停尚未核验同日来源收盘池与原始成交报价')
                if self.daily[day].get('amount') is not None and self.daily[day]['amount'] < 0:
                    self.daily[day]['amount'] = None
            previous = row
        self.action_dates = sorted(day for day, row in self.quotes.items() if row and row.get('corporate_action'))
        self.unknown_action_dates = sorted(day for day, row in self.quotes.items() if row and row.get('corporate_action_coverage_known') is not True)
        if retain_start is not None: self.quotes = {day: row for day, row in self.quotes.items() if day >= retain_start}
        self.event_cache = {}

    @classmethod
    def from_prepared(cls, prepared, symbol):
        """Materialize only this security from compact, shared dated arrays."""
        from app.domain.conditions import metrics_from_enriched
        import numpy as np

        context = object.__new__(cls)
        context.calendar = prepared['calendar']
        context.positions = prepared['positions']
        context.as_of = prepared['as_of']
        context.snapshot_id = prepared['snapshot_id']
        context.quotes = {}; context.daily = {}; context.invalid_dates = []
        context.action_dates = []; context.unknown_action_dates = []
        context.event_cache = {}
        index = prepared['symbol_index'].get(symbol)
        if index is None or prepared.get('unavailable'):
            return context

        def scalar(value):
            if isinstance(value, np.generic): value = value.item()
            return None if isinstance(value, float) and not isfinite(value) else value

        previous = None
        fields = prepared['fields']
        for offset, day in enumerate(prepared['days']):
            values = prepared['daily'][offset]
            raw = {key: scalar(matrix[offset, index]) for key, matrix in prepared['quotes'].items()}
            raw['date'] = day
            raw['corporate_action'] = bool(prepared['actions'][offset, index])
            coverage = prepared['action_coverage']
            raw['corporate_action_coverage_known'] = coverage if isinstance(coverage, bool) else bool(coverage[offset, index])
            # Duplicates/malformed quotes are excluded by the shared strict
            # bitmap before any indicator can become an event candidate.
            present = bool(values['present'][index])
            if day >= prepared['retain_start']:
                context.quotes[day] = raw
                if not present: context.invalid_dates.append(day)
                if raw['corporate_action']: context.action_dates.append(day)
                if not raw['corporate_action_coverage_known']: context.unknown_action_dates.append(day)
            if not present: continue
            row = {**raw, **{key: scalar(array[index]) for key, array in values.items() if key != 'present'}}
            metrics = metrics_from_enriched(row, previous)
            if metrics.get('amount') is not None and metrics['amount'] < 0: metrics['amount'] = None
            if day >= prepared['retain_start']:
                kept = fields | {key + '_reason' for key in fields} | {'observed_date', 'indicator_warmup_bars'}
                if 'close_breakout_high' in fields:
                    kept |= {'breakout_valid', 'breakout_previous_sessions', 'breakout_blocking_distance', 'new_high_current_close'}
                context.daily[day] = {key: value for key, value in metrics.items() if key in kept}
                if 'limit_up' in fields:
                    context.daily[day].update(limit_up=None, limit_up_reason='历史涨停尚未核验同日来源收盘池与原始成交报价')
            previous = row
        return context

    def at(self, as_of):
        # Do not copy or re-enrich the security's history for replay dates.
        return {'context': self, 'as_of': as_of}

    def price_binding(self, event, day):
        current = self.quotes.get(day)
        if not valid_quote(current): return None, '观察日缺少唯一有效日K成交报价，事件价比较保持未知'
        start = event['date']
        if bisect_right(self.invalid_dates, day) > bisect_right(self.invalid_dates, start):
            return None, '事件日至观察日有缺失、重复、零量或行动覆盖未知的市场日报价；不能跳过可能隐含的公司行动比较原价'
        if not current.get('corporate_action_coverage_known') or not event.get('corporate_action_coverage_known'):
            return None, '公司行动覆盖未知，不直接比较两期原始价格'
        if bisect_right(self.unknown_action_dates, day) > bisect_right(self.unknown_action_dates, start):
            return None, '事件日至观察日公司行动覆盖未知'
        if bisect_right(self.action_dates, day) > bisect_right(self.action_dates, start):
            return None, '事件日后存在公司行动；尚无已核验的事件价转换，不直接比较两期原价'
        return current['close'], None


def evaluate_event(tree, metrics, history):
    from app.domain.conditions import evaluate
    spec = tree['history_event']; window = spec['window']
    context = history.get('context') if isinstance(history, dict) else None
    day = history.get('as_of') if isinstance(history, dict) else None
    position = context.positions.get(day) if context else None
    offsets = range(window['from'], window['to']+1)
    if spec['match'] == 'earliest': offsets = reversed(offsets)
    evidence = {'model': VERSION, 'snapshot_id': context.snapshot_id if context else None,
                'as_of': day, 'event_period': 'D', 'price_basis': 'none',
                'current_observation_date': metrics.get('observed_date'),
                'current_condition_period': metrics.get('indicator_period', 'D')}
    actual_window = {**window, 'requested_sessions': window['to']-window['from']+1,
                     'calendar_sessions': sum(position is not None and position >= offset for offset in range(window['from'], window['to']+1)),
                     'available_sessions': sum(position is not None and position >= offset and context.calendar[position-offset] in context.daily for offset in range(window['from'], window['to']+1)),
                     'start': context.calendar[position-window['to']] if position is not None and position >= window['to'] else None,
                     'end': context.calendar[position-window['from']] if position is not None and position >= window['from'] else None}
    result = {**tree, 'type': 'history_event', 'result': None, 'selected_event': None,
              'event_checks': None, 'checks': [], 'window': actual_window, 'evidence': evidence,
              'reason': '没有准备历史日行情上下文，保持未知'}
    if 'price_path' in spec:
        result['price_path_checks'] = None
    if position is None: return result
    where_key = json.dumps(spec['where'], sort_keys=True, separators=(',', ':'))
    for offset in offsets:
        target = context.calendar[position-offset] if position >= offset else None
        if target is None or target not in context.daily:
            result['reason'] = '优先匹配位置缺少唯一有效日报价、公司行动覆盖或足够市场日历，不能跳过未知改选其他事件'
            evidence.update(blocking_date=target, blocking_offset=offset)
            return result
        key = (where_key, target)
        if key not in context.event_cache:
            context.event_cache[key] = evaluate(spec['where'], context.daily[target])
            if len(context.event_cache) > 4096: context.event_cache.pop(next(iter(context.event_cache)))
        check = context.event_cache[key]
        if check['result'] is None:
            result.update(event_checks=check, reason='优先日期的事件条件尚未知，不能绕过后绑定其他已知匹配')
            evidence.update(blocking_date=target, blocking_offset=offset)
            return result
        if check['result'] is False: continue
        event = context.quotes[target]
        result['selected_event'] = {field: event.get(field) for field in ('date', 'open', 'high', 'low', 'close')}
        result['selected_event'].update(offset=offset, price_basis='none')
        result['event_checks'] = check
        current_price, price_reason = context.price_binding(event, day)
        bound = {**history, 'event': event, 'current_price': current_price, 'price_reason': price_reason}
        # New explicitly daily clauses read the observation day's own history
        # context. Unscoped legacy clauses keep their strategy-period semantics.
        then_metrics={**metrics,'_period_metrics':{**metrics.get('_period_metrics',{}),'D':context.daily.get(day,{})}}
        then = evaluate(spec['then'], then_metrics, bound)
        result.update(result=then['result'], checks=[then], reason=then.get('reason'))
        if 'price_path' in spec:
            from app.domain.history_price_path import evaluate_price_path, all_results
            path = evaluate_price_path(spec['price_path'], context, event, day)
            result['price_path_checks'] = path
            state = all_results([then['result'], path['result']])
            result.update(result=state, reason=(path.get('reason') if path['result'] is state else then.get('reason')) if state is not True else None)
        evidence.update(current_price=current_price, current_price_date=day, current_price_period='D',
                        price_comparable=current_price is not None, price_comparison_reason=price_reason)
        return result
    result.update(result=False, reason='完整检查的历史窗口内没有满足事件条件的日期')
    return result


def evaluate_event_price(node, history):
    event = history.get('event') if isinstance(history, dict) else None
    current = history.get('current_price') if isinstance(history, dict) else None
    right = event.get(node['right_event_field']) if event else None
    result = None
    if positive(current) and positive(right):
        result = {'>': lambda: current > right, '>=': lambda: current >= right,
                  '<': lambda: current < right, '<=': lambda: current <= right,
                  '==': lambda: current == right, '!=': lambda: current != right}[node['op']]()
    return {**node, 'actual': current, 'right_actual': right, 'result': result,
            'observation_date': history.get('as_of') if isinstance(history, dict) else None,
            'observation_period': 'D', 'event_date': event.get('date') if event else None,
            'reason': ((history.get('price_reason') if isinstance(history, dict) else None) or '尚无本节点绑定的历史事件及可比原始价格') if result is None else None}
