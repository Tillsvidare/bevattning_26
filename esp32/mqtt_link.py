# MQTT-länk för ESP32 (umqtt) med egen reconnect-loop.
#
# umqtt är blockerande och inte task-säker, därför:
#  - Egen återanslutningsloop med await asyncio.sleep (umqtt.robusts inbyggda
#    busy-retry svälter asyncio och watchdogen).
#  - Ett asyncio.Lock runt ALLA klientanrop.
#  - Socket-timeout så en död broker inte kan blockera för evigt.
#  - Historik köas i en outbox och spolas när anslutningen är uppe, så
#    ON/OFF-händelser inte tappas vid korta avbrott.
#
# Topics (en publicerare per topic — inga eko-loopar). Med device_id i
# config namespacas allt under devices/{device_id}/ (molnets multi-tenant-
# schema); utan device_id används det gamla LAN-schemat (bakåtkompatibelt):
#   [devices/{did}/]valve/{id}/schedule/status   retained av enheten
#   [devices/{did}/]valve/{id}/schedule/set      prenumereras; ekas till /status
#   [devices/{did}/]valve/{id}/history           ON/OFF med ISO-tidsstämpel
#   devices/{did}/irrigation/status|set          huvudbrytaren (legacy:
#                                                bevattning/irrigation/...)
#   devices/{did}/sensor/status                  vattensensorn, retained
#   devices/{did}/availability                   "online"/"offline", LWT
#
# Autentisering: mqtt_user/mqtt_password från provisioneringen; mqtt_tls
# ger krypterad anslutning utan certvalidering (MicroPython saknar rimlig
# CA-hantering — accepterat beslut, se planen).

import asyncio
import time

import machine
import ubinascii

try:
    import ujson
except ImportError:  # CPython (för test på datorn)
    import json as ujson

from umqtt.simple import MQTTClient

import clock

VALVE_IDS = ("1", "2")
SOCKET_TIMEOUT_S = 5
RECONNECT_DELAY_S = 5
PING_INTERVAL_MS = 30000
OUTBOX_MAX = 50


class MqttLink:
    def __init__(self, cfg, get_schedule, apply_entries,
                 get_irrigation=None, apply_irrigation=None, get_sensor=None):
        """get_schedule() -> hela schemat; apply_entries(vid, list) -> validerad lista;
        get_irrigation() -> bool (huvudbrytaren); apply_irrigation(bool) sparar;
        get_sensor() -> bool (vattensensorn våt)."""
        self._host = cfg["mqtt_host"]
        self._port = cfg["mqtt_port"]
        self._user = cfg.get("mqtt_user") or None
        self._password = cfg.get("mqtt_password") or None
        self._tls = bool(cfg.get("mqtt_tls"))
        self._get_schedule = get_schedule
        self._apply_entries = apply_entries
        self._get_irrigation = get_irrigation
        self._apply_irrigation = apply_irrigation
        self._get_sensor = get_sensor

        # Topics byggs en gång här: namespacade med device_id (molnet),
        # annars det gamla LAN-schemat. client-id = device_id i molnet.
        device_id = cfg.get("device_id") or ""
        self._valve_prefix = ("devices/%s/" % device_id) if device_id else ""
        base = ("devices/%s/" % device_id) if device_id else "bevattning/"
        self._t_irr_status = (base + "irrigation/status").encode()
        self._t_irr_set = (base + "irrigation/set").encode()
        self._t_sensor_status = (base + "sensor/status").encode()
        self._t_availability = (base + "availability").encode()
        if device_id:
            self._client_id = device_id.encode()
        else:
            self._client_id = b"bevattning-" + ubinascii.hexlify(machine.unique_id())
        self._client = None
        self._lock = asyncio.Lock()
        self._pending_sets = []        # (valve_id, payload) mottagna på schedule/set
        self._pending_irrigation = []  # payloads mottagna på irrigation/set
        self._outbox = []              # (topic, payload) som väntar på anslutning
        self.connected = False
        # Molnsynk på/av (toggle i lokala webbgränssnittet). Av = helt lokal
        # drift: ingen anslutning görs och inget publiceras; historik köas i
        # outboxen (äldsta tappas vid taket) och spolas om synken slås på igen.
        self.enabled = True

    def set_enabled(self, enabled):
        """Slå på/av molnsynken; loop() kopplar upp/ner vid nästa varv."""
        self.enabled = bool(enabled)
        print("mqtt: molnsynk %s" % ("på" if self.enabled else "av"))

    # --- callbacks (körs inne i check_msg, med låset taget: rör inte klienten här) ---

    def _on_msg(self, topic, msg):
        if topic == self._t_irr_set:
            try:
                self._pending_irrigation.append(ujson.loads(msg))
            except ValueError:
                print("mqtt: irrigation/set med ogiltig JSON")
            return
        # Skala bort ev. devices/{id}/-prefix -> "valve/{n}/schedule/set".
        name = topic.decode()
        if self._valve_prefix and name.startswith(self._valve_prefix):
            name = name[len(self._valve_prefix):]
        parts = name.split("/")
        if len(parts) == 4 and parts[0] == "valve" and parts[2] == "schedule" and parts[3] == "set":
            valve_id = parts[1]
            try:
                payload = ujson.loads(msg)
            except ValueError:
                print("mqtt: /set med ogiltig JSON")
                return
            self._pending_sets.append((valve_id, payload))

    # --- anslutning ---

    def _make_ssl_context(self):
        """TLS utan certvalidering: krypterad trafik, men MicroPython har
        ingen rimlig CA-bundle-hantering (accepterat beslut, se planen)."""
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        try:
            ctx.check_hostname = False
        except AttributeError:
            pass
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _connect(self):
        import gc
        gc.collect()  # TLS-handskakningen vill ha sammanhängande heap
        client = MQTTClient(
            self._client_id, self._host, port=self._port, keepalive=60,
            user=self._user, password=self._password,
            ssl=self._make_ssl_context() if self._tls else None,
        )
        client.set_callback(self._on_msg)
        client.set_last_will(self._t_availability, b"offline", retain=True, qos=1)
        # timeout= sätter socket-timeout före TCP-handskakningen, så en död
        # broker inte kan blockera event-loopen längre än så här.
        client.connect(clean_session=True, timeout=SOCKET_TIMEOUT_S)
        self._client = client
        for vid in VALVE_IDS:
            client.subscribe(
                (self._valve_prefix + "valve/%s/schedule/set" % vid).encode(),
                qos=1,
            )
        if self._get_irrigation:
            client.subscribe(self._t_irr_set, qos=1)
        self._raw_publish(self._t_availability, b"online", retain=True)
        # Retained status så backenden får tillståndet direkt vid varje
        # (åter)anslutning: scheman + huvudbrytaren.
        schedule = self._get_schedule()
        for vid in VALVE_IDS:
            self._raw_publish(
                (self._valve_prefix + "valve/%s/schedule/status" % vid).encode(),
                ujson.dumps(schedule[vid]),
                retain=True,
            )
        if self._get_irrigation:
            self._raw_publish(
                self._t_irr_status,
                ujson.dumps({"enabled": bool(self._get_irrigation())}),
                retain=True,
            )
        if self._get_sensor:
            self._raw_publish(
                self._t_sensor_status,
                ujson.dumps({"wet": bool(self._get_sensor())}),
                retain=True,
            )
        print("mqtt: ansluten till %s:%d" % (self._host, self._port))

    def _raw_publish(self, topic, payload, retain=False):
        # check_msg() lämnar sockeln blockerande utan timeout; återställ den
        # före varje publish så QoS1-ACK-väntan inte kan hänga för evigt.
        # TLS: MicroPythons SSLSocket saknar settimeout — där är watchdogen
        # (30 s) skyddsnätet mot en evigt blockerande publish.
        try:
            self._client.sock.settimeout(SOCKET_TIMEOUT_S)
        except AttributeError:
            pass
        self._client.publish(topic, payload, retain=retain, qos=1)

    def _mark_down(self, err):
        print("mqtt: fel (%s), återansluter" % err)
        self.connected = False
        try:
            self._client.sock.close()
        except (OSError, AttributeError):
            pass

    def _disconnect(self):
        """Snygg frånkoppling när molnsynken slås av.

        Publicerar availability=offline explicit — en ren DISCONNECT gör
        att brokern kastar LWT:n utan att skicka den.
        """
        try:
            self._raw_publish(self._t_availability, b"offline", retain=True)
            self._client.disconnect()
        except (OSError, AttributeError):
            pass
        self.connected = False
        print("mqtt: frånkopplad (molnsynk av)")

    # --- publicering ---

    def publish_history(self, valve_id, state):
        """Köa en ON/OFF-händelse; loop() skickar när anslutningen är uppe.

        Synkron och alltid säker att anropa (även offline). Tidsstämpeln
        tas NU, inte vid sändning, så händelsen blir rätt även om den
        levereras senare.
        """
        payload = ujson.dumps({"ts": clock.iso_utc(), "state": state})
        if len(self._outbox) >= OUTBOX_MAX:
            self._outbox.pop(0)  # tappa äldsta hellre än att växa obegränsat
        self._outbox.append((
            (self._valve_prefix + "valve/%s/history" % valve_id).encode(),
            payload,
        ))

    async def publish_irrigation(self):
        """Publicera huvudbrytarens läge retained till irrigation/status."""
        if not (self.enabled and self.connected and self._get_irrigation):
            return  # _connect() publicerar om vid nästa anslutning
        try:
            async with self._lock:
                self._raw_publish(
                    self._t_irr_status,
                    ujson.dumps({"enabled": bool(self._get_irrigation())}),
                    retain=True,
                )
        except (OSError, AttributeError) as e:
            self._mark_down(e)

    async def publish_sensor(self):
        """Publicera vattensensorns läge retained till sensor/status."""
        if not (self.enabled and self.connected and self._get_sensor):
            return  # _connect() publicerar om vid nästa anslutning
        try:
            async with self._lock:
                self._raw_publish(
                    self._t_sensor_status,
                    ujson.dumps({"wet": bool(self._get_sensor())}),
                    retain=True,
                )
        except (OSError, AttributeError) as e:
            self._mark_down(e)

    async def publish_schedule(self, valve_id):
        """Publicera ventilens aktuella schema retained till /status."""
        if not (self.enabled and self.connected):
            return  # _connect() publicerar om alla statusar vid nästa anslutning
        entry = self._get_schedule()[str(valve_id)]
        try:
            async with self._lock:
                self._raw_publish(
                    (self._valve_prefix + "valve/%s/schedule/status" % valve_id).encode(),
                    ujson.dumps(entry),
                    retain=True,
                )
        except (OSError, AttributeError) as e:
            # Ingen fara: _connect() publicerar om alla statusar vid nästa anslutning.
            self._mark_down(e)

    # --- huvudloop ---

    async def loop(self, heartbeat=None):
        last_ping = 0
        while True:
            if heartbeat:
                heartbeat()

            if not self.enabled:
                if self.connected:
                    async with self._lock:
                        self._disconnect()
                await asyncio.sleep(1)
                continue

            if not self.connected:
                try:
                    async with self._lock:
                        self._connect()
                    self.connected = True
                    last_ping = 0
                except OSError as e:
                    print("mqtt: anslutning misslyckades (%s), nytt försök om %ds"
                          % (e, RECONNECT_DELAY_S))
                    await asyncio.sleep(RECONNECT_DELAY_S)
                    continue

            try:
                async with self._lock:
                    self._client.check_msg()
            except OSError as e:
                self._mark_down(e)
                continue

            # Tillämpa mottagen huvudbrytare: spara -> eka till /status.
            while self._pending_irrigation:
                payload = self._pending_irrigation.pop(0)
                if not (isinstance(payload, dict) and "enabled" in payload):
                    print("mqtt: ogiltig payload på irrigation/set")
                    continue
                if self._apply_irrigation:
                    self._apply_irrigation(bool(payload["enabled"]))
                await self.publish_irrigation()

            # Tillämpa mottagna /set: validera -> spara -> eka till /status.
            while self._pending_sets:
                valve_id, payload = self._pending_sets.pop(0)
                try:
                    self._apply_entries(valve_id, payload)
                except (ValueError, TypeError) as e:
                    print("mqtt: ogiltigt schema på /set: %s" % e)
                    continue
                await self.publish_schedule(valve_id)

            # Spola historik-outboxen.
            while self._outbox and self.connected:
                topic, payload = self._outbox[0]
                try:
                    async with self._lock:
                        self._raw_publish(topic, payload)
                    self._outbox.pop(0)
                except OSError as e:
                    self._mark_down(e)
                    break

            # umqtt pingar inte själv; håll keepalive vid liv.
            if self.connected:
                now = time.ticks_ms()
                if last_ping == 0 or time.ticks_diff(now, last_ping) > PING_INTERVAL_MS:
                    try:
                        async with self._lock:
                            self._client.ping()
                        last_ping = now
                    except OSError as e:
                        self._mark_down(e)
                        continue

            await asyncio.sleep_ms(200)
