"""Enhetsprovisionering: enheten byter en claim-kod mot MQTT-credentials.

Ingen session — claim-koden är själva credentialen (engångs, 24 h).
Flödet (planens "vänsäkra" variant): användaren skapar koden i webUI:t,
knappar in den i enhetens AP-portal, enheten POSTar hit efter WiFi.
"""

import asyncio
import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import delete, select

from . import mosq_passwd
from .config import MOSQ_PASSWD_FILE, MQTT_PUBLIC_HOST, MQTT_PUBLIC_PORT, MQTT_PUBLIC_TLS
from .database import SessionLocal
from .models import ClaimCode, Device, ValveEvent
from .schemas import ProvisionRequest, ProvisionResponse

log = logging.getLogger("irrigation.provision")

router = APIRouter(prefix="/api")

# Tid för mosquitto-watcherns SIGHUP-cykel innan enheten får sina credentials
# (annars kan första anslutningen avvisas — se planens risklista).
PASSWD_RELOAD_WAIT_S = 4


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _new_device_id(session) -> str:
    while True:
        device_id = "bv-" + secrets.token_hex(4)
        if session.get(Device, device_id) is None:
            return device_id


@router.post("/provision", response_model=ProvisionResponse)
async def provision(body: ProvisionRequest) -> ProvisionResponse:
    code = body.claim_code.strip().upper()
    hw_id = body.hw_id.strip().lower()
    password = secrets.token_urlsafe(16)
    password_hash = mosq_passwd.mosq_hash(password)

    with SessionLocal() as session:
        claim = session.scalar(select(ClaimCode).where(ClaimCode.code == code))
        if claim is None or claim.used_at is not None:
            raise HTTPException(status_code=400, detail="Ogiltig kopplingskod")
        if claim.expires_at < _utcnow():
            raise HTTPException(status_code=400, detail="Kopplingskoden har gått ut")

        existing = session.scalar(select(Device).where(Device.hw_id == hw_id))
        if existing is not None:
            # Om-provisionering. Fysisk åtkomst vinner: byter enheten ägare
            # rensas gamla ägarens historik för den.
            if existing.user_id != claim.user_id:
                session.execute(
                    delete(ValveEvent).where(ValveEvent.device_id == existing.id)
                )
                existing.user_id = claim.user_id
                existing.online = False
                existing.irrigation_enabled = None
                existing.sensor_wet = None
                existing.schedules_json = None
                log.info("enhet %s flyttad till användare %d", existing.id, claim.user_id)
            existing.mqtt_password_hash = password_hash
            device = existing
        else:
            device = Device(
                id=_new_device_id(session),
                user_id=claim.user_id,
                hw_id=hw_id,
                mqtt_password_hash=password_hash,
            )
            session.add(device)

        claim.used_at = _utcnow()
        claim.device_id = device.id
        session.commit()
        device_id = device.id

    mosq_passwd.write_passwd_file()
    if MOSQ_PASSWD_FILE:
        await asyncio.sleep(PASSWD_RELOAD_WAIT_S)  # låt watchern SIGHUP:a mosquitto

    log.info("enhet %s provisionerad (fw %s)", device_id, body.fw_version or "?")
    return ProvisionResponse(
        device_id=device_id,
        mqtt_username=device_id,
        mqtt_password=password,
        mqtt_host=MQTT_PUBLIC_HOST,
        mqtt_port=MQTT_PUBLIC_PORT,
        mqtt_tls=MQTT_PUBLIC_TLS,
    )
