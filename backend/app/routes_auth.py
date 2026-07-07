"""Konto-endpoints: registrering (med inbjudningskod), inloggning, session."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from .auth import (
    check_login_allowed,
    clear_login_failures,
    get_current_user,
    hash_password,
    record_login_failure,
    verify_password,
)
from .database import SessionLocal
from .models import InviteCode, User
from .schemas import LoginRequest, RegisterRequest, UserOut

log = logging.getLogger("irrigation.auth")

router = APIRouter(prefix="/api/auth")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.post("/register", response_model=UserOut)
async def register(body: RegisterRequest, request: Request) -> User:
    email = body.email.strip().lower()
    code = body.invite_code.strip().upper()
    with SessionLocal() as session:
        invite = session.scalar(select(InviteCode).where(InviteCode.code == code))
        if invite is None or invite.used_by is not None:
            raise HTTPException(status_code=400, detail="Ogiltig inbjudningskod")
        if session.scalar(select(User).where(User.email == email)) is not None:
            raise HTTPException(
                status_code=400, detail="Det finns redan ett konto med den adressen"
            )
        user = User(email=email, password_hash=hash_password(body.password))
        session.add(user)
        session.flush()
        invite.used_by = user.id
        invite.used_at = _utcnow()
        session.commit()
    request.session["user_id"] = user.id
    log.info("nytt konto: %s (inbjudan %s)", email, code)
    return user


@router.post("/login", response_model=UserOut)
async def login(body: LoginRequest, request: Request) -> User:
    email = body.email.strip().lower()
    check_login_allowed(email)
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(body.password, user.password_hash):
        record_login_failure(email)
        raise HTTPException(status_code=401, detail="Fel e-post eller lösenord")
    clear_login_failures(email)
    request.session["user_id"] = user.id
    return user


@router.post("/logout")
async def logout(request: Request) -> dict:
    request.session.clear()
    return {"status": "utloggad"}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
