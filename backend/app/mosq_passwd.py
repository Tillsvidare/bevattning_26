"""Mosquitto-lösenordshantering utan mosquitto_passwd-binären.

Genererar hashar i mosquittos $7$-format (PBKDF2-HMAC-SHA512) och skriver
password_file atomiskt på den delade volymen. En watcher i mosquitto-
containern skickar SIGHUP när filens mtime ändras (fas 2).

Radformat:  username:$7$<iterationer>$<salt b64>$<nyckel b64>
Verifieras explicit mot mosquitto i fas 2; fallback är mosquitto_passwd -b.
"""

import base64
import hashlib
import logging
import os
import secrets
import tempfile

from sqlalchemy import select

from .config import MOSQ_PASSWD_FILE, MQTT_PASSWORD, MQTT_USER

log = logging.getLogger("irrigation.mosq")

# mosquitto_passwd:s default (mosquitto 2.x).
ITERATIONS = 101
SALT_LEN = 12
KEY_LEN = 64


def mosq_hash(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(SALT_LEN)
    key = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, iterations, dklen=KEY_LEN)
    return "$7${}${}${}".format(
        iterations,
        base64.b64encode(salt).decode(),
        base64.b64encode(key).decode(),
    )


def write_passwd_file() -> None:
    """Skriv om hela password_file från DB (+ backend-superusern). Atomiskt:
    temp-fil i samma katalog + os.replace, så mosquitto aldrig ser en halv fil.
    No-op när MOSQ_PASSWD_FILE inte är satt (lokal dev utan broker-auth)."""
    if not MOSQ_PASSWD_FILE:
        return
    from .database import SessionLocal
    from .models import Device

    lines = []
    if MQTT_USER and MQTT_PASSWORD:
        lines.append(f"{MQTT_USER}:{mosq_hash(MQTT_PASSWORD)}")
    with SessionLocal() as session:
        devices = session.scalars(select(Device)).all()
    for device in devices:
        if device.mqtt_password_hash:
            lines.append(f"{device.id}:{device.mqtt_password_hash}")

    directory = os.path.dirname(os.path.abspath(MOSQ_PASSWD_FILE))
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".passwd-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
        # mkstemp ger 600 (ägd av backend-containerns user) — mosquitto kör
        # som egen user och måste kunna läsa filen efter os.replace.
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, MOSQ_PASSWD_FILE)
    except BaseException:
        os.unlink(tmp_path)
        raise
    log.info("passwd-fil skriven: %d konton", len(lines))
