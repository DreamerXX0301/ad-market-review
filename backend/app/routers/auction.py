"""盘前竞价的本地浏览与手动采集入口。"""
from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from app.core import json_clean
from app.services import auction_service as service

router = APIRouter(prefix='/api/v1/market/auction', tags=['auction-snapshots'])


@router.get('/dates')
def dates():
    return JSONResponse(json_clean(service.list_dates()))


@router.get('/automation')
def automation_view():
    from app.services.auction_automation import read_view
    return JSONResponse(json_clean(read_view()))


@router.post('/automation')
def automation_config(payload: dict = Body(...)):
    from app.services.auction_automation import update_config
    return JSONResponse(json_clean(update_config(payload)))


@router.get('/close-review')
def close_review(date: str):
    from app.services.auction_close_review import read_close_review
    return JSONResponse(json_clean(read_close_review(date)))


@router.get('/topic-summary')
def topic_summary(date: str):
    from app.services.auction_topic_summary import read_view
    return JSONResponse(json_clean(read_view(date)))


@router.get('/feedback')
def feedback(date: str):
    from app.services.auction_feedback import read_view
    return JSONResponse(json_clean(read_view(date)))


@router.get('/discovery')
def discovery(date: str):
    from app.services.auction_discovery import read_view
    return JSONResponse(json_clean(read_view(date, include_themes=True)))


@router.get('/discovery/history')
def discovery_history(date: str):
    from app.services.auction_discovery import read_history
    return JSONResponse(json_clean(read_history(date)))


@router.get('/resonance')
def resonance(date: str):
    from app.services.auction_resonance import read_view
    return JSONResponse(json_clean(read_view(date)))


@router.get('')
def view(date: str | None = None):
    return JSONResponse(json_clean(service.read_view(date)))


@router.post('/jobs')
def create(payload: dict = Body(...)):
    return JSONResponse(json_clean(service.create_capture(payload)))
