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
# Topics (en publicerare per topic — inga eko-loopar):
#   valve/{id}/schedule/status      publiceras retained av enheten
#   valve/{id}/schedule/set         prenumereras; tillämpas och ekas till /status
#   valve/{id}/history              ON/OFF med ISO-tidsstämpel från synkad klocka
#   bevattning/irrigation/status    huvudbrytaren, retained {"enabled": bool}
#   bevattning/irrigation/set       prenumereras; tillämpas och ekas till /status
#   bevattning/sensor/status        vattensensorn, retained {"wet": bool} —
#                                   inget /set (read-only, enheten äger läget)

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


IRRIGATION_STATUS = b"bevattning/irrigation/status"
IRRIGATION_SET = b"bevattning/irrigation/set"
SENSOR_STATUS = b"bevattning/sensor/status"


class MqttLink:
    def __init__(self, cfg, get_schedule, apply_entries,
                 get_irrigation=None, apply_irrigation=None, get_sensor=None):
        """get_schedule() -> hela schemat; apply_entries(vid, list) -> validerad lista;
        get_irrigation() -> bool (huvudbrytaren); apply_irrigation(bool) sparar;
        get_sensor() -> bool (vattensensorn våt)."""
        self._host = cfg["mqtt_host"]
        self._port = cfg["mqtt_port"]
        self._get_schedule = get_schedule
        self._apply_entries = apply_entries
        self._get_irrigation = get_irrigation
        self._apply_irrigation = apply_irrigation
        self._get_sensor = get_sensor
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
        if topic == IRRIGATION_SET:
            try:
                self._pending_irrigation.append(ujson.loads(msg))
            except ValueError:
                print("mqtt: irrigation/set med ogiltig JSON")
            return
        parts = topic.decode().split("/")
        if len(parts) == 4 and parts[0] == "valve" and parts[2] == "schedule" and parts[3] == "set":
            valve_id = parts[1]
            try:
                payload = ujson.loads(msg)
            except ValueError:
                print("mqtt: /set med ogiltig JSON")
                return
            self._pending_sets.append((valve_id, payload))

    # --- anslutning ---

    def _connect(self):
        client = MQTTClient(
            self._client_id, self._host, port=self._port, keepalive=60
        )
        client.set_callback(self._on_msg)
        client.set_last_will(b"bevattning/availability", b"offline", retain=True, qos=1)
        # timeout= sätter socket-timeout före TCP-handskakningen, så en död
        # broker inte kan blockera event-loopen längre än så här.
        client.connect(clean_session=True, timeout=SOCKET_TIMEOUT_S)
        self._client = client
        for vid in VALVE_IDS:
            client.subscribe(("valve/%s/schedule/set" % vid).encode(), qos=1)
        if self._get_irrigation:
            client.subscribe(IRRIGATION_SET, qos=1)
        self._raw_publish(b"bevattning/availability", b"online", retain=True)
        # Retained status så backenden får tillståndet direkt vid varje
        # (åter)anslutning: scheman + huvudbrytaren.
        schedule = self._get_schedule()
        for vid in VALVE_IDS:
            self._raw_publish(
                ("valve/%s/schedule/status" % vid).encode(),
                ujson.dumps(schedule[vid]),
                retain=True,
            )
        if self._get_irrigation:
            self._raw_publish(
                IRRIGATION_STATUS,
                ujson.dumps({"enabled": bool(self._get_irrigation())}),
                retain=True,
            )
        if self._get_sensor:
            self._raw_publish(
                SENSOR_STATUS,
                ujson.dumps({"wet": bool(self._get_sensor())}),
                retain=True,
            )
        print("mqtt: ansluten till %s:%d" % (self._host, self._port))

    def _raw_publish(self, topic, payload, retain=False):
        # check_msg() lämnar sockeln blockerande utan timeout; återställ den
        # före varje publish så QoS1-ACK-väntan inte kan hänga för evigt.
        self._client.sock.settimeout(SOCKET_TIMEOUT_S)
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
            self._raw_publish(b"bevattning/availability", b"offline", retain=True)
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
        self._outbox.append((("valve/%s/history" % valve_id).encode(), payload))

    async def publish_irrigation(self):
        """Publicera huvudbrytarens läge retained till irrigation/status."""
        if not (self.enabled and self.connected and self._get_irrigation):
            return  # _connect() publicerar om vid nästa anslutning
        try:
            async with self._lock:
                self._raw_publish(
                    IRRIGATION_STATUS,
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
                    SENSOR_STATUS,
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
                    ("valve/%s/schedule/status" % valve_id).encode(),
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
