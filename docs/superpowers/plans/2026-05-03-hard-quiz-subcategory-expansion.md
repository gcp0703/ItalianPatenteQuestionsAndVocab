# Hard Quiz Sub-Category Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `/api/quiz/hard` selection logic so the quiz samples questions from the *sub-categories* that contain the user's hard marks (weighted by mark count), instead of returning the marked questions themselves. When marked sub-categories cannot supply `count` distinct questions, expand to the surrounding top-level category rather than the whole bank.

**Architecture:** Extract the algorithm into a pure helper `_select_hard_quiz_question_ids(count, hard_ids, rng)` so it can be unit-tested in isolation. The endpoint becomes a thin wrapper that loads marks, raises 409 on empty marks, delegates to the helper, and projects the chosen IDs into `QuestionOut` objects. No frontend changes; no persistence changes.

**Tech Stack:** Python 3, FastAPI, pytest, `random.Random`, the bundled `quizPatenteB2023.json` question bank (7139 questions across 656 leaf sub-categories under ~25 top-level categories).

**Reference spec:** `docs/superpowers/specs/2026-05-03-hard-quiz-subcategory-expansion-design.md`

---

## File Structure

- **Modify** `backend/app/main.py`:
  - Add new helper `_select_hard_quiz_question_ids` immediately above the `@app.get("/api/quiz/hard", ...)` decorator (currently line 2247).
  - Replace the body of `get_hard_quiz` (currently lines 2247–2281) to delegate to the helper.
- **Modify** `tests/test_hard_questions.py`:
  - Add `import random` and `from backend.app import main as main_mod` at the top.
  - Append helper unit tests at the bottom (with shared fixture constants).
  - Replace three integration tests that hard-coded the old behavior:
    `test_get_hard_quiz_pads_with_fillers`,
    `test_get_hard_quiz_samples_when_set_exceeds_count`,
    `test_get_hard_quiz_exact_count_match`.
  - Adjust `test_get_hard_quiz_filters_unknown_ids` to assert "bogus id absent + valid id's sub-cat contributes" instead of "valid id present".

### Sub-category fixtures used in tests

Both fall under top-level `"definizioni generali doveri strada"`:

| Constant | Topic | Question IDs | Size |
|---|---|---|---|
| `SUBCAT_A` | `definizioni generali doveri strada / carreggiata doppio senso` | 1..7 | 7 |
| `SUBCAT_B` | `definizioni generali doveri strada / strada sei corsie` | 8..15 | 8 |

These are stable: the question bank is the bundled JSON, `_flatten_questions` is deterministic, and the existing test suite already uses ID 1 etc. as fixtures.

---

### Task 1: Add `_select_hard_quiz_question_ids` helper with unit tests

**Files:**
- Modify: `backend/app/main.py` (insert helper just before line 2247)
- Modify: `tests/test_hard_questions.py` (top imports + append at bottom)

- [ ] **Step 1: Add helper imports check**

The helper uses `random.Random` and the existing module globals `QUESTION_BANK` and `QUESTION_BY_ID`. `random` is already imported in `backend/app/main.py` (used at line 2263). No new imports are needed.

Verify by searching:

```bash
grep -n "^import random" backend/app/main.py
```

Expected: a match. If `random` is not imported (it should be), add `import random` to the import block at the top of `backend/app/main.py`.

- [ ] **Step 2: Add the helper function in `backend/app/main.py`**

Insert this function immediately before the line `@app.get("/api/quiz/hard", response_model=QuizResponse)` (currently line 2247):

```python
def _select_hard_quiz_question_ids(
    count: int,
    hard_ids: list[int],
    rng: random.Random,
) -> list[int]:
    """Pick `count` question IDs for a hard quiz.

    Treats each hard mark as a signal "the user struggles with this sub-category"
    rather than "the user wants this exact question." Samples from the
    sub-categories containing the marks, weighted by mark count using the
    largest-remainder method. If the marked sub-categories cannot supply
    `count` distinct questions, expands to other sub-categories under the
    same top-level categories (the segment before " / " in the topic string).

    Returns [] when `hard_ids` is empty or every id is unknown; the caller
    is responsible for the 409 response.
    """
    if not hard_ids:
        return []

    mark_counts: dict[str, int] = {}
    for qid in hard_ids:
        question = QUESTION_BY_ID.get(qid)
        if question is None:
            continue
        topic = question["topic"]
        mark_counts[topic] = mark_counts.get(topic, 0) + 1

    if not mark_counts:
        return []

    questions_by_subcat: dict[str, list[int]] = {}
    for question in QUESTION_BANK:
        questions_by_subcat.setdefault(question["topic"], []).append(question["id"])

    sorted_subcats = sorted(mark_counts.keys())
    available: dict[str, list[int]] = {
        sc: list(questions_by_subcat.get(sc, [])) for sc in sorted_subcats
    }
    picked: list[int] = []

    def allocate(targets: list[str], total: int) -> dict[str, int]:
        """Largest-remainder allocation across `targets`, weighted by mark_counts.

        Ties on remainder are broken by sub-category name so the result is
        deterministic in tests.
        """
        if total <= 0 or not targets:
            return {sc: 0 for sc in targets}
        marks_total = sum(mark_counts[sc] for sc in targets)
        exact = {sc: mark_counts[sc] * total / marks_total for sc in targets}
        floor = {sc: int(exact[sc]) for sc in targets}
        remainders = {sc: exact[sc] - floor[sc] for sc in targets}
        leftover = total - sum(floor.values())
        ranked = sorted(targets, key=lambda sc: (-remainders[sc], sc))
        result = dict(floor)
        for sc in ranked[:leftover]:
            result[sc] += 1
        return result

    def sample_from(sc: str, wanted: int) -> int:
        pool = available[sc]
        if wanted <= 0 or not pool:
            return 0
        if wanted >= len(pool):
            picked.extend(pool)
            available[sc] = []
            return len(pool)
        chosen = rng.sample(pool, wanted)
        chosen_set = set(chosen)
        available[sc] = [qid for qid in pool if qid not in chosen_set]
        picked.extend(chosen)
        return wanted

    allocations = allocate(sorted_subcats, count)
    surplus = 0
    for sc in sorted_subcats:
        wanted = allocations[sc]
        got = sample_from(sc, wanted)
        surplus += wanted - got

    while surplus > 0:
        candidates = [sc for sc in sorted_subcats if available[sc]]
        if not candidates:
            break
        new_alloc = allocate(candidates, surplus)
        new_surplus = 0
        for sc in candidates:
            wanted = new_alloc[sc]
            got = sample_from(sc, wanted)
            new_surplus += wanted - got
        if new_surplus >= surplus:
            break
        surplus = new_surplus

    if len(picked) < count:
        marked_top_levels = {sc.split(" / ", 1)[0] for sc in sorted_subcats}
        marked_subcat_set = set(sorted_subcats)
        already_picked = set(picked)
        expansion_pool = [
            question["id"]
            for question in QUESTION_BANK
            if question["id"] not in already_picked
            and question["topic"] not in marked_subcat_set
            and question["topic"].split(" / ", 1)[0] in marked_top_levels
        ]
        needed = count - len(picked)
        if needed >= len(expansion_pool):
            picked.extend(expansion_pool)
        else:
            picked.extend(rng.sample(expansion_pool, needed))

    rng.shuffle(picked)
    return picked
```

- [ ] **Step 3: Add test imports in `tests/test_hard_questions.py`**

In the existing import block at the top of the file (lines 2–5), add `import random` and a module import for the backend so unit tests can call the helper directly.

Locate this block at the top of the file:

```python
"""Tests for per-user Hard question marking and Hard quiz mode."""
from __future__ import annotations

import json
from pathlib import Path
```

Replace it with:

```python
"""Tests for per-user Hard question marking and Hard quiz mode."""
from __future__ import annotations

import json
import random
from pathlib import Path

from backend.app import main as main_mod
```

Note: the helper tests below depend only on the bundled question bank (`QUESTION_BANK`, `QUESTION_BY_ID`) and the helper function itself — none of which depend on the `QPB_USER_DATA_DIR` env var that the `client` fixture toggles. A top-level import is therefore safe even though `client` later reloads the module via `importlib.reload`.

- [ ] **Step 4: Append helper unit tests at the bottom of `tests/test_hard_questions.py`**

Append:

```python
# ---------------------------------------------------------------------------
# Unit tests for _select_hard_quiz_question_ids
# ---------------------------------------------------------------------------

# Both sub-categories below sit under top-level "definizioni generali doveri strada".
SUBCAT_A = "definizioni generali doveri strada / carreggiata doppio senso"
SUBCAT_A_IDS = list(range(1, 8))      # 7 questions
SUBCAT_B = "definizioni generali doveri strada / strada sei corsie"
SUBCAT_B_IDS = list(range(8, 16))     # 8 questions
TOP_LEVEL = "definizioni generali doveri strada"


def _topic(qid: int) -> str:
    return main_mod.QUESTION_BY_ID[qid]["topic"]


def test_helper_empty_hard_ids_returns_empty():
    result = main_mod._select_hard_quiz_question_ids(30, [], random.Random(0))
    assert result == []


def test_helper_all_unknown_hard_ids_returns_empty():
    result = main_mod._select_hard_quiz_question_ids(
        30, [99999998, 99999999], random.Random(0)
    )
    assert result == []


def test_helper_single_subcat_marks_fit():
    """count < sub-cat size: result is `count` distinct questions, all from that sub-cat."""
    rng = random.Random(0)
    result = main_mod._select_hard_quiz_question_ids(
        count=5, hard_ids=[1, 2, 3], rng=rng
    )
    assert len(result) == 5
    assert len(set(result)) == 5
    assert set(result) <= set(SUBCAT_A_IDS)


def test_helper_weighted_distribution():
    """5:1 mark split across two sub-cats with ample capacity:
    largest-remainder gives 25 from A and 5 from B."""
    by_subcat: dict[str, list[int]] = {}
    for q in main_mod.QUESTION_BANK:
        by_subcat.setdefault(q["topic"], []).append(q["id"])
    big = sorted(
        ((t, ids) for t, ids in by_subcat.items() if len(ids) >= 30),
        key=lambda pair: pair[0],
    )
    assert len(big) >= 2, "test fixture requires two sub-cats with >=30 questions"
    big_a_topic, big_a_ids = big[0]
    big_b_topic, big_b_ids = big[1]
    hard = big_a_ids[:5] + big_b_ids[:1]

    result = main_mod._select_hard_quiz_question_ids(
        count=30, hard_ids=hard, rng=random.Random(0)
    )
    assert len(result) == 30
    assert len(set(result)) == 30
    topics = [main_mod.QUESTION_BY_ID[qid]["topic"] for qid in result]
    assert sum(1 for t in topics if t == big_a_topic) == 25
    assert sum(1 for t in topics if t == big_b_topic) == 5


def test_helper_subcat_capped_redistributes_to_other_marked_subcat():
    """5 marks in A (size 7), 1 mark in B (size 8), count=15.

    Allocation: A=13, B=2 (largest-remainder, alphabetic tie-break favors A).
    A capped at 7 → surplus 6. B picks 2.
    Redistribute: only B remains (A exhausted) → B picks 6 more → B at 8/8.
    Final: all 7 from A and all 8 from B. No top-level expansion needed."""
    rng = random.Random(0)
    hard = SUBCAT_A_IDS[:5] + SUBCAT_B_IDS[:1]
    result = main_mod._select_hard_quiz_question_ids(
        count=15, hard_ids=hard, rng=rng
    )
    assert len(result) == 15
    assert len(set(result)) == 15
    assert set(result) == set(SUBCAT_A_IDS) | set(SUBCAT_B_IDS)


def test_helper_all_marked_exhausted_expands_to_top_level():
    """Same marks as previous test but count=30.

    After redistribution, both A (7) and B (8) are exhausted (15 picked).
    Top-level expansion pulls 15 more from other sub-cats under the same
    top-level category, never re-using A or B."""
    rng = random.Random(0)
    hard = SUBCAT_A_IDS[:5] + SUBCAT_B_IDS[:1]
    result = main_mod._select_hard_quiz_question_ids(
        count=30, hard_ids=hard, rng=rng
    )
    assert len(result) == 30
    assert len(set(result)) == 30
    a_b_set = set(SUBCAT_A_IDS) | set(SUBCAT_B_IDS)
    a_b_picks = [qid for qid in result if qid in a_b_set]
    assert sorted(a_b_picks) == sorted(a_b_set)
    expansion = [qid for qid in result if qid not in a_b_set]
    assert len(expansion) == 15
    for qid in expansion:
        topic = _topic(qid)
        assert topic.split(" / ", 1)[0] == TOP_LEVEL
        assert topic != SUBCAT_A
        assert topic != SUBCAT_B


def test_helper_marked_questions_are_eligible_in_their_subcat():
    """count = sub-cat size, marks within that sub-cat.

    Result is exactly the whole sub-cat — including the originally-marked IDs,
    confirming marked questions are NOT excluded from sampling."""
    rng = random.Random(0)
    hard = SUBCAT_A_IDS[:5]
    result = main_mod._select_hard_quiz_question_ids(
        count=7, hard_ids=hard, rng=rng
    )
    assert sorted(result) == sorted(SUBCAT_A_IDS)
```

- [ ] **Step 5: Run helper tests**

```bash
.venv/bin/pytest tests/test_hard_questions.py -k helper -v
```

Expected: all 7 helper tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py tests/test_hard_questions.py
git commit -m "$(cat <<'EOF'
Add _select_hard_quiz_question_ids helper with sub-cat-aware sampling

Treats hard marks as a signal about the sub-category the user struggles
with, weighting sample slots by mark count and falling back to the
surrounding top-level category when marked sub-cats are exhausted.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Use the helper in the `get_hard_quiz` endpoint

**Files:**
- Modify: `backend/app/main.py` (function body around lines 2247–2281)

- [ ] **Step 1: Replace the endpoint body**

Replace the entire `get_hard_quiz` definition (the `@app.get`-decorated function currently at lines 2247–2281) with:

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
    hard_ids = _normalize_hard_ids(raw, require_known=True)

    if not hard_ids:
        raise HTTPException(status_code=409, detail="no_hard_questions")

    chosen_ids = _select_hard_quiz_question_ids(count, hard_ids, random.Random())

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

- [ ] **Step 2: Run the hard-quiz tests**

```bash
.venv/bin/pytest tests/test_hard_questions.py -v
```

Expected results:
- `test_get_hard_quiz_requires_auth` — PASS (unchanged).
- `test_get_hard_quiz_empty_set_returns_409` — PASS (unchanged).
- All 7 helper tests added in Task 1 — PASS.
- `test_get_hard_quiz_pads_with_fillers` — **FAIL**: asserts "All Hard ids must be present" which no longer holds; Task 3 replaces this test.
- `test_get_hard_quiz_samples_when_set_exceeds_count` — **FAIL**: asserts every returned id is from the marked set; with new behavior the result is from the marked sub-cats, which is a superset; Task 3 replaces this test.
- `test_get_hard_quiz_filters_unknown_ids` — likely **FAIL**: asserts `1 in returned_ids` which is no longer guaranteed; Task 3 fixes the assertion.
- `test_get_hard_quiz_exact_count_match` — likely **FAIL** intermittently: asserts exactly `[1,2,3,4,5]`, now picks 5 random of {1..7}; Task 3 replaces this test.

These failures are expected and addressed by Task 3.

- [ ] **Step 3: Commit**

```bash
git add backend/app/main.py
git commit -m "$(cat <<'EOF'
Wire get_hard_quiz endpoint through new sub-cat-aware helper

The endpoint becomes a thin wrapper: load marks, raise 409 when empty,
delegate selection to _select_hard_quiz_question_ids, project to
QuestionOut. Three legacy integration tests now fail because they
asserted the old "marked-IDs verbatim" behavior; they are updated in
the next task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Update integration tests that locked in the old behavior

**Files:**
- Modify: `tests/test_hard_questions.py`

- [ ] **Step 1: Replace `test_get_hard_quiz_pads_with_fillers`**

Locate the function in `tests/test_hard_questions.py` (currently around line 208). Delete the entire function and replace it with:

```python
def test_get_hard_quiz_returns_count_distinct_questions(client):
    """When the marked sub-cat is too small for `count`, the quiz expands to
    the surrounding top-level category. All questions are distinct."""
    token = _register(client, "alice@example.com")
    # IDs 1..5 all live in sub-cat "carreggiata doppio senso" (7 questions total)
    # under top-level "definizioni generali doveri strada".
    hard_ids = [1, 2, 3, 4, 5]
    for qid in hard_ids:
        client.put(f"/api/quiz/hard-questions/{qid}", headers=_auth(token), json={"hard": True})

    r = client.get("/api/quiz/hard?count=10", headers=_auth(token))
    assert r.status_code == 200
    data = r.json()
    returned_ids = [q["id"] for q in data["questions"]]
    assert len(returned_ids) == 10
    assert len(set(returned_ids)) == 10  # no duplicates
    # The marked sub-cat has only 7 questions, so all 7 must be in the result.
    assert set(range(1, 8)) <= set(returned_ids)
    # The remaining 3 must be under the same top-level category.
    for q in data["questions"]:
        assert q["topic"].split(" / ", 1)[0] == "definizioni generali doveri strada"
```

- [ ] **Step 2: Replace `test_get_hard_quiz_samples_when_set_exceeds_count`**

Locate the function (currently around line 224). Delete and replace with:

```python
def test_get_hard_quiz_samples_within_marked_subcats_when_set_exceeds_count(client):
    """When marked sub-cats together hold more questions than `count`, the
    result is drawn from those sub-cats only — no top-level expansion."""
    token = _register(client, "alice@example.com")
    # IDs 1..7 are sub-cat A (size 7); IDs 8..15 are sub-cat B (size 8).
    # 14 marks across the two sub-cats; ask for 5 — well under 7+8.
    for qid in range(1, 15):
        client.put(f"/api/quiz/hard-questions/{qid}", headers=_auth(token), json={"hard": True})

    r = client.get("/api/quiz/hard?count=5", headers=_auth(token))
    assert r.status_code == 200
    returned_ids = [q["id"] for q in r.json()["questions"]]
    assert len(returned_ids) == 5
    assert len(set(returned_ids)) == 5
    # Every returned id must belong to one of the two marked sub-cats.
    assert set(returned_ids) <= set(range(1, 16))
```

- [ ] **Step 3: Replace `test_get_hard_quiz_exact_count_match`**

Locate the function (currently around line 256). Delete and replace with:

```python
def test_get_hard_quiz_pulls_from_marked_subcat_even_at_exact_match(client):
    """When `count` equals the size of the (single) marked sub-cat, the result
    is exactly that sub-cat's questions, irrespective of which subset was marked."""
    token = _register(client, "alice@example.com")
    # IDs 1..5 marked, all in sub-cat A (size 7). Ask for count=7.
    hard_ids = [1, 2, 3, 4, 5]
    for qid in hard_ids:
        client.put(f"/api/quiz/hard-questions/{qid}", headers=_auth(token), json={"hard": True})

    r = client.get("/api/quiz/hard?count=7", headers=_auth(token))
    assert r.status_code == 200
    returned_ids = [q["id"] for q in r.json()["questions"]]
    assert sorted(returned_ids) == [1, 2, 3, 4, 5, 6, 7]
```

- [ ] **Step 4: Update `test_get_hard_quiz_filters_unknown_ids`**

Locate the function (currently around line 239). Delete and replace with:

```python
def test_get_hard_quiz_filters_unknown_ids(client, isolated_env):
    """Unknown IDs in the persisted hard list must be ignored by the selector,
    and the quiz must still draw from the valid id's sub-cat."""
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
    # Bogus id must never appear.
    assert 99999999 not in returned_ids
    # The valid id is in sub-cat A (IDs 1..7). At least one returned id
    # must come from that sub-cat (in fact all should, since count=3 < 7).
    assert any(qid in range(1, 8) for qid in returned_ids)
```

- [ ] **Step 5: Run the full hard-quiz test file**

```bash
.venv/bin/pytest tests/test_hard_questions.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Run the full test suite to make sure nothing else broke**

```bash
.venv/bin/pytest -q
```

Expected: full pass.

- [ ] **Step 7: Commit**

```bash
git add tests/test_hard_questions.py
git commit -m "$(cat <<'EOF'
Update hard-quiz integration tests for sub-cat-aware selection

The three legacy tests asserted the old "marked-IDs verbatim" behavior.
Replace them with assertions that match the new behavior: the quiz pulls
from the sub-categories of the marked questions (with top-level fallback),
and the marked questions themselves may or may not appear by chance.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Notes

- **No frontend rebuild needed.** The response shape (`QuizResponse(questions=[…])`) is unchanged.
- **No persistence migration.** `tracking.hard_questions` continues to hold question IDs; the toggle endpoints are untouched.
- **Determinism.** The helper takes a `random.Random` instance so tests can seed it. The endpoint instantiates `random.Random()` (system-time seeded) per request, matching today's behavior.
- **Tie-breaking.** Largest-remainder ties are broken by sub-category name (alphabetical) so the cap+redistribute test is deterministic without seeding.

