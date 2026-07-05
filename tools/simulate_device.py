"""Fejk-ESP32 för end-to-end-test av molndelen utan hårdvara.

Gör tre saker:
  1. Publicerar retained scheman till valve/{id}/schedule/status.
  2. Publicerar 7 dagars påhittad ON/OFF-historik till valve/{id}/history.
  3. Prenumererar på valve/{id}/schedule/set och ekar tillbaka till /status
     efter 0.5 s — precis som riktiga enheten.

Körning:  pip install paho-mqtt && python tools/simulate_device.py [broker-host]
"""

import json
import random
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt

HOST = sys.argv[1] if len(sys.argv) > 1 else "localhost"
PORT = 1883
VALVES = (1, 2)

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
    client.publish(
        f"valve/{valve_id}/schedule/status",
        json.dumps(schedules[valve_id]),
        qos=1,
        retain=True,
    )
    print(f"-> valve/{valve_id}/schedule/status {schedules[valve_id]}")


def publish_irrigation(client: mqtt.Client) -> None:
    client.publish(
        "bevattning/irrigation/status", json.dumps(irrigation), qos=1, retain=True
    )
    print(f"-> bevattning/irrigation/status {irrigation}")


def publish_sensor(client: mqtt.Client) -> None:
    client.publish(
        "bevattning/sensor/status", json.dumps(sensor), qos=1, retain=True
    )
    print(f"-> bevattning/sensor/status {sensor}")


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
                    f"valve/{valve_id}/history",
                    json.dumps({"ts": iso(ts), "state": state}),
                    qos=1,
                )
    print("-> 7 dagars historik publicerad")


def on_message(client: mqtt.Client, userdata, msg) -> None:
    parts = msg.topic.split("/")
    # Eka bara riktiga /set-kommandon. amqtt-brokern (run_broker.py) har en
    # bugg där retained /status levereras till /set-prenumeranter — utan den
    # här kontrollen ekas gamla scheman tillbaka i en självförstärkande loop.
    if msg.topic == "bevattning/irrigation/set":
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
    # och skriver över de färska scheman som just publicerats.
    client_id = f"simulate-device-{random.randint(0, 0xFFFFFF):06x}"
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    client.on_message = on_message
    client.connect(HOST, PORT)
    for valve_id in VALVES:
        client.subscribe(f"valve/{valve_id}/schedule/set", qos=1)
        publish_status(client, valve_id)
    client.subscribe("bevattning/irrigation/set", qos=1)
    publish_irrigation(client)
    publish_sensor(client)
    publish_history(client)
    print(f"Simulator igång mot {HOST}:{PORT} — Ctrl+C avslutar")
    client.loop_forever()


if __name__ == "__main__":
    main()
