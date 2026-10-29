"""午评本地只读浏览及显式手动采集入口。"""
from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse

from app.core import json_clean
from app.services import noon_service as service

router = APIRouter(prefix='/api/v1/market/noon', tags=['noon-snapshots'])


@router.get('/dates')
def dates():
    return JSONResponse(json_clean(service.list_dates()))


@router.get('/close-review')
def close_review(date: str):
    from app.services.noon_close_service import read_view
    return JSONResponse(json_clean(read_view(date)))


@router.get('/close-review/items')
def close_review_items(date: str, review_id: str | None = None, section: str = 'events',
                       transition: str = 'all', board: str = 'all', sort: str = 'final_board_count',
                       direction: str = 'desc', cursor: str | None = Query(None, max_length=2048),
                       limit: int = Query(50, ge=1, le=100), classification: str | None = Query(None, max_length=400),
                       codes_only: bool = False):
    from app.services.noon_close_service import read_items
    return JSONResponse(json_clean(read_items(date, review_id=review_id, section=section,
        transition=transition, board=board, sort=sort, direction=direction, cursor=cursor,
        limit=limit, classification=classification, codes_only=codes_only)))


@router.get('')
def view(date: str | None = None):
    return JSONResponse(json_clean(service.read_view(date)))


@router.post('/jobs')
def create(payload: dict = Body(...)):
    return JSONResponse(json_clean(service.create_capture(payload)))


@router.get('/{snapshot_id}/notes')
def notes(snapshot_id: str):
    from app.services.noon_notes import read_notes
    return JSONResponse(json_clean(read_notes(snapshot_id)))


@router.get('/{snapshot_id}/sector-members')
def sector_members(snapshot_id: str, topic_code: str = Query(..., max_length=30),
                   board: str = 'all', query: str = Query('', max_length=100),
                   sort: str = 'change_pct', direction: str = 'desc',
                   page: int = Query(1, ge=1, le=10000), limit: int = Query(50, ge=1, le=100)):
    from app.services.noon_sector_views import read_sector_view
    return JSONResponse(json_clean(read_sector_view(snapshot_id, topic_code, board=board, query=query,
        sort=sort, direction=direction, page=page, limit=limit)))


@router.post('/{snapshot_id}/notes')
def save_note(snapshot_id: str, payload: dict = Body(...)):
    from app.services.noon_notes import save_note
    return JSONResponse(json_clean(save_note(snapshot_id, payload)))
