"""Kör backenden lokalt på Windows utan Docker.

aiomqtt kräver en selector-eventloop, men uvicorn väljer själv
ProactorEventLoop på Windows (uvicorn/loops/asyncio.py) — därför körs
servern här med en egen loop-factory. I Docker (Linux) behövs inget av
detta: kör `docker compose up` istället.

Seedar dev-data vid start (idempotent):
  admin dev@example.com / devlosen01, en för-claimad enhet bv-dev001
  (matchar simulate_device.py:s default) och inbjudningskoden DEVKOD01
  för att testa registrering av en andra användare.

    cd backend && python ../tools/run_dev.py
"""

import asyncio
import os
import sys

# Python lägger skriptets katalog (tools/) på sys.path, inte cwd —
# "app.main" finns i backend/ som skriptet ska köras ifrån.
sys.path.insert(0, os.getcwd())

# Stabil dev-nyckel så inloggningen överlever omstarter.
os.environ.setdefault("SECRET_KEY", "dev-nyckel-osaker")

import uvicorn

DEV_ADMIN_EMAIL = "dev@example.com"
DEV_ADMIN_PASSWORD = "devlosen01"
DEV_DEVICE_ID = "bv-dev001"
DEV_INVITE_CODE = "DEVKOD01"


def seed_dev_data() -> None:
    from sqlalchemy import select

    from app.auth import hash_password
    from app.database import SessionLocal, init_db
    from app.models import Device, InviteCode, User

    init_db()
    with SessionLocal() as session:
        admin = session.scalar(select(User).where(User.email == DEV_ADMIN_EMAIL))
        if admin is None:
            admin = User(
                email=DEV_ADMIN_EMAIL,
                password_hash=hash_password(DEV_ADMIN_PASSWORD),
                is_admin=True,
            )
            session.add(admin)
            session.flush()
        if session.get(Device, DEV_DEVICE_ID) is None:
            session.add(
                Device(id=DEV_DEVICE_ID, user_id=admin.id, name="Dev-enheten")
            )
        if session.scalar(
            select(InviteCode).where(InviteCode.code == DEV_INVITE_CODE)
        ) is None:
            session.add(InviteCode(code=DEV_INVITE_CODE, created_by=admin.id))
        session.commit()
    print(f"dev-login: {DEV_ADMIN_EMAIL} / {DEV_ADMIN_PASSWORD}")
    print(f"dev-enhet: {DEV_DEVICE_ID}   inbjudningskod: {DEV_INVITE_CODE}")


seed_dev_data()

# HOST=0.0.0.0 exponerar servern på LAN (t.ex. för test i mobilen).
config = uvicorn.Config("app.main:app",
                        host=os.environ.get("HOST", "127.0.0.1"), port=8000)
server = uvicorn.Server(config)

if sys.platform == "win32":
    asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
else:
    asyncio.run(server.serve())
