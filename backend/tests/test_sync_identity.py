"""Source-null regressions and independent coverage parity; no live requests."""
from collections import Counter
import random

import pandas as pd
import pytest

from app.services.security_identity import preserve_listing_dates
from app.services.limit_history import annual_coverage
from app.services.sync_service import merge_daily, midnight_ms
from test_sync import transport, start_job


def test_null_listing_date_keeps_only_same_identity_evidence():
    previous = {'600000.SH': {'name': '测试甲', 'list_date': '1999-11-10', 'exchange': 'SH'},
                '000001.SZ': {'name': '旧名称', 'list_date': '1991-04-03', 'exchange': 'SZ'}}
    incoming = [{'thscode': '600000.SH', 'name': '测试甲', 'exchange': 'SH', 'list_date': None},
                {'thscode': '000001.SZ', 'name': '新名称', 'exchange': 'SZ', 'list_date': None}]
    merged, inherited = preserve_listing_dates(incoming, previous, 'market-fixture')
    assert inherited == ['600000.SH']
    assert merged[0]['list_date'] == '1999-11-10'
    assert merged[0]['list_date_evidence']['snapshot_id'] == 'market-fixture'
    assert merged[1]['list_date'] is None and incoming[0]['list_date'] is None
    incoming[0]['list_date'] = '1998-01-01'
    assert preserve_listing_dates(incoming, previous)[0][0]['list_date'] == '1998-01-01'
    previous['600000.SH']['end_date'] = '2020-01-01'
    incoming[0]['list_date'] = None
    assert preserve_listing_dates(incoming, previous)[0][0]['list_date'] is None


@pytest.mark.parametrize(('name', 'old_name'), [
    ('XR伯特利', '伯特利'),
    ('XD美邦股', '美邦股份'),
    ('DR四字名称', '四字名称'),
    ('美邦股份', 'XD美邦股'),
])
def test_null_listing_date_recovers_shanghai_action_short_name(name, old_name):
    symbol = '605033.SH'
    identity = {'thscode': symbol, 'exchange': 'SH', 'asset_type': 'a-share'}
    incoming = {**identity, 'name': name, 'list_date': None}
    previous = {symbol: {**identity, 'name': old_name, 'list_date': '2021-09-16'}}

    rows, inherited = preserve_listing_dates([incoming], previous, 'market-previous')
    assert inherited == [symbol]
    assert rows[0]['list_date'] == '2021-09-16'
    assert rows[0]['name'] == name and incoming['list_date'] is None
    assert rows[0]['list_date_evidence']['snapshot_id'] == 'market-previous'

    incoming['list_date'] = '2021-09-17'
    rows, inherited = preserve_listing_dates([incoming], previous, 'market-previous')
    assert not inherited and rows[0]['list_date'] == '2021-09-17'


@pytest.mark.parametrize(('current_overrides', 'previous_overrides'), [
    ({'name': '美邦股'}, {}),
    ({'name': 'N美邦股'}, {}),
    ({'name': 'C美邦股'}, {}),
    ({'name': 'XD美邦'}, {}),
    ({'name': 'XD美邦股'}, {'name': '美邦股权投资'}),
    ({'name': 'XD美邦股'}, {'name': '美邦服装'}),
    ({'name': 'XD美邦3'}, {'name': '美邦3股'}),
    ({'thscode': '605033.SZ', 'exchange': 'SZ'}, {'thscode': '605033.SZ', 'exchange': 'SZ'}),
    ({'exchange': 'SZ'}, {}),
    ({'asset_type': None}, {}),
    ({}, {'asset_type': 'index'}),
    ({}, {'thscode': '605032.SH'}),
    ({'end_date': '2025-01-01'}, {}),
    ({}, {'end_date': '2025-01-01'}),
    ({}, {'list_date': '2021-09-31'}),
])
def test_null_listing_date_does_not_guess_from_other_short_names_or_identities(current_overrides, previous_overrides):
    identity = {'thscode': '605033.SH', 'exchange': 'SH', 'asset_type': 'a-share'}
    incoming = {**identity, 'name': 'XD美邦股', 'list_date': None, **current_overrides}
    previous = {incoming['thscode']: {**identity, 'name': '美邦股份', 'list_date': '2021-09-16', **previous_overrides}}
    rows, inherited = preserve_listing_dates([incoming], previous)
    assert not inherited and rows[0]['list_date'] is None


def test_unknown_listing_dates_cannot_be_reported_as_zero_missing(tmp_path):
    calendar = ['2026-01-05', '2026-01-06']
    pd.DataFrame([{'thscode': '600000.SH', 'currency': 'CNY', 'interval': '1d', 'adjusted': 'none',
                   'date_ms': midnight_ms(day), 'open_price': 10., 'high_price': 11., 'low_price': 9.,
                   'close_price': 10., 'volume': 100., 'turnover': 1000.} for day in calendar]).to_parquet(tmp_path/'input.parquet')
    master = [{'thscode': '600000.SH', 'list_date': None}, {'thscode': '000001.SZ', 'list_date': '1991-04-03'}]
    result = merge_daily(tmp_path/'input.parquet', None, tmp_path/'out.parquet', calendar[0], calendar[-1], calendar, master)
    assert result['unexplained_missing'] is None
    assert result['confirmed_missing_lower_bound'] == 2
    assert result['identity_unknown_count'] == 1 and result['expected_coverage_state'] == 'unknown'


def test_increment_merge_replaces_revisions_and_keeps_unseen_history(tmp_path):
    calendar=['2026-01-05','2026-01-06','2026-01-07']
    def quote(day,close=10.):
        return {'thscode':'600000.SH','currency':'CNY','interval':'1d','adjusted':'none',
                'date_ms':midnight_ms(day),'open_price':10.,'high_price':12.,'low_price':9.,
                'close_price':close,'volume':100.,'turnover':1000.}
    old=tmp_path/'old.parquet';incoming=tmp_path/'new.parquet';output=tmp_path/'merged.parquet'
    pd.DataFrame([quote(calendar[0]),quote(calendar[1])]).to_parquet(old,index=False)
    pd.DataFrame([quote(calendar[1],11.),quote(calendar[2])]).to_parquet(incoming,index=False)
    master=[{'thscode':'600000.SH','list_date':'2000-01-01'}]
    result=merge_daily(incoming,old,output,calendar[0],calendar[-1],calendar,master)
    merged=pd.read_parquet(output)
    assert result['rows']==3 and result['overlap_rows']==1 and result['revised_overlap_rows']==1
    assert result['unexplained_missing']==0
    assert merged.date_ms.tolist()==[midnight_ms(day) for day in calendar]
    assert merged.close_price.tolist()==[10.,11.,10.]
    # Both inputs are validated before a source revision can replace old keys.
    for duplicated in (incoming,old):
        original=pd.read_parquet(duplicated)
        pd.concat([original,original.iloc[:1]],ignore_index=True).to_parquet(duplicated,index=False)
        with pytest.raises(ValueError,match='重复键'):
            merge_daily(incoming,old,tmp_path/'rejected.parquet',calendar[0],calendar[-1],calendar,master)
        original.to_parquet(duplicated,index=False)
    pd.DataFrame([quote(calendar[1])]).to_parquet(incoming,index=False)
    with pytest.raises(ValueError,match='起止日'):
        merge_daily(incoming,old,tmp_path/'short.parquet',calendar[0],calendar[-1],calendar,master)


def test_indexed_annual_coverage_equals_independent_daily_predicates():
    rng = random.Random(92617)
    symbols = ['600000.SH', '000001.SZ', '600001.SH', '600000.SZ']
    calendar = [f'2026-01-{day:02}' for day in range(5, 29)]
    pools = {}
    for day in calendar:
        if rng.choice([False, False, True]):
            continue
        positives = rng.sample(symbols, rng.randrange(len(symbols)+1))
        raw = list({s[:6] for s in positives} | {rng.choice(symbols)[:6]})
        pools[day] = {'items': [{'instrument_id': s} for s in positives], 'complete': bool(rng.randrange(2)),
                      'pagination_complete': bool(rng.randrange(2)), 'ambiguous_code_rows': rng.randrange(2), 'raw_codes': raw}
    result = annual_coverage({'annual_pools': pools}, calendar, calendar[-1], symbols)
    for symbol in symbols:
        expected = 0
        for day in calendar:
            pool = pools.get(day, {})
            positive = {row['instrument_id'] for row in pool.get('items', [])}
            clean = pool.get('pagination_complete') is True and pool.get('ambiguous_code_rows') == 0 and isinstance(pool.get('raw_codes'), list)
            expected += symbol in positive or (clean and symbol[:6] not in pool['raw_codes'])
        assert result['instrument_coverage'][symbol]['covered_days'] == expected
    assert result['counts'] == dict(Counter(row['instrument_id'] for pool in pools.values() for row in pool['items']))


def test_index_bootstrap_does_not_load_stocks_and_security_ranges_do_not_mix(transport,monkeypatch):
    from app.core import get_market,data_dir
    from app.analytics.market import MarketData
    from app.services.sync_service import execute_sync
    execute_sync(start_job())
    market=MarketData(data_dir(),get_market().status()['snapshot_id'])
    with monkeypatch.context() as patch:
        patch.setattr(market,'_daily_frame',lambda:(_ for _ in ()).throw(AssertionError('bootstrap must not load stocks')))
        quotes=market.index_quotes()
        assert len(quotes)==4 and all(row['date']=='2026-10-19' for row in quotes)
    frame=market._daily_frame()
    for symbol in ('600000.SH','000001.SZ'):
        expected=frame.loc[frame.instrument_id==symbol].to_dict('records')
        assert market.bars(symbol)==expected
        assert market.bars(symbol,'2026-10-16')==[row for row in expected if row['date']<='2026-10-16']
    assert market.bars('999999.SH')==[]
    monthly=market.chart('600000.SH',period='M')
    assert monthly['period']=='M' and len(monthly['bars'])>=35
    assert monthly['bars'][-1]['month_closed'] is False
    assert monthly['bars'][-1]['macd_hist'] is None
    for bar in monthly['bars']:
        same_month=[row for row in market.bars('600000.SH') if row['date'][:7]==bar['date'][:7]]
        assert bar['volume']==sum(row['volume'] for row in same_month)
        assert bar['high']==max(row['high'] for row in same_month)
