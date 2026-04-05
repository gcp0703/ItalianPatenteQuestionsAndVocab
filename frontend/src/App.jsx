import { useEffect, useRef, useState } from "react";

const VOCAB_HIDDEN_WORDS_STORAGE_KEY = "quiz-patente-b-hidden-vocab-words";
const VOCAB_FEEDBACK_COUNTS_STORAGE_KEY = "quiz-patente-b-vocab-feedback-counts";
const VOCAB_DIFFICULT_WORDS_STORAGE_KEY = "quiz-patente-b-vocab-difficult-words";
const VOCAB_BATCH_SIZE = 20;
const VOCAB_BATCH_OVERLAY_MS = 2000;
const VOCAB_SOURCE_RANDOM = "random";
const VOCAB_SOURCE_KNOWN = "known";
const VOCAB_SOURCE_DIFFICULT = "difficult";
const VOCAB_SOURCE_RANKED = "ranked";

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

function loadHiddenVocabWords() {
  if (typeof window === "undefined") {
    return [];
  }

  try {
    const stored = window.localStorage.getItem(VOCAB_HIDDEN_WORDS_STORAGE_KEY);
    const parsed = stored ? JSON.parse(stored) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function loadVocabFeedbackCounts() {
  if (typeof window === "undefined") {
    return {};
  }

  try {
    const stored = window.localStorage.getItem(VOCAB_FEEDBACK_COUNTS_STORAGE_KEY);
    const parsed = stored ? JSON.parse(stored) : {};
    return normalizeFeedbackCounts(parsed);
  } catch {
    return {};
  }
}

function loadDifficultVocabWords() {
  if (typeof window === "undefined") {
    return [];
  }

  try {
    const stored = window.localStorage.getItem(VOCAB_DIFFICULT_WORDS_STORAGE_KEY);
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

function VocabFeedbackStats({ stats }) {
  return (
    <p className="vocab-feedback-stats">
      <span>👍 {stats.up}</span>
      <span>👎 {stats.down}</span>
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

function App() {
  const [mode, setMode] = useState("quiz");
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
  const [vocabHiddenWords, setVocabHiddenWords] = useState(loadHiddenVocabWords);
  const [vocabDifficultWords, setVocabDifficultWords] = useState(loadDifficultVocabWords);
  const [vocabFeedbackCounts, setVocabFeedbackCounts] = useState(loadVocabFeedbackCounts);
  const [vocabLoading, setVocabLoading] = useState(false);
  const [vocabRevealing, setVocabRevealing] = useState(false);
  const [vocabPendingTransition, setVocabPendingTransition] = useState(null);
  const [vocabError, setVocabError] = useState("");
  const vocabBatchSummaryResolverRef = useRef(null);
  const knownVocabWords = deriveKnownWords(vocabBank, vocabFeedbackCounts, vocabHiddenWords);

  useEffect(() => {
    loadQuiz();
  }, []);

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
        const response = await fetch("/api/vocab");
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

    window.localStorage.setItem(VOCAB_HIDDEN_WORDS_STORAGE_KEY, JSON.stringify(words));
  }

  function persistVocabFeedbackCounts(counts) {
    if (typeof window === "undefined") {
      return;
    }

    window.localStorage.setItem(VOCAB_FEEDBACK_COUNTS_STORAGE_KEY, JSON.stringify(counts));
  }

  function persistDifficultVocabWords(words) {
    if (typeof window === "undefined") {
      return;
    }

    window.localStorage.setItem(VOCAB_DIFFICULT_WORDS_STORAGE_KEY, JSON.stringify(words));
  }

  async function syncVocabTrackingToJson(feedbackCounts, hiddenWords, difficultWords) {
    const response = await fetch("/api/vocab/tracking", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        feedback_counts: feedbackCounts,
        hidden_words: hiddenWords,
        difficult_words: difficultWords
      })
    });

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

      const response = await fetch("/api/score", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(payload)
      });

      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(errorPayload.detail || "Impossibile calcolare il risultato.");
      }

      const score = await response.json();
      setResult(score);
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
      {vocabBatchOverlay && (
        <div className="vocab-batch-overlay" role="status" aria-live="polite">
          <div className="vocab-batch-overlay-card">
            <p className="eyebrow">Next Batch</p>
            <h2>{vocabBatchOverlay}</h2>
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

      {mode === "quiz" ? (
        <section className="content-grid">
          <article className="question-panel">
            <p className="topic-tag">{currentQuestion.topic}</p>
            <h2 className="question-text">{currentQuestion.text}</h2>

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
                      <VocabFeedbackStats stats={getWordFeedbackStats(vocabCurrent.word)} />
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
                  </div>
                </article>
              )}

              {vocabHistory.map((item) => (
                <article key={`${item.word}-${getTranslationKey(item.translation)}`} className="vocab-row history">
                  <div className="vocab-body">
                    <div className="vocab-copy">
                      <p className="vocab-language">Italiano</p>
                      <h3 className="vocab-word">{item.word}</h3>
                      <VocabFeedbackStats stats={getWordFeedbackStats(item.word)} />
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
