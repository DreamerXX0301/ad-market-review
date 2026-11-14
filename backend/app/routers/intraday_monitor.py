"""Read-only monitoring views, explicit settings and explicit read receipts."""
from fastapi import APIRouter, Body, Query
from app.services import intraday_monitor as service

router = APIRouter(prefix='/api/v1/intraday-monitor', tags=['intraday-monitor'])


@router.get('')
def view():
    return service.read_view()


@router.get('/summary')
def summary():
    return service.read_summary()


@router.get('/events')
def events(date: str | None = None, limit: int = Query(200, ge=1, le=500)):
    return service.read_events(date, limit)


@router.put('/settings')
def settings(payload: dict = Body(...)):
    return service.save_settings(payload)


@router.post('/events/read')
def read(payload: dict = Body(...)):
    return service.mark_read(payload)
