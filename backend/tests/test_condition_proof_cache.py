"""Pure technical proofs survive process boundaries without runtime decisions."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import zlib

import pytest

from app.analytics.boll_conditions import VERSION as BOLL_VERSION, config_key
from app.analytics.higher_lows import VERSION as HIGHER_VERSION
from app.analytics.indicators import INDICATOR_VERSION
from app.core import data_dir
from app.domain.conditions import prepare_metric_bars
from app.domain.strategy_prices import prepare_condition_prices, price_cache_key
from app.services import condition_basis_cache as basis, condition_proof_cache as cache, strategy_periods
from test_boll_signal_integration import breakout_rows, config


@pytest.fixture
def scope(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('SHIXU_WORK_DIR', str(tmp_path / 'work'))
    monkeypatch.setattr(cache, '_stats', {'hits': 0, 'misses': 0, 'stored': 0, 'errors': 0})
    basis.clear_cache(); strategy_periods.clear_metric_cache()
    day = '2026-10-23'; symbol = '600001.SH'; source = 'a' * 64
    key = (str(data_dir()), ('fixed-market', source), symbol, day, 'D',
           INDICATOR_VERSION, 'volume-comparison-v1', 'forward', day,
           price_cache_key(symbol, day, 'forward', {'verified_closure_sha256': source}))
    yield key
    basis.clear_cache(); strategy_periods.clear_metric_cache()


def ordinary(key):
    return {'close': 12.5, 'ma60': 11.4, 'macd_dif': .1, 'macd_dea': .02,
            'observed_date': key[3], 'indicator_warmup_bars': 90,
            'topic_ids': None, 'board': None, 'is_st': None,
            'tracking_change_pct': None, 'trading_age': None,
            'macd_cross': None, 'macd_cross_reason': '缺少前一根交叉证据',
            'macd_cross_evidence': {'required_warmup_bars': 36, 'source_dates': [key[3]]}}


def _child(script, *arguments):
    environment = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    return subprocess.run([sys.executable, '-c', script, *arguments], capture_output=True,
                          text=True, check=True, env=environment, timeout=15)


def test_ordinary_higher_lows_and_boll_are_shared_across_real_processes(scope):
    key = scope
    values = {
        'ordinary': ordinary(key),
        'higher_lows': {'version': HIGHER_VERSION, 'period': 'D', 'covered_periods': 90,
                        'recent_lows': [{'date': '2026-10-01', 'price': 10.1}]},
        'boll': {'version': BOLL_VERSION, 'period': 'D', 'result': None,
                 'reason': '收口排名窗口未覆盖', 'evidence': {'last_blocker': None}, 'config': config()},
    }
    parameter = (BOLL_VERSION, config_key(config()))
    assert cache.store(key, 'ordinary', values['ordinary'])
    assert cache.store(key, 'higher_lows', values['higher_lows'], HIGHER_VERSION)
    assert cache.store(key, 'boll', values['boll'], parameter)
    script = '''import json,sys
from app.services import condition_proof_cache as cache
value=json.loads(sys.argv[1]); value[1]=tuple(value[1]); key=tuple(value)
result={}
for kind,parameter in [('ordinary',None),('higher_lows',sys.argv[2]),('boll',json.loads(sys.argv[3]))]:
 result[kind]=cache.load(key,kind,parameter)
assert all(value is not None for value in result.values())
result['ordinary']['close']+=1
assert cache.store(key,'ordinary',result['ordinary'])
print(json.dumps(result,ensure_ascii=False,sort_keys=True))
'''
    result = json.loads(_child(script, json.dumps(key), HIGHER_VERSION, json.dumps(parameter)).stdout)
    assert result['higher_lows'] == values['higher_lows']
    assert result['boll'] == values['boll']
    assert result['ordinary'] == {**values['ordinary'], 'close': values['ordinary']['close'] + 1}
    assert cache.load(key, 'ordinary') == result['ordinary'], 'parent observes atomic child cache publication'


def test_genuine_dwm_metric_composition_restores_all_components_without_seed_recompute(scope):
    rows, calendar = breakout_rows(160); day = calendar[-1]
    source = {'verified_closure_sha256': scope[1][1]}; symbol = scope[2]
    prices = prepare_condition_prices(rows, [], calendar, day, 'forward', instrument_id=symbol,
        actions_coverage={'known': True, 'source_token': source, 'snapshot_id': scope[1][0]})
    for lane in ('raw_rows', 'price_rows'):
        prices[lane] = prepare_metric_bars(prices[lane])
    options = {'instrument_id': symbol, 'generation': scope[1], 'price_context': prices,
               'include_higher_lows': True, 'boll_configs': [config()]}
    expected = {period: strategy_periods.cached_metrics(rows, day, calendar, period, **options)
                for period in ('D', 'W', 'M')}
    script = '''import json,sys
from app.services import strategy_periods as periods
from app.domain.strategy_prices import price_cache_key
info=json.loads(sys.argv[1]); generation=tuple(info['generation'])
prices={'requested_price_basis':'forward','anchor_date':info['day'],
 'cache_key':price_cache_key(info['symbol'],info['day'],'forward',{'verified_closure_sha256':generation[1]})}
periods.metrics_for=lambda *a,**k:(_ for _ in ()).throw(AssertionError('full seed recomputation forbidden'))
request=periods.metric_request(True,[info['config']])
result={period:periods.cached_metrics_if_ready(info['day'],period,instrument_id=info['symbol'],generation=generation,request=request,price_context=prices) for period in ('D','W','M')}
assert all(value is not None for value in result.values())
print(json.dumps(result,ensure_ascii=False,sort_keys=True))
'''
    actual = json.loads(_child(script, json.dumps({'generation': scope[1], 'symbol': symbol, 'day': day, 'config': config()})).stdout)
    assert actual == expected


@pytest.mark.parametrize('boundary', ['root', 'generation', 'closure', 'symbol', 'day', 'period', 'algorithm', 'volume_version', 'basis', 'anchor', 'price_key'])
def test_every_technical_identity_boundary_isolated(scope, monkeypatch, tmp_path, boundary):
    key = scope
    assert cache.store(key, 'ordinary', ordinary(key))
    changed = list(key)
    if boundary == 'root':
        monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path / 'other-data'))
    elif boundary == 'generation': changed[1] = ('other-market', key[1][1])
    elif boundary == 'closure':
        changed[1] = (key[1][0], 'b' * 64)
        changed[9] = price_cache_key(key[2], key[3], 'forward', {'verified_closure_sha256': 'b' * 64})
    elif boundary == 'symbol':
        changed[2] = '600002.SH'
        changed[9] = price_cache_key(changed[2], key[3], 'forward', {'verified_closure_sha256': key[1][1]})
    elif boundary == 'day':
        changed[3] = changed[8] = '2026-10-22'
        changed[9] = price_cache_key(key[2], changed[3], 'forward', {'verified_closure_sha256': key[1][1]})
    elif boundary == 'period': changed[4] = 'W'
    elif boundary == 'algorithm': changed[5] = 'changed-indicator-semantics'
    elif boundary == 'volume_version': changed[6] = 'changed-volume-semantics'
    elif boundary == 'basis': changed = changed[:7]
    elif boundary == 'anchor': changed[8] = '2026-10-22'
    elif boundary == 'price_key': changed[9] = 'bad-proof'
    assert cache.load(tuple(changed), 'ordinary') is None


def test_component_algorithm_and_boll_config_do_not_leak(scope, monkeypatch):
    assert cache.store(scope, 'ordinary', ordinary(scope))
    assert cache.store(scope, 'higher_lows', {'version': HIGHER_VERSION}, HIGHER_VERSION)
    parameter = (BOLL_VERSION, config_key(config()))
    assert cache.store(scope, 'boll', {'version': BOLL_VERSION, 'result': True}, parameter)
    assert cache.load(scope, 'higher_lows', 'changed-higher-algorithm') is None
    assert cache.load(scope, 'boll', ('changed-boll-algorithm', parameter[1])) is None
    assert cache.load(scope, 'boll', (BOLL_VERSION, config_key(config(deviations=3)))) is None
    assert cache.load(scope, 'ordinary', 'unexpected-parameter') is None
    monkeypatch.setattr(basis, 'NORMALIZATION_VERSION', 'changed-normalization')
    assert cache.load(scope, 'ordinary') is None


def test_load_returns_private_nested_values_and_store_snapshots_input(scope):
    value = ordinary(scope); expected = deepcopy(value)
    assert cache.store(scope, 'ordinary', value)
    value['close'] = -1; value['macd_cross_evidence']['source_dates'].append('future')
    first = cache.load(scope, 'ordinary')
    assert first == expected
    first['close'] = -2; first['macd_cross_evidence']['source_dates'].clear()
    assert cache.load(scope, 'ordinary') == expected


@pytest.mark.parametrize('field,value', [
    ('decision', 'matched'), ('checks', {'result': True}), ('policy', {'expiry': 20}),
    ('current_topics', ['today']), ('topic_ids', ['today']), ('_complete_topic_ids', ['today']),
    ('tracking_change_pct', 8.2), ('trading_age', 10), ('board', 'main'), ('is_st', False),
])
def test_runtime_decisions_memberships_identity_and_policy_are_not_cached(scope, field, value):
    assert not cache.store(scope, 'ordinary', {**ordinary(scope), field: value})
    assert cache.load(scope, 'ordinary') is None


def test_corruption_wrong_scope_trailing_stream_and_limits_are_misses(scope, monkeypatch):
    assert cache.store(scope, 'ordinary', ordinary(scope))
    _, identity = cache._scope(scope, 'ordinary', None)
    original = basis._get_disk(identity)
    for bad in ((b'bad', original[1], original[2]), (original[0] + b'trailer', original[1], original[2]),
                (original[0], 'f' * 64, original[2]), (original[0], original[1], cache.MAX_PLAIN + 1)):
        with monkeypatch.context() as patch:
            patch.setattr(basis, '_get_disk', lambda *_: bad)
            assert cache.load(scope, 'ordinary') is None
    payload = json.loads(zlib.decompress(original[0])); payload['scope'] += '-changed'
    plain = json.dumps(payload).encode()
    with monkeypatch.context() as patch:
        patch.setattr(basis, '_get_disk', lambda *_: (zlib.compress(plain), sha256(plain).hexdigest(), len(plain)))
        assert cache.load(scope, 'ordinary') is None
    with monkeypatch.context() as patch:
        patch.setattr(cache, 'MAX_PLAIN', 10)
        assert cache.load(scope, 'ordinary') is None
        assert not cache.store(scope, 'ordinary', ordinary(scope))
    assert cache.load(scope, 'ordinary') == ordinary(scope)


def test_actual_corrupt_cache_database_is_optional_failure(scope):
    assert cache.store(scope, 'ordinary', ordinary(scope))
    path = Path(os.environ['SHIXU_WORK_DIR']) / basis._DIRECTORY / basis._FILENAME
    path.write_bytes(b'corrupt derived database, never a source')
    assert cache.load(scope, 'ordinary') is None
    assert not cache.store(scope, 'ordinary', ordinary(scope))


@pytest.mark.parametrize('payload', [None, [], 1, 'invalid', {'scope': 'invalid', 'value': []}])
def test_checksum_valid_but_wrong_json_shape_is_a_cache_miss(scope, monkeypatch, payload):
    plain = json.dumps(payload).encode()
    monkeypatch.setattr(basis, '_get_disk', lambda *_: (zlib.compress(plain), sha256(plain).hexdigest(), len(plain)))
    assert cache.load(scope, 'ordinary') is None


def test_readonly_or_missing_cache_permission_is_optional_failure(scope, monkeypatch):
    def denied(*args, **kwargs): raise PermissionError('readonly disposable work cache')
    monkeypatch.setattr(basis, '_put_disk', denied)
    monkeypatch.setattr(basis, '_get_disk', denied)
    assert not cache.store(scope, 'ordinary', ordinary(scope))
    assert cache.load(scope, 'ordinary') is None
    assert cache.cache_stats()['errors'] == 2


def test_all_sqlite_connections_keep_required_pragmas(scope):
    assert cache.store(scope, 'ordinary', ordinary(scope))
    connection = basis._disk_connection(False)
    try:
        assert connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert connection.execute('PRAGMA recursive_triggers').fetchone()[0] == 1
        assert connection.execute('PRAGMA busy_timeout').fetchone()[0] > 0
    finally:
        connection.close()
