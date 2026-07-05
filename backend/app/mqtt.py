"""MQTT-integration: lyssnar på historik och schemastatus, publicerar nya scheman.

Topics (en publicerare per topic — förhindrar eko-loopar):
  valve/{id}/history          enhet -> backend   ON/OFF-händelser -> SQLite
  valve/{id}/schedule/status  enhet -> backend   retained; cachas i minnet
  valve/{id}/schedule/set     backend -> enhet   ej retained (kommandon får
                                                 inte spelas upp vid reconnect)
"""

import asyncio
import json
import logging
from datetime import datetime

import aiomqtt

from .config import MQTT_HOST, MQTT_PORT
from .database import SessionLocal
from .models import ValveEvent

log = logging.getLogger("irrigation.mqtt")

RECONNECT_DELAY_S = 5

# Senast kända schema per ventil (lista av poster), matat av retained /status.
schedules: dict[int, list] = {}

# Huvudbrytarens senast kända läge, matat av retained bevattning/irrigation/status.
# Tom tills enheten rapporterat ({"enabled": bool} när känt).
irrigation: dict = {}

# Vattensensorns senast kända läge, matat av retained bevattning/sensor/status.
# Read-only: inget /set — enheten äger läget ({"wet": bool} när känt).
sensor: dict = {}

# Aktiv klient medan lyssnaren är ansluten (används även för publicering).
_client: aiomqtt.Client | None = None


def is_connected() -> bool:
    return _client is not None


def _parse_ts(value: str) -> datetime:
    """ISO-8601 från enheten, t.ex. '2026-07-05T04:30:00Z' -> naiv UTC."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def _valve_id_from_topic(topic: str) -> int | None:
    # "valve/1/history" -> 1
    parts = topic.split("/")
    try:
        return int(parts[1])
    except (IndexError, ValueError):
        return None


def store_event(valve_id: int, payload: dict) -> None:
    """Spara en ON/OFF-händelse i databasen."""
    state = payload.get("state")
    if state not in ("ON", "OFF"):
        log.warning("valve %d: ogiltig state i history-payload: %r", valve_id, payload)
        return
    try:
        ts = _parse_ts(payload["ts"])
    except (KeyError, ValueError) as e:
        log.warning("valve %d: ogiltig ts i history-payload (%s): %r", valve_id, e, payload)
        return
    with SessionLocal() as session:
        session.add(ValveEvent(valve_id=valve_id, state=state, ts=ts))
        session.commit()
    log.info("valve %d: %s @ %s sparad", valve_id, state, ts)


def update_schedule_cache(valve_id: int, payload) -> None:
    if isinstance(payload, dict):
        payload = [payload]  # äldre firmware: en post istället för lista
    schedules[valve_id] = payload
    log.info("valve %d: schema uppdaterat: %r", valve_id, payload)


def _dispatch(topic: str, raw: bytes) -> None:
    try:
        payload = json.loads(raw)
    except ValueError:
        log.warning("%s: payload är inte JSON: %r", topic, raw[:200])
        return
    if topic == "bevattning/irrigation/status":
        if isinstance(payload, dict) and "enabled" in payload:
            irrigation["enabled"] = bool(payload["enabled"])
            log.info("huvudbrytare: %s", "på" if irrigation["enabled"] else "av")
        return
    if topic == "bevattning/sensor/status":
        if isinstance(payload, dict) and "wet" in payload:
            sensor["wet"] = bool(payload["wet"])
            log.info("vattensensor: %s", "VÅT" if sensor["wet"] else "torr")
        return
    valve_id = _valve_id_from_topic(topic)
    if valve_id is None:
        return
    if topic.endswith("/history"):
        store_event(valve_id, payload)
    elif topic.endswith("/schedule/status"):
        update_schedule_cache(valve_id, payload)


async def mqtt_listener() -> None:
    """Lifespan-task: håller anslutningen uppe och tar emot meddelanden."""
    global _client
    while True:
        try:
            async with aiomqtt.Client(MQTT_HOST, MQTT_PORT) as client:
                _client = client
                await client.subscribe("valve/+/history", qos=1)
                await client.subscribe("valve/+/schedule/status", qos=1)
                await client.subscribe("bevattning/irrigation/status", qos=1)
                await client.subscribe("bevattning/sensor/status", qos=1)
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


async def publish_irrigation_set(enabled: bool) -> None:
    """Skicka nytt huvudbrytarläge till enheten. Ej retained."""
    if _client is None:
        raise ConnectionError("Ej ansluten till MQTT-brokern")
    await _client.publish(
        "bevattning/irrigation/set",
        json.dumps({"enabled": enabled}),
        qos=1,
        retain=False,
    )
    log.info("huvudbrytare publicerad till /set: %s", enabled)


async def publish_schedule_set(valve_id: int, schedule: list) -> None:
    """Skicka ett nytt schema till enheten. Ej retained — se modulens docstring."""
    if _client is None:
        raise ConnectionError("Ej ansluten till MQTT-brokern")
    await _client.publish(
        f"valve/{valve_id}/schedule/set",
        json.dumps(schedule),
        qos=1,
        retain=False,
    )
    log.info("valve %d: schema publicerat till /set: %r", valve_id, schedule)
