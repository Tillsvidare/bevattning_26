# WiFi STA-anslutning med retry/backoff.

import asyncio
import time

import network

CONNECT_TIMEOUT_S = 20
RETRY_INITIAL_S = 5
RETRY_MAX_S = 120

# Namnet enheten presenterar sig med: DHCP-hostname i routern och mDNS,
# så webbgränssnittet nås som http://bevattning.local utan IP-adress.
HOSTNAME = "bevattning"

try:
    network.hostname(HOSTNAME)
except AttributeError:
    pass  # äldre firmware utan network.hostname(); IP funkar fortfarande

_wlan = network.WLAN(network.STA_IF)


def is_connected():
    return _wlan.isconnected()


def ip():
    return _wlan.ifconfig()[0] if _wlan.isconnected() else None


async def _try_connect(ssid, password):
    _wlan.active(True)
    try:
        # Avbryt ev. pågående internt anslutningsförsök — annars kastar
        # IDF "Wifi Internal State Error" vid nästa connect() (kraschade
        # tidigare hela main när nätet inte fanns vid uppstart).
        _wlan.disconnect()
    except OSError:
        pass
    try:
        _wlan.connect(ssid, password)
    except OSError as e:
        print("net: connect vägrades (%s), släcker radion och försöker om" % e)
        _wlan.active(False)
        await asyncio.sleep(1)
        _wlan.active(True)
        return False
    for _ in range(CONNECT_TIMEOUT_S * 2):
        if _wlan.isconnected():
            print("net: ansluten, IP=%s" % ip())
            return True
        await asyncio.sleep_ms(500)
    return False


async def connect(cfg, timeout_s=None):
    """Blocka tills WiFi är uppe (med backoff mellan försök).

    Med timeout_s: ge upp efter så många sekunder och returnera False —
    main.py startar då inställningsportalen istället för att vänta evigt
    (routerbyte hos en vän). Utan timeout: vänta tills det lyckas."""
    delay = RETRY_INITIAL_S
    start = time.ticks_ms()
    while not await _try_connect(cfg["wifi_ssid"], cfg["wifi_password"]):
        if timeout_s and time.ticks_diff(time.ticks_ms(), start) >= timeout_s * 1000:
            print("net: ingen kontakt på %ds, ger upp" % timeout_s)
            return False
        print("net: anslutning misslyckades, nytt försök om %ds" % delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, RETRY_MAX_S)
    return True


async def monitor_task(cfg):
    """Återanslut om WiFi tappas under drift."""
    while True:
        if not _wlan.isconnected():
            print("net: WiFi tappad, återansluter")
            await connect(cfg)
        await asyncio.sleep(10)
