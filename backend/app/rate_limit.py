"""slowapi limiter setup. Keys requests by bearer token if present, otherwise
by client IP (X-Forwarded-For aware so nginx-proxied traffic is per-client)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

if TYPE_CHECKING:
    from starlette.requests import Request


def _key(request: "Request") -> str:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return f"bearer:{auth.split(None, 1)[1].strip()}"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=_key, default_limits=["60/minute"])


__all__ = ["limiter", "RateLimitExceeded"]
