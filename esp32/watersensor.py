# Vattensensor med hysteres: våt sensor stoppar bevattningen.
#
# Sensorn läses varje sekund. Flanken byts snabbt in i vått (2 våta poll
# i rad) men långsamt ut (30 sammanhängande torra poll), så tillståndet
# inte fladdrar av droppar eller vågskvalp. Vått läge grindas in i
# schemaläggningen via main.can_run() — samma mekanism som huvudbrytaren:
# pågående körning avbryts och inga nya startar, utan ikapp-körning när
# det torkar upp.

import asyncio

import hw

POLL_INTERVAL_S = 1
WET_POLLS = 2    # snabbt in i vått
DRY_POLLS = 30   # långsamt ut ur vått

wet = False


def is_wet():
    return wet


async def poll_task(on_change=None, heartbeat=None):
    """Polla sensorn; vid flank: logga och await on_change() (MQTT-publicering)."""
    global wet
    streak = 0  # på varandra följande poll som motsäger nuvarande läge
    while True:
        if heartbeat:
            heartbeat()
        if hw.read_water() != wet:
            streak += 1
        else:
            streak = 0
        if streak >= (WET_POLLS if not wet else DRY_POLLS):
            wet = not wet
            streak = 0
            print("vattensensor: %s"
                  % ("VÅT — bevattning stoppas" if wet else "torr igen"))
            if on_change:
                await on_change()
        await asyncio.sleep(POLL_INTERVAL_S)
