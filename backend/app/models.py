"""Databasmodell för ventilhistorik."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ValveEvent(Base):
    """En ON/OFF-händelse rapporterad av enheten via valve/{id}/history."""

    __tablename__ = "valve_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    valve_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(3), nullable=False)  # "ON" | "OFF"
    # Enhetens tidsstämpel (UTC, naiv) från den NTP-synkade klockan
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    # När backenden tog emot meddelandet (UTC, naiv)
    received_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )

    __table_args__ = (Index("ix_valve_ts", "valve_id", "ts"),)

    def __repr__(self) -> str:
        return f"<ValveEvent valve={self.valve_id} {self.state} @ {self.ts}>"
