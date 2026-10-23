"""Local closing-pool evidence for daily strategy/tracking Boolean conditions."""
from __future__ import annotations

import math
from bisect import bisect_left
from datetime import date, timedelta

from app.services.chart_limits import _combine, _evidence
from app.services.market_supplement import load_supplement

DAILY_LIMIT_FIELDS = {'limit_up', 'prev_limit_up'}
COUNT_FIELD = 'limit_up_count_1y'
LIMIT_FIELDS = DAILY_LIMIT_FIELDS | {COUNT_FIELD}
BATCH_SIZE = 256


def count_window(day):
    """One calendar year including its first date, excluding the observation day."""
    observed = date.fromisoformat(day)
    try: start = observed.replace(year=observed.year - 1)
    except ValueError: start = observed.replace(year=observed.year - 1, day=28)
    return start.isoformat(), (observed - timedelta(days=1)).isoformat()


def quote_index(rows):
    result = {}
    for row in rows:
        day = str(row.get('date', row.get('trade_date', '')))[:10]
        result[day] = None if day in result else row
    return result


def _valid(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


class LimitConditionEvidence:
    """Reuse chart proof rules, indexing source codes once per date/direction.

    All I/O is local metadata in the constructor, before a tracking transaction.
    A proof consumes the target day's raw close and actual traded volume; no
    percent-change threshold, current ST label, or later source day is inferred.
    """
    def __init__(self, market):
        reference = getattr(market, 'reference', None)
        self.reference = reference() if callable(reference) else {}
        status = getattr(market, 'status', None)
        self.snapshot_id = status().get('snapshot_id') if callable(status) else None
        try:
            self.supplement = load_supplement(self.snapshot_id) if self.snapshot_id else {}
        except (OSError, ValueError):
            self.supplement = {'failure_reason': '补充索引缺失或未通过完整性校验'}
        if self.supplement.get('snapshot_id') not in (None, self.snapshot_id):
            self.supplement = {}
        self._sources = {}
        self._count_days = {}

    def _records(self, day, kind):
        key = (day, kind)
        if key in self._sources:
            return self._sources[key]
        pool = 'limitup' if kind == 'up' else 'limitdown'
        records = []
        historical = self.reference.get('pool_history', {}).get(day, {})
        for container, path, coverage_key, quarantine_key in (
            (historical, 'reference.pool_history.' + day, 'coverage', 'quarantine'),
            (self.reference if self.reference.get('pool_trade_date') == day else {}, 'reference', 'pool_coverage', 'pool_quarantine'),
        ):
            if pool in container:
                record = {'date': day, 'items': container[pool],
                          'complete': container.get(coverage_key, {}).get(pool, {}).get('complete', False),
                          'quarantine': container.get(quarantine_key, {}).get(pool, [])}
                records.append((record, 'fuyao', path + '.' + pool, False, True))
        annual_key = 'annual_pools' if kind == 'up' else 'annual_down_pools'
        annual = self.supplement.get(annual_key, {}).get(day)
        if annual is not None:
            records.append((annual, annual.get('source') or 'fuyao', annual_key + '.' + day, True, True))
        public = self.supplement.get('public_pools', {}).get(day) if kind == 'up' else None
        if public is not None:
            records.append((public, public.get('source') or 'eastmoney-public', 'public_pools.' + day, False, False))
        indexed = []
        for record, source, path, granular, allow_absence in records:
            by_code = {}
            items = record.get('items')
            for row in items if isinstance(items, list) else []:
                if not isinstance(row, dict):
                    continue
                codes = {str(row.get(name) or '')[:6] for name in ('instrument_id', 'thscode', 'ticker', 'code')}
                for code in codes:
                    by_code.setdefault(code, []).append(row)
            indexed.append((record, by_code, source, path, granular, allow_absence))
        self._sources[key] = indexed
        return indexed

    def _state(self, symbol, day, quote):
        if not day or not quote or quote.get('date', quote.get('trade_date')) != day or not _valid(quote.get('close')) or not _valid(quote.get('volume')):
            return {'state': None, 'status': 'unknown', 'reason': '缺少该市场交易日有效成交报价，停牌或缺报不当作未涨停', 'evidence': []}
        states = {}
        for kind in ('up', 'down'):
            proofs = []
            for record, by_code, source, path, granular, allow_absence in self._records(day, kind):
                # Preserve the original nonempty-pool fact for absence proofs,
                # while avoiding a full pool scan for each of 5,000 stocks.
                items = record.get('items')
                narrowed = by_code.get(symbol[:6], [])
                record_view = {**record, 'items': narrowed or [{'instrument_id': 'unrelated-source-row'}]} if isinstance(items, list) and items else record
                proofs.append(_evidence(record_view, symbol, day, quote['close'],
                                        source=source, path=path, kind=kind,
                                        granular=granular, allow_absence=allow_absence))
            states[kind] = _combine(proofs)
        if any(value['status'] == 'conflict' for value in states.values()) or all(value['state'] is True for value in states.values()):
            return {'state': None, 'status': 'conflict', 'reason': '同日来源方向、证券或原始收盘证据冲突，保持未知',
                    'evidence': states['up']['evidence'] + states['down']['evidence']}
        result = states['up']
        if result['state'] is None and self.supplement.get('failure_reason'):
            result = {**result, 'reason': '补充来源完整性未通过核验；' + str(self.supplement['failure_reason'])}
        return result

    def metrics(self, symbol, day, calendar, quotes):
        position = calendar.index(day) if day in calendar else None
        previous = calendar[position - 1] if position is not None and position > 0 else None
        result = {}
        for field, target in (('limit_up', day), ('prev_limit_up', previous)):
            state = self._state(symbol, target, quotes.get(target)) if position is not None else {'state': None, 'status': 'unknown', 'reason': '观察日不在已留存市场交易日历中', 'evidence': []}
            result.update({field: state['state'], field + '_date': target,
                           field + '_reason': state['reason'], field + '_status': state['status'],
                           field + '_evidence': {'basis': '同日原始收盘与有效成交量核验的来源收盘池',
                                                'snapshot_id': self.snapshot_id,
                                                'supplement_job_id': self.supplement.get('job_id'),
                                                'source_proofs': state['evidence']}})
        return result

    def _count_day(self, day):
        """Compile default absence once; ordinary stocks never rescan raw codes.

        Events, quarantines and explicit raw codes are the only securities whose
        proof can differ from the generic source-absence result. Their actual
        raw close is still verified separately; no pool price supplies a quote.
        """
        if day not in self._count_days:
            special = set()
            for kind in ('up', 'down'):
                for record, by_code, *_ in self._records(day, kind):
                    special.update(by_code)
                    for key in ('raw_codes', 'rejected_codes'):
                        values = record.get(key)
                        if isinstance(values, list): special.update(str(code)[:6] for code in values)
                    for row in record.get('quarantine', []):
                        if isinstance(row, dict):
                            special.update(str(row.get(key) or '')[:6] for key in ('instrument_id', 'thscode', 'ticker', 'code'))
            default = self._state('__ABSENT_CODE__', day, {'date': day, 'close': 1, 'volume': 1})
            self._count_days[day] = {'special': special, 'default': default}
        return self._count_days[day]

    def _count_state(self, symbol, day, quote):
        if not quote or quote.get('date', quote.get('trade_date')) != day or not _valid(quote.get('close')) or not _valid(quote.get('volume')):
            return None, 'unknown'
        prepared = self._count_day(day)
        state = self._state(symbol, day, quote) if symbol[:6] in prepared['special'] else prepared['default']
        return state['state'], state['status']

    def historical_metrics(self, symbol, day, quote):
        """One exact event day; source codes/absence are compiled once per day.

        Generic source absence is reusable only after this security's unique
        raw traded quote is verified. Special source rows still verify their
        own identity and closing price, including opposite-direction conflicts.
        """
        if not quote or quote.get('date', quote.get('trade_date')) != day or not _valid(quote.get('close')) or not _valid(quote.get('volume')):
            state = self._state(symbol, day, quote)
        else:
            prepared = self._count_day(day)
            state = self._state(symbol, day, quote) if symbol[:6] in prepared['special'] else prepared['default']
        return {'limit_up': state['state'], 'limit_up_date': day,
                'limit_up_status': state['status'], 'limit_up_reason': state['reason'],
                'limit_up_evidence': {'basis': '同日原始收盘与有效成交量核验的来源收盘池',
                                      'snapshot_id': self.snapshot_id,
                                      'supplement_job_id': self.supplement.get('job_id'),
                                      'source_proofs': state['evidence']}}

    def count_series(self, symbol, observations, calendar, quotes):
        """Verify each source day once per stock; answer rolling windows by prefix sums."""
        observations = sorted(set(observations))
        if not observations: return {}
        calendar = sorted(set(calendar))
        first = count_window(observations[0])[0]
        timeline = [day for day in calendar if first <= day < observations[-1]]
        positives = []; unknowns = []
        counts = [0]; known = [0]; conflict_counts = [0]
        for day in timeline:
            state, status = self._count_state(symbol, day, quotes.get(day))
            counts.append(counts[-1] + (state is True))
            known.append(known[-1] + (state is not None))
            conflict_counts.append(conflict_counts[-1] + (status == 'conflict'))
            if state is True: positives.append(day)
            if state is None: unknowns.append(day)
        result = {}
        for observed in observations:
            start, end = count_window(observed)
            left = bisect_left(timeline, start); right = bisect_left(timeline, observed)
            calendar_complete = bool(calendar and calendar[0] <= start and observed in calendar)
            lower = counts[right] - counts[left]
            covered = known[right] - known[left]
            retained = right - left
            unknown = retained - covered
            complete = calendar_complete and unknown == 0
            upper = lower + unknown if calendar_complete else None
            confirmed_dates = positives[bisect_left(positives, start):bisect_left(positives, observed)]
            unknown_dates = unknowns[bisect_left(unknowns, start):bisect_left(unknowns, observed)]
            conflict_days = conflict_counts[right] - conflict_counts[left]
            reason = None if complete else ('市场日历未证明完整自然年窗口；' if not calendar_complete else '') + '近一年来源池或本证券逐日原始成交报价覆盖不足，仅保留已确认次数下界'
            if not complete and self.supplement.get('failure_reason'): reason += '；补充来源完整性校验失败'
            coverage = {'complete': complete, 'window_start': start, 'window_end': end,
                        'calendar_complete': calendar_complete, 'expected_days': retained if calendar_complete else None,
                        'retained_expected_days': retained, 'covered_days': covered,
                        'unknown_days': unknown if calendar_complete else None, 'retained_unknown_days': unknown,
                        'conflict_days': conflict_days, 'confirmed_dates': confirmed_dates,
                        'unknown_dates_sample': unknown_dates[:10], 'unknown_dates_truncated': len(unknown_dates) > 10,
                        'scope': '逐股同日原始收盘及有效成交量核验的来源收盘池；不是整池或年内次数',
                        'price_basis': 'none', 'today_excluded': True,
                        'snapshot_id': self.snapshot_id, 'supplement_job_id': self.supplement.get('job_id')}
            result[observed] = {COUNT_FIELD: lower if complete else None,
                                COUNT_FIELD + '_confirmed_count': lower,
                                COUNT_FIELD + '_lower_bound': lower,
                                COUNT_FIELD + '_upper_bound': upper,
                                COUNT_FIELD + '_coverage': coverage,
                                COUNT_FIELD + '_reason': reason}
        return result

    def batch_count_metrics(self, market, symbols, day, calendar, *, histories=None, include_daily=False):
        """Keep at most 256 securities' one-year quote dictionaries alive at once."""
        symbols = sorted(set(symbols)); result = {}
        if not symbols: return result
        start, _ = count_window(day)
        method = getattr(market, 'history_for_instruments', None)
        for offset in range(0, len(symbols), BATCH_SIZE):
            batch = symbols[offset:offset + BATCH_SIZE]
            selected = {symbol: histories.get(symbol, []) for symbol in batch} if histories is not None else method(batch, start=start, end=day) if callable(method) else {}
            for symbol in batch:
                quotes = quote_index(row for row in selected.get(symbol, []) if start <= str(row.get('date', row.get('trade_date', '')))[:10] <= day)
                metrics = self.count_series(symbol, [day], calendar, quotes)[day]
                if include_daily: metrics.update(self.metrics(symbol, day, calendar, quotes))
                result[symbol] = metrics
        return result


def batch_quote_indexes(market, symbols, day, calendar, histories=None):
    position = calendar.index(day) if day in calendar else None
    start = calendar[position - 1] if position is not None and position > 0 else day
    if histories is not None:
        return {symbol: quote_index([row for row in rows if row.get('date', row.get('trade_date')) in (start, day)]) for symbol, rows in histories.items()}
    method = getattr(market, 'history_for_instruments', None)
    if not callable(method):
        return {}
    return {symbol: quote_index(rows) for symbol, rows in method(symbols, start=start, end=day).items()}
