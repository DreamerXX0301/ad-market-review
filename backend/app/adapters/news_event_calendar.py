"""Read the National Bureau of Statistics' official release plans."""

from datetime import date, timedelta
from hashlib import sha256
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit

import httpx


INDEX = "https://www.stats.gov.cn/xxgk/sjfb/fbrcb/"
SOURCE = "nbs-release-calendar"
SOURCE_LABEL = "国家统计局"
_YEAR_TITLE = re.compile(r"(\d{4})年国家统计局主要统计信息发布日程表")
_MONTHS = ["序号", "内容"] + [f"{month}月" for month in range(1, 13)]
_DAY = re.compile(r"(\d{1,2})\s*/\s*([一二三四五六日])(?:\s*注\s*\d+)?")
_CLOCK = re.compile(r"(\d{1,2}):([0-5]\d)")
_WEEKDAYS = "一二三四五六日"
_MAX_BYTES = 768 * 1024


class _ScheduleMarkup(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = {}
        self.rows = []
        self._link = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._link = (dict(attrs).get("href"), [])
        elif tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = (dict(attrs).get("rowspan", "1"), [])
        elif tag in {"p", "br", "div"} and self._cell is not None:
            self._cell[1].append(" ")

    def handle_data(self, text):
        if self._link is not None:
            self._link[1].append(text)
        if self._cell is not None:
            self._cell[1].append(text)

    def handle_endtag(self, tag):
        if tag == "a" and self._link is not None:
            href, chunks = self._link
            title = re.sub(r"\s+", "", "".join(chunks))
            match = _YEAR_TITLE.fullmatch(title)
            if match and href:
                self.links.setdefault(int(match[1]), set()).add(href)
            self._link = None
        elif tag in {"td", "th"} and self._cell is not None and self._row is not None:
            rowspan, chunks = self._cell
            self._row.append((" ".join("".join(chunks).split()), rowspan))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _download(client, url):
    with client.stream("GET", url) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > _MAX_BYTES:
                raise ValueError("国家统计局日程响应超过大小上限")
    return body.decode("utf-8-sig")


def _release_url(href):
    url = urljoin(INDEX, href)
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.stats.gov.cn"
        or not re.fullmatch(r"/xxgk/sjfb/fbrcb/\d{6}/t\d{8}_\d+\.html", parsed.path)
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("国家统计局日程链接不在核验范围")
    return url


def _release_days(text, year, month):
    if not text or re.fullmatch(r"[.…·\s\-]+", text):
        return []
    days = []
    cursor = 0
    for match in _DAY.finditer(text):
        if text[cursor:match.start()].strip():
            raise ValueError("国家统计局日程日期格式变化")
        day = date(year, month, int(match[1]))
        if _WEEKDAYS[day.weekday()] != match[2] or day in days:
            raise ValueError("国家统计局日程日期与星期不符或重复")
        days.append(day)
        cursor = match.end()
    if not days or text[cursor:].strip():
        raise ValueError("国家统计局日程日期未能核验")
    return days


def parse_release_plan(html, year, url):
    if f"{year}年国家统计局主要统计信息发布日程表" not in html:
        raise ValueError("国家统计局日程年份不符")
    markup = _ScheduleMarkup()
    markup.feed(html)
    headers = [index for index, row in enumerate(markup.rows) if [cell[0] for cell in row] == _MONTHS]
    if len(headers) != 1:
        raise ValueError("国家统计局日程月份表头未能唯一核验")
    rows = markup.rows[headers[0] + 1:]
    events = []
    expected_row = 1
    for index, row in enumerate(rows):
        if len(row) == 1 and row[0][0].startswith("注"):
            break
        if not row or not row[0][0].isdigit():
            continue
        if len(row) != 14 or int(row[0][0]) != expected_row:
            raise ValueError("国家统计局日程行序号或列数不符")
        expected_row += 1
        title = row[1][0]
        if not title or len(title) > 120:
            raise ValueError("国家统计局日程项目名称异常")
        series = sha256(title.encode()).hexdigest()[:12]
        time_columns = [month for month, (_, rowspan) in enumerate(row[2:], 1) if rowspan == "1"]
        time_row = rows[index + 1] if index + 1 < len(rows) else []
        times = {}
        if len(time_row) == len(time_columns):
            for month, (text, _) in zip(time_columns, time_row):
                match = _CLOCK.fullmatch(text)
                if match and int(match[1]) < 24:
                    times[month] = f"{int(match[1]):02d}:{match[2]}"
        for month, (text, _) in enumerate(row[2:], 1):
            days = _release_days(text, year, month)
            for occurrence, day in enumerate(days, 1):
                summary = "国家统计局年度发布计划，具体安排以最新公告为准。"
                if month in times:
                    summary = f"计划发布时间 {times[month]}（北京时间）。{summary}"
                events.append({
                    "id": f"nbs-{year}-{month:02d}-{series}-{occurrence}",
                    "title": title,
                    "source": SOURCE_LABEL,
                    "url": url,
                    "event_date": day.isoformat(),
                    "status": "tentative",
                    "summary": summary,
                })
    if len(events) < 24 or len({event["id"] for event in events}) != len(events):
        raise ValueError("国家统计局年度日程为空、异常偏少或重复")
    return events


def fetch_official_calendar(day, *, transport=None):
    observed = date.fromisoformat(day)
    upcoming = observed + timedelta(days=29)
    with httpx.Client(
        timeout=httpx.Timeout(2.0),
        follow_redirects=False,
        headers={"Accept": "text/html", "User-Agent": "ad-market-review/official-calendar"},
        transport=transport,
    ) as client:
        index = _ScheduleMarkup()
        index.feed(_download(client, INDEX))
        events = []
        last_year = observed.year
        for year in range(observed.year, upcoming.year + 1):
            links = index.links.get(year, set())
            if not links and year != observed.year:
                break
            if len(links) != 1:
                raise ValueError("国家统计局当前年度日程链接未能唯一核验")
            url = _release_url(next(iter(links)))
            events.extend(parse_release_plan(_download(client, url), year, url))
            last_year = year
    return {
        "source": SOURCE,
        "source_url": INDEX,
        "events": events,
        "coverage_start": date(observed.year, 1, 1).isoformat(),
        "coverage_end": date(last_year, 12, 31).isoformat(),
    }
