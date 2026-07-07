# Bevattningskontroller - MicroPython för ESP32-S3.
#
# Uppstartsordning (viktig):
#   1. Ladda config + schema.
#   2. Pulsa båda ventilerna STÄNGDA (känt läge) FÖRE WDT-init —
#      6 s-pulsen skulle annars äta upp watchdog-marginalen.
#   3. WiFi -> starta tasks: klocksynk, MQTT, schemaläggare, webbserver,
#      LED-status, watchdog.
#
# WiFi-uppdateringsläge (knappen hålls vid uppstart) hanteras i boot.py,
# som kör wifi_update.serve() innan den här filen någonsin startar.
# Saknas giltig config.json startar i stället wifi_setup.serve() en
# accesspunkt med ett inställningsformulär (se nedan).

import asyncio
import time

from machine import WDT

import clock
import hw
import mqtt_link
import net
import provision
import scheduler
import storage
import watersensor
import webui

WATCHDOG_TIMEOUT_MS = 30000
# Utan WiFi-kontakt så här länge vid uppstart öppnas inställningsportalen.
WIFI_PORTAL_AFTER_S = 180
# MQTT-tasken får generöst fönster: en blockerande reconnect kan ta sekunder.
# Sensor-pollen grindar ventilstyrningen och WDT-övervakas därför också.
HEARTBEAT_TIMEOUT_MS = {"mqtt": 15000, "scheduler": 15000, "sensor": 15000}

print("Startar bevattningskontroller (OTA-test 2026-07-06)")

# --- Konfiguration, schema och inställningar (flash) ---

try:
    config = storage.load_config()
except (OSError, ValueError) as e:
    # Ingen giltig config: starta AP + formulär i webbläsaren i stället
    # för att krascha. serve() sparar config.json och startar om enheten.
    print("Ingen giltig config.json (%s): startar WiFi-inställningsläge" % e)
    import wifi_setup
    wifi_setup.serve()  # återvänder aldrig
schedule = storage.load_schedule()
settings = storage.load_settings()


def get_schedule():
    return schedule


def apply_entries(valve_id, entries):
    """Gemensam väg för webui och MQTT /set: validera -> spara på flash."""
    return storage.update_valve(schedule, valve_id, entries)


# --- Watchdog med heartbeats (provpump-mönstret) ---

wdt = None
heartbeats = {"mqtt": 0, "scheduler": 0, "sensor": 0}


def make_heartbeat(name):
    def beat():
        heartbeats[name] = time.ticks_ms()
    return beat


def all_tasks_alive():
    now = time.ticks_ms()
    for name, last in heartbeats.items():
        if time.ticks_diff(now, last) > HEARTBEAT_TIMEOUT_MS[name]:
            print("varning: %s svarar inte (%d ms sedan)"
                  % (name, time.ticks_diff(now, last)))
            return False
    return True


async def watchdog_task():
    while True:
        if wdt and all_tasks_alive():
            wdt.feed()
        await asyncio.sleep(1)


# --- Statuslampor: grönt = allt uppe, rött = fel/inte redo ---

async def led_task(mqtt):
    while True:
        # I lokal drift (molnsynk av) krävs ingen MQTT-anslutning för grönt.
        mqtt_ok = mqtt.connected or not mqtt.enabled
        ok = net.is_connected() and mqtt_ok and clock.synced
        hw.green_led.value(1 if ok else 0)
        hw.red_led.value(0 if ok else 1)
        await asyncio.sleep(1)


async def main():
    global wdt

    # Känt läge före allt annat (och före WDT).
    print("Uppstart: stänger båda ventilerna")
    hw.close_all_blocking()

    # WiFi först: WDT kan inte startas här, för net.connect kan vänta
    # godtyckligt länge (egen backoff) och heartbeats skulle åldras —
    # enheten skulle boot-loopa varje gång nätet är nere.
    #
    # Ingen kontakt inom fönstret = nätet är troligen bytt/borta: öppna
    # inställningsportalen (molnkopplingen bevaras; tom kod = WiFi-byte).
    # Portalen har egen inaktivitets-timeout -> reset -> nytt försök här,
    # så en router som bara var omstartad läker sig själv.
    if not await net.connect(config, timeout_s=WIFI_PORTAL_AFTER_S):
        print("Ingen WiFi-kontakt på %ds: startar inställningsportalen"
              % WIFI_PORTAL_AFTER_S)
        import wifi_setup
        wifi_setup.serve(existing=config)  # återvänder aldrig
    asyncio.create_task(net.monitor_task(config))

    wdt = WDT(timeout=WATCHDOG_TIMEOUT_MS)
    for name in heartbeats:
        heartbeats[name] = time.ticks_ms()
    asyncio.create_task(watchdog_task())

    # Kopplingskod utan credentials: byt koden mot MQTT-uppgifter hos
    # molnet (provision.py). Vid framgång sparas de i config.json och
    # enheten startar om; tills dess kör allt annat vidare i lokal drift.
    if config["claim_code"] and not config["mqtt_password"]:
        asyncio.create_task(provision.provision_task(config))

    def get_irrigation():
        return settings["irrigation_enabled"]

    def apply_irrigation(enabled):
        """Huvudbrytaren: spara på flash. Schemaläggaren och pågående
        körningar läser flaggan direkt via get_irrigation."""
        settings["irrigation_enabled"] = bool(enabled)
        storage.save_settings(settings)
        print("bevattning: %s" % ("på" if enabled else "AVSTÄNGD"))

    def can_run():
        """Kombinerad grind: huvudbrytaren PÅ och sensorn torr. Falsk grind
        avbryter pågående körning och blockerar nya starter — utan
        ikapp-körning när den öppnar igen (missad startminut matchar inte)."""
        return settings["irrigation_enabled"] and not watersensor.is_wet()

    mqtt = mqtt_link.MqttLink(config, get_schedule, apply_entries,
                              get_irrigation, apply_irrigation,
                              get_sensor=watersensor.is_wet)
    # Tom mqtt_host = endast lokal drift: molnsynken hålls av oavsett
    # sparad inställning (annars skulle loop() försöka ansluta till "").
    mqtt.enabled = settings["cloud_enabled"] and bool(config["mqtt_host"])
    valves = {
        vid: scheduler.ValveController(vid, mqtt, make_heartbeat("scheduler"),
                                       should_run=can_run)
        for vid in (1, 2)
    }

    def get_cloud():
        return {"enabled": mqtt.enabled, "connected": mqtt.connected}

    def get_device():
        """Moln-identitet + provisioneringsstatus för lokala webUI:t."""
        return {
            "device_id": config["device_id"],
            "claiming": bool(config["claim_code"]),
            "provision": provision.status,
        }

    def set_cloud(enabled):
        """Toggle från webbgränssnittet: spara på flash + koppla upp/ner."""
        if enabled and not config["mqtt_host"]:
            # webui svarar med get_cloud() efteråt, så kryssrutan hoppar
            # tillbaka till av när togglen vägras.
            print("mqtt: ingen broker konfigurerad, molnsynk kan inte slås på")
            return
        settings["cloud_enabled"] = bool(enabled)
        storage.save_settings(settings)
        mqtt.set_enabled(enabled)

    webui.init(get_schedule, apply_entries, mqtt.publish_schedule,
               get_cloud, set_cloud,
               get_irrigation, apply_irrigation, mqtt.publish_irrigation,
               watersensor.is_wet, get_device)

    asyncio.create_task(clock.sync_task())
    asyncio.create_task(mqtt.loop(make_heartbeat("mqtt")))
    asyncio.create_task(
        scheduler.scheduler_task(valves, get_schedule, make_heartbeat("scheduler"),
                                 irrigation_enabled=can_run)
    )
    asyncio.create_task(
        watersensor.poll_task(mqtt.publish_sensor, make_heartbeat("sensor"))
    )
    asyncio.create_task(led_task(mqtt))

    print("Webbserver på http://%s/ (http://%s.local/)"
          % (net.ip(), net.HOSTNAME))
    await webui.app.start_server(host="0.0.0.0", port=80)


try:
    asyncio.run(main())
except Exception as e:
    print("Fatalt fel: %s" % e)
    raise
