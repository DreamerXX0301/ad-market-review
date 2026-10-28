"""Named-generation, exact-day Parquet projection without historical panel I/O."""
from hashlib import sha256
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app import core
from app.analytics.market_storage import write_reference
from app.services import market_supplement
from app.services.noon_close_inputs import load_inputs

DAY = '2026-10-28'


@pytest.fixture
def isolated_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for name in ('SHIXU_SQLITE_PATH', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR', 'SHIXU_PUBLISHED_DIR'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(market_supplement, 'load_supplement_with_fingerprint', lambda *_a, **_k: ({}, None))
    return tmp_path


def generation(root, *, parts=False, wrong_event=False, amount_unit='CNY'):
    published = root / 'published'; name = 'market-fixture'; folder = published / name; folder.mkdir(parents=True)
    rows = [{'instrument_id': '600001.SH', 'date': day, 'open': 10., 'high': high, 'low': 9., 'close': close,
             'volume': 100., 'amount': 1200., 'source': 'fuyao', 'price_basis': 'none'}
            for day, close, high in [('2026-10-27', 99., 100.), (DAY, 11., 11.), ('2026-10-29', 33., 34.)]]
    temporary = root / 'daily.parquet'; pq.write_table(pa.Table.from_pylist(rows), temporary)
    data = temporary.read_bytes(); checksum = sha256(data).hexdigest()
    key = f'market-objects/daily/{checksum[:2]}/{checksum}.parquet'
    target = published / key; target.parent.mkdir(parents=True); target.write_bytes(data)
    daily = {'version': 'market-partitions-v1', 'role': 'daily.parquet', 'partitions': [
        {'month': '2026-10', 'object_key': key, 'sha256': checksum, 'bytes': len(data), 'rows': 3, 'data_begin': '2026-10-27', 'data_end': '2026-10-29'}]}
    descriptor = folder / 'daily.manifest.json'; descriptor.write_text(core.dumps(daily))
    reference = {'calendar': ['2026-10-27', DAY, '2026-10-29'], 'pool_trade_date': DAY,
                 'pool_coverage': {name: {'complete': True} for name in ('limitup', 'limitdown', 'breakpool')},
                 'limitup': [{'thscode': '600001.SH', 'date': DAY, 'last_price': 11.1 if wrong_event else 11., 'continue_day_cnt': 3}],
                 'limitdown': [], 'breakpool': [],
                 'indices': {'000001.SH': [{'date': DAY, 'close': 11., 'amount': 10000.}]}}
    if parts:
        files = write_reference(reference, published, folder, name)
        # write_reference returns the list of artifact records.
        if isinstance(files, tuple): files = files[0]
        path = folder / 'reference.json'
        files.append({'role': 'reference.json', 'path': f'published/{name}/reference.json', 'sha256': sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size})
    else:
        path = folder / 'reference.json'; path.write_text(core.dumps(reference))
        files = [{'role': 'reference.json', 'path': f'published/{name}/reference.json', 'sha256': sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}]
    files = [*files, {'role': 'daily.parquet', 'path': f'published/{name}/daily.manifest.json', 'sha256': sha256(descriptor.read_bytes()).hexdigest(), 'bytes': descriptor.stat().st_size}]
    manifest = {'summary': {'snapshot_id': name, 'source': 'fuyao', 'price_basis': 'none', 'amount_unit': amount_unit,
                            'data_end': '2026-10-29', 'files': files}}
    (folder / 'manifest.json').write_text(core.dumps(manifest))
    noon = {'version': 'noon-snapshot-v1', 'date': DAY,
            'stocks': [{'instrument_id': '600001.SH', 'prev_close': 10., 'latest_price': 10.5, 'change_pct': 5., 'price_basis': 'none', 'currency': 'CNY'}],
            'indices': [{'instrument_id': '000001.SH', 'prev_close': 10., 'price_basis': 'none'}],
            'sources': [{'name': 'stocks_page_1', 'source': 'fuyao', 'state': 'received'}]}
    return name, noon, target


@pytest.mark.parametrize('parts', [False, True])
def test_only_named_day_and_source_price_basis_are_projected(isolated_inputs, monkeypatch, parts):
    name, noon, _ = generation(isolated_inputs, parts=parts)
    import duckdb
    monkeypatch.setattr(duckdb, 'connect', lambda *_a, **_k: pytest.fail('no DuckDB reader'))
    content, origin = load_inputs(name, DAY, noon)
    assert len(content['stocks']) == 1 and content['stocks'][0]['date'] == DAY
    assert content['stocks'][0]['close'] == 11.
    assert content['stocks'][0]['change_pct'] == pytest.approx(10.)
    assert content['indices'][0]['change_pct'] == pytest.approx(10.)
    assert content['events']['limit_up']['complete'] is True
    assert content['events']['limit_down']['complete'] is True
    assert content['events']['limit_up']['items'][0]['previous_board_count'] == 3
    assert content['pool_semantics']['limit_break'] == 'unknown'
    assert content['amount_comparable'] is True
    assert origin['kind'] == 'derived-same-day-projection' and origin['source_snapshot_id'] == name
    assert all('path' not in x for x in origin['objects'])
    assert 'source_snapshot_id' not in content


def test_corrupt_event_does_not_become_complete_or_claim_unlisted(isolated_inputs):
    name, noon, _ = generation(isolated_inputs, wrong_event=True)
    content, _ = load_inputs(name, DAY, noon)
    assert content['events']['limit_up']['complete'] is False
    assert content['events']['limit_up']['unverified_count'] == 1
    assert content['events']['limit_up']['items'] == []


@pytest.mark.parametrize('missing_proof', ['source', 'unit'])
def test_amount_difference_requires_both_end_origin_units(isolated_inputs, missing_proof):
    name, noon, _ = generation(isolated_inputs, amount_unit='unknown' if missing_proof == 'unit' else 'CNY')
    if missing_proof == 'source': noon['sources'] = []
    content, _ = load_inputs(name, DAY, noon)
    assert content['amount_comparable'] is False


def test_corrupt_named_partition_and_path_escape_are_rejected(isolated_inputs):
    name, noon, path = generation(isolated_inputs)
    path.write_bytes(path.read_bytes() + b'corrupt')
    with pytest.raises(ValueError): load_inputs(name, DAY, noon)
    with pytest.raises(ValueError): load_inputs('../market-fixture', DAY, noon)


def test_absent_exact_session_is_not_replaced_by_another_date(isolated_inputs):
    name, noon, _ = generation(isolated_inputs)
    with pytest.raises(ValueError): load_inputs(name, '2026-10-26', noon)


@pytest.mark.parametrize('field,value', [('date', '2026-10-27'), ('trade_date', '2026-10-29'), ('quote_date', None), ('price_basis', 'qfq')])
def test_explicit_wrong_noon_day_or_basis_never_supplies_close_daily_return(isolated_inputs, field, value):
    name, noon, _ = generation(isolated_inputs)
    noon['stocks'][0][field] = value; noon['indices'][0][field] = value
    content, _ = load_inputs(name, DAY, noon)
    assert content['stocks'][0]['close'] == content['indices'][0]['close'] == 11.
    assert content['stocks'][0]['change_pct'] is None and content['indices'][0]['change_pct'] is None


def test_corrupt_supplement_index_is_not_accepted_as_coverage_loss(isolated_inputs, monkeypatch):
    name, noon, _ = generation(isolated_inputs)
    monkeypatch.setattr(market_supplement, 'load_supplement_with_fingerprint', lambda *_a, **_k: ({'quality': 'unknown', 'failure_reason': 'synthetic SHA failed'}, None))
    with pytest.raises(ValueError, match='原件未通过'): load_inputs(name, DAY, noon)


def test_real_sector_artifact_keeps_verified_price_basis_without_members(isolated_inputs, monkeypatch):
    from app.services.sector_history import normalize_sector_history, NORMALIZATION_VERSION, _midnight
    name, noon, _ = generation(isolated_inputs, parts=True)
    code = '881001.TI'; prior = '2026-10-27'
    topic = {'topic_code': code, 'topic_id': 'fuyao-' + code, 'kind': 'industry', 'name': '演示行业'}
    raw = {'code': 0, 'data': {'thscode': code, 'interval': '1d', 'adjust': None, 'item': [
      {'date_ms': _midnight(day), 'thscode': code, 'open_price': 10., 'high_price': close, 'low_price': 9., 'close_price': close, 'turnover': 1000.}
      for day, close in ((prior, 10.), (DAY, 11.))]}}
    sector = normalize_sector_history(raw, topic, DAY, prior)
    record = {'date': DAY, 'source': 'fuyao', 'normalization_version': NORMALIZATION_VERSION, 'items': [sector],
              'complete': True, 'failures': [], 'coverage': {'expected_previous_date': prior}}
    folder = isolated_inputs / 'published/market-supplement'; folder.mkdir()
    target = folder / 'sector-day.json'; target.write_text(core.dumps(record))
    descriptor = {'date': DAY, 'source': 'fuyao', 'normalization_version': NORMALIZATION_VERSION,
                  'artifact': 'sector-day.json', 'sha256': sha256(target.read_bytes()).hexdigest()}
    supplement = {'snapshot_id': name, 'target_date': DAY, 'sector_index_history': {DAY: descriptor}}
    monkeypatch.setattr(market_supplement, 'load_supplement_with_fingerprint', lambda *_a, **_k: (supplement, ('fixture', '', name, 'a'*64)))
    content, origin = load_inputs(name, DAY, noon)
    assert len(content['sectors']) == 1
    row = content['sectors'][0]
    assert row['price_basis'] == 'none' and row['amount_unit'] == row['currency'] == 'CNY'
    assert row['close'] == 11 and row['change_pct'] == pytest.approx(10)
    assert 'members' not in row
    assert any(row['role'] == 'sector-day-projection' for row in origin['objects'])
    target.write_bytes(target.read_bytes() + b'corruption')
    with pytest.raises(ValueError): load_inputs(name, DAY, noon)
