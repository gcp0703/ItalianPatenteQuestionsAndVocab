import pytest


def test_hash_token_is_deterministic_with_pepper(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", "fixed-pepper")
    from backend.app.auth import hash_token
    assert hash_token("abc") == hash_token("abc")
    assert len(hash_token("abc")) == 64


def test_hash_token_changes_with_pepper(monkeypatch):
    from backend.app.auth import hash_token
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", "pepper-A")
    a = hash_token("abc")
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", "pepper-B")
    b = hash_token("abc")
    assert a != b


def test_hash_token_requires_pepper(monkeypatch):
    monkeypatch.delenv("AUTH_TOKEN_PEPPER", raising=False)
    from backend.app.auth import hash_token
    with pytest.raises(RuntimeError, match="AUTH_TOKEN_PEPPER"):
        hash_token("abc")


def test_generate_token_returns_uuid4_hex(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", "p")
    from backend.app.auth import generate_token
    t1 = generate_token()
    t2 = generate_token()
    assert t1 != t2
    assert len(t1) == 32
    assert all(c in "0123456789abcdef" for c in t1)


def test_verify_token_matches_hash(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", "p")
    from backend.app.auth import generate_token, hash_token, verify_token
    token = generate_token()
    h = hash_token(token)
    assert verify_token(token, h) is True
    assert verify_token("wrong-token", h) is False


def test_parse_bearer_strips_prefix():
    from backend.app.auth import parse_bearer
    assert parse_bearer("Bearer abc123") == "abc123"
    assert parse_bearer("bearer  abc  ") == "abc"


def test_parse_bearer_rejects_malformed():
    from backend.app.auth import parse_bearer
    assert parse_bearer(None) is None
    assert parse_bearer("") is None
    assert parse_bearer("Token abc") is None
    assert parse_bearer("Bearer ") is None
