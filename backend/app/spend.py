"""Anthropic cost telemetry and soft monthly cap.

Pricing for claude-haiku-4-5 (USD per 1M tokens): $1 input, $5 output.
Update the constants below if pricing changes.

We track an in-memory total per (year, month). Process restarts reset to
zero — set the *hard* monthly cap in the Anthropic console (Settings →
Limits) for durable enforcement. The soft cap here just stops calling
Claude when the in-memory total crosses ANTHROPIC_MONTHLY_USD_CAP.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger("uvicorn.error")

CLAUDE_HAIKU_INPUT_USD_PER_TOKEN = 1.0 / 1_000_000
CLAUDE_HAIKU_OUTPUT_USD_PER_TOKEN = 5.0 / 1_000_000

_lock = threading.Lock()
_state: dict[str, float | str] = {"month_key": "", "total_usd": 0.0}


def _current_month_key() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def reset_for_test() -> None:
    with _lock:
        _state["month_key"] = _current_month_key()
        _state["total_usd"] = 0.0


def record_claude_call(input_tokens: int, output_tokens: int, model: str = "claude-haiku-4-5") -> float:
    """Add the call's USD cost to the monthly total, log it, return the cost."""
    cost = (
        input_tokens * CLAUDE_HAIKU_INPUT_USD_PER_TOKEN
        + output_tokens * CLAUDE_HAIKU_OUTPUT_USD_PER_TOKEN
    )
    key = _current_month_key()
    with _lock:
        if _state["month_key"] != key:
            _state["month_key"] = key
            _state["total_usd"] = 0.0
        _state["total_usd"] = float(_state["total_usd"]) + cost
        running_total = float(_state["total_usd"])

    logger.info(
        "ANTHROPIC_CALL model=%s in=%d out=%d cost_usd=%.6f month=%s month_total_usd=%.4f",
        model, input_tokens, output_tokens, cost, key, running_total,
    )
    return cost


def month_total_usd() -> float:
    with _lock:
        if _state["month_key"] != _current_month_key():
            return 0.0
        return float(_state["total_usd"])


def is_over_cap() -> bool:
    cap_str = os.environ.get("ANTHROPIC_MONTHLY_USD_CAP", "").strip()
    if not cap_str:
        return False
    try:
        cap = float(cap_str)
    except ValueError:
        logger.warning("ANTHROPIC_MONTHLY_USD_CAP=%r is not a number; ignoring.", cap_str)
        return False
    return month_total_usd() >= cap
