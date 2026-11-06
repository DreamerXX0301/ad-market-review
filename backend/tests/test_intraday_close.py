"""Closing detail is derived from the archived generation, never recaptured live."""
from copy import deepcopy
from hashlib import sha256
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.services import intraday_close as closing
from test_intraday_board_changes import frame as live_frame, raw_row

DAY, PRIOR, GENERATION = '2026-11-05', '2026-11-04', 'market-' + 'd' * 24
A, B, C = '000001.SZ', '000002.SZ', '000003.SZ'


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / 'published'
    directory = root / GENERATION
    directory.mkdir(parents=True)
    monkeypatch.setattr('app.core.published_dir', lambda: root)
    reference = {'calendar': [PRIOR, DAY], 'pool_trade_date': DAY,
        'pool_coverage': {key: {'complete': True, 'declared_total': count, 'quarantined_rows': 0, 'state': 'complete'}
                          for key, count in (('limitup', 2), ('breakpool', 1), ('limitdown', 0))},
        'limitup': [{'thscode': code, 'name': '原股票' + code[:6], 'last_price': 11,
                     'continue_day_cnt': 1 if code == A else 2} for code in (A, B)],
        'breakpool': [{'thscode': C, 'name': '原炸板股', 'last_price': 10.5, 'open_times': 2}], 'limitdown': [],
        'master': {}, 'topics': [{'topic_code': '881001.TI', 'name': '原行业', 'kind': 'industry',
             'source': 'fuyao', 'observed_date': PRIOR, 'members_complete': True, 'instrument_ids': [A, B, C]}]}
    price_rows = [{'instrument_id': code, 'date': day, 'open': 10., 'high': 11., 'low': 10.,
        'close': 10. if day == PRIOR else 10.5 if code == C else 11., 'volume': 1000., 'amount': 10000.,
        'source': 'fuyao', 'price_basis': 'none'} for day in (PRIOR, DAY) for code in (A, B, C)]
    price_temp = tmp_path / 'prices.parquet'
    pq.write_table(pa.Table.from_pylist(price_rows), price_temp)
    digest = sha256(price_temp.read_bytes()).hexdigest()
    price_path = root / f'market-objects/daily/{digest[:2]}/{digest}.parquet'
    price_path.parent.mkdir(parents=True)
    price_temp.rename(price_path)
    manifest = {'version': 'market-partitions-v1', 'role': 'daily.parquet', 'partitions': [
        {'month': DAY[:7], 'data_begin': PRIOR, 'data_end': DAY, 'sha256': digest,
         'bytes': price_path.stat().st_size, 'object_key': str(price_path.relative_to(root))}]}
    (directory / 'daily.manifest.json').write_text(json.dumps(manifest))
    actions = directory / 'corporate-actions.parquet'
    pq.write_table(pa.Table.from_pylist([], schema=pa.schema([('thscode', pa.string()), ('ex_date_ms', pa.int64())])), actions)
    def publish():
        (directory / 'reference.json').write_text(json.dumps(reference))
        files = [{'role': role, 'path': f'published/{GENERATION}/{name}',
                  'sha256': sha256((directory / name).read_bytes()).hexdigest(), 'bytes': (directory / name).stat().st_size}
                 for role, name in (('reference.json', 'reference.json'), ('daily.parquet', 'daily.manifest.json'),
                                    ('corporate-actions.parquet', 'corporate-actions.parquet'))]
        document = {'summary': {'snapshot_id': GENERATION, 'data_end': DAY, 'source': 'fuyao',
                               'price_basis': 'none', 'files': files}, 'published_at': DAY + 'T17:00:00+08:00'}
        (directory / 'manifest.json').write_text(json.dumps(document))
    publish()
    return root, directory, reference, publish


def anchor():
    return {'date': DAY, 'id': GENERATION, 'slot': 'close', 'collected_end_at': DAY + 'T15:00:00+08:00',
            'ladder': {'state': 'partial', 'sealed_count': 2, 'broken_board': {'state': 'complete', 'count': 1}}}


def test_original_close_pools_restore_all_details_zero_and_adjacent_changes_without_rewriting(source):
    root, directory, reference, _ = source
    original = (directory / 'reference.json').read_bytes()
    # A later head must never supply old closing events, names, or prices.
    (root / 'market-head.json').write_text(json.dumps({'snapshot_id': 'market-future'}))
    context = closing.closing_data(DAY, GENERATION)
    assert len(context['quotes']) == 3
    assert {key: pool['count'] for key, pool in context['pools'].items()} == {'limit_up': 2, 'limit_break': 1, 'limit_down': 0}
    assert all(pool['state'] == 'observed' for pool in context['pools'].values())
    value = anchor()
    earlier = live_frame('1430', [A, C], [B])
    closing.restore_close_details(value, context, previous=earlier, previous_row=raw_row(earlier))
    assert [(group['board_count'], group['count']) for group in value['ladder']['groups']] == [(2, 1), (1, 1)]
    assert value['ladder']['broken_board']['items'][0]['name'] == '原炸板股'
    assert value['ladder']['broken_board']['state'] == 'observed'
    assert value['sealed_stocks'][0]['themes'][0]['name'] == '原行业'
    groups = value['board_changes']['groups']
    assert groups['new_sealed']['count'] == groups['opened']['count'] == groups['resealed']['count'] == 1
    assert groups['new_limit_down']['count'] == 0
    assert groups['resealed']['items'][0]['board_transition'] == 'resealed'
    assert value['source_binding']['reference_sha256'] == sha256(original).hexdigest()
    assert (directory / 'reference.json').read_bytes() == original


def test_close_changes_use_original_members_for_stock_labels_brief_and_ai(source):
    from app.services.ai_intraday_evidence import attach_change_distributions, membership_inputs
    from app.services.ai_intraday_read import _change_summary
    from app.services.intraday_narrative import build_section

    _, _, reference, publish = source
    concept = {'name': '原题材', 'kind': 'concept', 'member_date': PRIOR}
    reference['topics'].extend([
        {'topic_code': code, 'name': name, 'kind': 'concept', 'source': 'fuyao',
         'observed_date': date, 'members_complete': True, 'instrument_ids': [B]}
        for code, name, date in [('885001.TI', concept['name'], PRIOR), ('885002.TI', '未来题材', '2026-11-06')]
    ])
    publish()
    context = closing.closing_data(DAY, GENERATION)
    earlier = live_frame('1430', [A])
    earlier['stocks'][1]['themes'] = [{'name': '前档专属题材', 'kind': 'concept', 'member_date': PRIOR}]
    frame = closing.restore_close_details(anchor(), context, previous=earlier, previous_row=raw_row(earlier))
    change = frame['board_changes']['groups']['new_sealed']['items'][0]
    expected = [{'name': '原行业', 'kind': 'industry', 'member_date': PRIOR}, concept]
    assert change['instrument_id'] == B and change['themes'] == expected

    prior = {**earlier, 'source_binding': frame['board_changes']['source_bindings']['previous']}
    brief = build_section(frame, prior)
    sealed = next(row for row in brief['highlights'] if row['kind'] == 'sealed')
    assert sealed['stock_ids'] == [B]
    assert '本段新入封板1只' in sealed['text'] and '原股票000002' in sealed['text']
    assert {item['name'] for item in sealed['topic_counts']} == {'原行业', '原题材'}

    sectors, members = membership_inputs(context['reference']['topics'], DAY)
    attach_change_distributions(frame, earlier, sectors, members, {})
    group = _change_summary(frame['board_changes'], '', True)['groups']['new_sealed']
    assert group['samples'][0]['themes'] == expected
    assert group['classification']['classified'] == 1
    assert [item['name'] for item in group['classification']['concepts']] == ['原题材']


@pytest.mark.parametrize('damage', ['reference_sha', 'prices_sha', 'wrong_day', 'before_close'])
def test_close_source_does_not_accept_changed_or_unbound_files(source, damage):
    _, directory, _, _ = source
    manifest_path = directory / 'manifest.json'
    document = json.loads(manifest_path.read_text())
    if damage == 'reference_sha':
        (directory / 'reference.json').write_text('{}')
    elif damage == 'prices_sha':
        document['summary']['files'][1]['sha256'] = 'f' * 64
    elif damage == 'wrong_day':
        document['summary']['data_end'] = PRIOR
    else:
        document['published_at'] = DAY + 'T14:59:59+08:00'
    manifest_path.write_text(json.dumps(document))
    with pytest.raises((ValueError, OSError)):
        closing.closing_data(DAY, GENERATION)


def test_partial_pool_missing_height_and_conflicts_stay_unknown_not_zero(source):
    _, _, reference, publish = source
    reference['limitup'][1]['continue_day_cnt'] = 0
    reference['pool_coverage']['limitup']['complete'] = False
    reference['breakpool'].append(deepcopy(reference['limitup'][0]))
    reference['pool_coverage']['breakpool']['declared_total'] = 2
    publish()
    context = closing.closing_data(DAY, GENERATION)
    assert context['conflicts'] == [A]
    value = closing.close_frame(context)
    assert value['ladder']['state'] == 'partial' and value['ladder']['sealed_count'] == 1
    assert value['ladder']['groups'][0]['board_count'] is None
    assert value['ladder']['highest_board'] is None
    assert value['ladder']['first_board_count'] is None
    assert value['ladder']['broken_board']['state'] == 'partial'
    before = live_frame('1430', [A])
    output = closing.restore_close_details(anchor(), context, previous=before, previous_row=raw_row(before))
    assert output['board_changes']['groups']['new_sealed']['state'] == 'partial'


def test_missing_action_evidence_preserves_closing_prices_but_does_not_guess_change(source):
    _, directory, _, _ = source
    (directory / 'corporate-actions.parquet').unlink()
    context = closing.closing_data(DAY, GENERATION)
    assert all(row['latest_price'] > 0 and row['change_pct'] is None and row['prev_close'] is None
               for row in context['quotes'].values())
    assert context['pools']['limit_up']['count'] == 2


def test_close_transition_needs_same_previous_price_and_original_prior_sha(source):
    context = closing.closing_data(DAY, GENERATION)
    before = live_frame('1430', [C], [B])
    record = raw_row(before)
    record['content_sha256'] = '0' * 64
    value = anchor()
    closing.restore_close_details(value, context, previous=before, previous_row=record)
    assert all(group['state'] == 'unknown' for group in value['board_changes']['groups'].values())
    before['comparison_input']['stocks'][1].update(prev_close=20, latest_price=22)
    closing.restore_close_details(value, context, previous=before, previous_row=raw_row(before))
    assert value['board_changes']['groups']['resealed']['count'] is None
