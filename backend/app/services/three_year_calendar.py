"""Fail-closed, source-derived historical calendar validation for initial sync.

Fuyao's official calendar endpoint only describes its recent one-year window.
Earlier sessions here are inferred from agreement between the stock bulk package
and four independent index histories; that agreement is not exchange provenance.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from math import isfinite
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

CHINA = ZoneInfo("Asia/Shanghai")
INDEX_CODES = ("000001.SH", "399001.SZ", "399006.SZ", "000688.SH")
VERSION = "source-calendar-three-years-v1"
CORE_INDEX_CODES = INDEX_CODES[:3]
STAR_BASE = '2019-12-31'
STAR_PUBLISHED = '2020-07-23'
STAR_SOURCE = 'https://www.sse.com.cn/market/sseindex/indexlist/indexdetails/indexmethods/c1/000688_000688hbook_CN.pdf'


def years_before(day: str, years: int = 3) -> str:
    """Subtract calendar years, keeping a leap-day target within February."""
    target = date.fromisoformat(day)
    try:
        return target.replace(year=target.year - years).isoformat()
    except ValueError:
        return target.replace(year=target.year - years, day=28).isoformat()


def _midnight(day: str) -> int:
    return int(datetime.combine(date.fromisoformat(day), time(), CHINA).timestamp() * 1000)


def _summary(days: list[str]) -> dict:
    return {"start": days[0] if days else None, "end": days[-1] if days else None,
            "count": len(days), "dates_sha256": sha256("\n".join(days).encode()).hexdigest()}


def index_dates(payload: dict, symbol: str, wanted: str, target: str,
                requested_begin: str | None = None) -> list[str]:
    """Validate every returned raw index row, including pre-window warmup rows."""
    data = payload.get("data")
    rows = data.get("item") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError(symbol + "指数历史为空或格式变化")
    if data.get("thscode") not in (None, symbol):
        raise ValueError(symbol + "指数响应证券代码与请求不符")
    if data.get("interval") not in (None, "1d"):
        raise ValueError(symbol + "指数响应不是日线")
    first = _midnight(requested_begin or wanted)
    last = _midnight(target) + 86400000 - 1
    observed: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(symbol + "指数历史行格式变化")
        ms = row.get("date_ms")
        if isinstance(ms, bool) or not isinstance(ms, (int, float)) or not isfinite(ms) or ms != int(ms) or not first <= ms <= last:
            raise ValueError(symbol + "指数日期无效或超出请求区间")
        day = datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(CHINA).date().isoformat()
        if row.get("thscode") not in (None, symbol):
            raise ValueError(symbol + "指数历史行证券代码与请求不符")
        if row.get("date") not in (None, day, day.replace("-", "")):
            raise ValueError(symbol + "指数显式日期与时间戳冲突")
        if day in observed:
            raise ValueError(symbol + "指数存在重复交易日")
        observed.add(day)
        values = [row.get(field) for field in
                  ("open_price", "high_price", "low_price", "close_price", "volume", "turnover")]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) for v in values):
            raise ValueError(symbol + "指数OHLC/量额缺失或无效")
        opening, high, low, close, volume, amount = values
        if min(opening, high, low, close) <= 0 or volume < 0 or amount < 0 or high < max(opening, low, close) or low > min(opening, high, close):
            raise ValueError(symbol + "指数OHLC/量额关系无效")
    result = sorted(day for day in observed if wanted <= day <= target)
    if not result or result[-1] != target:
        raise ValueError(symbol + "指数尚未更新到目标交易日")
    return result


def validate_three_year_calendar(bulk_path: str | Path, wanted: str, target: str,
                                 index_payloads: dict[str, dict],
                                 official_calendar: list[str]) -> tuple[list[str], dict]:
    """Return dates only when bulk, four indices and official overlap agree.

    Reads distinct dates with DuckDB rather than loading ten years of stock rows
    into Python. Individual security absences remain a separate coverage issue.
    """
    if set(index_payloads) != set(INDEX_CODES):
        raise ValueError("近三年日历必须同时核验四个指数历史")
    if wanted >= target:
        raise ValueError("近三年日历起止日期无效")
    con = duckdb.connect(":memory:")
    try:
        con.execute("SET memory_limit='256MB'")
        con.execute("SET threads=2")
        values = con.execute(
            "SELECT DISTINCT strftime(to_timestamp(date_ms/1000.0) AT TIME ZONE 'Asia/Shanghai','%Y-%m-%d') AS day "
            "FROM read_parquet(?) WHERE date_ms>=? AND date_ms<=? ORDER BY day",
            [str(bulk_path), _midnight(wanted), _midnight(target) + 86400000 - 1],
        ).fetchall()
    finally:
        con.close()
    days = [row[0] for row in values]
    if (len(days) < 650 or not days or days[-1] != target
            or (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days < 1000
            or not 0 <= (date.fromisoformat(days[0]) - date.fromisoformat(wanted)).days <= 14):
        raise ValueError("批量行情不足近三年：需至少650个交易日、跨度1000天且起日接近目标窗口")
    expected = set(days)
    indices = {}
    for symbol in INDEX_CODES:
        requested_begin = (date.fromisoformat(wanted) - timedelta(days=140)).isoformat() if symbol == "000001.SH" else wanted
        observed = index_dates(index_payloads[symbol], symbol, wanted, target, requested_begin)
        if set(observed) != expected:
            raise ValueError(symbol + "指数与近三年批量行情交易日期不一致，拒绝推导历史日历")
        indices[symbol] = {**_summary(observed), "matches_bulk": True}
    official = sorted({day for day in official_calendar if wanted <= day <= target})
    if (not official or official[-1] != target or len(official) < 200
            or (date.fromisoformat(official[-1]) - date.fromisoformat(official[0])).days < 330):
        raise ValueError("来源近期官方交易日历不足近一年，无法交叉核验")
    overlap = [day for day in days if official[0] <= day <= official[-1]]
    if set(overlap) != set(official):
        raise ValueError("近期官方交易日历与批量/指数历史重叠区间不一致，拒绝发布")
    provenance = {
        "version": VERSION, "basis": "source_derived_cross_checked", "source": "fuyao",
        "official_history": False, "wanted": wanted, **_summary(days),
        "bulk": {"kind": "daily-k", **_summary(days)}, "indices": indices,
        "official_recent": {"endpoint": "/api/a-share/calendar/trading-days", **_summary(official),
                            "matches_derived_overlap": True, "overlap_count": len(overlap)},
        "rules": {"minimum_sessions": 650, "minimum_span_days": 1000, "maximum_start_delay_days": 14,
                  "all_four_indices_equal_bulk": True, "official_overlap_equal": True},
        "scope": "四指数与全市场包共同出现日期推导；近期与官方接口一致，更早日期不是交易所官方历史日历证明；不证明逐股无缺报",
    }
    return days, provenance


def index_window_begin(symbol: str, wanted: str) -> str:
    """Do not request a history before STAR50's published base-date scope."""
    return max(wanted, STAR_BASE) if symbol == '000688.SH' else wanted


def validate_history_calendar(bulk_path, wanted, target, index_payloads, official_calendar, *, years, index_prewarm_days=140):
    """Validate 5/10-year stock dates with full old-index and dated STAR coverage.

    The historical stock calendar is inferred, not exchange-official. Three
    long-lived indices must cover the whole window. STAR is independently
    checked over its available contiguous suffix; missing pre-publication
    history remains visible and cannot establish older market sessions.
    The legacy 3-year validator is unchanged for frozen checkpoint compatibility.
    """
    if type(index_prewarm_days) is not int or index_prewarm_days not in (0, 140):
        raise ValueError('指数预热必须使用明确的旧140日或新窗口内0日方案')
    if years == 3 and index_prewarm_days != 140:
        raise ValueError('旧三年冻结方案保留原指数预热签名')
    if type(years) is not int or years not in (3, 5, 10):
        raise ValueError('历史日历仅支持明确的三年、五年或十年窗口')
    if years == 3:
        return validate_three_year_calendar(bulk_path, wanted, target, index_payloads, official_calendar)
    if set(index_payloads) != set(INDEX_CODES) or wanted >= target:
        raise ValueError('历史日历必须提供四指数并使用有效起止日')
    con = duckdb.connect(':memory:')
    try:
        con.execute("SET memory_limit='256MB'"); con.execute('SET threads=2')
        values = con.execute(
            "SELECT DISTINCT strftime(to_timestamp(date_ms/1000.0) AT TIME ZONE 'Asia/Shanghai','%Y-%m-%d') AS day "
            'FROM read_parquet(?) WHERE date_ms>=? AND date_ms<=? ORDER BY day',
            [str(bulk_path), _midnight(wanted), _midnight(target)+86400000-1]).fetchall()
    finally: con.close()
    days = [row[0] for row in values]
    minimum_sessions, minimum_span = (1100, 1750) if years == 5 else (2200, 3500)
    if (not days or len(days) < minimum_sessions or days[-1] != target
            or (date.fromisoformat(days[-1])-date.fromisoformat(days[0])).days < minimum_span
            or not 0 <= (date.fromisoformat(days[0])-date.fromisoformat(wanted)).days <= 14):
        raise ValueError(f'批量行情不足近{years}年：交易日数量、跨度或起止边界不符')
    indices = {}
    for symbol in INDEX_CODES:
        start = index_window_begin(symbol, wanted)
        requested_begin = (date.fromisoformat(start)-timedelta(days=index_prewarm_days)).isoformat() if symbol == '000001.SH' else start
        observed = index_dates(index_payloads[symbol], symbol, start, target, requested_begin)
        if symbol in CORE_INDEX_CODES or start == wanted:
            expected = days
        else:
            # Some vendors begin STAR history at publication rather than its
            # base. Accept that explicit short suffix, not an unexplained gap.
            latest_start = (date.fromisoformat(STAR_PUBLISHED)+timedelta(days=14)).isoformat()
            if observed[0] > latest_start:
                raise ValueError('科创50历史晚于公开发布边界，不能以任意短后缀冒充已核验')
            expected = [day for day in days if day >= observed[0]]
        if observed != expected:
            raise ValueError(symbol+'指数与批量行情对应日期不一致，拒绝推导历史日历')
        indices[symbol] = {**_summary(observed), 'matches_bulk': True,
                           'full_window': observed == days, 'requested_start': start,
                           'prehistory_not_covered': [wanted, observed[0]] if observed[0] > days[0] else None}
    official = sorted(set(day for day in official_calendar if wanted <= day <= target))
    if (not official or official[-1] != target or len(official) < 200
            or (date.fromisoformat(official[-1])-date.fromisoformat(official[0])).days < 330):
        raise ValueError('来源近期官方交易日历不足近一年，无法交叉核验')
    overlap = [day for day in days if official[0] <= day <= official[-1]]
    if overlap != official: raise ValueError('近期官方日历与历史来源日期重叠区间不一致')
    return days, {'version': 'source-calendar-history-window-v2' if index_prewarm_days == 0 else 'source-calendar-history-window-v1', 'basis': 'source_derived_cross_checked',
                  'source': 'fuyao', 'official_history': False, 'years': years, 'wanted': wanted, **_summary(days),
                  'bulk': {'kind': 'daily-k', **_summary(days)}, 'indices': indices,
                  'official_recent': {'endpoint': '/api/a-share/calendar/trading-days', **_summary(official),
                                      'matches_derived_overlap': True, 'overlap_count': len(overlap)},
                  'rules': {'minimum_sessions': minimum_sessions, 'minimum_span_days': minimum_span,
                            'maximum_start_delay_days': 14, 'required_full_window_indices': list(CORE_INDEX_CODES),
                            **({'index_prewarm_days': 0, 'index_request_plan': 'bounded-window-v2'} if index_prewarm_days == 0 else {}),
                            'star_suffix_equal_bulk': True, 'official_overlap_equal': True},
                  'star_scope': {'base_date': STAR_BASE, 'published_date': STAR_PUBLISHED, 'source': STAR_SOURCE,
                                 'notice': '科创50没有2016年历史；只核验其真实返回后缀，不用它证明更早日历。'},
                  'scope': '全市场包与长历史指数日期交叉核验；科创50按可用后缀核验，近期与官方接口一致；不证明历史官方日历或逐股无缺报'}
