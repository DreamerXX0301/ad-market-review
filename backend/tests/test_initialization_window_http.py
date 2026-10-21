"""The UI's advertised initial window and the frozen worker request agree."""
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path/'data'))
    from app.core import clear_market_cache, ensure_ready
    clear_market_cache(); ensure_ready()
    from app.routers import platform
    monkeypatch.setattr(platform.source_service,'client',lambda:SimpleNamespace(source_id='fuyao',key='isolated-fixture'))
    from app.main import app
    with TestClient(app) as test: yield test
    clear_market_cache()


def test_default_advertised_window_is_frozen_in_initial_task(client):
    from app.services.history_window import resolve_initial_window
    expected=resolve_initial_window()
    state=client.get('/api/v1/data/status').json()['initialization_window']
    assert state['default_window']==expected['window'] and state['default_years']==expected['years']
    assert state['retained_window'] is None
    result=client.post('/api/v1/sync/tasks',json={'mode':'initialise','request_key':'default-window'})
    assert result.status_code==200
    assert result.json()['parameters']['window']==expected['window']


@pytest.mark.parametrize('window',['near-five-years','near-three-years'])
def test_explicit_compatible_window_remains_frozen(client,window):
    result=client.post('/api/v1/sync/tasks',json={'mode':'initialise','window':window})
    assert result.status_code==200 and result.json()['parameters']['window']==window


def test_source_unsupported_ten_year_window_is_rejected_before_enqueue(client):
    state=client.get('/api/v1/data/status').json()['initialization_window']
    assert 'near-ten-years' not in [item['window'] for item in state['supported_windows']]
    result=client.post('/api/v1/sync/tasks',json={'mode':'initialise','window':'near-ten-years'})
    assert result.status_code==422 and '5' in result.json()['detail']
    assert client.get('/api/v1/tasks').json()['total']==0


@pytest.mark.parametrize('extra',[{'window':'all-history'},{'window':10},{'years':10},{'history_years':100}])
def test_invalid_window_cannot_submit_a_task(client,extra):
    assert client.post('/api/v1/sync/tasks',json={'mode':'initialise',**extra}).status_code==422
    assert client.get('/api/v1/tasks').json()['total']==0
