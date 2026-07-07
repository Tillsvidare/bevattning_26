# Lagring på flash: config.json (WiFi/MQTT) och schedule.json (bevattning).
#
# Samma mönster som provpump/storage.py: ujson, validering/clamp vid både
# läsning och skrivning, os.sync() så data överlever strömavbrott.

import os

try:
    import ujson
except ImportError:  # CPython (för test på datorn)
    import json as ujson

CONFIG_FILE = "config.json"
SCHEDULE_FILE = "schedule.json"
SETTINGS_FILE = "settings.json"

VALVE_IDS = ("1", "2")
MIN_DURATION = 1
MAX_DURATION = 180
# Max antal bevattningar per ventil och dygn (speglas i backend/app/schemas.py).
MAX_ENTRIES = 6


def _sync():
    """Spola till flash så data överlever strömavbrott (no-op på CPython)."""
    try:
        os.sync()
    except AttributeError:
        pass


def load_config():
    """Läs config.json. Utan den kan enheten inte göra något vettigt.

    Molnnycklarna är valfria — gamla configs förblir giltiga (lokal drift).
    mqtt_host/credentials sätts normalt av provisioneringen (provision.py),
    inte för hand. claim_code finns bara mellan portalen och en lyckad
    provisionering. cloud_url är en dev-override av provision.DEFAULT_URL.
    """
    with open(CONFIG_FILE) as f:
        cfg = ujson.load(f)
    for key in ("wifi_ssid", "wifi_password"):
        if key not in cfg:
            raise ValueError("config.json saknar '%s'" % key)
    cfg.setdefault("mqtt_host", "")
    cfg.setdefault("mqtt_port", 1883)
    cfg.setdefault("device_id", "")
    cfg.setdefault("mqtt_user", "")
    cfg.setdefault("mqtt_password", "")
    cfg.setdefault("mqtt_tls", False)
    cfg.setdefault("claim_code", "")
    cfg.setdefault("cloud_url", "")
    return cfg


def save_config(cfg):
    """Skriv config.json till flash (används av provisioneringen)."""
    with open(CONFIG_FILE, "w") as f:
        ujson.dump(cfg, f)
    _sync()
    print("storage: config.json sparad")


def validate_entry(entry):
    """Validera/klampa en ventils schemapost. Delas av webui och MQTT.

    Kastar ValueError vid ogiltig form; klampar duration till giltigt spann.
    """
    if not isinstance(entry, dict):
        raise ValueError("schemapost måste vara ett objekt")

    start = entry.get("start", "")
    parts = start.split(":") if isinstance(start, str) else []
    if len(parts) != 2:
        raise ValueError("start måste vara HH:MM")
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError("start måste vara HH:MM")
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("start utanför 00:00-23:59")

    duration = int(entry.get("duration_min", 15))
    duration = max(MIN_DURATION, min(MAX_DURATION, duration))

    return {
        "start": "%02d:%02d" % (hh, mm),
        "duration_min": duration,
        "enabled": bool(entry.get("enabled", False)),
    }


def validate_entries(entries):
    """Validera en ventils hela lista av bevattningar (0-MAX_ENTRIES)."""
    if isinstance(entries, dict):
        entries = [entries]  # gammalt format: en enda post
    if not isinstance(entries, list):
        raise ValueError("schema måste vara en lista av poster")
    if len(entries) > MAX_ENTRIES:
        raise ValueError("max %d bevattningar per dygn" % MAX_ENTRIES)
    return [validate_entry(e) for e in entries]


def load_schedule():
    """Läs schedule.json; saknade/trasiga listor ersätts med tom lista."""
    try:
        with open(SCHEDULE_FILE) as f:
            raw = ujson.load(f)
    except (OSError, ValueError):
        print("storage: ingen giltig schedule.json, använder default")
        raw = {}
    schedule = {}
    for vid in VALVE_IDS:
        try:
            schedule[vid] = validate_entries(raw.get(vid, []))
        except ValueError as e:
            print("storage: ventil %s: %s, använder tom lista" % (vid, e))
            schedule[vid] = []
    return schedule


def save_schedule(schedule):
    """Skriv hela schemat till flash."""
    try:
        with open(SCHEDULE_FILE, "w") as f:
            ujson.dump(schedule, f)
        _sync()
        print("storage: schedule.json sparad")
    except OSError:
        print("storage: kunde inte skriva schedule.json")


def load_settings():
    """Läs settings.json (körläge m.m.). Saknad/trasig fil ger default."""
    try:
        with open(SETTINGS_FILE) as f:
            raw = ujson.load(f)
    except (OSError, ValueError):
        raw = {}
    return {
        "cloud_enabled": bool(raw.get("cloud_enabled", True)),
        "irrigation_enabled": bool(raw.get("irrigation_enabled", True)),
    }


def save_settings(settings):
    """Skriv inställningarna till flash."""
    try:
        with open(SETTINGS_FILE, "w") as f:
            ujson.dump(settings, f)
        _sync()
        print("storage: settings.json sparad")
    except OSError:
        print("storage: kunde inte skriva settings.json")


def update_valve(schedule, valve_id, entries):
    """Validera och ersätt en ventils lista av bevattningar; sparar allt.

    Returnerar den validerade listan. Kastar ValueError vid ogiltig data.
    Gemensam väg för både lokala webbredigeringar och MQTT /set.
    """
    validated = validate_entries(entries)
    schedule[str(valve_id)] = validated
    save_schedule(schedule)
    return validated
