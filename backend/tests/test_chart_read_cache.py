"""On-demand views reuse only an independently verified published basis."""
import json
import os
import shutil

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pandas as pd
import pytest

from app.analytics.market import hash_file
from app.routers import platform
from app.services import chart_read_cache, market_service, sector_charts
from test_market_compact_reads import declared_condition_market, retained_market
from test_sector_charts import CODE, DAY, collect, local


def test_instrument_route_hits_after_first_open_and_rebuilds_after_publish(retained_market, monkeypatch):
    market, files = declared_condition_market(retained_market)
    root, days, symbols = retained_market
    monkeypatch.setenv('SHIXU_DATA_DIR', str(root))
    monkeypatch.setattr(market_service, 'get_market', lambda: market)
    count = []
    original = market.chart

    def counted(*args, **kwargs):
        count.append(market.status()['snapshot_id'])
        return original(*args, **kwargs)

    monkeypatch.setattr(market, 'chart', counted)
    app = FastAPI()
    app.include_router(platform.router)
    url = '/api/v1/instruments/' + symbols[0] + '/chart'
    with TestClient(app) as client:
        first = client.get(url)
        assert first.status_code == 200
        with monkeypatch.context() as guard:
            def no_reverify():
                pytest.fail('unchanged chart source should reuse the prior SHA check')

            guard.setattr(market, 'verify_condition_sources', no_reverify)
            assert client.get(url, params={'as_of': days[-1]}).json() == first.json()
        assert count == ['fixture']
        assert client.get(url, params={'period': 'W'}).json()['period'] == 'W'
        assert count == ['fixture', 'fixture']

        previous = root / 'published/fixture'
        following = root / 'published/replacement'
        shutil.copytree(previous, following)
        prices = pd.read_parquet(following / 'daily.parquet')
        prices['close'] += .25
        prices.to_parquet(following / 'daily.parquet', index=False)
        head = market.status()['manifest']['summary']
        head['snapshot_id'] = 'replacement'
        head['files'] = []
        for old in files:
            path = following / (root / old['path']).name
            head['files'].append({**old, 'path': str(path.relative_to(root)),
                                  'bytes': path.stat().st_size, 'sha256': hash_file(path)})
        (following / 'manifest.json').write_text(json.dumps({'summary': head}))
        market._publish_head(root / 'published', head)
        replacement = client.get(url)
        assert replacement.status_code == 200
        assert replacement.json()['bars'][-1]['close'] == first.json()['bars'][-1]['close'] + .25
        assert replacement.json()['snapshot_id'] == 'replacement'
        assert count[-1] == 'replacement'
        assert len(count) == 3
        assert client.get(url).json() == replacement.json()
        assert len(count) == 3

        source = following / 'daily.parquet'
        raw = source.read_bytes()
        old_stat = source.stat()
        source.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        os.utime(source, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        with pytest.raises(ValueError, match='SHA'):
            client.get(url)
    market.close()


def test_partitioned_chart_cache_checks_for_new_higher_priority_price_file(retained_market):
    market, _ = declared_condition_market(retained_market, partitioned=True)
    root, days, symbols = retained_market
    day = days[-1]
    status = market.status()
    build = lambda: market.chart(symbols[0], as_of=day)
    assert chart_read_cache.instrument_projection(market, status, day, symbols[0], build)['bars']
    (root / 'published/fixture/daily.parquet').write_bytes(b'new legacy file cannot bypass manifest')
    with pytest.raises(ValueError, match='大小|SHA'):
        chart_read_cache.instrument_projection(market, status, day, symbols[0], build)
    market.close()


def test_recent_session_limit_excludes_future_calendar_dates():
    dates = [f'2026-10-{day:02}' for day in range(1, 20)]
    assert chart_read_cache.recent_chart_day(dates, dates[9], dates[3])
    assert not chart_read_cache.recent_chart_day(dates, dates[9], dates[2])
    assert not chart_read_cache.recent_chart_day(dates, dates[9], dates[10])


def test_sector_older_views_keep_source_history_without_reusing_projection(local, monkeypatch):
    market, adapter, published = local
    collect(local)
    value = sector_charts.read_sector_chart(market, CODE, DAY)
    assert value['available']
    requests = len(adapter.calls)
    reads = []
    original = sector_charts._read_chart_cache

    def counted(*args, **kwargs):
        reads.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(sector_charts, '_read_chart_cache', counted)
    assert sector_charts.read_sector_chart(market, CODE, DAY) == value
    assert reads == []
    market.days += pd.bdate_range('2026-10-21', periods=7).strftime('%Y-%m-%d').tolist()
    market.state.update(data_end=market.days[-1], snapshot_id='replacement')
    for _ in range(2):
        historical = sector_charts.read_sector_chart(market, CODE, DAY)
        assert historical['available'] and historical['bars'] == value['bars']
    assert len(reads) == 2
    assert len(adapter.calls) == requests
    assert list((published / 'market-supplement/accepted/fuyao/sector-chart').glob('*.json'))


def test_recent_projected_sector_chart_yields_to_new_exact_evidence(local, monkeypatch):
    market, adapter, _ = local
    collect(local)
    day = market.days[-2]
    projected = sector_charts.read_sector_chart(market, CODE, day)
    assert projected['projected_from_later_cache']
    with monkeypatch.context() as guard:
        def no_recompute(*args, **kwargs):
            pytest.fail('warm projected view must reuse its verified computation')

        guard.setattr(sector_charts, '_read_chart_cache', no_recompute)
        assert sector_charts.read_sector_chart(market, CODE, day) == projected

    # The published main market id and later pointer both stay the same.
    plan = sector_charts.plan_sector_chart(market, CODE, day)
    assert sector_charts.collect_sector_chart(market, plan, adapter)['available']
    exact = sector_charts.read_sector_chart(market, CODE, day)
    assert exact['available'] and not exact['projected_from_later_cache']
    assert exact['cache_as_of'] == day
    assert exact['bars'] == projected['bars']
