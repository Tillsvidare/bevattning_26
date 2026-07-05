# WiFi STA-anslutning med retry/backoff.

import asyncio

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
    _wlan.connect(ssid, password)
    for _ in range(CONNECT_TIMEOUT_S * 2):
        if _wlan.isconnected():
            print("net: ansluten, IP=%s" % ip())
            return True
        await asyncio.sleep_ms(500)
    return False


async def connect(cfg):
    """Blocka tills WiFi är uppe (med backoff mellan försök)."""
    delay = RETRY_INITIAL_S
    while not await _try_connect(cfg["wifi_ssid"], cfg["wifi_password"]):
        print("net: anslutning misslyckades, nytt försök om %ds" % delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, RETRY_MAX_S)


async def monitor_task(cfg):
    """Återanslut om WiFi tappas under drift."""
    while True:
        if not _wlan.isconnected():
            print("net: WiFi tappad, återansluter")
            await connect(cfg)
        await asyncio.sleep(10)
