"""Manual annual pools. All market reads precede short SQLite write transactions."""
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import math
import json
import re
from urllib.parse import urlsplit

from app.core import connect, get_market, now_ms, new_id
from app.services.workspace_common import freeze_inputs, fail, js, obj, rows, money
from app.services.stock_summary import _master
from app.services.watchlist import _entry_baselines, _generation
from app.services.search_matching import identity_match

RELATIONS = {'name': '名称直接关联', 'homophone': '谐音', 'branch': '地支', 'culture': '文化 / IP', 'business': '实际业务', 'custom': '自定义'}
MAX_ACTIVE = 500


def year_info(year):
    if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 2199:
        fail('观察年度应为1900—2199之间的整数')
    target = year + 1
    zodiac = '鼠牛虎兔龙蛇马羊猴鸡狗猪'[(target - 2020) % 12]
    return {'observation_year': year, 'target_year': target, 'zodiac': zodiac, 'label': f'{year}年观察 · {target}{zodiac}年'}


def default_year():
    return datetime.now(ZoneInfo('Asia/Shanghai')).year


def _head(connection, required=False):
    clock = connection.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone()
    primary = freeze_inputs(connection)[1] if clock else None
    if required and not clock:
        fail('请先初始化并发布真实行情，再添加观察股票', 409)
    heads = rows(connection, "SELECT module,scope_key,snapshot_id FROM published_head WHERE module IN ('daily_raw','analysis_bars','calendar') ORDER BY module,scope_key") if primary else []
    generation = hashlib.sha256(js(heads).encode()).hexdigest() if heads else None
    return {'business_date': clock[0] if clock else None, 'generation': generation, 'primary_snapshot_id': primary}


def pools():
    with connect() as c:
        c.execute('BEGIN')
        head = _head(c)
        saved = rows(c, '''SELECT p.*, count(m.member_id) total,
            coalesce(sum(m.status='tracking'),0) active_count
            FROM zodiac_pool p LEFT JOIN zodiac_pool_member m USING(observation_year)
            GROUP BY p.observation_year ORDER BY observation_year DESC''')
    return {**head, 'default_year': default_year(), 'items': [{**row, **year_info(row['observation_year'])} for row in saved]}


def pool(year):
    description = year_info(year)
    with connect() as c:
        c.execute('BEGIN')
        head = _head(c)
        saved = c.execute('SELECT * FROM zodiac_pool WHERE observation_year=?', (year,)).fetchone()
    return {**description, **head, 'exists': bool(saved), 'pool_revision': saved['revision'] if saved else 0}


def _text(value, name, maximum):
    if not isinstance(value, str) or len(value) > maximum:
        fail(f'{name}应为不超过{maximum}字的文本')
    return value.strip()


def _metadata(payload, previous=None):
    allowed = {'relation_type', 'custom_label', 'reason', 'links', 'focused'}
    value = {'relation_type': 'custom', 'custom_label': '', 'reason': '', 'links': [], 'focused': False}
    if previous:
        value.update({key: previous[key] for key in allowed})
    value.update({key: payload[key] for key in allowed if key in payload})
    if not isinstance(value['relation_type'], str) or value['relation_type'] not in RELATIONS:
        fail('请选择有效的生肖关联类型')
    for key, maximum in (('custom_label', 30), ('reason', 1000)):
        value[key] = _text(value[key], key, maximum)
    if type(value['focused']) is not bool:
        fail('重点关注必须为布尔值')
    links = value['links']
    if not isinstance(links, list) or len(links) > 3:
        fail('依据链接最多3个')
    for link in links:
        if not isinstance(link, str) or len(link) > 2000:
            fail('依据链接应为不超过2000字的http/https地址')
        try:
            parsed = urlsplit(link)
            valid = parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password and not any(ch.isspace() for ch in link)
        except ValueError:
            valid = False
        if not valid:
            fail('依据链接仅支持不含账号密码的http/https地址')
    return value


def view(row):
    result = dict(row)
    result['links'] = obj(result.pop('links_json'), [])
    result['entry_basis'] = obj(result.pop('entry_basis_json'), {})
    result['entry_price'] = money(result.pop('entry_price_e8'))
    result['focused'] = bool(result['focused'])
    result['code'] = result['instrument_id'].split('.')[0]
    result['relation_label'] = RELATIONS[result['relation_type']]
    return result


def _request(action, payload):
    key = payload.get('request_key')
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9:_-]{8,128}', key):
        fail('需要8—128位唯一request_key')
    canonical = json.dumps([action, {k: v for k, v in payload.items() if k != 'request_key'}], sort_keys=True, ensure_ascii=False, allow_nan=False)
    return key, hashlib.sha256(canonical.encode()).hexdigest()


def _replay(c, request):
    old = c.execute('SELECT * FROM zodiac_pool_request WHERE request_key=?', (request[0],)).fetchone()
    if old:
        if old['arguments_sha256'] != request[1]:
            fail('此请求标识已用于不同操作，请刷新后重试', 409)
        return obj(old['response_json'])
    return None


def _receipt(c, request, response):
    c.execute('INSERT INTO zodiac_pool_request VALUES(?,?,?,?)', (*request, js(response), now_ms()))
    return response


def _expect(c, payload):
    head = _head(c, True)
    if any(payload.get('expected_' + key) != head[key] for key in ('business_date', 'generation')):
        fail('行情业务日或代际已更新，请刷新生肖池后重新提交', 409)
    return head


def _audit(c, member_id, action, before, after):
    c.execute('INSERT INTO zodiac_pool_audit VALUES(?,?,?,?,?,?)', (new_id('za'), member_id, action, now_ms(), js(before), js(after)))


def _get(c, member_id):
    row = c.execute('SELECT * FROM zodiac_pool_member WHERE member_id=?', (member_id,)).fetchone()
    if not row:
        fail('生肖观察记录不存在', 404)
    return dict(row)


def preview(year, payload):
    year_info(year)
    text = _text(payload.get('text', ''), '批量输入', 20000)
    tokens = re.split(r'[\s,，;；]+', text.strip()) if text.strip() else []
    if not 1 <= len(tokens) <= 100:
        fail('每次预览1—100项')
    master = _master(get_market())
    with connect() as c:
        active = {r[0] for r in c.execute("SELECT instrument_id FROM zodiac_pool_member WHERE observation_year=? AND status='tracking'", (year,))}
    result, seen = [], set()
    for token in tokens:
        matches = [dict(identity, instrument_id=symbol) for symbol, identity in master.items()
                   if token.upper() in (symbol, identity.get('code', symbol[:6])) or token == identity.get('name')]
        exact = bool(matches)
        if not exact:
            candidates = []
            for symbol, identity in master.items():
                match = identity_match(token, identity.get('name') or symbol, symbol, identity.get('code', symbol[:6]))
                if match is not None:
                    rank, evidence = match
                    candidates.append((rank, {**identity, 'instrument_id': symbol, **evidence}))
            candidates.sort(key=lambda pair: (pair[0], pair[1].get('name', ''), pair[1]['instrument_id']))
            matches = [r for _, r in candidates]
        candidate_count = len(matches)
        symbol = matches[0]['instrument_id'] if exact and len(matches) == 1 else None
        state = 'unrecognized' if not matches else 'ambiguous' if not exact or len(matches) > 1 else 'duplicate' if symbol in seen else 'active' if symbol in active else 'ready'
        result.append({'input': token, 'state': state, 'candidates': matches[:20], 'candidate_count': candidate_count, 'instrument_id': symbol})
        if symbol: seen.add(symbol)
    return {'items': result}


def _baseline_batch(market, saved, day, primary):
    generation = _generation(market, primary)
    calendar = market.calendar()
    # Shared validation requires exact dated positive OHLC and actual volume.
    result = _entry_baselines(market, saved, calendar, day, generation)
    reader = getattr(market, 'history_for_instruments', None)
    quoted = reader(sorted({r['instrument_id'] for r in saved}), start=min(r['added_business_date'] for r in saved), end=max(r['added_business_date'] for r in saved)) if saved and callable(reader) else {}
    with connect() as c:
        sources = rows(c, '''SELECT s.snapshot_id,s.manifest_sha256,s.lineage_json
          FROM dataset_snapshot s JOIN published_head h USING(snapshot_id)
          WHERE h.module IN ('daily_raw','calendar') ORDER BY h.module''')
        for source in sources:
            source['artifacts'] = rows(c, 'SELECT object_key,role,sha256 FROM snapshot_artifact WHERE snapshot_id=? ORDER BY object_key', (source['snapshot_id'],))
    for row in saved:
        value = result[row['instrument_id']]
        basis = value['basis']
        basis.update(method='zodiac-exact-entry-close-v1', unit='CNY/share', source_snapshots=sources,
                     calendar_sha256=hashlib.sha256('\n'.join(calendar).encode()).hexdigest())
        if basis.get('reason'): basis['reason'] = basis['reason'].replace('加入自选', '加入观察池')
        exact = [r for r in quoted.get(row['instrument_id'], []) if r.get('date') == row['added_business_date']]
        quote = {k: exact[0].get(k) for k in ('date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'source', 'price_basis')} if len(exact) == 1 and value['price_e8'] is not None else None
        # A self-contained immutable quote proof travels with ordinary SQLite backups.
        if quote and (isinstance(quote.get('amount'), bool) or not isinstance(quote.get('amount'), (int,float)) or not math.isfinite(quote['amount'])):
            quote['amount'] = None
        basis['quote'] = quote
        basis['quote_sha256'] = hashlib.sha256(js(quote).encode()).hexdigest() if quote else None
    return result


def add(year, payload):
    year_info(year)
    request = _request(f'add:{year}', payload)
    values = payload.get('items')
    if not isinstance(values, list) or not 1 <= len(values) <= 100 or any(not isinstance(v, dict) for v in values):
        fail('一次添加1—100项准确股票身份')
    with connect() as c:
        replay = _replay(c, request)
        if replay is not None: return replay
        head = _expect(c, payload)
    market = get_market(); master = _master(market)
    unique = {}
    for item in values:
        symbol = item.get('instrument_id')
        if not isinstance(symbol, str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', symbol) or symbol not in master:
            fail('只能加入本地已核验A股，请从搜索或批量预览选择准确证券')
        meta = _metadata(item)
        if symbol not in unique: unique[symbol] = meta
    baselines = _baseline_batch(market, [{'instrument_id': s, 'added_business_date': head['business_date']} for s in unique], head['business_date'], head['primary_snapshot_id'])
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        replay = _replay(c, request)
        if replay is not None: return replay
        _expect(c, payload)
        active = {r['instrument_id']: r for r in rows(c, "SELECT * FROM zodiac_pool_member WHERE observation_year=? AND status='tracking'", (year,))}
        if len(active) + len(set(unique) - set(active)) > MAX_ACTIVE:
            fail(f'每年度最多{MAX_ACTIVE}只观察中股票，本次未添加任何股票', 409)
        c.execute('INSERT OR IGNORE INTO zodiac_pool VALUES(?,?,1,?)', (year, year+1, now_ms()))
        output = []
        for symbol, meta in unique.items():
            if symbol in active:
                output.append({'state': 'already_active', 'item': view(active[symbol])}); continue
            number = c.execute('SELECT coalesce(max(round_number),0)+1 FROM zodiac_pool_member WHERE observation_year=? AND instrument_id=?', (year, symbol)).fetchone()[0]
            baseline = baselines[symbol]; stamp = now_ms(); identifier = new_id('zm')
            c.execute('''INSERT INTO zodiac_pool_member(member_id,observation_year,instrument_id,name,round_number,status,
                relation_type,custom_label,reason,links_json,focused,added_business_date,entry_price_e8,entry_basis_json,created_at_ms,updated_at_ms)
                VALUES(?,?,?,?,?,'tracking',?,?,?,?,?,?,?,?,?,?)''',
                (identifier, year, symbol, master[symbol].get('name') or symbol, number, meta['relation_type'], meta['custom_label'], meta['reason'], js(meta['links']), int(meta['focused']), head['business_date'], baseline['price_e8'], js(baseline['basis']), stamp, stamp))
            item = view(_get(c, identifier)); _audit(c, identifier, 'add', {}, item)
            output.append({'state': 'readded' if number > 1 else 'added', 'item': item})
        if any(x['state'] != 'already_active' for x in output):
            c.execute('UPDATE zodiac_pool SET revision=revision+1 WHERE observation_year=?', (year,))
        return _receipt(c, request, {'items': output, **head})


def change(member_id, payload, *, end=False):
    request = _request(('end:' if end else 'edit:') + member_id, payload)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        replay = _replay(c, request)
        if replay is not None: return replay
        head = _expect(c, payload); saved = _get(c, member_id); before = view(saved)
        if type(payload.get('revision')) is not int or payload['revision'] != saved['revision']:
            fail('记录已被其他操作更新，请刷新后重新编辑', 409)
        if end:
            if saved['status'] != 'tracking': fail('该轮已结束，请刷新记录', 409)
            reason = _text(payload.get('end_reason', ''), '移出原因', 1000)
            c.execute("UPDATE zodiac_pool_member SET status='ended',end_business_date=?,end_reason=?,revision=revision+1,updated_at_ms=? WHERE member_id=?", (head['business_date'], reason, now_ms(), member_id))
        else:
            meta = _metadata(payload, before)
            c.execute('''UPDATE zodiac_pool_member SET relation_type=?,custom_label=?,reason=?,links_json=?,focused=?,revision=revision+1,updated_at_ms=? WHERE member_id=?''',
                      (meta['relation_type'], meta['custom_label'], meta['reason'], js(meta['links']), int(meta['focused']), now_ms(), member_id))
        after = view(_get(c, member_id)); _audit(c, member_id, 'end' if end else 'edit', before, after)
        c.execute('UPDATE zodiac_pool SET revision=revision+1 WHERE observation_year=?', (saved['observation_year'],))
        return _receipt(c, request, {'item': after})


def verify(year, payload):
    year_info(year); request = _request(f'verify:{year}', payload)
    ids = payload.get('member_ids')
    if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(not isinstance(x, str) for x in ids): fail('一次核验1—100条基准')
    with connect() as c:
        replay = _replay(c, request)
        if replay is not None: return replay
        head = _expect(c, payload)
        saved = [_get(c, member_id) for member_id in dict.fromkeys(ids)]
        if any(r['observation_year'] != year for r in saved): fail('不能跨池核验基准')
    pending = [r for r in saved if r['entry_price_e8'] is None]
    market = get_market()
    # Group by added date so repeated ended rounds of one symbol stay independent.
    baselines = {}
    for day in sorted({r['added_business_date'] for r in pending}):
        batch = [r for r in pending if r['added_business_date'] == day]
        resolved = _baseline_batch(market, batch, head['business_date'], head['primary_snapshot_id'])
        baselines.update({r['member_id']: resolved[r['instrument_id']] for r in batch})
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        replay = _replay(c, request)
        if replay is not None: return replay
        _expect(c, payload)
        for row in pending:
            latest = _get(c, row['member_id'])
            if latest['revision'] != row['revision']: fail('基准核验期间记录已变化，请刷新后重试', 409)
            value = baselines[row['member_id']]
            c.execute('UPDATE zodiac_pool_member SET entry_price_e8=?,entry_basis_json=?,revision=revision+1,updated_at_ms=? WHERE member_id=? AND entry_price_e8 IS NULL', (value['price_e8'], js(value['basis']), now_ms(), row['member_id']))
            _audit(c, row['member_id'], 'verify_baseline', view(row), view(_get(c, row['member_id'])))
        if pending: c.execute('UPDATE zodiac_pool SET revision=revision+1 WHERE observation_year=?', (year,))
        return _receipt(c, request, {'items': [view(_get(c, r['member_id'])) for r in saved]})


def history(member_id):
    with connect() as c:
        saved = view(_get(c, member_id))
        audits = rows(c, 'SELECT * FROM zodiac_pool_audit WHERE member_id=? ORDER BY occurred_at_ms,rowid', (member_id,))
    return {'item': saved, 'items': [{**{k: v for k, v in a.items() if not k.endswith('_json')}, 'before': obj(a['before_json']), 'after': obj(a['after_json'])} for a in audits]}
