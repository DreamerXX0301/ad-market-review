"""原代际SQLite/SHA成员锚点与竞价题材统计；全部临时合成资料。"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from app import core
from app.analytics.market_storage import write_reference
from app.services import auction_topic_summary as summary, source_service

DAY, PRIOR, OBSERVED = '2026-10-29', '2026-10-28', '2026-10-21'
GENERATION = 'market-auction-topic-fixture'
SNAPSHOT = 'topics-auction-fixture'
CODES = ['600001.SH', '600002.SH', '600003.SH', '000001.SZ']


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def payload():
    return {'version': 'auction-snapshot-v1', 'date': DAY, 'previous_date': PRIOR,
        'collected_start_at': DAY + 'T09:26:00+08:00', 'previous_review': {'date': PRIOR,
        'payload': {'mode': 'previous_session_price_facts', 'date': PRIOR, 'source_snapshot_id': GENERATION}},
        'universe': {'items': [{'instrument_id': code} for code in CODES]},
        'quotes': {'items': [{'instrument_id': code, 'state': state, 'auction_pct': change}
            for code, state, change in [(CODES[0], 'observed', 6), (CODES[1], 'unknown', None),
                                        (CODES[2], 'observed', 0), (CODES[3], 'observed', -2)]]}}


def topic(code='881001.TI', kind='industry', members=None, **changes):
    return {'topic_code': code, 'name': '同名合成板块', 'kind': kind, 'source': 'fuyao',
            'observed_date': OBSERVED, 'members_complete': True,
            'instrument_ids': members if members is not None else CODES[:3], **changes}


def default_topics():
    return [topic(), topic('885001.TI', 'concept', [CODES[0], CODES[3]]),
            topic('885002.TI', 'concept', [CODES[1]])]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path / 'data'))
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('SHIXU_CREDENTIAL_MASTER_KEY_FILE', str(tmp_path / 'unused-synthetic-master'))
    core.ensure_ready()
    monkeypatch.setattr(source_service, 'client', lambda *_: pytest.fail('topic reads cannot fetch'))
    monkeypatch.setattr(core, 'get_market', lambda: pytest.fail('topic reads cannot follow current market'))
    return core.published_dir()


def publish(root, rows=None, *, split=True):
    rows = default_topics() if rows is None else rows
    folder = root / GENERATION
    folder.mkdir(parents=True)
    reference = folder / 'reference.json'
    if split:
        write_reference({'topics': rows, 'indices': {}}, root, folder, GENERATION)
    else:
        reference.write_bytes(encode({'topics': rows, 'indices': {}}))
    raw = reference.read_bytes()
    artifacts = [{'object_key': GENERATION + '/reference.json', 'role': 'reference.json',
                  'sha256': sha256(raw).hexdigest(), 'byte_count': len(raw), 'row_count': None}]
    document = json.loads(raw)
    if split:
        part = document['fields']['topics']
        artifacts.append({'object_key': part['object_key'], 'role': 'reference-part/' + part['sha256'],
                          'sha256': part['sha256'], 'byte_count': part['bytes'], 'row_count': None})
    (folder / 'manifest.json').write_bytes(encode({'summary': {'snapshot_id': GENERATION,
        'source': 'fuyao', 'data_end': PRIOR, 'files': [{'role': 'reference.json',
            'path': 'published/' + GENERATION + '/reference.json',
            'sha256': artifacts[0]['sha256'], 'bytes': len(raw)}]}}))
    catalog = root / 'catalog' / SNAPSHOT / 'manifest.json'
    catalog.parent.mkdir(parents=True)
    catalog.write_bytes(encode({'snapshot_id': SNAPSHOT, 'module': 'topics',
        'lineage': {'market_snapshot_id': GENERATION}, 'artifacts': artifacts}))
    with core.connect() as connection:
        connection.execute('''INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,
            parameters_hash,status,created_at_ms) VALUES('topic-fixture','topic-fixture','topic-fixture',
            'initialise','{}',?,'succeeded',1)''', ('a' * 64,))
        connection.execute('''INSERT INTO dataset_snapshot(snapshot_id,module,scope_key,job_id,
            data_begin,data_end,knowledge_cutoff_ms,price_basis,algorithm_version,lineage_json,
            manifest_path,manifest_sha256,state,quality,created_at_ms)
            VALUES(?,'topics','all','topic-fixture',?,?,1,'none','fixture',?,?,?,'staged','ready',1)''',
            (SNAPSHOT, PRIOR, PRIOR, json.dumps({'market_snapshot_id': GENERATION}),
             str(catalog.relative_to(root)), sha256(catalog.read_bytes()).hexdigest()))
        for item in artifacts:
            connection.execute('INSERT INTO snapshot_artifact VALUES(?,?,?,?,?,?)',
                (SNAPSHOT, item['object_key'], item['role'], item['sha256'], item['byte_count'], item['row_count']))
        connection.execute('''INSERT INTO snapshot_coverage(snapshot_id,feature,scope_json,coverage_state,
            observed_rows,continuous_begin,continuous_end) VALUES(?,'topics','{}','ready',?,?,?)''',
            (SNAPSHOT, len(rows), PRIOR, PRIOR))
        connection.execute("UPDATE dataset_snapshot SET state='validated' WHERE snapshot_id=?", (SNAPSHOT,))
        connection.execute("UPDATE dataset_snapshot SET state='published',published_at_ms=1 WHERE snapshot_id=?", (SNAPSHOT,))
    return folder


@pytest.mark.parametrize('split', [False, True])
def test_original_generation_keeps_same_names_separate_and_averages_only_valid_quotes(isolated, split):
    publish(isolated, split=split)
    value = payload(); original = deepcopy(value)
    result = summary.build_topic_summary(value)
    assert value == original
    assert result['source_snapshot_id'] == GENERATION and len(result['source_sha256']) == 64
    assert result['coverage'] == {'requested': 4, 'classified': 4, 'unclassified': 0,
        'valid_quotes': 3, 'groups': 3, 'rejected_groups': 0}
    groups = {item['topic_code']: item for item in result['groups']}
    assert len(groups) == 3 and {item['name'] for item in result['groups']} == {'同名合成板块'}
    assert groups['881001.TI']['sample_count'] == 3
    assert groups['881001.TI']['valid_count'] == 2 and groups['881001.TI']['missing_count'] == 1
    assert groups['881001.TI']['average_auction_pct'] == 3
    assert groups['885001.TI']['average_auction_pct'] == 2
    assert groups['885002.TI']['valid_count'] == 0 and groups['885002.TI']['average_auction_pct'] is None
    assert all(item['members_observed_date'] == OBSERVED for item in result['groups'])
    assert result['stock_classifications'][CODES[0]]['industries'][0]['topic_code'] == '881001.TI'
    assert result['stock_classifications'][CODES[0]]['topics'][0]['topic_code'] == '885001.TI'


def test_true_zero_is_valid_but_unknown_numeric_value_and_boolean_are_not(isolated):
    publish(isolated, [topic(members=CODES[:3])])
    value = payload()
    value['quotes']['items'][1]['auction_pct'] = 999
    value['quotes']['items'][0]['auction_pct'] = True
    row = summary.build_topic_summary(value)['groups'][0]
    assert row['sample_count'] == 3 and row['valid_count'] == 1 and row['missing_count'] == 2
    assert row['average_auction_pct'] == 0


@pytest.mark.parametrize('damage', ['no_source', 'conflicting_facts', 'future_context', 'wrong_source_context', 'context_conflict'])
def test_missing_conflicting_or_future_generation_never_falls_back_to_current(isolated, monkeypatch, damage):
    publish(isolated)
    value = payload()
    if damage == 'no_source':
        value['previous_review']['payload'].pop('source_snapshot_id')
    elif damage == 'conflicting_facts':
        value['previous_review']['background'] = {**value['previous_review']['payload'], 'source_snapshot_id': 'market-other'}
    else:
        value['market_context'] = {'source_snapshot_id': GENERATION, 'source': 'fuyao',
                                  'data_end': PRIOR, 'known_at_ms': 1}
        if damage == 'future_context': value['market_context']['data_end'] = DAY
        if damage == 'wrong_source_context': value['market_context']['source'] = 'other'
        if damage == 'context_conflict': value['market_context']['source_snapshot_id'] = 'market-other'
    monkeypatch.setattr(summary, '_load_original_topics', lambda *_: pytest.fail('unidentified sources must not read files'))
    result = summary.build_topic_summary(value)
    assert result['state'] == 'unknown' and result['groups'] == []


def test_new_frozen_market_context_supports_retained_review_without_background_facts(isolated):
    publish(isolated)
    value = payload()
    value['previous_review']['payload'] = {'date': PRIOR, 'plan': '合成已保存复盘'}
    value['market_context'] = {'source_snapshot_id': GENERATION, 'source': 'fuyao',
                              'data_end': PRIOR, 'known_at_ms': 1}
    assert len(summary.build_topic_summary(value)['groups']) == 3


def test_only_exact_original_files_are_read_and_split_unrelated_parts_are_ignored(isolated, monkeypatch):
    folder = publish(isolated)
    (isolated / 'market-head.json').write_text('{"snapshot_id":"market-current-must-not-read"}')
    original = Path.rglob
    def no_scan(path, pattern):
        if path == isolated or path.is_relative_to(isolated):
            pytest.fail('topic summary must not enumerate published history')
        return original(path, pattern)
    monkeypatch.setattr(Path, 'rglob', no_scan)
    assert summary.build_topic_summary(payload())['coverage']['classified'] == 4
    # Missing the original cannot be repaired by a current same-name catalog.
    (folder / 'reference.json').unlink()
    result = summary.build_topic_summary(payload())
    assert result['state'] == 'unknown' and result['groups'] == []


@pytest.mark.parametrize('damage', ['reference', 'catalog', 'part', 'self_rehashed_manifest', 'missing_sql_anchor'])
def test_sql_anchored_sha_cannot_be_replaced_by_self_declared_disk_hashes(isolated, damage):
    folder = publish(isolated)
    path = folder / 'reference.json'
    document = json.loads(path.read_text())
    if damage == 'reference':
        path.write_text('{}')
    elif damage == 'catalog':
        (isolated / 'catalog' / SNAPSHOT / 'manifest.json').write_text('{}')
    elif damage == 'part':
        (isolated / document['fields']['topics']['object_key']).write_text('[]')
    elif damage == 'self_rehashed_manifest':
        path.write_bytes(encode({'topics': [topic('885999.TI', 'concept')]}))
        manifest = json.loads((folder / 'manifest.json').read_text())
        manifest['summary']['files'][0].update(sha256=sha256(path.read_bytes()).hexdigest(), bytes=path.stat().st_size)
        (folder / 'manifest.json').write_bytes(encode(manifest))
    else:
        # A valid but different payload generation has no matching SQL anchor.
        value = payload(); value['previous_review']['payload']['source_snapshot_id'] = 'market-missing'
        assert summary.build_topic_summary(value)['groups'] == []
        return
    result = summary.build_topic_summary(payload())
    assert result['state'] == 'unknown' and result['groups'] == []


@pytest.mark.parametrize('bad', [
    {'source': 'other'}, {'observed_date': DAY}, {'instrument_ids': [CODES[0], CODES[0]]},
    {'instrument_ids': ['bad']}, {'topic_code': 'bad'}, {'name': ''}])
def test_invalid_source_future_date_or_members_are_not_classified(isolated, bad):
    publish(isolated, [topic(**bad)])
    result = summary.build_topic_summary(payload())
    assert result['groups'] == [] and result['coverage']['classified'] == 0
    assert result['coverage']['rejected_groups'] == 1


def test_duplicate_topic_identity_is_not_silently_merged(isolated):
    publish(isolated, [topic(), topic(name='另一个同代码名称')])
    result = summary.build_topic_summary(payload())
    assert result['groups'] == [] and result['coverage']['rejected_groups'] == 2


def test_absent_snapshot_get_is_read_only_unknown(isolated):
    with core.connect() as connection:
        before = connection.execute('SELECT count(*) FROM job').fetchone()[0]
    result = summary.read_view(DAY)
    assert result['date'] == DAY and result['version'] == summary.VERSION and result['state'] == 'unknown'
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM job').fetchone()[0] == before
