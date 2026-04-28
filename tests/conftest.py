"""Shared pytest fixtures for QuizPatenteB tests.

Every test gets a clean USER_DATA_DIR, deterministic auth secrets, and a fresh
import of `backend.app.main` so module-level path constants pick up the patched
env vars.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Iterator

import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    user_data_dir = tmp_path / "user_data"
    user_data_dir.mkdir()
    monkeypatch.setenv("QPB_USER_DATA_DIR", str(user_data_dir))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-" + "0" * 56)
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", "test-pepper-" + "0" * 52)
    monkeypatch.setenv("QPB_LOAD_DOTENV", "0")
    monkeypatch.setenv("BACKFILL_DEFINITIONS", "false")
    monkeypatch.setenv("BACKFILL_CHECKING", "false")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    yield user_data_dir


@pytest.fixture
def client(isolated_env: Path):
    """FastAPI TestClient with the isolated env applied via a fresh import."""
    for mod in list(sys.modules):
        if mod.startswith("backend.app"):
            del sys.modules[mod]
    from fastapi.testclient import TestClient
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c
