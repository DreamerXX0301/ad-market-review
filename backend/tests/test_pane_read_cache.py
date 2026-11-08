import json
from datetime import date, timedelta

import pytest

from app.services import pane_read_cache as cache


class PublishedMarket:
    def __init__(self):
        self.days = [(date(2026, 1, 5) + timedelta(days=i)).isoformat() for i in range(10)]
        self.latest = self.days[-2]

    def calendar(self):
        return self.days

    def status(self):
        return {'data_end': self.latest}


@pytest.fixture
def market(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, '_root', lambda: tmp_path / 'pane')
    monkeypatch.setattr(cache, '_memory', cache.OrderedDict())
    monkeypatch.setattr(cache, '_memory_bytes', 0)
    return PublishedMarket()


def test_lazy_fill_and_restart_keep_exact_body_and_reject_changed_proof(market):
    day = market.latest
    calls = []

    def build():
        calls.append(True)
        return {'rows': [{'count': len(calls), 'missing': None}]}

    first = cache.read_block('example', day, 'proof-a', build, market=market)
    first['rows'][0]['count'] = 999
    assert cache.read_block('example', day, 'proof-a', build, market=market)['rows'][0]['count'] == 1
    cache._memory.clear()
    cache._memory_bytes = 0
    assert cache.read_block('example', day, 'proof-a', build, market=market)['rows'][0]['count'] == 1
    assert cache.read_block('example', day, 'proof-b', build, market=market)['rows'][0]['count'] == 2
    assert len(calls) == 2


def test_corrupt_cache_is_rebuilt_and_unverified_source_bypasses_it(market):
    day = market.latest
    assert cache.read_block('example', day, 'proof', lambda: {'count': 1}, market=market) == {'count': 1}
    path = cache._root() / day / 'example.json'
    corrupt = json.loads(path.read_text())
    corrupt['body'] = '{"count":0}'
    path.write_text(json.dumps(corrupt))
    cache._memory.clear()
    cache._memory_bytes = 0
    assert cache.read_block('example', day, 'proof', lambda: {'count': 2}, market=market) == {'count': 2}
    assert cache.read_block('example', day, None, lambda: {'count': None}, market=market) == {'count': None}


def test_only_last_seven_published_sessions_and_size_budget(market, monkeypatch):
    monkeypatch.setattr(cache, 'MAX_BLOCK_BYTES', 40)
    monkeypatch.setattr(cache, 'MAX_FILES', 2)
    calls = []

    def build():
        calls.append(True)
        return {'number': 1}

    old, closed, future = market.days[1], market.latest, market.days[-1]
    for day in (old, future):
        for _ in range(2):
            cache.read_block('example', day, 'proof', build, market=market)
        assert not (cache._root() / day).exists()
    cache.read_block('large', closed, 'proof', lambda: 'a'*100, market=market)
    assert not (cache._root() / closed / 'large.json').exists()
    for name in ('one', 'two', 'three'):
        cache.read_block(name, closed, 'proof', build, market=market)
    assert len(cache._memory) == 2
    assert len(list((cache._root() / closed).glob('*.json'))) == 2
    assert len(calls) == 7


def test_builder_exception_is_not_retried_when_day_is_ineligible(market):
    calls = []

    def build():
        calls.append(True)
        raise ValueError('来源无法核验')

    with pytest.raises(ValueError, match='来源无法核验'):
        cache.read_block('example', market.days[0], 'proof', build, market=market)
    assert calls == [True]


def test_result_built_during_publication_is_not_cached(market, monkeypatch):
    day = market.latest
    generation = 'old'
    monkeypatch.setattr(market, 'status', lambda: {'data_end': day, 'snapshot_id': generation})

    def build():
        nonlocal generation
        generation = 'new'
        return {'count': None}

    assert cache.read_block('example', day, 'old', build, market=market) == {'count': None}
    assert not (cache._root() / day / 'example.json').exists()
    assert cache.read_block('example', day, 'new', lambda: {'count': 3}, market=market) == {'count': 3}
