"""Independent reminder sources share bounded views without rewriting drawings."""
import pytest
from fastapi import HTTPException

from app.services import market_alerts as alerts


@pytest.fixture(autouse=True)
def temporary_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR'):
        monkeypatch.delenv(key, raising=False)


def event(identifier, day='2026-11-11', stamp=1, symbol='000678.SZ'):
    return {'event_id': identifier, 'trade_date': day, 'created_at_ms': stamp,
            'instrument_id': symbol, 'title': identifier}


def listing(items, *, total, today, history, rules, check=None):
    return {'items': items, 'total': total, 'today_total': today, 'history_total': history,
            'rules_total': rules, 'business_date': '2026-11-11', 'as_of': '2026-11-11',
            'scope': 'all', 'latest_check': check, 'unknowns': []}


def test_list_union_sorts_before_truncating_without_mutating_manual(monkeypatch):
    manual_event = event('drawn-old', stamp=2)
    manual = listing([manual_event], total=7, today=3, history=4, rules=2,
                     check={'job_id': 'manual-check'})
    automatic = listing([event('auto-new', stamp=3)], total=8, today=5, history=3, rules=4,
                        check={'job_id': 'auto-check'})
    calls = []
    monkeypatch.setattr(alerts.line_alerts, 'list_alerts', lambda *args, **kwargs: (calls.append(args), manual)[1])
    monkeypatch.setattr(alerts.automatic_price_alerts, 'list_alerts', lambda *args, **kwargs: automatic)
    result = alerts.list_alerts(limit=1)
    assert result['items'][0]['event_id'] == 'auto-new'
    assert result['items'][0]['source_kind'] == 'automatic_price_volume'
    assert result['total'] == 15 and result['today_total'] == 8 and result['history_total'] == 7
    assert result['rules_total'] == 6
    assert result['latest_check']['job_id'] == 'manual-check'
    assert result['latest_checks']['automatic_price_volume']['job_id'] == 'auto-check'
    assert calls == [(None, 1, 'all')]
    assert 'source_kind' not in manual_event


def test_summary_uses_newest_five_across_both_indexed_sources(monkeypatch):
    manual = [event('manual-' + str(n), stamp=n) for n in range(5)]
    automatic = [event('auto-' + str(n), stamp=10+n) for n in range(5)]
    monkeypatch.setattr(alerts.line_alerts, 'alert_summary', lambda **kwargs: {'business_date': '2026-11-11', 'total': 20, 'items': manual})
    monkeypatch.setattr(alerts.automatic_price_alerts, 'alert_summary', lambda **kwargs: {'business_date': '2026-11-11', 'total': 30, 'items': automatic})
    result = alerts.alert_summary()
    assert result['total'] == 50
    assert [item['event_id'] for item in result['items']] == ['auto-4', 'auto-3', 'auto-2', 'auto-1', 'auto-0']
    assert result['limit'] == len(result['items']) == 5


def test_publication_race_never_mixes_two_business_days(monkeypatch):
    monkeypatch.setattr(alerts.line_alerts, 'alert_summary', lambda **kwargs: {'business_date': '2026-11-10', 'total': 1, 'items': []})
    monkeypatch.setattr(alerts.automatic_price_alerts, 'alert_summary', lambda **kwargs: {'business_date': '2026-11-11', 'total': 1, 'items': []})
    with pytest.raises(HTTPException) as failure:
        alerts.alert_summary()
    assert failure.value.status_code == 409


def test_watchlist_latest_uses_event_date_not_creation_time(monkeypatch):
    old = event('old-manual', '2026-11-10', 999)
    newer = event('new-auto', '2026-11-11', 1)
    monkeypatch.setattr(alerts.line_alerts, 'latest_by_stock', lambda *_: {'000678.SZ': old})
    monkeypatch.setattr(alerts.automatic_price_alerts, 'latest_by_stock', lambda *_: {'000678.SZ': newer})
    result = alerts.latest_by_stock(None, ['000678.SZ'], '2026-11-11')
    assert result['000678.SZ']['event_id'] == 'new-auto'
    assert 'source_kind' not in old
