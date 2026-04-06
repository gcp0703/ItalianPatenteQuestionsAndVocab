# Multi-User Email-Based Workspaces

## Context

QuizPatenteB is currently a single-user app. All vocabulary tracking (feedback counts, hidden/difficult words) is stored in a shared JSON file and browser localStorage. There's no concept of user identity. This design adds simple email-based profile selection so multiple people can use the same instance with independent progress tracking and quiz history.

## Requirements

- Email as simple profile selector (no passwords, no verification)
- Per-user: feedback counts, hidden words, difficult words, quiz history
- Shared: AI definitions, dictionary cache, question bank (reference data)
- Per-user JSON files on backend (source of truth)
- Login screen on launch with dropdown of known emails + add new
- Backend-centric approach (Approach A)

## Data Architecture

### Per-User Data Directory

New directory: `user_data/` at project root.

**User registry:** `user_data/_users.json`
```json
{
  "users": [
    { "email": "alice@example.com", "created": "2026-04-06T10:00:00" },
    { "email": "bob@example.com", "created": "2026-04-06T11:00:00" }
  ]
}
```

**Per-user file:** `user_data/<sanitized_email>.json`

Email sanitization: replace `@` with `_at_`, `.` with `_dot_`, other non-alphanumeric with `_`.
Example: `alice@example.com` → `alice_at_example_dot_com.json`

```json
{
  "email": "alice@example.com",
  "tracking": {
    "feedback_counts": {
      "autostrada": { "up": 3, "down": 1 },
      "semaforo": { "up": 5, "down": 0 }
    },
    "hidden_words": ["semaforo", "patente"],
    "difficult_words": ["carreggiata", "svincolo"]
  },
  "quiz_history": [
    {
      "date": "2026-04-06T10:30:00",
      "total": 30,
      "correct": 25
    }
  ]
}
```

### Shared Vocabulary File

`vocabolario_patente.json` retains:
- Word entries with `english`, `ai_definition`, `dictionary_cache`, `frequency`, `difficulty`, `count`, `topics`

After migration, the `tracking` field is **removed** from the shared file. Tracking lives only in per-user files.

## API Changes

### User Identification

All user-scoped endpoints require an `X-User-Email` header. If missing, return 400.

### New Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/users` | List known emails from registry |
| POST | `/api/users` | Register new email, create user file |
| DELETE | `/api/users/{email}` | Remove user and their data file |

### Modified Endpoints

| Endpoint | Change |
|----------|--------|
| `GET /api/vocab` | Merge shared vocab with current user's tracking from their JSON file |
| `POST /api/vocab/tracking` | Read/write user's JSON file instead of shared vocab file |
| `POST /api/score` | Persist quiz result to user's `quiz_history` array |

### Unchanged Endpoints

- `GET /api/health` — no user context needed
- `GET /api/quiz` — questions are shared data
- `GET /api/questions/{id}/translation` — shared data
- `GET /api/questions/{id}/answer` — shared data
- `GET /api/vocab/cache-stats` — shared AI definition stats
- `GET /api/vocab/translate` — shared translation pipeline
- `POST /api/vocab/prefetch` — shared background work

### New Endpoint: Quiz History

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/quiz/history` | Get current user's quiz history |

## Backend Implementation

### File: `backend/app/main.py`

**New constants:**
```python
USER_DATA_DIR = ROOT_DIR / "user_data"
USER_REGISTRY_FILE = USER_DATA_DIR / "_users.json"
USER_DATA_LOCK = Lock()  # Separate lock for user data writes
```

**New helper functions:**
- `sanitize_email(email: str) -> str` — convert email to safe filename
- `get_user_file_path(email: str) -> Path` — return path to user's JSON
- `load_user_data(email: str) -> dict` — read user's JSON (create if missing)
- `save_user_data(email: str, data: dict)` — write user's JSON with lock
- `load_user_registry() -> list[dict]` — read _users.json
- `save_user_registry(users: list[dict])` — write _users.json with lock
- `get_current_user(request: Request) -> str` — extract and validate X-User-Email header

**Modified `persist_vocab_tracking()`:**
- Instead of writing tracking into `vocabolario_patente.json`, write to user's JSON file
- Accept email parameter

**Modified `load_vocab_bank()`:**
- Accept optional email parameter
- If provided, merge user's tracking data into vocab entries
- If not provided, return vocab without tracking (for shared operations)

**New `persist_quiz_result()`:**
- Append quiz result to user's `quiz_history` array

### Pydantic Models

```python
class UserOut(BaseModel):
    email: str
    created: str

class UserCreateIn(BaseModel):
    email: str

class QuizHistoryEntry(BaseModel):
    date: str
    total: int
    correct: int

class QuizHistoryResponse(BaseModel):
    history: list[QuizHistoryEntry]
```

## Frontend Implementation

### File: `frontend/src/App.jsx`

**New state:**
- `currentUser` — email string, `null` if not logged in
- `users` — list of known emails from backend
- `quizHistory` — array of past quiz results

**Login screen:**
- Rendered when `currentUser` is `null`
- Dropdown of known emails fetched from `GET /api/users`
- Text input to add a new email (basic format validation)
- "Continue" button sets `currentUser` and saves to localStorage key `quiz-patente-b-current-user`
- On next launch, auto-select remembered email (still show login screen briefly with it pre-selected)

**localStorage namespacing:**
- All existing storage keys get the email appended: `quiz-patente-b-vocab-feedback-counts-<email>`
- Helper function to generate namespaced keys

**API call changes:**
- All `fetch()` calls to user-scoped endpoints include `X-User-Email` header
- Helper wrapper: `fetchWithUser(url, options)` that auto-adds the header

**Profile indicator:**
- Small text in the app header showing current user email
- No inline switcher — user returns to login screen to switch

**Quiz history section:**
- Accessible from main menu or after completing a quiz
- Simple table/list showing date, score, total questions

## Migration Strategy

1. On backend startup, if `user_data/` doesn't exist, create it with empty `_users.json`
2. Check if `vocabolario_patente.json` has any `tracking` data in word entries
3. If tracking data exists and no users registered yet:
   - Do NOT auto-migrate (we don't know whose data it is)
   - On first user login, offer to import existing tracking as their data
4. After import (or skip), strip `tracking` fields from shared vocab file
5. Frontend: existing localStorage data (without email namespace) is offered for import to the first logged-in user

## Verification

1. **Start app** — login screen appears with empty user list
2. **Add user** — type email, click continue. User file created in `user_data/`
3. **Track words** — mark words as known/difficult, give feedback. Check user JSON file reflects changes
4. **Switch users** — go back to login, add second email. Verify clean slate (no tracking from first user)
5. **Quiz history** — complete a quiz, verify result saved to user file. View history shows it
6. **Persistence** — restart app, log in as first user, verify all tracking data intact
7. **Migration** — test with existing tracked data in vocab file, verify import prompt works
8. **Delete user** — remove a user, verify their data file is deleted
