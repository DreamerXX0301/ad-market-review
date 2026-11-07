"""Bounded classification summary of the original top-1000 30-session rank.

Price ranking may be cached by market generation. Editable current memberships
are deliberately read once per request, and are never projected to old dates.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import re

from fastapi import HTTPException

SAMPLE_LIMIT = 1000
VISIBLE_LIMIT = 5
# Reviewed provider concepts describe trading eligibility, rather than an
# economic/policy/industry research theme. Do not guess from substrings or
# remove a user-created manual topic bearing the same name.
TOPIC_DEFINITION = '经济/政策/产业题材分布，不含已审定交易资格标签'
TRADING_ATTRIBUTE_NAMES = ('融资融券', '沪股通', '深股通')
TRADING_ATTRIBUTES = {('fuyao', 'concept', name) for name in TRADING_ATTRIBUTE_NAMES}


def _trading_attribute(tag):
    return (tag.get('source'), tag.get('kind'), tag.get('name')) in TRADING_ATTRIBUTES


def _identity(tag):
    return f"{tag.get('source', 'unknown')}:{tag['kind']}:{tag.get('source_code') or tag['topic_id']}"


def _day(value):
    if not isinstance(value, str):
        return None
    try:
        return value if date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def _tag(item, day, *, current=False):
    if not isinstance(item, dict) or item.get('kind') not in ('concept', 'industry', 'manual'):
        return None
    kind = item['kind']
    identifier = item.get('topic_id')
    name = item.get('name')
    observed = item.get('source_observed_date', item.get('observed_date'))
    # Current manual revisions have an explicit effective business day, not a
    # provider collection day. Provider/archived claims still require a date.
    if not (current and kind == 'manual') and (not _day(observed) or observed > day):
        return None
    if not isinstance(identifier, str) or not identifier or not isinstance(name, str) or not name.strip():
        return None
    return {**item, 'topic_id': identifier, 'name': name.strip(), 'kind': kind,
            'source_observed_date': observed,
            'source': item.get('source') or ('local-manual' if kind == 'manual' else 'local-provider')}


def _reference_memberships(topics, symbols, day):
    result = {symbol: [] for symbol in symbols}
    selected = set(symbols)
    for item in topics:
        tag = _tag(item, day)
        if tag is None:
            continue
        members = item.get('instrument_ids')
        if not isinstance(members, list):
            continue
        for symbol in selected.intersection(value for value in members if isinstance(value, str)):
            result[symbol].append(tag)
    return result


def _memberships(market, status, reference, day, sample, connection_factory=None, *, include_classifications=False,
                 include_latest_classification_reference=False):
    symbols = [row['instrument_id'] for row in sample]
    if not symbols:
        return {}, {'state': 'empty', 'scope': '所选日没有可排名证券', 'snapshot_id': status.get('snapshot_id')}
    current = day == status.get('data_end')
    if current and any(callable(getattr(market, name, None)) for name in ('topics', 'topics_data', 'topic_catalog')):
        from app.services.stock_memberships import current_memberships, merge_latest_provider_memberships
        try:
            kwargs = {'expected_day': day, 'include_unregistered_provider': True}
            if connection_factory is not None:
                kwargs['connection_factory'] = connection_factory
            if include_classifications:
                kwargs['include_classifications'] = True
            data = current_memberships(market, symbols, **kwargs)
        except HTTPException as error:
            if error.status_code != 409:
                raise
            return {}, {'state': 'unavailable', 'scope': '分类业务日与所选行情日不一致，分类保持未知',
                        'snapshot_id': status.get('snapshot_id')}
        found = {symbol: [tag for item in data['memberships'].get(symbol, [])
                          if (tag := _tag(item, day, current=True)) is not None] for symbol in symbols}
        classification_reference = None
        if include_latest_classification_reference:
            found, classification_reference = merge_latest_provider_memberships(found, data['memberships'], day)
        return found, {'state': 'current_business_partial', 'scope': data['scope'],
                       'classification_reference': classification_reference,
                       'business_date': data['business_date'], 'snapshot_id': status.get('snapshot_id'),
                       'snapshot_day': day, 'classifications': data.get('classifications', [])}
    if current:
        # Non-catalog/older imported readers can supply an explicitly dated
        # retained reference. Bare names from a cached rank are not evidence.
        topics = reference.get('topics', [])
        found = _reference_memberships(topics, symbols, day)
        return found, {'state': 'dated_partial', 'scope': '仅按所选日已留存且采集日不晚于观察日的分类样本统计',
                       'snapshot_id': status.get('snapshot_id'), 'classifications': topics,
                       'snapshot_day': day}
    from app.services.market_history import resolve_reference
    local, coverage = resolve_reference('sectors', day, status, reference)
    topics = local.get('topics', [])
    if include_classifications and not coverage.get('selected_date_available'):
        topics = []
    found = _reference_memberships(topics, symbols, day)
    return found, {'state': 'dated_partial' if coverage.get('selected_date_available') else 'unavailable',
                   'scope': coverage.get('notice'), 'snapshot_id': coverage.get('source_snapshot_id'),
                   'snapshot_day': coverage.get('source_snapshot_day'), 'classifications': topics}


def _groups(sample, memberships, *, industry):
    counts = Counter()
    definitions = {}
    classified = 0
    attribute_only = 0
    overlapping = False
    for row in sample:
        unique = {}
        attribute_seen = False
        for tag in memberships.get(row['instrument_id'], []):
            if (tag['kind'] == 'industry') != industry:
                continue
            if not industry and _trading_attribute(tag):
                attribute_seen = True
                continue
            identity = _identity(tag)
            unique[identity] = tag
        if unique:
            classified += 1
            overlapping = overlapping or len(unique) > 1
        elif attribute_seen:
            attribute_only += 1
        for identity, tag in unique.items():
            counts[identity] += 1
            definitions[identity] = tag
    denominator = len(sample)
    ordered = sorted(counts, key=lambda identity: (-counts[identity], definitions[identity]['name'], identity))
    return {'top': [{'id': identity, 'topic_id': definitions[identity]['topic_id'],
                     'name': definitions[identity]['name'], 'count': counts[identity],
                     'share_pct': round(counts[identity] * 100 / denominator, 2),
                     'data_kind': definitions[identity]['kind'], 'source': definitions[identity].get('source'),
                     'source_code': definitions[identity].get('source_code') or definitions[identity].get('topic_code'),
                     'classification_level': definitions[identity].get('classification_level', definitions[identity].get('level')),
                     'source_observed_date': definitions[identity].get('source_observed_date')}
                    for identity in ordered[:VISIBLE_LIMIT]],
            'classified_count': classified, 'attribute_only_count': attribute_only,
            'unknown_count': denominator-classified-attribute_only,
            'total_categories': len(counts), 'overlap': overlapping, 'overlap_possible': True}


def _excluded_attributes(sample, memberships):
    counts = Counter()
    definitions = {}
    unique_stocks = 0
    for row in sample:
        unique = {_identity(tag): tag for tag in memberships.get(row['instrument_id'], [])
                  if _trading_attribute(tag)}
        unique_stocks += bool(unique)
        for identity, tag in unique.items():
            counts[identity] += 1
            definitions[identity] = tag
    denominator = len(sample)
    return {'reason': 'trading_eligibility', 'unique_stock_count': unique_stocks,
            'total_assignments': sum(counts.values()),
            'mapping': [{'source': 'fuyao', 'data_kind': 'concept', 'name': name}
                        for name in TRADING_ATTRIBUTE_NAMES],
            'items': [{'id': identity, 'source': definitions[identity]['source'],
                       'data_kind': definitions[identity]['kind'],
                       'source_code': definitions[identity].get('source_code'),
                       'topic_id': definitions[identity]['topic_id'],
                       'name': definitions[identity]['name'], 'count': counts[identity],
                       'share_pct': round(counts[identity]*100/denominator, 2)}
                      for identity in sorted(counts, key=lambda key: (-counts[key], key))]}


def _rank_sample(ranked):
    eligible = sorted((row for row in ranked if type(row.get('rank')) is int
                       and 0 < row['rank'] <= SAMPLE_LIMIT and row.get('instrument_id')),
                      key=lambda row: (row['rank'], row['instrument_id']))
    sample = []
    seen = set()
    for row in eligible:
        if row['instrument_id'] not in seen:
            sample.append(row)
            seen.add(row['instrument_id'])
            if len(sample) == SAMPLE_LIMIT:
                break
    return sample


def ranking_distribution(market, status, reference, day, ranked, *, connection_factory=None,
                         include_latest_classification_reference=False):
    """Return copied rank rows plus an independent, fixed pre-filter summary."""
    sample = _rank_sample(ranked)
    seen = {row['instrument_id'] for row in sample}
    memberships, context = _memberships(
        market, status, reference, day, sample, connection_factory,
        include_latest_classification_reference=include_latest_classification_reference,
    )
    distribution = {'version': 'gainers-top1000-distribution-v1', 'date': day,
                    'sample_limit': SAMPLE_LIMIT, 'sample_count': len(sample),
                    'sample_scope': 'top_1000_before_filters', 'rank_basis': 'return_30d',
                    'classification_state': context['state'], 'classification_scope': context['scope'],
                    'classification_snapshot_id': context.get('snapshot_id'),
                    'topics': {**_groups(sample, memberships, industry=False),
                               'definition': TOPIC_DEFINITION},
                    'industries': _groups(sample, memberships, industry=True),
                    'excluded_attributes': _excluded_attributes(sample, memberships)}
    # Preserve cached ranks. Sample rows show the same freshly read labels used
    # by their summary, including explicitly known industry/manual kinds.
    if context.get('classification_reference'):
        distribution['classification_reference'] = context['classification_reference']
    rows = [{**row, 'topics': memberships.get(row['instrument_id'], []),
             'industries': [tag for tag in memberships.get(row['instrument_id'], []) if tag['kind'] == 'industry'],
             'topics_observed_date': day, 'topics_scope': context['scope']}
            if row.get('instrument_id') in seen else row for row in ranked]
    return rows, distribution


def classification_lookup(market, status, reference, day, ranked, *, mode, query, connection_factory=None):
    """Match dated classifications against the same original ranked sample."""
    sample = _rank_sample(ranked)
    memberships, context = _memberships(market, status, reference, day, sample, connection_factory,
                                        include_classifications=True)
    folded = query.casefold()

    def key(tag):
        return (tag['topic_id'], tag['kind'], tag['source'])

    def matches(tag):
        if mode == 'topic_id':
            return tag['topic_id'] == query
        return folded in tag['name'].casefold()

    categories = {}
    current = context['state'] == 'current_business_partial'
    for item in context.get('classifications', []):
        if not current and not isinstance(item.get('instrument_ids') if isinstance(item, dict) else None, list):
            continue
        tag = _tag(item, day, current=current)
        if tag is None or not matches(tag):
            continue
        identifier = key(tag)
        category = {field: tag.get(field) for field in
                    ('topic_id', 'name', 'kind', 'source', 'source_code', 'source_observed_date',
                     'members_complete', 'egress_allowed', 'ai_processing_allowed')}
        category['source_code'] = category['source_code'] or tag.get('topic_code')
        if not current and category['members_complete'] is True:
            category['members_complete'] = all(
                isinstance(symbol, str) and re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)', symbol)
                for symbol in tag['instrument_ids'])
        if prior := categories.get(identifier):
            category['members_complete'] = (category['members_complete'] is True
                                            and prior['members_complete'] is True
                                            and category['source_observed_date'] == prior['source_observed_date'])
            for field in ('egress_allowed', 'ai_processing_allowed'):
                if prior[field] is False:
                    category[field] = False
        categories[identifier] = category

    counts = Counter()
    rows = []
    for row in sample:
        matched = sorted({key(tag) for tag in memberships.get(row['instrument_id'], []) if key(tag) in categories})
        if not matched:
            continue
        counts.update(matched)
        rows.append({**row, 'matched_classifications': [categories[identifier] for identifier in matched]})
    complete = bool(categories) and all(item['members_complete'] is True for item in categories.values())
    state = ('empty_sample' if not sample else 'matched' if rows
             else 'confirmed_zero' if complete else 'unknown')
    ordered = sorted(categories, key=lambda identifier: (-counts[identifier], categories[identifier]['name'], identifier))
    return rows, {
        'mode': mode, 'query': query, 'state': state, 'sample_count': len(sample),
        'sample_limit': SAMPLE_LIMIT, 'sample_scope': 'top_1000_before_filters',
        'observed_count': len(rows), 'matched_count': len(rows) if complete else None,
        'categories': [{**categories[identifier], 'observed_count': counts[identifier],
                        'matched_count': counts[identifier] if categories[identifier]['members_complete'] is True else None}
                       for identifier in ordered],
        'coverage': {'state': context['state'], 'scope': context['scope'], 'members_complete': complete,
                     'classification_snapshot_id': context.get('snapshot_id'),
                     'classification_snapshot_day': context.get('snapshot_day'),
                     'membership_business_date': context.get('business_date')},
    }
