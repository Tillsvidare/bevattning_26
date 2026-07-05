"""SQLite via SQLAlchemy. Greenfield-databas: tabellerna skapas vid start."""

from sqlalchemy import create_engine
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


def init_db() -> None:
    from . import models  # noqa: F401  (registrerar tabellerna på Base)

    Base.metadata.create_all(engine)
