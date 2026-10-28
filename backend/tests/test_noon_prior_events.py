"""Original-generation prior-event recovery; fixtures are local and synthetic."""
from copy import deepcopy
from hashlib import sha256
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.services import noon_prior_events as context

DAY, PRIOR = '2026-10-28', '2026-10-27'
GENERATION = 'market-prior-events-fixture'


def payload():
    return {'date': DAY, 'calendar': {'verified': True, 'previous_day': PRIOR},
            'previous_review': {'date': PRIOR, 'state': 'background_only',
                'payload': {'date': PRIOR, 'mode': 'previous_session_price_facts',
                            'source_snapshot_id': GENERATION}}}


def reference():
    return {'pool_trade_date': PRIOR,
            'pool_coverage': {
                'breakpool': {'complete': True, 'declared_total': 2},
                'limitdown': {'complete': False, 'declared_total': 2, 'quarantined_rows': 1}},
            'breakpool': [{'thscode': '600001.SH', 'last_price': 10., 'name': '合成炸板甲'},
                          {'thscode': '600002.SH', 'last_price': 20., 'name': '合成炸板乙'}],
            'limitdown': [{'thscode': '600003.SH', 'last_price': 30., 'name': '合成跌停'}],
            'limitup': []}


def daily():
    return [{'instrument_id': symbol, 'date': PRIOR, 'close': price, 'volume': 100.,
             'price_basis': 'none', 'source': 'fuyao'}
            for symbol, price in [('600001.SH', 10.), ('600002.SH', 20.), ('600003.SH', 30.)]]


@pytest.fixture
def published(tmp_path, monkeypatch):
    root = tmp_path / 'published'
    root.mkdir()
    monkeypatch.setattr(context, 'published_dir', lambda: root)
    return root


def publish(root, ref=None, prices=None):
    folder = root / GENERATION
    folder.mkdir(exist_ok=True)
    ref_path = folder / 'reference.json'
    ref_path.write_text(json.dumps(reference() if ref is None else ref))
    prices = daily() if prices is None else prices
    temp = folder / 'test-prices.parquet'
    pq.write_table(pa.Table.from_pylist(prices), temp)
    data = temp.read_bytes();digest = sha256(data).hexdigest()
    object_key = f'market-objects/daily/{digest[:2]}/{digest}.parquet'
    target = root / object_key
    target.parent.mkdir(parents=True, exist_ok=True)
    temp.rename(target)
    days = sorted({row['date'] for row in prices})
    descriptor = {'version': 'market-partitions-v1', 'role': 'daily.parquet',
        'rows': len(prices), 'symbols': len({row['instrument_id'] for row in prices}),
        'data_begin': days[0], 'data_end': days[-1], 'partitions': [
            {'month': PRIOR[:7], 'object_key': object_key, 'sha256': digest, 'bytes': len(data),
             'rows': len(prices), 'data_begin': days[0], 'data_end': days[-1]}]}
    (folder / 'daily.manifest.json').write_text(json.dumps(descriptor))
    update_manifest(root)
    return folder, target


def update_manifest(root):
    folder = root / GENERATION
    files = []
    for name, role in [('reference.json', 'reference.json'), ('daily.manifest.json', 'daily.parquet')]:
        raw = (folder / name).read_bytes()
        files.append({'role': role, 'path': f'published/{GENERATION}/{name}',
                      'sha256': sha256(raw).hexdigest(), 'bytes': len(raw)})
    (folder / 'manifest.json').write_text(json.dumps({'summary': {
        'snapshot_id': GENERATION, 'data_end': PRIOR, 'source': 'fuyao', 'files': files}}))


def pool(result, key='limit_break'):
    return result['event_cohorts'][key]


def test_original_pool_and_selected_day_close_recover_without_mutating_snapshot(published):
    publish(published)
    value = payload();before = deepcopy(value)
    result = context.build_prior_event_context(value)
    assert value == before
    assert result['date'] == PRIOR and result['source_snapshot_id'] == GENERATION
    assert len(result['source_sha256']) == 64
    assert pool(result)['state'] == 'observed_sample'
    assert pool(result)['source_count'] == 2 and pool(result)['verified_count'] == 2
    assert {row['instrument_id'] for row in pool(result)['items']} == {'600001.SH', '600002.SH'}
    down = pool(result, 'limit_down')
    assert down['state'] == 'partial'
    assert down['source_count'] == 2 and down['verified_count'] == 1
    assert down['items'][0]['previous_close'] == 30
    assert down['items'][0]['previous_board_count'] is None


@pytest.mark.parametrize('change', ['event_price', 'event_date', 'zero_volume', 'wrong_quote_date', 'adjusted_quote', 'duplicate_quote'])
def test_price_volume_day_and_unique_close_evidence_are_required(published, change):
    ref, prices = reference(), daily()
    if change == 'event_price':ref['breakpool'][0]['last_price'] = 10.01
    elif change == 'event_date':ref['breakpool'][0]['trade_date'] = '2026-10-26'
    elif change == 'zero_volume':prices[0]['volume'] = 0
    elif change == 'wrong_quote_date':prices[0]['date'] = '2026-10-26'
    elif change == 'adjusted_quote':prices[0]['price_basis'] = 'forward'
    elif change == 'duplicate_quote':prices.append(deepcopy(prices[0]))
    publish(published, ref, prices)
    recovered = pool(context.build_prior_event_context(payload()))
    if change == 'duplicate_quote':
        assert recovered['verified_count'] is None and recovered['state'] == 'unknown'
        assert recovered['items'] == []
        return
    assert recovered['verified_count'] == 1
    assert recovered['state'] != 'observed_sample'
    assert [row['instrument_id'] for row in recovered['items']] == ['600002.SH']


@pytest.mark.parametrize('change', ['manifest_generation', 'future_data', 'reference_sha', 'daily_sha', 'parquet_sha', 'reference_day'])
def test_invalid_immutable_generation_is_not_replaced_by_current_sources(published, change):
    folder, target = publish(published)
    manifest_path = folder / 'manifest.json';manifest = json.loads(manifest_path.read_text())
    if change == 'manifest_generation':manifest['summary']['snapshot_id'] = 'market-wrong'
    elif change == 'future_data':manifest['summary']['data_end'] = DAY
    elif change == 'reference_sha':manifest['summary']['files'][0]['sha256'] = '0' * 64
    elif change == 'daily_sha':manifest['summary']['files'][1]['sha256'] = '0' * 64
    elif change == 'parquet_sha':target.write_bytes(target.read_bytes() + b'tamper')
    elif change == 'reference_day':
        ref = reference();ref['pool_trade_date'] = '2026-10-26'
        (folder / 'reference.json').write_text(json.dumps(ref));update_manifest(published)
        manifest = json.loads(manifest_path.read_text())
    manifest_path.write_text(json.dumps(manifest))
    (published / 'market-head.json').write_text(json.dumps({'snapshot_id': 'market-do-not-follow'}))
    result = context.build_prior_event_context(payload())
    assert pool(result)['items'] == []
    assert pool(result)['state'] == 'unknown'


def test_existing_frozen_pools_are_preferred_without_reading_original_store(published, monkeypatch):
    value = payload()
    frozen = {'date': PRIOR, 'state': 'observed_sample', 'source_count': 1, 'verified_count': 1,
              'items': [{'instrument_id': '600001.SH', 'previous_close': 10., 'previous_board_count': None}]}
    value['previous_review']['event_cohorts'] = {'limit_break': deepcopy(frozen), 'limit_down': deepcopy(frozen)}
    monkeypatch.setattr(context, 'published_dir', lambda: pytest.fail('frozen context must not read original storage'))
    result = context.build_prior_event_context(value)
    assert pool(result) == frozen and pool(result, 'limit_down') == frozen


def test_missing_original_or_mismatched_calendar_keeps_unknown(published):
    result = context.build_prior_event_context(payload())
    assert pool(result)['source_count'] is None and pool(result)['items'] == []
    publish(published)
    value = payload();value['calendar']['previous_day'] = '2026-10-26'
    result = context.build_prior_event_context(value)
    assert pool(result)['items'] == [] and pool(result)['state'] == 'unknown'


def test_wrong_day_frozen_pool_is_not_silently_rewritten_from_other_material(published):
    publish(published)
    value = payload()
    value['previous_review']['event_cohorts'] = {'limit_break': {
        'date': '2026-10-26', 'state': 'observed_sample', 'source_count': 1,
        'verified_count': 1, 'items': [{'instrument_id': '600009.SH', 'previous_close': 99.}]}}
    result = pool(context.build_prior_event_context(value))
    assert result['items'] == [] and result['state'] == 'unknown'
    assert result['date'] == PRIOR


def test_only_a_confirmed_complete_empty_pool_is_zero(published):
    ref = reference();ref['breakpool'] = []
    ref['pool_coverage']['breakpool'] = {'complete': True, 'declared_total': 0}
    publish(published, ref)
    result = pool(context.build_prior_event_context(payload()))
    assert result['source_count'] == 0 and result['verified_count'] == 0
    assert result['state'] != 'unknown'
    ref['pool_coverage']['breakpool'] = {'complete': False, 'declared_total': 2, 'quarantined_rows': 2}
    publish(published, ref)
    result = pool(context.build_prior_event_context(payload()))
    assert result['source_count'] == 2 and result['verified_count'] == 0
    assert result['state'] != 'observed_sample'
