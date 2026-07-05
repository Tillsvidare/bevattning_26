"""Provkör ESP32:ans lokala webbgränssnitt på datorn (utan hårdvara).

Kör riktiga esp32/webui.py med vendorerade microdot och riktiga
esp32/storage.py — bara MQTT-länken är fejkad (toggeln låtsas ansluta
direkt). schedule.json/settings.json skrivs i .webui_dev/ i projektroten,
precis som på enhetens flash.

    python tools/run_webui_dev.py     ->  http://127.0.0.1:8080
"""

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "esp32"))
sys.path.insert(0, os.path.join(ROOT, "esp32", "lib"))

# Egen "flash" så esp32/schedule.json (defaultfilen) inte skrivs över.
STATE_DIR = os.path.join(ROOT, ".webui_dev")
os.makedirs(STATE_DIR, exist_ok=True)
os.chdir(STATE_DIR)

import storage  # noqa: E402
import webui  # noqa: E402

schedule = storage.load_schedule()
settings = storage.load_settings()


def apply_entries(valve_id, entries):
    return storage.update_valve(schedule, valve_id, entries)


async def publish_schedule(valve_id):
    print("[fejk-mqtt] skulle publicera /status för ventil %s: %s"
          % (valve_id, schedule[str(valve_id)]))


def get_cloud():
    # Låtsas att anslutningen följer toggeln direkt.
    return {"enabled": settings["cloud_enabled"],
            "connected": settings["cloud_enabled"]}


def set_cloud(enabled):
    settings["cloud_enabled"] = bool(enabled)
    storage.save_settings(settings)
    print("[fejk-mqtt] molnsynk %s" % ("på" if enabled else "av"))


def get_irrigation():
    return settings["irrigation_enabled"]


def set_irrigation(enabled):
    settings["irrigation_enabled"] = bool(enabled)
    storage.save_settings(settings)
    print("[fejk-mqtt] bevattning %s" % ("på" if enabled else "AVSTÄNGD"))


async def publish_irrigation():
    print("[fejk-mqtt] skulle publicera irrigation/status: %s"
          % settings["irrigation_enabled"])


# Sätt True för att demo:a varningsläget "VÅT — bevattning stoppad".
fake_sensor = {"wet": False}


webui.init(lambda: schedule, apply_entries, publish_schedule,
           get_cloud, set_cloud,
           get_irrigation, set_irrigation, publish_irrigation,
           lambda: fake_sensor["wet"])

# HOST=0.0.0.0 exponerar servern på LAN (t.ex. för test i mobilen).
HOST = os.environ.get("HOST", "127.0.0.1")
print("Enhetens lokala gränssnitt: http://%s:8080  (Ctrl+C avslutar)" % HOST)
print("Tillstånd sparas i %s" % STATE_DIR)
asyncio.run(webui.app.start_server(host=HOST, port=8080))
