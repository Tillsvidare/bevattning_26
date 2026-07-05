"""FastAPI-app: API + statisk molnfrontend + MQTT-lyssnare som lifespan-task."""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .database import init_db
from .mqtt import mqtt_listener
from .routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(mqtt_listener())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="Bevattning", lifespan=lifespan)
app.include_router(router)
# Monteras sist så att /api/* vinner över statiska filer.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
