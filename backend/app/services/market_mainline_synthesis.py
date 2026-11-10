"""按同日已留存原因自动分区；展示归类不认证上涨主因。

The caller supplies its already-read immutable generation and raw quote batch.
This module has no database, provider or task dependencies. Closing-pool proof
verifies the event; it does not independently verify the provider's reason text.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date as calendar_date
import math
import re

ALGORITHM_VERSION = 'source-reason-directions-v4'
MAX_GROUPS = 3
MAX_RELATED_GROUPS = 24
MIN_MEMBERS = 2
MIN_FOCUS_MEMBERS = 3
MAX_TAG_LENGTH = 40
MAX_REASON_LENGTH = 2000
MAX_TAGS_PER_REASON = 24
_STOCK = re.compile(r'\d{6}\.(SH|SZ|BJ)')
_SHA = re.compile(r'[0-9a-fA-F]{64}')
_EMPTY_TAGS = {'未知', '暂无', '暂无数据', '未提供', '未归因', '待核验', '无', '其他',
               'n/a', 'na', 'none', 'null', 'unknown', '--', '-', '—', '–'}
_RETAIL_TAGS = {'商贸零售', '商业零售', '零售促销'}
_EARNINGS_TAGS = {'业绩增长', '三季报增长', '半年报增长', '三季度增长', '业绩预增',
                  '净利润增长', '业绩扭亏', '毛利率提升'}
_DIRECTION_FAMILIES = (
    ('family:snow', '冰雪经济', {'冰雪经济', '冰雪产业', '冰雪旅游', '冰雪旅游保障', '冰雪工程'}, '冰雪'),
    ('family:coal', '煤炭', {'煤炭', '煤炭涨价', '煤炭主业', '煤炭开采', '煤炭贸易', '煤矿'}, '煤炭|煤矿'),
    ('family:short-drama', '短剧与漫剧', {'AI漫剧', 'AI短剧', 'AI动画短剧', '漫剧', '微短剧', '短剧'}, '短剧|漫剧'),
    ('family:aerospace', '航空航天', {'航空航天', '商业航天', '航天装备', '航材国产化', '航天紧固件',
                                '卫星通信终端', '卫星通信', '卫星互联网', '卫星制造', '太空算力',
                                '星载算力一体机', '星载算力'}, '航天|航材|卫星|太空|星载'),
    ('family:ai-app', 'AI应用', {'AI应用', 'AI设计', 'AI营销', 'AI航检眼镜', 'AI漫剧', 'AI短剧',
                             'AI动画短剧'}, 'AI应用|AI设计|AI营销|AI航检|AI漫剧|AI短剧|AI动画短剧'),
    ('family:pcb', 'PCB', {'PCB', 'PCB制造', '印制电路板', '印刷电路板', '印制线路板',
                          '人形机器人PCB', 'AI服务器PCB'}, 'PCB|印制电路板|印刷电路板|印制线路板'),
    ('family:photovoltaic', '光伏', {'光伏', '光伏BIPV', '光伏边框', '光伏组件', '光伏电池',
                                  '光伏电站', '光伏银粉', '光伏玻璃', '风电光伏'}, '光伏'),
)
_FAMILY_TAGS = {tag for _, _, tags, _ in _DIRECTION_FAMILIES for tag in tags}
_WORD_FAMILY = re.compile(r'[“"「『]?([\u3400-\u9fff])[”"」』]?字辈')
_BACKGROUND = re.compile(r'^(?:.{0,8}国资(?:控股|入主)?|国企(?:控股|改革)?|.{1,5}国企|央企|次新股|'
                         r'回购(?:注销)?|股权(?:转让|受让)|重组|三季报|半年报|三季度|年报|业绩|净利润|毛利率)')
_NONPOSITIVE = re.compile(r'不涉及|未涉及|尚未|尚不|未曾|从未|不从事|未从事|未布局|不布局|'
                          r'不生产|未生产|未开展|不开展|尚无|暂无|并无|没有|不具备|不属于|不是|'
                          r'未进入|未涉足|未参与|未提供|未有|不做|不搞|不再|不相关|不含|无关|否认|澄清|'
                          r'传闻|网传|是否|疑似|不确定|不构成|不属实|有望|可能|或将|有待|暂未|'
                          r'拟布局|拟涉足|拟进入|拟投资|拟开展|'
                          r'未(?:实际|直接|正式)(?:布局|涉及|开展|从事|进入|涉足)|'
                          r'无(?:相关)?(?:产品|业务|收入|布局|订单)|'
                          r'无(?:任何)?(?:人形|工业|仿生|分拣|医疗物流|无人)?机器人(?:相关)?(?:业务|产品|收入|订单)|'
                          r'无(?:任何)?具身智能(?:相关)?(?:业务|产品|收入|订单)|'
                          r'(?:业务|收入|产品|订单)(?:为零|为0|已终止|终止|已停止|已暂停|已剥离|已退出|停止|暂停|不存在)|'
                          r'非(?=人形机器人|机器人|具身智能)|'
                          r'(?:非|无(?:任何|直接)?)(?=冰雪|煤炭|煤矿|短剧|漫剧|(?:商业)?航天|卫星|航材|太空|星载|'
                          r'PCB|印制电路板|印刷电路板|印制线路板|光伏|'
                          r'AI(?:应用|设计|营销|航检|漫剧|短剧|动画短剧))', re.IGNORECASE)
_NARRATIVE = re.compile(r'[，,、。；;：:！？!?]|公司|表示|回应|声称|收入|占比|主要从事|但尚|并未')
NOTICE = ('仅按本地同日已确认收盘涨停样本的来源原因自动归类；机器人相关采用有限正向标签家族，'
          '其他主归类按首个明确来源标签或有限家族归类，不使用目录或证券名称补原因。'
          '重点方向最多三个且不硬凑，其他方向、事件催化、个股线索与来源空缺分别保留。'
          '每股仅一个主归类；来源关联可查看同一股票的多条标签，不重复计入主归类总数。'
          '不认证上涨主因，也不认定龙头；闭市池证明和SHA仅核验收盘事件，不独立证明原因。')


def _day(value):
    try:
        return isinstance(value, str) and calendar_date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def has_source_reason(reason):
    if reason is None:
        return False
    if not isinstance(reason, str):
        return True
    return any(tag.strip().casefold() not in _EMPTY_TAGS
               and re.search(r'[\w\u3400-\u9fff]', tag) for tag in re.split(r'[+＋]', reason))


def _tags(reason):
    if not isinstance(reason, str) or len(reason) > MAX_REASON_LENGTH:
        return []
    pieces = re.split(r'[+＋]', reason)
    if len(pieces) > MAX_TAGS_PER_REASON:
        return []
    found = []
    for piece in pieces:
        tag = piece.strip()
        if (not tag or tag.casefold() in _EMPTY_TAGS
                or not re.search(r'[\w\u3400-\u9fff]', tag) or tag in found):
            continue
        found.append(tag)
    return found


def _explicit_label(tag):
    return (len(tag) <= MAX_TAG_LENGTH and not any(ord(char) < 32 for char in tag)
            and not _NONPOSITIVE.search(tag) and not _NARRATIVE.search(tag))


def _robot_word(tag):
    return '机器人' in tag or '具身智能' in tag


def _source_directions(tags):
    """Read the provider's positive tags across the full reason, without guessing membership."""
    rejected = [tag for tag in tags if not _explicit_label(tag)]
    explicit = [tag for tag in tags if _explicit_label(tag)]
    for key, name, aliases, conflict in _DIRECTION_FAMILIES:
        matches = [tag for tag in explicit if tag in aliases]
        if matches and not any(re.search(conflict, tag, re.IGNORECASE) for tag in rejected):
            yield (key, name, 'explicit-source-family-v3', matches)
            if key == 'family:aerospace' and '商业航天' in matches:
                yield ('exact:商业航天', '商业航天', 'exact-shared-source-tag-v3', ['商业航天'])
    words = defaultdict(list)
    for tag in explicit:
        if any(tag in other for other in rejected):
            continue
        word = _WORD_FAMILY.fullmatch(tag)
        if word:
            words[word[1]].append(tag)
        elif (tag not in _FAMILY_TAGS and tag not in _RETAIL_TAGS and tag not in _EARNINGS_TAGS
              and not _BACKGROUND.search(tag) and not _robot_word(tag)):
            yield ('exact:' + tag, tag, 'exact-shared-source-tag-v3', [tag])
    for word, matches in words.items():
        if not any(re.search(re.escape(word) + r'[”"」』]?字辈', tag) for tag in rejected):
            yield ('family:word:' + word, f'“{word}”字辈', 'word-family-v3', matches)


def _classification(tags, source_directions):
    robot = [tag for tag in tags if _robot_word(tag) and _explicit_label(tag)]
    if robot:
        # 同一原因中正反叙述冲突时保持个股线索，不挑其中一句制造方向。
        if any(_robot_word(tag) and not _explicit_label(tag) for tag in tags):
            return None
        return ('family:robot', '机器人相关', 'direction', 'robot-positive-tag-family-v2', robot)
    if not tags or not _explicit_label(tags[0]):
        return None
    first = tags[0]
    if first in _RETAIL_TAGS:
        return ('family:retail', '零售', 'direction', 'retail-first-tag-family-v2', [first])
    if first in _EARNINGS_TAGS:
        return ('catalyst:earnings', '业绩线索', 'catalyst', 'earnings-first-tag-family-v2', [first])
    if first in _FAMILY_TAGS or _WORD_FAMILY.fullmatch(first):
        match = next((entry for entry in source_directions if first in entry[3]), None)
        if match:
            key, name, rule, matches = match
            return (key, name, 'direction', rule, matches)
        # A conflicting source description must not regain a positive direction
        # just because its first tag is an exact spelling of a family.
        return None
    return ('exact:' + first, first, 'direction', 'exact-first-tag-v2', [first])


def classify_source_reason(reason):
    """Return the primary classification and secondary tags of a source reason."""
    tags = _tags(reason)
    directions = list(_source_directions(tags))
    return _classification(tags, directions), directions


def _first_time(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{2}:\d{2}(:\d{2})?', value):
        return '99:99:99'
    parts = [int(part) for part in value.split(':')]
    if parts[0] > 23 or parts[1] > 59 or (len(parts) == 3 and parts[2] > 59):
        return '99:99:99'
    return value if len(parts) == 3 else value + ':00'


def _height(row, day, snapshot):
    facts = row.get('board_presentation')
    if (not isinstance(facts, dict) or facts.get('version') != 'dated-stock-board-presentation-v1'
            or facts.get('date') != day or facts.get('snapshot_id') != snapshot
            or facts.get('instrument_id') != row['instrument_id']):
        return None
    height = facts.get('height', {})
    if not isinstance(height, dict):
        return None
    value = height.get('value')
    return int(value) if (height.get('state') == 'confirmed' and height.get('source_date') == day
                          and isinstance(height.get('label'), str) and height['label'].strip()
                          and _positive(value) and int(value) == value) else None


def _closing_refs(row, quote, day):
    state = row.get('limit_up_evidence')
    if (row.get('limit_up_state') is not True or not isinstance(state, dict)
            or state.get('state') is not True or state.get('status') != 'confirmed'):
        return None
    evidence = state.get('evidence')
    if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
        return None
    if any(item.get('status') == 'conflict' or item.get('state') is False for item in evidence):
        return None
    positive = [item for item in evidence if item.get('state') is True]
    if not positive:
        return None
    refs = {}
    for item in positive:
        binding = item.get('pattern_validation', {})
        prices = item.get('observed_prices')
        if (item.get('status') != 'confirmed' or not isinstance(binding, dict) or binding.get('instrument_id') != row['instrument_id']
                or binding.get('trade_date') != day or binding.get('source_identity_valid') is not True
                or binding.get('source_dates_valid') is not True or not isinstance(prices, list) or not prices
                or any(not _positive(price) or abs(price - quote['close']) > .011 for price in prices)):
            return None
        ref = {'source': item.get('source') if isinstance(item.get('source'), str) else None,
               'path': item.get('path') if isinstance(item.get('path'), str) else None,
               'purpose': 'closing_event_only'}
        if isinstance(item.get('raw_sha256'), str) and _SHA.fullmatch(item['raw_sha256']):
            ref['raw_sha256'] = item['raw_sha256'].lower()
        key = (ref['source'] or '', ref['path'] or '', ref.get('raw_sha256', ''))
        refs[key] = ref
    return [refs[key] for key in sorted(refs)]


def build_system_mainlines(rows, *, day, snapshot_id, coverage=None, quotes=None, overrides=None):
    """Synthesize a deterministic, disjoint display partition of local samples.

    ``quotes`` can supply the caller's authoritative raw history rows when panel
    display rows omit raw fields. Missing quotes never fall back to pool prices.
    The optional coverage describes the source pool only, never all-market truth.
    """
    source_rows = rows if isinstance(rows, list) else []
    coverage = coverage if isinstance(coverage, dict) else {}
    result = {'algorithm_version': ALGORITHM_VERSION, 'source_kind': 'local_source_synthesis',
              'state': 'candidate', 'date': day, 'snapshot_id': snapshot_id,
              'groups': [], 'other_groups': [], 'related_groups': [], 'individual_cues': [], 'missing_reason': [],
              'coverage': {'scope': 'local_confirmed_sample_only', 'examined_row_count': len(source_rows),
                           'confirmed_sample_count': 0, 'eligible_reason_count': 0,
                           'focus_count': 0, 'other_count': 0, 'individual_count': 0,
                           'missing_reason_count': 0, 'rejected_row_count': len(source_rows),
                           'source_pool_complete': coverage.get('source_pool_complete') is True,
                           'market_complete': False},
              'reason': None, 'notice': NOTICE}
    if not _day(day) or not isinstance(snapshot_id, str) or not snapshot_id.strip():
        result.update(state='unavailable', reason='缺少明确观察日或一致的已发布行情代际，未整理来源候选')
        return result
    eligible = {}
    conflicts = set()
    for row in source_rows:
        if not isinstance(row, dict):
            continue
        symbol = row.get('instrument_id')
        if (not isinstance(symbol, str) or not _STOCK.fullmatch(symbol) or row.get('date') != day
                or row.get('snapshot_id') != snapshot_id
                or any(key in row and row[key] != day for key in ('trade_date', 'evidence_date'))
                or row.get('kind', 'stock') != 'stock'):
            continue
        quote = quotes.get(symbol) if isinstance(quotes, dict) else row
        if (not isinstance(quote, dict) or quote.get('date', quote.get('trade_date')) != day
                or any(key in quote and quote[key] != day for key in ('date', 'trade_date'))
                or quote.get('price_basis', 'none') != 'none'
                or quote.get('snapshot_id', snapshot_id) != snapshot_id
                or any(key in quote and quote[key] != symbol for key in ('instrument_id', 'thscode'))
                or not _positive(quote.get('close')) or not _positive(quote.get('volume'))):
            continue
        refs = _closing_refs(row, quote, day)
        if refs is None:
            continue
        # Detail metadata can describe a later overlaid price/time feed rather
        # than the retained reason field. Do not assign its vendor to the cause.
        source = None
        value = {'row': row, 'height': _height(row, day, snapshot_id),
                 'first_time': _first_time(row.get('first_limit_time')), 'refs': refs,
                 'source': source.strip() if isinstance(source, str) and source.strip() else None}
        # Equal repeated rows are one stock. Conflicting duplicates are excluded
        # instead of letting input order choose its reason, source or height.
        if symbol in eligible and any(eligible[symbol][key] != value[key]
                                      for key in ('height', 'first_time', 'refs', 'source')):
            conflicts.add(symbol)
        if symbol in eligible and eligible[symbol]['row'].get('reason') != row.get('reason'):
            conflicts.add(symbol)
        eligible.setdefault(symbol, value)
    for symbol in conflicts:
        eligible.pop(symbol, None)
    overrides = {symbol: value for symbol, value in (overrides or {}).items()
                 if symbol in eligible and value.get('topic_id')}
    def order_member(symbol):
        item = eligible[symbol]
        return (-(item['height'] or 0), item['first_time'], symbol)

    def member(symbol, matched):
        item = eligible[symbol]
        return {'instrument_id': symbol, 'source_reason': item['row'].get('reason'),
                'matched_tag': matched[0] if matched else None, 'matched_tags': list(matched),
                'source': item['source'],
                'source_ref': {'snapshot_id': snapshot_id, 'date': day,
                               'endpoint': '/api/v1/market/ladder',
                               'field_path': f'items[instrument_id="{symbol}"].reason'},
                'closing_pool_refs': item['refs'],
                **({'manual_override': overrides[symbol]} if symbol in overrides else {})}

    def make_group(key, name, kind, rule, members):
        ordered = sorted(members, key=lambda item: order_member(item[0]))
        highest = eligible[ordered[0][0]]['height']
        labels = list(dict.fromkeys(tag for _, matched in ordered for tag in matched))
        return {'key': key, 'name': name, 'kind': kind, 'grouping_rule': rule, 'labels': labels,
                'count': len(ordered), 'state': 'candidate', 'max_known_height': highest,
                'high_stock_ids': [symbol for symbol, _ in ordered
                                   if highest is not None and eligible[symbol]['height'] == highest],
                'members': [member(symbol, matched) for symbol, matched in ordered],
                **({'manual_count': sum(symbol in overrides for symbol, _ in ordered)}
                   if any(symbol in overrides for symbol, _ in ordered) else {})}

    buckets = defaultdict(list)
    relations = defaultdict(list)
    relation_names = {}
    classifications = {}
    for symbol in sorted(eligible, key=order_member):
        item = eligible[symbol]
        reason = item['row'].get('reason')
        if override := overrides.get(symbol):
            key = override['topic_id'].removeprefix('reason:')
            classifications[symbol] = (key, override['name'], 'direction', 'manual-placement-v1', [])
            buckets[key].append(symbol)
            continue
        if not has_source_reason(reason):
            result['missing_reason'].append(member(symbol, []))
            continue
        classification, source_directions = classify_source_reason(reason)
        for key, name, rule, matches in source_directions:
            relation_names[key] = (name, rule)
            relations[key].append((symbol, matches))
        if classification is None:
            result['individual_cues'].append(member(symbol, []))
            continue
        classifications[symbol] = classification
        buckets[classification[0]].append(symbol)
    groups = []
    for symbols in buckets.values():
        if len(symbols) < MIN_MEMBERS and not any(symbol in overrides for symbol in symbols):
            symbol = symbols[0]
            result['individual_cues'].append(member(symbol, classifications[symbol][4]))
            continue
        key, name, kind, rule, _ = classifications[next((symbol for symbol in symbols if symbol not in overrides), symbols[0])]
        groups.append(make_group(key, name, kind, rule, [(symbol, classifications[symbol][4]) for symbol in symbols]))

    def order_group(group):
        first = min(eligible[item['instrument_id']]['first_time'] for item in group['members'])
        return (-group['count'], -(group['max_known_height'] or 0), first, group['name'], group['key'])

    groups.sort(key=order_group)
    result['groups'] = [group for group in groups if group['kind'] == 'direction'
                        and group['count'] >= MIN_FOCUS_MEMBERS][:MAX_GROUPS]
    focus_keys = {group['key'] for group in result['groups']}
    result['other_groups'] = [group for group in groups if group['key'] not in focus_keys]
    primary_ids = {group['key']: {item['instrument_id'] for item in group['members']
                                  if item['instrument_id'] not in overrides} for group in groups}
    related = [make_group(key, relation_names[key][0], 'direction', relation_names[key][1], matches)
               for key, matches in relations.items() if len(matches) >= MIN_MEMBERS
               and primary_ids.get(key, set()) < {symbol for symbol, _ in matches}]
    related.sort(key=lambda group: (group['key'].startswith('exact:') and group['key'] != 'exact:商业航天',
                                    order_group(group)))
    result['related_groups'] = related[:MAX_RELATED_GROUPS]
    for key in ('individual_cues', 'missing_reason'):
        result[key].sort(key=lambda item: order_member(item['instrument_id']))
    result['coverage'].update(confirmed_sample_count=len(eligible),
                              eligible_reason_count=sum(has_source_reason(item['row'].get('reason')) for item in eligible.values()),
                              focus_count=sum(group['count'] for group in result['groups']),
                              other_count=sum(group['count'] for group in result['other_groups']),
                              individual_count=len(result['individual_cues']),
                              missing_reason_count=len(result['missing_reason']),
                              rejected_row_count=len(source_rows) - len(eligible))
    if overrides:
        result['coverage']['manual_count'] = len(overrides)
        result['notice'] += ' 人工归类仅作用于指定交易日的展示，保留来源原因原文。'
    reasons = []
    if coverage.get('complete') is not True or coverage.get('source_pool_complete') is not True:
        reasons.append('来源池或逐股报价覆盖不完整，仅整理已返回且可核验的本地样本，不代表全市场')
    if not result['groups']:
        reasons.append('本次没有达到至少3只的重点方向，已自动保留其他分组与个股线索')
    if result['missing_reason']:
        reasons.append('部分来源原因未提供，状态保持未知')
    if result['coverage']['rejected_row_count']:
        reasons.append('日期、代际、原始成交、闭市池证据或重复行不满足一致性条件的输入未计入')
    result['reason'] = '；'.join(reasons) or None
    return result
