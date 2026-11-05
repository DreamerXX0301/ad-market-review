"""Lossless shared schemas for a dense noon/full-day reply, without dropping phases."""
from collections import Counter

from app.core import dumps


def compact_prefix(result):
    summary, bindings = result['summary'], result['source_bindings']
    context = summary.get('close_context') or summary.get('noon_context') or {}
    if not context.get('cutoff_at'):
        return
    # The same authoritative phase states already appear in the causal context.
    summary.pop('phases', None)
    summary.pop('calendar_binding', None)  # Same binding remains in source_bindings.
    if not result['metrics']:
        summary.pop('metric_defaults', None)
        summary.pop('metric_semantics', None)
    stages = bindings.get('close_intraday') or bindings.get('noon_morning') or {}
    fingerprints = Counter(dumps(row['member_reference']) for row in stages.values() if 'member_reference' in row)
    shared = {}
    for row in stages.values():
        proof = row.get('member_reference')
        if proof is not None and fingerprints[dumps(proof)] > 1:
            identity = next((key for key, value in shared.items() if value == proof), None)
            identity = identity or 'members-' + str(len(shared) + 1)
            shared[identity] = proof
            row.pop('member_reference')
            row['member_reference_ref'] = identity
    if shared:
        bindings['member_reference_sources'] = shared
    for row in stages.values():
        previous = row.get('board_change_previous')
        if previous:
            prior = stages.get(previous.get('slot'))
            if prior and all(prior.get(key) == previous.get(key) for key in ('snapshot_id', 'content_sha256')):
                for key in ('source_id', 'collected_start_at', 'collected_end_at'):
                    if key in previous:
                        prior[key] = previous[key]
                row['board_change_previous'] = {'source_slot_ref': previous['slot']}
    columns = {
        'board_changes.rows': ['kind', 'state', 'count', 'classification_state', 'total', 'classified', 'missing'],
        'ladder.values': ['state', 'sealed_count', 'height_state', 'highest_board', 'first_board_count', 'multi_board_count', 'unknown_height_count'],
        'ladder.broken_values': ['state', 'count', 'conflict_count'],
        'interval_amount.values': ['amount', 'previous_amount', 'comparison_amount', 'change_from_previous', 'previous_date', 'comparable_count', 'previous_comparable_count', 'state'],
        'previous_limit_up_feedback.values': ['date', 'state', 'total', 'priced', 'average_change_pct', 'promotion_count', 'promotion_state'],
        'same_day_limit_up_feedback.values': ['state', 'label', 'collected_end_at', 'held_count', 'entered_count', 'absent_count', 'held_state', 'entered_state', 'absent_state'],
        'breadth.values': ['up', 'down', 'flat', 'valid', 'observed'],
        'direction_discovery.values': ['state', 'reason', 'baseline_slot', 'matching_direction_count', 'omitted_matching_directions'],
    }
    used = {}
    omission_fields = sorted({key for row in result['evidence'] if row['kind'].startswith('intraday:')
                              for key in row['data'].get('omitted_for_budget', {})})
    for evidence in result['evidence']:
        evidence.pop('source', None)  # Its observation class is explicit in kind.
        if 'trade_date' in evidence.get('dates', {}) and evidence['dates']['trade_date'] == result.get('date'):
            evidence['dates'].pop('trade_date')
            if not evidence['dates']:
                evidence.pop('dates')
        data = evidence['data']
        data.pop('units', None)
        if 'directions' in data and not any(data['directions'].values()):
            data.pop('directions')
            data.pop('direction_scope', None)
            if data.pop('directions_omitted_for_budget', False):
                summary['optional_rows_omitted'] = True
        if not evidence['kind'].startswith('intraday:'):
            continue
        changes = data.get('board_changes', {})
        groups = changes.pop('groups', {})
        if groups:
            changes['rows'] = [[key, row.get('state'), row.get('count'),
                row.get('classification', {}).get('state'), row.get('classification', {}).get('total'),
                row.get('classification', {}).get('classified'), row.get('classification', {}).get('missing')]
                for key, row in groups.items()]
            used['board_changes.rows'] = columns['board_changes.rows']
        ladder = data.get('ladder', {})
        if ladder:
            broken = ladder.get('broken_board', {})
            data['ladder'] = {'values': [ladder.get(key) for key in columns['ladder.values']],
                              'broken_values': [broken.get(key) for key in columns['ladder.broken_values']]}
            used.update({key: columns[key] for key in ('ladder.values', 'ladder.broken_values')})
        for field in ('interval_amount', 'previous_limit_up_feedback', 'same_day_limit_up_feedback'):
            value = data.get(field)
            if value:
                schema = field + '.values'
                data[field] = {'values': [value.get(key) for key in columns[schema]]}
                used[schema] = columns[schema]
        for field in ('breadth', 'direction_discovery'):
            value = data.get(field)
            if value:
                schema = field + '.values'
                data[field] = {'values': [value.get(key) for key in columns[schema]]}
                used[schema] = columns[schema]
        if data.get('omitted_for_budget') and omission_fields:
            data['omitted_for_budget'] = [data['omitted_for_budget'].get(key, 0) for key in omission_fields]
            used['omitted_for_budget'] = omission_fields
        for field in ('sector_samples', 'stock_samples', 'indices'):
            if data.get(field) == []:
                data.pop(field)
        data.pop('sample_note', None)
        data.pop('snapshot_quality', None)  # Each phase's partial/available state remains explicit.
        data.pop('historical_metrics_state', None)
    summary['phase_fact_columns'] = used
    summary['compact_phase_note'] = 'values/rows按phase_fact_columns逐列对应，null仍未知；trade_date继承date，来源类由kind和source_bindings对应。所有时段和计数保留，样本省略另有标记。'
    summary['historical_metrics_state'] = 'not_in_snapshot'
    summary['fact_units'] = {'amount': 'CNY', 'delta': 'CNY', 'change_pct': '%', 'mean_pct': '%',
                             'median_pct': '%', 'change_pp': '百分点', 'counts': '家', 'price_basis': 'none'}
