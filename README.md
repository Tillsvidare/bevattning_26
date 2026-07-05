# Bevattning 26

IoT-bevattningssystem: ESP32-S3 (MicroPython) med två motorventiler,
Mosquitto + FastAPI-backend (SQLite) i Docker och en molnfrontend med
schemaredigering och 7-dagars historikdiagram (Chart.js).

## Arkitektur

```
ESP32-S3 (MicroPython)                    Server (Docker)
┌─────────────────────────┐               ┌──────────────────────────┐
│ scheduler ─ ventil M1/M2 │    MQTT      │ Mosquitto :1883          │
│ clock (NTP + sv. tid)    │◄────────────►│    ▲                     │
│ webui (Microdot :80)     │              │ FastAPI :8000 ── SQLite  │
│ schedule.json  config.json│             │    └─ statisk frontend   │
└─────────────────────────┘               └──────────────────────────┘
```

### MQTT-topics

| Topic | Riktning | Retained | Innehåll |
|---|---|---|---|
| `valve/{id}/schedule/status` | enhet → moln | ja | aktuellt schema (JSON-lista) |
| `valve/{id}/schedule/set` | moln → enhet | nej | nytt schema (JSON-lista) |
| `valve/{id}/history` | enhet → moln | nej | `{"ts": "...Z", "state": "ON"/"OFF"}` |
| `bevattning/irrigation/status` | enhet → moln | ja | huvudbrytaren `{"enabled": bool}` |
| `bevattning/irrigation/set` | moln → enhet | nej | nytt huvudbrytarläge |
| `bevattning/sensor/status` | enhet → moln | ja | vattensensorn `{"wet": bool}` — inget `/set`, enheten äger läget |
| `bevattning/availability` | enhet (LWT) | ja | `online`/`offline` |

Endast backenden publicerar `/set`; endast enheten `/status` — det gör
eko-loopar omöjliga. Enheten tillämpar `/set`, sparar `schedule.json` och
ekar tillbaka till `/status`.

Ett schema är en **lista av bevattningar** (max 6 per ventil och dygn):

```json
[
  {"start": "06:30", "duration_min": 15, "enabled": true},
  {"start": "18:30", "duration_min": 10, "enabled": true}
]
```

## Kom igång — server

### Med Docker (rekommenderat)

Kräver Docker med Compose. Bygger och startar Mosquitto (port 1883) och
backenden (port 8000); SQLite-databasen och brokerns retained-meddelanden
hamnar i namngivna volymer och överlever omstart.

```bash
docker compose up --build
```

### Utan Docker (t.ex. lokal utveckling på Windows)

1. Installera beroendena (helst i en virtuell miljö):

   ```bash
   python -m venv .venv
   .venv\Scripts\activate        # Linux/macOS: source .venv/bin/activate
   pip install -r backend/requirements.txt
   ```

2. Starta en MQTT-broker på `localhost:1883` — Mosquitto om det finns
   installerat, annars den medföljande Python-brokern:

   ```bash
   pip install amqtt
   python tools/run_broker.py
   ```
3. Starta backenden:

   ```bash
   cd backend
   python ../tools/run_dev.py
   ```

   Kör inte `uvicorn app.main:app` direkt på Windows — uvicorn väljer då en
   eventloop som aiomqtt inte fungerar med (se kommentaren i skriptet).

Backenden konfigureras med miljövariabler (docker-compose.yml sätter dem åt
dig; utan Docker gäller defaultvärdena):

| Variabel | Default | Beskrivning |
|---|---|---|
| `MQTT_HOST` | `localhost` | Brokerns adress |
| `MQTT_PORT` | `1883` | Brokerns port |
| `DB_PATH` | `./irrigation.db` | Sökväg till SQLite-databasen |

### Adresser

- Frontend: http://localhost:8000
- API: `GET/POST /api/valves/{1|2}/schedule`, `GET /api/valves/{id}/history?days=7`, `GET /api/irrigation` (POST), `GET /api/sensor`, `GET /api/health`

Testa utan hårdvara:

```bash
pip install paho-mqtt
python tools/simulate_device.py
```

Simulatorn publicerar scheman + 7 dagars historik och ekar `/set` → `/status`
precis som riktiga enheten.

## Kom igång — ESP32

1. Flasha MicroPython (ESP32-S3).
2. Kopiera `esp32/config.json.example` → `esp32/config.json` och fyll i
   WiFi-uppgifter och broker-IP. `mqtt_host` kan lämnas tom (eller utelämnas)
   för endast lokal drift — molnsynken går då inte att slå på. (Steget kan
   hoppas över helt — se inställningsläget nedan.)
3. Ladda upp allt: `mpremote cp -r esp32/* :` (inklusive `lib/`).
4. Starta om. REPL-loggen visar NTP-synk, MQTT-anslutning och webbserverns IP.
5. Lokalt gränssnitt: `http://bevattning.local/` (mDNS; eller
   `http://<enhetens-ip>/`) — redigera schemat direkt på enheten;
   ändringar synkas till molnet via MQTT.

**Uppdatering (knappen vid strömpåslag):** håll knappen intryckt när strömmen
slås på → enheten ansluter till WiFi, hämtar `manifest.json` från GitHub
(adressen i `esp32/ota_update.py`, `BASE_URL` — ändra till ditt repo),
laddar ned filer vars sha256 skiljer sig, verifierar och byter ut dem, och
startar om i normalläge. Enhetslokala filer (`config.json`, `schedule.json`,
`settings.json`) rörs aldrig. Publicera en uppdatering: kör
`python tools/make_manifest.py` och pusha till GitHub.

**Räddningsläge (AP):** misslyckas OTA-uppdateringen (ingen config, inget
WiFi, GitHub onåbart, trasig nedladdning) startar enheten i stället en öppen
accesspunkt `bevattning`. Anslut och skriv valfri http-adress i en vanlig
webbläsare — t.ex. `http://bevattning/` (snedstrecket krävs, annars söker
webbläsaren) — så visas filhanteraren där filer kan laddas upp manuellt. (Ingen "logga in"-vy öppnas här —
avsiktligt: dess minivy blockerar filväljaren, så enheten svarar "internet
OK" på telefonens nätverkskontroll i stället.)

**Inställningsläge (WiFi via webbläsare):** saknas giltig `config.json`
startar enheten samma accesspunkt, och formuläret öppnas automatiskt när
telefonen ansluter — välj WiFi-nätverk i den skannade listan (eller skriv
manuellt), fyll i lösenord och eventuell MQTT-broker (tom = endast lokal
drift). När inställningarna sparats startar enheten om i normalläge. Behöver WiFi ändras senare: gå in i uppdateringsläget och ladda
upp en ny `config.json`, eller ta bort den befintliga så startar
inställningsläget vid nästa omstart.

**Tid:** klockan NTP-synkas vid uppstart och därefter dygnsvis. Svensk
lokaltid (CET/CEST med EU:s sommartidsregler) beräknas på enheten, så schemat
går på rätt lokal tid även efter strömavbrott. Schemaläggaren kör aldrig
innan klockan är synkad.

**Huvudbrytare (bevattning på/av):** en toggle "Bevattning" finns i både
enhetens lokala gränssnitt och molndashboarden (`GET/POST /api/irrigation`).
Avstängd startar inga schemalagda bevattningar, och en pågående körning
avbryts (ventilen stängs). Läget ägs av enheten, sparas i `settings.json`
och synkas åt båda håll via MQTT precis som schemat.

**Vattensensor:** en analog vattensensor (GPIO 1) stoppar bevattningen när
den blir våt — samma effekt som huvudbrytaren av: pågående körning avbryts
(ventilen stängs) och inga nya schemalagda körningar startar. När sensorn
torkar körs inget ikapp; nästa schemalagda starttid gäller. Hysteres gör
läget stabilt: vått efter 2 s sammanhängande vått, torrt först efter 30 s
sammanhängande torrt. Läget syns i båda gränssnitten ("Sensor: VÅT —
bevattning stoppad") via retained `bevattning/sensor/status` och
`GET /api/sensor`; det är read-only och påverkar inte huvudbrytarens
sparade läge.

**Lokal drift (molnsynk av):** i enhetens lokala gränssnitt finns en toggle
"Molnsynk (MQTT)". Avstängd kör enheten helt lokalt: schemat går som vanligt
(WiFi och NTP behålls) men ingen MQTT-anslutning görs och inget publiceras.
Historikhändelser köas (upp till 50) och skickas när synken slås på igen,
och schemat republiceras då automatiskt till molnet. Valet sparas i
`settings.json` på flash och överlever omstart.

**Statuslampor:** grön = WiFi + klocksynk + MQTT OK (MQTT krävs inte i lokal
drift); röd = något saknas.

## Kataloger

- `backend/` — FastAPI-app + statisk frontend
- `mosquitto/` — brokerkonfiguration (anonym åtkomst: endast för LAN)
- `esp32/` — MicroPython-firmware (`lib/` innehåller vendorerad microdot + umqtt)
- `tools/` — enhetssimulator för test
