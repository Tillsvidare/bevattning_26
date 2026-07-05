"""Lokal MQTT-broker i ren Python — ersätter Mosquitto vid utveckling
på en maskin utan Docker.

    pip install amqtt
    python tools/run_broker.py

OBS: ingen persistens — retained meddelanden försvinner vid omstart
(till skillnad från Mosquitto-containern). Endast för utveckling.
"""

import asyncio
import logging

from amqtt.broker import Broker

logging.basicConfig(level=logging.INFO)

config = {
    "listeners": {"default": {"type": "tcp", "bind": "127.0.0.1:1883"}},
    "auth": {"allow-anonymous": True},
    "topic-check": {"enabled": False},
    "sys_interval": 0,  # sys-pluginen kraschar annars med None > int
}


async def main():
    broker = Broker(config)
    await broker.start()
    print("MQTT-broker igång på 127.0.0.1:1883 — Ctrl+C avslutar", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
