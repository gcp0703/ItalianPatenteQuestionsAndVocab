# Vocab — User-added custom words (design)

**Date:** 2026-05-11
**Status:** Approved (pending implementation plan)

## Goal

Let a signed-in user add their own Italian words or phrases to the vocab page so
they appear in the study rotation alongside the curated quiz-derived vocabulary.
Custom words are per-user (only the adder sees them) and managed from a new
"My Words" tab inside the vocab page.

## Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Scope / persistence | Per-user. Stored in `user_data/<email>.json`. Other users never see another user's custom words. |
| 2 | What the user provides | Italian word/phrase only. Translation and dictionary metadata are fetched lazily by the existing translate pipeline on first reveal. |
| 3 | Edit / delete | Delete only. Editing the Italian text is equivalent to delete-and-re-add, so no edit affordance is provided. |
| 4 | UI placement | Dedicated **"My Words"** tab/button in the vocab header. Acts as a management surface (list + add + delete). Custom words are studied through the normal flow once added. |
| 5 | Filter visibility | Custom words mix into Unknown / Known / Difficult / Ranked, alongside the curated bank. They are not segregated to the My Words tab for study purposes. |

Defaults locked in during brainstorming:

- Comma-separated input — one Add submission can introduce many entries.
- Words/phrases are lowercased, NFC-normalized, internal whitespace collapsed,
  and trimmed before storage.
- Duplicates of curated bank entries or of the user's existing custom entries
  are silently skipped (returned in `skipped` with a reason for UI surfacing).
- Delete is a hard delete from `user_data/<email>.json`. No shared feedback to
  preserve since the data is per-user.
- For phrases (entries containing whitespace), the "view questions" search
  uses a literal substring match; single-word entries continue to use the
  existing stem-prefix regex. Zero-match results are normal and the UI must
  render an empty-state message.

## Architecture overview

Custom vocabulary is layered on top of the curated bank at request time. The
master file `vocabolario_patente.normalized.json` is **not** touched by this
feature. Storage lives in `user_data/<email>.json` under a new top-level key
`custom_vocab`.

On `GET /api/vocab`, the backend builds the response from `VOCAB_BANK` (curated)
plus the caller's `custom_vocab` entries, projected through the same
`VocabWordOut` shape — so the frontend treats them identically and the existing
source filters work unchanged.

A new **"My Words"** button in the vocab header switches the panel into a
management view that lists the caller's custom entries with a delete action
and an add control accepting comma-separated input. Studying still happens
through the existing study stream once the new words enter the merged bank.

Translation/dictionary metadata for custom words is fetched on first reveal
through the existing `/api/vocab/translate` endpoint (extended to also accept
words outside the master bank). Cached results are written back to the user's
own `user_data/<email>.json`, not to the shared master file.

## Data model

In `user_data/<email>.json`, add one key:

```json
{
  "email": "user@example.com",
  "tracking": { ... },
  "quiz_history": [ ... ],
  "custom_vocab": {
    "diritto di precedenza": {
      "added_at": "2026-05-11T14:23:00Z",
      "english": "",
      "ai_definition": null,
      "ai_definition_failed": false,
      "dictionary_cache": null
    },
    "ciao": {
      "added_at": "2026-05-11T14:25:00Z",
      "english": "",
      "ai_definition": null,
      "ai_definition_failed": false,
      "dictionary_cache": null
    }
  }
}
```

**Key**: the normalized word/phrase (lowercase, NFC-normalized, internal
whitespace collapsed to single spaces, trimmed).

**Fields**:

- `added_at` — ISO 8601 UTC timestamp; informational; allows sort-by-recency
  in the management UI.
- `english` — empty string by default. The translate endpoint writes a
  resolved translation here when it succeeds.
- `ai_definition`, `ai_definition_failed`, `dictionary_cache` — mirror the
  same shape used by `load_vocab_bank` for master-file entries. The
  projection from raw metadata dict to `VocabWordOut` (currently inline
  inside `load_vocab_bank` and `get_vocab`) should be extracted into a
  shared helper so the same code path can build the response for both
  curated and custom entries.

### Validation when adding

- Trim outer whitespace; collapse internal whitespace to single spaces.
- NFC-normalize, then lowercase.
- Reject if empty after normalization.
- Reject if length > 60 characters.
- Reject if the result contains anything other than letters, spaces,
  apostrophes (`'`), or hyphens (`-`).
- Silently skip if the normalized form is already a key in `VOCAB_BY_WORD`
  or in the user's existing `custom_vocab`.

Comma-separated input is split server-side (single source of truth — the
frontend just forwards the raw input string).

### Concurrency

Writes to `custom_vocab` go through the existing `load_user_data` /
`save_user_data` pair, which acquire the module-level `USER_DATA_LOCK`
already used by `persist_vocab_tracking_for_user`. Reads-and-writes that need
to be atomic (load → mutate → save) must be performed inside a single
`with USER_DATA_LOCK:` block; the helpers above guard each call individually
but do not by themselves prevent a lost-update race between a load and a
later save. The new add/delete endpoints will use a dedicated locked
read-modify-write helper to avoid that.

## Backend API

All endpoints authenticate via the existing `get_current_user_email`
dependency.

### `POST /api/vocab/custom`

Add one or more words.

- **Request:**
  ```json
  { "input": "ciao, diritto di precedenza, semaforo" }
  ```
- **Behavior:** split on comma, then for each entry: trim → collapse
  whitespace → NFC → lowercase → validate → dedupe against `VOCAB_BANK` and
  the user's `custom_vocab`. Valid entries are written atomically with
  `added_at = now (UTC)`. Skipped entries carry a `reason`.
- **Response:**
  ```json
  {
    "added": ["ciao", "diritto di precedenza"],
    "skipped": [
      { "input": "semaforo", "reason": "already_in_bank" },
      { "input": "x!",       "reason": "invalid_chars" }
    ]
  }
  ```
- **Skip reasons:** `already_in_bank`, `already_custom`, `empty`, `too_long`,
  `invalid_chars`.

### `DELETE /api/vocab/custom/{word}`

Delete one of the caller's custom words.

- Path param is normalized server-side before lookup (so callers can pass
  any casing).
- **204** on success; **404** if the normalized word is not present in the
  caller's `custom_vocab`.
- Tracking entries (`feedback_counts[word]`, membership in `hidden_words` or
  `difficult_words`) are also removed in the same atomic write — once the
  word is gone from the user's bank it should not carry stale stats.

### `GET /api/vocab/custom`

List the caller's custom words for the management UI.

- **Response:**
  ```json
  {
    "words": [
      {
        "word": "ciao",
        "added_at": "2026-05-11T14:25:00Z",
        "english": "",
        "tracking": { "up": 0, "down": 0, "known": false, "difficult": false }
      },
      ...
    ]
  }
  ```
- `tracking` mirrors the shape used by `/api/vocab` so the management view
  can render feedback counts inline.

### Modified endpoints

- **`GET /api/vocab`** — after building the curated `words` list, append
  projected entries from the caller's `custom_vocab`. The merge happens
  inside `get_vocab` itself, which already calls `load_user_data(email)`.
  Tracking is read from the same `tracking.feedback_counts` /
  `tracking.hidden_words` / `tracking.difficult_words` maps as for curated
  words.

- **`GET /api/vocab/translate?word=...`** — currently 404s if the word is
  not in `VOCAB_BY_WORD`. Extension: if the caller is authenticated *and*
  the word exists in their `custom_vocab`, treat that entry as the source.
  Writes back `ai_definition` / `dictionary_cache` / `english` into
  `user_data/<email>.json` instead of the master file.

- **`POST /api/vocab/tracking`** — no change. `persist_vocab_tracking_for_user`
  already writes per-user, keyed by word string, indifferent to whether the
  word originated in the master bank or a custom list.

- **`GET /api/vocab/{word}/questions`** — extend the matching logic: if
  `word` contains whitespace, use a literal substring match
  (`re.escape(word)`, case-insensitive); otherwise use the existing
  stem-prefix regex. Zero matches return `count: 0` (already the natural
  behavior) — no error.

### New persistence helper

```
persist_custom_vocab_metadata(
    email: str,
    word: str,
    *,
    ai_definition: str | None = None,
    ai_definition_failed: bool | None = None,
    dictionary_cache: dict | None = None,
    english: str | None = None,
) -> None
```

Single locked write path used by the translate endpoint when it resolves
metadata for a custom word. Only fields passed as non-`None` are updated.

## Frontend UI

### Header button

A new `"My Words"` button is appended to the vocab source-actions row, between
`Ranked` and `Reset`. Clicking it switches the vocab panel into management mode
(separate from the study stream).

### Management mode layout

```
Vocab    Unknown | Known | Difficult | Ranked | My Words | Reset

[ Add words (comma-separated)                              ] [ Add ]

┌──────────────────────────────────────────────────────────────────┐
│ diritto di precedenza   👍 3  👎 1   added 2 days ago        [×] │
│ semaforo                👍 0  👎 0   added today              [×] │
│ ...                                                              │
└──────────────────────────────────────────────────────────────────┘

[Inline toast: "Added 2, skipped 1 (already in bank)"]
```

### State

- New React state `customVocab` in `App.jsx`, populated by
  `GET /api/vocab/custom` when entering My Words mode.
- `vocabSource` gets a new value `"custom"` purely for active-button styling
  — it does not drive `loadVocab`, since My Words is a management view, not
  a study source.
- The study card and batch progress are hidden in My Words mode.

### Add flow

1. User types into the input, presses Enter or clicks Add.
2. Frontend calls `POST /api/vocab/custom` with the raw input string.
3. On success: refresh `customVocab` via `GET /api/vocab/custom`, then
   invalidate the cached `vocabBank` (set to `[]`) so the next study load
   refetches the merged bank from `/api/vocab`.
4. Render an inline summary using the response: e.g.
   `"Added 2. Skipped 1 (already in bank: semaforo)."` Toast auto-dismisses
   after ~4 seconds and is exposed via `aria-live="polite"`.

### Delete flow

1. User clicks `×` on a row.
2. Confirm via `window.confirm("Remove this word?")`.
3. Call `DELETE /api/vocab/custom/{word}`.
4. On success: remove from local `customVocab` list, invalidate `vocabBank`.

### Empty state

> You haven't added any words yet. Type one or more Italian words above,
> separated by commas, then click Add.

### "View questions" empty state (for any vocab word)

When `GET /api/vocab/{word}/questions` returns `count: 0`, the existing
`vocabQuestionResults` panel must show a clear message
(e.g. "No quiz questions contain this word.") rather than rendering blank
space. This applies to curated words too but is most likely for user-added
phrases.

### Styling

New CSS classes sharing visual tokens with the existing vocab styles:

- `.vocab-custom-panel`
- `.vocab-custom-add` (input + button row)
- `.vocab-custom-list`
- `.vocab-custom-row`
- `.vocab-custom-delete`
- `.vocab-custom-empty`
- `.vocab-custom-toast`

### Caching

No `localStorage` caching of `customVocab` — the server is the source of
truth and the list is small. The existing `localStorage` keys for
hidden/difficult/feedback continue to work because they are keyed by user
email and are indifferent to whether a word is curated or custom.

## Testing

### Backend (pytest)

`POST /api/vocab/custom`:

- Single valid word.
- Multiple valid entries comma-separated.
- Mixed valid + invalid in one request.
- All-invalid input.
- Duplicate against `VOCAB_BANK` → `already_in_bank`.
- Duplicate against existing `custom_vocab` → `already_custom`.
- Empty / whitespace-only entries silently dropped.
- Length validation (> 60 chars → `too_long`).
- Character validation (digits / punctuation → `invalid_chars`).
- Normalization: input `"  CIAO  "` is stored as `"ciao"`.
- Whitespace collapse: `"diritto   di  precedenza"` stored as
  `"diritto di precedenza"`.
- NFC normalization round-trip.

`DELETE /api/vocab/custom/{word}`:

- 204 on success.
- 404 when the word isn't in caller's `custom_vocab`.
- Path normalization: `DELETE /api/vocab/custom/CIAO` deletes `"ciao"`.
- One user cannot delete another user's word (different email, isolated
  `user_data` files).
- Tracking cleanup: deleting a custom word also removes that word from
  `tracking.feedback_counts`, `tracking.hidden_words`, and
  `tracking.difficult_words` in the same atomic write.

`GET /api/vocab/custom`:

- Empty list for a new user.
- Returned `tracking` shape matches `/api/vocab`.

`GET /api/vocab`:

- Caller's custom words are merged into the response.
- Another user's request to the same endpoint does not see them.
- Tracking (`up`/`down`/`known`/`difficult`) on a custom word is reflected
  in the response.

`GET /api/vocab/translate?word=...`:

- Custom word resolves (no 404).
- `ai_definition` and `dictionary_cache` are persisted into the caller's
  `user_data` file, never the master file.

`GET /api/vocab/{word}/questions`:

- Phrase with spaces uses literal substring match.
- Phrase with zero matches returns `count: 0` with `questions: []`.
- Single-word lookups behave identically to before.

Concurrency:

- Two concurrent `POST /api/vocab/custom` calls for the same user do not
  lose entries (lock test using threads).

### Frontend (manual / Playwright)

- Add a single word → appears in My Words list and (after re-entering
  Unknown) in the study queue.
- Add comma-separated input → multiple entries added; skipped reasons
  surface in the toast.
- Add a duplicate of a curated word → skipped reason shown.
- Delete a word → disappears from My Words; gone from study queue on next
  load.
- View questions for a phrase with no matches → clear empty-state message.
- Custom word with no English provided → reveal triggers translate pipeline;
  result displays; reloading the page shows the cached translation.

### Manual smoke test before claiming done

1. Sign in. Open vocab. Switch to My Words. Confirm empty state.
2. Add `"diritto di precedenza, ciao, abbagliante"` →
   expect 2 added, `abbagliante` skipped (already in curated bank).
3. Switch to Unknown. Confirm `ciao` and `diritto di precedenza` are
   reachable in rotation (may take a few next-batch clicks; verify via the
   word appearing in the queue at least once over multiple cycles).
4. Reveal `ciao` → translation appears.
5. Reload the page → reveal `ciao` again → translation is served from cache.
6. Switch back to My Words. Click delete on `ciao`. Confirm. Verify it's
   gone from the management list.
7. Switch to Unknown → confirm `ciao` no longer appears in rotation.
8. Click "view questions" on `diritto di precedenza` → empty-state copy
   renders (assuming the phrase doesn't appear in any quiz question).

## Out of scope

- Sharing custom words across users.
- Bulk import from CSV / file upload.
- Editing the Italian text of an existing custom word.
- Tags / folders / collections within a user's custom list.
- A "review only my custom words" study filter (current behavior already
  mixes them into Unknown / Ranked, which is sufficient).
