"""Independent personal stock pool; reads only published local market data."""
from datetime import date
import math
import re

from app.core import connect, get_market, now_ms
from app.services.tracking_quotes import watchlist_quotes
from app.services.stock_memberships import current_memberships
from app.services.workspace_common import code_info, current, e8, fail, freeze_inputs, identities, js, money, obj, rows

MAX_ITEMS = 2000
ENTRY_BATCH_SIZE = 128
ENTRY_BASELINE_METHOD = 'exact-published-added-session-close-v2'
SOURCE_NAMES = {'stock': '个股详情', 'tracking': '策略跟踪', 'sector': '行业与题材', 'topic': '题材库'}


def _symbol(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', value.strip().upper()):
        fail('需要有效的A股股票代码，例如600519.SH')
    return value.strip().upper()


def _optional_text(value, field, length=200):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > length:
        fail(f'{field}应为不超过{length}字的文本')
    return value.strip() or None


def _day(value):
    if value is None:
        return None
    try:
        if isinstance(value, str) and date.fromisoformat(value).isoformat() == value:
            return value
    except ValueError:
        pass
    fail('观察日期应使用YYYY-MM-DD格式')


def _view(row):
    basis = obj(row.get('event_basis_json'), {})
    entry_basis = obj(row.get('watchlist_entry_basis_json'), {})
    entry_price = money(row.get('watchlist_entry_price_e8'))
    return {key: value for key, value in row.items() if key not in ('event_price_e8', 'event_basis_json', 'watchlist_entry_price_e8', 'watchlist_entry_basis_json')} | {
        'event_close': money(row.get('event_price_e8')),
        'event_date': row.get('tracking_entry_date'),
        'event_price_basis': basis.get('price_basis'),
        'event_baseline_reason': None if row.get('tracking_round_id') else '此自选来自人工添加，没有策略事件基准',
        'watchlist_entry_date': row.get('added_business_date'),
        'watchlist_entry_price': entry_price,
        'watchlist_entry_price_basis': entry_basis.get('price_basis'),
        'watchlist_entry_state': 'ready' if entry_price is not None else 'unknown',
        'watchlist_entry_reason': None if entry_price is not None else entry_basis.get('reason') or '加入自选日的唯一有效原始收盘价尚待核验',
        'watchlist_entry_basis': entry_basis,
    }


def _generation(market, primary):
    reader=getattr(market,'status',None)
    status=reader() if callable(reader) else {}
    return status.get('snapshot_id') or primary


def _entry_baselines(market, saved, calendar, day, generation):
    """Resolve only exact dated pairs from bounded local raw-price batches."""
    result={}
    for offset in range(0,len(saved),ENTRY_BATCH_SIZE):
        batch=saved[offset:offset+ENTRY_BATCH_SIZE]
        requests=[row for row in batch if row['added_business_date'] in calendar and row['added_business_date']<=day]
        reader=getattr(market,'history_for_instruments',None)
        histories=reader(sorted({row['instrument_id'] for row in requests}),
                         start=min(row['added_business_date'] for row in requests),end=max(row['added_business_date'] for row in requests)) if requests and callable(reader) else {}
        for row in batch:
            added=row['added_business_date']
            exact=[quote for quote in histories.get(row['instrument_id'],[]) if quote.get('date')==added]
            quote=exact[0] if len(exact)==1 else {}
            valid=all(not isinstance(quote.get(field),bool) and isinstance(quote.get(field),(int,float))
                      and math.isfinite(quote[field]) and quote[field]>0 for field in ('open','high','low','close','volume'))
            value=quote.get('close') if valid else None
            price=e8(value) if value is not None and value<92233720368 else None
            if price is not None and price<=0:price=None
            reason=None if price is not None else '加入自选日 '+added+' 缺少唯一有效收盘报价，不使用原跟踪事件价或今日价格替代'
            basis={'date':added,'price_basis':'none','market_snapshot_id':generation,'state':'ready' if price is not None else 'unknown',
                   'reason':reason,'captured_at_ms':now_ms(),'method':ENTRY_BASELINE_METHOD}
            result[row['instrument_id']]={'price_e8':price,'basis':basis}
    return result


def _backfill_entries(saved, market, calendar, day, primary):
    """Idempotently repair legacy missing baselines; never rewrite a success."""
    generation=_generation(market,primary)
    pending=[row for row in saved if row.get('watchlist_entry_price_e8') is None
             and (obj(row.get('watchlist_entry_basis_json'),{}).get('market_snapshot_id')!=generation
                  or obj(row.get('watchlist_entry_basis_json'),{}).get('method')!=ENTRY_BASELINE_METHOD)]
    if not pending:return saved
    resolved=_entry_baselines(market,pending,calendar,day,generation)
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if current(connection)!=day or freeze_inputs(connection)[1]!=primary:
            fail('行情业务日或代际已更新，请刷新自选池后核验加入基准',409)
        for row in pending:
            value=resolved[row['instrument_id']]
            updated=connection.execute('''UPDATE watchlist_item SET watchlist_entry_price_e8=?,watchlist_entry_basis_json=?
                WHERE instrument_id=? AND created_at_ms=? AND added_business_date=? AND watchlist_entry_price_e8 IS NULL
                AND watchlist_entry_basis_json=?''',
                (value['price_e8'],js(value['basis']),row['instrument_id'],row['created_at_ms'],row['added_business_date'],row.get('watchlist_entry_basis_json','{}'))).rowcount
            if updated:
                row.update(watchlist_entry_price_e8=value['price_e8'],watchlist_entry_basis_json=js(value['basis']))
        latest=rows(connection,'SELECT * FROM watchlist_item ORDER BY created_at_ms DESC,instrument_id')
    return latest


def _since_added(row, quote, action_dates, day):
    """Only the personal added-date baseline participates in this return."""
    added=row['added_business_date'];base=row.get('watchlist_entry_price');price=quote.get('close')
    def unknown(reason):return {'watchlist_change_pct':None,'watchlist_change_state':'unknown','watchlist_change_reason':reason}
    if base is None:return unknown(row.get('watchlist_entry_reason') or '加入自选日收盘基准待核验')
    if added>day:return unknown('行情观察日早于加入自选日，不能计算加入后涨幅')
    if price is None:return unknown(quote.get('source_reason') or '当前业务日缺少唯一有效收盘价，不使用旧报价替代')
    if row.get('watchlist_entry_price_basis')!='none':return unknown('加入自选价格口径未知，不能直接比较')
    if added!=day:
        if action_dates is None:
            return unknown('加入自选后公司行动覆盖尚未核验，原始价格可比性未知')
        if any(added<event_day<=day for event_day in action_dates):
            return unknown('加入自选后存在公司行动，尚无可核验的冻结基准价格转换')
    return {'watchlist_change_pct':(price/base-1)*100,'watchlist_change_state':'ready','watchlist_change_reason':None}


def add_item(payload):
    symbol = _symbol(payload.get('instrument_id'))
    round_id = _optional_text(payload.get('tracking_round_id'), '跟踪轮次')
    kind = payload.get('source_kind', 'tracking' if round_id else 'stock')
    if not isinstance(kind, str) or kind not in SOURCE_NAMES:
        fail('不支持的自选来源')
    if kind == 'tracking' and not round_id:
        fail('从策略跟踪加入自选时需要原跟踪轮次')
    source_id = _optional_text(payload.get('source_id'), '来源标识')
    source_name = _optional_text(payload.get('source_name'), '来源名称') or SOURCE_NAMES[kind]
    observed = _day(payload.get('observed_date'))
    with connect() as connection:
        day = current(connection)
        primary=freeze_inputs(connection)[1]
        if observed and observed > day:
            fail('来源观察日不能晚于当前已发布行情日')
        observed = observed or day
        previous = connection.execute('SELECT * FROM watchlist_item WHERE instrument_id=?', (symbol,)).fetchone()
        if previous:
            return {'item': _view(dict(previous)), 'added': False, 'already_exists': True}
    # Never perform market reads under the SQLite write reservation.
    master = identities(day)
    identity = master.get(symbol)
    if not identity or identity.get('kind', identity.get('asset_type', 'stock')) not in ('stock', 'equity', 'a_share', 'A'):
        fail('只能将本地已核验的A股股票加入自选，指数和板块请使用板块跟踪')
    market=get_market();generation=_generation(market,primary)
    own_entry=_entry_baselines(market,[{'instrument_id':symbol,'added_business_date':day}],market.calendar(),day,generation)[symbol]
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        previous = connection.execute('SELECT * FROM watchlist_item WHERE instrument_id=?', (symbol,)).fetchone()
        if previous:
            return {'item': _view(dict(previous)), 'added': False, 'already_exists': True}
        if current(connection) != day or freeze_inputs(connection)[1]!=primary:
            fail('行情业务日或代际已更新，请刷新后再加入自选', 409)
        if connection.execute('SELECT count(*) FROM watchlist_item').fetchone()[0] >= MAX_ITEMS:
            fail(f'自选池最多支持{MAX_ITEMS}只股票，请先移除部分自选', 409)
        entry_date = price = None
        basis = {}
        if round_id:
            original = connection.execute('SELECT r.*,s.name strategy_name FROM tracking_round r JOIN strategy s USING(strategy_id) WHERE round_id=?', (round_id,)).fetchone()
            if not original:
                fail('原个股跟踪轮次不存在', 404)
            if original['instrument_id'] != symbol:
                fail('自选股票与跟踪轮次不一致')
            kind = 'tracking'
            source_id = original['strategy_id']
            source_name = original['strategy_name']
            entry_date = original['entry_business_date']
            price = original['entry_price_e8']
            basis = obj(original['entry_basis_json'], {})
            observed = observed or day
        stamp = now_ms()
        connection.execute('''INSERT INTO watchlist_item(instrument_id,source_kind,source_id,source_name,
            observed_date,added_business_date,tracking_round_id,tracking_entry_date,event_price_e8,event_basis_json,created_at_ms,
            watchlist_entry_price_e8,watchlist_entry_basis_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (symbol, kind, source_id, source_name, observed, day, round_id, entry_date, price, js(basis), stamp,own_entry['price_e8'],js(own_entry['basis'])))
        row = dict(connection.execute('SELECT * FROM watchlist_item WHERE instrument_id=?', (symbol,)).fetchone())
    return {'item': _view(row) | code_info(symbol, master), 'added': True, 'already_exists': False}


def list_ids():
    with connect() as connection:
        ids = [row[0] for row in connection.execute('SELECT instrument_id FROM watchlist_item ORDER BY created_at_ms DESC,instrument_id')]
    return {'instrument_ids': ids, 'total': len(ids)}


def list_items(query=''):
    query = query.strip().lower()
    if len(query) > 200:
        fail('搜索内容过长')
    with connect() as connection:
        clock = connection.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone()
        day = clock[0] if clock else None
        saved = rows(connection, 'SELECT * FROM watchlist_item ORDER BY created_at_ms DESC,instrument_id')
        primary=freeze_inputs(connection)[1] if saved else None
    if not saved:
        return {'items': [], 'total': 0, 'matched_count': 0, 'business_date': day}
    market = get_market()
    from app.services.hot_browse import browse_scope, require_scope
    with connect() as connection:
        hot_scope=browse_scope(connection,market,expected_day=day)
    calendar=market.calendar()
    if day:saved=_backfill_entries(saved,market,calendar,day,primary)
    master = identities(day)
    selected = [_view(row) | code_info(row['instrument_id'], master) for row in saved]
    # Reuse the topic browser's current provider + manual-override semantics.
    # Its catalog and overrides are batched; no per-stock API or history reads.
    symbols = {row['instrument_id'] for row in selected}
    membership_data = current_memberships(market, symbols, expected_day=day)
    memberships = membership_data['memberships']
    for row in selected:
        tags = sorted(memberships.get(row['instrument_id'], []), key=lambda item: (item['kind'], item['name'], item['topic_id']))
        row.update(current_topics=tags,
                   current_industries=[tag for tag in tags if tag['kind'] == 'industry'],
                   current_topics_observed_date=day)
    if query:
        selected = [row for row in selected if query in ' '.join([*(str(row.get(key) or '') for key in ('name', 'code', 'instrument_id', 'source_name')), *(tag['name'] for tag in row['current_topics'])]).lower()]
    from app.services.line_alerts import latest_by_stock
    selected_symbols = [row['instrument_id'] for row in selected]
    with connect() as connection:
        alerts = latest_by_stock(connection, selected_symbols, day)
    # Reuse the fixed security inputs. The personal baseline and alert are
    # combined with those inputs afresh for the currently selected items.
    quotes = watchlist_quotes(market, selected_symbols if day else [], calendar, day,
                             require_current=lambda: require_scope(hot_scope, market))
    for row in selected:
        quote = quotes.get((row['instrument_id'], day), {})
        row.update(latest_price=quote.get('close'), today_change_pct=quote.get('change_pct'), quote_date=day,
                   latest_line_alert=alerts.get(row['instrument_id']))
        if quote.get('source_reason'):
            row['quote_reason'] = quote['source_reason']
        row.update(_since_added(row,quote,quote.get('action_dates'),day))
    return {'items': selected, 'total': len(saved), 'matched_count': len(selected), 'business_date': day,'hot_filter_scope':hot_scope,
            'current_topics_scope': membership_data['scope'],
            'baseline_notice': '加入后涨幅以首次加入自选业务日的独立冻结收盘为基准；原跟踪入池日期和事件日收盘单独保留，不参与自选涨幅计算。重复收藏不重置，移除自选不影响策略跟踪。'}


def remove_item(instrument_id):
    symbol = _symbol(instrument_id)
    with connect() as connection:
        deleted = connection.execute('DELETE FROM watchlist_item WHERE instrument_id=?', (symbol,)).rowcount
    return {'instrument_id': symbol, 'removed': bool(deleted)}
