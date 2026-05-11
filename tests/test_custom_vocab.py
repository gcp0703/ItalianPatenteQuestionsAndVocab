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