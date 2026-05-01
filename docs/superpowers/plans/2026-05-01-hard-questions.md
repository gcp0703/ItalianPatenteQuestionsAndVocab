# Hard Questions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each user mark questions as "Hard" and take a Hard-only quiz (with random fillers when their Hard set is small). Quiz nav button becomes a hover dropdown with Normal/Hard.

**Architecture:** Per-user list of integer question IDs persisted under `tracking.hard_questions` in `user_data/<email>.json`. Three new authenticated FastAPI endpoints (`GET`/`PUT` for the set, `GET /api/quiz/hard` for the quiz). React frontend keeps a `Set<number>` in state, fetches it on login, and updates it optimistically when the user toggles a checkbox in either Quiz or Topics view. The current "Quiz" nav button becomes a hover-to-open dropdown with Normal/Hard items.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic, pytest + FastAPI TestClient (backend); React 18 hooks, Vite (frontend).

**Spec:** `docs/superpowers/specs/2026-05-01-hard-questions-design.md`

---

## File Structure

**Backend (modify only):**
- `backend/app/main.py` — Pydantic models, helper functions, three new endpoints, fix two existing functions that destructively overwrite `tracking`.

**Backend tests (create):**
- `tests/test_hard_questions.py` — Tests for all three new endpoints + the persistence-preservation fix.

**Frontend (modify only):**
- `frontend/src/App.jsx` — New state, login fetch, toggle handler, Quiz tab checkbox, Topics tab checkbox, Quiz nav dropdown.
- `frontend/src/styles.css` — Dropdown styles, Quiz/Topics checkbox styles.

**Spec (no changes):**
- `docs/superpowers/specs/2026-05-01-hard-questions-design.md` already committed.

Each task below is self-contained: tests are written before code, every step has exact paths and code, every task ends with a commit.

---

## Task 1: Add `hard_questions` to empty user data and preserve it on tracking writes

**Why first:** Two existing functions (`persist_vocab_tracking_for_user`, `migrate_legacy_tracking`) overwrite `user_data["tracking"]` wholesale. If we don't fix these *first*, any subsequent vocab tracking sync will silently wipe the user's Hard set. We add the field to `_empty_user_data` and patch both writers in one task so the field is durable from the very first commit.

**Files:**
- Modify: `backend/app/main.py`
  - `_empty_user_data` at lines ~401-410
  - `persist_vocab_tracking_for_user` at lines ~1235-1249
  - `migrate_legacy_tracking` at lines ~1776-1782
- Test: `tests/test_hard_questions.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_hard_questions.py` with this content:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_hard_questions.py -v`
Expected: 2 failures. `test_empty_user_data_includes_hard_questions` fails because the empty user data lacks `hard_questions`. `test_vocab_tracking_sync_preserves_hard_questions` fails because the vocab tracking sync clobbers the field.

- [ ] **Step 3: Update `_empty_user_data` to include `hard_questions`**

In `backend/app/main.py`, find `_empty_user_data`:

```python
def _empty_user_data(email: str) -> dict[str, Any]:
    return {
        "email": email,
        "tracking": {
            "feedback_counts": {},
            "hidden_words": [],
            "difficult_words": [],
        },
        "quiz_history": [],
    }
```

Replace it with:

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
    }
```

- [ ] **Step 4: Fix `persist_vocab_tracking_for_user` to preserve `hard_questions`**

In `backend/app/main.py`, find `persist_vocab_tracking_for_user`:

```python
def persist_vocab_tracking_for_user(email: str, update: VocabTrackingSyncIn) -> int:
    """Save vocab tracking data to the user's personal JSON file."""
    feedback_counts = {
        word: {"up": _coerce_non_negative_int(c.up), "down": _coerce_non_negative_int(c.down)}
        for word, c in update.feedback_counts.items()
    }

    user_data = load_user_data(email)
    user_data["tracking"] = {
        "feedback_counts": feedback_counts,
        "hidden_words": update.hidden_words,
        "difficult_words": update.difficult_words,
    }
    save_user_data(email, user_data)
    return len(feedback_counts)
```

Replace the body with a version that reads the existing `hard_questions` and writes it back:

```python
def persist_vocab_tracking_for_user(email: str, update: VocabTrackingSyncIn) -> int:
    """Save vocab tracking data to the user's personal JSON file."""
    feedback_counts = {
        word: {"up": _coerce_non_negative_int(c.up), "down": _coerce_non_negative_int(c.down)}
        for word, c in update.feedback_counts.items()
    }

    user_data = load_user_data(email)
    existing_hard = user_data.get("tracking", {}).get("hard_questions", [])
    user_data["tracking"] = {
        "feedback_counts": feedback_counts,
        "hidden_words": update.hidden_words,
        "difficult_words": update.difficult_words,
        "hard_questions": existing_hard,
    }
    save_user_data(email, user_data)
    return len(feedback_counts)
```

- [ ] **Step 5: Fix `migrate_legacy_tracking` similarly**

In `backend/app/main.py`, find the block inside `migrate_legacy_tracking` near line 1776:

```python
    user_data = load_user_data(email)
    user_data["tracking"] = {
        "feedback_counts": feedback_counts,
        "hidden_words": hidden_words,
        "difficult_words": difficult_words,
    }
    save_user_data(email, user_data)
```

Replace it with:

```python
    user_data = load_user_data(email)
    existing_hard = user_data.get("tracking", {}).get("hard_questions", [])
    user_data["tracking"] = {
        "feedback_counts": feedback_counts,
        "hidden_words": hidden_words,
        "difficult_words": difficult_words,
        "hard_questions": existing_hard,
    }
    save_user_data(email, user_data)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_hard_questions.py -v`
Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/main.py tests/test_hard_questions.py
git commit -m "$(cat <<'EOF'
Add tracking.hard_questions to user data; preserve on vocab sync

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: GET /api/quiz/hard-questions — read the user's Hard set

**Files:**
- Modify: `backend/app/main.py` — add `HardQuestionsResponse` model and a new GET endpoint, declared near the other `/api/quiz/*` endpoints (after `/api/quiz/history`).
- Test: `tests/test_hard_questions.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_hard_questions.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hard_questions.py -v`
Expected: the three new tests fail with 404 (endpoint not found).

- [ ] **Step 3: Add the response model**

In `backend/app/main.py`, find `class QuizHistoryResponse(BaseModel)` near line 293. Immediately after it, add:

```python
class HardQuestionsResponse(BaseModel):
    hard_question_ids: list[int]


class HardQuestionToggleIn(BaseModel):
    hard: bool
```

(We add both models now even though the toggle one is used in Task 3 — it keeps related models together in one place.)

- [ ] **Step 4: Add the GET endpoint**

In `backend/app/main.py`, find `get_quiz_history` near line 2137. Immediately after that function (and before the `if IMAGE_DIR.exists():` block), add:

```python
@app.get("/api/quiz/hard-questions", response_model=HardQuestionsResponse)
@limiter.limit("60/minute")
async def get_hard_questions(
    request: Request, email: str = Depends(get_current_user_email)
) -> HardQuestionsResponse:
    user_data = load_user_data(email)
    raw = user_data.get("tracking", {}).get("hard_questions", [])
    ids = [int(qid) for qid in raw if isinstance(qid, int) or (isinstance(qid, str) and qid.isdigit())]
    return HardQuestionsResponse(hard_question_ids=ids)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_hard_questions.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py tests/test_hard_questions.py
git commit -m "$(cat <<'EOF'
Add GET /api/quiz/hard-questions endpoint

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: PUT /api/quiz/hard-questions/{question_id} — toggle a question

**Files:**
- Modify: `backend/app/main.py` — add a PUT endpoint immediately after the GET endpoint from Task 2.
- Test: `tests/test_hard_questions.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_hard_questions.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hard_questions.py -v`
Expected: the new tests fail (endpoint not found / wrong status).

- [ ] **Step 3: Add the PUT endpoint**

In `backend/app/main.py`, immediately after the `get_hard_questions` function from Task 2, add:

```python
@app.put("/api/quiz/hard-questions/{question_id}", status_code=204)
@limiter.limit("60/minute")
async def put_hard_question(
    request: Request,
    question_id: int,
    payload: HardQuestionToggleIn,
    email: str = Depends(get_current_user_email),
) -> Response:
    if question_id not in QUESTION_BY_ID:
        raise HTTPException(status_code=404, detail="Question not found.")

    def _apply() -> None:
        with USER_DATA_LOCK:
            user_data = _read_user_data_unlocked(email)
            tracking = user_data.setdefault("tracking", {})
            current = tracking.get("hard_questions", [])
            current_set = {int(q) for q in current if isinstance(q, int) or (isinstance(q, str) and q.isdigit())}
            if payload.hard:
                current_set.add(question_id)
            else:
                current_set.discard(question_id)
            tracking["hard_questions"] = sorted(current_set)
            _write_user_data_unlocked(email, user_data)

    await asyncio.to_thread(_apply)
    return Response(status_code=204)
```

You also need to ensure `Response` is imported. At the top of `backend/app/main.py`, find the FastAPI imports and confirm `Response` is included. If not, change e.g.:

```python
from fastapi import Depends, FastAPI, HTTPException, Header, Query, Request
```

to include `Response`:

```python
from fastapi import Depends, FastAPI, HTTPException, Header, Query, Request, Response
```

(Use `grep -n "from fastapi import" backend/app/main.py` to find the exact line and add `Response` if missing.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_hard_questions.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py tests/test_hard_questions.py
git commit -m "$(cat <<'EOF'
Add PUT /api/quiz/hard-questions/{id} toggle endpoint

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: GET /api/quiz/hard — Hard quiz with random fillers

**Files:**
- Modify: `backend/app/main.py` — add the endpoint right after the PUT from Task 3.
- Test: `tests/test_hard_questions.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_hard_questions.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hard_questions.py -v`
Expected: the new tests fail (endpoint not found).

- [ ] **Step 3: Add the Hard quiz endpoint**

In `backend/app/main.py`, immediately after the `put_hard_question` function from Task 3, add:

```python
@app.get("/api/quiz/hard", response_model=QuizResponse)
@limiter.limit("30/minute")
async def get_hard_quiz(
    request: Request,
    email: str = Depends(get_current_user_email),
    count: int = Query(default=30, ge=1, le=100),
) -> QuizResponse:
    user_data = load_user_data(email)
    raw = user_data.get("tracking", {}).get("hard_questions", [])
    hard_ids: list[int] = []
    for qid in raw:
        if isinstance(qid, int):
            cand = qid
        elif isinstance(qid, str) and qid.isdigit():
            cand = int(qid)
        else:
            continue
        if cand in QUESTION_BY_ID:
            hard_ids.append(cand)

    if not hard_ids:
        raise HTTPException(status_code=409, detail="no_hard_questions")

    hard_set = set(hard_ids)
    if len(hard_set) >= count:
        chosen_ids = random.sample(list(hard_set), count)
    else:
        filler_pool = [item["id"] for item in QUESTION_BANK if item["id"] not in hard_set]
        fillers_needed = count - len(hard_set)
        # filler_pool will always be large enough because count <= 100 and the bank has 7139 questions.
        fillers = random.sample(filler_pool, fillers_needed)
        chosen_ids = list(hard_set) + fillers
        random.shuffle(chosen_ids)

    questions = [
        QuestionOut(
            id=QUESTION_BY_ID[qid]["id"],
            text=QUESTION_BY_ID[qid]["text"],
            image_url=QUESTION_BY_ID[qid]["image_url"],
            topic=QUESTION_BY_ID[qid]["topic"],
        )
        for qid in chosen_ids
    ]
    return QuizResponse(questions=questions)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_hard_questions.py -v`
Expected: 15 passed.

- [ ] **Step 5: Run the full backend test suite to ensure no regressions**

Run: `pytest -v`
Expected: all tests pass (existing + 15 new).

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py tests/test_hard_questions.py
git commit -m "$(cat <<'EOF'
Add GET /api/quiz/hard endpoint with random fillers

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Frontend state and login fetch for the Hard set

This task adds the `hardQuestionIds` Set, the `quizMode` selector, and wires the login `useEffect` to populate the set. No UI changes yet — just plumbing.

**Files:**
- Modify: `frontend/src/App.jsx`

- [ ] **Step 1: Add the two new state declarations**

In `frontend/src/App.jsx`, find the `mode` declaration around line 752:

```jsx
  const [mode, setMode] = useState("quiz");
```

Immediately after that line (before any other state), add:

```jsx
  const [quizMode, setQuizMode] = useState("normal"); // "normal" | "hard"
  const [hardQuestionIds, setHardQuestionIds] = useState(() => new Set());
  const [hardToggleError, setHardToggleError] = useState("");
```

- [ ] **Step 2: Add a loader that fetches the user's Hard set**

In `frontend/src/App.jsx`, find `async function loadQuizHistory()` around line 1023. Immediately after that function, add a sibling loader:

```jsx
  async function loadHardQuestions() {
    if (!currentUser) return;
    try {
      const response = await fetchWithUser("/api/quiz/hard-questions", {}, currentUser);
      if (!response.ok) {
        console.warn("Failed to load hard questions", response.status);
        return;
      }
      const data = await response.json();
      const ids = Array.isArray(data.hard_question_ids) ? data.hard_question_ids : [];
      setHardQuestionIds(new Set(ids));
    } catch (err) {
      console.warn("Failed to load hard questions", err);
    }
  }
```

- [ ] **Step 3: Call the loader on login**

In `frontend/src/App.jsx`, find the existing useEffect at around line 869:

```jsx
  useEffect(() => {
    if (currentUser) {
      loadQuiz();
      loadQuizHistory();
    }
  }, [currentUser]);
```

Replace it with:

```jsx
  useEffect(() => {
    if (currentUser) {
      loadQuiz();
      loadQuizHistory();
      loadHardQuestions();
    } else {
      setHardQuestionIds(new Set());
      setQuizMode("normal");
    }
  }, [currentUser]);
```

- [ ] **Step 4: Verify the frontend still builds**

Run: `cd frontend && npm run build`
Expected: build succeeds with no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.jsx
git commit -m "$(cat <<'EOF'
Frontend: load per-user Hard question set on login

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Frontend toggle handler with optimistic update + revert on error

This task adds the toggle function and uses `quizMode` in `loadQuiz`. The function is called by the checkboxes added in Tasks 7-8.

**Files:**
- Modify: `frontend/src/App.jsx`

- [ ] **Step 1: Add `toggleHardQuestion` and update `loadQuiz` to use `quizMode`**

In `frontend/src/App.jsx`, find `async function loadQuiz()` around line 993. The current body is:

```jsx
  async function loadQuiz() {
    if (!currentUser) return;
    try {
      setQuizLoadError("");
      setQuizLoading(true);
      setResult(null);
      setSubmitError("");
      setAnswers({});
      setCheating(false);
      setCurrentIndex(0);
      const response = await fetch("/api/quiz");
      if (!response.ok) {
        throw new Error("Impossibile caricare il quiz.");
      }
      const data = await response.json();
      setQuiz(data.questions);
    } catch (err) {
      setQuizLoadError(err.message);
    } finally {
      setQuizLoading(false);
    }
  }
```

Replace the line `const response = await fetch("/api/quiz");` with:

```jsx
      const url = quizMode === "hard" ? "/api/quiz/hard" : "/api/quiz";
      const response = quizMode === "hard"
        ? await fetchWithUser(url, {}, currentUser)
        : await fetch(url);
      if (response.status === 409) {
        setQuizMode("normal");
        throw new Error("Nessuna domanda contrassegnata come Difficile. Torno alla modalità normale.");
      }
```

(That replaces the original `const response = ...` and `if (!response.ok)` is now reached only after the 409 check; the existing error-handling path below is unchanged.)

Then, immediately after `loadQuiz`, add the toggle helper:

```jsx
  async function toggleHardQuestion(questionId, nextChecked) {
    if (!currentUser || !questionId) return;
    setHardToggleError("");

    // Optimistically update.
    setHardQuestionIds((prev) => {
      const next = new Set(prev);
      if (nextChecked) next.add(questionId);
      else next.delete(questionId);
      return next;
    });

    try {
      const response = await fetchWithUser(
        `/api/quiz/hard-questions/${questionId}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ hard: Boolean(nextChecked) }),
        },
        currentUser,
      );
      if (!response.ok) {
        throw new Error(`Toggle failed (${response.status})`);
      }
    } catch (err) {
      // Revert optimistic change.
      setHardQuestionIds((prev) => {
        const next = new Set(prev);
        if (nextChecked) next.delete(questionId);
        else next.add(questionId);
        return next;
      });
      setHardToggleError("Impossibile aggiornare lo stato Difficile. Riprova.");
      console.error(err);
    }
  }
```

- [ ] **Step 2: Re-run loadQuiz when quizMode changes**

The existing `useEffect` from Task 5 only runs on `currentUser` changes. Add a second effect immediately after that one to reload the quiz when the user switches Normal ↔ Hard:

```jsx
  useEffect(() => {
    if (currentUser && mode === "quiz") {
      loadQuiz();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [quizMode]);
```

- [ ] **Step 3: Verify the frontend still builds**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.jsx
git commit -m "$(cat <<'EOF'
Frontend: toggle hard question + load /api/quiz/hard when in Hard mode

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Hard checkbox in the Quiz tab question panel

**Files:**
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Add the checkbox markup**

In `frontend/src/App.jsx`, find the question-panel block around line 2168 that currently reads:

```jsx
        <section className="content-grid">
          <article className="question-panel">
            <p className="topic-tag">{currentQuestion.topic}</p>
            <h2 className="question-text">
```

Replace the `<p className="topic-tag">...</p>` line with the following block (which keeps the topic tag but adds a header row containing the Hard checkbox on the right):

```jsx
            <div className="question-panel-header">
              <p className="topic-tag">{currentQuestion.topic}</p>
              <label className="hard-toggle">
                <input
                  type="checkbox"
                  checked={hardQuestionIds.has(currentQuestion.id)}
                  onChange={(e) => toggleHardQuestion(currentQuestion.id, e.target.checked)}
                />
                <span>Hard</span>
              </label>
            </div>
            {hardToggleError && (
              <p className="inline-error hard-toggle-error">{hardToggleError}</p>
            )}
```

- [ ] **Step 2: Add CSS for the header row and checkbox**

Append to `frontend/src/styles.css`:

```css
.question-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.5rem;
}

.hard-toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.9rem;
  cursor: pointer;
  user-select: none;
}

.hard-toggle input[type="checkbox"] {
  width: 1rem;
  height: 1rem;
  cursor: pointer;
}

.hard-toggle-error {
  margin: 0.25rem 0 0;
}
```

- [ ] **Step 3: Verify the frontend builds**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Manual smoke test in dev**

Run: `./restart-dev.sh` (or start backend and frontend per the README).
Open the app, log in, click "Quiz" in the nav. Verify the Hard checkbox appears top-right of the question panel and toggling it persists across page reloads.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.jsx frontend/src/styles.css
git commit -m "$(cat <<'EOF'
Frontend: Hard checkbox in Quiz tab question panel

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Hard checkbox in the Topics tab question rows

**Files:**
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Add the checkbox to each `<li>`**

In `frontend/src/App.jsx`, find the topics-question render block around line 2113:

```jsx
                  <ul className="topics-question-list">
                    {topicQuestions.map((q) => {
                      const tr = translations[q.id];
                      return (
                        <li key={q.id} className="topics-question-item">
                          <p className="question-text">{q.text}</p>
                          {includeTranslations && (
                            <p className={`topics-question-translation ${tr?.status || "loading"}`}>
                              {tr?.status === "ready"
                                ? tr.text
                                : tr?.status === "error"
                                ? `Traduzione non disponibile: ${tr.text}`
                                : "Traduzione in corso..."}
                            </p>
                          )}
                        </li>
                      );
                    })}
                  </ul>
```

Replace the `<li>` block with:

```jsx
                  <ul className="topics-question-list">
                    {topicQuestions.map((q) => {
                      const tr = translations[q.id];
                      return (
                        <li key={q.id} className="topics-question-item">
                          <div className="topics-question-body">
                            <p className="question-text">{q.text}</p>
                            {includeTranslations && (
                              <p className={`topics-question-translation ${tr?.status || "loading"}`}>
                                {tr?.status === "ready"
                                  ? tr.text
                                  : tr?.status === "error"
                                  ? `Traduzione non disponibile: ${tr.text}`
                                  : "Traduzione in corso..."}
                              </p>
                            )}
                          </div>
                          <label className="hard-toggle topics-hard-toggle">
                            <input
                              type="checkbox"
                              checked={hardQuestionIds.has(q.id)}
                              onChange={(e) => toggleHardQuestion(q.id, e.target.checked)}
                            />
                            <span>Hard</span>
                          </label>
                        </li>
                      );
                    })}
                  </ul>
```

- [ ] **Step 2: Add CSS to position the toggle bottom-right**

Append to `frontend/src/styles.css`:

```css
.topics-question-item {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.topics-question-body {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.topics-hard-toggle {
  align-self: flex-end;
}
```

(If `.topics-question-item` already has rules in `styles.css`, the new `display: flex; flex-direction: column;` should be merged into the existing rule rather than duplicated. Use `grep -n "topics-question-item" frontend/src/styles.css` to find any existing rule and merge accordingly.)

- [ ] **Step 3: Verify the frontend builds**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Manual smoke test in dev**

In the running dev app, click "Topics", pick a category and subtopic, verify each question row has a "Hard" checkbox in the bottom-right corner. Toggle one, switch to Quiz, navigate to that question, verify its Hard checkbox is checked.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.jsx frontend/src/styles.css
git commit -m "$(cat <<'EOF'
Frontend: Hard checkbox in Topics tab question rows

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Quiz nav dropdown (Normal / Hard)

**Files:**
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Replace the single Quiz button with a hover-dropdown wrapper**

In `frontend/src/App.jsx`, find the Quiz nav button around line 1956:

```jsx
            <button
              className={`secondary-button header-button ${mode === "quiz" ? "active" : ""}`}
              onClick={() => setMode("quiz")}
            >
              Quiz
            </button>
```

Replace it with the following block (which wraps the parent button and adds two child items):

```jsx
            <div className="quiz-menu-wrapper">
              <button
                className={`secondary-button header-button quiz-menu-trigger ${mode === "quiz" ? "active" : ""}`}
                onClick={() => {
                  setQuizMode("normal");
                  setMode("quiz");
                }}
                aria-haspopup="menu"
              >
                Quiz <span className="quiz-menu-caret" aria-hidden="true">▾</span>
              </button>
              <div className="quiz-menu-dropdown" role="menu">
                <button
                  type="button"
                  className="quiz-menu-item"
                  role="menuitem"
                  onClick={() => {
                    setQuizMode("normal");
                    setMode("quiz");
                  }}
                >
                  Normal
                </button>
                <button
                  type="button"
                  className="quiz-menu-item"
                  role="menuitem"
                  aria-disabled={hardQuestionIds.size === 0}
                  disabled={hardQuestionIds.size === 0}
                  onClick={() => {
                    if (hardQuestionIds.size === 0) return;
                    setQuizMode("hard");
                    setMode("quiz");
                  }}
                >
                  Hard
                </button>
              </div>
            </div>
```

- [ ] **Step 2: Add CSS for the dropdown and hover behavior**

Append to `frontend/src/styles.css`:

```css
.quiz-menu-wrapper {
  position: relative;
  display: inline-block;
}

.quiz-menu-caret {
  margin-left: 0.25rem;
  font-size: 0.75rem;
}

.quiz-menu-dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  min-width: 100%;
  background: var(--surface, #fff);
  border: 1px solid var(--border, #d0d0d8);
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  padding: 0.25rem 0;
  display: none;
  z-index: 50;
}

.quiz-menu-wrapper:hover .quiz-menu-dropdown,
.quiz-menu-wrapper:focus-within .quiz-menu-dropdown {
  display: block;
}

.quiz-menu-item {
  display: block;
  width: 100%;
  text-align: left;
  background: none;
  border: none;
  padding: 0.45rem 0.9rem;
  font: inherit;
  color: inherit;
  cursor: pointer;
}

.quiz-menu-item:hover:not(:disabled),
.quiz-menu-item:focus:not(:disabled) {
  background: var(--hover, rgba(0, 0, 0, 0.06));
}

.quiz-menu-item:disabled,
.quiz-menu-item[aria-disabled="true"] {
  opacity: 0.5;
  cursor: not-allowed;
}
```

(The CSS uses `:hover` and `:focus-within` rather than a JS grace-delay timer; CSS `:hover` already keeps the menu open while the cursor is over either the trigger or the dropdown because they share the same wrapper. This is the simplest reliable implementation and avoids timer races.)

- [ ] **Step 3: Verify the frontend builds**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Manual smoke test in dev**

Hover over "Quiz" in the nav. The dropdown should appear with Normal and Hard. With zero Hard marked, "Hard" should be visibly disabled. Mark one or more questions Hard, hover again — Hard becomes selectable. Click Hard mid-quiz; the current quiz should be discarded and a Hard quiz loaded.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.jsx frontend/src/styles.css
git commit -m "$(cat <<'EOF'
Frontend: Quiz nav dropdown with Normal/Hard options

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: End-to-end manual verification + frontend dist rebuild

**Files:**
- Modify: `frontend/dist/` (committed build output, per the recent commit `8ddbadc`/`b90eb21` pattern)

- [ ] **Step 1: Run the full backend test suite**

Run: `pytest -v`
Expected: all tests pass, including the 15 new ones.

- [ ] **Step 2: Restart dev servers and walk through the manual checklist**

Run: `./restart-dev.sh`

Walk through every item from the spec's "Manual verification checklist":

- Mark a question Hard in the Quiz tab → reload page → checkbox still checked.
- Mark a question Hard in the Topics tab → switch to Quiz → that question's checkbox is pre-checked when it appears.
- With zero Hard marked: hover Quiz nav → Hard grayed out, click is a no-op.
- Mark 12 questions Hard → hover Quiz → Hard becomes selectable → click → quiz loads with the 12 Hard + 18 random fillers (the 12 are pre-checked, the 18 are not).
- Mid-Normal-quiz, switch to Hard → quiz reloads, prior answers are gone.
- Logout / login → Hard set persists across sessions.
- Toggle a filler ON during a Hard quiz → reload → it's now in the Hard set.
- Toggle a Hard question OFF during a Hard quiz → reload → it's no longer in the set.

If any item fails, file a fix as a follow-up task before declaring done.

- [ ] **Step 3: Rebuild the frontend dist**

Run: `cd frontend && npm run build`
Expected: build succeeds; `frontend/dist/` is regenerated.

- [ ] **Step 4: Commit the dist rebuild**

```bash
git add frontend/dist
git commit -m "$(cat <<'EOF'
Rebuild frontend dist for Hard questions feature

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Final review**

Run: `git log --oneline main..HEAD`
Expected: 10 new commits, one per task.

Run: `git diff --stat main..HEAD`
Expected: changes contained to `backend/app/main.py`, `tests/test_hard_questions.py`, `frontend/src/App.jsx`, `frontend/src/styles.css`, `frontend/dist/`, and the spec/plan docs.

---

## Self-review notes

Spec coverage check:

| Spec section | Implemented in |
|---|---|
| Data model: `tracking.hard_questions` | Task 1 (empty user data + write preservation) |
| GET /api/quiz/hard-questions | Task 2 |
| PUT /api/quiz/hard-questions/{id} | Task 3 |
| GET /api/quiz/hard | Task 4 |
| Frontend state + login fetch | Task 5 |
| Frontend toggle handler + quizMode in loadQuiz | Task 6 |
| Quiz tab Hard checkbox | Task 7 |
| Topics tab Hard checkbox | Task 8 |
| Quiz nav dropdown | Task 9 |
| Manual verification checklist | Task 10 |

Type/name consistency: `hardQuestionIds`, `quizMode`, `toggleHardQuestion`, `loadHardQuestions`, `HardQuestionsResponse`, `HardQuestionToggleIn` are referenced consistently across tasks. The HTTP method/path triple `(PUT, /api/quiz/hard-questions/{question_id}, body {"hard": bool})` matches between Task 3's endpoint and Task 6's frontend call. The `409 no_hard_questions` contract matches between Task 4's endpoint and Task 6's `loadQuiz` 409 handling.

No placeholders. Every step shows full code or full command. Every task ends in a commit.
