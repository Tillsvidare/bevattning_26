"""API-endpoints för schema och historik."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from . import mqtt
from .config import VALVE_IDS
from .database import SessionLocal
from .models import ValveEvent
from .schemas import (
    MAX_ENTRIES,
    HistoryEvent,
    IrrigationState,
    ScheduleEntry,
    SensorState,
)

router = APIRouter(prefix="/api")


def _check_valve_id(valve_id: int) -> None:
    if valve_id not in VALVE_IDS:
        raise HTTPException(status_code=404, detail=f"Okänd ventil: {valve_id}")


@router.get("/health")
async def health() -> dict:
    return {"mqtt": mqtt.is_connected()}


@router.get("/irrigation", response_model=IrrigationState)
async def get_irrigation() -> dict:
    if "enabled" not in mqtt.irrigation:
        raise HTTPException(
            status_code=404,
            detail="Enheten har inte rapporterat huvudbrytaren ännu",
        )
    return mqtt.irrigation


@router.post("/irrigation", status_code=202)
async def set_irrigation(state: IrrigationState) -> dict:
    try:
        await mqtt.publish_irrigation_set(state.enabled)
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"status": "skickat", "enabled": state.enabled}


@router.get("/sensor", response_model=SensorState)
async def get_sensor() -> dict:
    """Vattensensorns läge. Ingen POST — enheten äger läget."""
    if "wet" not in mqtt.sensor:
        raise HTTPException(
            status_code=404,
            detail="Enheten har inte rapporterat vattensensorn ännu",
        )
    return mqtt.sensor


@router.get("/valves/{valve_id}/schedule", response_model=list[ScheduleEntry])
async def get_schedule(valve_id: int) -> list:
    _check_valve_id(valve_id)
    schedule = mqtt.schedules.get(valve_id)
    if schedule is None:
        raise HTTPException(
            status_code=404,
            detail="Enheten har inte rapporterat något schema ännu",
        )
    return schedule


@router.post("/valves/{valve_id}/schedule", status_code=202)
async def set_schedule(valve_id: int, schedule: list[ScheduleEntry]) -> dict:
    _check_valve_id(valve_id)
    if len(schedule) > MAX_ENTRIES:
        raise HTTPException(
            status_code=422, detail=f"Max {MAX_ENTRIES} bevattningar per dygn"
        )
    try:
        await mqtt.publish_schedule_set(
            valve_id, [entry.model_dump() for entry in schedule]
        )
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"status": "skickat", "valve_id": valve_id}


@router.get("/valves/{valve_id}/history", response_model=list[HistoryEvent])
async def get_history(
    valve_id: int, days: int = Query(default=7, ge=1, le=90)
) -> list[ValveEvent]:
    _check_valve_id(valve_id)
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    with SessionLocal() as session:
        events = session.scalars(
            select(ValveEvent)
            .where(ValveEvent.valve_id == valve_id, ValveEvent.ts >= since)
            .order_by(ValveEvent.ts)
        ).all()
    return list(events)
