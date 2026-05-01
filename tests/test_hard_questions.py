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


def test_get_hard_questions_handles_corrupt_non_list(client, isolated_env):
    """A non-list value at tracking.hard_questions must yield an empty response, not iterate characters."""
    token = _register(client, "alice@example.com")
    path = _user_file(isolated_env, "alice@example.com")
    data = json.loads(path.read_text())
    data["tracking"]["hard_questions"] = "42"  # corrupt: string, not list
    path.write_text(json.dumps(data))
    r = client.get("/api/quiz/hard-questions", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {"hard_question_ids": []}


def test_put_hard_question_requires_auth(client):
    r = client.put("/api/quiz/hard-questions/1", json={"hard": True})
    assert r.status_code == 401


def test_put_hard_question_adds_id(client, isolated_env):
    token = _register(client, "alice@example.com")
    r = client.put(
        "/api/quiz/hard-questions/1",
        headers=_auth(token),
        json={"hard": True},
    )
    assert r.status_code == 204

    r = client.get("/api/quiz/hard-questions", headers=_auth(token))
    assert r.json()["hard_question_ids"] == [1]


def test_put_hard_question_removes_id(client):
    token = _register(client, "alice@example.com")
    client.put("/api/quiz/hard-questions/1", headers=_auth(token), json={"hard": True})
    client.put("/api/quiz/hard-questions/2", headers=_auth(token), json={"hard": True})

    r = client.put(
        "/api/quiz/hard-questions/1",
        headers=_auth(token),
        json={"hard": False},
    )
    assert r.status_code == 204

    r = client.get("/api/quiz/hard-questions", headers=_auth(token))
    assert r.json()["hard_question_ids"] == [2]


def test_put_hard_question_idempotent(client):
    token = _register(client, "alice@example.com")
    # Adding twice is a no-op.
    r1 = client.put("/api/quiz/hard-questions/5", headers=_auth(token), json={"hard": True})
    r2 = client.put("/api/quiz/hard-questions/5", headers=_auth(token), json={"hard": True})
    assert r1.status_code == 204
    assert r2.status_code == 204

    r = client.get("/api/quiz/hard-questions", headers=_auth(token))
    assert r.json()["hard_question_ids"] == [5]

    # Removing a not-present id is also a no-op.
    r3 = client.put("/api/quiz/hard-questions/999", headers=_auth(token), json={"hard": False})
    assert r3.status_code == 204


def test_put_hard_question_unknown_id_returns_404(client):
    token = _register(client, "alice@example.com")
    # Question IDs are 1..7139 in the bundled bank. Pick one safely out of range.
    r = client.put(
        "/api/quiz/hard-questions/99999999",
        headers=_auth(token),
        json={"hard": True},
    )
    assert r.status_code == 404


def test_put_hard_question_rejects_empty_body(client):
    token = _register(client, "alice@example.com")
    r = client.put("/api/quiz/hard-questions/1", headers=_auth(token), json={})
    assert r.status_code == 422


def test_put_hard_question_rejects_null_hard(client):
    token = _register(client, "alice@example.com")
    r = client.put(
        "/api/quiz/hard-questions/1",
        headers=_auth(token),
        json={"hard": None},
    )
    assert r.status_code == 422


def test_put_hard_question_rejects_string_hard(client):
    """StrictBool must reject string-typed booleans like "yes" or "true"."""
    token = _register(client, "alice@example.com")
    r = client.put(
        "/api/quiz/hard-questions/1",
        headers=_auth(token),
        json={"hard": "yes"},
    )
    assert r.status_code == 422


def test_get_hard_quiz_requires_auth(client):
    r = client.get("/api/quiz/hard")
    assert r.status_code == 401


def test_get_hard_quiz_empty_set_returns_409(client):
    token = _register(client, "alice@example.com")
    r = client.get("/api/quiz/hard", headers=_auth(token))
    assert r.status_code == 409
    assert r.json()["detail"] == "no_hard_questions"


def test_get_hard_quiz_pads_with_fillers(client):
    token = _register(client, "alice@example.com")
    hard_ids = [1, 2, 3, 4, 5]
    for qid in hard_ids:
        client.put(f"/api/quiz/hard-questions/{qid}", headers=_auth(token), json={"hard": True})

    r = client.get("/api/quiz/hard?count=10", headers=_auth(token))
    assert r.status_code == 200
    data = r.json()
    returned_ids = [q["id"] for q in data["questions"]]
    assert len(returned_ids) == 10
    assert len(set(returned_ids)) == 10  # no duplicates
    # All Hard ids must be present.
    assert set(hard_ids) <= set(returned_ids)


def test_get_hard_quiz_samples_when_set_exceeds_count(client):
    token = _register(client, "alice@example.com")
    # Mark 20 hard, ask for 5.
    for qid in range(1, 21):
        client.put(f"/api/quiz/hard-questions/{qid}", headers=_auth(token), json={"hard": True})

    r = client.get("/api/quiz/hard?count=5", headers=_auth(token))
    assert r.status_code == 200
    returned_ids = [q["id"] for q in r.json()["questions"]]
    assert len(returned_ids) == 5
    assert len(set(returned_ids)) == 5
    # Every returned id must be from the Hard set.
    assert set(returned_ids) <= set(range(1, 21))


def test_get_hard_quiz_filters_unknown_ids(client, isolated_env):
    token = _register(client, "alice@example.com")
    # Seed user file with one valid id and one bogus id.
    path = _user_file(isolated_env, "alice@example.com")
    data = json.loads(path.read_text())
    data["tracking"]["hard_questions"] = [1, 99999999]
    path.write_text(json.dumps(data))

    r = client.get("/api/quiz/hard?count=3", headers=_auth(token))
    assert r.status_code == 200
    returned_ids = [q["id"] for q in r.json()["questions"]]
    assert len(returned_ids) == 3
    # The valid Hard id must be present; the bogus id must not.
    assert 1 in returned_ids
    assert 99999999 not in returned_ids


def test_get_hard_quiz_exact_count_match(client):
    """When the Hard set size exactly matches count, return all Hard with no fillers."""
    token = _register(client, "alice@example.com")
    hard_ids = [1, 2, 3, 4, 5]
    for qid in hard_ids:
        client.put(f"/api/quiz/hard-questions/{qid}", headers=_auth(token), json={"hard": True})

    r = client.get("/api/quiz/hard?count=5", headers=_auth(token))
    assert r.status_code == 200
    returned_ids = [q["id"] for q in r.json()["questions"]]
    assert sorted(returned_ids) == [1, 2, 3, 4, 5]
