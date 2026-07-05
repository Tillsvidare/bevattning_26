# WiFi-inställningsläge: accesspunkt + formulär i webbläsaren.
#
# Startas av main.py när config.json saknas eller är ogiltig. Enheten
# startar samma accesspunkt som uppdateringsläget ("bevattning") och
# serverar ett formulär där WiFi-uppgifter och MQTT-broker fylls i.
# Tack vare captive portal-stödet i wifi_update.py öppnas sidan
# automatiskt när telefonen ansluter till nätet. När en giltig config
# sparats startar enheten om i normalläge.

import network
import os
import time

try:
    import ujson
except ImportError:  # CPython (för test på datorn)
    import json as ujson

from wifi_update import (PORTAL_NAMES, _Reader, _send, _send_text,
                         read_request, redirect_portal, serve_forever)

CONFIG_FILE = "config.json"

_PAGE = """<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>bevattning - inställningar</title>
<style>
body { font-family: sans-serif; margin: 1em; max-width: 30em; }
h1 { font-size: 1.3em; }
label { display: block; margin: 0.8em 0 0.2em; }
input { width: 100%; padding: 0.5em; box-sizing: border-box; }
button { padding: 0.6em 1.2em; margin-top: 1.2em; }
button.net { display: block; width: 100%; text-align: left;
             margin: 0.2em 0 0; padding: 0.5em; }
#nets p { color: #666; font-size: 0.85em; margin: 0.3em 0; }
footer { margin-top: 2em; color: #666; font-size: 0.85em; }
</style>
</head>
<body>
<h1>bevattning &mdash; inst&auml;llningar</h1>
<form method="post" action="/">
<label>WiFi-n&auml;tverk (SSID)</label>
<input name="ssid" id="ssid" required>
<div id="nets">
{options}
</div>
<label>WiFi-l&ouml;senord (tomt f&ouml;r &ouml;ppet n&auml;t)</label>
<input name="password" type="password">
<label>MQTT-broker (tomt = endast lokal drift, ingen molnsynk)</label>
<input name="mqtt_host">
<label>MQTT-port</label>
<input name="mqtt_port" type="number" value="1883" min="1" max="65535">
<button>Spara och starta om</button>
</form>
<footer>Enheten startar om i normall&auml;ge n&auml;r inst&auml;llningarna
sparats. Beh&ouml;ver du &auml;ndra senare: h&aring;ll knappen vid
str&ouml;mp&aring;slag f&ouml;r uppdateringsl&auml;get och ladda upp en ny
config.json.</footer>
<script>
function pick(b) { document.getElementById('ssid').value = b.textContent; }
</script>
</body>
</html>
"""

_SAVED = """<!DOCTYPE html>
<html lang="sv"><head><meta charset="utf-8">
<title>bevattning - sparat</title></head>
<body style="font-family: sans-serif; margin: 1em;">
<p>Inst&auml;llningarna sparade &mdash; enheten startar om och ansluter
till WiFi. Du kan st&auml;nga sidan.</p>
</body></html>
"""


def _escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _scan_ssids():
    """Skanna näten (innan AP startas) för den klickbara listan.

    Direkt efter active(True) kan radion behöva en stund — försök några
    gånger innan vi ger upp (sidan visar då manuell inmatning).
    """
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    nets = []
    try:
        for _ in range(3):
            try:
                nets = sta.scan()
            except OSError:
                nets = []
            if nets:
                break
            time.sleep(0.5)
    finally:
        sta.active(False)
    best = {}  # ssid -> bästa RSSI (dubbletter från flera basstationer)
    for n in nets:
        try:
            ssid = n[0].decode()
        except (UnicodeError, ValueError):
            continue
        if ssid and (ssid not in best or n[3] > best[ssid]):
            best[ssid] = n[3]
    return sorted(best, key=lambda s: -best[s])


def _page(ssids):
    if ssids:
        options = "\n".join(
            '<button type="button" class="net" onclick="pick(this)">%s</button>'
            % _escape(s) for s in ssids)
    else:
        options = ("<p>Inga n&auml;t hittades &mdash; "
                   "skriv namnet manuellt ovan.</p>")
    return _PAGE.replace("{options}", options)


def _unquote_bytes(s):
    """Avkoda ett formulärfält: '+' -> mellanslag, %XX -> byte, sedan UTF-8
    (så att SSID med åäö överlever)."""
    s = s.replace(b"+", b" ")
    parts = s.split(b"%")
    out = parts[0]
    for p in parts[1:]:
        try:
            out += bytes([int(p[:2], 16)]) + p[2:]
        except ValueError:
            out += b"%" + p
    return out.decode()


def _form_decode(body):
    """Parsa application/x-www-form-urlencoded (bytes) -> dict med str."""
    fields = {}
    for pair in body.split(b"&"):
        if b"=" not in pair:
            continue
        key, value = pair.split(b"=", 1)
        try:
            fields[_unquote_bytes(key)] = _unquote_bytes(value)
        except (UnicodeError, ValueError):
            pass  # trasigt fält: hoppa över, valideringen fångar det
    return fields


def _parse_config(fields):
    """Validera formuläret -> config-dict. Kastar ValueError med svenskt
    felmeddelande vid ogiltig inmatning."""
    ssid = fields.get("ssid", "").strip()
    if not ssid:
        raise ValueError("SSID saknas")
    # Tom broker är ok: endast lokal drift (molnsynk av, se main.py).
    host = fields.get("mqtt_host", "").strip()
    try:
        port = int(fields.get("mqtt_port", "").strip() or "1883")
    except ValueError:
        raise ValueError("Ogiltig MQTT-port")
    if not 1 <= port <= 65535:
        raise ValueError("Ogiltig MQTT-port")
    return {
        "wifi_ssid": ssid,
        "wifi_password": fields.get("password", ""),
        "mqtt_host": host,
        "mqtt_port": port,
    }


def _save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        ujson.dump(cfg, f)
    try:
        os.sync()
    except AttributeError:
        pass


def _handle(conn, ssids):
    reader = _Reader(conn)
    req = read_request(reader)
    if not req:
        return False
    method, path, length, host = req
    if host not in PORTAL_NAMES:
        # Telefonens uppkopplingskontroll: redirect -> "logga in"-vyn öppnas.
        redirect_portal(conn)
        return False

    if method == "GET":
        _send(conn, b"200 OK", b"text/html; charset=utf-8", _page(ssids))
        return False
    if method == "POST":
        try:
            cfg = _parse_config(_form_decode(reader.read(length)))
        except ValueError as e:
            _send_text(conn, b"400 Bad Request", str(e))
            return False
        _save_config(cfg)
        print("wifi_setup: config.json sparad (SSID=%s)" % cfg["wifi_ssid"])
        _send(conn, b"200 OK", b"text/html; charset=utf-8", _SAVED)
        return True
    _send_text(conn, b"405 Method Not Allowed", "Stods inte")
    return False


def serve():
    """Starta accesspunkten och servera formuläret tills en giltig config
    sparats; serve_forever startar då om enheten. Återvänder aldrig."""
    ssids = _scan_ssids()  # skanna innan AP:n tar över radion
    print("SETUP MODE: formularet oppnas automatiskt vid anslutning")
    serve_forever(lambda conn: _handle(conn, ssids))
