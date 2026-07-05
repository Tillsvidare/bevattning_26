# Ventilstyrning och daglig schemaläggning.
#
# ValveController serialiserar all aktuering per ventil med ett asyncio.Lock,
# vilket strukturellt garanterar att öppna- och stäng-pulsen aldrig kan vara
# aktiva samtidigt på samma motor. Varje lägesändring publiceras som en
# historik-händelse (ON/OFF med tidsstämpel från den synkade klockan).

import asyncio
import time

import clock
import hw

POLL_INTERVAL_S = 20


class ValveController:
    def __init__(self, valve_id, mqtt, heartbeat=None, should_run=None):
        self.valve_id = valve_id
        self._open_pin, self._close_pin = hw.MOTORS[valve_id]
        self._mqtt = mqtt
        self._heartbeat = heartbeat
        # should_run() -> False avbryter en pågående bevattning i förtid
        # (huvudbrytaren "bevattning av" eller våt vattensensor).
        self._should_run = should_run
        self._lock = asyncio.Lock()
        self.is_open = False
        self.busy = False  # en pågående irrigate-cykel; nya starter hoppas över

    async def open(self):
        async with self._lock:
            if self.is_open:
                return
            print("valve %d: öppnar" % self.valve_id)
            await hw.pulse(self._open_pin, self._heartbeat)
            self.is_open = True
            self._mqtt.publish_history(self.valve_id, "ON")

    async def close(self):
        async with self._lock:
            if not self.is_open:
                return
            print("valve %d: stänger" % self.valve_id)
            await hw.pulse(self._close_pin, self._heartbeat)
            self.is_open = False
            self._mqtt.publish_history(self.valve_id, "OFF")

    async def irrigate(self, minutes):
        """Öppna, vattna i `minutes`, stäng — stängning garanterad."""
        if self.busy:
            print("valve %d: bevattning pågår redan, hoppar över" % self.valve_id)
            return
        self.busy = True
        print("valve %d: bevattning %d min" % (self.valve_id, minutes))
        try:
            await self.open()
            try:
                end = time.ticks_add(time.ticks_ms(), minutes * 60 * 1000)
                while time.ticks_diff(end, time.ticks_ms()) > 0:
                    if self._heartbeat:
                        self._heartbeat()
                    if self._should_run and not self._should_run():
                        print("valve %d: stoppad (huvudbrytare av eller våt "
                              "sensor), avbryter" % self.valve_id)
                        break
                    await asyncio.sleep_ms(500)
            finally:
                await self.close()
        finally:
            self.busy = False
        print("valve %d: bevattning klar" % self.valve_id)


async def scheduler_task(valves, get_schedule, heartbeat=None, irrigation_enabled=None):
    """Kolla var 20:e sekund om någon bevattning ska starta.

    Varje ventil har en lista av poster (flera bevattningar per dygn).
    Triggern är att lokal HH:MM matchar postens start och att just den
    starttiden inte redan körts idag (`ran`) — exakt en körning per post
    och dag oavsett loop-jitter. Kör aldrig innan klockan är NTP-synkad.
    Om en körning fortfarande pågår när nästa starttid slår in hoppas den
    nya över (ValveController.busy).
    """
    ran = {}  # (valve_id, "HH:MM") -> (y, mo, d) senast körd

    while True:
        if heartbeat:
            heartbeat()
        master_on = irrigation_enabled() if irrigation_enabled else True
        if clock.synced and master_on:
            y, mo, d, hh, mm = clock.now_local()[:5]
            now_hhmm = "%02d:%02d" % (hh, mm)
            today = (y, mo, d)
            schedule = get_schedule()
            for valve_id, valve in valves.items():
                for entry in schedule.get(str(valve_id), []):
                    key = (valve_id, entry["start"])
                    if (
                        entry["enabled"]
                        and entry["start"] == now_hhmm
                        and ran.get(key) != today
                    ):
                        ran[key] = today
                        asyncio.create_task(valve.irrigate(entry["duration_min"]))
        # Pollintervallet (20 s) är längre än watchdogens heartbeat-timeout
        # (15 s i main.py) — sov i 5 s-steg och slå heartbeaten mellan dem.
        for _ in range(POLL_INTERVAL_S // 5):
            await asyncio.sleep(5)
            if heartbeat:
                heartbeat()
