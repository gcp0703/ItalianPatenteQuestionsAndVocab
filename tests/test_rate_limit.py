def _register(client, email):
    r = client.post("/api/users", json={"email": email})
    assert r.status_code == 201, r.text
    return r.json()["token"]


def test_quiz_endpoint_rate_limited(client):
    """`/api/quiz` is limited at 30/min per IP. 35 calls => at least one 429."""
    statuses = [client.get("/api/quiz?count=1").status_code for _ in range(35)]
    assert 429 in statuses, f"expected at least one 429, got {sorted(set(statuses))}"


def test_vocab_translate_rate_limited(client):
    """`/api/vocab/translate` is limited at 10/min per bearer token."""
    token = _register(client, "alice@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    statuses = []
    for _ in range(15):
        # The word may 404 (not in VOCAB_BY_WORD) but the limiter still applies.
        resp = client.get("/api/vocab/translate?word=test", headers=headers)
        statuses.append(resp.status_code)
    assert 429 in statuses, f"expected at least one 429, got {sorted(set(statuses))}"
