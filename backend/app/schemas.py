"""Pydantic-scheman för API och MQTT-payloads."""

from datetime import datetime

from pydantic import BaseModel, Field

# Max antal bevattningar per ventil och dygn (speglas i esp32/storage.py).
MAX_ENTRIES = 6


class ScheduleEntry(BaseModel):
    """En bevattning: starttid + varaktighet. En ventils schema är en lista
    av dessa (0-6 poster) — samma form i schedule.json på enheten och på
    MQTT-topics valve/{id}/schedule/status och valve/{id}/schedule/set.
    """

    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", examples=["06:30"])
    duration_min: int = Field(ge=1, le=180)
    enabled: bool


class HistoryEvent(BaseModel):
    ts: datetime
    state: str  # "ON" | "OFF"


class IrrigationState(BaseModel):
    """Huvudbrytaren: av stänger all bevattning (och avbryter pågående)."""

    enabled: bool


class SensorState(BaseModel):
    """Vattensensorn: våt stoppar bevattningen tills nästa schemalagda start
    efter att den torkat. Read-only — enheten äger läget."""

    wet: bool
