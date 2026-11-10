"""Frozen rolling-year scope for A-share source pools."""
from datetime import date, timedelta

VERSION = 'limit-pools-rolling-year-v1'


def validate(version):
    if version is not None and version != VERSION:
        raise ValueError('未知涨跌停来源池范围版本')
    return version


def rolling_start(day):
    anchor = date.fromisoformat(day)
    try:
        anniversary = anchor.replace(year=anchor.year - 1)
    except ValueError:
        anniversary = anchor.replace(year=anchor.year - 1, day=28)
    return (anniversary + timedelta(days=1)).isoformat()


def rolling_days(calendar, day):
    start = rolling_start(day)
    return [value for value in calendar if start <= value <= day]


def unchanged_pool_dates(coverage, previous_snapshot, dates):
    proof = coverage.get('limit_pool_quote_delta', {})
    if (not previous_snapshot or proof.get('previous_snapshot') != previous_snapshot
            or proof.get('same_stock_directory') is not True
            or not isinstance(proof.get('changed_dates'), list)):
        return set()
    begin, end = proof.get('previous_begin'), proof.get('previous_end')
    if not isinstance(begin, str) or not isinstance(end, str):
        return set()
    changed = set(proof['changed_dates'])
    return {day for day in dates if begin <= day <= end and day not in changed}
