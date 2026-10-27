"""Dated local dragon-tiger views and explicit manual collection jobs."""
from fastapi import APIRouter,Body,Query
from fastapi.responses import JSONResponse
from app.core import json_clean
from app.services import dragon_tiger

router=APIRouter(prefix='/api/v1/market/dragon-tiger',tags=['dragon-tiger'])

@router.get('')
def day_view(date:str|None=None):
    return JSONResponse(json_clean(dragon_tiger.read_view(date)))

@router.post('/jobs')
def create(payload:dict=Body(...)):
    return json_clean(dragon_tiger.create_job(payload))

@router.get('/{instrument_id}')
def seats(instrument_id:str,date:str|None=None,range_days:int=Query(1,ge=1,le=3)):
    if range_days not in (1,3):
        from fastapi import HTTPException
        raise HTTPException(422,'龙虎榜区间仅支持单日或三日')
    return JSONResponse(json_clean(dragon_tiger.read_seats(instrument_id,date,range_days)))
