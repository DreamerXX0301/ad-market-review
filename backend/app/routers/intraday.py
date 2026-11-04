"""Intraday snapshot reads, capture requests, and local automation settings."""
from fastapi import APIRouter, Body, Query

from app.services import intraday_service

router = APIRouter(prefix='/api/v1/market/intraday', tags=['intraday'])


@router.get('')
def view(date: str | None = None):
    from app.services.intraday_brief import read_brief_view

    result = intraday_service.read_view(date)
    result.update(read_brief_view(result['date']))
    return result


@router.post('/jobs')
def create(payload: dict = Body(...)):
    return intraday_service.create_capture(payload)


@router.get('/automation')
def automation():
    return intraday_service.read_automation()


@router.put('/automation')
def configure(payload: dict = Body(...)):
    return intraday_service.configure_automation(payload)


@router.get('/history')
def history(date: str, slot: str, symbols: str = Query(min_length=9, max_length=320)):
    from app.services.intraday_stock_detail import read_history
    return read_history(date, slot, symbols)


@router.get('/members')
def members(date: str, slot: str, snapshot_id: str, topic_code: str, board: str = 'all',
            page: int = Query(1, ge=1, le=10000), limit: int = Query(30, ge=1, le=32)):
    from app.services.intraday_members import read_members

    return read_members(date, slot, snapshot_id, topic_code, board=board, page=page, limit=limit)


@router.get('/quotes/{snapshot_id}/{symbol}')
def stock_quote(snapshot_id: str, symbol: str):
    from app.services.intraday_stock_detail import read_quote
    return read_quote(snapshot_id, symbol)


@router.get('/auction-reference')
def auction_reference(date: str):
    from app.services.intraday_auction_reference import read_auction_reference
    return read_auction_reference(date)
