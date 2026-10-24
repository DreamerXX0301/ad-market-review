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

ALGORITHM_VERSION = 'source-reason-directions-v2'
MAX_GROUPS = 3
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
                          r'非(?=人形机器人|机器人|具身智能)')
_NARRATIVE = re.compile(r'[，,、。；;：:！？!?]|公司|表示|回应|声称|收入|占比|主要从事|但尚|并未')
NOTICE = ('仅按本地同日已确认收盘涨停样本的来源原因自动归类；机器人相关采用有限正向标签家族，'
          '其他按首个明确来源标签或有限零售、业绩家族归类，不使用目录或证券名称补原因。'
          '重点方向最多三个且不硬凑，其他方向、事件催化、个股线索与来源空缺分别保留。'
          '每股仅一个展示位置，不认证上涨主因，也不认定龙头；闭市池证明和SHA仅核验收盘事件，不独立证明原因。')


def _day(value):
    try:
        return isinstance(value, str) and calendar_date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _has_reason(reason):
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


def _classification(tags):
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
    return ('exact:' + first, first, 'direction', 'exact-first-tag-v2', [first])


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


def build_system_mainlines(rows, *, day, snapshot_id, coverage=None, quotes=None):
    """Synthesize a deterministic, disjoint display partition of local samples.

    ``quotes`` can supply the caller's authoritative raw history rows when panel
    display rows omit raw fields. Missing quotes never fall back to pool prices.
    The optional coverage describes the source pool only, never all-market truth.
    """
    source_rows = rows if isinstance(rows, list) else []
    coverage = coverage if isinstance(coverage, dict) else {}
    result = {'algorithm_version': ALGORITHM_VERSION, 'source_kind': 'local_source_synthesis',
              'state': 'candidate', 'date': day, 'snapshot_id': snapshot_id,
              'groups': [], 'other_groups': [], 'individual_cues': [], 'missing_reason': [],
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
        value = {'row': row, 'tags': _tags(row.get('reason')), 'height': _height(row, day, snapshot_id),
                 'first_time': _first_time(row.get('first_limit_time')), 'refs': refs,
                 'source': source.strip() if isinstance(source, str) and source.strip() else None}
        # Equal repeated rows are one stock. Conflicting duplicates are excluded
        # instead of letting input order choose its reason, source or height.
        if symbol in eligible and any(eligible[symbol][key] != value[key]
                                      for key in ('tags', 'height', 'first_time', 'refs', 'source')):
            conflicts.add(symbol)
        if symbol in eligible and eligible[symbol]['row'].get('reason') != row.get('reason'):
            conflicts.add(symbol)
        eligible.setdefault(symbol, value)
    for symbol in conflicts:
        eligible.pop(symbol, None)
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
                'closing_pool_refs': item['refs']}

    buckets = defaultdict(list)
    classifications = {}
    for symbol in sorted(eligible, key=order_member):
        item = eligible[symbol]
        if not _has_reason(item['row'].get('reason')):
            result['missing_reason'].append(member(symbol, []))
            continue
        classification = _classification(item['tags'])
        if classification is None:
            result['individual_cues'].append(member(symbol, []))
            continue
        classifications[symbol] = classification
        buckets[classification[0]].append(symbol)
    groups = []
    for symbols in buckets.values():
        if len(symbols) < MIN_MEMBERS:
            symbol = symbols[0]
            result['individual_cues'].append(member(symbol, classifications[symbol][4]))
            continue
        ordered = sorted(symbols, key=order_member)
        key, name, kind, rule, _ = classifications[ordered[0]]
        highest = max((eligible[symbol]['height'] or 0 for symbol in ordered), default=0) or None
        labels = list(dict.fromkeys(tag for symbol in ordered for tag in classifications[symbol][4]))
        groups.append({'key': key, 'name': name, 'kind': kind, 'grouping_rule': rule, 'labels': labels,
                       'count': len(ordered), 'state': 'candidate', 'max_known_height': highest,
                       'high_stock_ids': [symbol for symbol in ordered if highest is not None and eligible[symbol]['height'] == highest],
                       'members': [member(symbol, classifications[symbol][4]) for symbol in ordered]})

    def order_group(group):
        first = min(eligible[item['instrument_id']]['first_time'] for item in group['members'])
        return (-group['count'], -(group['max_known_height'] or 0), first, group['name'], group['key'])

    groups.sort(key=order_group)
    result['groups'] = [group for group in groups if group['kind'] == 'direction'
                        and group['count'] >= MIN_FOCUS_MEMBERS][:MAX_GROUPS]
    focus_keys = {group['key'] for group in result['groups']}
    result['other_groups'] = [group for group in groups if group['key'] not in focus_keys]
    for key in ('individual_cues', 'missing_reason'):
        result[key].sort(key=lambda item: order_member(item['instrument_id']))
    result['coverage'].update(confirmed_sample_count=len(eligible),
                              eligible_reason_count=len(eligible) - len(result['missing_reason']),
                              focus_count=sum(group['count'] for group in result['groups']),
                              other_count=sum(group['count'] for group in result['other_groups']),
                              individual_count=len(result['individual_cues']),
                              missing_reason_count=len(result['missing_reason']),
                              rejected_row_count=len(source_rows) - len(eligible))
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
