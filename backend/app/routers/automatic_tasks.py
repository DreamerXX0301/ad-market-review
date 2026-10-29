"""Configuration only; GET/PUT never schedule, fetch or run a task."""
from fastapi import APIRouter, Body
from app.services import automatic_tasks as service

router = APIRouter(prefix='/api/v1/settings/automatic-tasks', tags=['settings'])


@router.get('')
def settings():
    return service.read_settings()


@router.put('')
def configure(payload: dict = Body(...)):
    return service.configure(payload)
