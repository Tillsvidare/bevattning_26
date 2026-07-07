"""Databasmodeller: användare, inbjudningar, enheter och ventilhistorik."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(100), nullable=False)  # bcrypt
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<User {self.email}{' (admin)' if self.is_admin else ''}>"


class InviteCode(Base):
    """Engångskod för registrering, utfärdad av admin."""

    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )


class ClaimCode(Base):
    """Engångskod (24 h) som kopplar en fysisk enhet till ett konto."""

    __tablename__ = "claim_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    device_id: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )


class Device(Base):
    """En fysisk bevattningsenhet. id ("bv-xxxxxxxx") är även MQTT-username
    och client-id. Tillståndskolumnerna write-through:as från MQTT-status
    (NULL = enheten har inte rapporterat ännu) — API:t läser bara härifrån."""

    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="Bevattning")
    hw_id: Mapped[str | None] = mapped_column(String(32), unique=True)  # machine.unique_id hex
    mqtt_password_hash: Mapped[str | None] = mapped_column(String(255))  # mosquitto $7$
    schedules_json: Mapped[str | None] = mapped_column(Text)  # {"1": [entries], ...}
    irrigation_enabled: Mapped[bool | None] = mapped_column(Boolean)
    sensor_wet: Mapped[bool | None] = mapped_column(Boolean)
    online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<Device {self.id} user={self.user_id} {'online' if self.online else 'offline'}>"


class ValveEvent(Base):
    """En ON/OFF-händelse rapporterad av enheten via devices/{id}/valve/{n}/history."""

    __tablename__ = "valve_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Nullable: rader från tiden före multi-tenant saknar enhet tills de
    # importerats med tools/import_legacy_events.py.
    device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.id"))
    valve_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(3), nullable=False)  # "ON" | "OFF"
    # Enhetens tidsstämpel (UTC, naiv) från den NTP-synkade klockan
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    # När backenden tog emot meddelandet (UTC, naiv)
    received_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )

    __table_args__ = (
        Index("ix_valve_ts", "valve_id", "ts"),
        Index("ix_device_valve_ts", "device_id", "valve_id", "ts"),
    )

    def __repr__(self) -> str:
        return f"<ValveEvent {self.device_id} valve={self.valve_id} {self.state} @ {self.ts}>"
