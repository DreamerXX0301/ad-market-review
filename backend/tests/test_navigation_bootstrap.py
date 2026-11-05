"""Navigation must not scan the published tree to open a market page."""
from fastapi.testclient import TestClient

from test_platform import isolated


def test_navigation_skips_inventory_and_preserves_full_bootstrap(isolated, monkeypatch):
    from app.main import app
    from app.services import published_storage

    calls = []

    def inventory(root, snapshot):
        calls.append((root, snapshot))
        return {'logical_bytes': 123, 'complete': True, 'file_count': 1}

    monkeypatch.setattr(published_storage, 'published_storage', inventory)
    with TestClient(app) as client:
        light = client.get('/api/v1/bootstrap?scope=navigation')
        assert light.status_code == 200
        assert calls == [], 'opening a page may not walk the publication directory'
        light = light.json()
        assert light['data_summary']['storage_bytes'] is None
        assert light['data_summary']['storage_accounting'] is None
        full = client.get('/api/v1/bootstrap').json()
        assert len(calls) == 1
        assert full['data_summary']['storage_bytes'] == 123
        for key in ('data_date', 'market_snapshot_id', 'market_dates', 'indices', 'sources'):
            assert light[key] == full[key]
        for key, value in full['data_summary'].items():
            if key not in ('storage_bytes', 'storage_accounting'):
                assert light['data_summary'][key] == value
        assert client.get('/api/v1/bootstrap?scope=unsafe').status_code == 422
