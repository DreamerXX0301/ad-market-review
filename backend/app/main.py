"""Application assembly; imports never launch data synchronization or workers."""
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from fastapi import FastAPI,Request
from fastapi.responses import FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from app.core import APP_NAME,APP_ID,APP_VERSION,PROJECT_ROOT,ensure_ready
from app.routers import platform,workspace,backtests,watchlist,chart_annotations,market_mainlines,market_sentiment_snapshots,dragon_tiger,noon,auction,automatic_tasks
from app.routers import intraday, zodiac
from app.routers import ai
from app.routers import ai_events
from app.services.supplement_access import SupplementWriteMiddleware
from app.services.increment_access import IncrementWorkbenchMiddleware
from app.services.ai_attachments import AIRequestSizeMiddleware
from app.startup_warmup import warmup_read_dependencies

@asynccontextmanager
async def lifespan(app):
    ensure_ready()
    warmup_read_dependencies()
    yield

app=FastAPI(title=f'{APP_NAME} · A股研究工作台',version=APP_VERSION,lifespan=lifespan)
app.add_middleware(GZipMiddleware,minimum_size=1024,compresslevel=4)
app.add_middleware(SupplementWriteMiddleware)
app.add_middleware(IncrementWorkbenchMiddleware)
app.add_middleware(AIRequestSizeMiddleware)
# Register the literal path before platform's /market/{kind} catch-all.
app.include_router(market_mainlines.router)
app.include_router(market_sentiment_snapshots.router)
app.include_router(dragon_tiger.router)
app.include_router(noon.router)
app.include_router(intraday.router)
app.include_router(auction.router)
app.include_router(automatic_tasks.router)
app.include_router(ai.router)
app.include_router(ai_events.router)
app.include_router(platform.router)
app.include_router(workspace.router)
app.include_router(backtests.router)
app.include_router(watchlist.router)
app.include_router(zodiac.router)
app.include_router(chart_annotations.router)

@app.get('/health',include_in_schema=False)
def health():return {'status':'ok','application':APP_ID,'app_name':APP_NAME,'version':APP_VERSION}

@app.middleware('http')
async def local_write_origin(request:Request,call_next):
    origin=request.headers.get('origin')
    if origin and request.method not in ('GET','HEAD','OPTIONS'):
        parsed=urlparse(origin)
        if parsed.netloc!=request.headers.get('host') and parsed.hostname not in ('localhost','127.0.0.1','::1'):
            return JSONResponse({'detail':'跨站写请求被拒绝'},403)
    return await call_next(request)

@app.exception_handler(ValueError)
async def invalid_request(request:Request,error:ValueError):
    return JSONResponse({'detail':str(error)},422)

@app.exception_handler(RequestValidationError)
async def invalid_payload(request:Request,error:RequestValidationError):
    return JSONResponse({'detail':[{'loc':e.get('loc'),'msg':e.get('msg')} for e in error.errors()]},422)

dist=PROJECT_ROOT/'frontend/dist'
if (dist/'assets').is_dir():app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')

@app.get('/{path:path}',include_in_schema=False)
def frontend(path:str):
    if path.startswith('api/'):
        return JSONResponse({'detail':'接口不存在'},404)
    candidate=(dist/path).resolve()
    if candidate.is_relative_to(dist.resolve()) and candidate.is_file():return FileResponse(candidate)
    if (dist/'index.html').is_file():return FileResponse(dist/'index.html')
    return JSONResponse({'detail':'前端尚未构建，请在frontend执行npm ci && npm run build'},503)
