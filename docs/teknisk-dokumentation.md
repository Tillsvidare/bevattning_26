# Bevattning 26 — teknisk dokumentation

Multi-tenant molntjänst för ESP32-baserade bevattningskontroller.
Uppdaterad 2026-07-07 (commit `9c7c5e3`). Kundmanual med enklare språk:
[/manual.html](https://bevattning.tillsvidare.eu/manual.html).

## 1. Systemöversikt

```
 Vännens hem                          Hetzner CX23 (157.180.69.57)
┌──────────────────────────┐         ┌─────────────────────────────────────┐
│ ESP32-S3 (MicroPython)   │  MQTT/  │ Docker Compose                      │
│  scheduler → ventil M1/M2│  TLS    │ ┌─────────┐  ┌───────────────────┐  │
│  vattensensor, klocka    │ :8883   │ │Mosquitto│  │ Caddy :80/:443    │  │
│  lokalt webUI :80        │────────►│ │ passwd  │  │  auto-Lets Encrypt│  │
│  config/schedule på flash│         │ │ + ACL   │  └───────┬───────────┘  │
└──────────────────────────┘         │ └────▲────┘          │reverse proxy │
                                     │      │1883 (internt) │              │
 Användarens mobil/dator             │ ┌────┴───────────────▼────────────┐ │
┌──────────────────────────┐  HTTPS  │ │ FastAPI-backend                 │ │
│ PWA: bevattning.         │ :443    │ │  sessioner, provisionering,     │ │
│ tillsvidare.eu           │────────►│ │  write-through av enhetsstatus  │ │
│ (login/app/admin)        │         │ │  SQLite (WAL) på volym          │ │
└──────────────────────────┘         │ └─────────────────────────────────┘ │
                                     └─────────────────────────────────────┘
```

Principer:

- **Enheten äger sitt tillstånd** (schema, huvudbrytare, sensor) och
  publicerar det retained; backend skriver igenom till DB och API:t läser
  bara DB. Endast backend publicerar `/set`, endast enheten `/status` —
  eko-loopar är omöjliga.
- **Multi-tenant genom namespacing:** allt enhetsdata bor under
  `devices/{device_id}/` i MQTT och `/api/devices/{device_id}/` i API:t.
  Ägarskap kontrolleras per anrop; andras enheter ger 404.
- **Lokal drift är alltid möjlig:** enheten fungerar utan molnet
  (se §7.6) och molnet utan enheten (visar offline + senaste kända data).

## 2. Produktionsmiljön

| Sak | Värde |
|---|---|
| Server | Hetzner CX23, Ubuntu, `ssh root@157.180.69.57` |
| Domän | `bevattning.tillsvidare.eu` (Hetzner DNS, A + AAAA) |
| Repo på servern | `/opt/bevattning_26` (klonad från GitHub `Tillsvidare/bevattning_26`) |
| Brandvägg | Hetzner Cloud Firewall: inkommande 22, 80, 443, 8883/tcp + ICMP |
| Hemligheter | `/opt/bevattning_26/.env` (chmod 600, aldrig i git; mall i `.env.example`) |
| Backup | Hetzner Backups (VPS-snapshot) + cron 03:20: `sqlite3 .backup` + tar av mosquitto-data till `/root/backups/`, 14 dagars retention (`/root/backup.sh` = `tools/server_backup.sh`) |

Containers (docker-compose.yml):

- **caddy** — reverse proxy, skaffar/förnyar Let's Encrypt-cert automatiskt.
  Cert-datat ligger på volymen `caddy-data`.
- **mosquitto** — eclipse-mosquitto:2 med egen entrypoint
  (`mosquitto/entrypoint.sh`): väntar på Caddys cert, kopierar det till
  mosquitto-läsbar plats, startar brokern och SIGHUP:ar den när
  passwd-filen eller certet ändras (poll 3 s). 1883 finns bara i
  compose-nätet; endast 8883 (TLS) publiceras.
- **backend** — FastAPI/uvicorn, byggd från `backend/Dockerfile`. Ingen
  publicerad port; nås via Caddy. Skriver mosquittos `password_file` på
  den delade volymen `mqtt-auth`.

## 3. Datamodell (SQLite, WAL)

```
users        id, email UNIQUE, password_hash (bcrypt), is_admin, created_at
invite_codes id, code UNIQUE, created_by→users, used_by→users?, used_at?, created_at
claim_codes  id, code UNIQUE ("KOD-XXXXXX"), user_id→users, expires_at (24 h),
             used_at?, device_id?, created_at
devices      id TEXT PK ("bv-xxxxxxxx" = MQTT-username & client-id),
             user_id→users, name, hw_id UNIQUE (machine.unique_id hex),
             mqtt_password_hash (mosquitto $7$; klartext lagras aldrig),
             schedules_json, irrigation_enabled?, sensor_wet?,   ← write-through
             online, last_seen?, created_at
valve_events id, device_id→devices? (NULL = legacy), valve_id, state ON/OFF,
             ts (enhetens UTC), received_at; index (device_id, valve_id, ts)
```

Migrering: ingen Alembic. `database.init_db()` kör `create_all` plus
handskrivna `ALTER TABLE` i `_migrate()` (hittills: `valve_events.device_id`).

## 4. API-referens

Alla svar JSON. Sessionscookie (signerad, 30 dagar) sätts av login/register.
Ägda enheter via dependency `owned_device` — andras/okända ger 404.

| Metod & väg | Beskrivning |
|---|---|
| `POST /api/auth/register` | `{email, password≥8, invite_code}` — engångskod krävs |
| `POST /api/auth/login` | Rate-limit: 5 fel → 60 s låst (per e-post, i minnet) |
| `POST /api/auth/logout` · `GET /api/auth/me` | |
| `GET /api/devices` | Egna enheter: id, name, online, last_seen, irrigation_enabled, sensor_wet |
| `PATCH /api/devices/{id}` | `{name}` — byt namn |
| `DELETE /api/devices/{id}` | Tar bort enhet + historik + MQTT-konto (passwd skrivs om) |
| `POST /api/claim-codes` | Ny kopplingskod, 24 h TTL |
| `GET/POST /api/devices/{id}/irrigation` | Huvudbrytaren; POST → MQTT `/set`, 202 |
| `GET /api/devices/{id}/sensor` | Read-only — enheten äger läget |
| `GET/POST /api/devices/{id}/valves/{n}/schedule` | Lista av max 6 poster `{start "HH:MM", duration_min 1-180, enabled}` |
| `GET /api/devices/{id}/valves/{n}/history?days=N` | ON/OFF-händelser, N ≤ 90 |
| `POST /api/provision` | Se §6 — ingen session, claim-koden är credential |
| `POST/GET /api/admin/invites` | Skapa/lista inbjudningskoder (admin) |
| `GET /api/admin/users` · `POST /api/admin/users/{id}/password` | Användarlista, lösenordsreset (ingen SMTP — admin meddelar själv) |
| `GET /api/admin/devices` | Alla enheter med ägare |
| `GET /api/health` | `{"mqtt": bool}` — backendens brokeranslutning |

Admin-endpoints ger 404 (inte 403) för icke-admin, för att inte läcka sin existens.

## 5. MQTT

**Topics** (prefix `devices/{device_id}/`; enhet utan device_id använder
det gamla LAN-schemat utan prefix — bakåtkompatibilitet):

| Topic | Riktning | Retained | Payload |
|---|---|---|---|
| `…/valve/{n}/schedule/status` | enhet → | ja | schema (JSON-lista) |
| `…/valve/{n}/schedule/set` | → enhet | nej | nytt schema |
| `…/valve/{n}/history` | enhet → | nej | `{"ts": "…Z", "state": "ON"/"OFF"}` |
| `…/irrigation/status` / `…/irrigation/set` | båda | ja / nej | `{"enabled": bool}` |
| `…/sensor/status` | enhet → | ja | `{"wet": bool}` (inget /set) |
| `…/availability` | enhet (LWT) → | ja | `online` / `offline` |

**Auth & ACL:** `allow_anonymous false`. Varje enhet har username =
`device_id` med slumpat lösenord (PBKDF2-SHA512-hash i mosquittos
`$7$`-format, genererad av `backend/app/mosq_passwd.py` — verifierad mot
mosquitto 2.1.2). Backend ansluter som `backend` (lösenord ur `.env`).
ACL (`mosquitto/acl`): `backend` läser/skriver `devices/#`; enheter
mönstret `devices/%u/#` — publicering/prenumeration utanför eget namespace
kastas tyst (OBS: mosquitto ger SUBACK-success även vid ACL-nekad läsning —
testa alltid med meddelandeflöde, inte returkoder).

**TLS:** port 8883 med Caddys Let's Encrypt-cert (kopieras av entrypoint-
watchern, SIGHUP vid förnyelse). Enheterna kör `SSLContext` med
`CERT_NONE` — krypterad transport utan certkedjevalidering (medvetet:
MicroPython saknar rimlig CA-hantering; hotmodellen accepterar det).
Passwd-filen skrivs atomiskt (temp + `os.replace`, chmod 644 — mkstemps
600 kan mosquitto-usern inte läsa).

## 6. Provisionering & enhetslivscykel

```
Användare: "+ Lägg till enhet" ──► POST /api/claim-codes ──► KOD-XXXXXX (24 h)
Enhet: AP-portal (formulär: SSID, lösenord, kod) ──► config.json
Boot: claim_code utan mqtt_password ──► provision.py:
  POST /api/provision {claim_code, hw_id, fw_version}   (HTTPS, ingen session)
  ◄── {device_id, mqtt_username, mqtt_password, mqtt_host, mqtt_port, mqtt_tls}
  spara i config.json, radera koden, machine.reset()
Boot 2: mqtt_link ansluter TLS 8883 ──► availability=online ──► syns i appen
```

- **Felhantering på enheten:** nätfel → 3 försök (5 s, 30 s) och sedan var
  10:e minut, lokal drift under tiden, status i lokala webUI:t. 4xx →
  koden raderas + svensk instruktion i webUI:t.
- **Om-provisionering, samma ägare** (samma `hw_id`): samma `device_id`
  tillbaka, lösenordet roteras. Används för WiFi-/lösenordsbyte.
- **Ägarbyte** ("fysisk åtkomst vinner"): enheten flyttas till nya kontot,
  lösenordet roteras, gamla ägarens historik och cachade tillstånd raderas.
- **Borttagning:** `DELETE /api/devices/{id}` i appen → rad + historik
  bort, passwd-filen skrivs om → MQTT-kontot dör direkt.
- **Historikimport** från för-multi-tenant-databas:
  `tools/import_legacy_events.py` (adopterar `device_id IS NULL`-rader
  eller kopierar från gammal databasfil).

## 7. Firmware (esp32/, MicroPython ≥ 1.22, testad på 1.27/ESP32-S3)

Moduler: `boot.py` (OTA-/räddningsläge), `main.py` (orkestrering),
`net.py` (WiFi), `clock.py` (NTP + svensk tid), `scheduler.py` (ventiler),
`watersensor.py`, `mqtt_link.py`, `provision.py`, `webui.py` (Microdot),
`wifi_setup.py` (AP-portal), `wifi_update.py` (AP-server/captive portal),
`ota_update.py`, `storage.py` (config/schedule/settings på flash),
`hw.py`, `lib/` (vendorerad microdot + umqtt).

### 7.1 Uppstart
1. Ladda config (saknas → AP-portalen; §7.3), schema, inställningar.
2. Pulsa båda ventilerna stängda (känt läge) **före** WDT.
3. WiFi (`net.connect`, timeout 180 s → portal-fallback; §7.3).
4. Ev. provisionering (§6). 5. WDT + tasks: klocka, MQTT, schemaläggare,
   sensor, webUI, LED.

### 7.2 Watchdog
Hårdvaru-WDT 30 s, startas efter WiFi. `watchdog_task` matar varje sekund
endast om alla tre kritiska tasks (mqtt/scheduler/sensor) lämnat heartbeat
inom 15 s → total hängning eller enskilt task-häng ger omstart inom ~45 s.
Ej aktiv: före WiFi-anslutning, i portalen, i OTA-läget (medvetet — se
kommentarer i main.py/boot.py). Omstart är säker: ventilerna stängs först
av allt och schemat ligger på flash.

### 7.3 WiFi-portalen (`bevattning`-AP:n, captive portal)
Startar när: (a) config saknas/trasig, (b) **WiFi-fallback**: ingen
kontakt inom `WIFI_PORTAL_AFTER_S` = 180 s vid boot (i praktiken 3–4½ min
pga försökscykeln). I fallback-läget bevaras befintlig config: tomt
kodfält = rent WiFi-byte (molnkopplingen behålls — verifierat), ifylld kod
= ny koppling (gamla credentials rensas så provisioneringen triggas).
Portalen har inaktivitets-timeout 10 min → reset → nytt 3-minutersförsök
med sparade nätet, i evig cykel — en router som bara var omstartad läker
sig själv. WiFi-tapp under drift öppnar ALDRIG portalen (enheten kör
vidare lokalt och återansluter i det tysta).

### 7.4 OTA
Knapp hålls vid strömpåslag → `ota_update.run()`: hämta `manifest.json`
från GitHub raw (main-grenen, cache-buster), jämför sha256 per fil, ladda
ned ändrade/nya till `.tmp`, verifiera, byt ut allt, spara färska
manifestet, reset. Nya filer installeras automatiskt (saknad fil ≠ hash);
borttagna filer raderas ALDRIG. Enhetslokala filer (`config.json`,
`schedule.json`, `settings.json`) står i `EXCLUDE` i
`tools/make_manifest.py` och rörs aldrig — credentials överlever varje
OTA. Publicera: `python tools/make_manifest.py` + push. Misslyckad OTA →
räddningsläget (AP-filhanterare).

### 7.5 MQTT-länken
Egen reconnect-loop (umqtt är blockerande; allt klientarbete under ett
asyncio-Lock), LWT + retained statusrepublicering vid varje anslutning,
historik-outbox (50 poster) som spolas när länken är uppe. TLS via
`SSLContext(CERT_NONE)`. OBS: MicroPythons SSLSocket saknar `settimeout`
— sockelpill efter TLS-wrap kräver AttributeError-fallback (watchdogen är
skyddsnätet mot evig blockering).

### 7.6 Lokal drift
Tom kopplingskod i portalen → ingen molnkoppling alls. Kryssrutan
"Molnsynk" i lokala webUI:t kopplar ner snyggt (explicit offline-publish),
köar historik och återsynkar vid påslag. WiFi/NTP krävs alltid (schemat
behöver klockan). Lokala webUI:t på `http://bevattning.local/` visar även
enhets-id och provisioneringsstatus (`/api/device`).

## 8. Frontend (backend/static/)

- `login.html` — inloggning/registrering (inbjudningskod).
- `index.html` + `app.js` — huvudapp: online-status (LWT-driven, poll
  30 s), namnbyte (PATCH), enhetsväljare (>1 enhet), huvudbrytare, sensor,
  schemaredigering med eko-bekräftelse ("Sparat på enheten ✓"), 7-dagars
  Chart.js-diagram, claim-modal med livepollning. 401 → login.
- `admin.html` — inbjudningar, användare (lösenordsreset), enhetslista.
- `onboarding.html` / `manual.html` — kundmaterial, utskriftsvänliga.
- **PWA:** `manifest.json` + ikoner + minimal service worker (ingen
  cachning — alltid färsk data). "Lägg till på hemskärmen" ger fristående
  app; sessioncookien (30 d) delas.

## 9. Säkerhetsmodell

- Webb: bcrypt + signerad sessionscookie (SECRET_KEY i `.env`), `https_only`
  i produktion, registrering endast med engångs-inbjudningskod,
  login-rate-limit, ägarskapskontroll med 404-semantik.
- MQTT: per-enhet-konton, ACL-isolering per namespace, TLS, lösenord
  lagras endast som hash, rotation vid varje om-provisionering.
- Medvetna avvägningar: enhetens TLS validerar inte certkedjan (§5);
  provisionerings-POSTen skyddas av HTTPS + engångskod; ingen SMTP (glömt
  lösenord går via admin); claim-koden är värdelös efter användning/24 h.

## 10. Drift-runbook

```bash
# Deploy av ny version
ssh root@157.180.69.57
cd /opt/bevattning_26 && git pull && docker compose up -d --build

# Loggar
docker logs -f bevattning_26-backend-1     # API + MQTT-write-through
docker logs -f bevattning_26-mosquitto-1   # anslutningar, auth
docker logs -f bevattning_26-caddy-1       # cert-förnyelse

# Databasen direkt (t.ex. status utan admin-inlogg)
sqlite3 /var/lib/docker/volumes/bevattning_26_backend-data/_data/irrigation.db \
  "SELECT id, name, online, last_seen FROM devices;"

# Admin-lösenord tappat (ADMIN_* i .env gäller bara första bootstrap):
python3 - <<'P'
import bcrypt; print(bcrypt.hashpw(b"NYTT-LOSEN", bcrypt.gensalt()).decode())
P
sqlite3 <db> "UPDATE users SET password_hash='<hash>' WHERE email='robert@tillsvidare.com';"

# Backup-återläsning: stoppa stacken, ersätt irrigation.db i volymen med
# /root/backups/irrigation-DATUM.db, packa upp mosquitto-tar, starta.
```

## 11. Lokal utveckling (utan Docker, Windows)

```bash
python -m venv .venv && .venv/Scripts/pip install -r backend/requirements.txt paho-mqtt amqtt
.venv/Scripts/python tools/run_broker.py          # amqtt-broker :1883
cd backend && ../.venv/Scripts/python ../tools/run_dev.py   # seedar dev-data
.venv/Scripts/python tools/simulate_device.py     # fejk-enhet bv-dev001
```

`run_dev.py` seedar: admin `dev@example.com`/`devlosen01`, enheten
`bv-dev001`, inbjudningskoden `DEVKOD01`. Simulatorn tar `--device-id`,
`--user`, `--password`, `--tls` för test mot produktionsbrokern. Kör inte
uvicorn direkt på Windows (Proactor-loopen bryter aiomqtt — se run_dev.py).
Kända dev-fällor: amqtt levererar retained till `/set`-prenumeranter
(simulatorn och firmwaren filtrerar), och `netsh wlan`-skanning är
opålitlig för att detektera enhetens AP.

## 12. Kända begränsningar

- OTA raderar aldrig filer; borttagna moduler ligger kvar på enheten.
- Sessioner lagras i cookien: byte av SECRET_KEY loggar ut alla.
- Login-rate-limit och sessioner är per-process (OK: en uvicorn-process).
- `last_seen` uppdateras bara vid meddelanden — en tyst men ansluten enhet
  har gammal last_seen men korrekt `online` (LWT-driven).
- Fortinet/webbfilter-nät kan MITM:a/blockera domänen och klipper
  MQTT-sessioner (~30 s) — vitlista domänen i sådana miljöer.
- ESP32 ser bara 2,4 GHz-WiFi.
