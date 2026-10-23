from fastapi import APIRouter, Body
from app.services import chart_annotations as service

router = APIRouter(prefix='/api/v1/instruments', tags=['chart-annotations'])


@router.get('/{instrument_id}/annotations')
def list_marks(instrument_id: str):
    return service.list_annotations(instrument_id)


@router.post('/{instrument_id}/annotations')
def create_mark(instrument_id: str, payload: dict = Body(...)):
    return service.create_annotation(instrument_id, payload)


@router.post('/{instrument_id}/annotations/recommendations')
def recommend_marks(instrument_id: str, payload: dict = Body(...)):
    return service.recommendations(instrument_id, payload)


@router.post('/{instrument_id}/annotations/recommendations/save')
def save_recommended_marks(instrument_id: str, payload: dict = Body(...)):
    return service.save_recommendations(instrument_id, payload)


@router.patch('/{instrument_id}/annotations/{annotation_id}')
def update_mark(instrument_id: str, annotation_id: str, payload: dict = Body(...)):
    return service.update_annotation(instrument_id, annotation_id, payload)


@router.delete('/{instrument_id}/annotations/{annotation_id}')
def remove_mark(instrument_id: str, annotation_id: str):
    return service.delete_annotation(instrument_id, annotation_id)
