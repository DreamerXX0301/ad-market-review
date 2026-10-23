from fastapi import APIRouter, Body, Query
from app.services import market_sentiment_snapshots as service

router = APIRouter(prefix='/api/v1/market/sentiment-snapshots', tags=['manual-sentiment-snapshots'])


@router.post('')
def save_snapshot(payload: dict = Body(...)):
    return service.save_snapshot(payload)


@router.get('')
def list_snapshots(date: str | None = None, known_at_ms: int | None = Query(default=None, ge=1, le=9007199254740991),
                   limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=2147483647)):
    return service.list_snapshots(date, known_at_ms, limit, offset)


@router.get('/{snapshot_id}')
def get_snapshot(snapshot_id: str):
    return service.get_snapshot(snapshot_id)
