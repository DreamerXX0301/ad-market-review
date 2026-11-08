from datetime import date

import httpx
import pytest

from app.adapters.news_event_calendar import fetch_official_calendar, parse_release_plan


def _release_plan(year, *, wrong_weekday=False):
    columns = ["序号", "内容"] + [f"{month}月" for month in range(1, 13)]
    header = "".join(f"<th>{column}</th>" for column in columns)
    rows = []
    for number in range(1, 4):
        days = []
        for month in range(1, 13):
            weekday = "一二三四五六日"[date(year, month, number).weekday()]
            if wrong_weekday and number == 1 and month == 1:
                weekday = "一" if weekday != "一" else "二"
            item = f"<p>{number}/{weekday}</p>"
            if number == 1 and month == 3:
                second_weekday = "一二三四五六日"[date(year, month, 31).weekday()]
                item = f"<p>{number}/{weekday}<sup>注5</sup></p><p>31/{second_weekday}</p>"
            days.append(f"<td>{item}</td>")
        rows.append(f"<tr><td rowspan='2'>{number}</td><td rowspan='2'>测试发布{number}</td>{''.join(days)}</tr>")
        rows.append("<tr>" + "<td>9:30</td>" * 12 + "</tr>")
    return f"<title>{year}年国家统计局主要统计信息发布日程表</title><table><tr>{header}</tr>{''.join(rows)}</table>"


def test_schedule_preserves_two_dates_in_one_month_cell():
    events = parse_release_plan(_release_plan(2026), 2026, "https://www.stats.gov.cn/")
    march = [item for item in events if item["title"] == "测试发布1" and item["event_date"].startswith("2026-03")]
    assert [item["event_date"] for item in march] == ["2026-03-01", "2026-03-31"]
    assert len({item["id"] for item in march}) == 2
    assert all(item["status"] == "tentative" and "09:30" in item["summary"] for item in march)
    with pytest.raises(ValueError, match="星期"):
        parse_release_plan(_release_plan(2026, wrong_weekday=True), 2026, "https://www.stats.gov.cn/")


def test_schedule_leaves_year_boundary_unverified_until_new_plan_exists():
    def fetch(request):
        if request.url.path.endswith("/"):
            return httpx.Response(200, text='<a href="./202512/t20251224_1234.html">2026年国家统计局主要统计信息发布日程表</a>')
        assert request.url.path == "/xxgk/sjfb/fbrcb/202512/t20251224_1234.html"
        return httpx.Response(200, text=_release_plan(2026))

    result = fetch_official_calendar("2026-12-20", transport=httpx.MockTransport(fetch))
    assert result["coverage_end"] == "2026-12-31"
    assert result["source"] == "nbs-release-calendar"
    assert len(result["events"]) == 37


def test_schedule_rejects_unexpected_document_host_before_request():
    def fetch(request):
        assert request.url.host == "www.stats.gov.cn"
        return httpx.Response(200, text='<a href="https://example.com/">2026年国家统计局主要统计信息发布日程表</a>')

    with pytest.raises(ValueError, match="链接不在核验范围"):
        fetch_official_calendar("2026-11-09", transport=httpx.MockTransport(fetch))
