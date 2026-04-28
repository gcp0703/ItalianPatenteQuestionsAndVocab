"""Bearer-token authentication for QuizPatenteB.

Tokens are UUID4 hex strings. We store SHA-256(pepper || token) only.
The plaintext token is shown to the user once at registration, sent on every
request as `Authorization: Bearer <token>`, and never persisted server-side.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import uuid

from fastapi import Header, HTTPException


def _pepper() -> bytes:
    pepper = os.environ.get("AUTH_TOKEN_PEPPER", "")
    if not pepper:
        raise RuntimeError(
            "AUTH_TOKEN_PEPPER is not set. Generate one with "
            "`python -c 'import secrets; print(secrets.token_hex(32))'` "
            "and add it to /etc/quizpatenteb.env."
        )
    return pepper.encode("utf-8")


def hash_token(token: str) -> str:
    """Return hex SHA-256 of (pepper || token). Raises if pepper is unset."""
    h = hashlib.sha256()
    h.update(_pepper())
    h.update(token.encode("utf-8"))
    return h.hexdigest()


def generate_token() -> str:
    """Return a fresh UUID4 hex token (32 chars, 128 bits of entropy)."""
    return uuid.uuid4().hex


def verify_token(token: str, expected_hash: str) -> bool:
    """Constant-time comparison of hash_token(token) and expected_hash."""
    return hmac.compare_digest(hash_token(token), expected_hash)


def parse_bearer(authorization: str | None) -> str | None:
    """Return the token from `Bearer <token>`, or None if absent/malformed."""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def require_user(authorization: str | None = Header(None)) -> str:
    """FastAPI dependency: validate bearer token, return the caller's email."""
    token = parse_bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header.")

    # Lazy import to avoid a circular dependency on backend.app.main at module load.
    from backend.app.main import load_user_registry

    expected_hash = hash_token(token)
    for user in load_user_registry():
        stored = user.get("token_hash")
        if stored and hmac.compare_digest(stored, expected_hash):
            return user["email"]
    raise HTTPException(status_code=401, detail="Invalid token.")
