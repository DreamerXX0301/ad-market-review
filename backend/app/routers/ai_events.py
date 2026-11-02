"""Only replay an existing answer stream; this GET never starts generation."""
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from app.services.ai_event_read import MAX_CURSOR, read_batch, resume_cursor, stream_events

router = APIRouter(prefix='/api/v1/ai', tags=['ai'])


@router.get('/tasks/{task_id}/events')
async def task_events(request: Request, task_id: str, after: int = Query(0, ge=0, le=MAX_CURSOR)):
    cursor = resume_cursor(after, request.headers.get('last-event-id'))
    read_batch(task_id, cursor, limit=1)  # Return a real 404 before opening SSE.
    return StreamingResponse(
        stream_events(request, task_id, cursor), media_type='text/event-stream',
        headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'},
    )
