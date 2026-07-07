"""Pydantic-scheman för API och MQTT-payloads."""

from datetime import datetime

from pydantic import BaseModel, Field

# Enkel e-postkontroll — räcker för ett invite-only-system, ingen extra
# dependency (pydantics EmailStr kräver email-validator).
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

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


# ---------- Konton & enheter ----------


class RegisterRequest(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=255)
    password: str = Field(min_length=8, max_length=200)
    invite_code: str = Field(min_length=1, max_length=32)


class LoginRequest(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=200)


class UserOut(BaseModel):
    id: int
    email: str
    is_admin: bool


class DeviceOut(BaseModel):
    id: str
    name: str
    online: bool
    last_seen: datetime | None
    irrigation_enabled: bool | None
    sensor_wet: bool | None


class DevicePatch(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ClaimCodeOut(BaseModel):
    code: str
    expires_at: datetime


class ProvisionRequest(BaseModel):
    claim_code: str = Field(min_length=1, max_length=32)
    hw_id: str = Field(min_length=1, max_length=32)
    fw_version: str | None = None


class ProvisionResponse(BaseModel):
    device_id: str
    mqtt_username: str
    mqtt_password: str
    mqtt_host: str
    mqtt_port: int
    mqtt_tls: bool


class InviteOut(BaseModel):
    code: str
    used_by: int | None
    used_at: datetime | None
    created_at: datetime


class PasswordSet(BaseModel):
    password: str = Field(min_length=8, max_length=200)
