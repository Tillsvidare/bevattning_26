# OTA-uppdatering från GitHub (knappen hålls vid strömpåslag).
#
# Körs av boot.py FÖRE main.py: ansluter till WiFi med config.json,
# hämtar manifest.json från BASE_URL, jämför sha256 per fil med de
# lokala filerna, laddar ned ändrade filer till .tmp och verifierar
# hashen — först när ALLA är verifierade byts filerna ut (rename) och
# enheten startar om i normalläge. Enhetslokala filer (config.json,
# schedule.json, settings.json) finns inte i manifestet och rörs aldrig.
#
# Kastar vid fel; boot.py faller då tillbaka till AP-uppdateringsläget
# (wifi_update.serve()), så enheten kan alltid räddas utan USB-kabel.
#
# Publicering: kör tools/make_manifest.py och pusha till GitHub.

import machine
import network
import os
import time

try:
    import ubinascii as binascii
    import uhashlib as hashlib
except ImportError:  # CPython (för test på datorn)
    import binascii
    import hashlib

try:
    import ujson
except ImportError:
    import json as ujson

# Raw-adressen till esp32-katalogen på main-grenen.
BASE_URL = ("https://raw.githubusercontent.com/"
            "Tillsvidare/bevattning_26/main/esp32/")

WIFI_TIMEOUT_S = 30
HTTP_TIMEOUT_S = 30
CHUNK = 512

# raw.githubusercontent.com CDN-cachear i ~5 min. En slumpad frågesträng
# (ny per uppdateringskörning) gör cachenyckeln unik så enheten alltid får
# färskt innehåll — GitHub ignorerar själva parametern.
_nonce = binascii.hexlify(os.urandom(4)).decode()


def _url(name):
    return BASE_URL + name + "?ota=" + _nonce


def _get(url):
    try:
        import requests
    except ImportError:  # äldre firmware
        import urequests as requests
    try:
        return requests.get(url, timeout=HTTP_TIMEOUT_S)
    except TypeError:  # äldre urequests utan timeout-parameter
        return requests.get(url)


def _connect_wifi():
    with open("config.json") as f:
        cfg = ujson.load(f)
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.connect(cfg["wifi_ssid"], cfg["wifi_password"])
    for _ in range(WIFI_TIMEOUT_S * 2):
        if sta.isconnected():
            print("ota: WiFi uppe, IP=%s" % sta.ifconfig()[0])
            return
        time.sleep(0.5)
    raise OSError("WiFi-anslutning misslyckades")


def _local_sha256(name):
    """Hex-sha256 för en lokal fil, None om den saknas."""
    h = hashlib.sha256()
    try:
        with open(name, "rb") as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                h.update(chunk)
    except OSError:
        return None
    return binascii.hexlify(h.digest()).decode()


def _mkdirs(path):
    """Skapa katalogerna i en filsökväg (t.ex. lib/umqtt/x.py)."""
    parts = path.split("/")[:-1]
    p = ""
    for part in parts:
        p += part
        try:
            os.mkdir(p)
        except OSError:
            pass  # finns redan
        p += "/"


def _download(name, want_sha):
    """Ladda ned en fil till <name>.tmp och verifiera hashen."""
    r = _get(_url(name))
    if r.status_code != 200:
        r.close()
        raise OSError("HTTP %d för %s" % (r.status_code, name))
    h = hashlib.sha256()
    tmp = name + ".tmp"
    _mkdirs(tmp)
    with open(tmp, "wb") as f:
        while True:
            chunk = r.raw.read(CHUNK)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
    r.close()
    if binascii.hexlify(h.digest()).decode() != want_sha:
        os.remove(tmp)
        raise ValueError("hash stämmer inte för %s" % name)


def run():
    """Uppdatera från GitHub och starta om i normalläge. Återvänder aldrig
    vid framgång (machine.reset); kastar vid fel."""
    _connect_wifi()
    print("ota: hämtar %s" % _url("manifest.json"))
    r = _get(_url("manifest.json"))
    if r.status_code != 200:
        r.close()
        raise OSError("HTTP %d för manifestet" % r.status_code)
    manifest = ujson.loads(r.text)
    r.close()

    files = manifest["files"]
    changed = [n for n in sorted(files) if _local_sha256(n) != files[n]]
    print("ota: version %s, %d fil(er) att uppdatera"
          % (manifest.get("version", "?"), len(changed)))

    import gc
    for name in changed:
        gc.collect()  # TLS-handskakningen vill ha sammanhängande heap
        print("ota: laddar ned %s" % name)
        _download(name, files[name])

    # Alla .tmp är verifierade: byt ut allt. Fönstret för strömavbrott är
    # minimalt, och AP-uppdateringsläget finns som räddning.
    for name in changed:
        try:
            os.remove(name)
        except OSError:
            pass
        os.rename(name + ".tmp", name)
    try:
        os.sync()
    except AttributeError:
        pass

    print("ota: klart, startar om i normalläge")
    time.sleep(0.3)
    machine.reset()
