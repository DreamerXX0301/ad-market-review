"""Curated GPT choices and per-model admission; fake Keys/transports only."""
import json
import pytest

from app import core
from app.infrastructure.credentials import CredentialStore
from app.services import ai_service as service
from app.services.ai_catalog import MODELS, OPENAI_MODEL_IDS, OPENAI_PRICE_SOURCES, price
from app.services.ai_runtime import AIRuntime
from test_ai_service import isolated, configure, fake, drive


EXPECTED = (
    'gpt-6.1-sol', 'gpt-6-astra', 'gpt-6.1-terra', 'gpt-6-sol',
    'gpt-6-luna', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna',
)


def test_official_common_model_prices_and_order():
    assert OPENAI_MODEL_IDS == EXPECTED
    # Official standard USD/1M text-token rates reviewed on 2026-11-02.
    expected = [
        ('2', '0.1', '2.5', '10'), ('10', '1', '12.5', '50'),
        ('1', '0.05', '1.25', '5'), ('2', '0.2', '2.5', '10'),
        ('0.1', '0.01', '0.125', '0.5'), ('4', '0.4', '5', '20'),
        ('2', '0.2', '2.5', '12'), ('0.2', '0.02', '0.25', '1.2'),
    ]
    assert [tuple(model[key] for key in ('input', 'cached', 'cache_write', 'output'))
            for model in MODELS['openai']] == expected
    assert OPENAI_PRICE_SOURCES == {model: 'https://developers.openai.com/api/docs/models/'+model for model in EXPECTED}
    assert price('openai', 'gpt-4o') is None
    assert [model['id'] for model in MODELS['deepseek']] == ['deepseek-flash', 'deepseek-v4-pro']


def test_directory_discovery_cannot_expand_gpt_options_or_change_saved_model(isolated):
    client = isolated
    with core.connect() as db:
        db.execute("UPDATE ai_provider_config SET model='gpt-4o',models_json=? WHERE provider_id='openai'",
                   (json.dumps(['gpt-4o', 'embedding-fixture', 'gpt-6.1-terra', 'gpt-6.1-sol']),))
    row = next(item for item in client.get('/api/v1/ai/providers').json()['items'] if item['provider_id'] == 'openai')
    assert tuple(item['id'] for item in row['models']) == EXPECTED
    assert row['discovered_models'] == ['gpt-6.1-terra', 'gpt-6.1-sol']
    assert row['model'] == 'gpt-4o' and row['config_revision'] == 0


@pytest.mark.parametrize('model', ('gpt-4o', 'gpt-6.1-sol-2026-10-01', 'gpt-99-future', 'text-embedding-fixture'))
def test_unknown_gpt_rejected_before_key_write_or_config_mutation(isolated, monkeypatch, model):
    client = isolated
    monkeypatch.setattr(CredentialStore, 'save', lambda *args: pytest.fail('unsupported model must not write a Key'))
    response = client.put('/api/v1/ai/providers/openai', json={
        'expected_revision': 0, 'model': model, 'enabled': False, 'api_key': 'fixture-never-saved',
    })
    assert response.status_code == 422 and response.json()['detail']['reason_code'] == 'unsupported_model'
    row = next(item for item in service.providers()['items'] if item['provider_id'] == 'openai')
    assert row['config_revision'] == 0 and row['model'] == 'gpt-6.1-sol' and not row['configured']


@pytest.mark.parametrize('model', EXPECTED)
def test_each_common_model_requires_its_own_saved_key_model_verification(isolated, model):
    client = isolated
    runtime = AIRuntime(fake)
    try:
        row = configure(client, 'openai', model)
        premature = client.put('/api/v1/ai/providers/openai', json={
            'expected_revision': row['config_revision'], 'model': model, 'enabled': True,
        })
        assert premature.status_code == 409 and premature.json()['detail']['reason_code'] == 'verification_required'
        verification = client.post('/api/v1/ai/providers/openai/verify', json={
            'expected_revision': row['config_revision'], 'request_key': 'common-model-probe',
        })
        assert verification.status_code == 202
        assert drive(runtime, verification.json()['task_id'])['state'] == 'completed'
        row = next(item for item in service.providers()['items'] if item['provider_id'] == 'openai')
        assert row['model'] == model and row['verification']['state'] == 'passed'
        enabled = client.put('/api/v1/ai/providers/openai', json={
            'expected_revision': row['config_revision'], 'model': model, 'enabled': True,
        })
        assert enabled.status_code == 200 and enabled.json()['enabled']
        changed_model = 'gpt-6-astra' if model != 'gpt-6-astra' else 'gpt-6.1-sol'
        wrong_binding = client.put('/api/v1/ai/providers/openai', json={
            'expected_revision': enabled.json()['config_revision'], 'model': changed_model, 'enabled': True,
        })
        assert wrong_binding.status_code == 409 and wrong_binding.json()['detail']['reason_code'] == 'verification_required'
    finally:
        runtime.shutdown()


def test_old_unsupported_configuration_can_be_disabled_but_not_reenabled_or_rekeyed(isolated, monkeypatch):
    client = isolated
    with core.connect() as db:
        db.execute("UPDATE ai_provider_config SET model='gpt-4o',enabled=1 WHERE provider_id='openai'")
    disabled = client.put('/api/v1/ai/providers/openai', json={
        'expected_revision': 0, 'model': 'gpt-4o', 'enabled': False,
        'limits': {'daily_budget_usd': '0.5', 'daily_request_limit': 20, 'timeout_seconds': 45},
    })
    assert disabled.status_code == 200 and disabled.json()['model'] == 'gpt-4o' and not disabled.json()['enabled']
    monkeypatch.setattr(CredentialStore, 'save', lambda *args: pytest.fail('retired GPT ID must not accept another Key'))
    for changes in ({'enabled': True}, {'enabled': False, 'api_key': 'fixture-never-saved'}):
        response = client.put('/api/v1/ai/providers/openai', json={
            'expected_revision': disabled.json()['config_revision'], 'model': 'gpt-4o', **changes,
        })
        assert response.status_code == 422 and response.json()['detail']['reason_code'] == 'unsupported_model'


def test_changing_supported_gpt_does_not_rewrite_historical_task_model(isolated):
    client = isolated
    runtime = AIRuntime(fake)
    try:
        row = configure(client, 'openai', 'gpt-6.1-sol')
        created = client.post('/api/v1/ai/providers/openai/verify', json={
            'expected_revision': row['config_revision'], 'request_key': 'historical-probe',
        }).json()
        drive(runtime, created['task_id'])
        row = next(item for item in service.providers()['items'] if item['provider_id'] == 'openai')
        with core.connect() as db:
            original_task = tuple(db.execute('SELECT * FROM ai_task WHERE task_id=?', (created['task_id'],)).fetchone())
        response = client.put('/api/v1/ai/providers/openai', json={
            'expected_revision': row['config_revision'], 'model': 'gpt-6.1-terra', 'enabled': False,
        })
        assert response.status_code == 200
        with core.connect() as db:
            assert db.execute('SELECT model FROM ai_task WHERE task_id=?', (created['task_id'],)).fetchone()[0] == 'gpt-6.1-sol'
            assert tuple(db.execute('SELECT * FROM ai_task WHERE task_id=?', (created['task_id'],)).fetchone()) == original_task
    finally:
        runtime.shutdown()
