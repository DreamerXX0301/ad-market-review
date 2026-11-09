"""Disposable source I/O child. It does not open SQLite or publish anything."""
import json
import sys


def main():
    kind, *args = sys.argv[1:]
    if kind == 'flash':
        from app.adapters.news_flash import fetch_flash
        result = fetch_flash(*args)
    elif kind == 'calendar':
        from app.adapters.news_event_calendar import fetch_official_calendar
        result = fetch_official_calendar(*args)
    elif kind == 'trade_calendar':
        from app.adapters.providers import ProviderClient
        from app.core import digest
        from app.services.noon_collection import trading_days
        adapter = ProviderClient('fuyao', sys.stdin.read(1024))
        body = adapter.request('/api/a-share/calendar/trading-days', max_attempts=1, timeout=4)
        days = trading_days(body)
        if not 1 <= len(days) <= 4096:
            raise ValueError('交易日历未提供有界日期列表')
        result = {'state': 'ready', 'source_id': 'fuyao', 'source_sha256': digest(body), 'days': days}
    else:
        raise ValueError('未知的消息源采集类型')
    print(json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
