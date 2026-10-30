"""Annual manual stock observations; browsing never creates members or jobs."""
from fastapi import APIRouter, Body, Query
from app.services import zodiac, zodiac_metrics

router = APIRouter(prefix='/api/v1', tags=['zodiac'])


@router.get('/zodiac-pools')
def pools():
    return zodiac.pools()


@router.get('/zodiac-pools/{year}')
def pool(year: int):
    return zodiac.pool(year)


@router.get('/zodiac-pools/{year}/members')
def members(year: int, status: str = 'tracking', query: str = Query('', max_length=200),
            relation: str = '', focused: bool = False, board: str = '', completeness: str = '',
            sort: str = Query('focused:desc,added_business_date:desc', max_length=200),
            offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    return zodiac_metrics.listing(year, status=status, query=query, relation=relation, focused=focused,
                                  board=board, completeness=completeness, sort=sort, offset=offset, limit=limit)


@router.post('/zodiac-pools/{year}/preview')
def preview(year: int, payload: dict = Body(...)):
    return zodiac.preview(year, payload)


@router.post('/zodiac-pools/{year}/members')
def add(year: int, payload: dict = Body(...)):
    return zodiac.add(year, payload)


@router.post('/zodiac-pools/{year}/verify-baselines')
def verify(year: int, payload: dict = Body(...)):
    return zodiac.verify(year, payload)


@router.patch('/zodiac-pool-members/{member_id}')
def edit(member_id: str, payload: dict = Body(...)):
    return zodiac.change(member_id, payload)


@router.post('/zodiac-pool-members/{member_id}/end')
def end(member_id: str, payload: dict = Body(...)):
    return zodiac.change(member_id, payload, end=True)


@router.get('/zodiac-pool-members/{member_id}/history')
def history(member_id: str):
    return zodiac.history(member_id)


@router.get('/zodiac-pool-members/{member_id}/metrics')
def detail(member_id: str):
    return zodiac_metrics.detail(member_id)


@router.get('/zodiac-pool-members/{member_id}/evidence')
def evidence(member_id: str):
    return {'entry_basis': zodiac.history(member_id)['item']['entry_basis']}
