"""Only checksummed original-generation members may explain noon limit counts."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from app.services import noon_membership_context as context

DAY = '2026-10-28'
PRIOR = '2026-10-27'
OBSERVED = '2026-10-21'
GENERATION = 'market-noon-members-fixture'
CODE = '884288.TI'


def payload():
    return {'date': DAY, 'previous_review': {'date': PRIOR, 'state': 'background_only',
                'payload': {'date': PRIOR, 'mode': 'previous_session_price_facts', 'source_snapshot_id': GENERATION}},
            'sectors': [{'instrument_id': CODE, 'topic_code': CODE, 'name': '合成板块', 'kind': 'industry',
                'members_observed_date': OBSERVED, 'member_coverage': {'total': 2, 'valid': 2, 'complete': True},
                'member_up': 1, 'member_down': 1}],
            'stocks': [{'instrument_id': '600001.SH', 'latest_price': 11, 'prev_close': 10, 'change_pct': 10},
                       {'instrument_id': '600002.SH', 'latest_price': 9, 'prev_close': 10, 'change_pct': -10}],
            'events': {'limit_up': {'state': 'observed', 'items': [
                {'instrument_id': '600001.SH', 'latest_price': 11}]}}}


def topic():
    return {'topic_code': CODE, 'name': '合成板块', 'kind': 'industry', 'source': 'fuyao',
            'observed_date': OBSERVED, 'members_complete': True,
            'instrument_ids': ['600001.SH', '600002.SH']}


@pytest.fixture
def published(tmp_path, monkeypatch):
    root = tmp_path / 'published'
    root.mkdir()
    monkeypatch.setattr(context, 'published_dir', lambda: root)
    return root


def publish(root, topics=None, *, split=False):
    folder = root / GENERATION
    folder.mkdir(exist_ok=True)
    metadata = {'topics': [topic()] if topics is None else topics, 'indices': {}}
    if split:
        from app.analytics.market_storage import write_reference
        write_reference(metadata, root, folder, GENERATION)
    else:
        (folder / 'reference.json').write_text(json.dumps(metadata))
    update_manifest(root)
    return folder


def update_manifest(root):
    folder = root / GENERATION
    raw = (folder / 'reference.json').read_bytes()
    manifest = {'summary': {'snapshot_id': GENERATION, 'data_end': PRIOR, 'source': 'fuyao',
        'files': [{'role': 'reference.json', 'path': f'published/{GENERATION}/reference.json',
                   'sha256': sha256(raw).hexdigest(), 'bytes': len(raw)}]}}
    (folder / 'manifest.json').write_text(json.dumps(manifest))


def count(value):
    return value['counts'][CODE]


def test_original_generation_reconciles_full_frozen_aggregates_and_preserves_payload(published):
    publish(published)
    (published / 'market-head.json').write_text(json.dumps({'snapshot_id': 'market-later-do-not-follow'}))
    value = payload()
    before = deepcopy(value)
    result = context.build_member_limit_context(value)
    assert value == before
    assert result['source_snapshot_id'] == GENERATION and result['state'] == 'observed'
    assert count(result)['member_limit_up_count'] == 1
    assert count(result)['members_observed_date'] == OBSERVED
    assert len(count(result)['source_sha256']) == 64


@pytest.mark.parametrize('field,value', [('topic_code', '885001.TI'), ('kind', 'concept'),
    ('source', 'other-provider'), ('observed_date', '2026-10-22')])
def test_same_name_cannot_replace_explicit_code_class_source_or_member_day(published, field, value):
    row = topic()
    row[field] = value
    publish(published, [row])
    result = count(context.build_member_limit_context(payload()))
    assert result['member_limit_up_count'] is None and result['state'] == 'unknown'
    classification = context.build_member_limit_context(payload())['stock_classifications']['600001.SH']
    assert classification['industries'] == [] and classification['topics'] == []
    assert classification['state'] == 'unknown'


@pytest.mark.parametrize('tamper', ['wrong_generation', 'future_data', 'reference_sha', 'reference_path', 'symlink'])
def test_manifest_identity_chronology_path_and_sha_are_required(published, tamper):
    folder = publish(published)
    manifest_path = folder / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if tamper == 'wrong_generation':
        manifest['summary']['snapshot_id'] = 'market-another-generation'
    elif tamper == 'future_data':
        manifest['summary']['data_end'] = DAY
    elif tamper == 'reference_sha':
        manifest['summary']['files'][0]['sha256'] = '0' * 64
    elif tamper == 'reference_path':
        manifest['summary']['files'][0]['path'] = 'published/market-other/reference.json'
    else:
        reference = folder / 'reference.json'
        outside = published.parent / 'outside.json'
        reference.rename(outside)
        reference.symlink_to(outside)
    manifest_path.write_text(json.dumps(manifest))
    result = count(context.build_member_limit_context(payload()))
    assert result['member_limit_up_count'] is None and '未通过' in result['reason']


def test_split_reference_reads_only_topics_and_rechecks_cas_bytes(published):
    folder = publish(published, split=True)
    path = folder / 'reference.json'
    document = json.loads(path.read_text())
    item = document['fields']['topics']
    document['fields']['unrelated'] = {'object_key': '../must-not-read.json'}
    document['master_columns'] = {'bad': 'must-not-expand-identity-directory'}
    path.write_text(json.dumps(document))
    update_manifest(published)
    result = count(context.build_member_limit_context(payload()))
    assert result['member_limit_up_count'] == 1 and result['source_sha256'] == item['sha256']
    target = published / item['object_key']
    target.write_text(target.read_text().replace('600001', '600009'))
    result = count(context.build_member_limit_context(payload()))
    assert result['member_limit_up_count'] is None and result['state'] == 'unknown'


@pytest.mark.parametrize('tamper', ['members', 'valid', 'up', 'down', 'duplicate_member', 'duplicate_topic', 'duplicate_quote'])
def test_original_list_must_reconcile_with_noon_snapshot_not_merely_share_a_date(published, tamper):
    value, row = payload(), topic()
    if tamper == 'members':
        value['sectors'][0]['member_coverage']['total'] = 3
    elif tamper == 'valid':
        value['sectors'][0]['member_coverage']['valid'] = 1
    elif tamper in ('up', 'down'):
        value['sectors'][0]['member_' + tamper] = 99
    elif tamper == 'duplicate_member':
        row['instrument_ids'].append('600001.SH')
    elif tamper == 'duplicate_quote':
        value['stocks'].append(deepcopy(value['stocks'][0]))
    publish(published, [row, deepcopy(row)] if tamper == 'duplicate_topic' else [row])
    result = count(context.build_member_limit_context(value))
    assert result['member_limit_up_count'] is None


def test_missing_pool_is_unknown_complete_empty_is_zero_and_partial_is_lower_bound(published):
    publish(published)
    value = payload()
    value['events']['limit_up'] = {'state': 'unknown', 'items': []}
    assert count(context.build_member_limit_context(value))['member_limit_up_count'] is None
    value['events']['limit_up'] = {'state': 'observed', 'items': []}
    assert count(context.build_member_limit_context(value))['member_limit_up_count'] == 0
    value['events']['limit_up'] = {'state': 'partial', 'items': []}
    assert count(context.build_member_limit_context(value))['member_limit_up_count'] is None
    value['events']['limit_up']['items'] = [{'instrument_id': '600001.SH', 'latest_price': 11}]
    result = count(context.build_member_limit_context(value))
    assert result['state'] == 'partial' and result['member_limit_up_count'] == 1


def test_unfinished_members_cannot_turn_unmatched_events_into_zero(published):
    row = topic()
    row['members_complete'] = False
    publish(published, [row])
    value = payload()
    value['sectors'][0]['member_coverage']['complete'] = False
    value['events']['limit_up']['items'] = []
    assert count(context.build_member_limit_context(value))['member_limit_up_count'] is None


def test_frozen_counts_never_open_an_original_or_current_directory(published, monkeypatch):
    value = payload()
    value['stock_classifications'] = {}
    value['sectors'][0].update(member_limit_up_count=0, member_limit_up_state='observed')
    monkeypatch.setattr(context, '_load_topics', lambda *_: pytest.fail('frozen counts must remain pure'))
    assert count(context.build_member_limit_context(value))['member_limit_up_count'] == 0
    value['sectors'][0].update(member_limit_up_count=None, member_limit_up_state='unknown')
    assert count(context.build_member_limit_context(value))['member_limit_up_count'] is None


def test_absent_or_conflicting_source_generation_never_falls_back_to_latest(published, monkeypatch):
    value = payload()
    publish(published)
    monkeypatch.setattr(context, '_load_topics', lambda *_: pytest.fail('no identified immutable source'))
    value['previous_review']['background'] = {**value['previous_review']['payload'], 'source_snapshot_id': 'market-other'}
    assert count(context.build_member_limit_context(value))['member_limit_up_count'] is None
    value['previous_review'].pop('background')
    value['previous_review']['payload'].pop('source_snapshot_id')
    assert count(context.build_member_limit_context(value))['member_limit_up_count'] is None


def test_membership_classification_is_independent_of_limit_pool_and_preserves_list_date(published):
    value = payload()
    concept = {**topic(), 'topic_code': '885001.TI', 'name': '合成概念', 'kind': 'concept'}
    value['sectors'].append({**deepcopy(value['sectors'][0]), 'instrument_id': '885001.TI',
                            'topic_code': '885001.TI', 'name': '合成概念', 'kind': 'concept'})
    value['previous_review']['cohort_feedback'] = {'items': [{'instrument_id': '600001.SH'}]}
    value['events']['limit_up'] = {'state': 'unknown', 'items': []}
    publish(published, [topic(), concept])
    result = context.build_member_limit_context(value)
    assert count(result)['member_limit_up_count'] is None
    classified = result['stock_classifications']['600001.SH']
    assert classified['state'] == 'observed_sample'
    assert classified['industries'] == [{'topic_code': CODE, 'name': '合成板块', 'kind': 'industry',
        'members_observed_date': OBSERVED, 'source': 'fuyao'}]
    assert classified['topics'][0]['topic_code'] == '885001.TI'
    assert classified['source_snapshot_id'] == GENERATION and len(classified['source_sha256']) == 64
    assert '不是昨日涨停原因' in classified['reason']


def test_later_current_members_cannot_fill_missing_original_membership(published):
    value = payload()
    value['previous_review']['observations'] = [{'instrument_id': '600003.SH'}]
    value['stocks'].append({'instrument_id': '600003.SH', 'latest_price': 10, 'prev_close': 10, 'change_pct': 0})
    publish(published)
    later = published / 'market-later'
    later.mkdir()
    (later / 'reference.json').write_text(json.dumps({'topics': [{**topic(),
        'instrument_ids': ['600001.SH', '600002.SH', '600003.SH'], 'observed_date': DAY}]}))
    (published / 'market-head.json').write_text(json.dumps({'snapshot_id': 'market-later'}))
    classified = context.build_member_limit_context(value)['stock_classifications']
    assert classified['600001.SH']['industries'][0]['topic_code'] == CODE
    assert classified['600003.SH']['state'] == 'unknown' and classified['600003.SH']['industries'] == []


def test_worker_freeze_is_pure_and_saved_classifications_take_priority(published, monkeypatch):
    value = payload()
    value['previous_review']['observations'] = [{'instrument_id': '600002.SH'}, {'instrument_id': '600003.SH'}]
    before = deepcopy(value)
    frozen = context.freeze_stock_classifications(value, [topic()])
    assert value == before
    assert set(frozen) == {'600001.SH', '600002.SH', '600003.SH'}
    assert frozen['600001.SH']['industries'][0]['members_observed_date'] == OBSERVED
    assert frozen['600003.SH']['state'] == 'unknown'
    value['stock_classifications'] = frozen
    value['sectors'][0].update(member_limit_up_count=1, member_limit_up_state='observed')
    monkeypatch.setattr(context, '_load_topics', lambda *_: pytest.fail('all requested evidence is frozen'))
    assert context.build_member_limit_context(value)['stock_classifications'] == frozen


@pytest.mark.parametrize('field,bad', [('topic_code', '885001.TI'), ('members_observed_date', DAY),
                                     ('source', 'other-provider'), ('kind', 'concept')])
def test_invalid_frozen_classification_is_not_replaced_with_a_current_list(published, monkeypatch, field, bad):
    value = payload()
    frozen = context.freeze_stock_classifications(value, [topic()])
    frozen['600001.SH']['industries'][0][field] = bad
    value['stock_classifications'] = frozen
    value['sectors'][0].update(member_limit_up_count=1, member_limit_up_state='observed')
    monkeypatch.setattr(context, '_load_topics', lambda *_: pytest.fail('frozen classification must not fetch replacements'))
    actual = context.build_member_limit_context(value)['stock_classifications']['600001.SH']
    assert actual['state'] == 'unknown' and actual['industries'] == []


def test_classifications_include_displayed_rankings_and_have_a_bound(published):
    value = payload()
    value['stocks'].append({'instrument_id': '600003.SH', 'latest_price': 10, 'prev_close': 10, 'change_pct': 0})
    frozen = context.freeze_stock_classifications(value, [topic()])
    assert set(frozen) == {'600001.SH', '600002.SH', '600003.SH'}
    value['previous_review']['observations'] = [{'instrument_id': f'{i:06}.SH'} for i in range(3001)]
    frozen = context.freeze_stock_classifications(value, [topic()])
    assert len(frozen) == context.MAX_CLASSIFIED_STOCKS


def test_target_windows_match_amount_table_retained_rankings_and_hot_list():
    value = {'stocks': [{'instrument_id': f'{i:06}.SH', 'amount': i} for i in range(1, 31)],
             'summary': {'top_amount': [{'instrument_id': '000001.SH'}] * 20 + [{'instrument_id': '000090.SH'}],
                         'top_gainers': [{'instrument_id': '000002.SH'}]},
             'hot_stocks': [{'thscode': f'{i:06}.SH'} for i in range(100, 111)]}
    before = deepcopy(value)
    symbols, truncated = context._target_symbols(value)
    assert set(symbols) == {f'{i:06}.SH' for i in (*range(21, 31), 1, 2, *range(100, 110))}
    assert not truncated and value == before
    assert '000090.SH' not in symbols and '000110.SH' not in symbols
    value['hot_stocks'][0] = {'instrument_id': '000099.SH', 'thscode': '000098.SH'}
    symbols, _ = context._target_symbols(value)
    assert '000099.SH' not in symbols and '000098.SH' not in symbols


def test_new_display_target_missing_from_frozen_mapping_stays_unknown(published, monkeypatch):
    value = payload()
    frozen = context.freeze_stock_classifications(value, [topic()])
    value['stock_classifications'] = {'600001.SH': frozen['600001.SH']}
    value['sectors'][0].update(member_limit_up_count=1, member_limit_up_state='observed')
    value['summary'] = {'top_amount': [value['stocks'][1]]}
    monkeypatch.setattr(context, '_load_topics', lambda *_: pytest.fail('missing frozen target must not read replacements'))
    result = context.build_member_limit_context(value)['stock_classifications']
    assert result['600001.SH']['state'] == 'observed_sample'
    assert result['600002.SH']['state'] == 'unknown'
    assert result['600002.SH']['industries'] == [] and result['600002.SH']['topics'] == []


def board_ranking_payload():
    value = payload()
    value['events'] = {}
    value['stocks'] = [
        {'instrument_id': f'{prefix + offset:06}.{exchange}', 'kind': 'stock',
         'name': f'合成{label}{offset}', 'amount': amount - offset,
         'latest_price': 11, 'prev_close': 10, 'change_pct': 10}
        for prefix, exchange, amount, label in (
            (600100, 'SH', 4000, '主板'), (300100, 'SZ', 3000, '创业'),
            (688100, 'SH', 2000, '科创'), (920100, 'BJ', 1000, '北交'))
        for offset in range(12)]
    value['sectors'][0].update(member_coverage={'total': 48, 'valid': 48, 'complete': True},
                               member_up=48, member_down=0)
    members = {**topic(), 'instrument_ids': [row['instrument_id'] for row in value['stocks']]}
    return value, members


def test_each_board_amount_top_ten_receives_original_member_evidence_without_freezing_the_universe():
    value, members = board_ranking_payload()
    before = deepcopy(value)
    frozen = context.freeze_stock_classifications(value, [members])
    expected = {row['instrument_id'] for row in value['stocks']
                if int(row['instrument_id'][:6]) % 100 < 10}
    assert len(expected) == 40 and set(frozen) == expected
    assert all(row['state'] == 'observed_sample' and row['industries'][0]['members_observed_date'] == OBSERVED
               for row in frozen.values())
    assert all(frozen[f'{prefix:06}.{exchange}']['industries'][0]['topic_code'] == CODE
               for prefix, exchange in ((300100, 'SZ'), (688100, 'SH'), (920100, 'BJ')))
    assert '600110.SH' not in frozen and '688110.SH' not in frozen
    assert len(frozen) < len(value['stocks']) <= context.MAX_CLASSIFIED_STOCKS
    assert value == before


@pytest.mark.parametrize('identity,expected', [
    ({'instrument_id': '600001.SH'}, 'main'),
    ({'instrument_id': '301001.SZ'}, 'chinext'),
    ({'instrument_id': '688001.SH'}, 'star'),
    ({'instrument_id': '920001.BJ'}, 'beijing'),
    ({'instrument_id': '688001.SH', 'board': 'unknown'}, 'unknown'),
    ({'instrument_id': '688001.SH', 'board': 'custom'}, 'unknown'),
    ({'instrument_id': '688001.SH', 'board': None}, 'star'),
    ({'instrument_id': '688001.SH', 'kind': 'index'}, 'unknown'),
    ({'instrument_id': '688001.SH', 'asset_type': 'fund'}, 'unknown'),
    ({'instrument_id': '688001.SH', 'kind': '', 'asset_type': 'stock'}, 'unknown'),
    ({'instrument_id': '688001.TI'}, 'unknown'),
    ({'instrument_id': '999999.SH', 'name': '合成创业板名字'}, 'unknown'),
])
def test_board_display_guard_matches_frontend_explicit_unknown_and_nonstock_rules(identity, expected):
    assert context._display_board(identity) == expected


def test_each_board_hot_top_ten_preserves_provider_order_and_excludes_unknown_board_guesses():
    hot = [{'thscode': f'{prefix + offset:06}.{exchange}', 'rank': 100 - offset}
           for prefix, exchange in ((600100, 'SH'), (300100, 'SZ'), (688100, 'SH'), (920100, 'BJ'))
           for offset in range(12)]
    # These are beyond the all-board ten; their code/name cannot override an
    # explicit unknown board or a non-stock identity in a selected-board view.
    hot[12:12] = [{'thscode': '688999.SH', 'board': 'unknown', 'rank': 1},
                  {'thscode': '300999.SZ', 'kind': 'index', 'rank': 1},
                  {'thscode': '999999.SH', 'name': '合成北交所', 'rank': 1}]
    value = {'stocks': [], 'hot_stocks': hot}
    before = deepcopy(value)
    symbols, truncated = context._target_symbols(value)
    expected = {f'{prefix + offset:06}.{exchange}'
                for prefix, exchange in ((600100, 'SH'), (300100, 'SZ'), (688100, 'SH'), (920100, 'BJ'))
                for offset in range(10)}
    assert set(symbols) == expected and len(symbols) == 40 and not truncated
    assert all(symbol not in symbols for symbol in ('688999.SH', '300999.SZ', '999999.SH', '688110.SH'))
    windows = context._ranking_windows(hot)
    assert [row['thscode'] for row in windows[3]] == [f'{688100 + offset:06}.SH' for offset in range(10)]
    assert value == before


def test_new_selected_board_targets_missing_from_frozen_mapping_stay_unknown(published, monkeypatch):
    value, members = board_ranking_payload()
    frozen = context.freeze_stock_classifications(value, [members])
    value['stock_classifications'] = {symbol: row for symbol, row in frozen.items() if symbol.startswith('600')}
    value['sectors'][0].update(member_limit_up_count=0, member_limit_up_state='observed')
    monkeypatch.setattr(context, '_load_topics', lambda *_: pytest.fail('a new board filter cannot expand a frozen mapping'))
    actual = context.build_member_limit_context(value)['stock_classifications']
    assert len(actual) == 40 and actual['600100.SH']['state'] == 'observed_sample'
    for symbol in ('300100.SZ', '688100.SH', '920100.BJ'):
        assert actual[symbol]['state'] == 'unknown'
        assert actual[symbol]['industries'] == [] and actual[symbol]['topics'] == []
