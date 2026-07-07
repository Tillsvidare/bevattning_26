"""Lösenordshashning (bcrypt) och sessions-dependencies.

Sessionen är en signerad cookie (Starlette SessionMiddleware) med user_id.
Ingen JWT, ingen auth-lib — se planens arkitekturbeslut.
"""

import time

import bcrypt
from fastapi import Depends, HTTPException, Request

from .database import SessionLocal
from .models import Device, User

# Inloggnings-rate-limit i minnet: 5 fel på rad låser e-postadressen i 60 s.
MAX_FAILURES = 5
LOCKOUT_S = 60
_failures: dict[str, list] = {}  # email -> [antal, låst-till-monotonic]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def check_login_allowed(email: str) -> None:
    entry = _failures.get(email)
    if entry and entry[0] >= MAX_FAILURES:
        if time.monotonic() < entry[1]:
            raise HTTPException(
                status_code=429,
                detail="För många misslyckade försök — vänta en minut.",
            )
        del _failures[email]


def record_login_failure(email: str) -> None:
    entry = _failures.setdefault(email, [0, 0.0])
    entry[0] += 1
    if entry[0] >= MAX_FAILURES:
        entry[1] = time.monotonic() + LOCKOUT_S


def clear_login_failures(email: str) -> None:
    _failures.pop(email, None)


def get_current_user(request: Request) -> User:
    user_id = request.session.get("user_id")
    if user_id is not None:
        with SessionLocal() as session:
            user = session.get(User, user_id)
        if user is not None:
            return user
        request.session.clear()  # kontot borttaget — döda sessionen
    raise HTTPException(status_code=401, detail="Inte inloggad")


def get_admin_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        # 404 istället för 403: adminsidans existens ska inte läcka.
        raise HTTPException(status_code=404, detail="Hittades inte")
    return user


def owned_device(device_id: str, user: User = Depends(get_current_user)) -> Device:
    """Enhet som ägs av den inloggade användaren; 404 för andras/okända
    (existensen av andras enheter ska inte läcka)."""
    with SessionLocal() as session:
        device = session.get(Device, device_id)
    if device is None or device.user_id != user.id:
        raise HTTPException(status_code=404, detail=f"Okänd enhet: {device_id}")
    return device
