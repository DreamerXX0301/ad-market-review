"""User chart marks, separate from source quotes, signals and strategy rules."""
from datetime import date
import math
import re

from app.core import connect, get_market, new_id, now_ms
from app.services.workspace_common import fail
from app.services import line_alerts
from app.core import digest, dumps
from app.analytics.support_resistance import local_levels, VERSION as LEVEL_VERSION
from app.analytics.indicators import aggregate_weekly
from app.analytics.monthly import aggregate_monthly

COLORS = {'blue', 'amber', 'red', 'green', 'violet'}
MAX_LINES = 64


def _instrument(identifier):
    symbol = str(identifier).strip().upper()
    if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', symbol):
        fail('请选择明确的个股或指数代码')
    market = get_market()
    found = next((item for item in market.instruments(query=symbol, limit=10000)
                  if str(item.get('instrument_id', '')).upper() == symbol), None)
    if not found or found.get('kind', found.get('asset_type', 'stock')) not in ('stock', 'equity', 'a_share', 'A', 'index'):
        fail('划线仅适用于已留存的个股与大盘指数，不将板块保存为个股', 404)
    return symbol, market


def _is_index(symbol, market):
    return any(item.get('kind', item.get('asset_type')) == 'index' and str(item.get('instrument_id', '')).upper() == symbol
               for item in market.instruments(query=symbol, limit=10000))


def _index_rules(index, basis, modes, automatic):
    if index and (basis != 'none' or modes or automatic):
        fail('指数水平线使用原始点位，仅保存显示，不支持个股提醒或自动加入自选')


def _style(payload, previous=None):
    previous = previous or {}
    price = payload.get('price', previous.get('price'))
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not 0 < price <= 100_000_000 or not math.isfinite(price):
        fail('水平线价格需要大于0且不超过1亿元的有限数值')
    label = payload.get('label', previous.get('label', ''))
    if not isinstance(label, str) or len(label.strip()) > 40 or any(ord(char) < 32 for char in label):
        fail('标记名称最多40字，不能包含换行或控制字符')
    color = payload.get('color', previous.get('color', 'blue'))
    if not isinstance(color, str) or color not in COLORS: fail('请选择支持的水平线颜色')
    return float(price), label.strip(), color


def _rule_config(payload, previous=None):
    previous = previous or {}
    kind=payload.get('line_type',previous.get('line_type','reference'))
    if not isinstance(kind,str) or kind not in line_alerts.MODES:fail('请选择参考线、支撑线或压力线')
    modes=payload.get('alert_modes',previous.get('alert_modes',[]))
    if not isinstance(modes,list) or any(not isinstance(value,str) or value not in line_alerts.MODES[kind] for value in modes) or len(modes)!=len(set(modes)):
        fail('提醒模式与水平线类型不匹配或包含重复模式')
    automatic=payload.get('auto_watchlist',previous.get('auto_watchlist',False))
    if not isinstance(automatic,bool):fail('自动加入自选需要是布尔值')
    if kind=='reference' and automatic:fail('参考线不能启用自动自选，请明确选择支撑或压力规则')
    return kind,sorted(modes),automatic


def list_annotations(identifier):
    symbol, _ = _instrument(identifier)
    with connect() as c:
        items = [line_alerts.annotation_view(row) for row in c.execute(
            'SELECT * FROM chart_annotation WHERE instrument_id=? ORDER BY created_at_ms,annotation_id', (symbol,))]
        dates=line_alerts.retention.first_alerts(c,[item['annotation_id'] for item in items])
        for item in items:
            item.update(first_alert_date=dates.get(item['annotation_id']),post_alert_tracking_sessions=line_alerts.retention.SESSIONS)
    return {'instrument_id': symbol, 'items': items, 'total': len(items), 'limit': MAX_LINES}


def create_annotation(identifier, payload):
    symbol, market = _instrument(identifier)
    if not isinstance(payload, dict) or set(payload) - {'price', 'label', 'color', 'price_basis', 'anchor_date', 'line_type', 'alert_modes', 'auto_watchlist'}:
        fail('水平线参数不受支持')
    price, label, color = _style(payload)
    kind,modes,automatic=_rule_config(payload)
    basis = payload.get('price_basis', 'none')
    anchor = payload.get('anchor_date')
    if basis not in ('none', 'forward_event_affine'): fail('水平线价格口径不受支持')
    index = _is_index(symbol, market)
    _index_rules(index, basis, modes, automatic)
    if basis == 'none':
        if anchor is not None: fail('未复权水平线不需要复权锚定日')
    else:
        try: valid = isinstance(anchor, str) and date.fromisoformat(anchor).isoformat() == anchor
        except ValueError: valid = False
        end = market.status().get('data_end')
        if not valid or not end or anchor > end or anchor not in market.calendar():
            fail('前复权水平线需要已发布行情范围内的交易日锚点')
    stamp = now_ms()
    item = {'annotation_id': new_id('line'), 'instrument_id': symbol, 'price': price,
            'label': label, 'color': color, 'price_basis': basis, 'anchor_date': anchor,
            'created_at_ms': stamp, 'updated_at_ms': stamp,'line_type':kind,'alert_modes':modes,'auto_watchlist':automatic,'revision':1,'origin':{},
            'first_alert_date':None,'post_alert_tracking_sessions':line_alerts.retention.SESSIONS}
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        count = c.execute('SELECT COUNT(*) FROM chart_annotation WHERE instrument_id=?', (symbol,)).fetchone()[0]
        if count >= MAX_LINES: fail(f'每个标的最多保存{MAX_LINES}条水平线，请先移除不用的标记', 409)
        clock=c.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone()
        item['effective_business_date']=clock[0] if clock and kind!='reference' and not index else None
        c.execute('INSERT INTO chart_annotation(annotation_id,instrument_id,price,label,color,price_basis,anchor_date,created_at_ms,updated_at_ms,line_type,alert_modes_json,auto_watchlist,revision,effective_business_date,instrument_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (item['annotation_id'],symbol,price,label,color,basis,anchor,stamp,stamp,kind,line_alerts.dumps(modes),int(automatic),1,item['effective_business_date'],'index' if index else 'stock'))
        if modes:line_alerts.enqueue(c, 'line-alert-save:'+item['annotation_id']+':1', 'line-save')
    return item


def update_annotation(identifier, annotation_id, payload):
    symbol, market = _instrument(identifier)
    index = _is_index(symbol, market)
    if not isinstance(payload, dict) or not payload or set(payload) - {'price', 'label', 'color', 'line_type', 'alert_modes', 'auto_watchlist'}:
        fail('只能修改水平线价格、名称、颜色与显式提醒规则')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM chart_annotation WHERE instrument_id=? AND annotation_id=?', (symbol, annotation_id)).fetchone()
        if row is None: fail('水平线不存在或不属于此股票', 404)
        previous=line_alerts.annotation_view(row)
        previous['first_alert_date']=line_alerts.retention.first_alerts(c,[annotation_id]).get(annotation_id)
        price,label,color=_style(payload,previous);kind,modes,automatic=_rule_config(payload,previous)
        _index_rules(index, previous['price_basis'], modes, automatic)
        material=(price,kind,modes,automatic)!=(previous['price'],previous['line_type'],previous['alert_modes'],previous['auto_watchlist'])
        stamp=now_ms();revision=previous['revision']+int(material)
        clock=c.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone()
        effective=clock[0] if material and clock else previous['effective_business_date']
        origin = previous.get('origin', {})
        if origin and (price != previous['price'] or kind != previous['line_type']):
            origin = {**origin, 'manual_override': True}
        c.execute('UPDATE chart_annotation SET price=?,label=?,color=?,updated_at_ms=?,line_type=?,alert_modes_json=?,auto_watchlist=?,revision=?,effective_business_date=?,origin_json=? WHERE annotation_id=?',
                  (price,label,color,stamp,kind,line_alerts.dumps(modes),int(automatic),revision,effective,dumps(origin),annotation_id))
        if material and not index:line_alerts.enqueue(c,'line-alert-save:'+annotation_id+':'+str(revision),'line-save',no_rules_ok=True)
        result={**previous,'price':price,'label':label,'color':color,'updated_at_ms':stamp,'line_type':kind,'alert_modes':modes,'auto_watchlist':automatic,'revision':revision,'effective_business_date':effective,'origin':origin}
    return result


def delete_annotation(identifier, annotation_id):
    symbol, market = _instrument(identifier)
    index = _is_index(symbol, market)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        removed = c.execute('DELETE FROM chart_annotation WHERE instrument_id=? AND annotation_id=?', (symbol, annotation_id))
        if not removed.rowcount: fail('水平线不存在或不属于此股票', 404)
        if not index:line_alerts.enqueue(c,'line-alert-delete:'+annotation_id,'line-delete',no_rules_ok=True)
    return {'removed': True, 'annotation_id': annotation_id, 'instrument_id': symbol}


def recommendations(identifier, payload):
    """Manual local computation outside any SQLite write transaction."""
    symbol, market = _instrument(identifier)
    if not isinstance(payload, dict) or set(payload) - {'period', 'as_of', 'price_basis', 'lookback', 'snapshot_id'}:
        fail('支撑压力候选参数不受支持')
    period = payload.get('period', 'D')
    lookback = payload.get('lookback', 120)
    basis = payload.get('price_basis', 'none')
    if period not in ('D', 'W', 'M') or basis not in ('none', 'forward'):
        fail('请选择日、周或月周期及有效价格口径')
    if isinstance(lookback, bool) or not isinstance(lookback, int) or not 20 <= lookback <= 240:
        fail('回看范围需要为20至240根K线')
    before = market.status()
    snapshot = before.get('snapshot_id')
    if not snapshot:
        fail('尚无已发布行情，不能计算候选', 409)
    if payload.get('snapshot_id') is not None and payload['snapshot_id'] != snapshot:
        fail('图表行情版本已经变化，请刷新后重新计算候选', 409)
    as_of = payload.get('as_of') or before.get('data_end')
    try:
        valid = isinstance(as_of, str) and date.fromisoformat(as_of).isoformat() == as_of
    except ValueError:
        valid = False
    if not valid or not before.get('data_end') or as_of > before['data_end'] or as_of not in market.calendar():
        fail('候选观察日需要是已发布范围内的市场交易日')
    index = _is_index(symbol, market)
    if index and basis != 'none':
        fail('指数候选使用原始点位，不使用个股复权口径')
    try:
        # Read only this security. A full chart would also calculate indicators,
        # wave structures and (for the Shanghai index) all-market breadth.
        # None of those are required to calculate price levels.
        rows, adjustment = market.prices(symbol, as_of=as_of, price_basis=basis)
        reference_quote = dict(rows[-1]) if rows else None
        actual_basis = 'none' if basis == 'none' else 'forward_event_affine' if adjustment.get('usable') else 'none'
        calendar = market.calendar()
        if period == 'W':
            rows = aggregate_weekly(rows, calendar, as_of)
        elif period == 'M':
            rows = aggregate_monthly(rows, calendar, as_of)
        chart = {'bars': rows, 'period': period, 'as_of': as_of, 'price_basis': actual_basis,
                 'snapshot_id': snapshot, 'instrument_kind': 'index' if index else 'stock',
                 'reference_quote': reference_quote}
        levels = local_levels(chart, calendar, lookback)
    except ValueError as error:
        fail(str(error))
    if market.status().get('snapshot_id') != snapshot or chart.get('snapshot_id') != snapshot:
        fail('计算期间行情版本变化，请重新计算候选', 409)
    actual_basis = chart.get('price_basis')
    if actual_basis != ('forward_event_affine' if basis == 'forward' else 'none'):
        fail('请求的价格口径未通过校验，请切换未复权或先补全公司行动依据', 409)
    parameters = {'period': period, 'as_of': as_of, 'price_basis': basis,
                  'lookback': lookback, 'snapshot_id': snapshot}
    items = []
    for level in levels.pop('items'):
        origin = {'algorithm': LEVEL_VERSION, 'snapshot_id': snapshot, 'as_of': as_of,
                  'period': period, 'lookback': lookback, 'pivot_wing': levels['pivot_wing'],
                  'cluster_pct': levels['cluster_pct'], 'window_start': levels['window_start'],
                  'window_end': levels['window_end'], 'reference_price': levels['reference_price'],
                  'reference_date': levels['reference_date'], 'original_price': level['price'],
                  'line_type': level['line_type'], 'evidence': level['evidence'], 'manual_override': False}
        key = digest({'instrument_id': symbol, 'parameters': parameters, 'origin': origin})
        items.append({**level, 'candidate_id': key, 'price_basis': actual_basis,
                      'anchor_date': as_of if basis == 'forward' else None, 'origin': origin})
    with connect() as connection:
        saved = {row[0] for row in connection.execute('SELECT origin_key FROM chart_annotation WHERE instrument_id=? AND origin_key IS NOT NULL', (symbol,))}
    for item in items:
        item['saved'] = item['candidate_id'] in saved
    return {'instrument_id': symbol, 'instrument_kind': 'index' if index else 'stock',
            'parameters': parameters, **levels, 'items': items,
            'preview_id': digest({'instrument_id': symbol, 'parameters': parameters, 'items': [{key: value for key, value in item.items() if key != 'saved'} for item in items]}),
            'notice': '历史分型需右侧2根完整K线确认，再将0.8%内价位聚合；候选仅为历史价格参考，不预测未来。保存不启用提醒或自动自选。'}


def save_recommendations(identifier, payload):
    if not isinstance(payload, dict) or set(payload) != {'parameters', 'preview_id', 'candidate_ids'}:
        fail('请先计算并选择要保存的候选')
    ids = payload['candidate_ids']
    if not isinstance(ids, list) or not 1 <= len(ids) <= 6 or any(not isinstance(value, str) for value in ids) or len(ids) != len(set(ids)):
        fail('请选取1至6条互不重复的候选')
    preview = recommendations(identifier, payload['parameters'])
    if payload['preview_id'] != preview['preview_id']:
        fail('候选已经变化，请重新预览后保存', 409)
    selected = [item for item in preview['items'] if item['candidate_id'] in ids]
    if len(selected) != len(ids):
        fail('所选候选不属于这次计算，请重新预览', 409)
    symbol = preview['instrument_id']
    created, reused = [], []
    stamp = now_ms()
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        rows = list(connection.execute('SELECT * FROM chart_annotation WHERE instrument_id=?', (symbol,)))
        existing = {row['origin_key']: row for row in rows if row['origin_key']}
        missing = [item for item in selected if item['candidate_id'] not in existing]
        if len(rows) + len(missing) > MAX_LINES:
            fail(f'每个标的最多保存{MAX_LINES}条水平线，请先移除不用的标记', 409)
        for item in selected:
            if item['candidate_id'] in existing:
                reused.append(line_alerts.annotation_view(existing[item['candidate_id']]))
                continue
            identifier = new_id('line')
            kind = item['line_type']
            label = ('自动支撑' if kind == 'support' else '自动压力') + ' · ' + {'D': '日', 'W': '周', 'M': '月'}[preview['parameters']['period']]
            connection.execute('INSERT INTO chart_annotation(annotation_id,instrument_id,price,label,color,price_basis,anchor_date,created_at_ms,updated_at_ms,line_type,alert_modes_json,auto_watchlist,revision,effective_business_date,origin_json,origin_key,instrument_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                               (identifier, symbol, item['price'], label, 'green' if kind == 'support' else 'red', item['price_basis'], item['anchor_date'], stamp, stamp, kind, '[]', 0, 1, None, dumps(item['origin']), item['candidate_id'],preview['instrument_kind']))
            created.append(line_alerts.annotation_view(connection.execute('SELECT * FROM chart_annotation WHERE annotation_id=?', (identifier,)).fetchone()))
    return {'instrument_id': symbol, 'created': created, 'reused': reused, 'total_saved': len(created) + len(reused), 'alerts_enabled': False}
