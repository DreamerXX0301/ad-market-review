from fastapi import APIRouter, Body, Query
from app.services import market_mainlines as service

router = APIRouter(prefix='/api/v1/market/mainlines', tags=['daily-mainlines'])


@router.get('')
def list_mainlines(date: str, known_at_ms: int | None = Query(default=None, ge=1, le=9007199254740991)):
    return service.list_mainlines(date, known_at_ms)


@router.get('/history')
def mainline_history(date: str, instrument_id: str, limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0, le=2147483647)):
    return service.mainline_history(date, instrument_id, limit, offset)


@router.post('/revisions')
def save_revision(payload: dict = Body(...)):
    return service.save_revision(payload)
