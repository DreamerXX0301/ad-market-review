"""Attach verified closing-limit events without inferring price-band regimes.

The chart's displayed price is never used to validate an event. Events are
cross-checked against the same date's original daily close; weekly/monthly charts
only aggregate exact daily events and never label an entire period limit-up/down.
"""
from __future__ import annotations

from datetime import date
import math
import re

from app.services.market_supplement import load_supplement

KINDS = {'up': ('limitup', 'limit_up', '收盘涨停'), 'down': ('limitdown', 'limit_down', '收盘跌停')}
SCOPE = '已核验来源收盘池；false仅指该完整来源池明确未入池，不是交易所全A涨跌停历史全集'


def _number(value):
    return float(value) if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) else None


def _day(value):
    if not isinstance(value, str): return None
    try: return date.fromisoformat(value[:10]).isoformat()
    except ValueError: return None


def _matches(row, symbol):
    formal = row.get('instrument_id') or row.get('thscode')
    explicit = row.get('ticker') or row.get('code')
    if isinstance(formal, str):
        if formal.upper() == symbol: return 'exact'
        if formal[:6] == symbol[:6] or explicit == symbol[:6]: return 'identity_conflict'
        return None
    # Accepted source rows may retain only an explicit ticker. For the one
    # requested stock, its exact six-digit code is sufficient; never use names.
    if isinstance(explicit, str) and re.fullmatch(r'\d{6}', explicit) and explicit == symbol[:6]: return 'exact'
    return None


def _quarantined(record, symbol):
    if symbol[:6] in record.get('rejected_codes', []): return True
    for row in record.get('quarantine', []):
        if isinstance(row, dict) and _matches(row, symbol): return True
    return False


def _evidence(record, symbol, day, raw_close, *, source, path, kind, allow_absence=True, granular=False):
    base = {'source': source, 'path': path, 'scope': record.get('scope') or SCOPE}
    if raw_close is None: return {**base, 'state': None, 'status': 'unknown', 'reason': '该日原始收盘缺失，无法核验收盘涨跌停'}
    if record.get('date') is not None and _day(record.get('date')) != day:
        return {**base, 'state': None, 'status': 'unknown', 'reason': '来源池日期与该交易日不符'}
    declared_kind = record.get('pool_kind')
    if declared_kind in ('limitup', 'limitdown') and declared_kind != KINDS[kind][0]:
        return {**base, 'state': None, 'status': 'unknown', 'reason': '来源池事件类型不符'}
    items = record.get('items')
    if not isinstance(items, list): return {**base, 'state': None, 'status': 'unknown', 'reason': '未留存可验证的来源池'}
    if not items: return {**base, 'state': None, 'status': 'unknown', 'reason': '来源池为空，无法区分不支持该日与真实零事件'}
    matched = [(row, _matches(row, symbol)) for row in items if isinstance(row, dict) and _matches(row, symbol)]
    if matched:
        for row, identity in matched:
            if identity != 'exact':
                return {**base, 'state': None, 'status': 'conflict', 'reason': '来源证券标识冲突，未按名称或涨幅猜测'}
            if row.get('date') is not None and _day(row.get('date')) != day:
                return {**base, 'state': None, 'status': 'conflict', 'reason': '事件日期与原始报价日期不符'}
            prices = [_number(row[name]) for name in ('last_price', 'close') if name in row]
            if not prices or any(price is None for price in prices):
                return {**base, 'state': None, 'status': 'unknown', 'reason': '收盘事件缺少可核验原始价格'}
            if any(abs(price - raw_close) > .011 for price in prices):
                return {**base, 'state': None, 'status': 'conflict', 'reason': '来源事件价格与该日未复权收盘冲突'}
        return {**base, 'state': True, 'status': 'confirmed', 'reason': None}
    if _quarantined(record, symbol):
        return {**base, 'state': None, 'status': 'unknown', 'reason': '该证券事件被隔离，不能当作未入池'}
    if allow_absence:
        raw_codes = record.get('raw_codes')
        if granular and record.get('pagination_complete') is True and record.get('ambiguous_code_rows') == 0 and isinstance(raw_codes, list) and all(isinstance(code, str) and re.fullmatch(r'\d{6}', code) for code in raw_codes) and len(set(raw_codes)) == len(raw_codes):
            if symbol[:6] not in raw_codes:
                return {**base, 'state': False, 'status': 'source_absent', 'reason': '完整分页及明确代码集合证明该证券未入来源池'}
            return {**base, 'state': None, 'status': 'unknown', 'reason': '原始池存在该代码但没有可接受事件，不能当作未入池'}
        if record.get('complete') is True and not record.get('quarantine'):
            return {**base, 'state': False, 'status': 'source_absent', 'reason': '完整已核验来源池中明确未入池'}
    return {**base, 'state': None, 'status': 'unknown', 'reason': '该日期来源池覆盖不完整；公开补充池缺席不证明全A无事件'}


def _combine(proofs):
    conflict = any(proof['status'] == 'conflict' for proof in proofs)
    yes = [proof for proof in proofs if proof['state'] is True]
    no = [proof for proof in proofs if proof['state'] is False]
    if conflict or (yes and no):
        return {'state': None, 'status': 'conflict', 'reason': '来源收盘事件或价格证据冲突，保持未知', 'evidence': proofs}
    if yes: return {'state': True, 'status': 'confirmed', 'reason': None, 'evidence': proofs}
    if no: return {'state': False, 'status': 'source_absent', 'reason': no[0]['reason'], 'evidence': proofs}
    return {'state': None, 'status': 'unknown', 'reason': next((proof['reason'] for proof in proofs if proof.get('reason')), '该日期未留存完整可核验的收盘来源池'), 'evidence': proofs}


def _week(day):
    return date.fromisoformat(day).isocalendar()[:2]


def enrich_chart_limits(market, chart, supplement=None):
    result = dict(chart); result['bars'] = [dict(bar) for bar in chart.get('bars', [])]
    instrument = chart.get('instrument', {}); symbol = str(instrument.get('instrument_id') or instrument.get('thscode') or '').upper()
    bars = result['bars']; until = _day(chart.get('as_of')) or (bars[-1]['date'] if bars else None)
    since = bars[0]['date'] if bars else None
    if instrument.get('kind') == 'index':
        for bar in bars: bar.update(limit_up_state=None, limit_down_state=None, limit_events=[], limit_event_status={'up': 'not_applicable', 'down': 'not_applicable'})
        result.update(limit_events=[], limit_event_coverage={'applicable': False, 'since': since, 'until': until, 'reason': '指数不标记个股收盘涨跌停', 'scope': SCOPE, 'notice': '指数不适用个股收盘涨跌停事件标记。'})
        return result
    if not bars or not symbol or not until:
        result.update(limit_events=[], limit_event_coverage={'applicable': True, 'since': since, 'until': until, 'reason': '没有可核验日线', 'scope': SCOPE})
        return result
    raw = market.bars(symbol, as_of=until)
    raw_prices = {}; duplicate_dates = set()
    for row in raw:
        day = _day(row.get('date'))
        if not day or day > until: continue
        value = _number(row.get('close'))
        if value is not None and value <= 0: value = None
        if day in raw_prices and raw_prices[day] != value: duplicate_dates.add(day)
        raw_prices[day] = value
    for day in duplicate_dates: raw_prices[day] = None
    start = min(raw_prices) if raw_prices else since
    calendar = sorted({day for day in market.calendar() if start <= day <= until})
    if not calendar: calendar = sorted(raw_prices)
    reference = market.reference()
    supplement_warning = None
    if supplement is None:
        try: supplement = load_supplement(chart.get('snapshot_id'))
        except (ValueError, OSError):
            supplement = {}; supplement_warning = '收盘事件补充索引未通过读取；仅使用基础已发布来源'
    if supplement.get('failure_reason'):
        supplement_warning = '收盘事件补充不可用：' + str(supplement['failure_reason']) + '；仅使用基础已发布来源'
    if supplement.get('snapshot_id') and chart.get('snapshot_id') and supplement['snapshot_id'] != chart['snapshot_id']:
        supplement = {}; supplement_warning = '收盘事件补充属于其他行情代际，未混入当前图表'
    daily = {}; events = []
    for day in calendar:
        states = {}
        history = reference.get('pool_history', {}).get(day, {})
        for kind, (pool, event_kind, label) in KINDS.items():
            proofs = []
            if pool in history:
                record = {'items': history[pool], 'complete': history.get('coverage', {}).get(pool, {}).get('complete', False), 'quarantine': history.get('quarantine', {}).get(pool, []), 'date': day}
                proofs.append(_evidence(record, symbol, day, raw_prices.get(day), source='fuyao', path='reference.pool_history.' + day + '.' + pool, kind=kind))
            if reference.get('pool_trade_date') == day and pool in reference:
                record = {'items': reference[pool], 'complete': reference.get('pool_coverage', {}).get(pool, {}).get('complete', False), 'quarantine': reference.get('pool_quarantine', {}).get(pool, []), 'date': day}
                proofs.append(_evidence(record, symbol, day, raw_prices.get(day), source='fuyao', path='reference.' + pool, kind=kind))
            annual_key = 'annual_pools' if kind == 'up' else 'annual_down_pools'
            annual = supplement.get(annual_key, {}).get(day)
            if annual is not None:
                proofs.append(_evidence(annual, symbol, day, raw_prices.get(day), source=annual.get('source') or 'fuyao', path=annual_key + '.' + day, kind=kind, granular=True))
            public = supplement.get('public_pools', {}).get(day) if kind == 'up' else None
            if public is not None:
                proofs.append(_evidence(public, symbol, day, raw_prices.get(day), source=public.get('source') or 'eastmoney-public', path='public_pools.' + day, kind=kind, allow_absence=False))
            states[kind] = _combine(proofs)
        if any(states[kind]['status'] == 'conflict' for kind in KINDS):
            for kind in KINDS: states[kind].update(state=None, status='conflict', reason='同一证券当日存在来源事件或原价冲突，涨跌停均保持未知')
        elif states['up']['state'] is True and states['down']['state'] is True:
            for kind in KINDS: states[kind].update(state=None, status='conflict', reason='同一收盘日同时出现涨停和跌停事件，保持未知')
        day_events = []
        for kind, (_, event_kind, label) in KINDS.items():
            if states[kind]['state'] is not True: continue
            positives = [proof for proof in states[kind]['evidence'] if proof['state'] is True]
            sources = list(dict.fromkeys(proof['source'] for proof in positives))
            event = {'date': day, 'kind': event_kind, 'label': label, 'source': ' + '.join(sources), 'sources': sources, 'basis': 'verified-source-close-pool', 'raw_close': raw_prices[day], 'chart_price': None, 'bar_date': None, 'evidence': positives}
            day_events.append(event); events.append(event)
        daily[day] = {'up': states['up'], 'down': states['down'], 'events': day_events}
    period = chart.get('period', 'D')
    if period == 'D':
        for bar in bars:
            day = bar['date']; state = daily.get(day, {})
            attached = []
            for event in state.get('events', []):
                event.update(chart_price=_number(bar.get('close')), bar_date=day)
                attached.append(dict(event))
            bar.update(limit_up_state=state.get('up', {}).get('state'), limit_down_state=state.get('down', {}).get('state'), limit_events=attached, limit_event_status={kind: state.get(kind, {}).get('status', 'unknown') for kind in KINDS}, limit_event_reason={kind: state.get(kind, {}).get('reason') for kind in KINDS})
    else:
        monthly = period == 'M'
        period_key = (lambda day: day[:7]) if monthly else _week
        status = 'monthly_daily_summary' if monthly else 'weekly_daily_summary'
        scope = 'monthly_daily_events' if monthly else 'weekly_daily_events'
        grouped = {}
        for day, state in daily.items(): grouped.setdefault(period_key(day), []).append((day, state))
        for bar in bars:
            members = grouped.get(period_key(bar['date']), []); attached = []
            for _, state in members:
                for event in state['events']:
                    event.update(bar_date=bar['date']); attached.append(dict(event))
            bar.update(limit_up_state=None, limit_down_state=None, limit_events=attached, limit_event_status={'up': status, 'down': status}, limit_event_summary={'scope': scope, 'up_count': sum(state['up']['state'] is True for _, state in members), 'down_count': sum(state['down']['state'] is True for _, state in members), 'unknown_up_days': sum(state['up']['state'] is None for _, state in members), 'unknown_down_days': sum(state['down']['state'] is None for _, state in members), 'observed_trade_days': len(members)})
    coverage = {'applicable': True, 'since': calendar[0] if calendar else since, 'until': until, 'expected_trade_days': len(calendar), 'raw_quote_days': sum(raw_prices.get(day) is not None for day in calendar), 'scope': SCOPE, 'notice': SCOPE + '；未知或冲突不视为普通交易日，空池不证明零事件。', 'price_validation_basis': 'same-date-unadjusted-daily-close', 'historical_identity_inferred': False, 'future_dates_excluded': True, 'supplement_job_id': supplement.get('job_id')}
    for kind in KINDS:
        stats = {status + '_days': sum(state[kind]['status'] == status for state in daily.values()) for status in ('confirmed', 'source_absent', 'unknown', 'conflict')}
        stats['covered_trade_dates'] = [day for day, state in daily.items() if state[kind]['state'] is not None]
        coverage[kind] = stats
    result.update(limit_events=[dict(event) for event in events if event['bar_date'] is not None], limit_event_coverage=coverage)
    if supplement_warning: result['warnings'] = list(result.get('warnings', [])) + [supplement_warning]
    return result
