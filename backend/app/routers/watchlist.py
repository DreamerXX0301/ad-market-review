"""Personal watchlist routes, independent of strategy tracking lifecycles."""
from fastapi import APIRouter, Body, Query
from app.services import watchlist

router = APIRouter(prefix='/api/v1/watchlist', tags=['watchlist'])


@router.get('')
def list_items(query: str = Query('', max_length=200)):
    return watchlist.list_items(query)


@router.get('/ids')
def list_ids():
    return watchlist.list_ids()


@router.get('/line-alerts')
def line_alerts(as_of: str | None = None, limit: int = Query(100, ge=1, le=1000)):
    from app.services.line_alerts import list_alerts
    return list_alerts(as_of, limit)


@router.post('/line-alerts/evaluate')
def evaluate_lines(payload: dict = Body(default={})):
    from app.services.line_alerts import create_check
    return create_check(payload)


@router.post('')
def add_item(payload: dict = Body(...)):
    return watchlist.add_item(payload)


@router.delete('/{instrument_id}')
def remove_item(instrument_id: str):
    return watchlist.remove_item(instrument_id)
