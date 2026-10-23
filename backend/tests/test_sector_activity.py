"""Small exact-day source proofs, no databases, provider calls or member backfill."""
from copy import deepcopy
from datetime import date, timedelta
import gc
import weakref

import pytest

from app.services import sector_activity as activity


def sessions(count=12):
    current = date(2026, 10, 5)
    result = []
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def quote(day, index, *, kind='concept', level=None, source='fuyao'):
    return {'topic_id': 'fuyao-'+f'{880000+index:06d}.TI', 'topic_code': f'{880000+index:06d}.TI',
            'name': '测试板块'+str(index), 'kind': kind, 'classification_level': level,
            'source': source, 'normalization_version': activity.NORMALIZATION_VERSION,
            'date': day, 'quote_date': day, 'change_pct': 20-index, 'amount': 1000-index,
            'quote_basis': 'provider_index'}


class LocalRanks:
    def __init__(self, days=None, size=15):
        self.days = days or sessions()
        self.snapshot = 'synthetic-sector-generation'
        self.records = {day: {'date': day, 'complete': True,
                              'items': [quote(day, index) for index in range(size)]} for day in self.days}
        self.calls = []

    def calendar(self):
        return list(self.days)

    def reader(self, document, day):
        self.calls.append(day)
        return deepcopy(self.records.get(day, {'date': day, 'complete': False, 'items': []}))

    def panel(self, day):
        pytest.fail('seven-day activity must not compute seven full market/indicator panels')

    def reference(self):
        return {'_supplement': {'target_date': self.days[-1], 'sector_index_history': deepcopy(self.records)}}


@pytest.fixture(autouse=True)
def clear_cache():
    activity.clear_sector_activity_cache()
    yield
    activity.clear_sector_activity_cache()


def decorate(market, day, rows=None, reference=None, current=None):
    return activity.decorate_sector_activity(
        market, {'snapshot_id': market.snapshot}, day,
        deepcopy(market.records.get(day, {}).get('items', [])) if rows is None else rows,
        market.reference() if reference is None else reference,
        market.records.get(day) if current is None else current, price_reader=market.reader)


def test_fixed_positive_change_and_amount_rankings_include_observation_and_never_request_future():
    market = LocalRanks()
    day = market.days[8]
    for record in market.records.values():
        record['items'][14]['amount'] = 2000
        record['items'][12]['change_pct'] = -40
    original = deepcopy(market.records)
    rows, summary = decorate(market, day)
    assert market.records == original
    assert [row['topic_code'] for row in rows] == [row['topic_code'] for row in original[day]['items']]
    first = rows[0]['sector_activity_7d']
    assert first['change_pct']['count'] == first['amount']['count'] == 7
    assert first['change_pct']['dates'] == market.days[2:9]
    assert [entry['rank'] for entry in first['change_pct']['ranks']] == [1]*7
    assert [entry['rank'] for entry in first['amount']['ranks']] == [2]*7
    last = rows[14]['sector_activity_7d']
    assert last['change_pct']['count'] == 0 and last['amount']['count'] == 7
    assert rows[12]['sector_activity_7d']['change_pct']['count'] == 0
    assert first['change_pct']['state'] == 'complete' and first['change_pct']['complete'] is True
    assert summary['window_start'] == market.days[2] and summary['window_end'] == day
    assert summary['metrics'] == ['change_pct', 'amount']
    assert market.calls == market.days[2:8], 'the current verified proof is reused and future dates stay unread'
    assert set(first['amount']['ranks'][0]) == {'date', 'rank', 'value'}


def test_exact_source_code_kind_and_industry_level_not_names_or_members_determine_identity():
    market = LocalRanks(size=2)
    day = market.days[8]
    for observed, record in market.records.items():
        record['items'] = [
            quote(observed, 0),
            quote(observed, 20, kind='industry', level=1),
            quote(observed, 21, kind='industry', level=2),
            quote(observed, 22, kind='industry'),
            quote(observed, 30, source='another-source'),
        ]
        # Renaming does not change identity, nor does adding current-only members.
        record['items'][0]['name'] = '历史名称'+observed
        record['items'][0]['instrument_ids'] = ['unrelated-current-member']
    rows = deepcopy(market.records[day]['items'])
    rows[0]['name'] = '新的名称'
    rows.append({**quote(day, 30), 'name': rows[4]['name']})
    for record in market.records.values():
        for row in record['items']:
            # Make all industry levels eligible despite very small directories.
            row['change_pct'] = 1
    rows, summary = decorate(market, day, rows=rows)
    assert rows[0]['sector_activity_7d']['change_pct']['count'] == 7
    for index, level in ((1, '1'), (2, '2'), (3, None)):
        value = rows[index]['sector_activity_7d']['change_pct']
        assert value['count'] == 7 and value['group_level'] == level
        assert [entry['rank'] for entry in value['ranks']] == [1]*7
    assert '层级未知' in rows[3]['sector_activity_7d']['change_pct']['basis']
    assert '不视为一级' in rows[3]['sector_activity_7d']['change_pct']['reason']
    assert rows[-1]['sector_activity_7d']['change_pct']['count'] == 0, 'same-name, same-code other source must not merge'
    assert {entry['level'] for entry in summary['groups'] if entry['kind'] == 'industry'} == {'1', '2', None}


def test_sample_members_and_unverified_code_skip_all_history_reads_and_stay_unknown():
    market = LocalRanks()
    day = market.days[8]
    rows = [{**quote(day, 0), 'quote_basis': 'archived_member_sample'},
            {**quote(day, 1), 'topic_code': None},
            {**quote(day, 2), 'topic_code': 'not-a-source-code'},
            {**quote(day, 3), 'normalization_version': 'other-version'}]
    decorated, summary = decorate(market, day, rows=rows)
    assert market.calls == [] and summary['groups'] == []
    assert all(row['sector_activity_7d'][metric]['count'] is None
               and row['sector_activity_7d'][metric]['state'] == 'unknown'
               for row in decorated for metric in activity.METRICS)
    assert decorate(market, day, rows=[])[0] == [] and market.calls == []


def test_missing_dates_short_calendar_and_wrong_day_are_not_silently_complete():
    market = LocalRanks()
    day = market.days[8]
    missing = market.days[4]
    del market.records[missing]
    rows, summary = decorate(market, day)
    value = rows[0]['sector_activity_7d']['change_pct']
    assert value['count'] == 6 and value['state'] == 'observed_partial' and value['label'] == '已观察6次 · 样本不完整'
    assert value['observed_days'] == 6 and value['expected_days'] == 7 and not value['complete']
    assert all(group['missing_dates'] == [missing] for group in summary['groups'])
    short = LocalRanks(sessions(4))
    short_rows, _ = decorate(short, short.days[-1])
    assert short_rows[0]['sector_activity_7d']['amount']['count'] == 4
    assert not short_rows[0]['sector_activity_7d']['amount']['complete']
    wrong = LocalRanks()
    wrong.records[wrong.days[3]]['date'] = wrong.days[4]
    wrong_rows, _ = decorate(wrong, wrong.days[8])
    assert wrong_rows[0]['sector_activity_7d']['amount']['count'] == 6


def test_positive_filter_and_deterministic_code_ties_keep_metrics_independent():
    day = sessions(1)[0]
    data = [quote(day, index) for index in reversed(range(12))]
    for row in data:
        row['amount'] = 100
        row['change_pct'] = 0 if row['topic_code'] == '880000.TI' else 1
    facts = activity._daily({'date': day, 'complete': True, 'items': data}, day)
    group = activity._group(data[0])
    assert [row['topic_code'] for row in facts[group, 'amount']['items']] == [f'{880000+index}.TI' for index in range(10)]
    assert [row['topic_code'] for row in facts[group, 'change_pct']['items']] == [f'{880000+index}.TI' for index in range(1, 11)]
    assert facts[group, 'amount']['complete'] is True
    assert activity._daily(None, day) == {}
    assert activity._daily({'date': day, 'items': 'malformed'}, day) == {}
    assert isinstance(hash(activity._signature({'sector_index_history': {
        day: {'artifact': {'invalid': 'not a path'}, 'sha256': ['invalid hash']}}}, [day])), int)


def test_incomplete_competitors_are_observations_not_monotonic_lower_bounds():
    market = LocalRanks()
    day = market.days[8]
    partial_day = market.days[4]
    market.records[partial_day]['items'] = market.records[partial_day]['items'][:10]
    market.records[partial_day]['complete'] = False
    rows, summary = decorate(market, day)
    value = rows[9]['sector_activity_7d']['amount']
    assert value['count'] == 7 and value['state'] == 'observed_partial'
    assert value['label'] == '已观察7次 · 样本不完整'
    assert '不是次数下界' in value['reason']
    assert all(group['incomplete_dates'] == [partial_day] for group in summary['groups'])
    # A previously absent competitor pushes the partial day's tenth item out.
    # Therefore the original seven observations were never a >=7 guarantee.
    market.records[partial_day]['items'].append({**quote(partial_day, 30), 'amount': 5000, 'change_pct': 40})
    market.records[partial_day]['complete'] = True
    rows, _ = decorate(market, day)
    assert rows[9]['sector_activity_7d']['amount']['count'] == 6
    assert rows[9]['sector_activity_7d']['amount']['state'] == 'complete'
    # Unknown values and duplicate source codes invalidate the affected date.
    market.records[partial_day]['items'][0]['amount'] = None
    market.records[partial_day]['items'].append(deepcopy(market.records[partial_day]['items'][1]))
    rows, _ = decorate(market, day)
    assert rows[9]['sector_activity_7d']['amount']['state'] == 'observed_partial'


def test_cache_is_bounded_generation_asof_descriptor_and_instance_scoped_with_current_overlay(monkeypatch):
    market = LocalRanks(sessions(40))
    for day in market.days:
        decorate(market, day)
    assert len(activity._cache) == activity.CACHE_LIMIT == 32
    day = market.days[-1]
    calls = len(market.calls)
    rows, _ = decorate(market, day)
    assert len(market.calls) == calls
    rows[0]['sector_activity_7d']['amount']['ranks'][0]['rank'] = 999
    current = deepcopy(market.records[day])
    current['items'][0]['amount'] = 0
    again, _ = decorate(market, day, current=current)
    assert again[0]['sector_activity_7d']['amount']['count'] == 6
    assert len(market.calls) == calls, 'fresh current proof overlays historical cache'
    market.records[market.days[-3]]['items'][0]['amount'] = 0
    again, _ = decorate(market, day)
    assert again[0]['sector_activity_7d']['amount']['count'] == 6
    assert len(market.calls) == calls+6, 'a changed supplement descriptor invalidates the facts'
    market.snapshot = 'replacement-snapshot'
    decorate(market, day)
    assert len(market.calls) == calls+12
    monkeypatch.setattr(activity, 'id', lambda _: 42, raising=False)
    first, second = LocalRanks(), LocalRanks()
    first.records[first.days[3]]['items'][0]['amount'] = 0
    assert decorate(first, first.days[8])[0][0]['sector_activity_7d']['amount']['count'] == 6
    assert decorate(second, second.days[8])[0][0]['sector_activity_7d']['amount']['count'] == 7
    # The ranking cache must never retain the large immutable market frame.
    reference = weakref.ref(second)
    del second
    gc.collect()
    assert reference() is None


def test_anchor_only_head_revision_invalidates_cached_date_acceptance():
    market = LocalRanks()
    day = market.days[8]
    reference = market.reference()
    original_reader = market.reader

    def anchored_reader(document, observed):
        # Match sector_prices_for_date's independent head acceptance boundary.
        if observed > document['target_date']:
            return {'date': observed, 'complete': False, 'items': []}
        return original_reader(document, observed)

    market.reader = anchored_reader
    assert decorate(market, day, reference=reference)[0][0]['sector_activity_7d']['amount']['count'] == 7
    reference['_supplement']['target_date'] = market.days[4]
    current = {'date': day, 'complete': False, 'items': []}
    cached, _ = decorate(market, day, reference=reference, current=current)
    assert cached[0]['sector_activity_7d']['amount']['count'] == 3
    activity.clear_sector_activity_cache()
    uncached, _ = decorate(market, day, reference=reference, current=current)
    assert uncached == cached, 'changing only the acceptance anchor must match a cold verified read'
