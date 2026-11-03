"""Recover member limit-up counts from an explicit retained noon generation.

Older noon bodies retained member aggregates rather than constituent identities.
This reader may consult only that body's named immutable reference generation,
then reconciles its list against the frozen aggregates before using the frozen
noon event pool. It never follows a current head, opens DuckDB, or writes facts.
"""
from collections import Counter
from datetime import date
import json
import math
import re
import stat

from app.analytics.market_storage import load_reference, safe_object, verify_immutable_file
from app.domain.universe import BOARDS, identity_flags
from app.infrastructure.config import published_dir

MAX_TOPICS = 1500
MAX_MEMBERS = 10000
MAX_TOTAL_MEMBERS = 1000000
MAX_CLASSIFIED_STOCKS = 3000
MAX_CLASSIFICATION_LINKS = 20000
MAX_INDUSTRIES = 32
MAX_CONCEPTS = 100
MAX_REFERENCE_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
SYMBOL = re.compile(r'\d{6}\.(?:SH|SZ|BJ)\Z')
SECTOR = re.compile(r'\d{6}\.(?:TI|SH|SZ)\Z')
GENERATION = re.compile(r'market-[A-Za-z0-9_-]{1,160}\Z')
SHA = re.compile(r'[a-f0-9]{64}\Z')
SCOPE = '原采集代际指定名单与本次已核验涨停池交集；仅为已留存成员样本，不代表全板块收盘涨停家数'


def _day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _code(row):
    code = row.get('topic_code') or row.get('instrument_id')
    if not isinstance(code, str) or not SECTOR.fullmatch(code):
        return None
    if row.get('topic_code') and row.get('instrument_id') and row['topic_code'] != row['instrument_id']:
        return None
    return code


def _result(row, count=None, state='unknown', reason=None, source_sha256=None):
    return {'member_limit_up_count': count, 'member_limit_up_state': state,
            'member_limit_up_reason': reason, 'state': state, 'reason': reason,
            'scope': SCOPE, 'members_observed_date': row.get('members_observed_date'),
            'source_sha256': source_sha256}


def _source_id(payload):
    previous = payload.get('previous_review') or {}
    day, prior = payload.get('date'), previous.get('date')
    if not _day(day) or not _day(prior) or prior >= day:
        return None
    ids = set()
    for facts in (previous.get('background'), previous.get('payload')):
        if (isinstance(facts, dict) and facts.get('mode') == 'previous_session_price_facts'
                and facts.get('date') == prior and isinstance(facts.get('source_snapshot_id'), str)):
            ids.add(facts['source_snapshot_id'])
    if len(ids) != 1:
        return None
    value = ids.pop()
    return value if GENERATION.fullmatch(value) else None


def _read_json(path, budget):
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= budget:
        raise ValueError('不可变来源文件超出读取边界')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('不可变来源结构无效')
    return value


def _load_topics(generation, previous_day):
    root = published_dir().resolve()
    manifest = _read_json(safe_object(root, generation + '/manifest.json'), MAX_MANIFEST_BYTES)
    summary = manifest.get('summary')
    if (not isinstance(summary, dict) or summary.get('snapshot_id') != generation
            or not _day(summary.get('data_end')) or summary['data_end'] > previous_day
            or summary.get('source') not in (None, 'fuyao')):
        raise ValueError('原代际身份、来源或观察日不符')
    files = summary.get('files')
    descriptors = [row for row in files if isinstance(row, dict) and row.get('role') == 'reference.json'] if isinstance(files, list) else []
    if len(descriptors) != 1:
        raise ValueError('原代际缺少唯一参考文件')
    descriptor = descriptors[0]
    digest, size = descriptor.get('sha256'), descriptor.get('bytes')
    if (descriptor.get('path') != 'published/' + generation + '/reference.json'
            or not isinstance(digest, str) or not SHA.fullmatch(digest)
            or _count(size) is None or not 0 < size <= MAX_REFERENCE_BYTES):
        raise ValueError('原代际参考路径、SHA或大小无效')
    path = safe_object(root, generation + '/reference.json')
    verify_immutable_file(path, digest, size)
    document = _read_json(path, MAX_REFERENCE_BYTES)
    source_sha = digest
    if document.get('storage_version') in ('market-reference-parts-v1', 'market-reference-parts-v2'):
        fields = document.get('fields')
        topic = fields.get('topics') if isinstance(fields, dict) else None
        if (not isinstance(topic, dict) or _count(topic.get('bytes')) is None
                or topic['bytes'] > MAX_REFERENCE_BYTES):
            raise ValueError('原代际缺少有界题材成员对象')
        document = load_reference(path, document={
            'storage_version': document['storage_version'], 'fields': {'topics': topic}, 'indices': {}})
        source_sha = topic.get('sha256')
    topics = document.get('topics')
    if not isinstance(topics, list) or len(topics) > MAX_TOPICS:
        raise ValueError('原代际成员目录缺失或超过有界容量')
    if sum(len(row.get('instrument_ids', [])) for row in topics
           if isinstance(row, dict) and isinstance(row.get('instrument_ids'), list)) > MAX_TOTAL_MEMBERS:
        raise ValueError('原代际成员总量超过有界容量')
    return topics, source_sha


def _quotes(payload):
    rows = payload.get('stocks')
    rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    counts = Counter(row.get('instrument_id') for row in rows if isinstance(row.get('instrument_id'), str))
    return {row['instrument_id']: row for row in rows
            if isinstance(row.get('instrument_id'), str) and SYMBOL.fullmatch(row['instrument_id'])
            and counts[row['instrument_id']] == 1}


def _valid_quote(row):
    return ((_number(row.get('latest_price')) or 0) > 0
            and (_number(row.get('prev_close')) or 0) > 0
            and _number(row.get('change_pct')) is not None)


def _limit_pool(payload, quotes):
    pool = (payload.get('events') or {}).get('limit_up') or {}
    rows = pool.get('items')
    if pool.get('state') not in ('observed', 'partial') or not isinstance(rows, list):
        return None, False
    found = set()
    for row in rows:
        if not isinstance(row, dict):
            return None, False
        symbol, latest = row.get('instrument_id'), _number(row.get('latest_price'))
        quote = quotes.get(symbol, {}) if isinstance(symbol, str) else {}
        price = _number(quote.get('latest_price'))
        if (not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) or symbol in found
                or latest is None or latest <= 0 or price is None or abs(price - latest) > .011):
            return None, False
        found.add(symbol)
    return found, pool['state'] == 'observed'


def _verified_members(row, candidates, quotes, day):
    code = _code(row)
    observed = row.get('members_observed_date')
    if (not _day(observed) or observed > day or row.get('kind') not in ('industry', 'concept')):
        return None, '午评未留存可核验的成员日期或分类'
    matches = [topic for topic in candidates.get(code, []) if topic.get('kind') == row['kind']
               and topic.get('source') == 'fuyao' and topic.get('observed_date') == observed]
    if len(matches) != 1:
        return None, '原代际未找到唯一的同来源代码、分类与成员日期名单'
    topic = matches[0]
    members = topic.get('instrument_ids')
    if (not isinstance(members, list) or len(members) > MAX_MEMBERS
            or any(not isinstance(code, str) or not SYMBOL.fullmatch(code) for code in members)
            or len(set(members)) != len(members)):
        return None, '原代际成员身份无效、重复或超过有界容量'
    values = [quotes[symbol] for symbol in members if symbol in quotes and _valid_quote(quotes[symbol])]
    expected = {'total': len(members), 'valid': len(values),
                'complete': topic.get('members_complete') is True and len(values) == len(members)}
    coverage = row.get('member_coverage')
    up_count = sum(value['change_pct'] > 0 for value in values) if values else None
    down_count = sum(value['change_pct'] < 0 for value in values) if values else None
    if (not isinstance(coverage, dict) or any(coverage.get(key) != value for key, value in expected.items())
            or _count(coverage.get('total')) is None or _count(coverage.get('valid')) is None
            or not isinstance(coverage.get('complete'), bool)
            or (row.get('member_up') is not None if up_count is None else _count(row.get('member_up')) != up_count)
            or (row.get('member_down') is not None if down_count is None else _count(row.get('member_down')) != down_count)):
        return None, '原代际名单与午评冻结的成员数量或涨跌汇总无法对账'
    return {'members': members, 'complete': expected['complete'], 'topic': topic}, None


def _member_count(row, proof, up, pool_complete, source_sha):
    data, error = proof
    if error is not None:
        return _result(row, reason=error)
    if up is None:
        return _result(row, reason='本次快照没有可核验的涨停池', source_sha256=source_sha)
    count = len(set(data['members']) & up)
    if pool_complete and data['complete']:
        return _result(row, count, 'observed', '原代际名单与午评汇总已对账，统计本次已核验池交集', source_sha)
    if count:
        return _result(row, count, 'partial', '成员或涨停池覆盖不完整，仅为已确认匹配下界', source_sha)
    return _result(row, reason='成员或涨停池覆盖不完整，未匹配不等于零涨停', source_sha256=source_sha)


def _display_board(row):
    """Match the noon UI's current code-based display classification only."""
    kind = row.get('kind')
    if kind is None:
        kind = row.get('asset_type')
    if kind is not None and kind != 'stock':
        return 'unknown'
    explicit = row.get('board')
    if explicit is not None:
        return explicit if isinstance(explicit, str) and explicit in BOARDS else 'unknown'
    text = lambda value: value.strip().upper() if isinstance(value, str) else ''
    symbol = text(row.get('instrument_id')) or text(row.get('thscode'))
    if symbol and not re.fullmatch(r'\d{6}(?:\.(?:SH|SZ|BJ))?', symbol):
        return 'unknown'
    raw_code = row.get('code')
    if raw_code is None or raw_code == '':
        raw_code = symbol.split('.')[0]
    if not isinstance(raw_code, (str, int, float)) or isinstance(raw_code, bool):
        return 'unknown'
    if isinstance(raw_code, float):
        if not math.isfinite(raw_code) or not raw_code.is_integer():
            return 'unknown'
        raw_code = int(raw_code)
    code = str(raw_code).strip().zfill(6)
    if not re.fullmatch(r'\d{6}', code):
        return 'unknown'
    exchange = text(row.get('exchange')) or (symbol.split('.')[1] if '.' in symbol else '')
    return identity_flags({'instrument_id': symbol, 'code': code, 'exchange': exchange})['board']


def _ranking_windows(rows):
    """All-board and each selected-board top ten; never freeze all quotes."""
    boards = {board: [] for board in BOARDS}
    for row in rows:
        selected = boards.get(_display_board(row))
        if selected is not None and len(selected) < 10:
            selected.append(row)
    return [rows[:10], *boards.values()]


def _target_symbols(payload):
    previous = payload.get('previous_review') or {}
    events = payload.get('events') or {}
    sources = [(previous.get('cohort_feedback') or {}).get('items'), previous.get('observations')]
    cohorts = previous.get('event_cohorts') or {}
    for key in ('limit_up', 'limit_down', 'limit_break'):
        sources.append((cohorts.get(key) or {}).get('items'))
        sources.append((events.get(key) or {}).get('items'))
    # Include only retained/displayed ranking windows, never the entire stock
    # universe. Older bodies may lack summary rankings, while the amount table
    # still takes its ten rows directly from the retained stock quotes.
    summary = payload.get('summary')
    summary = summary if isinstance(summary, dict) else {}
    for key in ('top_amount', 'top_gainers'):
        rows = summary.get(key)
        sources.append(rows[:20] if isinstance(rows, list) else [])
    stocks = payload.get('stocks')
    stocks = [row for row in stocks if isinstance(row, dict)] if isinstance(stocks, list) else []
    by_amount = sorted(stocks, key=lambda row: (
        _number(row.get('amount')) is None, -(_number(row.get('amount')) or 0)))
    sources.extend(_ranking_windows(by_amount))
    hot = payload.get('hot_stocks')
    hot = [row for row in hot if isinstance(row, dict)] if isinstance(hot, list) else []
    sources.extend(_ranking_windows(hot))
    symbols = set()
    for rows in sources:
        for row in rows if isinstance(rows, list) else []:
            symbol = (row.get('instrument_id') or row.get('thscode')) if isinstance(row, dict) else None
            if (isinstance(row, dict) and row.get('instrument_id') and row.get('thscode')
                    and row['instrument_id'] != row['thscode']):
                continue
            if isinstance(symbol, str) and SYMBOL.fullmatch(symbol):
                symbols.add(symbol)
                if len(symbols) > MAX_CLASSIFIED_STOCKS:
                    return sorted(symbols)[:MAX_CLASSIFIED_STOCKS], True
    return sorted(symbols), False


def _classification(reason, generation=None, source_sha=None):
    return {'industries': [], 'topics': [], 'state': 'unknown', 'reason': reason,
            'source_snapshot_id': generation, 'source_sha256': source_sha}


def _member_proofs(payload, topics, quotes):
    if (not isinstance(topics, list) or len(topics) > MAX_TOPICS
            or sum(len(row.get('instrument_ids', [])) for row in topics
                   if isinstance(row, dict) and isinstance(row.get('instrument_ids'), list)) > MAX_TOTAL_MEMBERS):
        raise ValueError('成员目录超过有界容量')
    candidates = {}
    for topic in topics:
        if isinstance(topic, dict) and isinstance(topic.get('topic_code'), str):
            candidates.setdefault(topic['topic_code'], []).append(topic)
    sectors = payload.get('sectors')
    sectors = [row for row in sectors if isinstance(row, dict)] if isinstance(sectors, list) else []
    if len(sectors) > MAX_TOPICS:
        raise ValueError('午评板块超过有界容量')
    identities = Counter(_code(row) for row in sectors)
    return {_code(row): ((None, '午评同代码板块重复，未合并归属') if identities[_code(row)] != 1
            else _verified_members(row, candidates, quotes, payload.get('date')))
            for row in sectors if _code(row) is not None}


def _classifications_from_proofs(payload, proofs, generation, source_sha, *, target=None):
    symbols, target_truncated = _target_symbols(payload) if target is None else (target, False)
    output = {symbol: _classification('原采集代际没有通过汇总对账的成员归属；不等于没有行业或题材', generation, source_sha)
              for symbol in symbols}
    total, truncated = 0, set()
    for proof, error in proofs.values():
        if error is not None:
            continue
        topic = proof['topic']
        code, kind, name = topic.get('topic_code'), topic.get('kind'), topic.get('name')
        if (not isinstance(code, str) or not SECTOR.fullmatch(code) or kind not in ('industry', 'concept')
                or not isinstance(name, str) or not name.strip() or len(name) > 256):
            continue
        tag = {'topic_code': code, 'name': name, 'kind': kind,
               'members_observed_date': topic['observed_date'], 'source': 'fuyao'}
        field, limit = ('industries', MAX_INDUSTRIES) if kind == 'industry' else ('topics', MAX_CONCEPTS)
        for symbol in proof['members']:
            if symbol not in output:
                continue
            result = output[symbol]
            if len(result[field]) >= limit or total >= MAX_CLASSIFICATION_LINKS:
                truncated.add(symbol)
                continue
            result[field].append(dict(tag))
            total += 1
    for symbol, result in output.items():
        for field in ('industries', 'topics'):
            result[field].sort(key=lambda row: (row['name'], row['topic_code']))
        if result['industries'] or result['topics']:
            result.update(state='partial' if symbol in truncated or target_truncated else 'observed_sample',
                          reason='原采集代际已核验成员归属样本，名单日期单列；归属不是昨日涨停原因或上涨因果。'
                          + (' 有界容量外归属未纳入。' if symbol in truncated or target_truncated else ''))
    return output


def freeze_stock_classifications(payload, topics, *, source_sha256=None):
    """Freeze watched, event and displayed-ranking stocks from the used lists."""
    generation = _source_id(payload)
    try:
        proofs = _member_proofs(payload, topics, _quotes(payload))
        return _classifications_from_proofs(payload, proofs, generation, source_sha256)
    except (ValueError, TypeError, KeyError):
        symbols, _ = _target_symbols(payload)
        return {symbol: _classification('采集所用成员资料未通过归属核验，不用当前目录替代', generation) for symbol in symbols}


def _frozen_classifications(payload, generation):
    symbols, _ = _target_symbols(payload)
    mapping = payload.get('stock_classifications')
    output = {}
    sectors = {_code(row): row for row in payload.get('sectors') or [] if isinstance(row, dict) and _code(row)}
    for symbol in symbols:
        missing = _classification('本份午评未冻结可核验的该股成员归属', generation)
        value = mapping.get(symbol) if isinstance(mapping, dict) else None
        if not isinstance(value, dict):
            output[symbol] = missing
            continue
        if value.get('source_snapshot_id') not in (None, generation):
            output[symbol] = _classification('冻结归属来源代际与午评不符', generation)
            continue
        if value.get('state') == 'unknown' and value.get('industries') == [] and value.get('topics') == []:
            output[symbol] = {**missing, 'reason': value.get('reason') or missing['reason'],
                              'source_sha256': value.get('source_sha256')}
            continue
        if value.get('state') not in ('observed_sample', 'partial'):
            output[symbol] = missing
            continue
        valid = True
        fields = {}
        for field, kind, limit in (('industries', 'industry', MAX_INDUSTRIES), ('topics', 'concept', MAX_CONCEPTS)):
            rows = value.get(field)
            if not isinstance(rows, list) or len(rows) > limit:
                valid = False
                break
            fields[field] = []
            for row in rows:
                code = row.get('topic_code') if isinstance(row, dict) else None
                sector = sectors.get(code, {})
                if (not isinstance(row, dict) or row.get('kind') != kind or row.get('source') != 'fuyao'
                        or not isinstance(row.get('name'), str) or not row['name'].strip()
                        or len(row['name']) > 256 or sector.get('kind') != kind
                        or row.get('members_observed_date') != sector.get('members_observed_date')
                        or not _day(row.get('members_observed_date')) or row['members_observed_date'] > payload['date']):
                    valid = False
                    break
                fields[field].append({key: row[key] for key in ('topic_code', 'name', 'kind', 'members_observed_date', 'source')})
            if not valid:
                break
        output[symbol] = ({**missing, **fields, 'state': value['state'],
            'reason': value.get('reason') or '本份午评已冻结的成员归属；不是涨停原因',
            'source_sha256': value.get('source_sha256')} if valid and any(fields.values()) else missing)
    return output


def build_member_limit_context(payload):
    """Return dated sample counts, with no current head or mutable-store reads."""
    generation = _source_id(payload)
    symbols, _ = _target_symbols(payload)
    frozen_classes = 'stock_classifications' in payload
    classifications = (_frozen_classifications(payload, generation) if frozen_classes else
        {symbol: _classification('原采集代际成员资料未取得，不使用当前名单补写归属', generation) for symbol in symbols})
    sectors = payload.get('sectors')
    sectors = [row for row in sectors if isinstance(row, dict)] if isinstance(sectors, list) else []
    if len(sectors) > MAX_TOPICS:
        return {'source_snapshot_id': generation, 'state': 'unknown', 'counts': {},
                'stock_classifications': classifications, 'reason': '午评板块数量超过有界容量'}
    counts, pending = {}, []
    identities = Counter(_code(row) for row in sectors)
    for row in sectors:
        code = _code(row)
        if code is None:
            continue
        if identities[code] != 1:
            counts[code] = _result(row, reason='午评同代码板块重复，未合并统计')
        elif 'member_limit_up_count' in row:
            value, state = _count(row.get('member_limit_up_count')), row.get('member_limit_up_state')
            valid = value is not None and state in ('observed', 'partial') and (state == 'observed' or value > 0)
            counts[code] = _result(row, value if valid else None, state if valid else 'unknown',
                row.get('member_limit_up_reason') or '本份午评冻结的板块涨停样本统计', row.get('member_limit_up_source_sha256'))
        else:
            counts[code] = _result(row, reason='本份午评没有冻结成员涨停统计，原代际成员资料未取得')
            pending.append(row)
    if (pending or symbols and not frozen_classes) and generation is not None:
        try:
            topics, digest = _load_topics(generation, payload['previous_review']['date'])
            quotes = _quotes(payload)
            proofs = _member_proofs(payload, topics, quotes)
            up, complete = _limit_pool(payload, quotes)
            for row in pending:
                counts[_code(row)] = _member_count(row, proofs[_code(row)], up, complete, digest)
            if not frozen_classes:
                classifications = _classifications_from_proofs(payload, proofs, generation, digest)
        except (OSError, ValueError, TypeError, KeyError):
            for row in pending:
                counts[_code(row)] = _result(row, reason='原采集代际成员来源未通过身份、日期、路径或SHA核验，不使用当前名单替代')
    known = [row for row in counts.values() if row['member_limit_up_count'] is not None]
    state = 'observed' if counts and len(known) == len(counts) and all(row['state'] == 'observed' for row in known) else 'partial' if known else 'unknown'
    return {'source_snapshot_id': generation, 'state': state, 'counts': counts,
            'stock_classifications': classifications,
            'reason': '仅使用原采集代际成员及冻结午评池，缺失保留未知，不改写原快照'}
