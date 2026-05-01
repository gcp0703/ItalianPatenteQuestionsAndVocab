import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AVAILABLE_LANGUAGES,
  DEFAULT_LANGUAGE,
  LANGUAGE_STORAGE_KEY,
} from "./languages";

const CURRENT_USER_STORAGE_KEY = "quiz-patente-b-current-user";
const AUTH_TOKEN_STORAGE_KEY = "quiz-patente-b-auth-token";
const VOCAB_HIDDEN_WORDS_STORAGE_KEY = "quiz-patente-b-hidden-vocab-words";
const VOCAB_FEEDBACK_COUNTS_STORAGE_KEY = "quiz-patente-b-vocab-feedback-counts";
const VOCAB_DIFFICULT_WORDS_STORAGE_KEY = "quiz-patente-b-vocab-difficult-words";
const VOCAB_BATCH_SIZE = 20;
const VOCAB_BATCH_OVERLAY_MS = 2000;
const VOCAB_SOURCE_RANDOM = "random";
const VOCAB_SOURCE_KNOWN = "known";
const VOCAB_SOURCE_DIFFICULT = "difficult";
const VOCAB_SOURCE_RANKED = "ranked";

function getUserStorageKey(baseKey, email) {
  return `${baseKey}-${email}`;
}

function getSavedUser() {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(CURRENT_USER_STORAGE_KEY) || null;
}

function saveCurrentUser(email) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(CURRENT_USER_STORAGE_KEY, email);
}

function getSavedToken() {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY) || null;
}

function saveAuthToken(token) {
  if (typeof window === "undefined") return;
  if (token) {
    window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
  } else {
    window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
  }
}

function clearAuth() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
  window.localStorage.removeItem(CURRENT_USER_STORAGE_KEY);
}

async function fetchWithUser(url, options = {}, _email) {
  const headers = { ...(options.headers || {}) };
  const token = getSavedToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401 && typeof window !== "undefined") {
    clearAuth();
    window.dispatchEvent(new CustomEvent("qpb-auth-required"));
  }
  return response;
}

const emptyState = {
  quiz: [],
  answers: {},
  currentIndex: 0,
  result: null,
  translations: {},
  loading: true,
  submitError: "",
  screenError: ""
};

function loadHiddenVocabWords(email) {
  if (typeof window === "undefined") {
    return [];
  }

  try {
    const key = email ? getUserStorageKey(VOCAB_HIDDEN_WORDS_STORAGE_KEY, email) : VOCAB_HIDDEN_WORDS_STORAGE_KEY;
    const stored = window.localStorage.getItem(key);
    const parsed = stored ? JSON.parse(stored) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function loadVocabFeedbackCounts(email) {
  if (typeof window === "undefined") {
    return {};
  }

  try {
    const key = email ? getUserStorageKey(VOCAB_FEEDBACK_COUNTS_STORAGE_KEY, email) : VOCAB_FEEDBACK_COUNTS_STORAGE_KEY;
    const stored = window.localStorage.getItem(key);
    const parsed = stored ? JSON.parse(stored) : {};
    return normalizeFeedbackCounts(parsed);
  } catch {
    return {};
  }
}

function loadDifficultVocabWords(email) {
  if (typeof window === "undefined") {
    return [];
  }

  try {
    const key = email ? getUserStorageKey(VOCAB_DIFFICULT_WORDS_STORAGE_KEY, email) : VOCAB_DIFFICULT_WORDS_STORAGE_KEY;
    const stored = window.localStorage.getItem(key);
    const parsed = stored ? JSON.parse(stored) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function shuffleItems(items) {
  const shuffled = [...items];

  for (let index = shuffled.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(Math.random() * (index + 1));
    [shuffled[index], shuffled[swapIndex]] = [shuffled[swapIndex], shuffled[index]];
  }

  return shuffled;
}

function wait(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function getFeedbackStats(counts, word) {
  const current = counts[word];
  return {
    up: current?.up ?? 0,
    down: current?.down ?? 0
  };
}

function isWordKnown(word, feedbackCounts, hiddenWords) {
  const stats = getFeedbackStats(feedbackCounts, word);
  if (stats.up > 0 || stats.down > 0) {
    return stats.up > 0 && stats.up >= stats.down;
  }

  const hiddenSet = new Set(hiddenWords);
  return hiddenSet.has(word);
}

function deriveKnownWords(bank, feedbackCounts, hiddenWords) {
  return bank
    .filter((item) => isWordKnown(item.word, feedbackCounts, hiddenWords))
    .map((item) => item.word);
}

function getUnknownVocabEntries(bank, hiddenWords, feedbackCounts) {
  return bank.filter((item) => !isWordKnown(item.word, feedbackCounts, hiddenWords));
}

function getKnownVocabEntries(bank, hiddenWords, feedbackCounts) {
  return bank.filter((item) => isWordKnown(item.word, feedbackCounts, hiddenWords));
}

function createBatchAttemptCounts(batch) {
  return Object.fromEntries(batch.map((item) => [item.word, 0]));
}

function normalizeFeedbackCounts(counts) {
  if (!counts || typeof counts !== "object" || Array.isArray(counts)) {
    return {};
  }

  return Object.fromEntries(
    Object.entries(counts).map(([word, stats]) => [
      word,
      {
        up: Number.isFinite(stats?.up) ? Math.max(stats.up, 0) : 0,
        down: Number.isFinite(stats?.down) ? Math.max(stats.down, 0) : 0
      }
    ])
  );
}

function mergeTrackedWords(localWords, bank, key) {
  const bankWords = new Set(bank.map((item) => item.word));
  const merged = new Set(Array.isArray(localWords) ? localWords : []);

  for (const word of Array.from(merged)) {
    if (!bankWords.has(word)) {
      merged.delete(word);
    }
  }

  bank.forEach((item) => {
    if (item?.tracking?.[key]) {
      merged.add(item.word);
    }
  });

  return Array.from(merged);
}

function mergeTrackedFeedbackCounts(localCounts, bank) {
  const bankWords = new Set(bank.map((item) => item.word));
  const merged = Object.fromEntries(
    Object.entries(normalizeFeedbackCounts(localCounts)).filter(([word]) => bankWords.has(word))
  );

  bank.forEach((item) => {
    const current = merged[item.word] ?? { up: 0, down: 0 };
    merged[item.word] = {
      up: Math.max(current.up, item?.tracking?.up ?? 0),
      down: Math.max(current.down, item?.tracking?.down ?? 0)
    };
  });

  return merged;
}

function createNextFeedbackCounts(counts, word, direction) {
  const current = counts[word] ?? { up: 0, down: 0 };
  return {
    ...counts,
    [word]: {
      up: current.up + (direction === "up" ? 1 : 0),
      down: current.down + (direction === "down" ? 1 : 0)
    }
  };
}

function createKnownModeDownFeedbackCounts(counts, word) {
  const current = counts[word] ?? { up: 0, down: 0 };
  return {
    ...counts,
    [word]: {
      up: 0,
      down: current.down + 1
    }
  };
}

function createUnknownFeedbackCounts(counts, word) {
  const current = counts[word] ?? { up: 0, down: 0 };
  return {
    ...counts,
    [word]: {
      up: 0,
      down: current.down
    }
  };
}

function getRankedVocabEntries(entries, feedbackCounts) {
  return [...entries].sort((left, right) => {
    const leftDown = feedbackCounts[left.word]?.down ?? 0;
    const rightDown = feedbackCounts[right.word]?.down ?? 0;
    if (rightDown !== leftDown) {
      return rightDown - leftDown;
    }

    return left.word.localeCompare(right.word);
  });
}

function getVocabSourceEntries(bank, hiddenWords, source, difficultWords, feedbackCounts) {
  const unknownEntries = getUnknownVocabEntries(bank, hiddenWords, feedbackCounts);
  const knownEntries = getKnownVocabEntries(bank, hiddenWords, feedbackCounts);

  if (source === VOCAB_SOURCE_KNOWN) {
    return shuffleItems(knownEntries);
  }

  if (source === VOCAB_SOURCE_DIFFICULT) {
    const difficultSet = new Set(difficultWords);
    const difficultEntries = shuffleItems(unknownEntries.filter((item) => difficultSet.has(item.word)));
    const nonDifficultEntries = shuffleItems(unknownEntries.filter((item) => !difficultSet.has(item.word)));
    return [...difficultEntries, ...nonDifficultEntries];
  }

  if (source === VOCAB_SOURCE_RANKED) {
    return getRankedVocabEntries(unknownEntries, feedbackCounts);
  }

  return shuffleItems(unknownEntries);
}

function createVocabBatch(bank, hiddenWords, source, difficultWords, feedbackCounts) {
  const sourceEntries = getVocabSourceEntries(bank, hiddenWords, source, difficultWords, feedbackCounts);
  const batch = sourceEntries.slice(0, VOCAB_BATCH_SIZE);
  const queue = [...batch];

  return {
    batch,
    queue,
    entry: queue[0] ?? null,
    roundRestarted: false
  };
}

function getNextBatchStep(batch, queue, completedWords) {
  const nextQueue = queue.slice(1);

  if (nextQueue.length > 0) {
    return {
      batch,
      queue: nextQueue,
      entry: nextQueue[0],
      roundRestarted: false
    };
  }

  const completedSet = new Set(completedWords);
  const remainingBatchEntries = batch.filter((item) => !completedSet.has(item.word));
  const recycledQueue = remainingBatchEntries;

  return {
    batch,
    queue: recycledQueue,
    entry: recycledQueue[0] ?? null,
    roundRestarted: recycledQueue.length > 0
  };
}

function parseVocabMeanings(translation) {
  if (!translation) {
    return { primary: "", variants: [] };
  }

  const parts = translation
    .split("/")
    .map((item) => item.trim())
    .filter(Boolean);

  return {
    primary: parts[0] ?? translation,
    variants: parts.slice(1)
  };
}

function getTranslationText(translation) {
  if (!translation) {
    return "";
  }

  if (typeof translation === "string") {
    return translation;
  }

  // Prefer the primary translation (Google Translate — most common meaning)
  // over dictionary meanings (which can be obscure technical definitions).
  return translation.translation ?? "";
}

function getTranslationKey(translation) {
  if (!translation) {
    return "none";
  }

  if (typeof translation === "string") {
    return translation;
  }

  return [
    translation.translation ?? "",
    translation.dictionary?.lookup_word ?? "",
    translation.dictionary?.meanings?.join("|") ?? ""
  ].join("::");
}

function VocabHelpChip() {
  const chipRef = useRef(null);
  const [pos, setPos] = useState(null);

  function show() {
    const el = chipRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const tooltipWidth = 280;
    const margin = 12;
    // Anchor the tooltip's right edge to the chip's right edge so it grows
    // leftward, clamped so it doesn't escape the viewport on either side.
    let left = rect.right - tooltipWidth;
    if (left < margin) left = margin;
    if (left + tooltipWidth > window.innerWidth - margin) {
      left = window.innerWidth - tooltipWidth - margin;
    }
    setPos({ top: rect.bottom + 8, left });
  }

  function hide() {
    setPos(null);
  }

  return (
    <span
      ref={chipRef}
      className="vocab-help-chip"
      role="img"
      aria-label="Help: what do these buttons do?"
      tabIndex={0}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      ?
      {pos && createPortal(
        <span
          className="vocab-help-tooltip"
          role="tooltip"
          style={{ top: `${pos.top}px`, left: `${pos.left}px` }}
        >
          <span className="vocab-help-tooltip-line">Thumbs-up means you know it</span>
          <span className="vocab-help-tooltip-line">Thumbs-down means you don't</span>
          <span className="vocab-help-tooltip-line">The fretting face means it's tough, so add it to the difficult word list</span>
        </span>,
        document.body
      )}
    </span>
  );
}

function escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Mirrors backend/app/main.py:get_vocab_word_questions — stem-prefix match for
// 4+ char stems (after stripping trailing aeio), otherwise exact word boundary.
function highlightWordInText(text, word) {
  if (!text || !word) return text;
  const stem = word.replace(/[aeio]+$/i, "");
  const pattern =
    stem.length >= 4
      ? new RegExp(`\\b${escapeRegex(stem)}[\\p{L}\\p{N}_]*`, "giu")
      : new RegExp(`\\b${escapeRegex(word)}\\b`, "giu");
  const parts = [];
  let lastIndex = 0;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    parts.push(<strong key={`m-${match.index}`}>{match[0]}</strong>);
    lastIndex = match.index + match[0].length;
    if (match[0].length === 0) pattern.lastIndex++;
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex));
  return parts;
}

function VocabFeedbackStats({ stats, onSearch }) {
  return (
    <p className="vocab-feedback-stats">
      <span>👍 {stats.up}</span>
      <span>👎 {stats.down}</span>
      {onSearch && (
        <button
          className="vocab-search-button"
          title="Show all the questions that include forms of this word"
          aria-label="Show all the questions that include forms of this word"
          onClick={onSearch}
        >
          📋
        </button>
      )}
    </p>
  );
}

function VocabTranslation({ translation, hidden = false, tone = null }) {
  const translationText = getTranslationText(translation);

  if (hidden || !translationText) {
    return <p className="vocab-translation vocab-translation-hidden">?</p>;
  }

  const { primary, variants } = parseVocabMeanings(translationText);
  const dictionary = typeof translation === "string" ? null : translation.dictionary;
  const revealClassName =
    tone == null ? "" : tone === "up" ? "vocab-translation-revealed-up" : "vocab-translation-revealed-down";

  return (
    <div className="vocab-translation-block">
      <p className={`vocab-translation ${revealClassName}`.trim()}>{primary}</p>
      {variants.length > 0 && (
        <div className="vocab-translation-variants">
          <p className="vocab-translation-variants-label">Variant meanings</p>
          <ul className="vocab-translation-variants-list">
            {variants.map((variant) => (
              <li key={variant}>{variant}</li>
            ))}
          </ul>
        </div>
      )}
      {dictionary?.meanings?.length > 0 && (
        <div className="vocab-translation-variants">
          <p className="vocab-translation-variants-label">Dictionary meanings</p>
          <ul className="vocab-translation-variants-list">
            {dictionary.meanings.map((meaning) => (
              <li key={meaning}>{meaning}</li>
            ))}
          </ul>
        </div>
      )}
      {dictionary?.related?.length > 0 && (
        <div className="vocab-translation-variants">
          <p className="vocab-translation-variants-label">Related phrases</p>
          <ul className="vocab-translation-variants-list">
            {dictionary.related.map((item) => (
              <li key={`${item.term}-${item.english}`}>
                <strong>{item.term}</strong>: {item.english}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function LoginScreen({ onLogin }) {
  const [mode, setMode] = useState("register"); // "register" | "existing"
  const [email, setEmail] = useState("");
  const [token, setToken] = useState("");
  const [issuedToken, setIssuedToken] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);

  function loginAs(emailLower, tokenValue) {
    saveAuthToken(tokenValue);
    saveCurrentUser(emailLower);
    onLogin(emailLower);
  }

  async function handleRegister() {
    const e = email.trim().toLowerCase();
    if (!e.includes("@")) {
      setError("Inserisci un indirizzo email valido.");
      return;
    }
    setError("");
    setBusy(true);
    try {
      const res = await fetch("/api/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: e }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Impossibile creare l'utente.");
      }
      const body = await res.json();
      setIssuedToken(body.token);
      saveAuthToken(body.token);
      saveCurrentUser(e);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleExisting() {
    const e = email.trim().toLowerCase();
    const t = token.trim();
    if (!e.includes("@") || !t) {
      setError("Inserisci email e token.");
      return;
    }
    setError("");
    setInfo("");
    setBusy(true);
    try {
      saveAuthToken(t);
      const res = await fetch("/api/auth/whoami", {
        headers: { Authorization: `Bearer ${t}` },
      });
      if (!res.ok) {
        saveAuthToken(null);
        throw new Error("Token non valido per questa email.");
      }
      const body = await res.json();
      if (body.email !== e) {
        saveAuthToken(null);
        throw new Error("Il token non corrisponde all'email indicata.");
      }
      loginAs(e, t);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleForgotToken() {
    const e = email.trim().toLowerCase();
    if (!e.includes("@")) {
      setError("Inserisci la tua email per ricevere un nuovo token.");
      return;
    }
    setError("");
    setInfo("");
    setBusy(true);
    try {
      await fetch("/api/auth/forgot-token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: e }),
      });
      // Always show the same message — the server is anti-enumeration.
      setInfo(
        "Se questa email è registrata, ti abbiamo inviato un nuovo token. " +
        "Controlla la posta (anche lo spam). Il token precedente è stato " +
        "invalidato."
      );
      setToken("");
    } catch {
      setInfo(
        "Se questa email è registrata, ti abbiamo inviato un nuovo token."
      );
    } finally {
      setBusy(false);
    }
  }

  if (issuedToken) {
    return (
      <main className="app-shell">
        <section className="hero-card">
          <p className="eyebrow">Quiz Patente B</p>
          <h1>Salva il tuo token</h1>
        </section>
        <section className="login-panel">
          <p>
            Account creato per <strong>{email.trim().toLowerCase()}</strong>. Il
            token qui sotto è la tua chiave d'accesso: copialo e conservalo in
            un posto sicuro. Non potrai recuperarlo in seguito.
          </p>
          <pre className="login-input" style={{ wordBreak: "break-all" }}>{issuedToken}</pre>
          <button
            className="primary-button login-button"
            onClick={() => onLogin(email.trim().toLowerCase())}
          >
            Ho salvato il token, continua
          </button>
        </section>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <section className="hero-card">
        <div className="hero-brand">
          <img className="hero-icon" src="/app-icon.svg" alt="Quiz Patente B" />
          <div>
            <p className="eyebrow">Quiz Patente B</p>
            <h1>Accesso</h1>
          </div>
        </div>
      </section>

      <section className="login-panel">
        <div className="login-mode-tabs">
          <button
            type="button"
            className={mode === "register" ? "primary-button" : "secondary-button"}
            onClick={() => { setMode("register"); setError(""); }}
          >
            Nuovo utente
          </button>
          <button
            type="button"
            className={mode === "existing" ? "primary-button" : "secondary-button"}
            onClick={() => { setMode("existing"); setError(""); }}
          >
            Ho già un token
          </button>
        </div>

        <div className="login-new">
          <label className="login-label" htmlFor="login-email">Email</label>
          <input
            id="login-email"
            className="login-input"
            type="email"
            placeholder="email@esempio.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && mode === "register") handleRegister();
            }}
            disabled={busy}
          />
        </div>

        {mode === "existing" && (
          <>
            <div className="login-new">
              <label className="login-label" htmlFor="login-token">Token</label>
              <input
                id="login-token"
                className="login-input"
                type="text"
                placeholder="32 caratteri esadecimali"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleExisting(); }}
                disabled={busy}
              />
            </div>
            <button
              type="button"
              className="login-forgot-link"
              onClick={handleForgotToken}
              disabled={busy}
            >
              Token dimenticato? Inviamene uno nuovo via email
            </button>
          </>
        )}

        {error && <p className="inline-error">{error}</p>}
        {info && <p className="inline-info">{info}</p>}

        <button
          className="primary-button login-button"
          onClick={mode === "register" ? handleRegister : handleExisting}
          disabled={busy}
        >
          {mode === "register" ? "Crea account" : "Accedi"}
        </button>
      </section>
    </main>
  );
}

function App() {
  const [currentUser, setCurrentUser] = useState(getSavedUser);
  const [mode, setMode] = useState("quiz");
  const [quizMode, setQuizMode] = useState("normal"); // "normal" | "hard"
  const [hardQuestionIds, setHardQuestionIds] = useState(() => new Set());
  const [hardToggleError, setHardToggleError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [language, setLanguage] = useState(() => {
    if (typeof window === "undefined") return DEFAULT_LANGUAGE;
    return window.localStorage.getItem(LANGUAGE_STORAGE_KEY) || DEFAULT_LANGUAGE;
  });
  const [quiz, setQuiz] = useState(emptyState.quiz);
  const [answers, setAnswers] = useState(emptyState.answers);
  const [currentIndex, setCurrentIndex] = useState(emptyState.currentIndex);
  const [result, setResult] = useState(emptyState.result);
  const [translations, setTranslations] = useState(emptyState.translations);
  const [loading, setLoading] = useState(emptyState.loading);
  const [submitting, setSubmitting] = useState(false);
  const [cheating, setCheating] = useState(false);
  const [submitError, setSubmitError] = useState(emptyState.submitError);
  const [screenError, setScreenError] = useState(emptyState.screenError);
  const [vocabBank, setVocabBank] = useState([]);
  const [vocabDefinitionsCachedPercent, setVocabDefinitionsCachedPercent] = useState(null);
  const [vocabCurrent, setVocabCurrent] = useState(null);
  const [vocabCurrentTranslation, setVocabCurrentTranslation] = useState(null);
  const [vocabRevealTone, setVocabRevealTone] = useState(null);
  const [vocabHistory, setVocabHistory] = useState([]);
  const [vocabBatch, setVocabBatch] = useState([]);
  const [vocabQueue, setVocabQueue] = useState([]);
  const [vocabBatchSolvedWords, setVocabBatchSolvedWords] = useState([]);
  const [vocabBatchAttempts, setVocabBatchAttempts] = useState({});
  const [vocabBatchResults, setVocabBatchResults] = useState([]);
  const [vocabSource, setVocabSource] = useState(VOCAB_SOURCE_RANDOM);
  const [vocabBatchNumber, setVocabBatchNumber] = useState(0);
  const [vocabRoundNumber, setVocabRoundNumber] = useState(0);
  const [vocabBatchOverlay, setVocabBatchOverlay] = useState(null);
  const [vocabBatchSummary, setVocabBatchSummary] = useState(null);
  const [vocabHiddenWords, setVocabHiddenWords] = useState(() => loadHiddenVocabWords(currentUser));
  const [vocabDifficultWords, setVocabDifficultWords] = useState(() => loadDifficultVocabWords(currentUser));
  const [vocabFeedbackCounts, setVocabFeedbackCounts] = useState(() => loadVocabFeedbackCounts(currentUser));
  const [vocabLoading, setVocabLoading] = useState(false);
  const [vocabRevealing, setVocabRevealing] = useState(false);
  const [vocabPendingTransition, setVocabPendingTransition] = useState(null);
  const [vocabError, setVocabError] = useState("");
  const [quizHistory, setQuizHistory] = useState([]);
  const [vocabQuestionResults, setVocabQuestionResults] = useState(null);
  const [quizVariantResults, setQuizVariantResults] = useState(null);
  const [topics, setTopics] = useState([]);
  const [selectedCategory, setSelectedCategory] = useState("");
  const [selectedSubtopic, setSelectedSubtopic] = useState("");
  const [topicAnswerFilter, setTopicAnswerFilter] = useState(true);
  const [topicQuestions, setTopicQuestions] = useState([]);
  const [topicsLoading, setTopicsLoading] = useState(false);
  const [includeTranslations, setIncludeTranslations] = useState(false);
  const vocabBatchSummaryResolverRef = useRef(null);
  const knownVocabWords = deriveKnownWords(vocabBank, vocabFeedbackCounts, vocabHiddenWords);

  async function handleLogin(email) {
    setCurrentUser(email);
    setVocabHiddenWords(loadHiddenVocabWords(email));
    setVocabDifficultWords(loadDifficultVocabWords(email));
    setVocabFeedbackCounts(loadVocabFeedbackCounts(email));
    setVocabBank([]);
    setLoading(true);

    // Check for legacy tracking data to migrate (only once)
    const migrationKey = "quiz-patente-b-migration-asked";
    if (!window.localStorage.getItem(migrationKey)) {
      try {
        const res = await fetch("/api/legacy-tracking");
        if (res.ok) {
          const data = await res.json();
          if (data.has_tracking) {
            const shouldMigrate = window.confirm(
              `Sono stati trovati dati di tracciamento esistenti per ${data.tracked_count} parole.\n\nVuoi importarli nel tuo profilo?`
            );
            window.localStorage.setItem(migrationKey, "true");
            if (shouldMigrate) {
              await fetchWithUser("/api/migrate", { method: "POST" }, email);
            }
          }
        }
      } catch {
        // ignore migration errors
      }
    }
  }

  function handleLogout() {
    clearAuth();
    setCurrentUser(null);
    setVocabBank([]);
    setVocabCurrent(null);
    setVocabCurrentTranslation(null);
    setQuiz([]);
    setResult(null);
    setQuizHistory([]);
    setMode("quiz");
  }

  function handleLanguageChange(nextCode) {
    setLanguage(nextCode);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, nextCode);
    }
  }

  useEffect(() => {
    function onAuthRequired() {
      setCurrentUser(null);
      setVocabBank([]);
      setVocabCurrent(null);
      setVocabCurrentTranslation(null);
      setQuiz([]);
      setResult(null);
      setQuizHistory([]);
      setMode("quiz");
    }
    window.addEventListener("qpb-auth-required", onAuthRequired);
    return () => window.removeEventListener("qpb-auth-required", onAuthRequired);
  }, []);

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

  useEffect(() => {
    if (mode !== "topics" || topics.length > 0) return;
    fetch("/api/topics")
      .then((r) => r.json())
      .then((data) => setTopics(data.topics || []))
      .catch((err) => setScreenError(err.message));
  }, [mode]);

  const topicHierarchy = useMemo(() => {
    const map = new Map();
    for (const t of topics) {
      const idx = t.indexOf(" / ");
      const category = idx === -1 ? t : t.slice(0, idx);
      const subtopic = idx === -1 ? "" : t.slice(idx + 3);
      if (!map.has(category)) map.set(category, []);
      map.get(category).push(subtopic);
    }
    return map;
  }, [topics]);

  const categories = useMemo(
    () => [...topicHierarchy.keys()].sort(),
    [topicHierarchy],
  );

  const subtopicsForCategory = selectedCategory
    ? (topicHierarchy.get(selectedCategory) || [])
    : [];

  useEffect(() => {
    if (!selectedCategory) {
      setSelectedSubtopic("");
      return;
    }
    const subs = topicHierarchy.get(selectedCategory) || [];
    if (subs.length === 0) {
      setSelectedSubtopic("");
    } else if (!subs.includes(selectedSubtopic)) {
      setSelectedSubtopic(subs[0]);
    }
  }, [selectedCategory, topicHierarchy]);

  useEffect(() => {
    if (mode !== "topics" || !selectedCategory || !selectedSubtopic) {
      setTopicQuestions([]);
      return;
    }
    const fullTopic = `${selectedCategory} / ${selectedSubtopic}`;
    setTopicsLoading(true);
    const url = `/api/topics/questions?topic=${encodeURIComponent(fullTopic)}&answer=${topicAnswerFilter}`;
    fetch(url)
      .then((r) => r.json())
      .then((data) => setTopicQuestions(data.questions || []))
      .catch((err) => setScreenError(err.message))
      .finally(() => setTopicsLoading(false));
  }, [mode, selectedCategory, selectedSubtopic, topicAnswerFilter]);

  useEffect(() => {
    if (mode !== "topics" || !includeTranslations || topicQuestions.length === 0) return;

    const idsNeeded = topicQuestions
      .map((q) => q.id)
      .filter((id) => {
        const cached = translations[id];
        return !cached || cached.status === "error";
      });
    if (idsNeeded.length === 0) return;

    setTranslations((previous) => {
      const next = { ...previous };
      for (const id of idsNeeded) {
        next[id] = { status: "loading", text: "" };
      }
      return next;
    });

    fetch("/api/translations/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question_ids: idsNeeded }),
    })
      .then(async (response) => {
        if (!response.ok) {
          const errorPayload = await response.json().catch(() => ({}));
          throw new Error(errorPayload.detail || "Traduzione non disponibile.");
        }
        return response.json();
      })
      .then((data) => {
        setTranslations((previous) => {
          const next = { ...previous };
          for (const [id, text] of Object.entries(data.translations || {})) {
            next[id] = { status: "ready", text };
          }
          for (const [id, err] of Object.entries(data.errors || {})) {
            next[id] = { status: "error", text: err };
          }
          return next;
        });
      })
      .catch((err) => {
        setTranslations((previous) => {
          const next = { ...previous };
          for (const id of idsNeeded) {
            if (next[id]?.status === "loading") {
              next[id] = { status: "error", text: err.message };
            }
          }
          return next;
        });
      });
  }, [mode, includeTranslations, topicQuestions]);

  if (!currentUser) {
    return <LoginScreen onLogin={handleLogin} />;
  }

  async function loadQuiz() {
    setLoading(true);
    setScreenError("");
    setSubmitError("");
    setResult(null);
    setTranslations({});
    setCheating(false);

    try {
      const response = await fetch("/api/quiz");
      if (!response.ok) {
        throw new Error("Impossibile caricare il quiz.");
      }

      const data = await response.json();
      const nextAnswers = {};
      data.questions.forEach((question) => {
        nextAnswers[question.id] = null;
      });

      setQuiz(data.questions);
      setAnswers(nextAnswers);
      setCurrentIndex(0);
    } catch (error) {
      setScreenError(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function loadQuizHistory() {
    try {
      const response = await fetchWithUser("/api/quiz/history", {}, currentUser);
      if (response.ok) {
        const data = await response.json();
        setQuizHistory(data.history || []);
      }
    } catch {
      // ignore
    }
  }

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

  async function refreshCacheStats() {
    try {
      const response = await fetch("/api/vocab/cache-stats");
      if (response.ok) {
        const data = await response.json();
        setVocabDefinitionsCachedPercent(data.definitions_cached_percent);
      }
    } catch {
      // ignore
    }
  }

  async function loadVocabQuestions(word) {
    try {
      const res = await fetch(`/api/vocab/${encodeURIComponent(word)}/questions`);
      if (!res.ok) return;
      const data = await res.json();
      setVocabQuestionResults(data);
    } catch {
      // ignore
    }
  }

  async function loadQuizVariants(questionId) {
    try {
      const res = await fetch(`/api/questions/${questionId}/variants`);
      if (!res.ok) return;
      const data = await res.json();
      setQuizVariantResults(data);
    } catch {
      // ignore
    }
  }

  async function loadVocab(source = vocabSource, forceReload = false) {
    setVocabLoading(true);
    setVocabError("");
    setVocabRevealing(false);
    setVocabSource(source);

    try {
      let bank = vocabBank;
      let hiddenWords = deriveKnownWords(bank, vocabFeedbackCounts, vocabHiddenWords);
      let difficultWords = vocabDifficultWords;
      let feedbackCounts = vocabFeedbackCounts;

      if (forceReload || bank.length === 0) {
        const response = await fetchWithUser("/api/vocab", {}, currentUser);
        if (!response.ok) {
          throw new Error("Impossibile caricare il vocabolario.");
        }

        const data = await response.json();
        bank = data.words;
        setVocabBank(bank);
        setVocabDefinitionsCachedPercent(data.definitions_cached_percent);

        const trackedKnownWords = mergeTrackedWords(vocabHiddenWords, bank, "known");
        difficultWords = mergeTrackedWords(vocabDifficultWords, bank, "difficult");
        feedbackCounts = mergeTrackedFeedbackCounts(vocabFeedbackCounts, bank);
        hiddenWords = deriveKnownWords(bank, feedbackCounts, trackedKnownWords);

        persistHiddenVocabWords(hiddenWords);
        persistDifficultVocabWords(difficultWords);
        persistVocabFeedbackCounts(feedbackCounts);

        setVocabHiddenWords(hiddenWords);
        setVocabDifficultWords(difficultWords);
        setVocabFeedbackCounts(feedbackCounts);
      }

      const { batch, queue, entry } = createVocabBatch(
        bank,
        hiddenWords,
        source,
        difficultWords,
        feedbackCounts
      );
      if (!entry) {
        setVocabBatch([]);
        setVocabQueue([]);
        setVocabBatchSolvedWords([]);
        setVocabBatchResults([]);
        setVocabBatchAttempts({});
        setVocabCurrent(null);
        setVocabCurrentTranslation(null);
        setVocabRevealTone(null);
        setVocabHistory([]);
        throw new Error(
          source === VOCAB_SOURCE_DIFFICULT
            ? "Nessuna parola difficile disponibile."
            : source === VOCAB_SOURCE_KNOWN
              ? "Nessuna parola conosciuta disponibile."
              : "Nessuna parola disponibile."
        );
      }

      setVocabBatch(batch);
      setVocabQueue(queue);
      setVocabBatchSolvedWords([]);
      setVocabBatchResults([]);
      setVocabBatchAttempts(createBatchAttemptCounts(batch));
      setVocabCurrent(entry);
      setVocabCurrentTranslation(null);
      setVocabRevealTone(null);
      setVocabHistory([]);
      setVocabBatchNumber((previous) => previous + 1);
      setVocabRoundNumber(1);
      prefetchBatchMeanings(queue);
    } catch (error) {
      setVocabError(error.message);
    } finally {
      setVocabLoading(false);
    }
  }

  async function openVocabMode() {
    setMode("vocab");

    if (!vocabCurrent && !vocabLoading) {
      await loadVocab(vocabSource);
    }
  }

  function persistHiddenVocabWords(words) {
    if (typeof window === "undefined") {
      return;
    }

    const key = currentUser ? getUserStorageKey(VOCAB_HIDDEN_WORDS_STORAGE_KEY, currentUser) : VOCAB_HIDDEN_WORDS_STORAGE_KEY;
    window.localStorage.setItem(key, JSON.stringify(words));
  }

  function persistVocabFeedbackCounts(counts) {
    if (typeof window === "undefined") {
      return;
    }

    const key = currentUser ? getUserStorageKey(VOCAB_FEEDBACK_COUNTS_STORAGE_KEY, currentUser) : VOCAB_FEEDBACK_COUNTS_STORAGE_KEY;
    window.localStorage.setItem(key, JSON.stringify(counts));
  }

  function persistDifficultVocabWords(words) {
    if (typeof window === "undefined") {
      return;
    }

    const key = currentUser ? getUserStorageKey(VOCAB_DIFFICULT_WORDS_STORAGE_KEY, currentUser) : VOCAB_DIFFICULT_WORDS_STORAGE_KEY;
    window.localStorage.setItem(key, JSON.stringify(words));
  }

  async function syncVocabTrackingToJson(feedbackCounts, hiddenWords, difficultWords) {
    const response = await fetchWithUser("/api/vocab/tracking", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        feedback_counts: feedbackCounts,
        hidden_words: hiddenWords,
        difficult_words: difficultWords
      })
    }, currentUser);

    if (!response.ok) {
      const errorPayload = await response.json().catch(() => ({}));
      throw new Error(errorPayload.detail || "Impossibile salvare il progresso del vocabolario.");
    }
  }

  async function syncVocabTrackingToJsonQuietly(feedbackCounts, hiddenWords, difficultWords) {
    try {
      await syncVocabTrackingToJson(feedbackCounts, hiddenWords, difficultWords);
    } catch (error) {
      setVocabError(error.message);
    }
  }

  function prefetchBatchMeanings(queue) {
    const words = queue.map((item) => item.word);
    if (words.length === 0) {
      return;
    }

    fetch("/api/vocab/prefetch", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ words })
    }).catch(() => {});
  }

  async function resetVocabTracking() {
    if (typeof window === "undefined") {
      return;
    }

    const confirmed = window.confirm(
      "Reset all vocab tracking? This will clear known words, success counts, fail counts, and difficult-word stars."
    );

    if (!confirmed) {
      return;
    }

    setVocabLoading(true);
    setVocabError("");
    setVocabRevealing(false);
    setVocabBatchOverlay(null);
    setVocabBatchSummary(null);

    persistHiddenVocabWords([]);
    persistVocabFeedbackCounts({});
    persistDifficultVocabWords([]);

    setVocabHiddenWords([]);
    setVocabFeedbackCounts({});
    setVocabDifficultWords([]);
    setVocabHistory([]);
    setVocabBatch([]);
    setVocabQueue([]);
    setVocabBatchSolvedWords([]);
    setVocabBatchResults([]);
    setVocabBatchAttempts({});
    setVocabBatchNumber(0);
    setVocabRoundNumber(0);
    setVocabCurrent(null);
    setVocabCurrentTranslation(null);
    setVocabRevealTone(null);

    try {
      await syncVocabTrackingToJson({}, [], []);
      await loadVocab(vocabSource, true);
    } catch (error) {
      setVocabError(error.message);
    } finally {
      setVocabLoading(false);
    }
  }

  function getWordFeedbackStats(word) {
    return getFeedbackStats(vocabFeedbackCounts, word);
  }

  function registerVocabFeedback(word, direction) {
    const next = createNextFeedbackCounts(vocabFeedbackCounts, word, direction);
    persistVocabFeedbackCounts(next);
    setVocabFeedbackCounts(next);
    return next;
  }

  function registerKnownModeDownFeedback(word) {
    const next = createKnownModeDownFeedbackCounts(vocabFeedbackCounts, word);
    persistVocabFeedbackCounts(next);
    setVocabFeedbackCounts(next);
    return next;
  }

  function clearKnownFeedback(word) {
    const next = createUnknownFeedbackCounts(vocabFeedbackCounts, word);
    persistVocabFeedbackCounts(next);
    setVocabFeedbackCounts(next);
    return next;
  }

  function isDifficultWord(word) {
    return vocabDifficultWords.includes(word);
  }

  function toggleDifficultWord(word) {
    setVocabDifficultWords((previous) => {
      const next = previous.includes(word)
        ? previous.filter((item) => item !== word)
        : [...previous, word];
      persistDifficultVocabWords(next);
      return next;
    });
  }

  async function showBatchOverlay(batchNumber) {
    setVocabBatchOverlay(`Batch ${batchNumber}`);
    await wait(VOCAB_BATCH_OVERLAY_MS);
    setVocabBatchOverlay(null);
  }

  async function showBatchSummary(batchNumber, roundCount, batch, attempts) {
    if (batch.length === 0) {
      return;
    }

    setVocabBatchSummary({
      batchNumber,
      roundCount,
      itemCount: batch.length,
      items: [...batch]
        .map((item) => ({
          word: item.word,
          tries: attempts[item.word] ?? 0
        }))
        .sort((left, right) => {
          if (right.tries !== left.tries) {
            return right.tries - left.tries;
          }

          return left.word.localeCompare(right.word);
        })
    });

    await new Promise((resolve) => {
      vocabBatchSummaryResolverRef.current = resolve;
    });
  }

  function closeBatchSummary() {
    setVocabBatchSummary(null);

    if (vocabBatchSummaryResolverRef.current) {
      const resolve = vocabBatchSummaryResolverRef.current;
      vocabBatchSummaryResolverRef.current = null;
      resolve();
    }
  }

  const currentQuestion = quiz[currentIndex];
  const unansweredCount = quiz.filter((question) => answers[question.id] === null).length;
  const currentSelection = currentQuestion ? answers[currentQuestion.id] : null;
  const currentOutcome = result?.details.find((detail) => detail.question_id === currentQuestion?.id) ?? null;
  const resultLookup = result
    ? Object.fromEntries(result.details.map((detail) => [detail.question_id, detail]))
    : {};
  const totalVocabWords = vocabBank.length;
  const knownVocabCount = knownVocabWords.length;
  const unknownVocabCount = Math.max(totalVocabWords - knownVocabCount, 0);
  const currentBatchSize = vocabBatch.length;
  const currentBatchSolvedCount = vocabBatchSolvedWords.length;
  const currentBatchQuestionNumber =
    currentBatchSize > 0 && vocabCurrent ? currentBatchSize - vocabQueue.length + 1 : 0;
  const vocabBatchProgressPercent =
    currentBatchSize > 0 ? (currentBatchSolvedCount / currentBatchSize) * 100 : 0;
  const vocabBatchProgressDisplay = Math.round(vocabBatchProgressPercent);

  function chooseAnswer(value, shouldAdvance = true) {
    if (!currentQuestion || result) {
      return;
    }

    setAnswers((previous) => ({
      ...previous,
      [currentQuestion.id]: value
    }));

    if (shouldAdvance) {
      setCurrentIndex((index) => Math.min(index + 1, quiz.length - 1));
    }
  }

  async function cheatAnswer() {
    if (!currentQuestion || result) {
      return;
    }

    setCheating(true);
    setSubmitError("");

    try {
      const response = await fetch(`/api/questions/${currentQuestion.id}/answer`);
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(errorPayload.detail || "Impossibile recuperare la risposta corretta.");
      }

      const data = await response.json();
      chooseAnswer(data.correct_answer, false);
    } catch (error) {
      setSubmitError(error.message);
    } finally {
      setCheating(false);
    }
  }

  async function revealVocabWord() {
    if (!vocabCurrent || (vocabRevealing && !vocabPendingTransition)) {
      return;
    }

    // If changing answer while transition is pending, update feedback and recalculate label
    if (vocabPendingTransition) {
      registerVocabFeedback(vocabCurrent.word, "down");
      setVocabRevealTone("down");
      setVocabBatchResults((prev) =>
        prev.map((entry) => entry.word === vocabCurrent.word ? { ...entry, result: "down" } : entry)
      );
      setVocabBatchSolvedWords((prev) => prev.filter((w) => w !== vocabCurrent.word));
      if (vocabPendingTransition.label === "Next batch") {
        setVocabPendingTransition((prev) => ({ ...prev, label: "Next round" }));
      }
      return;
    }

    setVocabRevealing(true);
    setVocabError("");

    try {
      const response = await fetch(`/api/vocab/translate?word=${encodeURIComponent(vocabCurrent.word)}`);
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(errorPayload.detail || "Impossibile tradurre la parola.");
      }

      const data = await response.json();
      setVocabCurrentTranslation(data);
      setVocabRevealTone("down");
      const completedWord = {
        word: vocabCurrent.word,
        translation: data,
        isKnown: false
      };
      const nextBatchAttempts = {
        ...vocabBatchAttempts,
        [vocabCurrent.word]: (vocabBatchAttempts[vocabCurrent.word] ?? 0) + 1
      };
      setVocabBatchAttempts(nextBatchAttempts);
      if (!vocabBatchAttempts[vocabCurrent.word]) {
        setVocabBatchResults((prev) => [...prev, { word: vocabCurrent.word, result: "down" }]);
      } else {
        setVocabBatchResults((prev) =>
          prev.map((entry) => entry.word === vocabCurrent.word ? { ...entry, result: "down" } : entry)
        );
      }
      const isKnownReview = vocabSource === VOCAB_SOURCE_KNOWN;
      const nextFeedbackCounts = isKnownReview
        ? registerKnownModeDownFeedback(vocabCurrent.word)
        : registerVocabFeedback(vocabCurrent.word, "down");
      const nextHiddenWords = deriveKnownWords(vocabBank, nextFeedbackCounts, knownVocabWords);
      persistHiddenVocabWords(nextHiddenWords);
      setVocabHiddenWords(nextHiddenWords);
      const nextBatchSolvedWords = isKnownReview
        ? Array.from(new Set([...vocabBatchSolvedWords, vocabCurrent.word]))
        : vocabBatchSolvedWords;
      const nextStep = getNextBatchStep(vocabBatch, vocabQueue, nextBatchSolvedWords);
      const isLastInRound = !nextStep.entry || nextStep.roundRestarted;

      if (isLastInRound) {
        const label = !nextStep.entry ? "Next batch" : "Next round";
        setVocabPendingTransition({
          label,
          apply: () => {
            if (!nextStep.entry && vocabBank.length > 0) {
              const nextBatch = createVocabBatch(
                vocabBank,
                nextHiddenWords,
                vocabSource,
                vocabDifficultWords,
                nextFeedbackCounts
              );
              setVocabBatch(nextBatch.batch);
              setVocabQueue(nextBatch.queue);
              setVocabBatchSolvedWords([]);
              setVocabBatchResults([]);
              setVocabBatchAttempts(createBatchAttemptCounts(nextBatch.batch));
              setVocabCurrent(nextBatch.entry);
              setVocabHistory([]);
              setVocabBatchNumber((previous) => previous + 1);
              setVocabRoundNumber(nextBatch.entry ? 1 : 0);
              prefetchBatchMeanings(nextBatch.queue);
              refreshCacheStats();
              if (!nextBatch.entry) {
                setVocabError("Nessuna parola disponibile.");
              }
            } else {
              setVocabHistory([]);
              setVocabBatch(nextStep.batch);
              setVocabQueue(nextStep.queue);
              setVocabBatchSolvedWords(isKnownReview ? [] : nextBatchSolvedWords);
              setVocabCurrent(nextStep.entry);
              setVocabRoundNumber((previous) => previous + 1);
            }
            setVocabCurrentTranslation(null);
            setVocabRevealTone(null);
            setVocabPendingTransition(null);
            setVocabRevealing(false);
          }
        });
        return;
      }

      await wait(1000);

      setVocabHistory((previous) => [completedWord, ...previous]);
      setVocabBatch(nextStep.batch);
      setVocabQueue(nextStep.queue);
      setVocabBatchSolvedWords(isKnownReview ? nextBatchSolvedWords : nextBatchSolvedWords);
      setVocabCurrent(nextStep.entry);
      setVocabCurrentTranslation(null);
      setVocabRevealTone(null);
    } catch (error) {
      setVocabError(error.message);
    } finally {
      setVocabRevealing(false);
    }
  }

  async function approveVocabWord() {
    if (!vocabCurrent || (vocabRevealing && !vocabPendingTransition)) {
      return;
    }

    // If changing answer while transition is pending, update feedback and recalculate label
    if (vocabPendingTransition) {
      registerVocabFeedback(vocabCurrent.word, "up");
      setVocabRevealTone("up");
      setVocabBatchResults((prev) =>
        prev.map((entry) => entry.word === vocabCurrent.word ? { ...entry, result: "up" } : entry)
      );
      const nextSolved = vocabBatchSolvedWords.includes(vocabCurrent.word)
        ? vocabBatchSolvedWords
        : [...vocabBatchSolvedWords, vocabCurrent.word];
      setVocabBatchSolvedWords(nextSolved);
      const allSolved = vocabBatch.every((item) => nextSolved.includes(item.word));
      if (allSolved !== (vocabPendingTransition.label === "Next batch")) {
        setVocabPendingTransition((prev) => ({ ...prev, label: allSolved ? "Next batch" : "Next round" }));
      }
      return;
    }

    setVocabRevealing(true);
    setVocabError("");

    try {
      const response = await fetch(`/api/vocab/translate?word=${encodeURIComponent(vocabCurrent.word)}`);
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(errorPayload.detail || "Impossibile tradurre la parola.");
      }

      const data = await response.json();
      setVocabCurrentTranslation(data);
      setVocabRevealTone("up");
      const nextBatchAttempts = {
        ...vocabBatchAttempts,
        [vocabCurrent.word]: (vocabBatchAttempts[vocabCurrent.word] ?? 0) + 1
      };
      setVocabBatchAttempts(nextBatchAttempts);
      if (!vocabBatchAttempts[vocabCurrent.word]) {
        setVocabBatchResults((prev) => [...prev, { word: vocabCurrent.word, result: "up" }]);
      } else {
        setVocabBatchResults((prev) =>
          prev.map((entry) => entry.word === vocabCurrent.word ? { ...entry, result: "up" } : entry)
        );
      }
      const nextFeedbackCounts = registerVocabFeedback(vocabCurrent.word, "up");
      const nextHiddenWords = deriveKnownWords(vocabBank, nextFeedbackCounts, knownVocabWords);
      const isNowKnown = isWordKnown(vocabCurrent.word, nextFeedbackCounts, nextHiddenWords);

      const completedWord = {
        word: vocabCurrent.word,
        translation: data,
        isKnown: isNowKnown
      };
      const isKnownReview = vocabSource === VOCAB_SOURCE_KNOWN;
      persistHiddenVocabWords(nextHiddenWords);
      setVocabHiddenWords(nextHiddenWords);
      const nextBatchSolvedWords = Array.from(new Set([...vocabBatchSolvedWords, vocabCurrent.word]));
      const remainingBatchEntries = vocabBatch.filter((item) => !nextBatchSolvedWords.includes(item.word));
      const isBatchComplete = remainingBatchEntries.length === 0;
      const nextStep = isBatchComplete
        ? createVocabBatch(
            vocabBank,
            nextHiddenWords,
            vocabSource,
            vocabDifficultWords,
            nextFeedbackCounts
          )
        : getNextBatchStep(vocabBatch, vocabQueue, nextBatchSolvedWords);

      const isLastInRound = isBatchComplete || nextStep.roundRestarted;

      if (isLastInRound) {
        const label = isBatchComplete ? "Next batch" : "Next round";
        setVocabPendingTransition({
          label,
          apply: async () => {
            if (isBatchComplete) {
              setVocabHistory([]);
              const nextBatchNumber = vocabBatchNumber + 1;
              const completedBatchNumber = vocabBatchNumber || 1;
              const completedBatch = vocabBatch;
              const completedBatchAttempts = nextBatchAttempts;
              setVocabCurrentTranslation(null);
              setVocabRevealTone(null);
              setVocabPendingTransition(null);
              setVocabCurrent(null);
              await showBatchSummary(completedBatchNumber, vocabRoundNumber || 1, completedBatch, completedBatchAttempts);
              await syncVocabTrackingToJsonQuietly(nextFeedbackCounts, nextHiddenWords, vocabDifficultWords);
              setVocabBatchNumber(nextBatchNumber);
              setVocabRoundNumber(nextStep.entry ? 1 : 0);
              if (nextStep.entry) {
                await showBatchOverlay(nextBatchNumber);
              }
              setVocabBatch(nextStep.batch);
              setVocabQueue(nextStep.queue);
              setVocabBatchSolvedWords([]);
              setVocabBatchResults([]);
              setVocabBatchAttempts(createBatchAttemptCounts(nextStep.batch));
              setVocabCurrent(nextStep.entry);
              prefetchBatchMeanings(nextStep.queue);
              refreshCacheStats();
              if (!nextStep.entry) {
                setVocabError("Non ci sono altre parole disponibili.");
              }
            } else {
              setVocabHistory([]);
              setVocabBatch(nextStep.batch);
              setVocabQueue(nextStep.queue);
              setVocabBatchSolvedWords(isKnownReview ? [] : nextBatchSolvedWords);
              setVocabCurrent(nextStep.entry);
              setVocabRoundNumber((previous) => previous + 1);
              setVocabCurrentTranslation(null);
              setVocabRevealTone(null);
              setVocabPendingTransition(null);
            }
            setVocabRevealing(false);
          }
        });
        return;
      }

      await wait(1000);

      setVocabHistory((previous) => [completedWord, ...previous]);
      setVocabBatch(nextStep.batch);
      setVocabQueue(nextStep.queue);
      setVocabBatchSolvedWords(isKnownReview ? nextBatchSolvedWords : nextBatchSolvedWords);
      setVocabCurrent(nextStep.entry);
      setVocabCurrentTranslation(null);
      setVocabRevealTone(null);
    } catch (error) {
      setVocabError(error.message);
    } finally {
      setVocabRevealing(false);
    }
  }

  function undoKnownVocabWord(word) {
    const nextFeedbackCounts = clearKnownFeedback(word);
    const nextHiddenWords = deriveKnownWords(vocabBank, nextFeedbackCounts, knownVocabWords);
    persistHiddenVocabWords(nextHiddenWords);
    setVocabHiddenWords(nextHiddenWords);
    setVocabHistory((previous) =>
      previous.map((item) => (item.word === word ? { ...item, isKnown: false } : item))
    );
    setVocabBatchResults((previous) =>
      previous.map((entry) => (entry.word === word ? { ...entry, result: "down" } : entry))
    );
    setVocabBatchSolvedWords((previous) => previous.filter((w) => w !== word));

    if (!vocabCurrent) {
      const nextBatch = createVocabBatch(
        vocabBank,
        nextHiddenWords,
        vocabSource,
        vocabDifficultWords,
        nextFeedbackCounts
      );
      setVocabBatch(nextBatch.batch);
      setVocabQueue(nextBatch.queue);
      setVocabBatchSolvedWords([]);
      setVocabBatchResults([]);
      setVocabBatchAttempts(createBatchAttemptCounts(nextBatch.batch));
      setVocabCurrent(nextBatch.entry);
      setVocabHistory([]);
      setVocabBatchNumber((previous) => previous + 1);
      setVocabRoundNumber(nextBatch.entry ? 1 : 0);
      prefetchBatchMeanings(nextBatch.queue);
    }

    setVocabError("");
  }

  async function requestTranslation(questionId, isOpen) {
    if (!isOpen) {
      return;
    }

    const cached = translations[questionId];
    if (cached && cached.status !== "error") {
      return;
    }

    setTranslations((previous) => ({
      ...previous,
      [questionId]: { status: "loading", text: "" }
    }));

    try {
      const response = await fetch(`/api/questions/${questionId}/translation`);
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(errorPayload.detail || "Traduzione non disponibile.");
      }

      const data = await response.json();
      setTranslations((previous) => ({
        ...previous,
        [questionId]: { status: "ready", text: data.translation }
      }));
    } catch (error) {
      setTranslations((previous) => ({
        ...previous,
        [questionId]: { status: "error", text: error.message }
      }));
    }
  }

  async function finishQuiz() {
    if (unansweredCount > 0) {
      setSubmitError(`Completa tutte le domande prima di terminare. Mancano ${unansweredCount} risposte.`);
      return;
    }

    setSubmitting(true);
    setSubmitError("");

    try {
      const payload = {
        answers: quiz.map((question) => ({
          question_id: question.id,
          selected: answers[question.id]
        }))
      };

      const response = await fetchWithUser("/api/score", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(payload)
      }, currentUser);

      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(errorPayload.detail || "Impossibile calcolare il risultato.");
      }

      const score = await response.json();
      setResult(score);
      loadQuizHistory();
    } catch (error) {
      setSubmitError(error.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <main className="app-shell">
        <section className="hero-card">
          <p className="eyebrow">Quiz Patente B</p>
          <h1>Preparazione in corso</h1>
          <p>Sto estraendo 30 domande casuali dal database.</p>
        </section>
      </main>
    );
  }

  if (screenError) {
    return (
      <main className="app-shell">
        <section className="hero-card">
          <p className="eyebrow">Errore</p>
          <h1>Il quiz non è disponibile</h1>
          <p>{screenError}</p>
          <button className="primary-button" onClick={loadQuiz}>
            Riprova
          </button>
        </section>
      </main>
    );
  }

  return (
    <main className="app-shell">
      {vocabBatchSummary && (
        <div className="vocab-batch-overlay" role="dialog" aria-modal="true" aria-labelledby="batch-summary-title">
          <div className="vocab-batch-summary-card">
            <p className="eyebrow">Batch Complete</p>
            <h2 id="batch-summary-title">Batch {vocabBatchSummary.batchNumber}</h2>
            <button className="primary-button" onClick={closeBatchSummary}>
              Continue
            </button>
            <div className="vocab-batch-summary-stats" aria-label="Batch statistics">
              <div className="vocab-batch-summary-stat">
                <span className="vocab-batch-summary-stat-label">Rounds</span>
                <strong>{vocabBatchSummary.roundCount}</strong>
              </div>
              <div className="vocab-batch-summary-stat">
                <span className="vocab-batch-summary-stat-label">Words</span>
                <strong>{vocabBatchSummary.itemCount}</strong>
              </div>
            </div>
            <div className="vocab-batch-summary-list">
              {vocabBatchSummary.items.map((item) => (
                <div key={item.word} className="vocab-batch-summary-row">
                  <span>{item.word}</span>
                  <span>{item.tries} {item.tries === 1 ? "round" : "rounds"}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
      {vocabQuestionResults && (
        <div className="vocab-batch-overlay" role="dialog" aria-modal="true" onClick={() => setVocabQuestionResults(null)}>
          <div className="vocab-questions-modal-card" onClick={(e) => e.stopPropagation()}>
            <button className="vocab-questions-close" onClick={() => setVocabQuestionResults(null)}>&times;</button>
            <h2>Questions with &ldquo;{vocabQuestionResults.word}&rdquo;</h2>
            <p className="vocab-questions-count">{vocabQuestionResults.count} question{vocabQuestionResults.count !== 1 ? "s" : ""} found</p>
            {vocabQuestionResults.count === 0 ? (
              <p className="vocab-questions-empty">No quiz questions contain this word.</p>
            ) : (
              <div className="vocab-questions-list">
                {vocabQuestionResults.questions.map((q) => (
                  <div key={q.id} className="vocab-question-item">
                    <div className="vocab-question-text">
                      {q.image_url && <img className="vocab-question-image" src={q.image_url} alt="" />}
                      <p>{highlightWordInText(q.text, vocabQuestionResults.word)}</p>
                    </div>
                    <div className="vocab-question-meta">
                      <span className={`vocab-question-answer ${q.answer ? "vero" : "falso"}`}>
                        {q.answer ? "Vero" : "Falso"}
                      </span>
                      <span className="vocab-question-topic">{q.topic}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
      {quizVariantResults && (
        <div className="vocab-batch-overlay" role="dialog" aria-modal="true" onClick={() => setQuizVariantResults(null)}>
          <div className="vocab-questions-modal-card" onClick={(e) => e.stopPropagation()}>
            <button className="vocab-questions-close" onClick={() => setQuizVariantResults(null)}>&times;</button>
            <h2>Question Variants</h2>
            <p className="vocab-questions-count">{quizVariantResults.count} variant{quizVariantResults.count !== 1 ? "s" : ""}</p>
            <div className="vocab-questions-list">
              {quizVariantResults.questions.map((q) => (
                <div key={q.id} className={`vocab-question-item${q.id === quizVariantResults.question_id ? " vocab-question-current" : ""}`}>
                  <div className="vocab-question-text">
                    {q.image_url && <img className="vocab-question-image" src={q.image_url} alt="" />}
                    <p>{q.text}</p>
                  </div>
                  <div className="vocab-question-meta">
                    <span className={`vocab-question-answer ${q.answer ? "vero" : "falso"}`}>
                      {q.answer ? "Vero" : "Falso"}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
      {vocabBatchOverlay && (
        <div className="vocab-batch-overlay" role="status" aria-live="polite">
          <div className="vocab-batch-overlay-card">
            <p className="eyebrow">Next Batch</p>
            <h2>{vocabBatchOverlay}</h2>
          </div>
        </div>
      )}
      {settingsOpen && (
        <div className="vocab-batch-overlay" role="dialog" aria-modal="true" onClick={() => setSettingsOpen(false)}>
          <div className="settings-modal-card" onClick={(e) => e.stopPropagation()}>
            <button className="vocab-questions-close" onClick={() => setSettingsOpen(false)}>&times;</button>
            <h2>Settings</h2>
            <div className="settings-row">
              <label className="settings-label" htmlFor="settings-language">Language</label>
              <select
                id="settings-language"
                className="settings-select"
                value={language}
                onChange={(e) => handleLanguageChange(e.target.value)}
              >
                {AVAILABLE_LANGUAGES.map((lang) => (
                  <option key={lang.code} value={lang.code}>{lang.label}</option>
                ))}
              </select>
            </div>
          </div>
        </div>
      )}
      <section className={`hero-card ${mode === "vocab" ? "hero-card-with-progress" : ""}`}>
        <div className="hero-brand">
          <img className="hero-icon" src="/app-icon.svg" alt="Quiz Patente B" />
          <div>
            <p className="eyebrow">Quiz Patente B</p>
            <h1>Patente B</h1>
          </div>
        </div>
        <div className="hero-meta">
          {mode === "quiz" ? (
            <>
              <span>Domanda {currentIndex + 1} di {quiz.length}</span>
              <span>{quiz.length - unansweredCount} risposte inserite</span>
            </>
          ) : (
            <>
              <span>Vocabolario patente</span>
              <span>Conosciute {knownVocabCount} / {totalVocabWords || 0}</span>
              <span>Sconosciute {unknownVocabCount} / {totalVocabWords || 0}</span>
              {vocabDefinitionsCachedPercent != null && (
                <span>{vocabDefinitionsCachedPercent}% definitions cached</span>
              )}
            </>
          )}
          <span className="user-indicator">{currentUser}</span>
          <div className="header-actions">
            <button
              className={`secondary-button header-button ${mode === "quiz" ? "active" : ""}`}
              onClick={() => setMode("quiz")}
            >
              Quiz
            </button>
            <button
              className={`secondary-button header-button ${mode === "vocab" ? "active" : ""}`}
              onClick={openVocabMode}
            >
              Vocab
            </button>
            <button
              className={`secondary-button header-button ${mode === "topics" ? "active" : ""}`}
              onClick={() => setMode("topics")}
            >
              Topics
            </button>
            <button
              className={`secondary-button header-button ${mode === "history" ? "active" : ""}`}
              onClick={() => setMode("history")}
            >
              History
            </button>
            <button
              className="secondary-button header-button"
              onClick={() => setSettingsOpen(true)}
            >
              Settings
            </button>
            <button
              className="secondary-button header-button"
              onClick={handleLogout}
            >
              Logout
            </button>
          </div>
        </div>
        {mode === "vocab" && (
          <div className="hero-progress-wrapper">
            <p className="hero-progress-status">
              Batch {vocabBatchNumber || 1}, Round {vocabRoundNumber || 1} :: Question {currentBatchQuestionNumber} of {currentBatchSize || VOCAB_BATCH_SIZE}
            </p>
            <div className="hero-progress-dots" aria-label="Batch results">
              {Array.from({ length: currentBatchSize || VOCAB_BATCH_SIZE }, (_, i) => {
                const entry = vocabBatchResults[i];
                const isCurrent = vocabCurrent && !vocabPendingTransition && (
                  entry?.word === vocabCurrent.word || (!entry && i === vocabBatchResults.length)
                );
                const cls = isCurrent ? "dot-active" : entry?.result === "up" ? "dot-correct" : entry?.result === "down" ? "dot-wrong" : "dot-empty";
                const difficult = entry?.word && vocabDifficultWords.includes(entry.word) ? " dot-difficult" : "";
                return <span key={i} className={`progress-dot ${cls}${difficult}`} />;
              })}
            </div>
          </div>
        )}
      </section>

      {mode === "quiz" && result && (
        <section className="result-card">
          <div>
            <p className="eyebrow">Risultato finale</p>
            <h2>{result.correct} corrette, {result.wrong} errate</h2>
            <p>Il punteggio è stato calcolato confrontando le tue 30 risposte con il dataset originale.</p>
          </div>
          <button className="secondary-button" onClick={loadQuiz}>
            Nuovo quiz
          </button>
        </section>
      )}

      {mode === "topics" ? (
        <section className="topics-panel">
          <div className="vocab-header">
            <p className="eyebrow">Topics</p>
          </div>
          <div className="topics-layout">
            <div className="topics-pickers">
              <label className="topics-listbox-label">
                Categoria
                <select
                  className="topics-listbox"
                  size={12}
                  value={selectedCategory}
                  onChange={(e) => setSelectedCategory(e.target.value)}
                >
                  {categories.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              </label>
              <label className="topics-listbox-label">
                Sotto-argomento
                <select
                  className="topics-listbox"
                  size={12}
                  value={selectedSubtopic}
                  onChange={(e) => setSelectedSubtopic(e.target.value)}
                  disabled={!selectedCategory}
                >
                  {subtopicsForCategory.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </label>
            </div>
            <div className="topics-content">
              <div className="topics-controls">
                <fieldset className="topics-answer-filter" disabled={!selectedSubtopic}>
                  <label>
                    <input
                      type="radio"
                      name="topic-answer"
                      checked={topicAnswerFilter === true}
                      onChange={() => setTopicAnswerFilter(true)}
                    /> True
                  </label>
                  <label>
                    <input
                      type="radio"
                      name="topic-answer"
                      checked={topicAnswerFilter === false}
                      onChange={() => setTopicAnswerFilter(false)}
                    /> False
                  </label>
                </fieldset>
                <label className="topics-translate-toggle">
                  <input
                    type="checkbox"
                    checked={includeTranslations}
                    onChange={(e) => setIncludeTranslations(e.target.checked)}
                  /> Include English translation
                </label>
              </div>
              {topicsLoading ? (
                <p>Caricamento...</p>
              ) : !selectedCategory ? (
                <p>Seleziona una categoria per vedere le domande.</p>
              ) : !selectedSubtopic ? (
                <p>Seleziona un sotto-argomento per vedere le domande.</p>
              ) : topicQuestions.length === 0 ? (
                <p>Nessuna domanda trovata.</p>
              ) : (
                <>
                  {(() => {
                    const distinctImages = [
                      ...new Set(topicQuestions.map((q) => q.image_url).filter(Boolean)),
                    ];
                    return distinctImages.length > 0 ? (
                      <div className="topics-image-bar">
                        {distinctImages.map((url) => (
                          <img key={url} src={url} alt="" className="topics-image" />
                        ))}
                      </div>
                    ) : null;
                  })()}
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
                </>
              )}
            </div>
          </div>
        </section>
      ) : mode === "history" ? (
        <section className="history-panel">
          <div className="vocab-header">
            <p className="eyebrow">Quiz History</p>
          </div>
          {quizHistory.length === 0 ? (
            <p>Nessun quiz completato.</p>
          ) : (
            <table className="history-table">
              <thead>
                <tr>
                  <th>Data</th>
                  <th>Corrette</th>
                  <th>Totale</th>
                  <th>%</th>
                </tr>
              </thead>
              <tbody>
                {[...quizHistory].reverse().map((entry) => (
                  <tr key={`${entry.date}-${entry.correct}-${entry.total}`}>
                    <td>{new Date(entry.date).toLocaleString()}</td>
                    <td>{entry.correct}</td>
                    <td>{entry.total}</td>
                    <td>{Math.round((entry.correct / entry.total) * 100)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      ) : mode === "quiz" ? (
        <section className="content-grid">
          <article className="question-panel">
            <p className="topic-tag">{currentQuestion.topic}</p>
            <h2 className="question-text">
              <button
                className="quiz-search-button"
                title="Show all question variants"
                onClick={() => loadQuizVariants(currentQuestion.id)}
              >
                🔍
              </button>
              {currentQuestion.text}
            </h2>

            <details
              key={currentQuestion.id}
              className="translation-panel"
              onToggle={(event) => requestTranslation(currentQuestion.id, event.currentTarget.open)}
            >
              <summary>Mostra la traduzione in inglese</summary>
              <div className="translation-body">
                {translations[currentQuestion.id]?.status === "loading" && <p>Traduzione in corso con Google Translate...</p>}
                {translations[currentQuestion.id]?.status === "ready" && <p>{translations[currentQuestion.id].text}</p>}
                {translations[currentQuestion.id]?.status === "error" && <p>{translations[currentQuestion.id].text}</p>}
                {!translations[currentQuestion.id] && <p>Apri questa sezione per richiedere la traduzione automatica.</p>}
              </div>
            </details>
          </article>

          <article className="response-panel">
            <div className="image-frame">
              <p className="panel-label">Immagine associata</p>
              {currentQuestion?.image_url ? (
                <div className="image-media">
                  <img
                    className="question-image"
                    src={currentQuestion.image_url}
                    alt="Immagine associata al quesito"
                  />
                </div>
              ) : (
                <div className="image-placeholder">
                  <strong>Nessuna immagine</strong>
                  <span>Questo quesito è solo testuale.</span>
                </div>
              )}
            </div>

            <div className="response-panel-body">
              <div className="answer-grid">
                <button
                  className={`answer-button ${currentSelection === true ? "selected true" : ""}`}
                  onClick={() => chooseAnswer(true)}
                  disabled={Boolean(result) || cheating}
                >
                  Vero
                </button>
                <button
                  className="answer-button cheat-button"
                  onClick={cheatAnswer}
                  disabled={Boolean(result) || currentQuestion == null || cheating}
                >
                  {cheating ? "..." : "Cheat"}
                </button>
                <button
                  className={`answer-button ${currentSelection === false ? "selected false" : ""}`}
                  onClick={() => chooseAnswer(false)}
                  disabled={Boolean(result) || cheating}
                >
                  Falso
                </button>
              </div>

              {currentOutcome && (
                <div className={`feedback-banner ${currentOutcome.is_correct ? "success" : "error"}`}>
                  <strong>{currentOutcome.is_correct ? "Risposta corretta" : "Risposta errata"}</strong>
                  <span>La soluzione esatta era: {currentOutcome.correct_answer ? "Vero" : "Falso"}.</span>
                </div>
              )}

              {submitError && <p className="inline-error">{submitError}</p>}

              <div className="navigation-row">
                <button
                  className="secondary-button"
                  onClick={() => setCurrentIndex((index) => Math.max(index - 1, 0))}
                  disabled={currentIndex === 0}
                  aria-label="Domanda precedente"
                >
                  ←
                </button>

                <div className="step-indicators" aria-label="Stato delle domande">
                  {quiz.map((question, index) => {
                    const isAnswered = answers[question.id] !== null;
                    const isCurrent = index === currentIndex;
                    const isWrong = resultLookup[question.id] && !resultLookup[question.id].is_correct;
                    return (
                      <button
                        key={question.id}
                        className={`step-dot ${isAnswered ? "answered" : ""} ${isWrong ? "wrong" : ""} ${isCurrent ? "current" : ""}`}
                        onClick={() => setCurrentIndex(index)}
                        aria-label={`Vai alla domanda ${index + 1}`}
                      >
                        {index + 1}
                      </button>
                    );
                  })}
                </div>

                <button
                  className="secondary-button"
                  onClick={() => setCurrentIndex((index) => Math.min(index + 1, quiz.length - 1))}
                  disabled={currentIndex === quiz.length - 1}
                  aria-label="Domanda successiva"
                >
                  →
                </button>
              </div>

              <button className="primary-button finish-button" onClick={finishQuiz} disabled={submitting || Boolean(result)}>
                {submitting ? "Calcolo del risultato..." : "Finished"}
              </button>
            </div>
          </article>
        </section>
      ) : (
        <section className="vocab-panel">
          <div className="vocab-header">
            <p className="eyebrow">Vocab</p>
            <div className="vocab-source-actions">
              <button
                className={`secondary-button ${vocabSource === VOCAB_SOURCE_RANDOM ? "header-button active" : ""}`}
                onClick={() => loadVocab(VOCAB_SOURCE_RANDOM)}
                disabled={vocabLoading || vocabRevealing}
              >
                Unknown
              </button>
              <button
                className={`secondary-button ${vocabSource === VOCAB_SOURCE_KNOWN ? "header-button active" : ""}`}
                onClick={() => loadVocab(VOCAB_SOURCE_KNOWN)}
                disabled={vocabLoading || vocabRevealing}
              >
                Known
              </button>
              <button
                className={`secondary-button ${vocabSource === VOCAB_SOURCE_DIFFICULT ? "header-button active" : ""}`}
                onClick={() => loadVocab(VOCAB_SOURCE_DIFFICULT)}
                disabled={vocabLoading || vocabRevealing}
              >
                Difficult Words
              </button>
              <button
                className={`secondary-button ${vocabSource === VOCAB_SOURCE_RANKED ? "header-button active" : ""}`}
                onClick={() => loadVocab(VOCAB_SOURCE_RANKED)}
                disabled={vocabLoading || vocabRevealing}
              >
                Ranked
              </button>
              <button
                className="secondary-button vocab-reset-button"
                onClick={resetVocabTracking}
                disabled={vocabLoading || vocabRevealing}
              >
                Reset
              </button>
            </div>
          </div>

          {vocabLoading && !vocabCurrent ? (
            <p>Sto preparando una nuova parola.</p>
          ) : vocabError && !vocabCurrent ? (
            <div>
              <p className="inline-error">{vocabError}</p>
              <button className="primary-button" onClick={() => loadVocab(vocabSource, true)}>
                Riprova
              </button>
            </div>
          ) : (
            <div className="vocab-stream">
              {vocabPendingTransition && (
                <button
                  className={`primary-button vocab-transition-button ${vocabPendingTransition.label === "Next batch" ? "vocab-transition-batch" : ""}`}
                  onClick={vocabPendingTransition.apply}
                >
                  {vocabPendingTransition.label}
                </button>
              )}
              {vocabCurrent && (
                <article className="vocab-row active">
                  <div className="vocab-body">
                    <div className="vocab-copy">
                      <p className="vocab-language">Italiano</p>
                      <h3 className="vocab-word">{vocabCurrent.word}</h3>
                      <VocabFeedbackStats
                        stats={getWordFeedbackStats(vocabCurrent.word)}
                        onSearch={() => loadVocabQuestions(vocabCurrent.word)}
                      />
                    </div>
                    <div className="vocab-copy">
                      <p className="vocab-language">English</p>
                      <VocabTranslation
                        translation={vocabCurrentTranslation}
                        hidden={!vocabCurrentTranslation}
                        tone={vocabCurrentTranslation ? vocabRevealTone : null}
                      />
                    </div>
                  </div>
                  <div className="vocab-action">
                    <button
                      className="secondary-button vocab-icon-button vocab-icon-approve"
                      onClick={approveVocabWord}
                      disabled={vocabRevealing && !vocabPendingTransition}
                      aria-label="Conosco questa parola"
                      title="Conosco questa parola"
                    >
                      👍
                    </button>
                    <button
                      className="primary-button vocab-icon-button vocab-icon-reveal"
                      onClick={revealVocabWord}
                      disabled={vocabRevealing && !vocabPendingTransition}
                      aria-label="Mostra l'inglese"
                      title="Mostra l'inglese"
                    >
                      {vocabRevealing && !vocabPendingTransition ? "…" : "👎"}
                    </button>
                    <button
                      className={`secondary-button vocab-icon-button vocab-icon-difficult ${isDifficultWord(vocabCurrent.word) ? "active" : ""}`}
                      onClick={() => toggleDifficultWord(vocabCurrent.word)}
                      disabled={vocabRevealing && !vocabPendingTransition}
                      aria-label="Segna come difficile"
                      title="Segna come difficile"
                    >
                      {isDifficultWord(vocabCurrent.word) ? "\ud83d\ude21" : "\ud83d\ude10"}
                    </button>
                    <VocabHelpChip />
                  </div>
                </article>
              )}

              {vocabHistory.map((item) => (
                <article key={`${item.word}-${getTranslationKey(item.translation)}`} className="vocab-row history">
                  <div className="vocab-body">
                    <div className="vocab-copy">
                      <p className="vocab-language">Italiano</p>
                      <h3 className="vocab-word">{item.word}</h3>
                      <VocabFeedbackStats
                        stats={getWordFeedbackStats(item.word)}
                        onSearch={() => loadVocabQuestions(item.word)}
                      />
                    </div>
                    <div className="vocab-copy">
                      <p className="vocab-language">English</p>
                      <VocabTranslation translation={item.translation} />
                    </div>
                  </div>
                  <div className="vocab-action">
                    <button
                      className={`secondary-button vocab-icon-button vocab-icon-difficult ${isDifficultWord(item.word) ? "active" : ""}`}
                      onClick={() => toggleDifficultWord(item.word)}
                      aria-label="Segna come difficile"
                      title="Segna come difficile"
                    >
                      {isDifficultWord(item.word) ? "\ud83d\ude21" : "\ud83d\ude10"}
                    </button>
                    {item.isKnown ? (
                      <button
                        className="primary-button vocab-icon-button vocab-icon-reveal"
                        onClick={() => undoKnownVocabWord(item.word)}
                        aria-label="Rimuovi dai termini conosciuti"
                        title="Rimuovi dai termini conosciuti"
                      >
                        👎
                      </button>
                    ) : (
                      <div className="vocab-action-placeholder" />
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}

          {vocabError && vocabCurrent && <p className="inline-error">{vocabError}</p>}
        </section>
      )}
    </main>
  );
}

export default App;
