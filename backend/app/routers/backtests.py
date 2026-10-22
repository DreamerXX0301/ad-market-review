"""Backtest HTTP contract; long computations execute only in the worker."""
from fastapi import APIRouter,HTTPException,Body,Query
from app.analytics import backtest_store as store

router=APIRouter(prefix='/api/v1/backtests',tags=['backtests'])


def call(function,*args):
    try:return function(*args)
    except store.BacktestRequestConflict as error:raise HTTPException(status_code=409,detail=str(error)) from error
    except KeyError as error:raise HTTPException(status_code=404,detail=str(error)) from error
    except ValueError as error:raise HTTPException(status_code=422,detail=str(error)) from error


@router.post('/availability')
def availability(payload:dict=Body(...)):
    return call(store.get_availability,payload.get('strategy_id'))


@router.get('')
def list_backtests(deleted:bool=False,limit:int=Query(100,ge=1,le=100),offset:int=Query(0,ge=0)):return call(store.list_backtests,deleted,limit,offset)


@router.post('')
def create_backtest(payload:dict=Body(...)):
    return call(store.create_backtest,payload)


@router.get('/{backtest_id}')
def get_backtest(backtest_id:str):return call(store.get_backtest,backtest_id)


@router.delete('/{backtest_id}')
def delete_backtest(backtest_id:str):return call(store.delete_backtest,backtest_id)


@router.post('/{backtest_id}/restore')
def restore_backtest(backtest_id:str):return call(store.restore_backtest,backtest_id)
