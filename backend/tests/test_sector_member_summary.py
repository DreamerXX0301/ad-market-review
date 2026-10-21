"""Board member expansion adds identity, not an extra per-stock calculation."""
import pytest

from test_workspace import ws
from test_sector_tracking import local, add, CODE
from app.services import sector_tracking as tracking, topic_members


def test_current_member_summary_reuses_batched_statistics_and_real_stock_board(local, monkeypatch):
    _, _, identifier = add(local)
    # Initialize the exact source classification without reading any quote.
    from app.services.topics import seed_topics
    seed_topics()
    calls = []
    def result(*args, **kwargs):
        calls.append(True)
        return {'items': [{'instrument_id': '600001.SH', 'code': '600001', 'name': '测试',
            'return_ytd_pct': 20, 'return_mtd_pct': 4, 'change_30d_pct': 8,
            'limit_up_ytd': None, 'limit_up_ytd_observed': 3,
            'last_limit_up_date': local['state']['day'], 'last_limit_up_complete': True,
            'sessions_since_limit_up': 0}], 'selected_topics': [{'source_observed_date': local['market'].days[71]}],
            'warnings': [], 'total': 1}
    monkeypatch.setattr(topic_members, 'list_members', result)
    output = tracking.members(identifier)
    assert calls == [True]
    row = output['items'][0]
    assert row['board'] == 'main' and row['board_label'] == '主板'
    assert row['annual_limit_up_count'] is None and row['annual_limit_up_count_observed'] == 3
    assert row['latest_limit_up_date'] == local['state']['day'] and row['sessions_since_latest_limit_up'] == 0
    assert row['return_30d_pct'] == 8 and row['month_return_pct'] == 4
    assert output['membership_as_of'] == local['market'].days[71]
    assert output['quote_as_of'] == local['state']['day']
