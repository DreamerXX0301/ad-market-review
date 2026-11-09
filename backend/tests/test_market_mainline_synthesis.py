"""合成来源四分区回归；不访问供应商、任务或业务数据库。"""
from copy import deepcopy
import random

import pytest

from app.services import market_ladder
from app.services.market_mainline_synthesis import (
    ALGORITHM_VERSION, MAX_REASON_LENGTH, MAX_TAG_LENGTH, MAX_TAGS_PER_REASON,
    build_system_mainlines,
)

DAY = '2026-01-05'
SNAPSHOT = 'synthetic-generation'
COMPLETE = {'source_pool_complete': True, 'complete': True}
SHA = 'a' * 64


def row(code, reason='合成方向', height=1, first='09:31:00'):
    symbol = f'{code:06d}.SH'
    return {'instrument_id': symbol, 'name': '合成名称不得用于归因', 'date': DAY,
            'snapshot_id': SNAPSHOT, 'close': 10, 'volume': 100, 'price_basis': 'none',
            'reason': reason, 'details_source': '合成原因来源', 'first_limit_time': first,
            'board_count': height, 'topics': [{'name': '不得借用的热门目录题材'}],
            'limit_up_state': True,
            'limit_up_evidence': {'state': True, 'status': 'confirmed', 'evidence': [
                {'state': True, 'status': 'confirmed', 'source': '合成闭市池',
                 'path': f'annual_pools.{DAY}', 'raw_sha256': SHA, 'observed_prices': [10],
                 'pattern_validation': {'instrument_id': symbol, 'trade_date': DAY,
                                        'source_identity_valid': True, 'source_dates_valid': True}}]},
            'board_presentation': {'version': 'dated-stock-board-presentation-v1', 'date': DAY,
                                   'snapshot_id': SNAPSHOT, 'instrument_id': symbol,
                                   'height': {'value': height, 'label': f'{height}连板', 'state': 'confirmed', 'source_date': DAY}}}


def build(rows, **extra):
    return build_system_mainlines(rows, day=DAY, snapshot_id=SNAPSHOT, coverage=COMPLETE, **extra)


def all_groups(result):
    return result['groups'] + result['other_groups']


def assert_partition(result, expected):
    members = [member for group in all_groups(result) for member in group['members']]
    members += result['individual_cues'] + result['missing_reason']
    ids = [member['instrument_id'] for member in members]
    assert set(ids) == set(expected) and len(ids) == len(set(ids))
    coverage = result['coverage']
    assert coverage['focus_count'] == sum(group['count'] for group in result['groups'])
    assert coverage['other_count'] == sum(group['count'] for group in result['other_groups'])
    assert coverage['individual_count'] == len(result['individual_cues'])
    assert coverage['missing_reason_count'] == len(result['missing_reason'])
    assert sum(coverage[key] for key in ('focus_count','other_count','individual_count','missing_reason_count')) == coverage['confirmed_sample_count'] == len(ids)
    assert coverage['eligible_reason_count'] == len(ids) - len(result['missing_reason'])
    assert 'unassigned' not in result and 'unassigned_count' not in coverage


def test_explicit_tags_produce_candidates_with_individual_verbatim_evidence():
    rows = [row(1, '  合成方向 + 次级标签 + 合成方向  ', 5), row(2, '合成方向', 2),row(3)]
    before = deepcopy(rows)
    result = build(rows)
    assert result['algorithm_version'] == ALGORITHM_VERSION
    assert result['source_kind'] == 'local_source_synthesis' and result['state'] == 'candidate'
    assert result['date'] == DAY and result['snapshot_id'] == SNAPSHOT
    group, = result['groups']
    assert group['name'] == '合成方向' and group['count'] == 3
    assert group['key']=='exact:合成方向' and group['kind']=='direction'
    assert group['grouping_rule']=='exact-first-tag-v2' and group['labels']==['合成方向']
    assert group['max_known_height'] == 5 and group['high_stock_ids'] == ['000001.SH']
    assert group['state'] == 'candidate'
    member = group['members'][0]
    assert member['source_reason'] == rows[0]['reason'] and member['matched_tag'] == '合成方向'
    assert member['matched_tags']==['合成方向']
    assert member['source'] is None  # row details metadata does not prove reason-field provenance
    assert member['source_ref'] == {'snapshot_id': SNAPSHOT, 'date': DAY,
                                    'endpoint': '/api/v1/market/ladder',
                                    'field_path': 'items[instrument_id="000001.SH"].reason'}
    assert member['closing_pool_refs'] == [{'source': '合成闭市池', 'path': f'annual_pools.{DAY}',
                                           'raw_sha256': SHA, 'purpose': 'closing_event_only'}]
    assert '不独立证明原因' in result['notice'] and '不认定龙头' in result['notice']
    assert result['coverage']['market_complete'] is False
    assert_partition(result,[item['instrument_id'] for item in rows])
    assert rows == before


def test_related_directions_read_later_tags_without_counting_a_stock_twice_in_the_partition():
    rows = [
        row(1, '冰雪旅游保障+高速公路', 2),
        row(2, '冰雪工程+液冷'),
        row(3, '冰雪经济+商业航天'),
        row(4, '航材国产化+AI航检眼镜'),
        row(5, '商业航天+“羊”字辈'),
        row(6, '“羊”字辈+百草枯', 2),
        row(7, '机器人关节模组+商业航天+"羊"字辈'),
        row(8, '煤炭+制氢'),
        row(9, '煤炭涨价+煤炭主业'),
        row(10, 'AI漫剧+游戏运营'),
        row(11, '短剧+股权转让'),
        row(12, '储能+合成二级题材'),
        row(13, '液冷+合成二级题材'),
        row(14, '冰雪产业+公司澄清不涉及冰雪业务'),
        row(15, '焦炭+国企控股'),
        row(16, '电力+合成二级题材+否认合成二级题材'),
        row(17, '冰雪经济+无冰雪相关业务'),
        row(18, '商业航天+无商业航天订单'),
    ]
    result = build(rows)
    assert_partition(result, [item['instrument_id'] for item in rows])
    primary = {group['name']: group for group in all_groups(result)}
    related = {group['name']: group for group in result['related_groups']}

    def matches(group):
        return {member['instrument_id']: member['matched_tags'] for member in group['members']}

    assert primary['冰雪经济']['max_known_height'] == 2
    assert matches(primary['冰雪经济']) == {
        '000001.SH': ['冰雪旅游保障'], '000002.SH': ['冰雪工程'], '000003.SH': ['冰雪经济'],
    }
    assert matches(primary['煤炭']) == {
        '000008.SH': ['煤炭'], '000009.SH': ['煤炭涨价', '煤炭主业'],
    }
    assert primary['短剧与漫剧']['count'] == 2
    assert matches(related['航空航天']) == {
        '000003.SH': ['商业航天'], '000004.SH': ['航材国产化'], '000005.SH': ['商业航天'],
        '000007.SH': ['商业航天'],
    }
    assert set(matches(related['商业航天'])) == {'000003.SH', '000005.SH', '000007.SH'}
    assert set(matches(related['AI应用'])) == {'000004.SH', '000010.SH'}
    assert matches(related['“羊”字辈'])['000007.SH'] == ['"羊"字辈']
    assert set(matches(related['合成二级题材'])) == {'000012.SH', '000013.SH'}


@pytest.mark.parametrize('changes', [
    {'date': '2026-01-02'}, {'snapshot_id': 'other-generation'},
    {'evidence_date': '2026-01-02'}, {'trade_date': '2026-01-02'}, {'limit_up_state': None}, {'limit_up_state': 1},
    {'volume': 0}, {'volume': -1}, {'volume': True}, {'volume': float('nan')},
    {'close': None}, {'close': 0}, {'close': float('inf')}, {'price_basis': 'forward'},
    {'kind': 'index'}, {'instrument_id': 'unresolved'},
])
def test_wrong_scope_unknown_or_nontraded_inputs_never_form_direction(changes):
    invalid = row(1)
    invalid.update(changes)
    result = build([invalid, row(2)])
    assert not result['groups']
    assert result['coverage']['confirmed_sample_count'] == 1
    assert result['coverage']['rejected_row_count'] == 1


@pytest.mark.parametrize('changes', [
    {'state': None}, {'status': 'unknown'}, {'status': 'conflict'}, {'evidence': []},
])
def test_closing_status_alone_does_not_bypass_retained_evidence(changes):
    invalid = row(1)
    invalid['limit_up_evidence'].update(changes)
    assert not build([invalid, row(2)])['groups']


@pytest.mark.parametrize('target,changes', [
    ('proof', {'state': False}), ('proof', {'status': 'conflict'}), ('proof', {'status': 'unknown'}),
    ('proof', {'observed_prices': [11]}), ('proof', {'observed_prices': []}),
    ('binding', {'trade_date': '2026-01-02'}), ('binding', {'instrument_id': '000002.SH'}),
    ('binding', {'source_dates_valid': False}), ('binding', {'source_identity_valid': False}),
])
def test_pool_identity_date_price_and_conflicts_are_not_relabelled_as_reasons(target, changes):
    invalid = row(1)
    proof = invalid['limit_up_evidence']['evidence'][0]
    (proof if target == 'proof' else proof['pattern_validation']).update(changes)
    assert not build([invalid, row(2)])['groups']


@pytest.mark.parametrize('reason', [None, '', ' —–- ', '未知+待核验+其他'])
def test_only_empty_or_complete_placeholders_are_missing_reason(reason):
    result = build([row(1, reason), row(2, reason)])
    assert result['groups'] == []
    assert result['coverage']['eligible_reason_count'] == 0
    assert result['coverage']['confirmed_sample_count'] == 2
    assert len(result['missing_reason']) == 2 and not result['individual_cues']
    assert all(item['source_reason']==reason and item['matched_tags']==[] for item in result['missing_reason'])
    assert_partition(result,['000001.SH','000002.SH'])


@pytest.mark.parametrize('reason', ['A'*(MAX_TAG_LENGTH+1),'A'*(MAX_REASON_LENGTH+1),
                                  '+'.join(str(i) for i in range(MAX_TAGS_PER_REASON+1)),
                                  '公司表示相关业务情况仍需要进一步说明','机器人、汽车'])
def test_long_or_narrative_reason_stays_provided_individual_information(reason):
    result=build([row(1,reason),row(2,reason),row(3,reason)])
    assert not all_groups(result) and not result['missing_reason']
    assert len(result['individual_cues'])==3
    assert all(item['source_reason']==reason for item in result['individual_cues'])
    assert result['coverage']['eligible_reason_count']==3


def test_robot_family_uses_ordered_actual_positive_labels_and_deduplicates_members():
    rows=[row(1,'业绩增长 + 具身智能 + 人形机器人 + 具身智能',6),
          row(2,'机器人腱绳+特种钢索',2),row(3,'机器人关节模组+无框电机',2),
          row(4,'医疗物流机器人+AI智能体'),row(5,'具身智能数据集+AI影视')]
    result=build(rows)
    group,=result['groups']
    assert group['key']=='family:robot' and group['name']=='机器人相关' and group['count']==5
    assert group['members'][0]['matched_tags']==['具身智能','人形机器人']
    assert group['members'][0]['matched_tag']=='具身智能'
    assert group['labels']==['具身智能','人形机器人','机器人腱绳','机器人关节模组','医疗物流机器人','具身智能数据集']
    assert '机器人相关' not in group['labels'] and '业绩增长' not in group['labels']
    assert not result['other_groups']
    assert_partition(result,[item['instrument_id'] for item in rows])


@pytest.mark.parametrize('reason',['不涉及机器人','机器人业务尚未涉及','非机器人业务','未布局具身智能',
                                  '尚未涉足机器人','暂无机器人业务','机器人业务未开展','否认进军机器人',
                                  '机器人传闻澄清','是否涉及机器人','网传人形机器人','拟布局机器人但尚无产品',
                                  '无机器人业务','无具身智能业务','不做机器人','未参与机器人',
                                  '机器人业务为零','机器人业务已终止','未提供机器人业务',
                                  '机器人+公司否认涉足机器人'])
def test_negative_uncertain_or_conflicting_robot_narrative_never_forms_positive_direction(reason):
    result=build([row(1,reason),row(2,reason),row(3,reason)])
    assert not all_groups(result) and not result['missing_reason']
    assert len(result['individual_cues'])==3


@pytest.mark.parametrize('reason',['物理AI','人工智能','机器视觉','电子皮肤','灵巧手'])
def test_unrelated_ai_or_component_tag_cannot_be_broadened_into_robot_family(reason):
    result=build([row(1,reason),row(2,reason),row(3,reason)])
    assert result['groups'][0]['key']=='exact:'+reason
    assert result['groups'][0]['name']==reason
    assert result['groups'][0]['labels']==[reason]


def test_positive_robot_labels_are_not_rejected_by_single_negative_characters():
    group,=build([row(1,'机器人无框电机'),row(2,'无人机器人'),row(3,'人形机器人')])['groups']
    assert group['count']==3 and group['key']=='family:robot'


def test_retail_is_a_limited_first_tag_family_and_earnings_never_becomes_focus():
    rows=[row(1,'商贸零售+商业地产',3),row(2,'零售促销+股份回购'),row(3,'商业零售+净利润增长'),
          row(4,'大消费'),row(5,'职业装'),row(6,'抗敏牙膏'),row(7,'日用陶瓷'),
          row(8,'番茄制品+商贸零售'),row(9,'业绩增长+黄羽肉鸡'),
          row(10,'三季报增长+抗敏牙膏'),row(11,'半年报增长+软件')]
    result=build(rows)
    group,=result['groups']
    assert group['name']=='零售' and group['count']==3
    assert group['labels']==['商贸零售','零售促销','商业零售']
    earnings,=result['other_groups']
    assert earnings['name']=='业绩线索' and earnings['kind']=='catalyst' and earnings['count']==3
    assert {item['instrument_id'] for item in result['individual_cues']}=={f'{code:06d}.SH' for code in range(4,9)}
    assert_partition(result,[item['instrument_id'] for item in rows])


def test_secondary_shared_military_tag_cannot_take_primary_lighting_cue():
    rows=[row(1,'专业照明+AI照明+军工+“羊”字辈',6),row(2,'新能源客车+汽车车桥+军工')]
    result=build(rows)
    assert not all_groups(result)
    assert result['individual_cues'][0]['source_reason']==rows[0]['reason']
    assert result['individual_cues'][0]['matched_tag']=='专业照明'
    assert result['individual_cues'][0]['matched_tags']==['专业照明']


def test_complete_partition_is_disjoint_stable_and_keeps_missing_separate_from_narrative():
    rows = [row(1,'甲+乙',5),row(2,'甲',2,'09:30:00'),row(3,'甲'),row(4,'乙'),
            row(5,'丙',2),row(6,'丙'),row(7,'业绩增长'),row(8,'三季报增长'),
            row(9,None),row(10,'未知+待核验'),row(11,'公司表示仍在核验情况')]
    rows.append(deepcopy(rows[0]))
    expected = build(rows)
    assert [group['name'] for group in expected['groups']] == ['甲']
    assert [group['name'] for group in expected['other_groups']] == ['丙','业绩线索']
    assert {item['instrument_id'] for item in expected['individual_cues']}=={'000004.SH','000011.SH'}
    assert expected['coverage']['rejected_row_count']==1
    assert_partition(expected,[f'{code:06d}.SH' for code in range(1,12)])
    randomizer = random.Random(10)
    for _ in range(20):
        shuffled = deepcopy(rows)
        randomizer.shuffle(shuffled)
        assert build(shuffled) == expected


def test_count_height_time_and_exact_name_have_stable_priority_with_three_group_cap():
    rows=[]
    for name,count,height,first in [('家数优先',4,1,'14:00'),('高度优先',3,6,'14:00'),
                                    ('早封优先',3,2,'09:30'),('迟封剩余',3,2,'10:00')]:
        start=len(rows)
        rows.extend(row(start+index+1,name,height,first) for index in range(count))
    result = build(rows)
    assert [group['name'] for group in result['groups']] == ['家数优先', '高度优先', '早封优先']
    assert result['other_groups'][0]['name']=='迟封剩余' and result['other_groups'][0]['count']==3
    assert not result['individual_cues']
    assert [group['name'] for group in build([row(1,'乙'),row(2,'乙'),row(3,'甲'),row(4,'甲')])['other_groups']]==sorted(['甲','乙'])


@pytest.mark.parametrize('height', [None, 0, -1, 1.5, True, '6', float('inf')])
def test_unknown_heights_are_neither_zero_nor_core_candidates(height):
    result = build([row(1, height=height), row(2, height=height)])
    group, = result['other_groups']
    assert group['max_known_height'] is None and group['high_stock_ids'] == []
    assert 'core' not in group and 'leader' not in group


def test_stale_height_generation_stays_unknown_despite_raw_high_count():
    rows = [row(1, height=9), row(2, height=2)]
    rows[0]['board_presentation']['snapshot_id'] = 'other'
    group, = build(rows)['other_groups']
    assert group['max_known_height'] == 2 and group['high_stock_ids'] == ['000002.SH']


@pytest.mark.parametrize('value', [None, {}, {'value': 9, 'state': 'confirmed', 'source_date': DAY}])
def test_incomplete_height_facts_do_not_promote_raw_counts(value):
    rows = [row(1, height=9), row(2, height=2)]
    rows[0]['board_presentation']['height'] = value
    group, = build(rows)['other_groups']
    assert group['max_known_height'] == 2 and group['high_stock_ids'] == ['000002.SH']


def test_repeated_stock_rows_are_unique_and_conflicting_duplicates_do_not_pick_input_order():
    a, b = row(1), row(2)
    assert build([a, deepcopy(a), b])['other_groups'][0]['count'] == 2
    conflict = deepcopy(a)
    conflict['reason'] = '其他明确方向'
    left = build([a, b, conflict])
    right = build([conflict, b, a])
    assert left == right and not left['groups']
    assert left['coverage']['confirmed_sample_count'] == 1


def test_partial_sample_does_not_become_all_market_even_when_two_matches_exist():
    result = build_system_mainlines([row(1), row(2)], day=DAY, snapshot_id=SNAPSHOT,
                                    coverage={'source_pool_complete': False, 'complete': False})
    assert len(result['other_groups']) == 1 and not result['groups']
    assert result['coverage']['market_complete'] is False
    assert result['coverage']['source_pool_complete'] is False and '不代表全市场' in result['reason']


def test_raw_sha_refs_are_optional_and_never_invented_for_the_reason():
    rows = [row(1), row(2)]
    rows[0]['limit_up_evidence']['evidence'][0].pop('raw_sha256')
    rows[1]['limit_up_evidence']['evidence'][0]['raw_sha256'] = 'not-a-sha'
    for member in build(rows)['other_groups'][0]['members']:
        assert 'raw_sha256' not in member['closing_pool_refs'][0]
        assert member['closing_pool_refs'][0]['purpose'] == 'closing_event_only'
        assert 'sha256' not in member['source_ref']


def test_authoritative_batched_quotes_can_fill_missing_panel_fields_without_fallback():
    rows = [row(1), row(2)]
    quotes = {item['instrument_id']: deepcopy(item) for item in rows}
    for item in rows:
        item.pop('close')
        item.pop('volume')
    assert len(build(rows, quotes=quotes)['other_groups']) == 1
    quotes[rows[0]['instrument_id']]['volume'] = 0
    assert not all_groups(build(rows, quotes=quotes))
    assert not all_groups(build(rows, quotes={}))


@pytest.mark.parametrize('day,snapshot', [('2026-02-30', SNAPSHOT), (DAY, None), (DAY, '')])
def test_unbound_request_is_explicitly_unavailable(day, snapshot):
    result = build_system_mainlines([row(1), row(2)], day=day, snapshot_id=snapshot)
    assert result['state'] == 'unavailable' and not result['groups'] and result['reason']


class LocalMarket:
    def __init__(self):
        self.days = ['2025-12-31', '2026-01-02', DAY]
        self.current_snapshot = SNAPSHOT
        self.history_calls = 0
        self.events = [{'thscode': f'{code:06d}.SH', 'ticker': f'{code:06d}', 'date': DAY,
                        'last_price': 10, 'continue_day_cnt': height,
                        'first_limit_time': '09:31:00', 'limit_up_reason': '合成原因+独立标签',
                        'details_source': '合成原因来源'} for code, height in [(1, 5), (2, 2)]]
        record = {'date': DAY, 'pool_kind': 'limitup', 'items': self.events, 'complete': True,
                  'pagination_complete': True, 'ambiguous_code_rows': 0, 'raw_codes': ['000001', '000002'],
                  'rejected_codes': [], 'source': '合成闭市池', 'raw_sha256': SHA}
        self.reference = {'_supplement': {'annual_pools': {DAY: record}}}

    def status(self): return {'snapshot_id': self.current_snapshot, 'data_end': DAY}
    def calendar(self): return self.days
    def instruments(self, limit=10000):
        return [{'instrument_id': item['thscode'], 'kind': 'stock', 'list_date': '2020-01-01'} for item in self.events]
    def history_for_instruments(self, symbols, start, end):
        self.history_calls += 1
        return {symbol: [{'instrument_id': symbol, 'date': day, 'close': 10, 'volume': 100,
                           'open': 9, 'high': 10, 'low': 9} for day in self.days if start <= day <= end]
                for symbol in symbols}
    def panel_rows(self):
        return [{'instrument_id': item['thscode'], 'date': DAY, 'snapshot_id': SNAPSHOT,
                 'close': 10, 'volume': 100, 'open': 9, 'high': 10, 'low': 9} for item in self.events]


def test_ladder_wires_synthesis_from_existing_quote_batch_and_published_reason_only(monkeypatch):
    market = LocalMarket()
    monkeypatch.setattr(market_ladder, 'resolve_reference', lambda *args: ({'topics': []}, {'selected_date_available': False}))
    result = market_ladder.build_ladder(market, market.status(), DAY, market.reference, market.panel_rows(), {}, include_patterns=False)
    system = result['system_mainlines']
    assert system['date'] == DAY and system['snapshot_id'] == SNAPSHOT
    assert not system['groups']
    assert system['other_groups'][0]['count'] == 2 and system['other_groups'][0]['name'] == '合成原因'
    assert system['other_groups'][0]['max_known_height'] == 5
    assert system['other_groups'][0]['members'][0]['source_reason'] == result['items'][0]['reason']
    assert_partition(system,[item['instrument_id'] for item in result['items']])
    assert not result['theme_counts'] and market.history_calls == 1
    market.current_snapshot = 'changed-generation'
    changed = market_ladder.build_ladder(market, {'snapshot_id': SNAPSHOT}, DAY, market.reference,
                                        market.panel_rows(), {}, include_patterns=False)
    assert changed['system_mainlines']['state'] == 'unavailable'
    assert not changed['system_mainlines']['groups']
