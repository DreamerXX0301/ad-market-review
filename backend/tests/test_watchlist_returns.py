"""Personal-pool returns never borrow tracking event prices or latest backfills."""
from copy import deepcopy
import sqlite3
from pathlib import Path

import pytest

from test_watchlist import personal, add
from test_workspace import ws, make_strategy, add_one, advance
from app import core
from app.services import watchlist
from app.services.tracking_quotes import clear_tracking_quote_cache
from app.services.workspace_common import e8, js

SYMBOL='600001.SH'


def close(market,index):
    return next(row['close'] for row in market.data[SYMBOL] if row['date']==market.days[index])


def legacy(market, *, symbol=SYMBOL, added_index=71, round_=None, stamp=1):
    with core.connect() as connection:
        connection.execute('''INSERT INTO watchlist_item(instrument_id,source_kind,source_name,
            observed_date,added_business_date,tracking_round_id,tracking_entry_date,event_price_e8,event_basis_json,created_at_ms)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (symbol,'tracking' if round_ else 'stock','legacy-fixture',market.days[69],market.days[added_index],
             round_['round_id'] if round_ else None,round_['entry_business_date'] if round_ else None,
             e8(round_['entry_price']) if round_ else None,js({'price_basis':'none'}) if round_ else '{}',stamp))


def test_add_freezes_its_own_business_day_close_and_duplicate_never_resets(personal,monkeypatch):
    market,client=personal
    _,original=add_one(client,make_strategy(client))
    advance(market,71)
    created=add(client,tracking_round_id=original['round_id'],watchlist_entry_price=999,event_close=888)
    assert created.status_code==200,created.text
    row=created.json()['item']
    assert row['event_close']==original['entry_price']==close(market,70)
    assert row['watchlist_entry_price']==close(market,71)
    assert row['watchlist_entry_date']==market.days[71]
    assert client.get('/api/v1/watchlist').json()['items'][0]['watchlist_change_pct']==0
    advance(market,74)
    listed=client.get('/api/v1/watchlist').json()['items'][0]
    assert listed['watchlist_change_pct']==pytest.approx((close(market,74)/close(market,71)-1)*100)
    assert listed['watchlist_change_pct']!=pytest.approx((close(market,74)/original['entry_price']-1)*100)
    monkeypatch.setattr(watchlist,'_entry_baselines',lambda *args:pytest.fail('duplicate collection must not re-read or rebase'))
    repeated=add(client,tracking_round_id=original['round_id']).json()
    assert repeated['already_exists']
    assert repeated['item']['watchlist_entry_price']==row['watchlist_entry_price']
    assert repeated['item']['watchlist_entry_basis']==row['watchlist_entry_basis']
    assert repeated['item']['created_at_ms']==row['created_at_ms']


def test_manual_historical_origin_date_does_not_move_personal_added_baseline(personal):
    market,client=personal
    row=add(client,source_kind='sector',observed_date=market.days[60]).json()['item']
    assert row['observed_date']==market.days[60]
    assert row['watchlist_entry_date']==market.entry
    assert row['watchlist_entry_price']==close(market,70)
    assert row['event_close'] is None


@pytest.mark.parametrize('case',['missing','duplicate','zero_volume','invalid_price'])
def test_missing_added_quote_never_uses_tracking_anchor_or_current_price(personal,case):
    market,client=personal
    _,original=add_one(client,make_strategy(client))
    advance(market,71)
    added=next(row for row in market.data[SYMBOL] if row['date']==market.days[71])
    if case=='missing':market.data[SYMBOL].remove(added)
    elif case=='duplicate':market.data[SYMBOL].append(deepcopy(added))
    elif case=='zero_volume':added['volume']=0
    else:added['close']=None
    result=add(client,tracking_round_id=original['round_id'])
    assert result.status_code==200,result.text
    saved=result.json()['item']
    assert saved['event_close']==original['entry_price']
    assert saved['watchlist_entry_price'] is None
    advance(market,74)
    row=client.get('/api/v1/watchlist').json()['items'][0]
    assert row['latest_price'] is not None
    assert row['watchlist_change_pct'] is None
    assert row['watchlist_change_state']=='unknown'
    assert market.days[71] in row['watchlist_change_reason']


@pytest.mark.parametrize('evidence',[None,{}, {SYMBOL:[{'date':'not-a-date'}]}])
def test_unknown_action_coverage_keeps_raw_cross_date_return_unknown(personal,monkeypatch,evidence):
    market,client=personal
    assert add(client).status_code==200
    advance(market,74)
    monkeypatch.setattr(market,'actions_for_instruments',lambda symbols:evidence)
    row=client.get('/api/v1/watchlist').json()['items'][0]
    assert row['watchlist_entry_price']==close(market,70)
    assert row['watchlist_change_pct'] is None and '公司行动' in row['watchlist_change_reason']


def test_action_after_added_is_unknown_but_entry_date_action_does_not_rebase_it(personal,monkeypatch):
    market,client=personal
    assert add(client).status_code==200
    advance(market,74)
    monkeypatch.setattr(market,'actions_for_instruments',lambda symbols:{SYMBOL:[{'date':market.days[72]}]})
    row=client.get('/api/v1/watchlist').json()['items'][0]
    assert row['watchlist_change_pct'] is None
    monkeypatch.setattr(market,'actions_for_instruments',lambda symbols:{SYMBOL:[{'date':market.days[70]}]})
    row=client.get('/api/v1/watchlist').json()['items'][0]
    assert row['watchlist_change_pct']==pytest.approx((close(market,74)/close(market,70)-1)*100)


@pytest.mark.parametrize('case',['missing','duplicate','zero_volume'])
def test_invalid_current_quote_keeps_personal_return_unknown(personal,case):
    market,client=personal
    assert add(client).status_code==200
    advance(market,74)
    current=next(row for row in market.data[SYMBOL] if row['date']==market.days[74])
    if case=='missing':market.data[SYMBOL].remove(current)
    elif case=='duplicate':market.data[SYMBOL].append(deepcopy(current))
    else:current['volume']=0
    row=client.get('/api/v1/watchlist').json()['items'][0]
    assert row['latest_price'] is None and row['watchlist_change_pct'] is None


def test_legacy_backfill_resolves_original_added_date_once_outside_write_transaction(personal,monkeypatch):
    market,client=personal
    _,original=add_one(client,make_strategy(client))
    advance(market,75)
    legacy(market,round_=original)
    batch=market.history_for_instruments;calls=[]
    def counted(symbols,start=None,end=None):
        with core.connect() as other:
            other.execute('BEGIN IMMEDIATE');other.rollback()
        calls.append((symbols,start,end))
        return batch(symbols,start=start,end=end)
    monkeypatch.setattr(market,'history_for_instruments',counted)
    row=client.get('/api/v1/watchlist').json()['items'][0]
    assert row['watchlist_entry_price']==close(market,71)
    assert row['event_close']==close(market,70)
    assert row['latest_price']==close(market,75)
    assert row['watchlist_change_pct']==pytest.approx((close(market,75)/close(market,71)-1)*100)
    assert len(calls)==2
    assert calls[0]==([SYMBOL],market.days[71],market.days[71])
    with core.connect() as connection:
        frozen=connection.execute('SELECT watchlist_entry_basis_json FROM watchlist_item').fetchone()[0]
    again=client.get('/api/v1/watchlist').json()['items'][0]
    assert again['watchlist_entry_basis']==row['watchlist_entry_basis']
    with core.connect() as connection:
        assert connection.execute('SELECT watchlist_entry_basis_json FROM watchlist_item').fetchone()[0]==frozen
    assert sum(start==end==market.days[71] for _,start,end in calls)==1


def test_unknown_legacy_baseline_retries_only_after_published_generation_changes(personal,monkeypatch,tmp_path):
    market,client=personal
    advance(market,75);legacy(market)
    generation={'value':'market-a'}
    monkeypatch.setattr(market,'status',lambda:{'snapshot_id':generation['value']},raising=False)
    monkeypatch.setattr(market,'data_dir',tmp_path,raising=False)
    original=next(row for row in market.data[SYMBOL] if row['date']==market.days[71])
    market.data[SYMBOL].remove(original)
    batch=market.history_for_instruments;calls=[]
    def counted(symbols,start=None,end=None):
        calls.append((start,end));return batch(symbols,start=start,end=end)
    monkeypatch.setattr(market,'history_for_instruments',counted)
    clear_tracking_quote_cache()
    assert client.get('/api/v1/watchlist').json()['items'][0]['watchlist_entry_price'] is None
    assert client.get('/api/v1/watchlist').json()['items'][0]['watchlist_entry_price'] is None
    assert calls.count((market.days[71],market.days[71]))==1
    market.data[SYMBOL].append(original);generation['value']='market-b'
    row=client.get('/api/v1/watchlist').json()['items'][0]
    assert row['watchlist_entry_price']==original['close']
    assert row['watchlist_change_pct'] is not None
    assert calls.count((market.days[71],market.days[71]))==2


@pytest.mark.parametrize('available',[True,False])
def test_old_same_generation_unknown_receipt_is_retried_once_with_new_method(personal,monkeypatch,available):
    market,client=personal;advance(market,75);legacy(market)
    monkeypatch.setattr(market,'status',lambda:{'snapshot_id':'same-generation'},raising=False)
    with core.connect() as connection:
        connection.execute('UPDATE watchlist_item SET watchlist_entry_basis_json=?',
            (js({'market_snapshot_id':'same-generation','method':'exact-published-added-session-close-v1','state':'unknown','reason':'old transient calendar gap'}),))
    if not available:
        market.data[SYMBOL]=[row for row in market.data[SYMBOL] if row['date']!=market.days[71]]
    original=market.history_for_instruments;reads=[]
    def read(symbols,start=None,end=None):
        reads.append((start,end));return original(symbols,start=start,end=end)
    monkeypatch.setattr(market,'history_for_instruments',read)
    first=client.get('/api/v1/watchlist').json()['items'][0]
    assert (first['watchlist_entry_price'] is not None) is available
    assert first['watchlist_entry_basis']['method']==watchlist.ENTRY_BASELINE_METHOD
    second=client.get('/api/v1/watchlist').json()['items'][0]
    assert second['watchlist_entry_basis']==first['watchlist_entry_basis']
    assert reads.count((market.days[71],market.days[71]))==1
    assert second['created_at_ms']==1 and second['added_business_date']==market.days[71]


def test_method_upgrade_never_revalidates_or_rewrites_a_successful_old_baseline(personal,monkeypatch):
    market,client=personal;legacy(market,added_index=70)
    basis={'market_snapshot_id':'old-generation','method':'exact-published-added-session-close-v1','price_basis':'none','date':market.entry}
    with core.connect() as connection:
        connection.execute('UPDATE watchlist_item SET watchlist_entry_price_e8=?,watchlist_entry_basis_json=?',(e8(close(market,70)),js(basis)))
    monkeypatch.setattr(watchlist,'_entry_baselines',lambda *args:pytest.fail('successful v1 baseline is immutable'))
    row=client.get('/api/v1/watchlist').json()['items'][0]
    assert row['watchlist_entry_price']==close(market,70)
    assert row['watchlist_entry_basis']==basis and row['watchlist_change_pct']==0


def test_successful_baseline_cannot_be_rewritten_but_remove_and_readd_starts_a_new_record(personal):
    market,client=personal
    first=add(client).json()['item']
    with pytest.raises(sqlite3.IntegrityError,match='immutable'):
        with core.connect() as connection:
            connection.execute('UPDATE watchlist_item SET watchlist_entry_price_e8=?',(e8(999),))
    advance(market,74)
    assert client.delete('/api/v1/watchlist/'+SYMBOL).json()['removed']
    second=add(client).json()['item']
    assert second['watchlist_entry_date']!=first['watchlist_entry_date']
    assert second['watchlist_entry_price']==close(market,74)


def test_add_rejects_clock_change_during_entry_price_read(personal,monkeypatch):
    market,client=personal;batch=market.history_for_instruments
    def publishing(symbols,start=None,end=None):
        values=batch(symbols,start=start,end=end);advance(market,71);return values
    monkeypatch.setattr(market,'history_for_instruments',publishing)
    response=add(client)
    assert response.status_code==409
    assert client.get('/api/v1/watchlist/ids').json()['total']==0


def test_legacy_entry_reads_use_bounded_batches_not_full_history(personal,monkeypatch):
    market,client=personal;advance(market,75)
    symbols=[f'{600100+index:06}.SH' for index in range(300)]
    for index,symbol in enumerate(symbols):
        market.data[symbol]=deepcopy(market.data[SYMBOL])
        legacy(market,symbol=symbol,stamp=index+1)
    batch=market.history_for_instruments;calls=[]
    def counted(symbols,start=None,end=None):
        calls.append((len(symbols),start,end));return batch(symbols,start=start,end=end)
    monkeypatch.setattr(market,'history_for_instruments',counted)
    row=client.get('/api/v1/watchlist').json()
    assert row['total']==300
    baselines=[call for call in calls if call[1]==call[2]==market.days[71]]
    assert len(baselines)==3 and max(call[0] for call in baselines)<=watchlist.ENTRY_BATCH_SIZE
    assert all(item['watchlist_entry_price']==close(market,71) for item in row['items'])


def test_schema_013_preserves_legacy_origin_without_guessing_prices_and_freezes_success():
    connection=sqlite3.connect(':memory:');connection.row_factory=sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    directory=Path(__file__).resolve().parents[1]/'app'/'migrations'
    for migration in sorted(directory.glob('[0-9]*.sql')):
        if migration.name<'013':connection.executescript(migration.read_text())
    connection.execute('''INSERT INTO watchlist_item(instrument_id,source_kind,source_name,observed_date,
        added_business_date,event_basis_json,created_at_ms) VALUES(?,?,?,?,?,?,?)''',
        (SYMBOL,'stock','legacy','2026-10-20','2026-10-21','{}',1))
    original=dict(connection.execute('SELECT * FROM watchlist_item').fetchone())
    connection.executescript((directory/'013_watchlist_entry_return.sql').read_text())
    row=dict(connection.execute('SELECT * FROM watchlist_item').fetchone())
    assert {key:row[key] for key in original}==original
    assert row['watchlist_entry_price_e8'] is None and row['watchlist_entry_basis_json']=='{}'
    connection.execute('UPDATE watchlist_item SET watchlist_entry_price_e8=?,watchlist_entry_basis_json=?',
                       (e8(23.47),js({'date':'2026-10-21','price_basis':'none'})))
    with pytest.raises(sqlite3.IntegrityError,match='immutable'):
        connection.execute('UPDATE watchlist_item SET watchlist_entry_price_e8=?',(e8(23.25),))
    assert connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    connection.close()
