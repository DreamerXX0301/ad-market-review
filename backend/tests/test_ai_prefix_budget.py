"""Dense cumulative replies share schemas; source identities and numbers survive."""
from copy import deepcopy

from app.services.ai_prefix_budget import compact_prefix


def test_compaction_preserves_each_count_and_resolves_shared_member_and_previous_sources():
    proof = {'generation': 'market-original', 'date': '2026-11-04', 'reference_sha256': 'a' * 64}
    earlier = {'snapshot_id': 'first', 'content_sha256': 'b' * 64, 'member_reference': dict(proof)}
    later = {'snapshot_id': 'second', 'content_sha256': 'c' * 64, 'member_reference': dict(proof),
             'board_change_previous': {'slot': '1000', 'snapshot_id': 'first', 'content_sha256': 'b' * 64,
                                      'collected_start_at': '2026-11-05T10:00:00+08:00'}}
    data = {'snapshot_quality': 'partial', 'board_changes': {'state': 'observed', 'baseline_slot': '1000',
            'groups': {'new_sealed': {'state': 'observed', 'count': 40,
                       'classification': {'state': 'observed', 'total': 40, 'classified': 38, 'missing': 2}}}},
            'ladder': {'state': 'partial', 'sealed_count': 85, 'highest_board': 4, 'unknown_height_count': 2,
                       'broken_board': {'state': 'observed', 'count': 47}},
            'interval_amount': {'amount': 123456789.25, 'previous_amount': None, 'state': 'partial'},
            'previous_limit_up_feedback': {'state': 'partial', 'total': 90, 'average_change_pct': 1.23456789},
            'same_day_limit_up_feedback': {'state': 'observed', 'held_count': 36, 'entered_count': 40, 'absent_count': 4},
            'stock_samples': [], 'sector_samples': [], 'indices': []}
    result = {'summary': {'close_context': {'cutoff_at': '2026-11-05T15:00:00+08:00',
                    'phases': {'1000': {'phase_state': 'partial'}, '1030': {'phase_state': 'partial'}}},
                    'phases': {'close': {'phase_state': 'available'}}},
              'source_bindings': {'close_intraday': {'1000': earlier, '1030': later}}, 'metrics': [],
              'evidence': [{'kind': 'intraday:1030', 'data': deepcopy(data)}]}
    compact_prefix(result)
    item = result['evidence'][0]['data']
    schemas = result['summary']['phase_fact_columns']
    changes = dict(zip(schemas['board_changes.rows'], item['board_changes']['rows'][0]))
    assert changes == {'kind': 'new_sealed', 'state': 'observed', 'count': 40,
                       'classification_state': 'observed', 'total': 40, 'classified': 38, 'missing': 2}
    ladder = dict(zip(schemas['ladder.values'], item['ladder']['values']))
    assert ladder['sealed_count'] == 85 and ladder['highest_board'] == 4 and ladder['unknown_height_count'] == 2
    broken = dict(zip(schemas['ladder.broken_values'], item['ladder']['broken_values']))
    assert broken['count'] == 47
    amount = dict(zip(schemas['interval_amount.values'], item['interval_amount']['values']))
    assert amount['amount'] == 123456789.25 and amount['previous_amount'] is None
    prior = dict(zip(schemas['previous_limit_up_feedback.values'], item['previous_limit_up_feedback']['values']))
    assert prior['total'] == 90 and prior['average_change_pct'] == 1.23456789
    bindings = result['source_bindings']
    member_ref = bindings['close_intraday']['1030']['member_reference_ref']
    assert bindings['member_reference_sources'][member_ref] == proof
    assert bindings['close_intraday']['1030']['board_change_previous'] == {'source_slot_ref': '1000'}
    assert bindings['close_intraday']['1000']['collected_start_at'] == '2026-11-05T10:00:00+08:00'
    assert result['summary']['fact_units']['amount'] == 'CNY'
