"""Chart marks persist independently; neither quotes nor strategy rules are edited."""
import math
import pytest
from fastapi import HTTPException
from test_workspace import ws
from app import core
from app.services import chart_annotations as marks
from app.routers.chart_annotations import router


@pytest.fixture
def annotations(ws, monkeypatch):
    market, client = ws
    client.app.include_router(router)
    monkeypatch.setattr(marks, 'get_market', lambda: market)
    monkeypatch.setattr(market, 'status', lambda: {'data_end': market.days[-1]}, raising=False)
    return market, client


def test_saved_marks_can_be_read_updated_and_removed_without_touching_source(annotations):
    market, client = annotations
    original = [dict(row) for row in market.data['600001.SH']]
    response = client.post('/api/v1/instruments/600001.SH/annotations', json={'price': 123.45, 'label': '支撑位', 'color': 'amber'})
    assert response.status_code == 200, response.text
    saved = response.json()
    listed = client.get('/api/v1/instruments/600001.SH/annotations').json()
    assert listed['items'] == [saved]
    assert saved['price_basis'] == 'none' and saved['anchor_date'] is None
    changed = client.patch(f"/api/v1/instruments/600001.SH/annotations/{saved['annotation_id']}", json={'price': 120.0})
    assert changed.json()['price'] == 120 and changed.json()['label'] == '支撑位'
    # Neither reading nor writing a mark can create market jobs or change bars.
    assert market.data['600001.SH'] == original
    with core.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM job').fetchone()[0] == 1
        assert c.execute('SELECT COUNT(*) FROM strategy').fetchone()[0] == 0
    removed = client.delete(f"/api/v1/instruments/600001.SH/annotations/{saved['annotation_id']}")
    assert removed.json()['removed'] is True
    assert client.get('/api/v1/instruments/600001.SH/annotations').json()['items'] == []


def test_marks_are_isolated_by_instrument_and_adjustment_anchor(annotations):
    market, client = annotations
    anchor = market.days[70]
    saved = client.post('/api/v1/instruments/600001.SH/annotations', json={
        'price': 120, 'price_basis': 'forward_event_affine', 'anchor_date': anchor}).json()
    assert saved['anchor_date'] == anchor
    assert client.get('/api/v1/instruments/000001.SZ/annotations').json()['items'] == []
    assert client.delete('/api/v1/instruments/000001.SZ/annotations/'+saved['annotation_id']).status_code == 404
    assert client.patch('/api/v1/instruments/600001.SH/annotations/'+saved['annotation_id'], json={'anchor_date': market.days[71]}).status_code == 422
    assert client.get('/api/v1/instruments/600001.SH/annotations').json()['items'][0]['anchor_date'] == anchor


@pytest.mark.parametrize('payload', [
    {'price': 0}, {'price': -1}, {'price': True}, {'price': '12.0'}, {'price': 10**400},
    {'price': 1, 'label': 'x'*41}, {'price': 1, 'color': []},
    {'price': 1, 'label': 'a\nb'}, {'price': 1, 'color': 'bad'},
    {'price': 1, 'price_basis': 'none', 'anchor_date': '2026-01-05'},
    {'price': 1, 'price_basis': 'forward_event_affine', 'anchor_date': '2026-02-30'},
    {'price': 1, 'price_basis': 'forward_event_affine', 'anchor_date': '2099-01-01'},
])
def test_invalid_marks_never_reach_storage(annotations, payload):
    _, client = annotations
    assert client.post('/api/v1/instruments/600001.SH/annotations', json=payload).status_code == 422
    assert client.get('/api/v1/instruments/600001.SH/annotations').json()['items'] == []


@pytest.mark.parametrize('price', [math.inf, -math.inf, math.nan])
def test_nonfinite_prices_are_rejected(annotations, price):
    with pytest.raises(HTTPException): marks.create_annotation('600001.SH', {'price': price})


def test_instrument_and_line_limit_guards(annotations, monkeypatch):
    _, client = annotations
    assert client.post('/api/v1/instruments/000001.SH/annotations', json={'price': 12}).status_code == 404
    monkeypatch.setattr(marks, 'MAX_LINES', 2)
    for value in (100, 101): assert client.post('/api/v1/instruments/600001.SH/annotations', json={'price': value}).status_code == 200
    assert client.post('/api/v1/instruments/600001.SH/annotations', json={'price': 102}).status_code == 409
    assert client.get('/api/v1/instruments/600001.SH/annotations').json()['total'] == 2


@pytest.mark.parametrize('kind,color', [('support','green'),('resistance','red'),('reference','violet')])
def test_new_line_colors_follow_type_but_explicit_and_saved_colors_are_preserved(annotations,kind,color):
    _, client = annotations
    root='/api/v1/instruments/600001.SH/annotations'
    saved=client.post(root,json={'price':12,'line_type':kind}).json()
    assert saved['color']==color
    override=client.post(root,json={'price':13,'line_type':kind,'color':'violet'}).json()
    assert override['color']=='violet'
    legacy=client.post(root,json={'price':14,'color':'amber'}).json()
    changed=client.patch(root+'/'+legacy['annotation_id'],json={'label':'原线保留'}).json()
    assert changed['color']=='amber'
    switch=client.patch(root+'/'+legacy['annotation_id'],json={'line_type':'support'}).json()
    assert switch['color']=='green'


@pytest.mark.parametrize('invalid', [[], {}, None, True])
def test_invalid_line_type_returns_validation_error(annotations,invalid):
    _,client=annotations
    response=client.post('/api/v1/instruments/600001.SH/annotations',json={'price':12,'line_type':invalid})
    assert response.status_code==422
