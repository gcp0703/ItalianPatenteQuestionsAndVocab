# Vocab — User-added custom words Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a signed-in user add Italian words and phrases to their per-user vocab list via a new "My Words" tab in the vocab page; these mix into the existing study filters and are managed (add/delete) entirely client-side per user.

**Architecture:** Per-user storage under a new `custom_vocab` key in `user_data/<email>.json`. Three new endpoints (`POST /api/vocab/custom`, `DELETE /api/vocab/custom/{word}`, `GET /api/vocab/custom`) plus extensions to `GET /api/vocab`, `GET /api/vocab/translate`, and `GET /api/vocab/{word}/questions`. Frontend adds a "My Words" header button that switches the vocab panel into a management view (add form + list + delete) while leaving the existing study stream intact.

**Tech Stack:** Python 3 / FastAPI / Pydantic v2 (backend), pytest + FastAPI TestClient (tests), React + Vite (frontend), plain CSS.

**Reference spec:** `docs/superpowers/specs/2026-05-11-vocab-custom-words-design.md`

---

## File Structure

**Modified:**
- `backend/app/main.py` — All new models, helpers, endpoints, and changes to existing endpoints live here, following the file's existing pattern (single-module FastAPI app).
- `frontend/src/App.jsx` — All UI state and rendering for the new "My Words" tab, extends existing vocab panel.
- `frontend/src/styles.css` — New CSS classes for the management panel.

**Created:**
- `tests/test_custom_vocab.py` — All backend tests for the new feature.

---

## Task 1: Add `custom_vocab` to new user data shape

**Files:**
- Modify: `backend/app/main.py` — `_empty_user_data` (around line 411)
- Test: `tests/test_custom_vocab.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_custom_vocab.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_custom_vocab.py::test_empty_user_data_includes_custom_vocab -v
```

Expected: FAIL with `KeyError: 'custom_vocab'` (or equivalent assertion failure).

- [ ] **Step 3: Update `_empty_user_data` to include `custom_vocab`**

In `backend/app/main.py`, modify `_empty_user_data` (around line 411):

```python
def _empty_user_data(email: str) -> dict[str, Any]:
    return {
        "email": email,
        "tracking": {
            "feedback_counts": {},
            "hidden_words": [],
            "difficult_words": [],
            "hard_questions": [],
        },
        "quiz_history": [],
        "custom_vocab": {},
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_custom_vocab.py -v
```

Expected: PASS.

- [ ] **Step 5: Also run the full existing test suite to confirm no regressions**

```bash
.venv/bin/pytest -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py tests/test_custom_vocab.py
git commit -m "Add custom_vocab key to empty user data shape"
```

---

## Task 2: Normalization & validation helper

**Files:**
- Modify: `backend/app/main.py` — add helper near the other vocab helpers (after `_coerce_non_negative_int`, around line 362)
- Test: `tests/test_custom_vocab.py`

- [ ] **Step 1: Write failing tests for the helper**

Append to `tests/test_custom_vocab.py`:

```python
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
    decomposed = "café"
    precomposed = "café"
    word, reason = _normalize_custom_vocab_input(decomposed)
    assert reason is None
    assert word == precomposed
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_custom_vocab.py -v -k normalize_custom_vocab
```

Expected: 7 FAILs with `ImportError: cannot import name '_normalize_custom_vocab_input'`.

- [ ] **Step 3: Implement the helper**

In `backend/app/main.py`, add after `_coerce_non_negative_int` (around line 362):

```python
CUSTOM_VOCAB_MAX_LEN = 60
_CUSTOM_VOCAB_ALLOWED_RE = re.compile(r"^[a-zà-ÿ' \-]+$")


def _normalize_custom_vocab_input(raw: str) -> tuple[str | None, str | None]:
    """Normalize a user-entered custom vocab word.

    Returns (normalized, None) on success, or (None, reason) on rejection.
    Reasons: "empty", "too_long", "invalid_chars".
    """
    if not isinstance(raw, str):
        return None, "invalid_chars"
    text = unicodedata.normalize("NFC", raw).strip().lower()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None, "empty"
    if len(text) > CUSTOM_VOCAB_MAX_LEN:
        return None, "too_long"
    if not _CUSTOM_VOCAB_ALLOWED_RE.match(text):
        return None, "invalid_chars"
    return text, None
```

(`unicodedata` and `re` are already imported at the top of the file — verify with grep before adding.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_custom_vocab.py -v -k normalize_custom_vocab
```

Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py tests/test_custom_vocab.py
git commit -m "Add custom vocab input normalization helper"
```

---

## Task 3: Pydantic models for the custom-vocab API

**Files:**
- Modify: `backend/app/main.py` — add models near the existing `VocabResponse` models (around line 217)

- [ ] **Step 1: Add the models**

In `backend/app/main.py`, after the existing `VocabPrefetchResponse` (around line 268), add:

```python
class CustomVocabAddIn(BaseModel):
    input: str = Field(min_length=1, max_length=2000)


class CustomVocabSkipped(BaseModel):
    input: str
    reason: str  # one of: already_in_bank, already_custom, empty, too_long, invalid_chars


class CustomVocabAddResponse(BaseModel):
    added: list[str]
    skipped: list[CustomVocabSkipped]


class CustomVocabEntryOut(BaseModel):
    word: str
    added_at: str
    english: str
    tracking: VocabTrackingOut


class CustomVocabListResponse(BaseModel):
    words: list[CustomVocabEntryOut]
```

Note: `VocabTrackingOut` is already defined around line 241 — these new models reference it.

- [ ] **Step 2: Run the existing test suite to verify import still works**

```bash
.venv/bin/pytest tests/test_custom_vocab.py -v
```

Expected: existing tests still pass; no new tests yet.

- [ ] **Step 3: Commit**

```bash
git add backend/app/main.py
git commit -m "Add Pydantic models for custom vocab endpoints"
```

---

## Task 4: Add custom vocab — locked helper + endpoint + tests

**Files:**
- Modify: `backend/app/main.py` — add helper and route
- Test: `tests/test_custom_vocab.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_custom_vocab.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_custom_vocab.py -v -k add_custom_vocab
```

Expected: 9 FAILs (404 / endpoint not found).

- [ ] **Step 3: Implement the helper**

In `backend/app/main.py`, after `persist_vocab_tracking_for_user` (around line 1295), add:

```python
def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_custom_vocab_words(email: str, raw_input: str) -> dict[str, Any]:
    """Add zero or more comma-separated entries to the user's custom_vocab.

    Returns {"added": [str, ...], "skipped": [{"input": str, "reason": str}, ...]}.
    Performs a single locked read-modify-write.
    """
    parts = [p for p in raw_input.split(",")]
    added: list[str] = []
    skipped: list[dict[str, str]] = []

    with USER_DATA_LOCK:
        data = _read_user_data_unlocked(email)
        if "custom_vocab" not in data or not isinstance(data["custom_vocab"], dict):
            data["custom_vocab"] = {}
        custom = data["custom_vocab"]
        now = _now_iso_utc()

        for raw in parts:
            normalized, reason = _normalize_custom_vocab_input(raw)
            if reason == "empty":
                # Silently drop empty fragments from comma splits.
                continue
            if reason is not None:
                skipped.append({"input": raw.strip(), "reason": reason})
                continue
            assert normalized is not None
            if normalized in VOCAB_BY_WORD:
                skipped.append({"input": raw.strip(), "reason": "already_in_bank"})
                continue
            if normalized in custom:
                skipped.append({"input": raw.strip(), "reason": "already_custom"})
                continue
            custom[normalized] = {
                "added_at": now,
                "english": "",
                "ai_definition": None,
                "ai_definition_failed": False,
                "dictionary_cache": None,
            }
            added.append(normalized)

        if added:
            _write_user_data_unlocked(email, data)

    return {"added": added, "skipped": skipped}
```

`datetime` and `timezone` are already imported at line 25 of `main.py` — no new import needed.

- [ ] **Step 4: Implement the endpoint**

In `backend/app/main.py`, after `prefetch_vocab_batch` (around line 2150), add:

```python
@app.post("/api/vocab/custom", response_model=CustomVocabAddResponse)
async def add_custom_vocab(
    body: CustomVocabAddIn,
    email: str = Depends(get_current_user_email),
) -> CustomVocabAddResponse:
    result = await asyncio.to_thread(add_custom_vocab_words, email, body.input)
    return CustomVocabAddResponse(
        added=result["added"],
        skipped=[CustomVocabSkipped(**s) for s in result["skipped"]],
    )
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_custom_vocab.py -v
```

Expected: all add-custom-vocab tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py tests/test_custom_vocab.py
git commit -m "Add POST /api/vocab/custom endpoint with validation and dedup"
```

---

## Task 5: Delete custom vocab — locked helper + endpoint + tests

**Files:**
- Modify: `backend/app/main.py`
- Test: `tests/test_custom_vocab.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_custom_vocab.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_custom_vocab.py -v -k delete_custom_vocab
```

Expected: 6 FAILs (endpoint not found).

- [ ] **Step 3: Implement the helper**

In `backend/app/main.py`, after `add_custom_vocab_words`, add:

```python
def delete_custom_vocab_word(email: str, raw_word: str) -> bool:
    """Delete a custom word and its tracking entries. Returns True if deleted."""
    normalized, reason = _normalize_custom_vocab_input(raw_word)
    if reason is not None or normalized is None:
        return False

    with USER_DATA_LOCK:
        data = _read_user_data_unlocked(email)
        custom = data.get("custom_vocab")
        if not isinstance(custom, dict) or normalized not in custom:
            return False
        del custom[normalized]

        tracking = data.get("tracking")
        if isinstance(tracking, dict):
            feedback = tracking.get("feedback_counts")
            if isinstance(feedback, dict):
                feedback.pop(normalized, None)
            for key in ("hidden_words", "difficult_words"):
                values = tracking.get(key)
                if isinstance(values, list):
                    tracking[key] = [w for w in values if w != normalized]

        _write_user_data_unlocked(email, data)
        return True
```

- [ ] **Step 4: Implement the endpoint**

In `backend/app/main.py`, after the POST endpoint added in Task 4, add:

```python
@app.delete("/api/vocab/custom/{word}", status_code=204)
async def delete_custom_vocab(
    word: str,
    email: str = Depends(get_current_user_email),
):
    deleted = await asyncio.to_thread(delete_custom_vocab_word, email, word)
    if not deleted:
        raise HTTPException(status_code=404, detail="Word not in your custom vocab.")
    return Response(status_code=204)
```

`Response` is already imported from `fastapi` at line 28 of `main.py` — no new import needed.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_custom_vocab.py -v
```

Expected: all delete tests PASS, all earlier tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py tests/test_custom_vocab.py
git commit -m "Add DELETE /api/vocab/custom/{word} with tracking cleanup"
```

---

## Task 6: List custom vocab — endpoint + tests

**Files:**
- Modify: `backend/app/main.py`
- Test: `tests/test_custom_vocab.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_custom_vocab.py`:

```python
def test_list_custom_vocab_requires_auth(client):
    r = client.get("/api/vocab/custom")
    assert r.status_code == 401


def test_list_custom_vocab_empty(client):
    token = _register(client, "alice@example.com")
    r = client.get("/api/vocab/custom", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json() == {"words": []}


def test_list_custom_vocab_returns_entries(client):
    token = _register(client, "alice@example.com")
    client.post(
        "/api/vocab/custom",
        headers=_auth(token),
        json={"input": "ciao, diritto di precedenza"},
    )
    r = client.get("/api/vocab/custom", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    words = {w["word"]: w for w in body["words"]}
    assert set(words.keys()) == {"ciao", "diritto di precedenza"}
    for entry in body["words"]:
        assert entry["english"] == ""
        assert entry["added_at"]
        assert entry["tracking"] == {"up": 0, "down": 0, "known": False, "difficult": False}


def test_list_custom_vocab_reflects_tracking(client):
    token = _register(client, "alice@example.com")
    client.post("/api/vocab/custom", headers=_auth(token), json={"input": "ciao"})
    client.post(
        "/api/vocab/tracking",
        headers=_auth(token),
        json={
            "feedback_counts": {"ciao": {"up": 3, "down": 1}},
            "hidden_words": ["ciao"],
            "difficult_words": [],
        },
    )
    r = client.get("/api/vocab/custom", headers=_auth(token))
    assert r.status_code == 200, r.text
    entry = next(w for w in r.json()["words"] if w["word"] == "ciao")
    assert entry["tracking"] == {"up": 3, "down": 1, "known": True, "difficult": False}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_custom_vocab.py -v -k list_custom_vocab
```

Expected: 4 FAILs.

- [ ] **Step 3: Implement the endpoint**

In `backend/app/main.py`, after the DELETE endpoint, add:

```python
@app.get("/api/vocab/custom", response_model=CustomVocabListResponse)
async def list_custom_vocab(
    email: str = Depends(get_current_user_email),
) -> CustomVocabListResponse:
    data = await asyncio.to_thread(load_user_data, email)
    tracking = data.get("tracking") or {}
    feedback_counts = tracking.get("feedback_counts") or {}
    hidden = set(tracking.get("hidden_words") or [])
    difficult = set(tracking.get("difficult_words") or [])
    custom = data.get("custom_vocab") or {}

    out_words: list[CustomVocabEntryOut] = []
    for word, entry in custom.items():
        counts = feedback_counts.get(word) or {}
        out_words.append(
            CustomVocabEntryOut(
                word=word,
                added_at=str(entry.get("added_at") or ""),
                english=str(entry.get("english") or ""),
                tracking=VocabTrackingOut(
                    up=_coerce_non_negative_int(counts.get("up", 0)),
                    down=_coerce_non_negative_int(counts.get("down", 0)),
                    known=word in hidden,
                    difficult=word in difficult,
                ),
            )
        )
    return CustomVocabListResponse(words=out_words)
```

**Routing note:** FastAPI matches routes in declaration order. `/api/vocab/custom` must be declared **before** `/api/vocab/{word}/questions` and `/api/vocab/translate` to avoid `custom` being captured as a `{word}` path param. Since we're adding it after `prefetch_vocab_batch` (which is later in the file than those endpoints), this is fine — but verify by running the tests, which exercise both routes.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_custom_vocab.py -v
```

Expected: all list tests PASS; all earlier tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py tests/test_custom_vocab.py
git commit -m "Add GET /api/vocab/custom listing endpoint"
```

---

## Task 7: Merge custom words into `GET /api/vocab`

**Files:**
- Modify: `backend/app/main.py` — `get_vocab` function around line 1967
- Test: `tests/test_custom_vocab.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_custom_vocab.py`:

```python
def test_get_vocab_includes_custom_words(client):
    token = _register(client, "alice@example.com")
    client.post(
        "/api/vocab/custom",
        headers=_auth(token),
        json={"input": "ciao, diritto di precedenza"},
    )
    r = client.get("/api/vocab", headers=_auth(token))
    assert r.status_code == 200, r.text
    words = {w["word"]: w for w in r.json()["words"]}
    assert "ciao" in words
    assert "diritto di precedenza" in words
    assert words["ciao"]["known_translation"] is None
    assert words["ciao"]["tracking"] == {"up": 0, "down": 0, "known": False, "difficult": False}


def test_get_vocab_custom_words_are_per_user(client):
    token_a = _register(client, "alice@example.com")
    token_b = _register(client, "bob@example.com")
    client.post("/api/vocab/custom", headers=_auth(token_a), json={"input": "ciao"})

    body_a = client.get("/api/vocab", headers=_auth(token_a)).json()
    body_b = client.get("/api/vocab", headers=_auth(token_b)).json()
    words_a = {w["word"] for w in body_a["words"]}
    words_b = {w["word"] for w in body_b["words"]}
    assert "ciao" in words_a
    assert "ciao" not in words_b


def test_get_vocab_custom_word_tracking_reflected(client):
    token = _register(client, "alice@example.com")
    client.post("/api/vocab/custom", headers=_auth(token), json={"input": "ciao"})
    client.post(
        "/api/vocab/tracking",
        headers=_auth(token),
        json={
            "feedback_counts": {"ciao": {"up": 1, "down": 2}},
            "hidden_words": [],
            "difficult_words": ["ciao"],
        },
    )
    body = client.get("/api/vocab", headers=_auth(token)).json()
    entry = next(w for w in body["words"] if w["word"] == "ciao")
    assert entry["tracking"] == {"up": 1, "down": 2, "known": False, "difficult": True}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_custom_vocab.py -v -k get_vocab
```

Expected: 3 FAILs (custom words not in response).

- [ ] **Step 3: Modify `get_vocab` to append custom entries**

In `backend/app/main.py`, modify `get_vocab` (around line 1967):

```python
@app.get("/api/vocab", response_model=VocabResponse)
async def get_vocab(email: str = Depends(get_current_user_email)) -> VocabResponse:
    user_data = load_user_data(email)
    user_tracking = user_data.get("tracking", {})
    user_counts = user_tracking.get("feedback_counts", {})
    user_hidden = set(user_tracking.get("hidden_words", []))
    user_difficult = set(user_tracking.get("difficult_words", []))

    words = []
    for item in VOCAB_BANK:
        word = item["word"]
        counts = user_counts.get(word, {})
        tracking = VocabTrackingOut(
            up=_coerce_non_negative_int(counts.get("up", 0)),
            down=_coerce_non_negative_int(counts.get("down", 0)),
            known=word in user_hidden,
            difficult=word in user_difficult,
        )
        words.append(VocabWordOut(
            word=word,
            known_translation=item["known_translation"],
            tracking=tracking,
        ))

    # Append per-user custom words.
    custom = user_data.get("custom_vocab") or {}
    for word, entry in custom.items():
        counts = user_counts.get(word, {})
        tracking = VocabTrackingOut(
            up=_coerce_non_negative_int(counts.get("up", 0)),
            down=_coerce_non_negative_int(counts.get("down", 0)),
            known=word in user_hidden,
            difficult=word in user_difficult,
        )
        english = (entry.get("english") or "").strip() or None
        words.append(VocabWordOut(
            word=word,
            known_translation=english,
            tracking=tracking,
        ))

    percent = _definitions_cached_percent()
    return VocabResponse(words=words, definitions_cached_percent=percent)
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_custom_vocab.py -v
```

Expected: get_vocab tests PASS; all earlier tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py tests/test_custom_vocab.py
git commit -m "Merge user's custom_vocab into GET /api/vocab response"
```

---

## Task 8: Support custom words in `GET /api/vocab/translate`

**Files:**
- Modify: `backend/app/main.py` — `translate_vocab_word` (around line 2015)
- Test: `tests/test_custom_vocab.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_custom_vocab.py`:

```python
import sys


def test_translate_unknown_word_returns_404(client):
    """Words that aren't in the bank or in the user's custom vocab → 404."""
    token = _register(client, "alice@example.com")
    r = client.get(
        "/api/vocab/translate",
        headers=_auth(token),
        params={"word": "thisisnotaword"},
    )
    assert r.status_code == 404


def test_translate_custom_word_persists_back_to_user_file(
    client, isolated_env, monkeypatch
):
    """Translating a custom word writes ai_definition into user_data, not the master file."""
    token = _register(client, "alice@example.com")
    client.post("/api/vocab/custom", headers=_auth(token), json={"input": "ciao"})

    # Stub the AI / dictionary / translation pipeline so the test is hermetic.
    main_mod = sys.modules["backend.app.main"]
    monkeypatch.setattr(main_mod, "get_ai_definition", lambda w: "1. Hi.\n2. Hello.")
    monkeypatch.setattr(main_mod, "get_dictionary_details", lambda w, hint: None)
    monkeypatch.setattr(main_mod, "translate_text", lambda w: "hi")

    r = client.get(
        "/api/vocab/translate",
        headers=_auth(token),
        params={"word": "ciao"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "Hi" in body["translation"] or body["translation"] == "hi"

    # Custom word's ai_definition should now be persisted in user_data, NOT in the master file.
    data = json.loads(_user_file(isolated_env, "alice@example.com").read_text())
    entry = data["custom_vocab"]["ciao"]
    assert entry["ai_definition"]  # non-empty


def test_translate_custom_word_for_different_user_still_404(client, monkeypatch):
    """Bob's custom word isn't translatable by Alice."""
    main_mod = sys.modules["backend.app.main"]
    monkeypatch.setattr(main_mod, "get_ai_definition", lambda w: "x")
    monkeypatch.setattr(main_mod, "get_dictionary_details", lambda w, hint: None)
    monkeypatch.setattr(main_mod, "translate_text", lambda w: "x")

    token_a = _register(client, "alice@example.com")
    token_b = _register(client, "bob@example.com")
    client.post("/api/vocab/custom", headers=_auth(token_b), json={"input": "miaparola"})

    r = client.get(
        "/api/vocab/translate",
        headers=_auth(token_a),
        params={"word": "miaparola"},
    )
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_custom_vocab.py -v -k translate
```

Expected: FAILs — current endpoint 404s for any word not in `VOCAB_BY_WORD` and has no auth.

- [ ] **Step 3: Add helper to persist custom-word metadata**

In `backend/app/main.py`, after `delete_custom_vocab_word`, add:

```python
def persist_custom_vocab_metadata(
    email: str,
    word: str,
    *,
    ai_definition: str | None = None,
    ai_definition_failed: bool | None = None,
    dictionary_cache: dict[str, Any] | None = None,
    english: str | None = None,
) -> None:
    """Write resolved translation metadata back to a user's custom_vocab entry."""
    with USER_DATA_LOCK:
        data = _read_user_data_unlocked(email)
        custom = data.get("custom_vocab")
        if not isinstance(custom, dict) or word not in custom:
            return
        entry = custom[word]
        if ai_definition is not None:
            entry["ai_definition"] = ai_definition
        if ai_definition_failed is not None:
            entry["ai_definition_failed"] = ai_definition_failed
        if dictionary_cache is not None:
            entry["dictionary_cache"] = dictionary_cache
        if english is not None:
            entry["english"] = english
        _write_user_data_unlocked(email, data)
```

- [ ] **Step 4: Modify the translate endpoint**

Replace the existing `translate_vocab_word` (around line 2015) with:

```python
@app.get("/api/vocab/translate", response_model=VocabTranslationResponse)
@limiter.limit("10/minute")
async def translate_vocab_word(
    request: Request,
    word: str = Query(min_length=1),
    email: str = Depends(get_current_user_email),
) -> VocabTranslationResponse:
    entry = VOCAB_BY_WORD.get(word)
    custom_source = False
    if not entry:
        user_data = await asyncio.to_thread(load_user_data, email)
        custom = user_data.get("custom_vocab") or {}
        if word in custom:
            entry = {
                "word": word,
                "known_translation": (custom[word].get("english") or "").strip() or None,
                "ai_definition": (custom[word].get("ai_definition") or "").strip() or None,
                "ai_definition_failed": bool(custom[word].get("ai_definition_failed")),
                "dictionary_cache": custom[word].get("dictionary_cache"),
            }
            custom_source = True
    if not entry:
        raise HTTPException(status_code=404, detail="Word not found.")

    translation = entry["known_translation"]
    ai_definition: str | None = entry.get("ai_definition")

    if not translation and not ai_definition:
        def _user_ai_call() -> str | None:
            AI_MODEL_GATE.user_acquire()
            try:
                return get_ai_definition(word)
            finally:
                AI_MODEL_GATE.user_release()

        try:
            ai_definition = await asyncio.to_thread(_user_ai_call)
            if ai_definition:
                if custom_source:
                    await asyncio.to_thread(
                        persist_custom_vocab_metadata,
                        email,
                        word,
                        ai_definition=ai_definition,
                    )
                else:
                    await asyncio.to_thread(persist_ai_definitions, {word: ai_definition})
        except Exception:
            pass

    if not translation and not ai_definition:
        try:
            translation = await asyncio.to_thread(translate_text, word)
        except Exception:
            pass

    if ai_definition and not translation:
        translation = ai_definition

    google_hint = translation or ""
    dictionary: VocabDictionaryOut | None = None
    cached_dict = entry.get("dictionary_cache")
    skip_dictionary = bool(translation or ai_definition) and not cached_dict
    try:
        dictionary_payload = (
            cached_dict if skip_dictionary
            else await asyncio.to_thread(get_dictionary_details, word, google_hint)
        )
        if dictionary_payload:
            if custom_source and not cached_dict:
                await asyncio.to_thread(
                    persist_custom_vocab_metadata,
                    email,
                    word,
                    dictionary_cache=dictionary_payload,
                )
            dictionary = VocabDictionaryOut(
                lookup_word=dictionary_payload["lookup_word"],
                lemma=dictionary_payload["lemma"],
                meanings=dictionary_payload["meanings"],
                related=[
                    VocabDictionaryRelatedOut(
                        term=item["term"],
                        meaning=item["meaning"],
                        english=item["english"],
                    )
                    for item in dictionary_payload["related"]
                ],
            )
    except Exception:
        dictionary = None

    if not translation and dictionary and dictionary.meanings:
        translation = " / ".join(dictionary.meanings)

    if not translation:
        raise HTTPException(
            status_code=502,
            detail="Translation failed. Check your network connection and try again.",
        )

    return VocabTranslationResponse(word=word, translation=translation, dictionary=dictionary)
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_custom_vocab.py -v
```

Expected: translate tests PASS; all earlier tests still PASS.

- [ ] **Step 6: Run the full suite to catch any regression**

```bash
.venv/bin/pytest -v
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add backend/app/main.py tests/test_custom_vocab.py
git commit -m "Support custom words in /api/vocab/translate with per-user persistence"
```

---

## Task 9: Phrase substring match in `GET /api/vocab/{word}/questions`

**Files:**
- Modify: `backend/app/main.py` — `get_vocab_word_questions` around line 2099
- Test: `tests/test_custom_vocab.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_custom_vocab.py`:

```python
def test_vocab_questions_single_word_uses_stem_prefix(client):
    """Existing single-word behavior must be preserved."""
    r = client.get("/api/vocab/abbagliante/questions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] >= 1
    # The stem-prefix regex should also match plural "abbaglianti"
    matches_plural = any("abbaglianti" in q["text"].lower() for q in body["questions"])
    matches_singular = any("abbagliante" in q["text"].lower() for q in body["questions"])
    assert matches_plural or matches_singular


def test_vocab_questions_phrase_uses_substring_match(client):
    """Multi-word phrases use literal substring match (case-insensitive)."""
    # Pick a phrase known to appear in the question bank.
    r = client.get("/api/vocab/diritto%20di%20precedenza/questions")
    assert r.status_code == 200, r.text
    body = r.json()
    # If any question contains the literal substring, count > 0.
    for q in body["questions"]:
        assert "diritto di precedenza" in q["text"].lower()


def test_vocab_questions_phrase_with_no_matches_returns_zero(client):
    r = client.get("/api/vocab/parola%20inventata%20xyz/questions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 0
    assert body["questions"] == []
```

- [ ] **Step 2: Run tests to verify behavior**

```bash
.venv/bin/pytest tests/test_custom_vocab.py -v -k vocab_questions
```

Expected: at least one FAIL (the phrase test likely fails today because the existing stem-prefix regex doesn't handle spaces well — `\b{stem}\w*` would match only the first word's prefix).

- [ ] **Step 3: Modify the endpoint**

In `backend/app/main.py`, replace `get_vocab_word_questions` (around line 2099):

```python
@app.get("/api/vocab/{word}/questions", response_model=VocabQuestionsResponse)
async def get_vocab_word_questions(word: str) -> VocabQuestionsResponse:
    if " " in word:
        # Multi-word phrase: literal substring match, case-insensitive.
        pattern = re.compile(re.escape(word), re.IGNORECASE)
    else:
        stem = re.sub(r"[aeio]+$", "", word)
        if len(stem) >= 4:
            pattern = re.compile(rf"\b{re.escape(stem)}\w*", re.IGNORECASE)
        else:
            pattern = re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
    matches = [
        QuestionMatchOut(
            id=q["id"],
            text=q["text"],
            answer=q["answer"],
            image_url=q.get("image_url"),
            topic=q["topic"],
        )
        for q in QUESTION_BANK
        if pattern.search(q["text"])
    ]
    return VocabQuestionsResponse(word=word, questions=matches, count=len(matches))
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_custom_vocab.py -v -k vocab_questions
.venv/bin/pytest -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py tests/test_custom_vocab.py
git commit -m "Use substring match for multi-word phrases in vocab/{word}/questions"
```

---

## Task 10: Frontend — "My Words" button + state + load custom list

**Files:**
- Modify: `frontend/src/App.jsx`

- [ ] **Step 1: Add the source constant and state**

In `frontend/src/App.jsx`, near the other `VOCAB_SOURCE_*` constants (around line 16), add:

```javascript
const VOCAB_SOURCE_CUSTOM = "custom";
```

Inside the `App` component, near the other vocab state (around line 793), add:

```javascript
  const [customVocab, setCustomVocab] = useState([]);
  const [customVocabLoading, setCustomVocabLoading] = useState(false);
  const [customVocabError, setCustomVocabError] = useState("");
  const [customVocabAddInput, setCustomVocabAddInput] = useState("");
  const [customVocabToast, setCustomVocabToast] = useState(null);
```

- [ ] **Step 2: Add the loader function**

In the same component, near the other `loadVocab*` functions (around line 1144), add:

```javascript
  async function loadCustomVocab() {
    setCustomVocabLoading(true);
    setCustomVocabError("");
    try {
      const res = await fetchWithUser("/api/vocab/custom", {}, currentUser);
      if (!res.ok) throw new Error("Impossibile caricare le tue parole.");
      const data = await res.json();
      setCustomVocab(data.words || []);
    } catch (err) {
      setCustomVocabError(err.message || "Errore di rete.");
      setCustomVocab([]);
    } finally {
      setCustomVocabLoading(false);
    }
  }
```

- [ ] **Step 3: Add the "My Words" header button**

In the vocab header (around line 2470 — the `vocab-source-actions` div), add a new button between the `Ranked` button and the `Reset` button:

```jsx
              <button
                className={`secondary-button ${vocabSource === VOCAB_SOURCE_CUSTOM ? "header-button active" : ""}`}
                onClick={() => {
                  setVocabSource(VOCAB_SOURCE_CUSTOM);
                  loadCustomVocab();
                }}
                disabled={vocabLoading || vocabRevealing}
              >
                My Words
              </button>
```

- [ ] **Step 4: Render management view conditionally**

In the vocab panel's body, the existing render chain begins:

```jsx
          {vocabLoading && !vocabCurrent ? (
            <p>Sto preparando una nuova parola.</p>
          ) : vocabError && !vocabCurrent ? (
            ...
          ) : (
            <div className="vocab-stream">
              ... (the whole vocab-stream block, ~100 lines)
            </div>
          )}
```

Prepend a new branch at the top of the ternary so that when `vocabSource === VOCAB_SOURCE_CUSTOM` the management panel renders instead. Concretely, change the opening line from `{vocabLoading && !vocabCurrent ? (` to:

```jsx
          {vocabSource === VOCAB_SOURCE_CUSTOM ? (
            <CustomVocabPanel
              entries={customVocab}
              loading={customVocabLoading}
              error={customVocabError}
              addInput={customVocabAddInput}
              setAddInput={setCustomVocabAddInput}
              toast={customVocabToast}
              onAdd={handleAddCustomVocab}
              onDelete={handleDeleteCustomVocab}
            />
          ) : vocabLoading && !vocabCurrent ? (
```

All other branches (the `vocabError && !vocabCurrent` branch and the `<div className="vocab-stream">` block) remain exactly as they are. Only the head of the ternary changes.

`CustomVocabPanel`, `handleAddCustomVocab`, and `handleDeleteCustomVocab` are introduced in Tasks 11 and 12. To make this task compile in the meantime, also add **temporary stubs** above the JSX:

```javascript
  // Temporary stubs; replaced in tasks 11 and 12.
  function handleAddCustomVocab() { setCustomVocabToast({ kind: "info", text: "Coming soon." }); }
  function handleDeleteCustomVocab() {}
  function CustomVocabPanel({ entries, loading, error }) {
    if (loading) return <p>Loading...</p>;
    if (error) return <p className="inline-error">{error}</p>;
    return <p>Custom vocab: {entries.length} entries.</p>;
  }
```

- [ ] **Step 5: Manual verification**

Run dev server:

```bash
./restart-dev.sh
```

Open the app, log in, go to the vocab page, click "My Words". Confirm:
- Button highlights as active.
- Panel shows "Custom vocab: 0 entries." (or "Loading..." briefly).
- Switching back to Unknown returns the study stream.
- No console errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.jsx
git commit -m "Add My Words tab scaffolding to vocab page"
```

---

## Task 11: Frontend — Add words form with toast

**Files:**
- Modify: `frontend/src/App.jsx`

- [ ] **Step 1: Implement `handleAddCustomVocab`**

Replace the temporary `handleAddCustomVocab` stub from Task 10 with:

```javascript
  async function handleAddCustomVocab(event) {
    if (event) event.preventDefault();
    const input = customVocabAddInput.trim();
    if (!input) return;

    setCustomVocabLoading(true);
    setCustomVocabError("");
    try {
      const res = await fetchWithUser(
        "/api/vocab/custom",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ input }),
        },
        currentUser,
      );
      if (!res.ok) throw new Error("Impossibile aggiungere parole.");
      const data = await res.json();

      const addedCount = (data.added || []).length;
      const skipped = data.skipped || [];
      const parts = [];
      if (addedCount) parts.push(`Added ${addedCount}.`);
      if (skipped.length) {
        const reasons = skipped.map((s) => `${s.input} (${s.reason.replace(/_/g, " ")})`).join(", ");
        parts.push(`Skipped ${skipped.length}: ${reasons}.`);
      }
      if (!parts.length) parts.push("No changes.");
      setCustomVocabToast({ kind: addedCount ? "success" : "info", text: parts.join(" ") });

      setCustomVocabAddInput("");
      // Invalidate the cached master bank so the next study session refetches with new words.
      setVocabBank([]);
      await loadCustomVocab();
    } catch (err) {
      setCustomVocabError(err.message || "Errore di rete.");
    } finally {
      setCustomVocabLoading(false);
    }
  }
```

- [ ] **Step 2: Add auto-dismiss for the toast**

Near the other `useEffect` hooks, add:

```javascript
  useEffect(() => {
    if (!customVocabToast) return;
    const timer = setTimeout(() => setCustomVocabToast(null), 4500);
    return () => clearTimeout(timer);
  }, [customVocabToast]);
```

- [ ] **Step 3: Update `CustomVocabPanel` to render the add form**

Replace the stub `CustomVocabPanel` from Task 10 with (still leaving the list iteration minimal for now — Task 12 adds the full list with delete):

```jsx
  function CustomVocabPanel({ entries, loading, error, addInput, setAddInput, toast, onAdd, onDelete }) {
    return (
      <div className="vocab-custom-panel">
        <form className="vocab-custom-add" onSubmit={onAdd}>
          <input
            type="text"
            className="vocab-custom-input"
            placeholder="Add words (comma-separated)"
            value={addInput}
            onChange={(e) => setAddInput(e.target.value)}
            disabled={loading}
            aria-label="Add words (comma-separated)"
          />
          <button
            type="submit"
            className="primary-button"
            disabled={loading || !addInput.trim()}
          >
            Add
          </button>
        </form>
        {toast && (
          <p
            className={`vocab-custom-toast vocab-custom-toast-${toast.kind}`}
            aria-live="polite"
          >
            {toast.text}
          </p>
        )}
        {error && <p className="inline-error">{error}</p>}
        {loading ? (
          <p>Loading…</p>
        ) : entries.length === 0 ? (
          <p className="vocab-custom-empty">
            You haven't added any words yet. Type one or more Italian words above, separated by commas, then click Add.
          </p>
        ) : (
          <ul className="vocab-custom-list">
            {entries.map((entry) => (
              <li key={entry.word} className="vocab-custom-row">
                <span className="vocab-custom-word">{entry.word}</span>
                <span className="vocab-custom-stats">
                  👍 {entry.tracking.up}  👎 {entry.tracking.down}
                </span>
                <button
                  type="button"
                  className="vocab-custom-delete"
                  onClick={() => onDelete(entry.word)}
                  aria-label={`Delete ${entry.word}`}
                  title="Delete this word"
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }
```

- [ ] **Step 4: Manual verification**

```bash
./restart-dev.sh
```

1. Log in, vocab page, click "My Words".
2. Type `ciao, semaforo, hello!` in the input, click Add.
3. Expect toast: `Added 1. Skipped 2: semaforo (already in bank), hello! (invalid chars).`
4. Expect `ciao` row appears in the list.
5. Switch to Unknown → study a couple of batches → `ciao` should eventually appear (random sample over the merged bank). If it doesn't appear within ~5 batches, switch to Ranked or Difficult Words — it should still be a valid candidate.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.jsx
git commit -m "Add custom-vocab add form with toast feedback"
```

---

## Task 12: Frontend — Delete custom words with confirmation

**Files:**
- Modify: `frontend/src/App.jsx`

- [ ] **Step 1: Implement `handleDeleteCustomVocab`**

Replace the temporary `handleDeleteCustomVocab` stub from Task 10 with:

```javascript
  async function handleDeleteCustomVocab(word) {
    if (!word) return;
    if (!window.confirm(`Remove "${word}" from your custom vocabulary?`)) return;

    setCustomVocabLoading(true);
    setCustomVocabError("");
    try {
      const res = await fetchWithUser(
        `/api/vocab/custom/${encodeURIComponent(word)}`,
        { method: "DELETE" },
        currentUser,
      );
      if (res.status === 404) {
        setCustomVocabToast({ kind: "info", text: `"${word}" was already gone.` });
      } else if (!res.ok) {
        throw new Error("Impossibile rimuovere la parola.");
      } else {
        setCustomVocabToast({ kind: "success", text: `Removed "${word}".` });
      }

      // Optimistic local removal + cache invalidation.
      setCustomVocab((prev) => prev.filter((e) => e.word !== word));
      setVocabBank([]);
      await loadCustomVocab();
    } catch (err) {
      setCustomVocabError(err.message || "Errore di rete.");
    } finally {
      setCustomVocabLoading(false);
    }
  }
```

- [ ] **Step 2: Manual verification**

```bash
./restart-dev.sh
```

1. Log in, go to My Words, add a word (e.g. `ciao`).
2. Click the `×` button on its row.
3. Confirm the dialog → expect the word to disappear and a "Removed" toast.
4. Switch to Unknown → study a batch; `ciao` should no longer appear in rotation.
5. Re-add `ciao`, mark it 👍 and 👎 (via Unknown study), and confirm in My Words it shows the counts. Then delete it. Confirm via inspecting the backend response from `/api/vocab/custom` (DevTools network tab) that the deleted word's tracking entry no longer appears.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.jsx
git commit -m "Add delete-with-confirm to My Words panel"
```

---

## Task 13: Frontend — Verify zero-questions empty state

The empty state is already implemented in `App.jsx` around line 1929
(`vocabQuestionResults.count === 0` → `<p className="vocab-questions-empty">No quiz questions contain this word.</p>`)
and the CSS class `.vocab-questions-empty` already exists in `frontend/src/styles.css` around line 1145. No code changes needed — only verification.

- [ ] **Step 1: Manual verification**

```bash
./restart-dev.sh
```

1. Log in, go to My Words, add `parola inventata` (assuming this phrase doesn't appear verbatim in any quiz question — verify with `grep -i "parola inventata" quizPatenteB2023.json` first).
2. Switch to Unknown, advance batches until the phrase appears (or use Ranked to reach it faster), then click the search icon next to it.
3. Expect the modal to show "No quiz questions contain this word."
4. For comparison, click the search icon on a curated word that DOES appear in questions (e.g. `semaforo`) — confirm the list still renders normally.

- [ ] **Step 2: No commit needed (no code changes)**

---

## Task 14: Frontend — CSS styling for the management panel

**Files:**
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Add CSS**

Append to `frontend/src/styles.css`:

```css
.vocab-custom-panel {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.vocab-custom-add {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}

.vocab-custom-input {
  flex: 1;
  padding: 0.5rem 0.75rem;
  font-size: 1rem;
  border: 1px solid var(--color-border, #ccc);
  border-radius: 6px;
  background: var(--color-surface, #fff);
  color: inherit;
}

.vocab-custom-toast {
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  background: var(--color-info-bg, rgba(0, 100, 200, 0.08));
  border: 1px solid var(--color-info-border, rgba(0, 100, 200, 0.25));
  font-size: 0.9rem;
}

.vocab-custom-toast-success {
  background: var(--color-success-bg, rgba(0, 150, 0, 0.08));
  border-color: var(--color-success-border, rgba(0, 150, 0, 0.25));
}

.vocab-custom-empty {
  padding: 1rem;
  color: var(--color-muted, #888);
  font-style: italic;
  text-align: center;
}

.vocab-custom-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.vocab-custom-row {
  display: grid;
  grid-template-columns: 1fr auto auto;
  align-items: center;
  gap: 0.75rem;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--color-border, #eee);
  border-radius: 6px;
  background: var(--color-surface, #fff);
}

.vocab-custom-word {
  font-weight: 600;
}

.vocab-custom-stats {
  color: var(--color-muted, #666);
  font-size: 0.9rem;
}

.vocab-custom-delete {
  border: none;
  background: transparent;
  font-size: 1.25rem;
  line-height: 1;
  cursor: pointer;
  color: var(--color-muted, #888);
  padding: 0.25rem 0.5rem;
}

.vocab-custom-delete:hover {
  color: var(--color-danger, #c33);
}

.vocab-questions-empty {
  padding: 0.5rem 0;
  color: var(--color-muted, #888);
  font-style: italic;
}
```

If the project's CSS uses different design tokens, adjust the variable names to match. Otherwise the fallback values keep things sane.

- [ ] **Step 2: Manual verification**

```bash
./restart-dev.sh
```

Visit the My Words tab. Confirm:
- Input + Add button sit on one row.
- List rows have a clean border and three columns (word, stats, delete).
- Empty state copy is muted/italic.
- Toast appears in a tinted box; dismisses after ~4.5 seconds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/styles.css
git commit -m "Style the My Words management panel"
```

---

## Task 15: Manual end-to-end smoke test

- [ ] **Step 1: Restart dev server fresh**

```bash
./restart-dev.sh
```

- [ ] **Step 2: Execute the smoke test from the spec**

1. Sign in. Open vocab. Switch to My Words. Confirm empty state copy.
2. Add `diritto di precedenza, ciao, abbagliante` → expect 2 added, `abbagliante` skipped with reason `already in bank`. Toast surfaces it.
3. Switch to Unknown. Cycle through several next-batch transitions. Confirm `ciao` and `diritto di precedenza` appear in rotation at least once over a handful of batches.
4. Reveal `ciao` → translation appears.
5. Reload the page. Reveal `ciao` again → translation is served from cache (Network tab: response is fast / no AI definition compute spinning).
6. Switch back to My Words. Click delete on `ciao`. Confirm dialog → word disappears. Toast surfaces success.
7. Switch to Unknown → confirm `ciao` no longer appears in rotation across the next several batches.
8. Click the search icon on `diritto di precedenza` in the vocab card → expect "No quiz questions contain this word." (assuming the phrase doesn't actually appear in any question — if it does, the list renders normally and the empty-state branch needs to be tested with a more synthetic phrase).
9. Open DevTools → Application → Local Storage. Confirm no `quiz-patente-b-custom-vocab*` keys exist (the spec says no localStorage caching for custom words).
10. Open the per-user JSON in `user_data/<your-email>.json`. Confirm `custom_vocab` contains your remaining entries with `ai_definition` populated for the words you revealed.

- [ ] **Step 3: Run the full backend test suite one more time**

```bash
.venv/bin/pytest -v
```

Expected: all green.

- [ ] **Step 4: Final commit (if any leftover changes)**

```bash
git status
# If there are uncommitted changes from the smoke test:
git add -A
git commit -m "Final cleanup after manual smoke test"
```

---

## Out of scope (per spec)

- Sharing custom words across users.
- Bulk import from CSV / file upload.
- Editing the Italian text of an existing custom word.
- Tags / folders / collections.
- A "review only my custom words" study filter.
