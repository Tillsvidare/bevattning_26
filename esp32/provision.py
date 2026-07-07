# Provisionering: byter kopplingskoden mot MQTT-credentials via molnets
# HTTPS-API (POST /api/provision — samma requests-väg som OTA använder).
#
# Körs som asyncio-task från main.py när config har claim_code men saknar
# mqtt_password. Vid framgång sparas credentials i config.json, koden
# raderas och enheten startar om så att mqtt_link får de nya uppgifterna.
#
# Felhantering (planens vänsäkra flöde):
#   nätverksfel  -> 3 försök med backoff, sedan lokal drift + nytt försök
#                   var 10:e minut (status läsbar i lokala webUI:t)
#   4xx-svar     -> ogiltig/utgången kod: sluta försöka, radera koden och
#                   visa svensk instruktion i webUI:t
#
# CPython-kompatibel för bänktest (machine/reset stubbas då bort).

import asyncio
import time

try:
    import ujson
except ImportError:  # CPython (för test på datorn)
    import json as ujson

import storage

DEFAULT_URL = "https://bevattning.tillsvidare.eu"
HTTP_TIMEOUT_S = 30
RETRY_DELAYS_S = (5, 30)     # backoff mellan de första försöken
RETRY_PERIOD_S = 600         # därefter nytt försök var 10:e minut

# Läses av webui: state = idle | running | done | invalid | waiting
status = {"state": "idle", "message": ""}


def _set_status(state, message):
    status["state"] = state
    status["message"] = message
    print("provision: %s - %s" % (state, message))


def _hw_id():
    try:
        import machine
        import ubinascii
        return ubinascii.hexlify(machine.unique_id()).decode()
    except ImportError:  # CPython-bänktest
        import uuid
        return "%012x" % uuid.getnode()


def _fw_version():
    try:
        with open("manifest.json") as f:
            return ujson.load(f).get("version", "?")
    except (OSError, ValueError):
        return "?"


def _post(url, payload):
    """POST JSON, returnera (statuskod, svar-dict). Kastar OSError vid nätfel."""
    try:
        import requests
    except ImportError:  # äldre firmware
        import urequests as requests
    body = ujson.dumps(payload)
    headers = {"Content-Type": "application/json"}
    try:
        r = requests.post(url, data=body, headers=headers,
                          timeout=HTTP_TIMEOUT_S)
    except TypeError:  # äldre urequests utan timeout-parameter
        r = requests.post(url, data=body, headers=headers)
    try:
        code = r.status_code
        try:
            data = r.json()
        except ValueError:
            data = {}
    finally:
        r.close()
    return code, data


def _apply(cfg, data):
    """Spara credentials från provisioneringssvaret och radera koden."""
    cfg["device_id"] = data["device_id"]
    cfg["mqtt_user"] = data["mqtt_username"]
    cfg["mqtt_password"] = data["mqtt_password"]
    cfg["mqtt_host"] = data["mqtt_host"]
    cfg["mqtt_port"] = int(data["mqtt_port"])
    cfg["mqtt_tls"] = bool(data["mqtt_tls"])
    cfg["claim_code"] = ""
    storage.save_config(cfg)


def provision_once(cfg):
    """Ett provisioneringsförsök. Returnerar True vid framgång.
    Kastar OSError vid nätverksfel; ValueError vid avvisad kod (4xx)."""
    url = (cfg.get("cloud_url") or DEFAULT_URL) + "/api/provision"
    import gc
    gc.collect()  # TLS-handskakningen vill ha sammanhängande heap
    code, data = _post(url, {
        "claim_code": cfg["claim_code"],
        "hw_id": _hw_id(),
        "fw_version": _fw_version(),
    })
    if code == 200 and "device_id" in data:
        _apply(cfg, data)
        return True
    if 400 <= code < 500:
        raise ValueError(data.get("detail", "kod avvisad (HTTP %d)" % code))
    raise OSError("HTTP %d" % code)


def _reset():
    try:
        import machine
        time.sleep(0.3)
        machine.reset()  # återvänder aldrig
    except ImportError:
        pass  # CPython-bänktest: låt anroparen fortsätta


async def provision_task(cfg):
    """Kör tills provisioneringen lyckats eller koden avvisats."""
    attempt = 0
    _set_status("running", "Kopplar enheten till molnkontot ...")
    while True:
        try:
            if provision_once(cfg):
                _set_status("done", "Enheten är kopplad - startar om.")
                _reset()
                return
        except ValueError as e:
            # Ogiltig/utgången kod: sluta försöka. Koden raderas så nästa
            # omstart går direkt till lokal drift; ny kod anges via portalen.
            cfg["claim_code"] = ""
            storage.save_config(cfg)
            _set_status("invalid",
                        "Kopplingskoden avvisades (%s). Skapa en ny kod i "
                        "molntjänsten och ange den i enhetens "
                        "inställningsläge." % e)
            return
        except OSError as e:
            attempt += 1
            delay = (RETRY_DELAYS_S[attempt - 1]
                     if attempt <= len(RETRY_DELAYS_S) else RETRY_PERIOD_S)
            _set_status("waiting",
                        "Kunde inte nå molnet (%s) - nytt försök om %d s."
                        % (e, delay))
            await asyncio.sleep(delay)
            _set_status("running", "Kopplar enheten till molnkontot ...")
