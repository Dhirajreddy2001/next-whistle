"""Small signed-token helpers for anonymous Core sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def issue_access_token(session_id: str, now: int, key: bytes, lifetime_seconds: int) -> tuple[str, int]:
    expires_at = now + lifetime_seconds
    payload = _b64(json.dumps({"sid": session_id, "iat": now, "exp": expires_at}, separators=(",", ":")).encode())
    signature = _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}", expires_at


def read_access_token(token: str, now: int, key: bytes) -> str | None:
    try:
        payload_part, signature = token.split(".", 1)
        expected = _b64(hmac.new(key, payload_part.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        padded = payload_part + "=" * (-len(payload_part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        if not isinstance(payload.get("sid"), str) or int(payload["exp"]) <= now:
            return None
        return payload["sid"]
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
