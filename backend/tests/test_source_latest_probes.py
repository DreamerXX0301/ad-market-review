"""Latest permission evidence is selected by current credential revision."""
import json

import pytest

from app.core import connect, dumps, set_setting
from app.services import source_service as service
from test_queue_sources import isolated, by_id


def insert_probe(connection, identifier, source, capability, stamp, evidence, access='allowed'):
    connection.execute('''INSERT INTO capability_probe
        (probe_id,connection_id,capability,document_support,account_access,coverage_state,
         probed_at_ms,schema_version,evidence_json) VALUES(?,?,?,'supported',?,'partial',?,'fixture',?)''',
        (identifier, source, capability, access, stamp, dumps(evidence)))


@pytest.mark.parametrize('revision', ['legacy', 'current', None, {'nested': ['value', 1]}, '{"looks":"structured"}'])
def test_latest_query_matches_full_append_only_reference_for_revisions_and_ties(isolated, revision):
    revisions = {'fuyao': revision}
    with connect() as connection:
        evidences = [{}, {'credential_revision': None}, {'credential_revision': 'old'},
                     {'credential_revision': revision}, {'credential_revision': {'looks': 'structured'}}]
        for number, evidence in enumerate(evidences):
            for capability in ('daily', 'index', 'future-capability'):
                insert_probe(connection, f'fixture-{number}-{capability}', 'fuyao', capability,
                             10+number//2, {**evidence, 'reason': f'reason-{number}'},
                             'denied' if number % 2 else 'allowed')
        insert_probe(connection, 'public', 'tickflow-public', 'daily', 3, {})
        expected = {}
        for row in connection.execute('SELECT * FROM capability_probe ORDER BY probed_at_ms,rowid'):
            evidence = json.loads(row['evidence_json'])
            if evidence.get('credential_revision', 'legacy') == revisions.get(row['connection_id'], 'legacy'):
                expected[(row['connection_id'], row['capability'])] = (row['probe_id'], evidence)
        actual = service._latest_probes(connection, revisions)
    assert {key: (row['probe_id'], row['_evidence']) for key, row in actual.items()} == expected


def test_current_revision_only_decodes_latest_evidence_and_reads_settings_once(isolated, monkeypatch):
    set_setting('source-credential-revision:fuyao', 'current')
    with connect() as connection:
        for number in range(60):
            insert_probe(connection, f'old-{number}', 'fuyao', 'daily', number+1,
                         {'credential_revision': 'old', 'reason': f'old-{number}'})
        insert_probe(connection, 'current-first', 'fuyao', 'daily', 100,
                     {'credential_revision': 'current', 'reason': 'superseded'})
        insert_probe(connection, 'current-last', 'fuyao', 'daily', 100,
                     {'credential_revision': 'current', 'reason': 'latest'}, 'denied')
    original = json.loads; decoded = []
    def loads(value, *args, **kwargs):
        result = original(value, *args, **kwargs)
        if isinstance(result, dict) and 'reason' in result: decoded.append(result['reason'])
        return result
    monkeypatch.setattr(service.json, 'loads', loads)
    monkeypatch.setattr(service, 'setting', lambda *_: pytest.fail('source list must read active source in its own snapshot'))
    monkeypatch.setattr(service.CredentialStore, 'get', lambda *_: pytest.fail('source reads must never decrypt a Key'))
    result = service.list_sources(include_supplement=False)
    assert decoded == ['latest']
    daily = next(row for row in by_id(result, 'fuyao')['capabilities'] if row['key'] == 'daily')
    assert daily['status'] == 'locked' and daily['reason'] == 'latest' and daily['checked_at'] == 100


def test_configure_and_verify_return_only_the_requested_source_without_supplement(isolated, monkeypatch):
    monkeypatch.setattr(service, 'supplement_status', lambda: pytest.fail('source mutation response does not consume supplement status'))
    monkeypatch.setattr(service.ProviderClient, 'request', lambda *_: {'data': {'item': [{'fixture': True}]}})
    configured = service.configure('fuyao', {'api_key': 'private-fixture-value'})
    assert configured['configured']
    result = service.verify('fuyao')
    assert result['status'] == 'verified'
    assert 'private-fixture-value' not in json.dumps(result)
