# Hard Questions — Per-User Marking and Hard Quiz Mode

**Date:** 2026-05-01
**Status:** Approved (pending spec review)

## Goal

Let each user mark individual quiz questions as "Hard" and take a quiz drawn primarily from their Hard set. The current Quiz nav button becomes a hover dropdown with **Normal** and **Hard** options. Normal is the existing random quiz. Hard is a 30-question quiz where all of the user's Hard questions are included, padded with random non-Hard fillers if the user has fewer than 30 Hard questions. The Hard option is disabled when the user has zero Hard questions.

## Data model

A new field `tracking.hard_questions` is added to the per-user file `user_data/<email>.json`:

```json
{
  "tracking": {
    "feedback_counts": { ... },
    "hidden_words": [ ... ],
    "difficult_words": [ ... ],
    "hard_questions": [42, 138, 405]
  },
  "quiz_history": [ ... ]
}
```

- `hard_questions` is a list of integer question IDs as assigned by `_flatten_questions` in `backend/app/main.py` (1-based, in traversal order of `quizPatenteB2023.json`).
- `_empty_user_data()` is updated to include `"hard_questions": []`.
- Reads tolerate the field being missing (treat as empty list). No migration step is needed for existing user files; the first toggle persists the new field.
- Unknown IDs in the persisted list (e.g. if the question bank is regenerated) are silently filtered when the Hard quiz endpoint composes a quiz.

## Backend API

All three endpoints require Bearer-token auth via the existing `get_current_user_email` dependency. Reads/writes use the existing `USER_DATA_LOCK` for read-modify-write safety, same as `feedback_counts` updates.

### `GET /api/quiz/hard-questions`

Returns the current user's Hard set.

Response 200:
```json
{ "hard_question_ids": [42, 138, 405] }
```

Rate limit: `60/minute`.

### `PUT /api/quiz/hard-questions/{question_id}`

Toggles a single question's Hard state for the current user.

Request body:
```json
{ "hard": true }
```

Behavior:
- Validates that `question_id` exists in `QUESTION_BY_ID`. Unknown IDs return `404 Not Found`.
- Loads user data, adds or removes the ID from `tracking.hard_questions`, saves.
- Idempotent: setting `hard=true` for an already-Hard question is a no-op; same for `hard=false`.

Response: `204 No Content` on success.

Rate limit: `60/minute`.

### `GET /api/quiz/hard?count=30`

Returns a quiz composed of the user's Hard questions plus random fillers.

Query params:
- `count` (int, default 30, min 1, max 100) — same constraints as `/api/quiz`.

Response shape: identical to `/api/quiz` (`QuizResponse` with a `questions` array of `QuestionOut`).

Algorithm:
1. Load user's `tracking.hard_questions`. Filter to IDs that exist in `QUESTION_BY_ID`.
2. If the filtered set is empty → return `409 Conflict` with detail `"no_hard_questions"`.
3. If `len(Hard) >= count` → take a random sample of `count` questions from the Hard set.
4. Otherwise → take all Hard questions, then random-sample `(count - len(Hard))` fillers from `QUESTION_BANK \ Hard`.
5. Shuffle the combined list and return.

Rate limit: `30/minute` (same as `/api/quiz`).

## Frontend — state

Two new pieces of state in `App.jsx`:

```js
const [hardQuestionIds, setHardQuestionIds] = useState(new Set());
const [quizMode, setQuizMode] = useState("normal"); // "normal" | "hard"
```

Lifecycle:
- On login (when `currentUser` becomes truthy), call `GET /api/quiz/hard-questions` and populate `hardQuestionIds`.
- On logout, clear `hardQuestionIds` (alongside the existing `setQuiz([])` etc.).
- On toggle, optimistically update the Set, call `PUT /api/quiz/hard-questions/{id}`, and revert on error with an inline error message.
- `loadQuiz()` is updated to call `/api/quiz` when `quizMode === "normal"` and `/api/quiz/hard` when `"hard"`.

## Frontend — Quiz nav dropdown

The current "Quiz" header button at `App.jsx:1956` becomes a hover-to-open dropdown:

```
┌────────┐
│  Quiz ▾│
├────────┤
│ Normal │
│ Hard   │
└────────┘
```

Behavior:
- Opens on `mouseenter` of the button container, closes on `mouseleave` after a ~200ms grace delay so users can move from button to menu without it snapping shut.
- Clicking the parent "Quiz" button itself defaults to Normal (preserving today's behavior for users who don't notice the dropdown).
- Clicking "Normal" → `setQuizMode("normal")` + `setMode("quiz")` + `loadQuiz()`.
- Clicking "Hard" → `setQuizMode("hard")` + `setMode("quiz")` + `loadQuiz()`. Switching mid-quiz immediately discards the current quiz (existing `loadQuiz` already replaces `quiz` and `currentIndex`).
- "Hard" is `aria-disabled` and visually grayed out when `hardQuestionIds.size === 0`. Clicking it is a no-op.
- The active-state highlight on the parent button still appears whenever `mode === "quiz"`, regardless of Normal/Hard.
- Keyboard: focusing the parent button + pressing Enter still picks Normal; the dropdown's options are reachable via Tab when open.

Styling: the dropdown uses absolute positioning anchored to the parent button, with the same secondary-button visual language as the rest of the header. New CSS rules go in `frontend/src/styles.css`.

## Frontend — Hard checkbox in Quiz tab

In the question panel (`App.jsx:2168`), a small "Hard" checkbox is added at the top of the panel, to the right of the topic tag:

```
┌─────────────────────────────────────────────────────┐
│  [topic tag]                          ☐ Hard        │
│  🔍 Question text here...                            │
│                                                       │
│  ▸ Mostra la traduzione in inglese                   │
└─────────────────────────────────────────────────────┘
```

- `checked` reflects `hardQuestionIds.has(currentQuestion.id)`.
- `onChange` triggers the optimistic toggle described above.
- For a Hard quiz, the 12 actually-Hard questions arrive with the box pre-checked; the 18 fillers arrive unchecked. The user can toggle either freely — toggling a filler ON adds it to their Hard set; toggling a Hard one OFF removes it.

## Frontend — Hard checkbox in Topics tab

In each topics-question `<li>` (`App.jsx:2116`), a Hard checkbox is added in the bottom-right corner of the row, inside the box:

```
┌─────────────────────────────────────────────┐
│ Question text in Italian...                 │
│ Translation in English...                   │
│                                  ☐ Hard ─── │
└─────────────────────────────────────────────┘
```

- The `<li>` becomes a flex/grid container so the question text fills the top and the Hard checkbox sits at the bottom-right inside the row's box.
- Same wiring as the Quiz tab: `checked` from `hardQuestionIds.has(q.id)`, `onChange` calls the toggle endpoint and updates the Set optimistically.
- The checkbox is interactive in Topics ("browse") view; toggling here updates the same Set used by the Hard quiz.
- New CSS rules in `frontend/src/styles.css` adjust `.topics-question-item` layout and add a `.topics-hard-toggle` class.

## Error handling

- **Toggle fails (network/auth):** revert the optimistic Set change, show an inline error near the checkbox, log to console.
- **`GET /api/quiz/hard-questions` fails on login:** log a warning, continue with an empty Set. Normal quizzes still work; the Hard menu item stays grayed out.
- **`GET /api/quiz/hard` returns `409 no_hard_questions`:** shouldn't happen since the menu is disabled, but if a race occurs, show an inline error and revert `quizMode` to `"normal"`.
- **Question ID in user file no longer exists:** the Hard quiz endpoint silently filters unknown IDs when composing a quiz. The toggle endpoint returns `404` for unknown IDs.

## Testing

### Backend (`tests/`)

New test cases:
- `GET /api/quiz/hard-questions` returns `{"hard_question_ids": []}` for a new user.
- `PUT /api/quiz/hard-questions/{id}` with `hard=true` adds the ID; subsequent `GET` reflects it.
- `PUT` with `hard=false` removes the ID; idempotent for already-removed IDs.
- `PUT` with an unknown `question_id` returns 404.
- `GET /api/quiz/hard` returns 409 `no_hard_questions` when set is empty.
- `GET /api/quiz/hard` returns Hard + random fillers totaling `count` when `len(Hard) < count`, with no duplicates.
- `GET /api/quiz/hard` returns a random sample of `count` from the Hard set when `len(Hard) >= count`, with no duplicates.
- `GET /api/quiz/hard` silently filters unknown question IDs from the persisted set.

### Frontend

No automated test suite exists today; verify manually with Playwright (golden path + edge cases below).

## Manual verification checklist

- Mark a question Hard in the Quiz tab → reload page → checkbox still checked.
- Mark a question Hard in the Topics tab → switch to Quiz → that question's checkbox is pre-checked when it appears.
- Hover Quiz nav with zero Hard → see Normal/Hard menu, Hard grayed out, click is a no-op.
- Mark 12 questions Hard → Hover Quiz → Hard becomes selectable → click → quiz loads with the 12 Hard + 18 random fillers (verify 12 are pre-checked, 18 are not).
- Mid-Normal-quiz, switch to Hard → quiz reloads, prior answers are gone.
- Logout / login → Hard set persists across sessions.
- Toggle a filler ON during a Hard quiz → reload → it's now in the Hard set.
- Toggle a Hard question OFF during a Hard quiz → reload → it's no longer in the set.
