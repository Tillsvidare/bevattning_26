# Knappen (GPIO 5) i normal drift.
#
# Kort tryck (0,1-3 s) togglar huvudbrytaren — samma sekvens som webui:ts
# POST /api/irrigation (spara + eka till molnet). Långt tryck (>= 3 s,
# summern piper vid tröskeln som "släpp nu"-signal) startar 10 minuters
# manuell bevattning på båda ventilerna, eller stoppar en pågående körning
# (via ValveController.cancel). Kräver samma grind som schemat (can_run):
# huvudbrytare PÅ och torr sensor — annars dubbelpip = vägrat.
#
# Vid strömpåslag betyder knappen fortfarande uppdateringsläge; boot.py
# läser den bara under de första ~500 ms och krockar inte med den här
# tasken. Avgörandet kort/långt sker på släpp (hålltiden mäts), och
# 50 ms-pollen ger debounce på köpet.

import asyncio
import time

import hw

LONG_PRESS_MS = 3000
SHORT_MIN_MS = 100   # kortare = studs/nuddning, ignoreras
MANUAL_MINUTES = 10
POLL_MS = 50

# Sätts av main.py — samma injektionsstil som webui.init.
_deps = {}


def init(valves, get_irrigation, apply_irrigation, publish_irrigation, can_run):
    """valves: {id: ValveController}; get/apply_irrigation är huvudbrytaren;
    publish_irrigation ekar läget till molnet; can_run är schemats grind."""
    _deps["valves"] = list(valves.values())
    _deps["get_irrigation"] = get_irrigation
    _deps["apply_irrigation"] = apply_irrigation
    _deps["publish_irrigation"] = publish_irrigation
    _deps["can_run"] = can_run


async def _short_press():
    """Toggla huvudbrytaren, med pip som kvittens (LED:n byter till/från
    orange via led_task inom en sekund)."""
    enabled = not _deps["get_irrigation"]()
    print("knapp: bevattning %s" % ("på" if enabled else "AV"))
    _deps["apply_irrigation"](enabled)
    await _deps["publish_irrigation"]()
    await hw.beep()


async def _long_press():
    valves = _deps["valves"]
    if any(v.busy for v in valves):
        print("knapp: stoppar pågående bevattning")
        for v in valves:
            v.cancel = True
        await hw.beep()
    elif _deps["can_run"]():
        print("knapp: manuell bevattning %d min" % MANUAL_MINUTES)
        for v in valves:
            asyncio.create_task(v.irrigate(MANUAL_MINUTES))
    else:
        # Huvudbrytare av eller våt sensor: dubbelpip = vägrat.
        print("knapp: manuell bevattning vägrad (huvudbrytare av/våt sensor)")
        await hw.beep(80)
        await asyncio.sleep_ms(120)
        await hw.beep(80)


async def button_task():
    while True:
        if hw.button.value() == 0:  # nedtryckt (aktiv låg)
            start = time.ticks_ms()
            beeped = False
            while hw.button.value() == 0:
                if (not beeped and time.ticks_diff(time.ticks_ms(), start)
                        >= LONG_PRESS_MS):
                    beeped = True
                    await hw.beep()  # tröskel nådd: släpp nu = långtryck
                await asyncio.sleep_ms(POLL_MS)
            held = time.ticks_diff(time.ticks_ms(), start)
            if held >= LONG_PRESS_MS:
                await _long_press()
            elif held >= SHORT_MIN_MS:
                await _short_press()
        await asyncio.sleep_ms(POLL_MS)
