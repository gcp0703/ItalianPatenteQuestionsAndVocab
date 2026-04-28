def _register(client, email):
    r = client.post("/api/users", json={"email": email})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == email
    assert "token" in body and len(body["token"]) == 32
    return body["token"]


def test_post_users_returns_token_once(client):
    _register(client, "alice@example.com")
    r = client.post("/api/users", json={"email": "alice@example.com"})
    assert r.status_code == 409


def test_get_users_requires_admin_token(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    _register(client, "alice@example.com")
    assert client.get("/api/users").status_code == 401
    bob_token = _register(client, "bob@example.com")
    r = client.get("/api/users", headers={"Authorization": f"Bearer {bob_token}"})
    assert r.status_code == 403


def test_get_users_works_for_admin(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    admin_token = _register(client, "admin@example.com")
    _register(client, "user1@example.com")
    r = client.get("/api/users", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    emails = {u["email"] for u in r.json()}
    assert {"admin@example.com", "user1@example.com"} <= emails


def test_delete_user_requires_self_token(client):
    alice_token = _register(client, "alice@example.com")
    bob_token = _register(client, "bob@example.com")
    r = client.delete(
        "/api/users/alice@example.com",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert r.status_code == 403
    r = client.delete(
        "/api/users/alice@example.com",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert r.status_code == 200


def test_user_scoped_endpoint_requires_token(client):
    assert client.get("/api/quiz/history").status_code == 401
    token = _register(client, "alice@example.com")
    r = client.get("/api/quiz/history", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_whoami_returns_caller_email(client):
    token = _register(client, "alice@example.com")
    r = client.get("/api/auth/whoami", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == {"email": "alice@example.com"}
