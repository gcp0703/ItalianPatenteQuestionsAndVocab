"""Tests for per-user Hard question marking and Hard quiz mode."""
from __future__ import annotations

import json
from pathlib import Path


def _register(client, email: str) -> str:
    r = client.post("/api/users", json={"email": email})
    assert r.status_code == 201, r.text
    return r.json()["token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _user_file(isolated_env: Path, email: str) -> Path:
    safe = email.replace("@", "_at_").replace(".", "_dot_")
    return isolated_env / f"{safe}.json"


def test_empty_user_data_includes_hard_questions(client, isolated_env):
    """Newly registered user file has tracking.hard_questions = []."""
    _register(client, "alice@example.com")
    path = _user_file(isolated_env, "alice@example.com")
    data = json.loads(path.read_text())
    assert data["tracking"]["hard_questions"] == []


def test_vocab_tracking_sync_preserves_hard_questions(client, isolated_env):
    """Syncing vocab tracking must not wipe tracking.hard_questions."""
    token = _register(client, "alice@example.com")

    # Seed the user file with a hard_questions list.
    path = _user_file(isolated_env, "alice@example.com")
    data = json.loads(path.read_text())
    data["tracking"]["hard_questions"] = [1, 2, 3]
    path.write_text(json.dumps(data))

    # Trigger a vocab tracking sync (which historically overwrote `tracking`).
    r = client.post(
        "/api/vocab/tracking",
        headers=_auth(token),
        json={"feedback_counts": {}, "hidden_words": [], "difficult_words": []},
    )
    assert r.status_code == 200, r.text

    after = json.loads(path.read_text())
    assert after["tracking"]["hard_questions"] == [1, 2, 3]


def test_migrate_preserves_hard_questions(client, isolated_env):
    """Calling /api/migrate must not wipe tracking.hard_questions."""
    token = _register(client, "alice@example.com")

    # Seed the user file with a hard_questions list.
    path = _user_file(isolated_env, "alice@example.com")
    data = json.loads(path.read_text())
    data["tracking"]["hard_questions"] = [11, 22, 33]
    path.write_text(json.dumps(data))

    r = client.post("/api/migrate", headers=_auth(token))
    assert r.status_code == 200, r.text

    after = json.loads(path.read_text())
    assert after["tracking"]["hard_questions"] == [11, 22, 33]


def test_get_hard_questions_requires_auth(client):
    r = client.get("/api/quiz/hard-questions")
    assert r.status_code == 401


def test_get_hard_questions_empty_for_new_user(client):
    token = _register(client, "alice@example.com")
    r = client.get("/api/quiz/hard-questions", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {"hard_question_ids": []}


def test_get_hard_questions_returns_persisted_set(client, isolated_env):
    token = _register(client, "alice@example.com")

    # Seed the user file directly.
    path = _user_file(isolated_env, "alice@example.com")
    data = json.loads(path.read_text())
    data["tracking"]["hard_questions"] = [42, 138, 405]
    path.write_text(json.dumps(data))

    r = client.get("/api/quiz/hard-questions", headers=_auth(token))
    assert r.status_code == 200
    assert sorted(r.json()["hard_question_ids"]) == [42, 138, 405]
