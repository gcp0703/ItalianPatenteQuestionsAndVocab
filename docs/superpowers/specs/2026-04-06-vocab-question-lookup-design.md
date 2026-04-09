# Vocab Word Question Lookup

## Context

Users studying vocabulary want to see the word in context — specifically, which patente quiz questions contain that word. This helps reinforce understanding by connecting vocabulary to real exam questions. Currently vocab and quiz are separate systems with no cross-reference.

## Design

### Backend: New API Endpoint

**`GET /api/vocab/{word}/questions`**

- Searches `QUESTION_BANK` for questions whose `text` field contains the word (exact, case-insensitive)
- No user authentication required (questions are shared data)
- Returns list of matching questions with answers

**Response model:**

```python
class VocabQuestionMatch(BaseModel):
    id: int
    text: str
    answer: bool          # True = Vero, False = Falso
    image_url: str | None
    topic: str

class VocabQuestionsResponse(BaseModel):
    word: str
    questions: list[VocabQuestionMatch]
    count: int
```

**Search logic:** Case-insensitive substring match of the word within each question's `text` field. Use word boundary matching (`re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE)`) to avoid partial matches (e.g., "auto" matching "automatico").

**File:** `backend/app/main.py` — add endpoint near the existing `/api/vocab` routes (~line 1490).

### Frontend: Search Icon + Modal

**Icon placement:** A 🔍 button below the Italian word in the active vocab card, inside the `.vocab-copy` div that contains the word. Small, subtle, not competing with the word itself.

**Location in code:** `frontend/src/App.jsx` ~line 1790, after the `<h3 className="vocab-word">` element and `<VocabFeedbackStats>`.

**On click:**
1. Call `GET /api/vocab/{word}/questions`
2. Store results in new state variable `vocabQuestionResults`
3. Show modal overlay

**Modal overlay:** Reuse existing `.vocab-batch-overlay` pattern from `frontend/src/styles.css` (lines 45-77).

**Modal content:**
- Header: "Questions with '{word}'" and count
- Scrollable list of questions, each showing:
  - Question text (Italian)
  - Vero/Falso badge (green for Vero, red for Falso)
  - Topic label (small, muted)
  - Question image if present
- "No questions found" message if empty
- Close button (X) in top-right corner
- Click outside card to dismiss

**New CSS classes** in `frontend/src/styles.css`:
- `.vocab-questions-modal-card` — based on `.vocab-batch-summary-card`, scrollable, max-height 85vh
- `.vocab-question-item` — individual question row with text + answer badge
- `.vocab-question-answer` — Vero/Falso badge styling (green/red)
- `.vocab-search-button` — small icon button under the word

**New state variables** in App.jsx:
- `vocabQuestionResults` — `{word, questions, count} | null`

**Dismiss:** Set `vocabQuestionResults` to `null` on close button click or overlay background click.

### Files to Modify

1. `backend/app/main.py` — new Pydantic models, new endpoint
2. `frontend/src/App.jsx` — search icon, modal component, state, fetch logic
3. `frontend/src/styles.css` — modal card and question item styles

### Verification

1. Start the backend, navigate to vocab section
2. Click 🔍 on a common word (e.g., "sorpasso", "veicolo") — modal should show matching questions with Vero/Falso answers
3. Click 🔍 on a rare word — modal should show "no questions found"
4. Verify modal scrolls when many questions match
5. Verify close button and click-outside dismiss both work
6. Verify question images display when present
7. Test on mobile viewport — modal should be responsive
