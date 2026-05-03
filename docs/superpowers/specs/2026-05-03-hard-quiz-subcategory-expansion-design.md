# Hard Quiz Sub-Category Expansion — Design

## Background

The `/api/quiz/hard` endpoint currently returns up to `count` (default 30, max 100) of the user's marked-hard question IDs verbatim, then backfills with random non-hard questions if the user has fewer marks than `count`. This means the user repeatedly sees the exact same questions they marked, and any backfill is drawn from anywhere in the bank — including sub-categories the user has never struggled with.

## Goal

Treat each hard mark as a signal "the user struggles with this sub-category," not "the user wants to re-see this exact question." The hard quiz should:

1. Sample random questions from the sub-categories that contain hard marks, instead of returning the marked questions themselves.
2. Weight the sampling so sub-categories with more hard marks contribute more questions.
3. When sub-categories with marks cannot supply `count` distinct questions, expand to the surrounding top-level category rather than to the whole bank.

The questions persisted in `tracking.hard_questions`, the toggle endpoint, and the `GET /api/quiz/hard-questions` listing are all unchanged. Only the question-selection logic inside `GET /api/quiz/hard` changes.

## Definitions

- **Sub-category** — the full leaf `topic` string on a question, e.g. `"definizioni-generali-doveri-strada / carreggiata-doppio-senso"`. There are 656 unique sub-categories across 7139 questions.
- **Top-level category** — the segment before `" / "` in a topic, e.g. `"definizioni-generali-doveri-strada"`. There are roughly 25 top-level categories.
- **Marked sub-category** — a sub-category that contains at least one of the user's hard-marked question IDs.
- **Mark count** for a sub-category — the number of the user's hard marks that fall in that sub-category.

## Algorithm

Inputs: `count` (1–100), `hard_question_ids: list[int]` from the user's tracking.

1. If `hard_question_ids` is empty → raise `409 no_hard_questions` (unchanged behavior).
2. Build `mark_counts: dict[sub_cat -> int]` by grouping the user's marks by their sub-category.
3. Allocate quiz slots across marked sub-categories proportional to `mark_counts`, using the largest-remainder method so the allocated slots sum to exactly `count`.
4. For each marked sub-category, sample `min(allocated, sub_cat_size)` distinct questions uniformly at random from the **entire** sub-category. Marked-hard questions remain eligible — they may or may not appear by chance; they are not preferentially picked or excluded.
5. **Redistribute step.** If any sub-category was capped at its size in step 4 and slots remain unfilled, redistribute those surplus slots across the remaining marked sub-categories that still have unpicked questions, still weighted by their original `mark_counts`. Repeat until either the surplus is placed or every marked sub-category is exhausted.
6. **Top-level expansion step.** If `count` slots are still not filled after step 5, build an expanded pool from all sub-categories under the same top-level categories as the marked sub-categories, excluding sub-categories already considered and questions already picked. Sample the remainder uniformly at random from that pool.
7. Shuffle the combined list and return as a `QuizResponse`.

### Worked example

User has marks: 5 in sub-cat A (which has 11 questions total), 1 in sub-cat B (7 questions total). Both A and B are under top-level T. `count = 30`.

- Mark counts: `{A: 5, B: 1}`. Total marks: 6.
- Allocation: A → `5/6 × 30 = 25`, B → `5`. After largest-remainder: A=25, B=5.
- Step 4: A capped at 11 (surplus 14), B capped at 7 (surplus 0 since 5 ≤ 7) → wait, allocation 5 ≤ 7 so B picks 5. Total picked so far = 11 + 5 = 16. Surplus = 14.
- Step 5: A is exhausted, B has 7 - 5 = 2 unpicked. Redistribute weighted by mark counts of remaining sub-cats with capacity → all 14 surplus goes to B, capped at 2 more → B picks 2 more. Total = 18. Surplus = 12.
- Step 6: Expand to other sub-categories under top-level T. Sample 12 distinct questions uniformly at random from those (excluding already-picked). Total = 30.

### Why largest-remainder rounding

For mark counts `{A: 5, B: 1}` and `count = 30`, exact slots are `{A: 25.0, B: 5.0}` — already integer. For `{A: 2, B: 1}` and `count = 30`, exact slots are `{A: 20.0, B: 10.0}` — also integer. For `{A: 2, B: 1, C: 1}` and `count = 30`, exact slots are `{A: 15.0, B: 7.5, C: 7.5}` — needs rounding to integers summing to 30. Largest-remainder is the standard, deterministic-modulo-tie-breaking choice; floor-then-fill the leftover slots to the sub-cats with the largest fractional remainders, breaking ties by sub-category name to keep tests deterministic.

## Surface changes

- **Modified:** body of `get_hard_quiz` in `backend/app/main.py` (currently lines 2247–2281). The endpoint signature, response model, rate limit, and 409 error remain identical.
- **New helper:** `_select_hard_quiz_question_ids(count, hard_ids, question_bank, by_id) -> list[int]` extracted so the algorithm can be unit-tested directly without going through HTTP. Lives next to other quiz helpers in `main.py`.
- **No frontend changes.** The response shape is `QuizResponse(questions=[…])` — unchanged.
- **No persistence changes.** `tracking.hard_questions`, `_normalize_hard_ids`, `GET /api/quiz/hard-questions`, and `PUT /api/quiz/hard-questions/{id}` are all untouched.

## Testing

New tests in `tests/` for `_select_hard_quiz_question_ids`:

- **Empty marks → caller raises 409** (covered at the endpoint test, not the helper).
- **All marks fit comfortably** — single mark in one sub-cat, `count = 30`, sub-cat has 50 questions: result has 30 distinct questions, all from that sub-cat.
- **Mark-weighted distribution** — marks `{A: 5, B: 1}`, both sub-cats have ≥30 questions: result has roughly 25 from A and 5 from B (exactly 25 and 5 given largest-remainder).
- **Sub-cat exhausted, redistribute** — marks `{A: 5, B: 1}`, A has 11 questions, B has ≥30: result has 11 from A and 19 from B.
- **All marked sub-cats exhausted, top-level expansion** — marks `{A: 5, B: 1}`, A has 11, B has 7, both under top-level T: result has 11 from A, 7 from B, 12 from other sub-cats under T.
- **Marked questions eligible** — single mark, sub-cat has 5 questions, `count = 5`: the result is exactly the 5 questions in that sub-cat (the marked one included).
- **Determinism for testability** — when the test seeds `random` consistently, the result is reproducible.

Plus an integration test on the endpoint covering the 409 path and a smoke check that the response shape matches `QuizResponse`.

## Out of scope

- No UI for distinguishing the new behavior from the old. The hard quiz button keeps its current label.
- No analytics or telemetry for tracking how often expansion fires.
- No change to how questions get marked hard in the first place.
- The `count` query parameter still accepts 1–100 and defaults to 30.
