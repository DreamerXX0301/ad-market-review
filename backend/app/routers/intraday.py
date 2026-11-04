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


@router.get('/quotes/{snapshot_id}/{symbol}')
def stock_quote(snapshot_id: str, symbol: str):
    from app.services.intraday_stock_detail import read_quote
    return read_quote(snapshot_id, symbol)
