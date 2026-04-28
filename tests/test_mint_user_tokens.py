"""Tests for the bearer-token migration CLI."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run_cli(user_data_dir: Path, pepper: str = "test-pepper") -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "backend.scripts.mint_user_tokens"],
        env={
            "QPB_LOAD_DOTENV": "0",
            "AUTH_TOKEN_PEPPER": pepper,
            "QPB_USER_DATA_DIR": str(user_data_dir),
            "PATH": "/usr/bin:/bin",
        },
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_mints_tokens_for_wrapped_registry(tmp_path: Path):
    """Production format: {"users": [...]}"""
    user_data = tmp_path / "mint_user_data"
    user_data.mkdir()
    registry = user_data / "_users.json"
    registry.write_text(json.dumps({
        "users": [
            {"email": "alice@example.com", "created": "2026-01-01T00:00:00+00:00"},
            {"email": "bob@example.com", "created": "2026-01-02T00:00:00+00:00",
             "token_hash": "preexisting"},
        ]
    }))

    rc, out, err = _run_cli(user_data)
    assert rc == 0, err
    rows = [r for r in out.strip().split("\n") if r]
    assert len(rows) == 1, f"expected one minted row, got {rows}"
    email, token = rows[0].split("\t")
    assert email == "alice@example.com"
    assert len(token) == 32

    saved = json.loads(registry.read_text())
    assert "users" in saved, "wrapped shape must be preserved"
    by_email = {u["email"]: u for u in saved["users"]}
    assert "token_hash" in by_email["alice@example.com"]
    assert by_email["bob@example.com"]["token_hash"] == "preexisting"


def test_mints_tokens_for_bare_list_registry(tmp_path: Path):
    """Resilience: older fixtures stored as bare lists still work."""
    user_data = tmp_path / "mint_user_data"
    user_data.mkdir()
    registry = user_data / "_users.json"
    registry.write_text(json.dumps([
        {"email": "alice@example.com", "created": "2026-01-01T00:00:00+00:00"},
    ]))

    rc, out, err = _run_cli(user_data)
    assert rc == 0, err
    saved = json.loads(registry.read_text())
    assert isinstance(saved, list), "bare-list shape must be preserved"
    assert "token_hash" in saved[0]


def test_idempotent(tmp_path: Path):
    user_data = tmp_path / "mint_user_data"
    user_data.mkdir()
    registry = user_data / "_users.json"
    registry.write_text(json.dumps({"users": [
        {"email": "alice@example.com", "created": "2026-01-01T00:00:00+00:00"},
    ]}))

    rc1, out1, _ = _run_cli(user_data)
    assert rc1 == 0 and out1.strip()
    rc2, out2, _ = _run_cli(user_data)
    assert rc2 == 0 and out2.strip() == "", "second run should mint nothing"
