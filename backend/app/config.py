"""Inställningar från miljövariabler (sätts i docker-compose.yml)."""

import os

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
DB_PATH = os.environ.get("DB_PATH", "./irrigation.db")

# Ventil-id:n som systemet känner till (valve/1 = M1, valve/2 = M2)
VALVE_IDS = (1, 2)
