"""MQTT-integration: lyssnar på enheternas status, publicerar kommandon.

Alla topics är namespacade per enhet (en publicerare per topic —
förhindrar eko-loopar):
  devices/{id}/valve/{n}/history          enhet -> backend   ON/OFF -> SQLite
  devices/{id}/valve/{n}/schedule/status  enhet -> backend   retained
  devices/{id}/valve/{n}/schedule/set     backend -> enhet   ej retained
  devices/{id}/irrigation/status|set      huvudbrytaren
  devices/{id}/sensor/status              vattensensorn (read-only)
  devices/{id}/availability               "online"/"offline", retained + LWT

Enhetens tillstånd write-through:as till devices-raden vid varje status-
meddelande; API:t läser bara DB. Okända device-id:n ignoreras.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import aiomqtt

from .config import MQTT_HOST, MQTT_PASSWORD, MQTT_PORT, MQTT_USER
from .database import SessionLocal
from .models import Device, ValveEvent

log = logging.getLogger("irrigation.mqtt")

RECONNECT_DELAY_S = 5

TOPIC_FILTERS = (
    "devices/+/valve/+/history",
    "devices/+/valve/+/schedule/status",
    "devices/+/irrigation/status",
    "devices/+/sensor/status",
    "devices/+/availability",
)

# Aktiv klient medan lyssnaren är ansluten (används även för publicering).
_client: aiomqtt.Client | None = None


def is_connected() -> bool:
    return _client is not None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_ts(value: str) -> datetime:
    """ISO-8601 från enheten, t.ex. '2026-07-05T04:30:00Z' -> naiv UTC."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def store_event(session, device_id: str, valve_id: int, payload: dict) -> None:
    """Spara en ON/OFF-händelse i databasen."""
    state = payload.get("state")
    if state not in ("ON", "OFF"):
        log.warning("%s valve %d: ogiltig state i history-payload: %r",
                    device_id, valve_id, payload)
        return
    try:
        ts = _parse_ts(payload["ts"])
    except (KeyError, ValueError) as e:
        log.warning("%s valve %d: ogiltig ts i history-payload (%s): %r",
                    device_id, valve_id, e, payload)
        return
    session.add(ValveEvent(device_id=device_id, valve_id=valve_id, state=state, ts=ts))
    log.info("%s valve %d: %s @ %s sparad", device_id, valve_id, state, ts)


def _update_schedule(device: Device, valve_id: int, payload) -> None:
    if isinstance(payload, dict):
        payload = [payload]  # äldre firmware: en post istället för lista
    schedules = json.loads(device.schedules_json) if device.schedules_json else {}
    schedules[str(valve_id)] = payload
    device.schedules_json = json.dumps(schedules)
    log.info("%s valve %d: schema uppdaterat: %r", device.id, valve_id, payload)


def _dispatch(topic: str, raw: bytes) -> None:
    parts = topic.split("/")
    if len(parts) < 3 or parts[0] != "devices":
        return
    device_id, rest = parts[1], parts[2:]

    with SessionLocal() as session:
        device = session.get(Device, device_id)
        if device is None:
            log.debug("okänt device-id på %s — ignoreras", topic)
            return
        device.last_seen = _utcnow()

        if rest == ["availability"]:
            device.online = raw.strip() == b"online"
            log.info("%s: %s", device_id, "online" if device.online else "offline")
            session.commit()
            return

        try:
            payload = json.loads(raw)
        except ValueError:
            log.warning("%s: payload är inte JSON: %r", topic, raw[:200])
            session.commit()
            return

        if rest == ["irrigation", "status"]:
            if isinstance(payload, dict) and "enabled" in payload:
                device.irrigation_enabled = bool(payload["enabled"])
                log.info("%s: huvudbrytare %s", device_id,
                         "på" if device.irrigation_enabled else "av")
        elif rest == ["sensor", "status"]:
            if isinstance(payload, dict) and "wet" in payload:
                device.sensor_wet = bool(payload["wet"])
                log.info("%s: vattensensor %s", device_id,
                         "VÅT" if device.sensor_wet else "torr")
        elif len(rest) == 3 and rest[0] == "valve" and rest[2] == "history":
            try:
                store_event(session, device_id, int(rest[1]), payload)
            except ValueError:
                pass
        elif len(rest) == 4 and rest[0] == "valve" and rest[2:] == ["schedule", "status"]:
            try:
                _update_schedule(device, int(rest[1]), payload)
            except ValueError:
                pass
        session.commit()


async def mqtt_listener() -> None:
    """Lifespan-task: håller anslutningen uppe och tar emot meddelanden."""
    global _client
    auth = {}
    if MQTT_USER:
        auth = {"username": MQTT_USER, "password": MQTT_PASSWORD}
    while True:
        try:
            async with aiomqtt.Client(MQTT_HOST, MQTT_PORT, **auth) as client:
                _client = client
                for topic_filter in TOPIC_FILTERS:
                    await client.subscribe(topic_filter, qos=1)
                log.info("ansluten till %s:%d", MQTT_HOST, MQTT_PORT)
                async for message in client.messages:
                    try:
                        _dispatch(str(message.topic), bytes(message.payload))
                    except Exception:
                        log.exception("fel vid hantering av %s", message.topic)
        except aiomqtt.MqttError as e:
            log.warning("MQTT-fel: %s — återansluter om %ds", e, RECONNECT_DELAY_S)
        except asyncio.CancelledError:
            raise
        finally:
            _client = None
        await asyncio.sleep(RECONNECT_DELAY_S)


async def publish_irrigation_set(device_id: str, enabled: bool) -> None:
    """Skicka nytt huvudbrytarläge till enheten. Ej retained."""
    if _client is None:
        raise ConnectionError("Ej ansluten till MQTT-brokern")
    await _client.publish(
        f"devices/{device_id}/irrigation/set",
        json.dumps({"enabled": enabled}),
        qos=1,
        retain=False,
    )
    log.info("%s: huvudbrytare publicerad till /set: %s", device_id, enabled)


async def publish_schedule_set(device_id: str, valve_id: int, schedule: list) -> None:
    """Skicka ett nytt schema till enheten. Ej retained — se modulens docstring."""
    if _client is None:
        raise ConnectionError("Ej ansluten till MQTT-brokern")
    await _client.publish(
        f"devices/{device_id}/valve/{valve_id}/schedule/set",
        json.dumps(schedule),
        qos=1,
        retain=False,
    )
    log.info("%s valve %d: schema publicerat till /set: %r", device_id, valve_id, schedule)
