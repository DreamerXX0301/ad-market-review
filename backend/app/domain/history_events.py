"""Daily historical event binding with explicit priority and no source I/O."""
from bisect import bisect_right
from math import isfinite
from decimal import Decimal, localcontext
import json
import re

VERSION = 'daily-history-event-v1'
ANY_VERSION = 'daily-history-event-v2-any'
UNSUPPORTED = {'topic_ids', 'board', 'is_st', 'prev_limit_up',
               'higher_lows', 'boll_signal',
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
    from app.domain.history_price_path import cross_cycle_fields
    result=set()
    for tree in trees:
        for event in nodes(tree):
            result |= names(event['where']) | {field for leaf in daily_current_nodes(event['then']) for field in names(leaf) if supported_field(field)}
            cycle=event.get('price_path',{}).get('cross_cycle')
            if cycle:result.update(cross_cycle_fields(cycle['indicator']))
    return result


def preparation_warmup(event):
    """Explicit daily today clauses may need more history than event matching."""
    if 'cross_cycle' in event.get('price_path',{}):return None
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


def _raw_daily_facts(row,previous,positions,previous_date):
    """Only two raw quotes and one shared calendar map; no indicator seed."""
    from app.analytics.indicators import intraday_high_change
    day=row['date'];position=positions.get(day)
    previous=(previous if valid_quote(previous) and previous.get('corporate_action_coverage_known') is True
              and position is not None and positions.get(previous.get('date'))==position-1 else None)
    comparable=previous is not None and not row.get('corporate_action')
    ratio=row['volume']/previous['volume'] if comparable else None
    change=(row['close']/previous['close']-1)*100 if comparable else None
    ready=ratio is not None and isfinite(ratio) and ratio>0
    volume_reason=None if ready else '当前或前一可比周期缺少有效正成交量、存在零量停牌或量能倍数不可核验，放量/缩量保持未知'
    high,high_reason=intraday_high_change(row,previous,positions)
    turnover=row.get('turnover_ratio_pct')
    result={'change_pct':change,'volume':row['volume'],'amount':row.get('amount') if row.get('amount') is None or row['amount']>=0 else None,
            'volume_ratio':ratio,'volume_expand':ratio>1 if ready else None,'volume_shrink':ratio<1 if ready else None,
            'volume_expand_reason':volume_reason,'volume_shrink_reason':volume_reason,
            'intraday_high_change_pct':high,'intraday_high_change_pct_reason':high_reason,'intraday_high_change_pct_date':day,
            'turnover_ratio_pct':turnover,'turnover_ratio_pct_date':day,
            'turnover_ratio_pct_reason':None if turnover is not None else '缺少观察日可核验的换手率；成交额不是换手率，不能用当前流通股本回填历史'}
    for field,quote,target in (('limit_up',row,day),('prev_limit_up',previous,previous_date)):
        value=quote.get('limit_up') if quote else None
        result[field]=value if isinstance(value,bool) else None
        result[field+'_date']=target
        result[field+'_reason']=None if result[field] is not None else '缺少精确市场交易日的有效成交报价或可核验涨停来源；不回溯其他报价日'
    return result


def retained_indicator_rows(rows, calendar, first=None):
    """Replay every seed but emit only the retained window and its predecessor.

    EMA/KD/MA and gap resets still consume the original complete sequence. The
    preceding valid quote is kept so the first retained event has exactly the
    same crossing/volume context as the full materialization.
    """
    from app.analytics.indicators import enrich_bars
    if first is None:return enrich_bars(rows, calendar)
    start=next((index for index,row in enumerate(rows) if row['date']>=first),len(rows))
    return enrich_bars(rows, calendar, tail=max(1,len(rows)-max(0,start-1)))


class HistoryContext:
    """One security, prepared once and reusable for tracking/backtest dates.

    This object is ephemeral. Only evaluate()'s small, selected-event proof may
    enter a persisted business result. Disposable input caches may retain a
    bounded pure technical tail; event decisions and source-pool proofs never
    enter that shared tail.
    """
    def __init__(self, rows, calendar, as_of, snapshot_id=None, enriched=None, retain_start=None,price_context=None):
        # Explicit raw contexts are the same legacy path as no context. Only
        # an opted-in forward context may add anchored adjustment evidence.
        self.price_context=price_context if price_context and price_context.get('requested_price_basis')=='forward' else None
        self.price_basis='none'
        if price_context and price_context.get('requested_price_basis')=='forward':
            self._init_forward(rows,calendar,as_of,snapshot_id,retain_start,price_context)
            return
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
        values = retained_indicator_rows(valid, calendar, retain_start) if enriched is None else enriched
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
        self.raw_quotes=self.quotes
        self.event_cache = {}

    def _init_forward(self,rows,calendar,as_of,snapshot_id,retain_start,context):
        from app.domain.conditions import normalized_bar,metrics_from_enriched,FIELDS,_PreparedMetricBars
        from app.analytics.indicators import enrich_bars
        self.calendar=calendar;self.positions={day:index for index,day in enumerate(calendar)}
        self.as_of=as_of;self.snapshot_id=snapshot_id;self.price_basis=context.get('price_basis','unavailable')
        self.raw_quotes={};self.quotes={};self.daily={};self.event_cache={}
        raw_rows=context.get('raw_rows',rows);prepared_raw=isinstance(raw_rows,_PreparedMetricBars)
        for source in raw_rows.normalized() if prepared_raw else raw_rows:
            day=source.get('date',source.get('trade_date'))
            if not isinstance(day,str) or day>as_of or day not in self.positions:continue
            self.raw_quotes[day]=None if day in self.raw_quotes else dict(source) if prepared_raw else normalized_bar(source)
        usable=self.price_basis=='forward_event_affine' and context.get('anchor_date')==as_of
        price_rows=context.get('price_rows',[]) if usable else [];prepared_prices=isinstance(price_rows,_PreparedMetricBars)
        for source in price_rows.normalized() if prepared_prices else price_rows:
            day=source.get('date')
            if day in self.raw_quotes:self.quotes[day]=None if day in self.quotes else dict(source) if prepared_prices else normalized_bar(source)
        valid=[row for row in self.raw_quotes.values() if valid_quote(row) and row.get('corporate_action_coverage_known') is True]
        valid_dates={row['date'] for row in valid}
        self.invalid_dates=[day for day in calendar if day<=as_of and day not in valid_dates]
        self.unknown_action_dates=sorted(day for day,row in self.raw_quotes.items() if row and row.get('corporate_action_coverage_known') is not True)
        self.action_dates=[]  # Only the verified price lane crosses known actions.
        indicator_rows=[row for day,row in sorted(self.quotes.items()) if day in valid_dates and valid_quote(row)]
        values=retained_indicator_rows(indicator_rows,calendar,retain_start)
        # The historical path has stricter quote eligibility than ordinary
        # indicators. Reuse its fully replayed last two rows only when neither
        # path filtered any seed or adapted missing volume/amount. Otherwise
        # keep the independent original recurrences and unknown explanations.
        if (usable and prepared_prices and len(indicator_rows)==len(price_rows.normalized())
                and all(row.get('amount') is not None for row in indicator_rows)):
            price_rows._daily_indicator_tail=(as_of,tuple(calendar),len(indicator_rows),
                                             tuple(dict(row) for row in values[-2:]))
        enriched_by_date={row['date']:row for row in values};previous_by_date={row['date']:values[i-1] if i else None for i,row in enumerate(values)}
        facts={'change_pct','volume','amount','volume_ratio','volume_expand','volume_shrink','intraday_high_change_pct','limit_up','prev_limit_up','turnover_ratio_pct'}
        for raw in valid:
            day=raw['date']
            if retain_start is not None and day<retain_start:continue
            row=enriched_by_date.get(day);metrics=metrics_from_enriched(row,previous_by_date.get(day))
            position=self.positions[day];before=self.raw_quotes.get(calendar[position-1]) if position else None
            source=_raw_daily_facts(raw,before,self.positions,calendar[position-1] if position else None)
            for field in facts:
                for suffix in ('','_reason','_date','_status','_evidence'):
                    if field+suffix in source:metrics[field+suffix]=source[field+suffix]
            if not usable:
                reason=('复权锚与当前观察日不一致，不能使用未来锚价格' if context.get('anchor_date')!=as_of else context.get('adjustment',{}).get('reason') or '本地前复权价格不可用')
                for field in set(FIELDS)-facts:
                    if metrics.get(field) is None:metrics[field+'_reason']=reason
                self.quotes[day]={**raw,**dict.fromkeys(('open','high','low','close'))}
            metrics.update(observed_date=day,price_basis=self.price_basis,adjustment_anchor=as_of)
            metrics.update(limit_up=None,limit_up_reason='历史涨停尚未核验同日来源收盘池与原始成交报价')
            self.daily[day]=metrics
        if retain_start is not None:
            self.quotes={day:row for day,row in self.quotes.items() if day>=retain_start}
            self.raw_quotes={day:row for day,row in self.raw_quotes.items() if day>=retain_start}

    @classmethod
    def from_prepared(cls, prepared, symbol):
        """Materialize only this security from compact, shared dated arrays."""
        from app.domain.conditions import metrics_from_enriched
        import numpy as np

        context = object.__new__(cls)
        context.price_context=None;context.price_basis='none'
        context.calendar = prepared['calendar']
        context.positions = prepared['positions']
        context.as_of = prepared['as_of']
        context.snapshot_id = prepared['snapshot_id']
        context.quotes = {}; context.daily = {}; context.invalid_dates = []
        context.action_dates = []; context.unknown_action_dates = []
        context.event_cache = {}
        context.raw_quotes=context.quotes
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
                kept = fields | {key + suffix for key in fields for suffix in ('_reason','_evidence')} | {'observed_date', 'indicator_warmup_bars'}
                if 'close_breakout_high' in fields:
                    kept |= {'breakout_valid', 'breakout_previous_sessions', 'breakout_blocking_distance', 'new_high_current_close'}
                context.daily[day] = {key: value for key, value in metrics.items() if key in kept}
                if 'limit_up' in fields:
                    context.daily[day].update(limit_up=None, limit_up_reason='历史涨停尚未核验同日来源收盘池与原始成交报价')
            previous = row
        return context

    def at(self, as_of):
        # Do not copy or re-enrich the security's history for replay dates.
        if self.price_context and self.price_context.get('requested_price_basis')=='forward' and self.price_context.get('anchor_date')!=as_of:
            return {'context':None,'as_of':as_of,'price_reason':'复权锚与当前观察日不一致，不能使用未来锚价格'}
        return {'context': self, 'as_of': as_of}

    def price_binding(self, event, day):
        if self.price_context and self.price_context.get('requested_price_basis')=='forward':
            if self.price_basis!='forward_event_affine' or self.price_context.get('anchor_date')!=self.as_of:
                return None,self.price_context.get('adjustment',{}).get('reason') or '本地前复权价格与观察日锚未核验'
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


def _daily_then_metrics(metrics,context,day):
    """New structures use the caller's strict daily summary, not event seeds."""
    daily=dict(context.daily.get(day,{}))
    source=metrics.get('_period_metrics',{}).get('D')
    if source is None and metrics.get('indicator_period','D')=='D':source=metrics
    if source and 'higher_lows_state' in source:daily['higher_lows_state']=source['higher_lows_state']
    if source and 'boll_states' in source:daily['boll_states']=source['boll_states']
    return daily


def _bind_candidate(result, spec, metrics, history, context, day, target, offset, check, *, skip_failed_path=False):
    """All later clauses share exactly this candidate's event and price basis."""
    from app.domain.conditions import evaluate
    event = context.quotes[target]
    result['selected_event'] = {field: event.get(field) for field in ('date', 'open', 'high', 'low', 'close')}
    result['selected_event'].update(offset=offset, price_basis=context.price_basis)
    if context.price_context:
        raw_event=context.raw_quotes.get(target) or {}
        result['selected_event'].update(anchor_date=context.price_context.get('anchor_date'),
            **{'raw_'+key:raw_event.get(key) for key in ('open','high','low','close')})
    result['event_checks'] = check
    current_price, price_reason = context.price_binding(event, day)
    bound = {**history, 'event': event, 'current_price': current_price, 'price_reason': price_reason}
    # New explicitly daily clauses read the observation day's own history
    # context. Unscoped legacy clauses keep their strategy-period semantics.
    then_metrics={**metrics,'_period_metrics':{**metrics.get('_period_metrics',{}),'D':_daily_then_metrics(metrics,context,day)}}
    then = evaluate(spec['then'], then_metrics, bound)
    result.update(result=then['result'], checks=[then], reason=then.get('reason'))
    if 'price_path' in spec and not (skip_failed_path and then['result'] is False):
        from app.domain.history_price_path import evaluate_price_path, all_results
        path = evaluate_price_path(spec['price_path'], context, event, day)
        result['price_path_checks'] = path
        state = all_results([then['result'], path['result']])
        result.update(result=state, reason=(path.get('reason') if path['result'] is state else then.get('reason')) if state is not True else None)
    result['evidence'].update(current_price=current_price, current_price_date=day, current_price_period='D',
                              price_comparable=current_price is not None, price_comparison_reason=price_reason)
    return result


def _evaluate_any(result, spec, metrics, history, context, day, position):
    """Existential three-valued matching; retain one proof, never all candidates."""
    from app.domain.conditions import evaluate
    from app.domain.history_price_path import all_results
    summary = {'order': 'nearest_first', 'examined_sessions': 0, 'where_matches': 0,
               'rejected_candidates': 0, 'unknown_event_sessions': 0, 'unknown_candidates': 0,
               'stopped_after_match': False, 'complete_window_scan': False}
    result['search_summary'] = summary
    unbound_then = None

    def independent_then_false():
        nonlocal unbound_then
        if unbound_then is None:
            periods = dict(metrics.get('_period_metrics', {}))
            if context is not None:
                periods['D'] = _daily_then_metrics(metrics,context,day)
            bound = {**(history if isinstance(history, dict) else {}), 'event': None,
                     'current_price': None, 'price_reason': '没有可绑定的事件报价，事件价保持未知'}
            unbound_then = evaluate(spec['then'], {**metrics, '_period_metrics': periods}, bound)
        return unbound_then['result'] is False

    if position is None:
        summary['context_unavailable'] = True
        if independent_then_false():
            summary['independent_then_false'] = True
            result.update(result=False, reason='独立今日条件已不满足，无事件绑定也可确认范围内没有完整满足的候选')
            return result
        summary['first_unknown'] = {'date': None, 'offset': None, 'stage': 'context', 'reason': result['reason']}
        return result

    def unknown(target, offset, stage, reason):
        summary.setdefault('first_unknown', {'date': target, 'offset': offset, 'stage': stage, 'reason': reason})

    where_key = json.dumps(spec['where'], sort_keys=True, separators=(',', ':'))
    for offset in range(spec['window']['from'], spec['window']['to'] + 1):
        summary['examined_sessions'] += 1
        target = context.calendar[position-offset] if position >= offset else None
        if target is None or target not in context.daily:
            summary['unknown_event_sessions'] += 1
            if independent_then_false():
                summary['rejected_candidates'] += 1
                continue
            summary['unknown_candidates'] += 1
            unknown(target, offset, 'where', '事件位置缺少唯一有效日报价、公司行动覆盖或足够市场日历')
            continue
        key = (where_key, target)
        if key not in context.event_cache:
            context.event_cache[key] = evaluate(spec['where'], context.daily[target])
            if len(context.event_cache) > 4096: context.event_cache.pop(next(iter(context.event_cache)))
        check = context.event_cache[key]
        if check['result'] is None:
            summary['unknown_event_sessions'] += 1
        if check['result'] is False:
            continue
        if check['result'] is True:
            summary['where_matches'] += 1
        candidate = _bind_candidate({**result, 'evidence': dict(result['evidence'])}, spec, metrics, history,
                                    context, day, target, offset, check, skip_failed_path=True)
        # A known false later clause disproves even an unconfirmed where.
        # A provisional candidate is never returned as a selected event.
        state = all_results([check['result'], candidate['result']])
        if state is True:
            summary.update(stopped_after_match=True, complete_window_scan=offset == spec['window']['to'])
            return candidate
        if state is None:
            summary['unknown_candidates'] += 1
            unknown(target, offset, 'where' if check['result'] is None else 'candidate',
                    (check.get('reason') if check['result'] is None else candidate.get('reason'))
                    or '同一事件的今日条件或价格过程尚未知')
        else:
            summary['rejected_candidates'] += 1
    summary['complete_window_scan'] = True
    state = None if summary['unknown_candidates'] else False
    result.update(result=state, reason='范围内尚无事件整体满足，仍有事件条件或后续条件未知' if state is None
                  else '完整检查的历史窗口内，没有同一事件同时满足事件、今日和价格过程条件')
    return result


def evaluate_event(tree, metrics, history):
    from app.domain.conditions import evaluate
    spec = tree['history_event']; window = spec['window']
    context = history.get('context') if isinstance(history, dict) else None
    day = history.get('as_of') if isinstance(history, dict) else None
    position = context.positions.get(day) if context else None
    offsets = range(window['from'], window['to']+1)
    if spec['match'] == 'earliest': offsets = reversed(offsets)
    evidence = {'model': ANY_VERSION if spec['match']=='any' else VERSION, 'snapshot_id': context.snapshot_id if context else None,
                'as_of': day, 'event_period': 'D', 'price_basis': context.price_basis if context else 'none',
                'current_observation_date': metrics.get('observed_date'),
                'current_condition_period': metrics.get('indicator_period', 'D')}
    if context and context.price_context:
        evidence.update(requested_price_basis='forward',anchor_date=context.price_context.get('anchor_date'))
    actual_window = {**window, 'requested_sessions': window['to']-window['from']+1,
                     'calendar_sessions': sum(position is not None and position >= offset for offset in range(window['from'], window['to']+1)),
                     'available_sessions': sum(position is not None and position >= offset and context.calendar[position-offset] in context.daily for offset in range(window['from'], window['to']+1)),
                     'start': context.calendar[position-window['to']] if position is not None and position >= window['to'] else None,
                     'end': context.calendar[position-window['from']] if position is not None and position >= window['from'] else None}
    result = {**tree, 'type': 'history_event', 'result': None, 'selected_event': None,
              'event_checks': None, 'checks': [], 'window': actual_window, 'evidence': evidence,
              'reason': (history.get('price_reason') if isinstance(history,dict) else None) or '没有准备历史日行情上下文，保持未知'}
    if 'price_path' in spec:
        result['price_path_checks'] = None
    if spec['match'] == 'any':
        return _evaluate_any(result, spec, metrics, history, context, day, position)
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
        return _bind_candidate(result, spec, metrics, history, context, day, target, offset, check)
    result.update(result=False, reason='完整检查的历史窗口内没有满足事件条件的日期')
    return result


def evaluate_event_price(node, history):
    event = history.get('event') if isinstance(history, dict) else None
    current = history.get('current_price') if isinstance(history, dict) else None
    right = event.get(node['right_event_field']) if event else None
    if 'right_event_change_pct' in node:
        return _evaluate_relative_event_price(node,history,event,current,right)
    result = None
    if positive(current) and positive(right):
        result = {'>': lambda: current > right, '>=': lambda: current >= right,
                  '<': lambda: current < right, '<=': lambda: current <= right,
                  '==': lambda: current == right, '!=': lambda: current != right}[node['op']]()
    return {**node, 'actual': current, 'right_actual': right, 'result': result,
            'observation_date': history.get('as_of') if isinstance(history, dict) else None,
            'observation_period': 'D', 'event_date': event.get('date') if event else None,
            'reason': ((history.get('price_reason') if isinstance(history, dict) else None) or '尚无本节点绑定的历史事件及可比原始价格') if result is None else None}


def _decimal_number(value,positive_only=False):
    number=float(value)
    return number if isfinite(number) and (not positive_only or number>0) else None


def _evaluate_relative_event_price(node,history,event,current,right):
    """Signed event return thresholds use exact decimal multiplication."""
    threshold=relative=None;state=None
    if positive(right):
        with localcontext() as arithmetic:
            # A finite binary64 percentage may be as small as 5e-324.
            # 1024 significant digits preserve 1+p/100 and its product with
            # every normalized finite price without a tolerance or cent rounding.
            arithmetic.prec=1024
            threshold=Decimal(str(right))*(1+Decimal(str(node['right_event_change_pct']))/100)
            if positive(current):
                actual=Decimal(str(current))
                with localcontext() as display:
                    display.prec=80
                    relative=(actual/Decimal(str(right))-1)*100
                state={'>':lambda:actual>threshold,'>=':lambda:actual>=threshold,
                       '<':lambda:actual<threshold,'<=':lambda:actual<=threshold,
                       '==':lambda:actual==threshold,'!=':lambda:actual!=threshold}[node['op']]()
    return {**node,'actual':current,'right_actual':_decimal_number(threshold,True) if threshold is not None else None,
            'event_price':right,'threshold_decimal':format(threshold,'f') if threshold is not None else None,
            'event_change_pct':_decimal_number(relative) if relative is not None else None,
            'event_change_pct_decimal':format(relative,'f') if relative is not None else None,
            'comparison_model':'daily-event-relative-price-v1',
            'observation_date':history.get('as_of') if isinstance(history,dict) else None,
            'observation_period':'D','event_date':event.get('date') if event else None,
            'reason':((history.get('price_reason') if isinstance(history,dict) else None) or '尚无本节点绑定的历史事件及可比原始价格') if state is None else None,
            'result':state}
