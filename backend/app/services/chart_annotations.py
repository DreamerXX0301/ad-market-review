"""User chart marks, separate from source quotes, signals and strategy rules."""
from datetime import date
import math
import re

from app.core import connect, get_market, new_id, now_ms
from app.services.workspace_common import fail
from app.services import line_alerts

COLORS = {'blue', 'amber', 'red', 'green', 'violet'}
MAX_LINES = 64


def _instrument(identifier):
    symbol = str(identifier).strip().upper()
    if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', symbol):
        fail('请选择明确的个股代码')
    market = get_market()
    found = next((item for item in market.instruments(limit=10000)
                  if str(item.get('instrument_id', '')).upper() == symbol), None)
    if not found or found.get('kind', found.get('asset_type', 'stock')) not in ('stock', 'equity', 'a_share', 'A'):
        fail('划线仅适用于已留存的个股，不将指数或板块保存为个股', 404)
    return symbol, market


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
            'created_at_ms': stamp, 'updated_at_ms': stamp,'line_type':kind,'alert_modes':modes,'auto_watchlist':automatic,'revision':1}
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        count = c.execute('SELECT COUNT(*) FROM chart_annotation WHERE instrument_id=?', (symbol,)).fetchone()[0]
        if count >= MAX_LINES: fail(f'每只股票最多保存{MAX_LINES}条水平线，请先移除不用的标记', 409)
        clock=c.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone()
        item['effective_business_date']=clock[0] if clock and kind!='reference' else None
        c.execute('INSERT INTO chart_annotation(annotation_id,instrument_id,price,label,color,price_basis,anchor_date,created_at_ms,updated_at_ms,line_type,alert_modes_json,auto_watchlist,revision,effective_business_date) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (item['annotation_id'],symbol,price,label,color,basis,anchor,stamp,stamp,kind,line_alerts.dumps(modes),int(automatic),1,item['effective_business_date']))
        if modes:line_alerts.enqueue(c, 'line-alert-save:'+item['annotation_id']+':1', 'line-save')
    return item


def update_annotation(identifier, annotation_id, payload):
    symbol, _ = _instrument(identifier)
    if not isinstance(payload, dict) or not payload or set(payload) - {'price', 'label', 'color', 'line_type', 'alert_modes', 'auto_watchlist'}:
        fail('只能修改水平线价格、名称、颜色与显式提醒规则')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM chart_annotation WHERE instrument_id=? AND annotation_id=?', (symbol, annotation_id)).fetchone()
        if row is None: fail('水平线不存在或不属于此股票', 404)
        previous=line_alerts.annotation_view(row)
        price,label,color=_style(payload,previous);kind,modes,automatic=_rule_config(payload,previous)
        material=(price,kind,modes,automatic)!=(previous['price'],previous['line_type'],previous['alert_modes'],previous['auto_watchlist'])
        stamp=now_ms();revision=previous['revision']+int(material)
        clock=c.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone()
        effective=clock[0] if material and clock else previous['effective_business_date']
        c.execute('UPDATE chart_annotation SET price=?,label=?,color=?,updated_at_ms=?,line_type=?,alert_modes_json=?,auto_watchlist=?,revision=?,effective_business_date=? WHERE annotation_id=?',
                  (price,label,color,stamp,kind,line_alerts.dumps(modes),int(automatic),revision,effective,annotation_id))
        if material:line_alerts.enqueue(c,'line-alert-save:'+annotation_id+':'+str(revision),'line-save',no_rules_ok=True)
        result={**previous,'price':price,'label':label,'color':color,'updated_at_ms':stamp,'line_type':kind,'alert_modes':modes,'auto_watchlist':automatic,'revision':revision,'effective_business_date':effective}
    return result


def delete_annotation(identifier, annotation_id):
    symbol, _ = _instrument(identifier)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        removed = c.execute('DELETE FROM chart_annotation WHERE instrument_id=? AND annotation_id=?', (symbol, annotation_id))
        if not removed.rowcount: fail('水平线不存在或不属于此股票', 404)
        line_alerts.enqueue(c,'line-alert-delete:'+annotation_id,'line-delete',no_rules_ok=True)
    return {'removed': True, 'annotation_id': annotation_id, 'instrument_id': symbol}
