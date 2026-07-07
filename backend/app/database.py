"""SQLite via SQLAlchemy. Tabellerna skapas vid start; ingen Alembic —
enstaka schemaändringar görs med handskrivna ALTER TABLE i _migrate()."""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DB_PATH


class Base(DeclarativeBase):
    pass


# check_same_thread=False: sessioner används från FastAPI:s trådpool
# och från MQTT-lyssnartasken.
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _migrate() -> None:
    """Handskrivna migreringar för databaser skapade före multi-tenant."""
    with engine.connect() as conn:
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(valve_events)"))]
        if cols and "device_id" not in cols:
            conn.execute(text("ALTER TABLE valve_events ADD COLUMN device_id TEXT"))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_device_valve_ts "
                "ON valve_events (device_id, valve_id, ts)"
            ))
            conn.commit()


def init_db() -> None:
    from . import models  # noqa: F401  (registrerar tabellerna på Base)

    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
    _migrate()
    Base.metadata.create_all(engine)
