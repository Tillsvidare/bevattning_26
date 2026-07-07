# Bevattning 26

Multi-tenant IoT-bevattning: ESP32-S3 (MicroPython) med två motorventiler
och vattensensor, självprovisionerande mot en molntjänst
(**bevattning.tillsvidare.eu**) med konton, PWA-webbapp, schemaläggning
och 7-dagars historik. Körs lika gärna helt lokalt utan moln.

**Dokumentation:**

- [docs/teknisk-dokumentation.md](docs/teknisk-dokumentation.md) —
  arkitektur, datamodell, API, MQTT, provisionering, firmware, drift-runbook
- [hardware.md](hardware.md) — kopplingar och komponenter
- Kundmaterial (enkelt språk, utskriftsvänligt):
  [manual](https://bevattning.tillsvidare.eu/manual.html) ·
  [kom igång-guide](https://bevattning.tillsvidare.eu/onboarding.html)

## Arkitektur i korthet

```
ESP32-S3 ──MQTT/TLS :8883──► Mosquitto (per-enhet-konton + ACL) ─┐ Docker på
mobil/PWA ──HTTPS :443──► Caddy (Lets Encrypt) ──► FastAPI ── SQLite │ Hetzner-VPS
                                                     ▲───────────────┘
```

- Allt enhetsdata namespacas `devices/{device_id}/…` (MQTT) respektive
  `/api/devices/{device_id}/…` (API); ägarskap kontrolleras per anrop.
- Enheten äger sitt tillstånd (retained status → write-through till DB);
  endast backend publicerar `/set`, endast enheten `/status`.
- Nya enheter kopplas med engångs **kopplingskod**: AP-portal → WiFi + kod
  → `POST /api/provision` → MQTT-credentials → klart. Vänner registrerar
  konto med **inbjudningskod** från adminsidan.

## Snabbstart

**Produktion** (Hetzner, `/opt/bevattning_26`):

```bash
git pull && docker compose up -d --build     # .env krävs, se .env.example
```

**Lokal utveckling utan Docker** (Windows — kör inte uvicorn direkt,
se kommentaren i tools/run_dev.py):

```bash
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt paho-mqtt amqtt
.venv/Scripts/python tools/run_broker.py                    # broker :1883
cd backend && ../.venv/Scripts/python ../tools/run_dev.py   # API :8000 + dev-seed
.venv/Scripts/python tools/simulate_device.py               # fejk-enhet
```

Dev-inlogg: `dev@example.com` / `devlosen01` (enheten `bv-dev001`,
inbjudningskoden `DEVKOD01`).

**Firmware:** flasha MicroPython på ESP32-S3, `mpremote cp -r esp32/* :`
— resten (WiFi + koppling) görs i AP-portalen som startar automatiskt.
Uppdateringar därefter via OTA: håll knappen vid strömpåslag, enheten
hämtar från GitHub (publicera med `python tools/make_manifest.py` + push).

## Kataloger

- `backend/` — FastAPI-app + statisk frontend/PWA (`static/`)
- `esp32/` — MicroPython-firmware (`lib/` = vendorerad microdot + umqtt)
- `mosquitto/` — brokerkonfig, ACL, entrypoint-watcher (cert/passwd)
- `tools/` — dev-broker, dev-server, enhetssimulator, manifest, backup
- `docs/` — teknisk dokumentation
