"""Admin-endpoints: inbjudningskoder, användarlista, lösenordsåterställning.

Glömt lösenord hanteras av ägaren här (ingen SMTP) — se planens auth-beslut.
"""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from .auth import get_admin_user, hash_password
from .database import SessionLocal
from .models import Device, InviteCode, User
from .schemas import InviteOut, PasswordSet

router = APIRouter(prefix="/api/admin", dependencies=[Depends(get_admin_user)])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.post("/invites", response_model=InviteOut)
async def create_invite(admin: User = Depends(get_admin_user)) -> InviteCode:
    invite = InviteCode(
        code=secrets.token_hex(4).upper(), created_by=admin.id
    )
    with SessionLocal() as session:
        session.add(invite)
        session.commit()
    return invite


@router.get("/invites", response_model=list[InviteOut])
async def list_invites() -> list[InviteCode]:
    with SessionLocal() as session:
        invites = session.scalars(
            select(InviteCode).order_by(InviteCode.created_at.desc())
        ).all()
    return list(invites)


@router.get("/users")
async def list_users() -> list[dict]:
    with SessionLocal() as session:
        users = session.scalars(select(User).order_by(User.created_at)).all()
        devices = session.scalars(select(Device)).all()
    count = {}
    for d in devices:
        count[d.user_id] = count.get(d.user_id, 0) + 1
    return [
        {
            "id": u.id,
            "email": u.email,
            "is_admin": u.is_admin,
            "created_at": u.created_at,
            "device_count": count.get(u.id, 0),
        }
        for u in users
    ]


@router.post("/users/{user_id}/password")
async def set_user_password(user_id: int, body: PasswordSet) -> dict:
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="Okänd användare")
        user.password_hash = hash_password(body.password)
        session.commit()
    return {"status": "lösenord bytt", "email": user.email}


@router.get("/devices")
async def list_all_devices() -> list[dict]:
    with SessionLocal() as session:
        rows = session.execute(
            select(Device, User.email).join(User, Device.user_id == User.id)
        ).all()
    return [
        {
            "id": d.id,
            "name": d.name,
            "owner": email,
            "hw_id": d.hw_id,
            "online": d.online,
            "last_seen": d.last_seen,
            "created_at": d.created_at,
        }
        for d, email in rows
    ]
