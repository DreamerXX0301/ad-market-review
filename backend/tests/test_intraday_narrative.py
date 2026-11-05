from copy import deepcopy
from datetime import datetime

import pytest

from app.services import intraday_narrative as narrative
from app.services.intraday_brief import sector_comparison_metadata

DAY = '2026-11-05'


def sector(code, name, change, *, count=0, state='observed', **more):
    row = {'instrument_id': code, 'name': name, 'kind': 'industry', 'latest_price': 100 + change,
           'prev_close': 100, 'change_pct': change, 'currency': 'CNY', 'price_basis': 'none',
           'limit_up_count': count, 'limit_up_count_state': state, 'member_count': 3,
           'member_date': '2026-11-04', 'member_list_complete': True, 'member_reference_sha256': 'a' * 64}
    return {**row, **sector_comparison_metadata(row, source='fuyao'), **more}


def frame(slot, sectors=(), *, sealed=3, broken=2, broken_state='partial', ready=None):
    clock = {'1000': '10:00', '1030': '10:30', '1100': '11:00', 'noon': '11:35', '1330': '13:30', '1400': '14:00', '1430': '14:30'}[slot]
    at = DAY + 'T' + clock + ':00+08:00'
    return {'slot': slot, 'id': 'original-' + slot, 'date': DAY, 'quality': 'partial',
            'collected_start_at': at, 'collected_end_at': at,
            '_ready': ready if ready is not None else int(datetime.fromisoformat(at).timestamp() * 1000) + 1,
            'source_binding': {'source_id': 'fuyao', 'snapshot_id': 'original-' + slot, 'content_sha256': 'a' * 64},
            'market': {'breadth': {'up': 10, 'down': 2}, 'turnover': {'amount': 1_000_000_000}},
            'ladder': {'state': 'observed', 'sealed_count': sealed,
                       'broken_board': {'state': broken_state, 'count': broken, 'items': []}},
            'rotation_sectors': list(sectors)}


class Members:
    def stocks(self, *_args, **_kwargs):
        return [{'instrument_id': '000001.SZ', 'name': '真实名称样本', 'change_pct': 8,
                 'limit_up_state': 'sealed', 'board_count': None}]


def brief(slot, frames):
    return {'slot': slot, 'body': '已留存原始正文', 'evidence': [
        {'id': key + '.breadth', 'slot': key, 'source_id': value['id'],
         'collected_end_at': value['collected_end_at']} for key, value in frames.items()]}


def test_section_explains_improving_and_fading_directions_and_zero_counts_without_inventing_height():
    before = frame('1400', [sector('884001.TI', '行业甲', 2), sector('884002.TI', '行业乙', 2)])
    current = frame('1430', [sector('884001.TI', '行业甲', 4, count=1), sector('884002.TI', '行业乙', 1.8)], sealed=5)
    frozen = deepcopy((before, current))
    section = narrative.build_section(current, before, members=Members())
    by_kind = {kind: [row for row in section['highlights'] if row['kind'] == kind]
               for kind in ('strengthening', 'weakening', 'sealed', 'broken')}
    assert by_kind['strengthening'][0]['change_pp'] == 2
    assert by_kind['strengthening'][0]['limit_up_change'] == 1
    weak = by_kind['weakening'][0]
    assert '涨幅小幅收窄0.20个百分点' in weak['text'] and weak['limit_up_count'] == 0
    assert '封板0只' in weak['text'] and '首板' not in weak['text'] and '（封板）' in weak['text']
    assert by_kind['sealed'][0]['change_count'] == 2
    assert by_kind['broken'][0]['change_count'] is None
    assert '不计算家数增减' in by_kind['broken'][0]['text']
    assert (before, current) == frozen and len(section['highlights']) <= 8


@pytest.mark.parametrize('damage', ['previous_price', 'source', 'kind', 'currency', 'gap'])
def test_changed_basis_or_missing_adjacent_frame_is_not_called_rotation(damage):
    before = frame('1400', [sector('884001.TI', '行业甲', 2)])
    current = frame('1430', [sector('884001.TI', '行业甲', 4)])
    row = before['rotation_sectors'][0]
    if damage == 'previous_price':
        row.update(prev_close=50, latest_price=51)
    elif damage == 'source':
        row['quote_source'] = 'another'
    elif damage == 'kind':
        row['kind'] = 'concept'
    elif damage == 'currency':
        row['currency'] = 'USD'
    else:
        before['slot'] = '1330'
    section = narrative.build_section(current, before, members=Members())
    assert not any(row['kind'] in ('strengthening', 'weakening') for row in section['highlights'])
    assert next(row for row in section['highlights'] if row['kind'] == 'leading')['change_pp'] is None


def test_changed_membership_never_creates_sealed_count_difference():
    before = frame('1400', [sector('884001.TI', '行业甲', 2, count=2)])
    current = frame('1430', [sector('884001.TI', '行业甲', 4, count=4, member_reference_sha256='b' * 64)])
    section = narrative.build_section(current, before, members=Members())
    assert all(row.get('limit_up_change') is None for row in section['highlights'])
    assert all('同成员较前档' not in row['text'] for row in section['highlights'])


def test_cumulative_sections_are_computed_once_and_respect_source_ready_cutoff(monkeypatch):
    late = int(datetime.fromisoformat(DAY + 'T10:31:00+08:00').timestamp() * 1000)
    frames = {'1000': frame('1000', ready=late), '1030': frame('1030'), '1100': frame('1100')}
    monkeypatch.setattr(narrative, 'load_frames', lambda *_: frames)
    original = narrative.build_section
    calls = []
    def build(frame, previous, **kwargs):
        calls.append(frame['slot'])
        return original(frame, previous, **kwargs)
    monkeypatch.setattr(narrative, 'build_section', build)
    briefs = [brief(slot, frames) for slot in ('1000', '1030', '1100')]
    narrative.attach_sections(DAY, briefs, [])
    assert calls == ['1000', '1030', '1100']
    assert [row['slot'] for row in briefs[1]['sections']] == ['1030']
    assert '缺少相邻档' in briefs[1]['sections'][0]['comparison_note']
    assert [row['slot'] for row in briefs[2]['sections']] == ['1000', '1030', '1100']
    assert all(row['body'] == '已留存原始正文' for row in briefs)


@pytest.mark.parametrize('changed', ['snapshot_id', 'source_time', 'not_originally_observed'])
def test_same_slot_replacements_or_absent_original_facts_do_not_rewrite_old_brief(changed, monkeypatch):
    frames = {'1000': frame('1000', [sector('884001.TI', '行业甲', 1)]),
              '1030': frame('1030', [sector('884001.TI', '行业甲', 3)])}
    original = brief('1030', frames)
    archived = deepcopy(original)
    if changed == 'snapshot_id':
        frames['1000']['id'] = 'different-snapshot-same-slot'
    elif changed == 'source_time':
        frames['1000']['collected_end_at'] = DAY + 'T10:02:00+08:00'
    else:
        original['evidence'] = original['evidence'][1:]
    monkeypatch.setattr(narrative, 'load_frames', lambda *_: frames)
    narrative.attach_sections(DAY, [original], [])
    assert [row['slot'] for row in original['sections']] == ['1030']
    assert not any(row['kind'] == 'strengthening' for row in original['sections'][0]['highlights'])
    assert original['body'] == archived['body']
    frames['1030']['id'] = 'different-target'
    narrative.attach_sections(DAY, [original], [])
    assert original['sections'] == []


def test_current_sealed_sector_ranking_prefers_narrow_coverage_and_preserves_lower_bounds():
    current = frame('1430', [sector('884001.TI', '行业甲', 4, count=3),
        sector('884002.TI', '行业乙', 5, count=4, state='partial'),
        sector('886001.TI', '具体题材', 6, count=5, kind='concept', member_count=30),
        sector('886002.TI', '宽属性', 10, count=50, kind='concept', member_count=2000),
        sector('886003.TI', '缺失成员的行情', 10, count=None, state='unknown', kind='concept', member_count=None)])
    section = narrative.build_section(current, None, members=Members())
    sealed = next(row for row in section['highlights'] if row['kind'] == 'sealed')
    assert [row['name'] for row in sealed['current_sector_counts']] == ['行业乙', '行业甲', '具体题材']
    assert '行业乙 封板至少4只' in sealed['text'] and '全市场完整排名' in sealed['text']
    assert '宽属性' not in sealed['text'] and len(section['highlights']) <= 8


def test_noon_label_uses_recorded_schedule_and_comparisons_preserve_that_label():
    before = frame('noon', [sector('884001.TI', '行业甲', 2)])
    before['display_label'] = '11:35 午评'
    current = frame('1330', [sector('884001.TI', '行业甲', 4)])
    assert narrative.build_section(before, None, members=Members())['label'] == '11:35 午评'
    assert '较11:35走强' in narrative.build_section(current, before, members=Members())['highlights'][0]['text']
    before['display_label'] = '午评'
    assert '较午评走强' in narrative.build_section(current, before, members=Members())['highlights'][0]['text']


@pytest.mark.parametrize('damage', [None, 'price', 'conflict', 'receipt'])
def test_noon_representative_state_uses_original_member_proof_events_receipt_and_quote(damage):
    from app.services.noon_sector_views import freeze_memberships
    from test_noon_membership_context import payload, topic, CODE

    raw = payload()
    day = raw['date']
    for row in raw['stocks']:
        row.update(price_basis='none', currency='CNY')
    raw.update(collected_start_at=day + 'T11:35:00+08:00', collected_end_at=day + 'T11:35:30+08:00')
    raw['sources'] = [{'domain': key + '_page_1', 'state': 'received', 'source': 'fuyao',
        'endpoint': '/api/a-share/special-data/' + key.replace('_', '-') + '-pool', 'sha256': 'a' * 64,
        'source_timestamp': {'state': 'observed', 'time': day + 'T11:30:00+08:00'}}
        for key in ('limit_up', 'limit_break')]
    raw['sector_memberships'] = freeze_memberships(raw, [topic()])
    if damage == 'price':
        raw['events']['limit_up']['items'][0]['latest_price'] = 12
    elif damage == 'conflict':
        raw['events']['limit_break'] = deepcopy(raw['events']['limit_up'])
    elif damage == 'receipt':
        raw['sources'] = []
    current = {'slot': 'noon', 'id': 'noon-original', '_raw': raw}
    rows = narrative._Members().stocks(current, {'instrument_id': CODE})
    assert len(rows) == 2
    state = next(row['limit_up_state'] for row in rows if row['instrument_id'] == '600001.SH')
    assert state == ('sealed' if damage is None else 'unknown' if damage in ('price', 'conflict') else None)
    assert all(row['board_count'] is None for row in rows)


def test_group_theme_concentration_only_counts_listed_verified_memberships_and_remains_bounded():
    before, current = frame('1400'), frame('1430')
    items = [{'instrument_id': f'{n:06d}.SZ', 'name': '已核实' + str(n), 'change_pct': 10,
              'themes': [{'name': '行业甲', 'kind': 'industry', 'member_date': '2026-11-04'},
                         {'name': '题材甲', 'kind': 'concept', 'member_date': '2026-11-04'}]}
             for n in range(1, 4)]
    items.append({'instrument_id': '000004.SZ', 'name': '归属未知', 'change_pct': 10, 'themes': []})
    current['board_changes'] = {'groups': {'new_sealed': {'state': 'observed', 'count': 10, 'items': items}}}
    section = narrative.build_section(current, before, members=Members())
    sealed = next(row for row in section['highlights'] if row['kind'] == 'sealed')
    assert sealed['listed_stock_count'] == 4 and sealed['classified_stock_count'] == 3
    assert all(row['count'] == 3 for row in sealed['topic_counts'])
    assert '已列4只' in sealed['text'] and '题材可重叠' in sealed['text']
    assert len(sealed['stocks']) <= 2 and len(section['highlights']) <= 8


def test_shared_projection_is_reused_only_for_exact_original_sha(monkeypatch):
    from contextlib import nullcontext
    from hashlib import sha256
    import json
    import sqlite3
    from app.services import intraday_brief as brief, intraday_service as live
    from test_intraday_board_changes import frame as captured

    value = {**captured('1430'), 'quality': 'partial'}
    original = json.dumps(value)
    digest = sha256(original.encode()).hexdigest()
    finished = int(datetime.fromisoformat(value['collected_end_at']).timestamp() * 1000) + 10
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    connection.executescript('''CREATE TABLE market_intraday_capture(trade_date,slot,job_id,snapshot_id,content_sha256,payload_json);
        CREATE TABLE job(job_id,status,finished_at_ms);''')
    connection.execute('INSERT INTO market_intraday_capture VALUES (?,?,?,?,?,?)',
                       (DAY, '1430', 'saved-job', value['id'], digest, original))
    connection.execute('INSERT INTO job VALUES (?,?,?)', ('saved-job', 'partial', finished))
    monkeypatch.setattr(brief, 'connect', lambda: nullcontext(connection))
    monkeypatch.setattr(brief, 'now_ms', lambda: finished + 100)
    calls = []
    def attach(values):
        calls.extend(item['id'] for item in values)
        for item in values:
            item['rotation_sectors'] = []
    monkeypatch.setattr(live, '_attach_saved_stock_references', attach)
    shown = {**value, 'rotation_sectors': [], 'board_changes': {
        'source_bindings': {'current': {'content_sha256': digest}}}}
    assert narrative.load_frames(DAY, [], [shown])['1430']['id'] == value['id']
    assert calls == []
    shown['board_changes']['source_bindings']['current']['content_sha256'] = 'wrong'
    narrative.load_frames(DAY, [], [shown])
    assert calls == [value['id']]
    assert connection.execute('SELECT payload_json FROM market_intraday_capture').fetchone()[0] == original
    connection.close()
