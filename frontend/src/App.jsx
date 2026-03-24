import { useEffect, useState } from "react";

const VOCAB_HIDDEN_WORDS_STORAGE_KEY = "quiz-patente-b-hidden-vocab-words";

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

function getNextVocabEntry(bank, usedWords, hiddenWords) {
  const hiddenSet = new Set(hiddenWords);
  const visibleBank = bank.filter((item) => !hiddenSet.has(item.word));

  if (!visibleBank.length) {
    return { entry: null, nextUsedWords: [] };
  }

  const usedSet = new Set(usedWords);
  let pool = visibleBank.filter((item) => !usedSet.has(item.word));
  let nextUsedWords = usedWords;

  if (pool.length === 0) {
    pool = visibleBank;
    nextUsedWords = [];
  }

  const entry = pool[Math.floor(Math.random() * pool.length)];
  return {
    entry,
    nextUsedWords: [...nextUsedWords, entry.word]
  };
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
  const [vocabCurrent, setVocabCurrent] = useState(null);
  const [vocabCurrentTranslation, setVocabCurrentTranslation] = useState(null);
  const [vocabRevealTone, setVocabRevealTone] = useState(null);
  const [vocabHistory, setVocabHistory] = useState([]);
  const [vocabUsedWords, setVocabUsedWords] = useState([]);
  const [vocabHiddenWords, setVocabHiddenWords] = useState(loadHiddenVocabWords);
  const [vocabLoading, setVocabLoading] = useState(false);
  const [vocabRevealing, setVocabRevealing] = useState(false);
  const [vocabError, setVocabError] = useState("");

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

  async function loadVocab(forceReload = false) {
    setVocabLoading(true);
    setVocabError("");
    setVocabRevealing(false);

    try {
      let bank = vocabBank;
      if (forceReload || bank.length === 0) {
        const response = await fetch("/api/vocab");
        if (!response.ok) {
          throw new Error("Impossibile caricare il vocabolario.");
        }

        const data = await response.json();
        bank = data.words;
        setVocabBank(bank);
      }

      const { entry, nextUsedWords } = getNextVocabEntry(bank, [], vocabHiddenWords);
      if (!entry) {
        throw new Error("Nessuna parola disponibile.");
      }

      setVocabCurrent(entry);
      setVocabCurrentTranslation(null);
      setVocabRevealTone(null);
      setVocabHistory([]);
      setVocabUsedWords(nextUsedWords);
    } catch (error) {
      setVocabError(error.message);
    } finally {
      setVocabLoading(false);
    }
  }

  async function openVocabMode() {
    setMode("vocab");

    if (!vocabCurrent && !vocabLoading) {
      await loadVocab();
    }
  }

  function persistHiddenVocabWords(words) {
    if (typeof window === "undefined") {
      return;
    }

    window.localStorage.setItem(VOCAB_HIDDEN_WORDS_STORAGE_KEY, JSON.stringify(words));
  }

  const currentQuestion = quiz[currentIndex];
  const unansweredCount = quiz.filter((question) => answers[question.id] === null).length;
  const currentSelection = currentQuestion ? answers[currentQuestion.id] : null;
  const currentOutcome = result?.details.find((detail) => detail.question_id === currentQuestion?.id) ?? null;
  const resultLookup = result
    ? Object.fromEntries(result.details.map((detail) => [detail.question_id, detail]))
    : {};

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
    if (!vocabCurrent || vocabRevealing) {
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
      setVocabCurrentTranslation(data.translation);
      setVocabRevealTone("down");

      const completedWord = {
        word: vocabCurrent.word,
        translation: data.translation,
        isKnown: false
      };
      const { entry, nextUsedWords } = getNextVocabEntry(vocabBank, vocabUsedWords, vocabHiddenWords);

      await new Promise((resolve) => {
        window.setTimeout(resolve, 1000);
      });

      setVocabHistory((previous) => [completedWord, ...previous]);
      setVocabCurrent(entry);
      setVocabCurrentTranslation(null);
      setVocabRevealTone(null);
      setVocabUsedWords(nextUsedWords);
      if (!entry) {
        setVocabError("Non ci sono altre parole disponibili.");
      }
    } catch (error) {
      setVocabError(error.message);
    } finally {
      setVocabRevealing(false);
    }
  }

  async function approveVocabWord() {
    if (!vocabCurrent || vocabRevealing) {
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
      setVocabCurrentTranslation(data.translation);
      setVocabRevealTone("up");

      const completedWord = {
        word: vocabCurrent.word,
        translation: data.translation,
        isKnown: true
      };
      const nextHiddenWords = Array.from(new Set([...vocabHiddenWords, vocabCurrent.word]));
      persistHiddenVocabWords(nextHiddenWords);
      setVocabHiddenWords(nextHiddenWords);

      const nextUsedWordsBase = vocabUsedWords.filter((word) => word !== vocabCurrent.word);
      const { entry, nextUsedWords } = getNextVocabEntry(vocabBank, nextUsedWordsBase, nextHiddenWords);

      await new Promise((resolve) => {
        window.setTimeout(resolve, 1000);
      });

      setVocabHistory((previous) => [completedWord, ...previous]);
      setVocabCurrent(entry);
      setVocabCurrentTranslation(null);
      setVocabRevealTone(null);
      setVocabUsedWords(nextUsedWords);

      if (!entry) {
        setVocabError("Non ci sono altre parole disponibili.");
      }
    } catch (error) {
      setVocabError(error.message);
    } finally {
      setVocabRevealing(false);
    }
  }

  function undoKnownVocabWord(word) {
    const nextHiddenWords = vocabHiddenWords.filter((item) => item !== word);
    persistHiddenVocabWords(nextHiddenWords);
    setVocabHiddenWords(nextHiddenWords);
    setVocabHistory((previous) =>
      previous.map((item) => (item.word === word ? { ...item, isKnown: false } : item))
    );

    if (!vocabCurrent) {
      const { entry, nextUsedWords } = getNextVocabEntry(vocabBank, vocabUsedWords, nextHiddenWords);
      setVocabCurrent(entry);
      setVocabUsedWords(nextUsedWords);
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
      <section className="hero-card">
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
              <span>{vocabHistory.length} parole viste</span>
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
            <div>
              <p className="eyebrow">Vocab</p>
              <h2>Parole italiane casuali con traduzione inglese</h2>
            </div>
            <button className="secondary-button" onClick={() => loadVocab(true)} disabled={vocabLoading || vocabRevealing}>
              Nuova lista
            </button>
          </div>

          {vocabLoading && !vocabCurrent ? (
            <p>Sto preparando una nuova parola.</p>
          ) : vocabError && !vocabCurrent ? (
            <div>
              <p className="inline-error">{vocabError}</p>
              <button className="primary-button" onClick={() => loadVocab(true)}>
                Riprova
              </button>
            </div>
          ) : (
            <div className="vocab-stream">
              {vocabCurrent && (
                <article className="vocab-row active">
                  <div className="vocab-body">
                    <div className="vocab-copy">
                      <p className="vocab-language">Italiano</p>
                      <h3 className="vocab-word">{vocabCurrent.word}</h3>
                    </div>
                    <div className="vocab-copy">
                      <p className="vocab-language">English</p>
                      <p
                        className={`vocab-translation ${
                          vocabCurrentTranslation
                            ? vocabRevealTone === "up"
                              ? "vocab-translation-revealed-up"
                              : "vocab-translation-revealed-down"
                            : "vocab-translation-hidden"
                        }`}
                      >
                        {vocabCurrentTranslation || "?"}
                      </p>
                    </div>
                  </div>
                  <div className="vocab-action">
                    <button
                      className="secondary-button vocab-icon-button vocab-icon-approve"
                      onClick={approveVocabWord}
                      disabled={vocabRevealing}
                      aria-label="Conosco questa parola"
                      title="Conosco questa parola"
                    >
                      👍
                    </button>
                    <button
                      className="primary-button vocab-icon-button vocab-icon-reveal"
                      onClick={revealVocabWord}
                      disabled={vocabRevealing}
                      aria-label="Mostra l'inglese"
                      title="Mostra l'inglese"
                    >
                      {vocabRevealing ? "…" : "👎"}
                    </button>
                  </div>
                </article>
              )}

              {vocabHistory.map((item) => (
                <article key={`${item.word}-${item.translation ?? "known"}`} className="vocab-row history">
                  <div className="vocab-body">
                    <div className="vocab-copy">
                      <p className="vocab-language">Italiano</p>
                      <h3 className="vocab-word">{item.word}</h3>
                    </div>
                    <div className="vocab-copy">
                      <p className="vocab-language">English</p>
                      <p className="vocab-translation">{item.translation}</p>
                    </div>
                  </div>
                  <div className="vocab-action">
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
