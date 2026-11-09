"""Read-only API for local news archives and verified calendar sources."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.services import news_events
from app.services.pane_read_cache import with_historical_cache

router = APIRouter(prefix='/api/v1/market/news-events', tags=['news-events'])


@router.get('')
@with_historical_cache
def view(date: str | None = None):
    return JSONResponse(news_events.read_view(date))


@router.get('/calendar')
@with_historical_cache
def calendar(date: str | None = None, days: int = 7):
    return JSONResponse(news_events.read_calendar(date, days))
