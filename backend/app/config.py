"""Inställningar från miljövariabler (sätts i docker-compose.yml / .env)."""

import os

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
# Tomt = anonym anslutning (lokal dev utan broker-auth).
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")

DB_PATH = os.environ.get("DB_PATH", "./irrigation.db")

# Sessionscookiens signeringsnyckel. Tom = osäker dev-fallback (varning loggas).
SECRET_KEY = os.environ.get("SECRET_KEY", "")
# 1 = cookien kräver HTTPS (sätts i produktion bakom Caddy).
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "") == "1"
SESSION_MAX_AGE_S = 30 * 24 * 3600  # 30 dagar

# Adminkontot bootstrappas från dessa om users-tabellen är tom.
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

# Mosquittos password_file på delad volym. Tomt = hanteras ej (lokal dev).
MOSQ_PASSWD_FILE = os.environ.get("MOSQ_PASSWD_FILE", "")

# Vad enheterna får som MQTT-adress vid provisionering.
MQTT_PUBLIC_HOST = os.environ.get("MQTT_PUBLIC_HOST", "localhost")
MQTT_PUBLIC_PORT = int(os.environ.get("MQTT_PUBLIC_PORT", "1883"))
MQTT_PUBLIC_TLS = os.environ.get("MQTT_PUBLIC_TLS", "") == "1"

# Ventil-id:n som systemet känner till (valve/1 = M1, valve/2 = M2)
VALVE_IDS = (1, 2)
