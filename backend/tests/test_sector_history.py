"""Local source/cache fixtures; never credentials, network, tasks or real data."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from app.adapters.providers import ProviderError
from app.services import sector_history as sector

DAY = '2026-10-20'
PREVIOUS = '2026-10-19'
INDUSTRY = '881101.TI'
CONCEPT = '885728.TI'


def topic(code=INDUSTRY, kind='industry'):
    return {'topic_id': 'fuyao-'+code, 'topic_code': code,
            'name': '行业样本' if kind == 'industry' else '概念样本', 'kind': kind}


def payload(code=INDUSTRY, amount=0):
    rows = [{'date_ms': sector._midnight(day), 'open_price': close,
             'high_price': close+1, 'low_price': close-1, 'close_price': close,
             'volume': 0, 'turnover': amount}
            for day, close in ((PREVIOUS, 100), (DAY, 105))]
    return {'code': 0, 'data': {'thscode': code, 'interval': '1d', 'adjust': None, 'item': rows}}


class Market:
    def __init__(self, catalog=None):
        self.ref = catalog if catalog is not None else {
            'industry_catalog': [{'thscode': INDUSTRY, 'name': '行业样本'}],
            'concept_catalog': [{'thscode': CONCEPT, 'name': '概念样本'}],
            # Later member data must not seed selected-day source prices.
            'topics': [{'topic_code': INDUSTRY, 'observed_date': '2026-10-21',
                        'instrument_ids': ['600000.SH']}]}

    def reference(self): return self.ref
    def calendar(self): return ['2026-10-16', PREVIOUS, DAY]
    def status(self): return {'data_end': DAY}


class Cache:
    def __init__(self, root):
        self.root = root
        root.mkdir()
        self.records = {}; self.accept_calls = 0

    def reuse(self, source, capability, day, signature):
        entry = self.records.get((source, capability, day))
        return deepcopy(entry[1]) if entry and entry[0] == signature else None

    def accept(self, source, capability, day, signature, result, raw):
        self.accept_calls += 1
        path = self.root/f'raw-{self.accept_calls}.json'
        path.write_text(json.dumps(raw))
        result = {**deepcopy(result), 'fetched_at': '2026-10-21T10:00:00+08:00',
                  'raw_artifact': path.name, 'raw_sha256': sha256(path.read_bytes()).hexdigest()}
        self.records[(source, capability, day)] = (signature, result)
        return result


class Adapter:
    def __init__(self, fail=(), amounts=None):
        self.calls = []; self.fail = set(fail); self.amounts = amounts or {}

    def request(self, path, params):
        self.calls.append((path, dict(params)))
        code = params['thscode']
        if code in self.fail: raise ProviderError('controlled unavailable source')
        return payload(code, self.amounts.get(code, 0))


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(sector.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(sector, '_now', lambda: '2026-10-21T11:00:00+08:00')
    return Market(), Cache(tmp_path/'cache'), Adapter()


def test_exact_index_return_zero_amount_and_later_members_are_independent(local):
    market, cache, adapter = local
    record = sector.collect_sector_history(market, DAY, cache, adapter)
    assert record['complete'] is True
    assert len(record['items']) == 2
    assert all(row['change_pct'] == pytest.approx(5) for row in record['items'])
    assert all(row['amount'] == 0 and row['amount_unit'] == 'CNY' for row in record['items'])
    assert {row['kind'] for row in record['items']} == {'industry', 'concept'}
    assert record['coverage']['expected_previous_date'] == PREVIOUS
    assert record['coverage']['membership_independent'] is True
    assert record['coverage']['historical_catalog_complete'] is False
    assert record['coverage']['catalog_observed_at'].startswith('2026-10-21')
    assert record['coverage']['request_count'] == 2
    for path, params in adapter.calls:
        assert path == sector.HISTORY_PATH
        assert params['start'] == sector._midnight(PREVIOUS)
        assert params['end'] == sector._midnight(DAY)+86400000-1
        assert set(params) == {'thscode', 'interval', 'start', 'end'}


def test_optional_amount_remains_unknown_but_valid_price_pair_is_cacheable(local):
    market, cache, _ = local
    first = sector.collect_sector_history(market, DAY, cache, Adapter(amounts={INDUSTRY: None}))
    assert first['complete'] is False
    assert first['coverage']['accepted_count'] == 2
    assert first['coverage']['amount_unknown_count'] == 1
    row = next(row for row in first['items'] if row['topic_code'] == INDUSTRY)
    assert row['amount'] is None and row['amount_reason']
    adapter = Adapter(fail={INDUSTRY, CONCEPT})
    second = sector.collect_sector_history(market, DAY, cache, adapter)
    assert adapter.calls == []
    assert second['coverage']['cached_count'] == 2
    assert second['coverage']['amount_unknown_count'] == 1


@pytest.mark.parametrize('fault', [
    'wrong_code', 'wrong_row_code', 'wrong_interval', 'wrong_adjust', 'business_failure',
    'duplicate_target', 'missing_previous', 'missing_target', 'future', 'outside',
    'wrong_explicit_date', 'non_midnight', 'bool_price', 'negative_amount', 'bad_ohlc', 'empty'])
def test_invalid_source_never_becomes_complete_price_row(fault):
    value = payload()
    rows = value['data']['item']
    if fault == 'wrong_code': value['data']['thscode'] = CONCEPT
    elif fault == 'wrong_row_code': rows[1]['thscode'] = CONCEPT
    elif fault == 'wrong_interval': value['data']['interval'] = '1m'
    elif fault == 'wrong_adjust': value['data']['adjust'] = 'qfq'
    elif fault == 'business_failure': value['code'] = 1003
    elif fault == 'duplicate_target': rows.append(dict(rows[1]))
    elif fault == 'missing_previous': rows.pop(0)
    elif fault == 'missing_target': rows.pop()
    elif fault == 'future': rows[1]['date_ms'] = sector._midnight('2026-10-21')
    elif fault == 'outside': rows[0]['date_ms'] = sector._midnight('2026-10-16')
    elif fault == 'wrong_explicit_date': rows[1]['date'] = '2026-10-21'
    elif fault == 'non_midnight': rows[1]['date_ms'] += 1
    elif fault == 'bool_price': rows[1]['close_price'] = True
    elif fault == 'negative_amount': rows[1]['turnover'] = -1
    elif fault == 'bad_ohlc': rows[1]['low_price'] = 108
    elif fault == 'empty': value['data']['item'] = []
    with pytest.raises(ValueError): sector.normalize_sector_history(value, topic(), DAY, PREVIOUS)


def test_cache_reads_raw_again_preserves_fetch_time_and_keeps_other_successes(local):
    market, cache, adapter = local
    first = sector.collect_sector_history(market, DAY, cache, adapter)
    original_times = {row['topic_code']: row['fetched_at'] for row in first['items']}
    # Normalization metadata and cached derived numbers are not trusted.
    for _, record in cache.records.values():
        record['normalization_version'] = 'bad-old-normalizer'
        record['items'][0]['change_pct'] = 999
    offline = Adapter(fail={INDUSTRY, CONCEPT})
    second = sector.collect_sector_history(market, DAY, cache, offline)
    assert offline.calls == [] and cache.accept_calls == 2
    assert all(row['change_pct'] == pytest.approx(5) for row in second['items'])
    assert {row['topic_code']: row['fetched_at'] for row in second['items']} == original_times
    for row in second['items']:
        assert row['raw_artifact'] and row['raw_sha256']


def test_bad_raw_cache_cannot_be_silently_reused_or_erase_other_codes(local):
    market, cache, adapter = local
    sector.collect_sector_history(market, DAY, cache, adapter)
    stored = cache.records[('fuyao', 'sector-index', DAY+'-'+INDUSTRY)][1]
    (cache.root/stored['raw_artifact']).write_text('{}')
    offline = Adapter(fail={INDUSTRY, CONCEPT})
    result = sector.collect_sector_history(market, DAY, cache, offline)
    assert offline.calls == []
    assert [row['topic_code'] for row in result['items']] == [CONCEPT]
    assert result['failures'][0]['stage'] == 'cache_validation'
    assert result['complete'] is False


def test_cached_raw_with_valid_hash_but_wrong_day_is_rejected(local):
    market, cache, adapter = local
    sector.collect_sector_history(market, DAY, cache, adapter)
    stored = cache.records[('fuyao', 'sector-index', DAY+'-'+INDUSTRY)][1]
    raw_path = cache.root/stored['raw_artifact'];raw = json.loads(raw_path.read_text())
    raw['data']['item'][-1]['date_ms'] = sector._midnight('2026-10-21')
    raw_path.write_text(json.dumps(raw));stored['raw_sha256'] = sha256(raw_path.read_bytes()).hexdigest()
    result = sector.collect_sector_history(market, DAY, cache, Adapter())
    assert [row['topic_code'] for row in result['items']] == [CONCEPT]


def test_provider_failure_is_bounded_and_retains_cached_success_on_retry(local):
    market, cache, _ = local
    first = sector.collect_sector_history(market, DAY, cache, Adapter(fail={INDUSTRY}))
    assert [row['topic_code'] for row in first['items']] == [CONCEPT]
    failed = Adapter(fail={INDUSTRY, CONCEPT})
    second = sector.collect_sector_history(market, DAY, cache, failed)
    assert [params['thscode'] for _, params in failed.calls] == [INDUSTRY]
    assert second['coverage']['request_count'] == 1
    assert second['coverage']['max_provider_transport_attempts'] == 3
    assert [row['topic_code'] for row in second['items']] == [CONCEPT]


def test_duplicate_catalog_is_deduplicated_and_budget_is_explicit(local, monkeypatch):
    market, cache, adapter = local
    market.ref['industry_catalog'].append(dict(market.ref['industry_catalog'][0]))
    market.ref['industry_catalog'].append({'thscode': '884001.TI', 'name': '细分行业'})
    monkeypatch.setattr(sector, 'MAX_CODES', 2)
    result = sector.collect_sector_history(market, DAY, cache, adapter)
    assert len(adapter.calls) == 2
    assert result['coverage']['catalog_count'] == 3
    assert result['coverage']['duplicate_catalog_rows'] == 1
    assert result['coverage']['unrequested_count'] == 1
    assert any(row['stage'] == 'request_budget' for row in result['failures'])
    assert result['complete'] is False


def test_conflicting_catalog_identity_is_not_assigned_arbitrary_kind(local):
    market, cache, adapter = local
    market.ref['concept_catalog'].append({'thscode': INDUSTRY, 'name': '不一致分类'})
    result = sector.collect_sector_history(market, DAY, cache, adapter)
    assert [row['topic_code'] for row in result['items']] == [CONCEPT]
    assert len(adapter.calls) == 1
    assert result['failures'][0]['stage'] == 'catalog'


def test_missing_previous_calendar_does_not_fetch_and_future_target_is_rejected(local):
    market, cache, adapter = local
    market.calendar = lambda: [DAY]
    result = sector.collect_sector_history(market, DAY, cache, adapter)
    assert adapter.calls == [] and result['items'] == []
    assert result['failures'][0]['stage'] == 'calendar'
    with pytest.raises(ValueError): sector.collect_sector_history(market, '2026-10-21', cache, adapter)


def test_guard_is_checked_before_accept_and_can_stop_without_publishing(local):
    market, cache, adapter = local
    def guard():
        if adapter.calls: raise RuntimeError('paused after provider response')
    with pytest.raises(RuntimeError):
        sector.collect_sector_history(market, DAY, cache, adapter, guard=guard)
    assert len(adapter.calls) == 1 and cache.accept_calls == 0


def test_progress_and_sequential_request_spacing(local, monkeypatch):
    market, cache, adapter = local
    waits = [];progress = []
    monkeypatch.setattr(sector.time, 'monotonic', lambda: 10)
    monkeypatch.setattr(sector.time, 'sleep', waits.append)
    sector.collect_sector_history(market, DAY, cache, adapter, progress=lambda *args: progress.append(args))
    assert waits == [1.2]
    assert progress == [(1, 2, INDUSTRY), (2, 2, CONCEPT)]


def document(local):
    market, cache, adapter = local
    record = sector.collect_sector_history(market, DAY, cache, adapter)
    return {'target_date': DAY, 'sector_index_history': {DAY: record}}


def test_reader_uses_only_selected_date_and_recomputes_price_proof(local):
    value = document(local)
    exact = sector.sector_prices_for_date(value, DAY)
    assert len(exact['items']) == 2 and exact['complete'] is True
    assert sector.sector_prices_for_date(value, PREVIOUS)['items'] == []
    assert sector.sector_prices_for_date(value, '2026-10-21')['items'] == []
    assert sector.sector_prices_for_date(value, '2026-02-30')['items'] == []


@pytest.mark.parametrize('fault', ['date', 'version', 'signature', 'pct', 'future_row', 'duplicate'])
def test_reader_invalid_proofs_are_unknown_and_never_fill_other_dates(local, fault):
    value = document(local);record = value['sector_index_history'][DAY]
    if fault == 'date': record['date'] = PREVIOUS
    elif fault == 'version': record['normalization_version'] = 'old'
    elif fault == 'signature': record['items'][0]['request_signature'] = 'bad'
    elif fault == 'pct': record['items'][0]['change_pct'] = 999
    elif fault == 'future_row': record['items'][0]['date'] = '2026-10-21'
    else: record['items'].append(dict(record['items'][0]))
    result = sector.sector_prices_for_date(value, DAY)
    assert result['complete'] is False
    assert len(result['items']) == (0 if fault in ('date', 'version') else 1)


def test_reader_loads_only_exact_day_descriptor_and_validates_sha(local, tmp_path, monkeypatch):
    value = document(local);record = value['sector_index_history'][DAY]
    published = tmp_path/'published';root = published/'market-supplement';root.mkdir(parents=True)
    path = root/'day.json';path.write_text(json.dumps(record))
    monkeypatch.setattr(sector, 'published_dir', lambda: published)
    value['sector_index_history'][DAY] = {'date': DAY, 'source': 'fuyao',
        'normalization_version': sector.NORMALIZATION_VERSION,
        'artifact': 'day.json', 'sha256': sha256(path.read_bytes()).hexdigest()}
    # An unrelated broken descriptor must not be opened when the good day is read.
    value['sector_index_history'][PREVIOUS] = {'date': PREVIOUS, 'source': 'fuyao',
        'normalization_version': sector.NORMALIZATION_VERSION, 'artifact': 'missing.json', 'sha256': 'bad'}
    result = sector.sector_prices_for_date(value, DAY)
    assert len(result['items']) == 2
    assert result['coverage']['available_dates'] == [PREVIOUS, DAY]
    assert result['coverage']['selected_date_available'] is True
    path.write_text('{}')
    broken = sector.sector_prices_for_date(value, DAY)
    assert broken['items'] == [] and broken['coverage']['selected_date_available'] is False


@pytest.mark.parametrize('artifact', ['../outside.json', '/tmp/absolute.json'])
def test_reader_rejects_descriptor_path_traversal(local, artifact):
    value = document(local)
    value['sector_index_history'][DAY] = {'date': DAY, 'source': 'fuyao',
        'normalization_version': sector.NORMALIZATION_VERSION, 'artifact': artifact, 'sha256': 'bad'}
    assert sector.sector_prices_for_date(value, DAY)['items'] == []


def test_reader_does_not_use_future_descriptor_even_if_rows_exist(local):
    value = document(local)
    value['target_date'] = PREVIOUS
    assert sector.sector_prices_for_date(value, DAY)['items'] == []


@pytest.mark.parametrize('status,code', [(401, None), (403, None), (429, None), (None, 2001)])
def test_explicit_authorization_or_rate_limit_stops_further_requests_but_keeps_cache(local, status, code):
    market, cache, _ = local
    # Keep a previously accepted concept while the first industry's provider
    # rejects the credential/rate. The collector must not hammer more requests.
    sector.collect_sector_history(market, DAY, cache, Adapter(fail={INDUSTRY}))
    market.ref['industry_catalog'].append({'thscode': '884001.TI', 'name': '细分行业'})
    class Denied(Adapter):
        def request(self, path, params):
            self.calls.append((path, params))
            raise ProviderError('controlled denied', status=status, code=code)
    denied = Denied()
    result = sector.collect_sector_history(market, DAY, cache, denied)
    assert len(denied.calls) == 1
    assert [row['topic_code'] for row in result['items']] == [CONCEPT]
    assert result['coverage']['source_blocked_count'] == 1
    assert result['coverage']['unrequested_count'] == 1


def test_empty_catalog_is_unknown_and_exit_guard_still_runs(local):
    market, cache, adapter = local
    market.ref = {'concept_catalog': [], 'industry_catalog': []}
    calls = []
    result = sector.collect_sector_history(market, DAY, cache, adapter, guard=lambda: calls.append(True))
    assert adapter.calls == [] and len(calls) == 2
    assert result['complete'] is False and result['failures'][0]['stage'] == 'catalog'


def test_reader_descriptor_cannot_rename_other_day_valid_artifact(local, tmp_path, monkeypatch):
    value = document(local);record = value['sector_index_history'][DAY]
    record['date'] = PREVIOUS
    published = tmp_path/'published';root = published/'market-supplement';root.mkdir(parents=True)
    path = root/'wrong-date.json';path.write_text(json.dumps(record))
    monkeypatch.setattr(sector, 'published_dir', lambda: published)
    value['sector_index_history'][DAY] = {'date': DAY, 'source': 'fuyao',
        'normalization_version': sector.NORMALIZATION_VERSION,
        'artifact': path.name, 'sha256': sha256(path.read_bytes()).hexdigest()}
    assert sector.sector_prices_for_date(value, DAY)['items'] == []
