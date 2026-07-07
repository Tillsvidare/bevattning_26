"""FastAPI-app: API + statisk molnfrontend + MQTT-lyssnare som lifespan-task."""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from . import mosq_passwd, provision, routes, routes_admin, routes_auth
from .auth import hash_password
from .config import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    COOKIE_SECURE,
    SECRET_KEY,
    SESSION_MAX_AGE_S,
)
from .database import SessionLocal, init_db
from .models import User
from .mqtt import mqtt_listener

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("irrigation.main")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _bootstrap_admin() -> None:
    """Skapa adminkontot från env om users-tabellen är tom."""
    if not (ADMIN_EMAIL and ADMIN_PASSWORD):
        return
    with SessionLocal() as session:
        if session.scalar(select(User).limit(1)) is not None:
            return
        session.add(
            User(
                email=ADMIN_EMAIL.strip().lower(),
                password_hash=hash_password(ADMIN_PASSWORD),
                is_admin=True,
            )
        )
        session.commit()
    log.info("adminkonto skapat: %s", ADMIN_EMAIL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _bootstrap_admin()
    mosq_passwd.write_passwd_file()  # synka broker-konton från DB vid start
    task = asyncio.create_task(mqtt_listener())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="Bevattning", lifespan=lifespan)

if not SECRET_KEY:
    log.warning("SECRET_KEY saknas — kör med osäker dev-nyckel (sätt i .env i prod!)")
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY or "dev-nyckel-osaker",
    max_age=SESSION_MAX_AGE_S,
    same_site="lax",
    https_only=COOKIE_SECURE,
)

app.include_router(routes.router)
app.include_router(routes_auth.router)
app.include_router(routes_admin.router)
app.include_router(provision.router)
# Monteras sist så att /api/* vinner över statiska filer.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
