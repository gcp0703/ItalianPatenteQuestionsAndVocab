"""Tests for per-user custom vocabulary words."""
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


def test_empty_user_data_includes_custom_vocab(client, isolated_env):
    """Newly registered user file has custom_vocab = {}."""
    _register(client, "alice@example.com")
    path = _user_file(isolated_env, "alice@example.com")
    data = json.loads(path.read_text())
    assert data["custom_vocab"] == {}


def test_normalize_custom_vocab_input_strips_and_lowercases():
    from backend.app.main import _normalize_custom_vocab_input
    word, reason = _normalize_custom_vocab_input("  CIAO  ")
    assert word == "ciao"
    assert reason is None


def test_normalize_custom_vocab_input_collapses_whitespace():
    from backend.app.main import _normalize_custom_vocab_input
    word, reason = _normalize_custom_vocab_input("diritto   di  precedenza")
    assert word == "diritto di precedenza"
    assert reason is None


def test_normalize_custom_vocab_input_allows_apostrophes_and_hyphens():
    from backend.app.main import _normalize_custom_vocab_input
    for ok in ("l'auto", "stop-and-go", "passaggio a livello"):
        word, reason = _normalize_custom_vocab_input(ok)
        assert reason is None, f"{ok!r} should be valid"
        assert word


def test_normalize_custom_vocab_input_rejects_empty():
    from backend.app.main import _normalize_custom_vocab_input
    word, reason = _normalize_custom_vocab_input("   ")
    assert word is None
    assert reason == "empty"


def test_normalize_custom_vocab_input_rejects_too_long():
    from backend.app.main import _normalize_custom_vocab_input
    word, reason = _normalize_custom_vocab_input("a" * 61)
    assert word is None
    assert reason == "too_long"


def test_normalize_custom_vocab_input_rejects_invalid_chars():
    from backend.app.main import _normalize_custom_vocab_input
    for bad in ("hello!", "123", "<script>", "word.dot"):
        word, reason = _normalize_custom_vocab_input(bad)
        assert word is None, f"{bad!r} should be invalid"
        assert reason == "invalid_chars"


def test_normalize_custom_vocab_input_nfc_normalizes():
    """Combining characters get normalized to single codepoints."""
    from backend.app.main import _normalize_custom_vocab_input
    # "café" with combining acute (U+0065 U+0301) → "café" with precomposed (U+00E9)
    decomposed = "caf\u0065\u0301"  # e + combining acute = NFD
    precomposed = "caf\u00e9"        # precomposed é = NFC
    # Sanity check: the two strings differ at the byte/codepoint level.
    # If this fails, the literals collapsed on save and the test below
    # would be a tautology — see the code review of commit 24ff4bc.
    assert decomposed != precomposed
    word, reason = _normalize_custom_vocab_input(decomposed)
    assert reason is None
    assert word == precomposed


def test_add_custom_vocab_requires_auth(client):
    r = client.post("/api/vocab/custom", json={"input": "ciao"})
    assert r.status_code == 401


def test_add_custom_vocab_single_word(client, isolated_env):
    token = _register(client, "alice@example.com")
    r = client.post(
        "/api/vocab/custom",
        headers=_auth(token),
        json={"input": "ciao"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] == ["ciao"]
    assert body["skipped"] == []

    data = json.loads(_user_file(isolated_env, "alice@example.com").read_text())
    assert "ciao" in data["custom_vocab"]
    entry = data["custom_vocab"]["ciao"]
    assert entry["english"] == ""
    assert entry["ai_definition"] is None
    assert entry["dictionary_cache"] is None
    assert entry["ai_definition_failed"] is False
    assert "added_at" in entry  # ISO timestamp string


def test_add_custom_vocab_multiple_comma_separated(client):
    token = _register(client, "alice@example.com")
    r = client.post(
        "/api/vocab/custom",
        headers=_auth(token),
        json={"input": "ciao, diritto di precedenza, semaforo"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # `semaforo` is in the curated bank, so it should be skipped.
    assert sorted(body["added"]) == ["ciao", "diritto di precedenza"]
    skipped_inputs = {s["input"]: s["reason"] for s in body["skipped"]}
    assert skipped_inputs == {"semaforo": "already_in_bank"}


def test_add_custom_vocab_normalizes_input(client, isolated_env):
    token = _register(client, "alice@example.com")
    r = client.post(
        "/api/vocab/custom",
        headers=_auth(token),
        json={"input": "  CIAO  "},
    )
    assert r.status_code == 200, r.text
    assert r.json()["added"] == ["ciao"]
    data = json.loads(_user_file(isolated_env, "alice@example.com").read_text())
    assert "ciao" in data["custom_vocab"]


def test_add_custom_vocab_collapses_whitespace(client, isolated_env):
    token = _register(client, "alice@example.com")
    r = client.post(
        "/api/vocab/custom",
        headers=_auth(token),
        json={"input": "diritto   di  precedenza"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["added"] == ["diritto di precedenza"]
    data = json.loads(_user_file(isolated_env, "alice@example.com").read_text())
    assert "diritto di precedenza" in data["custom_vocab"]


def test_add_custom_vocab_skips_duplicate_custom(client):
    token = _register(client, "alice@example.com")
    client.post("/api/vocab/custom", headers=_auth(token), json={"input": "ciao"})
    r = client.post("/api/vocab/custom", headers=_auth(token), json={"input": "ciao"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] == []
    assert body["skipped"] == [{"input": "ciao", "reason": "already_custom"}]


def test_add_custom_vocab_invalid_entries(client):
    token = _register(client, "alice@example.com")
    r = client.post(
        "/api/vocab/custom",
        headers=_auth(token),
        json={"input": "hello!, , " + "x" * 70 + ", ok"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] == ["ok"]
    skipped = {s["input"]: s["reason"] for s in body["skipped"]}
    assert skipped["hello!"] == "invalid_chars"
    # Empty-after-trim entries are silently dropped (not in skipped).
    assert "" not in skipped
    long_input = "x" * 70
    assert skipped[long_input] == "too_long"


def test_add_custom_vocab_dedupes_within_same_request(client):
    token = _register(client, "alice@example.com")
    r = client.post(
        "/api/vocab/custom",
        headers=_auth(token),
        json={"input": "ciao, CIAO, ciao"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] == ["ciao"]
    # The 2nd and 3rd `ciao` are duplicates within the same request,
    # not duplicates of stored data — they're surfaced as already_custom
    # because the first was inserted before they were checked.
    assert all(s["reason"] == "already_custom" for s in body["skipped"])
    assert len(body["skipped"]) == 2


def test_add_custom_vocab_per_user_isolation(client, isolated_env):
    token_a = _register(client, "alice@example.com")
    token_b = _register(client, "bob@example.com")
    client.post("/api/vocab/custom", headers=_auth(token_a), json={"input": "ciao"})
    data_b = json.loads(_user_file(isolated_env, "bob@example.com").read_text())
    assert data_b["custom_vocab"] == {}


def test_delete_custom_vocab_requires_auth(client):
    r = client.delete("/api/vocab/custom/ciao")
    assert r.status_code == 401


def test_delete_custom_vocab_removes_entry(client, isolated_env):
    token = _register(client, "alice@example.com")
    client.post("/api/vocab/custom", headers=_auth(token), json={"input": "ciao"})
    r = client.delete("/api/vocab/custom/ciao", headers=_auth(token))
    assert r.status_code == 204, r.text
    data = json.loads(_user_file(isolated_env, "alice@example.com").read_text())
    assert "ciao" not in data["custom_vocab"]


def test_delete_custom_vocab_normalizes_path(client, isolated_env):
    token = _register(client, "alice@example.com")
    client.post("/api/vocab/custom", headers=_auth(token), json={"input": "ciao"})
    r = client.delete("/api/vocab/custom/CIAO", headers=_auth(token))
    assert r.status_code == 204, r.text
    data = json.loads(_user_file(isolated_env, "alice@example.com").read_text())
    assert "ciao" not in data["custom_vocab"]


def test_delete_custom_vocab_404_when_missing(client):
    token = _register(client, "alice@example.com")
    r = client.delete("/api/vocab/custom/nopesuchword", headers=_auth(token))
    assert r.status_code == 404


def test_delete_custom_vocab_cleans_tracking(client, isolated_env):
    """Deleting a custom word also removes its tracking entries."""
    token = _register(client, "alice@example.com")
    client.post("/api/vocab/custom", headers=_auth(token), json={"input": "ciao"})

    # Seed tracking data for "ciao" through the tracking endpoint.
    client.post(
        "/api/vocab/tracking",
        headers=_auth(token),
        json={
            "feedback_counts": {"ciao": {"up": 2, "down": 1}},
            "hidden_words": ["ciao"],
            "difficult_words": ["ciao"],
        },
    )

    # Now delete the custom word.
    r = client.delete("/api/vocab/custom/ciao", headers=_auth(token))
    assert r.status_code == 204, r.text

    data = json.loads(_user_file(isolated_env, "alice@example.com").read_text())
    assert "ciao" not in data["custom_vocab"]
    assert "ciao" not in data["tracking"]["feedback_counts"]
    assert "ciao" not in data["tracking"]["hidden_words"]
    assert "ciao" not in data["tracking"]["difficult_words"]


def test_delete_custom_vocab_does_not_affect_other_users(client, isolated_env):
    token_a = _register(client, "alice@example.com")
    token_b = _register(client, "bob@example.com")
    client.post("/api/vocab/custom", headers=_auth(token_a), json={"input": "ciao"})
    client.post("/api/vocab/custom", headers=_auth(token_b), json={"input": "ciao"})
    client.delete("/api/vocab/custom/ciao", headers=_auth(token_a))

    data_b = json.loads(_user_file(isolated_env, "bob@example.com").read_text())
    assert "ciao" in data_b["custom_vocab"]