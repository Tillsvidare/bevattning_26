"""API-endpoints: enheter, schema, historik — allt scopat till inloggad ägare.

Svarsformerna för irrigation/sensor/schedule/history är identiska med
en-enhets-versionen; bara URL-prefixet /api/devices/{id} är nytt.
"""

import json
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select

from . import mosq_passwd, mqtt
from .auth import get_current_user, owned_device
from .config import VALVE_IDS
from .database import SessionLocal
from .models import ClaimCode, Device, User, ValveEvent
from .schemas import (
    MAX_ENTRIES,
    ClaimCodeOut,
    DeviceOut,
    DevicePatch,
    HistoryEvent,
    IrrigationState,
    ScheduleEntry,
    SensorState,
)

router = APIRouter(prefix="/api")

CLAIM_CODE_TTL_H = 24


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _check_valve_id(valve_id: int) -> None:
    if valve_id not in VALVE_IDS:
        raise HTTPException(status_code=404, detail=f"Okänd ventil: {valve_id}")


@router.get("/health")
async def health() -> dict:
    return {"mqtt": mqtt.is_connected()}


# ---------- Enheter ----------


@router.get("/devices", response_model=list[DeviceOut])
async def list_devices(user: User = Depends(get_current_user)) -> list[Device]:
    with SessionLocal() as session:
        devices = session.scalars(
            select(Device).where(Device.user_id == user.id).order_by(Device.created_at)
        ).all()
    return list(devices)


@router.patch("/devices/{device_id}", response_model=DeviceOut)
async def rename_device(
    body: DevicePatch, device: Device = Depends(owned_device)
) -> Device:
    with SessionLocal() as session:
        device = session.get(Device, device.id)
        device.name = body.name.strip()
        session.commit()
    return device


@router.delete("/devices/{device_id}")
async def delete_device(device: Device = Depends(owned_device)) -> dict:
    with SessionLocal() as session:
        session.execute(delete(ValveEvent).where(ValveEvent.device_id == device.id))
        session.execute(delete(Device).where(Device.id == device.id))
        session.commit()
    mosq_passwd.write_passwd_file()  # enhetens MQTT-konto slutar gälla
    return {"status": "borttagen", "device_id": device.id}


@router.post("/claim-codes", response_model=ClaimCodeOut)
async def create_claim_code(user: User = Depends(get_current_user)) -> ClaimCode:
    claim = ClaimCode(
        code="KOD-" + secrets.token_hex(3).upper(),
        user_id=user.id,
        expires_at=_utcnow() + timedelta(hours=CLAIM_CODE_TTL_H),
    )
    with SessionLocal() as session:
        session.add(claim)
        session.commit()
    return claim


# ---------- Enhetens tillstånd (läses ur devices-raden, skrivs via MQTT) ----------


@router.get("/devices/{device_id}/irrigation", response_model=IrrigationState)
async def get_irrigation(device: Device = Depends(owned_device)) -> dict:
    if device.irrigation_enabled is None:
        raise HTTPException(
            status_code=404,
            detail="Enheten har inte rapporterat huvudbrytaren ännu",
        )
    return {"enabled": device.irrigation_enabled}


@router.post("/devices/{device_id}/irrigation", status_code=202)
async def set_irrigation(
    state: IrrigationState, device: Device = Depends(owned_device)
) -> dict:
    try:
        await mqtt.publish_irrigation_set(device.id, state.enabled)
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"status": "skickat", "enabled": state.enabled}


@router.get("/devices/{device_id}/sensor", response_model=SensorState)
async def get_sensor(device: Device = Depends(owned_device)) -> dict:
    """Vattensensorns läge. Ingen POST — enheten äger läget."""
    if device.sensor_wet is None:
        raise HTTPException(
            status_code=404,
            detail="Enheten har inte rapporterat vattensensorn ännu",
        )
    return {"wet": device.sensor_wet}


@router.get(
    "/devices/{device_id}/valves/{valve_id}/schedule",
    response_model=list[ScheduleEntry],
)
async def get_schedule(valve_id: int, device: Device = Depends(owned_device)) -> list:
    _check_valve_id(valve_id)
    schedules = json.loads(device.schedules_json) if device.schedules_json else {}
    schedule = schedules.get(str(valve_id))
    if schedule is None:
        raise HTTPException(
            status_code=404,
            detail="Enheten har inte rapporterat något schema ännu",
        )
    return schedule


@router.post("/devices/{device_id}/valves/{valve_id}/schedule", status_code=202)
async def set_schedule(
    valve_id: int,
    schedule: list[ScheduleEntry],
    device: Device = Depends(owned_device),
) -> dict:
    _check_valve_id(valve_id)
    if len(schedule) > MAX_ENTRIES:
        raise HTTPException(
            status_code=422, detail=f"Max {MAX_ENTRIES} bevattningar per dygn"
        )
    try:
        await mqtt.publish_schedule_set(
            device.id, valve_id, [entry.model_dump() for entry in schedule]
        )
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"status": "skickat", "valve_id": valve_id}


@router.get(
    "/devices/{device_id}/valves/{valve_id}/history",
    response_model=list[HistoryEvent],
)
async def get_history(
    valve_id: int,
    device: Device = Depends(owned_device),
    days: int = Query(default=7, ge=1, le=90),
) -> list[ValveEvent]:
    _check_valve_id(valve_id)
    since = _utcnow() - timedelta(days=days)
    with SessionLocal() as session:
        events = session.scalars(
            select(ValveEvent)
            .where(
                ValveEvent.device_id == device.id,
                ValveEvent.valve_id == valve_id,
                ValveEvent.ts >= since,
            )
            .order_by(ValveEvent.ts)
        ).all()
    return list(events)
