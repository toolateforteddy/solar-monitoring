"""Gateway access tokens.

Gateways on firmware 7 and later require a JWT issued by Enphase. A system
owner's token lasts about a year. `fetch_token` gets one with Enlighten
credentials; `token_expiry` reads its expiry so the collector can warn early.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import requests

LOGIN_URL = "https://enlighten.enphaseenergy.com/login/login.json"
TOKEN_URL = "https://entrez.enphaseenergy.com/tokens"


def token_expiry(token: str) -> datetime | None:
    """Return the token's `exp` claim as an aware UTC datetime, if readable."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
    except (IndexError, KeyError, ValueError, TypeError):
        return None


def days_until_expiry(token: str, now: datetime | None = None) -> float | None:
    expiry = token_expiry(token)
    if expiry is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (expiry - now).total_seconds() / 86400


def fetch_token(email: str, password: str, serial: str, timeout: float = 30) -> str:
    """Log in to Enlighten and request an owner token for the gateway `serial`."""
    with requests.Session() as session:
        login = session.post(
            LOGIN_URL,
            data={"user[email]": email, "user[password]": password},
            timeout=timeout,
        )
        login.raise_for_status()
        session_id = login.json().get("session_id")
        if not session_id:
            raise RuntimeError("Enlighten login failed: check the email and password.")

        resp = session.post(
            TOKEN_URL,
            json={"session_id": session_id, "serial_num": serial, "username": email},
            timeout=timeout,
        )
        resp.raise_for_status()
        token = resp.text.strip()
        if token.count(".") != 2:
            raise RuntimeError("Enphase did not return a token for this gateway serial.")
        return token
