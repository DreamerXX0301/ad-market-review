"""Byte-for-byte compatibility with the released typed-column wire protocol."""
from array import array
from base64 import b64encode
import json
import struct
import sys

import pytest

from app.domain.strategy_prices import compact_source_token, prepare_condition_prices
from app.services import condition_basis_cache as basis
from app.services import forward_history_cache as history
from test_forward_tracking_backtest import ForwardMarket


class LegacyColumns:
    """Frozen v1 encoder oracle, before the homogeneous-column optimization.

    Keep this independent implementation: comparing only decoded values would
    miss type, missing/null-mask, float-bit and column-pool wire regressions.
    """
    def __init__(self):
        self.columns = []
        self.index = {}

    def table(self, rows):
        fields = sorted({field for row in rows for field in row})
        references = []
        for field in fields:
            values = [row.get(field, basis._MISSING) for row in rows]
            missing = tuple(index for index, value in enumerate(values) if value is basis._MISSING)
            null = tuple(index for index, value in enumerate(values) if value is None)
            types = {type(value) for value in values if value is not basis._MISSING and value is not None}
            kind = 'float' if types == {float} else 'int' if types == {int} else 'bool' if types == {bool} else 'json'
            present = [0 if value is basis._MISSING or value is None else value for value in values]
            if kind == 'float':
                binary = array('d', present)
                if sys.byteorder != 'little':
                    binary.byteswap()
                raw = binary.tobytes()
            elif kind == 'int' and all(-(1 << 63) <= value < (1 << 63) for value in present):
                binary = array('q', present)
                if sys.byteorder != 'little':
                    binary.byteswap()
                raw = binary.tobytes()
            elif kind == 'bool':
                raw = bytes(present)
            else:
                kind = 'json'
                raw = json.dumps([None if value is basis._MISSING else value for value in values],
                    ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
            identity = kind, raw, missing, null
            reference = self.index.get(identity)
            if reference is None:
                reference = len(self.columns)
                self.index[identity] = reference
                self.columns.append({'kind': kind, 'count': len(values), 'data': b64encode(raw).decode(),
                                     'missing': missing, 'null': null})
            references.append(reference)
        return {'fields': fields, 'columns': references, 'count': len(rows)}


def wire(table, columns):
    return json.dumps({'table': table, 'columns': columns}, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode()


NAN_PAYLOAD = struct.unpack('<d', bytes.fromhex('010000000000f87f'))[0]


@pytest.mark.parametrize('values', [
    [1., 2., -0., 0., 1e-300, 1e300],
    [NAN_PAYLOAD, float('nan'), float('inf'), float('-inf'), -0.],
    [-(1 << 63), (1 << 63)-1, 0, -1, 1],
    [True, False, True, False],
    ['2026-10-23', '中文', '', 'a\\b', '"quote"', '\n'],
    [None, None, None],
    [1 << 64, -(1 << 80), 0],
    [True, 1, False, 0],
    [1., 1, 2., 2],
    [None, 1., -0., None],
    [None, True, False, None],
    [None, 'date', '', None],
    [{'source': ['x', None]}, {'source': ['y']}, {}],
    [[1, 2], [], [None, False]],
    [],
])
def test_homogeneous_and_rare_lanes_keep_exact_original_wire_bytes(values):
    rows = [{'value': value} for value in values]
    legacy, current = LegacyColumns(), basis._Columns()
    old, new = legacy.table(rows), current.table(rows)
    assert wire(new, current.columns) == wire(old, legacy.columns)
    if rows:
        decoded = basis._ReadColumns(current.columns).table(new)
        # The byte oracle above, rather than NaN's non-reflexive equality,
        # verifies the raw IEEE754 payload and signed zero.
        assert [type(row['value']) for row in decoded] == [type(value) for value in values]


def test_missing_keys_nulls_mixed_types_and_column_pool_identity_are_unchanged():
    rows = [{}, {'a': None, 'b': None}, {'a': 1., 'b': 1.},
            {'a': 1, 'b': 1}, {'a': False, 'b': False}, {'a': 'x', 'b': 'x'}]
    legacy, current = LegacyColumns(), basis._Columns()
    old, new = legacy.table(rows), current.table(rows)
    assert wire(new, current.columns) == wire(old, legacy.columns)
    assert new['columns'][0] == new['columns'][1]
    restored = basis._ReadColumns(current.columns).table(new)
    assert restored == rows and 'a' not in restored[0] and restored[1]['a'] is None
    # Reusing a pool across successive tables retains insertion order and IDs.
    more = [{'renamed': row['a']} if 'a' in row else {} for row in rows]
    assert wire(current.table(more), current.columns) == wire(legacy.table(more), legacy.columns)


@pytest.mark.parametrize('values', [[float('nan'), 1], [{'nested': float('inf')}], [object()]])
def test_non_json_rare_fallbacks_reject_the_same_values_as_the_original(values):
    rows = [{'value': value} for value in values]
    with pytest.raises((TypeError, ValueError)) as old:
        LegacyColumns().table(rows)
    with pytest.raises(type(old.value)):
        basis._Columns().table(rows)


def test_direct_column_input_is_identical_to_rows_including_sparse_masks():
    rows = [{}, {'a': None}, {'a': 1., 'b': 'x'}, {'a': -0., 'b': 'y'}]
    fields = sorted({field for row in rows for field in row})
    columns = ([row.get(field, basis._MISSING) for row in rows] for field in fields)
    legacy, current = LegacyColumns(), basis._Columns()
    assert wire(current.table_from_columns(fields, columns, len(rows)), current.columns) == wire(
        legacy.table(rows), legacy.columns)


@pytest.mark.parametrize('fields,columns,count', [
    (['a'], [[1]], 2), (['a'], [], 0), ([], [[]], 0),
    (['a', 'a'], [[], []], 0), (['b', 'a'], [[], []], 0),
    (['a'], [[]], True), (['a'], [[]], -1),
    (['a'], [[]], basis.MAX_ROWS+1), ([None], [[]], 0),
])
def test_direct_columns_fail_closed_on_shape_or_contract_errors(fields, columns, count):
    with pytest.raises(ValueError):
        basis._Columns().table_from_columns(fields, iter(columns), count)


def legacy_shared_input(value):
    columns = LegacyColumns()
    tables = {}
    for lane in ('quotes', 'raw_quotes'):
        tables[lane] = columns.table([{'_cache_day': day,
            **(row if isinstance(row, dict) else {'_cache_none': True})}
            for day, row in sorted(value[lane].items())])
    daily = value['daily']
    fields = sorted({field for metrics in daily.values() for field in metrics})
    blocks = []
    for offset in range(0, len(fields), basis.MAX_FIELDS-1):
        selected = fields[offset:offset+basis.MAX_FIELDS-1]
        blocks.append(columns.table([{'_cache_day': day,
            **{field: metrics[field] for field in selected if field in metrics}}
            for day, metrics in sorted(daily.items())]))
    return {'encoding': 'technical-columns-v1',
            'metadata': {key: item for key, item in value.items() if key not in ('quotes', 'raw_quotes', 'daily')},
            'tables': tables, 'daily_blocks': blocks, 'columns': columns.columns}


@pytest.mark.parametrize('missing_quote', [False, True])
def test_shared_tail_direct_encoding_has_identical_bytes_across_field_blocks(missing_quote):
    days = ['2026-10-20', '2026-10-21', '2026-10-22', '2026-10-23']
    quotes = {day: {'date': day, 'close': 10.+index, 'volume': 100,
                    'corporate_action': False} for index, day in enumerate(days)}
    if missing_quote:
        quotes[days[1]] = None
    daily = {day: {f'metric_{field:03d}': float(field) if field % 3 else None
                   for field in range(150)} for day in days}
    daily[days[-1]]['source_evidence'] = {'sha': ['original'], 'known': False}
    daily[days[1]].pop('metric_002')
    value = {'version': history.VERSION, 'as_of': days[-1], 'snapshot_id': 'synthetic-only',
        'quotes': quotes, 'raw_quotes': quotes, 'daily': daily,
        'invalid_dates': days[1:2] if missing_quote else [], 'action_dates': [],
        'unknown_action_dates': [], 'price_context': {'price_basis': 'forward_event_affine'}}
    expected = legacy_shared_input(value)
    actual = history._encode_shared_input(value)
    assert json.dumps(actual, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode() == json.dumps(
        expected, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    assert len(actual['daily_blocks']) == 2
    assert history._decode_shared_input(actual) == value


def test_full_basis_record_checksum_compressed_bytes_and_input_are_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    market = ForwardMarket(320, 200, 280)
    symbol, day = '600000.SH', market.days[-1]
    token = compact_source_token(market.verify_condition_sources())
    rows = market.data[symbol]
    rows[-1]['unusual_large_integer'] = 1 << 80
    rows[-2]['unusual_float_bits'] = NAN_PAYLOAD
    prices = prepare_condition_prices(rows, market.events[symbol], market.days, day, 'forward',
        instrument_id=symbol, actions_coverage={'known': True, 'source_token': token,
                                              'snapshot_id': 'forward-fixture'})
    key = basis.key_for(symbol, day, 'forward-fixture', token)
    expected_rows = json.dumps(rows, allow_nan=True, separators=(',', ':'))
    current = basis._encode(key, prices)
    with monkeypatch.context() as old:
        old.setattr(basis, '_Columns', LegacyColumns)
        legacy = basis._encode(key, prices)
    assert current == legacy  # Compressed stream, plain SHA256 and plain size.
    assert json.dumps(rows, allow_nan=True, separators=(',', ':')) == expected_rows
