"""Fejk-ESP32 för end-to-end-test av molndelen utan hårdvara.

Gör fyra saker:
  1. Publicerar availability=online (retained, LWT=offline) för enheten.
  2. Publicerar retained scheman till devices/{id}/valve/{n}/schedule/status.
  3. Publicerar 7 dagars påhittad ON/OFF-historik till .../valve/{n}/history.
  4. Prenumererar på .../schedule/set + .../irrigation/set och ekar tillbaka
     till /status efter 0.5 s — precis som riktiga enheten.

Körning:  pip install paho-mqtt && python tools/simulate_device.py [broker-host]
Mot VPS:  python tools/simulate_device.py bevattning.tillsvidare.eu \
              --port 8883 --tls --device-id bv-xxxxxxxx --user bv-xxxxxxxx --password ...
"""

import argparse
import json
import random
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("host", nargs="?", default="localhost")
parser.add_argument("--port", type=int, default=1883)
parser.add_argument("--device-id", default="bv-dev001")
parser.add_argument("--user", default=None, help="MQTT-username (default: anonym)")
parser.add_argument("--password", default=None)
parser.add_argument("--tls", action="store_true",
                    help="TLS utan certvalidering (som enhetens MicroPython)")
args = parser.parse_args()

DEVICE_ID = args.device_id
VALVES = (1, 2)


def t(suffix: str) -> str:
    """Namespaca ett topic under devices/{id}/."""
    return f"devices/{DEVICE_ID}/{suffix}"


# Lista av bevattningar per ventil (flera per dygn stöds, max 6)
schedules = {
    1: [
        {"start": "06:30", "duration_min": 15, "enabled": True},
        {"start": "18:30", "duration_min": 10, "enabled": True},
    ],
    2: [
        {"start": "20:00", "duration_min": 10, "enabled": True},
    ],
}

irrigation = {"enabled": True}  # huvudbrytaren
sensor = {"wet": False}         # vattensensorn (read-only, inget /set)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def publish_status(client: mqtt.Client, valve_id: int) -> None:
    topic = t(f"valve/{valve_id}/schedule/status")
    client.publish(topic, json.dumps(schedules[valve_id]), qos=1, retain=True)
    print(f"-> {topic} {schedules[valve_id]}")


def publish_irrigation(client: mqtt.Client) -> None:
    client.publish(t("irrigation/status"), json.dumps(irrigation), qos=1, retain=True)
    print(f"-> {t('irrigation/status')} {irrigation}")


def publish_sensor(client: mqtt.Client) -> None:
    client.publish(t("sensor/status"), json.dumps(sensor), qos=1, retain=True)
    print(f"-> {t('sensor/status')} {sensor}")


def publish_history(client: mqtt.Client) -> None:
    """7 dagars rimlig historik: morgonkörning ventil 1, kvällskörning ventil 2."""
    now = datetime.now(timezone.utc)
    rng = random.Random(26)
    for days_ago in range(7, 0, -1):
        day = now - timedelta(days=days_ago)
        runs = [
            (1, day.replace(hour=4, minute=30, second=0), 15),   # ~06:30 lokalt
            (2, day.replace(hour=18, minute=0, second=0), 10),   # ~20:00 lokalt
        ]
        # lite extra körningar då och då
        if rng.random() < 0.4:
            runs.append((rng.choice(VALVES), day.replace(hour=11, minute=15), 5))
        for valve_id, start, minutes in runs:
            start += timedelta(minutes=rng.randint(-5, 5))
            end = start + timedelta(minutes=minutes)
            for ts, state in ((start, "ON"), (end, "OFF")):
                client.publish(
                    t(f"valve/{valve_id}/history"),
                    json.dumps({"ts": iso(ts), "state": state}),
                    qos=1,
                )
    print("-> 7 dagars historik publicerad")


def on_message(client: mqtt.Client, userdata, msg) -> None:
    # devices/{id}/... -> delarna efter prefixet
    parts = msg.topic.split("/")[2:]
    # Eka bara riktiga /set-kommandon. amqtt-brokern (run_broker.py) har en
    # bugg där retained /status levereras till /set-prenumeranter — utan den
    # här kontrollen ekas gamla scheman tillbaka i en självförstärkande loop.
    if parts == ["irrigation", "set"]:
        payload = json.loads(msg.payload)
        print(f"<- {msg.topic} {payload}")

        def echo_irrigation() -> None:
            time.sleep(0.5)
            irrigation["enabled"] = bool(payload["enabled"])
            publish_irrigation(client)

        threading.Thread(target=echo_irrigation, daemon=True).start()
        return
    if len(parts) != 4 or parts[0] != "valve" or parts[3] != "set":
        return
    valve_id = int(parts[1])
    payload = json.loads(msg.payload)
    print(f"<- {msg.topic} {payload}")

    def echo() -> None:
        time.sleep(0.5)
        schedules[valve_id] = payload
        publish_status(client, valve_id)

    threading.Thread(target=echo, daemon=True).start()


def main() -> None:
    # Unikt klient-id per körning: med ett återanvänt id kan brokern leverera
    # om gamla /set-meddelanden från förra sessionen, som då ekas till /status
    # och skriver över de färska scheman som just publicerats. (Riktiga
    # enheten använder device_id som client-id; simulatorn startas om oftare.)
    client_id = f"simulate-{DEVICE_ID}-{random.randint(0, 0xFFFFFF):06x}"
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    if args.user:
        client.username_pw_set(args.user, args.password)
    if args.tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # som enhetens MicroPython
        client.tls_set_context(ctx)
    client.on_message = on_message
    client.will_set(t("availability"), "offline", qos=1, retain=True)
    client.connect(args.host, args.port)
    client.publish(t("availability"), "online", qos=1, retain=True)
    for valve_id in VALVES:
        client.subscribe(t(f"valve/{valve_id}/schedule/set"), qos=1)
        publish_status(client, valve_id)
    client.subscribe(t("irrigation/set"), qos=1)
    publish_irrigation(client)
    publish_sensor(client)
    publish_history(client)
    print(f"Simulator {DEVICE_ID} igång mot {args.host}:{args.port} — Ctrl+C avslutar")
    client.loop_forever()


if __name__ == "__main__":
    main()
