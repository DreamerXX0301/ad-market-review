"""Pure comparisons of frozen noon index amounts; no market or storage reader."""
from collections import Counter
from datetime import date, datetime, time
import math
import re
from zoneinfo import ZoneInfo

CHINA = ZoneInfo('Asia/Shanghai')
CODE = re.compile(r'\d{6}\.(?:SH|SZ|BJ)\Z')


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(CHINA) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _window(value, day):
    start = _timestamp(value.get('collected_start_at'))
    end = _timestamp(value.get('collected_end_at'))
    return (start, end) if (start and end and start <= end and start.date().isoformat() == day
                           and end.date().isoformat() == day and time(11, 30) <= start.time()
                           and end.time() < time(13)) else None


def index_source(payload, row):
    """Use explicit per-row or received index-domain provenance, never an inferred vendor."""
    own = row.get('source')
    own = own if isinstance(own, str) and own.strip() else None
    domain = [entry for entry in _rows(payload.get('sources'))
              if entry.get('name') == 'indices' or entry.get('domain') == 'indices']
    if domain:
        if any(entry.get('state') != 'received' or not isinstance(entry.get('source'), str)
               or not entry['source'].strip()
               or entry.get('endpoint', '/api/a-share-index/prices/snapshot') != '/api/a-share-index/prices/snapshot'
               for entry in domain):
            return None
        values = {entry['source'] for entry in domain}
        if len(values) != 1:
            return None
        reported = next(iter(values))
        return reported if own in (None, reported) else None
    reported = payload.get('source')
    reported = reported if isinstance(reported, str) and reported.strip() else None
    return own if own is not None and reported in (None, own) else reported if own is None else None


def build_index_comparisons(payload):
    """Compare one source/code/CNY amount with the exact prior retained noon.

    A matching individual quote clock is required for '昨日同期'. Without that
    proof the two valid lunch captures are only '较昨日午评' snapshots. Neither
    branch is yesterday's full-session amount or a five-day volume ratio.
    """
    if not isinstance(payload, dict):
        return {}
    rows = [row for row in _rows(payload.get('indices'))
            if isinstance(row.get('instrument_id'), str) and CODE.fullmatch(row['instrument_id'])]
    counts = Counter(row['instrument_id'] for row in rows)
    previous = payload.get('previous_review')
    previous = previous if isinstance(previous, dict) else {}
    baseline = previous.get('previous_noon')
    baseline = baseline if isinstance(baseline, dict) else {}
    past_rows = _rows(baseline.get('indices'))
    past_counts = Counter(row.get('instrument_id') for row in past_rows if isinstance(row.get('instrument_id'), str))
    indexed = {row['instrument_id']: row for row in past_rows
               if isinstance(row.get('instrument_id'), str) and past_counts[row['instrument_id']] == 1}
    day, prior = payload.get('date'), baseline.get('date')
    calendar = payload.get('calendar')
    calendar = calendar if isinstance(calendar, dict) else {}
    dates_valid = (_day(day) and _day(prior) and prior < day and prior == previous.get('date')
                   and calendar.get('verified') is True and calendar.get('previous_day') == prior
                   and baseline.get('calendar_verified') is True)
    current_window = _window(payload, day) if _day(day) else None
    previous_window = _window(baseline, prior) if _day(prior) else None
    output = {}
    for row in rows:
        code = row['instrument_id']; old = indexed.get(code, {})
        current_amount = _number(row.get('amount')); previous_amount = _number(old.get('amount'))
        item = {'state': 'unavailable', 'label': '昨日午评对照不可用',
                'previous_amount': previous_amount if previous_amount is not None and previous_amount >= 0 else None,
                'current_amount': current_amount if current_amount is not None and current_amount >= 0 else None,
                'ratio': None, 'change_pct': None, 'direction': None,
                'reason': '未冻结严格前一交易日可核验的午评指数成交额。',
                'scope': '同来源、同指数代码、人民币累计成交额；不和昨日全天比较，不是专业五日量比。'}
        output[code] = item
        if baseline.get('state') != 'available':
            item['reason'] = baseline.get('reason') or item['reason']
        elif not dates_valid:
            item['reason'] = '午评日期或严格前一市场交易日证据不一致，未与更早或其他日期比较。'
        elif counts[code] != 1 or past_counts[code] > 1:
            item['reason'] = '本次或昨日午评存在重复指数代码，无法确定唯一累计成交额。'
        elif not old:
            item['reason'] = '昨日午评未冻结相同指数代码，未按名称或其他指数替代。'
        elif any(key in row and row[key] != day for key in ('date', 'trade_date', 'quote_date')):
            item['reason'] = '本次指数行显式日期与午评日期冲突，未计算金额之比。'
        elif not current_window or not previous_window:
            item['reason'] = '两份快照的同日午休采集区间未通过核验，不冒充昨日午评或同期。'
        elif row.get('currency') != 'CNY' or old.get('currency') != 'CNY':
            item['reason'] = '指数成交额币种未明确为CNY或币种不一致，未计算金额之比。'
        elif not index_source(payload, row) or index_source(payload, row) != index_source(baseline, old):
            item['reason'] = '两份指数成交额来源缺失、冲突或不同，未跨源计算金额之比。'
        elif current_amount is None or current_amount < 0 or previous_amount is None or previous_amount <= 0:
            item['reason'] = '本次累计成交额缺失/无效或昨日基数不大于零，未补零或计算无限比值。'
        else:
            current_at, previous_at = row.get('quote_at'), old.get('quote_at')
            if current_at is not None and previous_at is not None:
                actual, prior_actual = _timestamp(current_at), _timestamp(previous_at)
                if (not actual or not prior_actual or actual.date().isoformat() != day
                        or prior_actual.date().isoformat() != prior or actual > current_window[1]
                        or prior_actual > previous_window[1] or actual.time() != prior_actual.time()):
                    item['reason'] = '逐条指数报价时间未能证明前一交易日相同时刻，未标为昨日同期。'
                    continue
                state, label = 'matched_period', '昨日同期'
                reason = '逐条指数报价提供了各自交易日相同北京时间的累计成交额。'
                item['scope'] += ' 两日逐条报价时刻均为' + actual.strftime('%H:%M:%S') + '。'
            else:
                # A present but invalid/conflicting timestamp is different from a missing one.
                known = [(current_at, day, current_window), (previous_at, prior, previous_window)]
                if any(value is not None and (not _timestamp(value) or _timestamp(value).date().isoformat() != expected
                                              or _timestamp(value) > window[1]) for value, expected, window in known):
                    item['reason'] = '已提供的逐条指数报价时间无效或与采集日期冲突，未当成单纯缺失。'
                    continue
                state, label = 'snapshot_comparison', '较昨日午评'
                reason = '两份均为各自交易日午休采集快照；缺少成对逐条行情时间，只比较累计成交额增减。'
            ratio = current_amount / previous_amount
            change = (ratio - 1) * 100
            if not math.isfinite(ratio) or not math.isfinite(change):
                item['reason'] = '金额比值超出有限数值范围，未输出无穷值。'
                continue
            item.update(state=state, label=label, ratio=ratio, change_pct=change,
                        direction='increase' if current_amount > previous_amount else 'decrease'
                        if current_amount < previous_amount else 'flat', reason=reason)
    return output
