"""Tests for POST /api/auth/forgot-token."""
from __future__ import annotations

from unittest.mock import patch


def _register(client, email):
    r = client.post("/api/users", json={"email": email})
    assert r.status_code == 201, r.text
    return r.json()["token"]


def test_forgot_token_unknown_email_returns_uniform_response(client):
    """Anti-enumeration: unknown email gets the same shape as a known one."""
    r = client.post("/api/auth/forgot-token", json={"email": "ghost@example.com"})
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_forgot_token_invalidates_old_token_for_known_user(client):
    old_token = _register(client, "alice@example.com")
    # Old token works:
    assert client.get(
        "/api/auth/whoami", headers={"Authorization": f"Bearer {old_token}"}
    ).status_code == 200

    with patch("backend.app.email_sender.send_forgot_token", return_value=True) as mock_send:
        r = client.post("/api/auth/forgot-token", json={"email": "alice@example.com"})
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    mock_send.assert_called_once()
    sent_email, sent_token = mock_send.call_args[0]
    assert sent_email == "alice@example.com"
    assert len(sent_token) == 32

    # The OLD token must no longer authenticate:
    assert client.get(
        "/api/auth/whoami", headers={"Authorization": f"Bearer {old_token}"}
    ).status_code == 401
    # The NEW token must authenticate:
    assert client.get(
        "/api/auth/whoami", headers={"Authorization": f"Bearer {sent_token}"}
    ).status_code == 200


def test_forgot_token_unknown_email_does_not_send(client):
    with patch("backend.app.email_sender.send_forgot_token") as mock_send:
        r = client.post("/api/auth/forgot-token", json={"email": "ghost@example.com"})
    assert r.status_code == 200
    mock_send.assert_not_called()


def test_forgot_token_invalid_email_format_returns_uniform_response(client):
    r = client.post("/api/auth/forgot-token", json={"email": "not-an-email"})
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_forgot_token_per_email_throttle(client):
    """Same email can't request more than 5 resets per hour."""
    _register(client, "alice@example.com")
    successes = 0
    throttled = 0
    with patch("backend.app.email_sender.send_forgot_token", return_value=True) as mock_send:
        for _ in range(8):
            r = client.post("/api/auth/forgot-token", json={"email": "alice@example.com"})
            assert r.status_code == 200, r.text  # always uniform
            if mock_send.called and mock_send.call_count > successes:
                successes += 1
            else:
                throttled += 1
    # First 5 actually email; rest are silently throttled.
    assert successes == 5, f"expected 5 emails, got {successes}"
    assert throttled >= 3, f"expected at least 3 throttled, got {throttled}"
