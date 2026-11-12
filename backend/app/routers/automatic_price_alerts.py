"""Explicit per-stock automatic price reminders, separate from drawn lines."""
from fastapi import APIRouter, Body
from app.services import automatic_price_alerts as service

router = APIRouter(prefix='/api/v1', tags=['automatic-price-alerts'])


@router.get('/instruments/{instrument_id}/automatic-price-alerts')
def get_config(instrument_id: str):
    return service.get_config(instrument_id)


@router.put('/instruments/{instrument_id}/automatic-price-alerts')
def save_config(instrument_id: str, payload: dict = Body(...)):
    return service.save_config(instrument_id, payload)


@router.post('/watchlist/automatic-price-alerts/evaluate')
def evaluate(payload: dict = Body(default={})):
    return service.create_check(payload)
