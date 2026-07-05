# Klocksynk och svensk lokaltid för ESP32 (MicroPython).
#
# RTC:n sätts till UTC av ntptime.settime(). All epok-matematik här sker i
# enhetens egen epok (MicroPython räknar från 2000, Unix från 1970) — det
# spelar ingen roll eftersom vi bara jämför värden från samma mktime/localtime.
# Över MQTT skickas ALDRIG råa epoksekunder, bara ISO-strängar (iso_utc()).
#
# DST-regel (EU): CEST gäller från sista söndagen i mars 01:00 UTC till
# sista söndagen i oktober 01:00 UTC. Gränserna är definierade i UTC och
# RTC:n går i UTC, så jämförelsen är exakt utan lokaltids-tvetydigheter.

import time

try:
    import ntptime
except ImportError:  # CPython (för DST-test på datorn)
    ntptime = None

# Schemaläggaren vägrar köra innan klockan är synkad.
synced = False

RETRY_INITIAL_S = 30
RETRY_MAX_S = 300
RESYNC_INTERVAL_S = 24 * 3600


def _mktime(y, mo, d, hh):
    # MicroPython tar 8-tupel, CPython 9-tupel (kör test med TZ=UTC).
    try:
        return time.mktime((y, mo, d, hh, 0, 0, 0, 0))
    except TypeError:
        return time.mktime((y, mo, d, hh, 0, 0, 0, 0, -1))


def last_sunday(year, month):
    """Dag i månaden (mars/oktober har 31 dagar) för sista söndagen."""
    weekday = time.localtime(_mktime(year, month, 31, 12))[6]  # 0=måndag
    return 31 - ((weekday + 1) % 7)


def dst_active(t):
    """True om CEST (sommartid) gäller vid epoktiden t (UTC)."""
    year = time.localtime(t)[0]
    dst_start = _mktime(year, 3, last_sunday(year, 3), 1)   # 01:00 UTC
    dst_end = _mktime(year, 10, last_sunday(year, 10), 1)   # 01:00 UTC
    return dst_start <= t < dst_end


def utc_offset(t):
    """Sekunder att addera till UTC för svensk lokaltid."""
    return 7200 if dst_active(t) else 3600


def now_local():
    """Lokal svensk tid som time.localtime-tupel."""
    t = time.time()
    return time.localtime(t + utc_offset(t))


def iso_utc():
    """UTC nu som ISO-8601-sträng, t.ex. '2026-07-05T04:30:00Z'."""
    y, mo, d, hh, mm, ss = time.gmtime()[:6]
    return "%04d-%02d-%02dT%02d:%02d:%02dZ" % (y, mo, d, hh, mm, ss)


async def sync_task():
    """Synka RTC via NTP med backoff; omsynka dygnsvis mot drift."""
    global synced
    import asyncio

    delay = RETRY_INITIAL_S
    while True:
        try:
            ntptime.settime()
            synced = True
            delay = RETRY_INITIAL_S
            print("clock: NTP-synk OK, UTC=%s" % iso_utc())
            await asyncio.sleep(RESYNC_INTERVAL_S)
        except OSError as e:
            print("clock: NTP misslyckades (%s), nytt försök om %ds" % (e, delay))
            await asyncio.sleep(delay)
            delay = min(delay * 2, RETRY_MAX_S)
