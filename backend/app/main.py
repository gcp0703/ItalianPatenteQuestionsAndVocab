from __future__ import annotations

import asyncio
import html
import json
import logging
import random
import re
import time
from contextlib import asynccontextmanager
from html.parser import HTMLParser
from functools import lru_cache
from pathlib import Path
import threading
from threading import Lock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from deep_translator import GoogleTranslator
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_FILE = ROOT_DIR / "quizPatenteB2023.json"
VOCAB_FILE = ROOT_DIR / "vocabolario_patente.json"
NORMALIZED_VOCAB_FILE = ROOT_DIR / "vocabolario_patente.normalized.json"
IMAGE_DIR = ROOT_DIR / "img_sign"
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"
VOCAB_WRITE_LOCK = Lock()
DICTIONARY_URL = "https://www.dizionario-italiano.it/dizionario-italiano.php?parola={}100"
DICTIONARY_USER_AGENT = "Mozilla/5.0"
DICTIONARY_TIMEOUT_SECONDS = 10
MAX_DICTIONARY_MEANINGS = 4
MAX_DICTIONARY_RELATED = 5
logger = logging.getLogger("uvicorn.error")

DRIVING_TRANSLATION_OVERRIDES = {
    "galleria": "tunnel",
    "gallerie": "tunnels",
    "guida": "driving",
    "guidare": "to drive",
    "guidando": "driving",
    "guidato": "driven",
    "guidata": "driven",
    "guidi": "you drive",
    "guido": "I drive",
    "corsia": "lane",
    "corsie": "lanes",
    "carreggiata": "carriageway",
    "carreggiate": "carriageways",
    "autocarro": "truck",
    "autocarri": "trucks",
    "sorpasso": "overtaking",
    "sorpassare": "to overtake",
    "marciapiede": "sidewalk",
    "marciapiedi": "sidewalks",
    "svincolo": "interchange",
    "svincoli": "interchanges",
    "semaforo": "traffic light",
    "semafori": "traffic lights",
}

DRIVING_PHRASE_OVERRIDES = {
    "galleries": "tunnels",
    "gallery": "tunnel",
}

class AIModelGate:
    """Ensures only one AI model request runs at a time, with user priority.

    When a user request arrives, the background worker yields immediately
    so the user never waits behind a background definition fetch.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._user_waiting = 0
        self._count_lock = threading.Lock()

    def user_acquire(self) -> None:
        with self._count_lock:
            self._user_waiting += 1
        self._lock.acquire()

    def user_release(self) -> None:
        self._lock.release()
        with self._count_lock:
            self._user_waiting -= 1

    def background_acquire(self) -> bool:
        with self._count_lock:
            if self._user_waiting > 0:
                return False
        return self._lock.acquire(timeout=0)

    def background_release(self) -> None:
        self._lock.release()

    def has_user_waiting(self) -> bool:
        with self._count_lock:
            return self._user_waiting > 0


AI_MODEL_GATE = AIModelGate()
_background_stop = threading.Event()

TRANSLATION_CONTEXT_HINT = "macchina guidare autovettura"
TRANSLATION_TEXT_MARKER = "[[[TEXT]]]"
TRANSLATION_CONTEXT_PREFIX = f"[[[CTX]]] {TRANSLATION_CONTEXT_HINT} [[[ENDCTX]]] {TRANSLATION_TEXT_MARKER} "


class QuestionOut(BaseModel):
    id: int
    text: str
    image_url: str | None
    topic: str


class QuizResponse(BaseModel):
    questions: list[QuestionOut]


class SubmittedAnswer(BaseModel):
    question_id: int
    selected: bool | None


class ScoreSubmission(BaseModel):
    answers: list[SubmittedAnswer]


class ScoreDetail(BaseModel):
    question_id: int
    selected: bool
    correct_answer: bool
    is_correct: bool


class ScoreResponse(BaseModel):
    total: int
    correct: int
    wrong: int
    details: list[ScoreDetail]


class TranslationResponse(BaseModel):
    question_id: int
    translation: str


class AnswerRevealResponse(BaseModel):
    question_id: int
    correct_answer: bool


class VocabWordOut(BaseModel):
    word: str
    known_translation: str | None
    tracking: VocabTrackingOut


class VocabResponse(BaseModel):
    words: list[VocabWordOut]
    definitions_cached_percent: int


class VocabDictionaryRelatedOut(BaseModel):
    term: str
    meaning: str | None
    english: str


class VocabDictionaryOut(BaseModel):
    lookup_word: str
    lemma: str | None
    meanings: list[str]
    related: list[VocabDictionaryRelatedOut]


class VocabTranslationResponse(BaseModel):
    word: str
    translation: str
    dictionary: VocabDictionaryOut | None = None


class VocabTrackingOut(BaseModel):
    up: int
    down: int
    known: bool
    difficult: bool


class VocabFeedbackCountsIn(BaseModel):
    up: int = 0
    down: int = 0


class VocabTrackingSyncIn(BaseModel):
    feedback_counts: dict[str, VocabFeedbackCountsIn]
    hidden_words: list[str]
    difficult_words: list[str]


class VocabTrackingSyncResponse(BaseModel):
    updated_words: int


class VocabPrefetchRequest(BaseModel):
    words: list[str]


class VocabPrefetchResponse(BaseModel):
    queued_words: int


def _humanize_topic(parts: list[str]) -> str:
    return " / ".join(part.replace("-", " ") for part in parts)


def _preserve_case(source: str, replacement: str) -> str:
    if source.isupper():
        return replacement.upper()
    if source[:1].isupper():
        return replacement.capitalize()
    return replacement


def _apply_phrase_overrides(text: str) -> str:
    updated = text
    for source, replacement in DRIVING_PHRASE_OVERRIDES.items():
        updated = re.sub(
            rf"\b{re.escape(source)}\b",
            lambda match: _preserve_case(match.group(0), replacement),
            updated,
            flags=re.IGNORECASE,
        )
    return updated


def _extract_translated_target_text(text: str) -> str:
    if TRANSLATION_TEXT_MARKER in text:
        return text.split(TRANSLATION_TEXT_MARKER, 1)[1].strip()
    return text.strip()


def _coerce_non_negative_int(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _read_vocab_tracking(metadata: dict[str, Any]) -> dict[str, int | bool]:
    tracking = metadata.get("tracking")
    if not isinstance(tracking, dict):
        tracking = {}

    return {
        "up": _coerce_non_negative_int(tracking.get("up")),
        "down": _coerce_non_negative_int(tracking.get("down")),
        "known": bool(tracking.get("known", False)),
        "difficult": bool(tracking.get("difficult", False)),
    }


def _read_dictionary_cache(metadata: dict[str, Any]) -> dict[str, Any] | None:
    if "dictionary_cache" not in metadata:
        return None

    cache = metadata.get("dictionary_cache")
    if not isinstance(cache, dict):
        return None

    related_items = []
    for item in cache.get("related", []):
        if not isinstance(item, dict):
            continue
        term = str(item.get("term") or "").strip()
        if not term:
            continue
        meaning = str(item.get("meaning")).strip() if item.get("meaning") is not None else None
        english = str(item.get("english") or "").strip()
        if not english:
            continue
        related_items.append(
            {
                "term": term,
                "meaning": meaning or None,
                "english": english,
            }
        )

    meanings = [
        str(meaning).strip()
        for meaning in cache.get("meanings", [])
        if str(meaning).strip()
    ]
    lookup_word = str(cache.get("lookup_word") or "").strip()

    return {
        "lookup_word": lookup_word or None,
        "lemma": str(cache.get("lemma")).strip() if cache.get("lemma") is not None else None,
        "meanings": meanings,
        "related": related_items,
    }


class DictionaryHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.lemmas: list[str] = []
        self.meanings: list[str] = []
        self.meaning_pos: list[str] = []
        self.related: list[dict[str, str | None]] = []
        self._capture_stack: list[dict[str, Any]] = []
        self._skip_stack: list[int] = []
        self._pending_related_term: str | None = None
        self._current_pos: str = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = set((dict(attrs).get("class") or "").split())

        if self._capture_stack and tag == "br":
            self._capture_stack[-1]["buffer"].append(" ")

        if self._capture_stack and classes.intersection({"esempi", "autore"}):
            self._skip_stack.append(len(self._capture_stack))
            return

        if "grammatica" in classes:
            self._capture_stack.append({"name": "grammatica", "buffer": [], "depth": 1})
            return

        if classes.intersection({"lemma", "italiano", "cit_ita_1", "cit_ita_2"}):
            if "lemma" in classes:
                name = "lemma"
            elif "italiano" in classes:
                name = "meaning"
            elif "cit_ita_1" in classes:
                name = "related_term"
            else:
                name = "related_meaning"

            self._capture_stack.append({"name": name, "buffer": [], "depth": 1})
            return

        if self._capture_stack:
            self._capture_stack[-1]["depth"] += 1

    def handle_endtag(self, tag: str) -> None:
        if self._skip_stack and self._skip_stack[-1] == len(self._capture_stack):
            self._skip_stack.pop()
            return

        if not self._capture_stack:
            return

        self._capture_stack[-1]["depth"] -= 1
        if self._capture_stack[-1]["depth"] > 0:
            return

        capture = self._capture_stack.pop()
        text = _normalize_dictionary_text("".join(capture["buffer"]))
        if not text:
            return

        if capture["name"] == "grammatica":
            self._current_pos = text.lower()
        elif capture["name"] == "lemma":
            self.lemmas.append(text)
        elif capture["name"] == "meaning":
            self.meanings.append(text)
            self.meaning_pos.append(self._current_pos)
        elif capture["name"] == "related_term":
            self._pending_related_term = text
        elif capture["name"] == "related_meaning":
            self.related.append({"term": self._pending_related_term or text, "meaning": text})
            self._pending_related_term = None

    def handle_data(self, data: str) -> None:
        if self._capture_stack and not self._skip_stack:
            self._capture_stack[-1]["buffer"].append(data)


def _normalize_dictionary_text(text: str) -> str:
    cleaned = html.unescape(text)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.replace("||", " ")
    return cleaned.strip(" ;|")


@lru_cache(maxsize=1)
def load_normalized_vocab_data() -> tuple[dict[str, str], dict[str, list[str]]]:
    if not NORMALIZED_VOCAB_FILE.exists():
        return {}, {}

    with NORMALIZED_VOCAB_FILE.open("r", encoding="utf-8") as normalized_file:
        payload = json.load(normalized_file)

    by_word = payload.get("by_word")
    entries = payload.get("entries")
    if not isinstance(by_word, dict):
        by_word = {}
    if not isinstance(entries, dict):
        entries = {}

    normalized_lookup = {str(word): str(normalized) for word, normalized in by_word.items()}
    normalized_groups = {
        str(normalized_word): [
            str(source_word)
            for source_word in entry.get("source_words", [])
            if isinstance(source_word, str)
        ]
        for normalized_word, entry in entries.items()
        if isinstance(entry, dict)
    }
    return normalized_lookup, normalized_groups


def get_normalized_vocab_word(word: str) -> str:
    normalized_lookup, _ = load_normalized_vocab_data()
    return normalized_lookup.get(word, word)


def get_normalized_vocab_group(word: str) -> list[str]:
    _, normalized_groups = load_normalized_vocab_data()
    return normalized_groups.get(word, [word])


def load_vocab_storage_payload() -> tuple[Path, dict[str, Any], dict[str, dict[str, Any]]]:
    if NORMALIZED_VOCAB_FILE.exists():
        with NORMALIZED_VOCAB_FILE.open("r", encoding="utf-8") as vocab_file:
            payload = json.load(vocab_file)
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            entries = {}
        return NORMALIZED_VOCAB_FILE, payload, entries

    with VOCAB_FILE.open("r", encoding="utf-8") as vocab_file:
        payload = json.load(vocab_file)
    if not isinstance(payload, dict):
        payload = {}
    return VOCAB_FILE, payload, payload


def write_vocab_storage_payload(
    path: Path, payload: dict[str, Any], entries: dict[str, dict[str, Any]]
) -> None:
    if path == NORMALIZED_VOCAB_FILE:
        payload["entries"] = entries
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            payload["meta"] = meta
        meta["normalized_word_count"] = len(entries)
    else:
        payload = entries

    with path.open("w", encoding="utf-8") as vocab_file:
        json.dump(payload, vocab_file, ensure_ascii=False, indent=2)
        vocab_file.write("\n")


def _translate_auxiliary_text(text: str) -> str:
    translated = GoogleTranslator(source="it", target="en").translate(text)
    if not translated:
        raise RuntimeError("Google Translate did not return a translation.")
    return _apply_phrase_overrides(translated.strip())


@lru_cache(maxsize=8192)
def translate_dictionary_text(text: str) -> str:
    return _translate_auxiliary_text(text)


@lru_cache(maxsize=2048)
def fetch_dictionary_page(word: str) -> str | None:
    url = DICTIONARY_URL.format(quote(word))
    request = Request(url, headers={"User-Agent": DICTIONARY_USER_AGENT})

    try:
        with urlopen(request, timeout=DICTIONARY_TIMEOUT_SECONDS) as response:
            page = response.read().decode("utf-8", errors="ignore")
    except (HTTPError, URLError, TimeoutError, OSError):
        return None

    if 'class="italiano"' not in page or "non ha prodotto alcun risultato" in page:
        return None

    return page


def _meaning_sort_priority(pos: str) -> int:
    """Verb definitions first (0), then noun (1), then adjective/other (2)."""
    if "verbo" in pos:
        return 0
    if "sostantivo" in pos:
        return 1
    return 2


def parse_dictionary_page(page: str) -> dict[str, Any]:
    parser = DictionaryHtmlParser()
    parser.feed(page)

    meanings = []
    seen_meanings: set[str] = set()
    for meaning, pos in zip(parser.meanings, parser.meaning_pos):
        cleaned = _normalize_dictionary_text(meaning)
        if cleaned and cleaned not in seen_meanings:
            seen_meanings.add(cleaned)
            meanings.append({"text": cleaned, "pos": pos})

    # When multiple parts of speech exist, prioritize verb > noun > adjective
    pos_types = {_meaning_sort_priority(m["pos"]) for m in meanings}
    if len(pos_types) > 1:
        meanings.sort(key=lambda m: _meaning_sort_priority(m["pos"]))
    meanings = [m["text"] for m in meanings]

    related_items = []
    seen_related: set[tuple[str, str | None]] = set()
    for item in parser.related:
        term = _normalize_dictionary_text(item["term"] or "")
        meaning = _normalize_dictionary_text(item["meaning"] or "") or None
        key = (term, meaning)
        if term and key not in seen_related:
            seen_related.add(key)
            related_items.append({"term": term, "meaning": meaning})

    return {
        "lemma": parser.lemmas[0] if parser.lemmas else None,
        "meanings": meanings,
        "related": related_items[:MAX_DICTIONARY_RELATED],
    }


def _rerank_meanings_by_hint(meanings: list[str], hint: str) -> list[str]:
    """Rerank translated meanings so those relevant to *hint* come first."""
    if not hint or not meanings:
        return meanings

    hint_words = {w.lower() for w in re.split(r"[\s/,;]+", hint) if len(w) > 1}
    if not hint_words:
        return meanings

    def _relevance(meaning: str) -> int:
        words = set(re.split(r"[\s/,;:()]+", meaning.lower()))
        # Count direct word overlap with the Google translation
        return -sum(1 for w in hint_words if w in words or any(w in mw for mw in words))

    return sorted(meanings, key=_relevance)


@lru_cache(maxsize=2048)
def get_dictionary_details(word: str, google_hint: str = "") -> dict[str, Any] | None:
    cached_entry = VOCAB_BY_WORD.get(word)
    if cached_entry and cached_entry.get("dictionary_cache") is not None:
        cache = cached_entry["dictionary_cache"]
        if google_hint:
            cache = {**cache, "meanings": _rerank_meanings_by_hint(
                cache["meanings"], google_hint
            )[:MAX_DICTIONARY_MEANINGS]}
        return cache

    lookup_word = get_normalized_vocab_word(word)
    if lookup_word != word:
        lookup_entry = VOCAB_BY_WORD.get(lookup_word)
        if lookup_entry and lookup_entry.get("dictionary_cache") is not None:
            cache = lookup_entry["dictionary_cache"]
            if google_hint:
                cache = {**cache, "meanings": _rerank_meanings_by_hint(
                    cache["meanings"], google_hint
                )[:MAX_DICTIONARY_MEANINGS]}
            return cache

        for related_word in get_normalized_vocab_group(lookup_word):
            related_entry = VOCAB_BY_WORD.get(related_word)
            if related_entry and related_entry.get("dictionary_cache") is not None:
                cache = related_entry["dictionary_cache"]
                if google_hint:
                    cache = {**cache, "meanings": _rerank_meanings_by_hint(
                        cache["meanings"], google_hint
                    )[:MAX_DICTIONARY_MEANINGS]}
                return cache

    page = fetch_dictionary_page(lookup_word)
    if not page and lookup_word != word:
        lookup_word = word
        page = fetch_dictionary_page(word)
    if not page:
        dictionary_cache = {
            "lookup_word": lookup_word,
            "lemma": None,
            "meanings": [],
            "related": [],
        }
        persist_dictionary_cache(word, dictionary_cache)
        return dictionary_cache

    parsed = parse_dictionary_page(page)
    # Translate all meanings, then rerank by relevance to Google and truncate.
    # Most words have < 10 meanings so this is cheap; words with many (like
    # "corrente" with 36) need the full pool to surface the right senses.
    translated_meanings = [
        translate_dictionary_text(meaning) for meaning in parsed["meanings"]
    ]
    translated_meanings = _rerank_meanings_by_hint(translated_meanings, google_hint)
    translated_meanings = translated_meanings[:MAX_DICTIONARY_MEANINGS]

    related = [
        {
            "term": item["term"],
            "meaning": item["meaning"],
            "english": translate_dictionary_text(item["meaning"] or item["term"]),
        }
        for item in parsed["related"]
    ]

    dictionary_cache = {
        "lookup_word": lookup_word,
        "lemma": parsed["lemma"],
        "meanings": translated_meanings,
        "related": related,
    }
    persist_dictionary_cache(word, dictionary_cache)
    return dictionary_cache


def _flatten_questions(node: Any, path: list[str], items: list[dict[str, Any]]) -> None:
    if isinstance(node, dict):
        if {"q", "a"}.issubset(node):
            items.append(
                {
                    "id": len(items) + 1,
                    "text": node["q"],
                    "answer": bool(node["a"]),
                    "image_url": node.get("img"),
                    "topic": _humanize_topic(path),
                }
            )
            return

        for key, value in node.items():
            _flatten_questions(value, [*path, key], items)
        return

    if isinstance(node, list):
        for value in node:
            _flatten_questions(value, path, items)


@lru_cache(maxsize=1)
def load_question_bank() -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    with DATA_FILE.open("r", encoding="utf-8") as data_file:
        raw_data = json.load(data_file)

    items: list[dict[str, Any]] = []
    _flatten_questions(raw_data, [], items)
    by_id = {item["id"]: item for item in items}
    return items, by_id


@lru_cache(maxsize=1)
def load_vocab_bank() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    _, _, raw_data = load_vocab_storage_payload()

    items = [
        {
            "word": word,
            "known_translation": (metadata.get("english") or "").strip() or None,
            "ai_definition": (metadata.get("ai_definition") or "").strip() or None,
            "ai_definition_failed": bool(metadata.get("ai_definition_failed")),
            "tracking": _read_vocab_tracking(metadata),
            "dictionary_cache": _read_dictionary_cache(metadata),
        }
        for word, metadata in raw_data.items()
    ]
    by_word = {item["word"]: item for item in items}
    return items, by_word


@lru_cache(maxsize=8192)
def translate_text(text: str) -> str:
    exact_override = DRIVING_TRANSLATION_OVERRIDES.get(text.strip().lower())
    if exact_override:
        return exact_override

    contextual_input = f"{TRANSLATION_CONTEXT_PREFIX}{text}"
    translated = GoogleTranslator(source="it", target="en").translate(contextual_input)
    if not translated:
        raise RuntimeError("Google Translate did not return a translation.")
    extracted = _extract_translated_target_text(translated)
    return _apply_phrase_overrides(extracted)


_ai_model_cache: dict[str, Any] = {}


def _load_ai_model() -> tuple[Any, Any]:
    """Load the local MLX model and tokenizer, cached after first call."""
    if "model" in _ai_model_cache:
        return _ai_model_cache["model"], _ai_model_cache["tokenizer"]

    from dotenv import dotenv_values
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    model_name = env.get("AI_MODEL", "mlx-community/Qwen3.5-27B-4bit")

    from mlx_lm import load
    model, tokenizer = load(model_name)
    _ai_model_cache["model"] = model
    _ai_model_cache["tokenizer"] = tokenizer
    logger.info("Loaded local AI model: %s", model_name)
    return model, tokenizer


AI_DEFINITION_PROMPT = (
    "You are an Italian-English dictionary for someone studying for the Italian "
    "driver's license exam (Patente B). "
    "Given an Italian word, provide its English definition in the context of "
    "automobiles, driving, and Italian traffic laws. "
    "If the word has multiple meanings, lead with the one most relevant to driving "
    "and traffic, then briefly list other common meanings. "
    "Be concise: one short sentence per meaning, max 4 meanings. "
    "Do NOT include the Italian word in your response. "
    "Respond ONLY with the numbered definitions, nothing else."
)


@lru_cache(maxsize=8192)
def get_ai_definition(word: str) -> str | None:
    """Get a contextual English definition using the local MLX model."""
    try:
        model, tokenizer = _load_ai_model()
    except Exception as exc:
        logger.warning("Failed to load AI model: %s", exc)
        return None

    messages = [
        {"role": "system", "content": AI_DEFINITION_PROMPT},
        {"role": "user", "content": word},
    ]

    from mlx_lm import generate
    prompt = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True,
        enable_thinking=False,
    )
    response = generate(
        model, tokenizer, prompt=prompt, max_tokens=200, verbose=False,
    )
    cleaned = response.strip()
    return cleaned if cleaned else None


QUESTION_BANK, QUESTION_BY_ID = load_question_bank()
VOCAB_BANK, VOCAB_BY_WORD = load_vocab_bank()


def refresh_vocab_bank() -> None:
    global VOCAB_BANK, VOCAB_BY_WORD

    load_vocab_bank.cache_clear()
    VOCAB_BANK, VOCAB_BY_WORD = load_vocab_bank()


def persist_dictionary_cache(word: str, dictionary_cache: dict[str, Any]) -> int:
    normalized_word = get_normalized_vocab_word(word)
    target_words = {normalized_word}

    payload = {
        "lookup_word": dictionary_cache.get("lookup_word"),
        "lemma": dictionary_cache.get("lemma"),
        "meanings": dictionary_cache.get("meanings", []),
        "related": dictionary_cache.get("related", []),
    }

    with VOCAB_WRITE_LOCK:
        path, raw_payload, raw_data = load_vocab_storage_payload()

        updated_words = 0
        for target_word in target_words:
            metadata = raw_data.get(target_word)
            if not isinstance(metadata, dict):
                continue
            metadata["dictionary_cache"] = payload
            updated_words += 1

        if updated_words > 0:
            write_vocab_storage_payload(path, raw_payload, raw_data)

    if updated_words > 0:
        refresh_vocab_bank()

    return updated_words


def persist_ai_definitions(definitions: dict[str, str]) -> int:
    """Persist a batch of AI definitions to the vocab JSON file."""
    with VOCAB_WRITE_LOCK:
        path, raw_payload, raw_data = load_vocab_storage_payload()

        updated_words = 0
        for word, definition in definitions.items():
            normalized_word = get_normalized_vocab_word(word)
            metadata = raw_data.get(normalized_word)
            if not isinstance(metadata, dict):
                metadata = raw_data.get(word)
            if not isinstance(metadata, dict):
                continue
            metadata["ai_definition"] = definition
            updated_words += 1

        if updated_words > 0:
            write_vocab_storage_payload(path, raw_payload, raw_data)

    if updated_words > 0:
        refresh_vocab_bank()

    return updated_words


def persist_ai_definition_failures(words: list[str]) -> int:
    """Mark words where the AI model returned no definition."""
    with VOCAB_WRITE_LOCK:
        path, raw_payload, raw_data = load_vocab_storage_payload()

        updated_words = 0
        for word in words:
            normalized_word = get_normalized_vocab_word(word)
            metadata = raw_data.get(normalized_word)
            if not isinstance(metadata, dict):
                metadata = raw_data.get(word)
            if not isinstance(metadata, dict):
                continue
            metadata["ai_definition_failed"] = True
            updated_words += 1

        if updated_words > 0:
            write_vocab_storage_payload(path, raw_payload, raw_data)

    if updated_words > 0:
        refresh_vocab_bank()

    return updated_words


def persist_vocab_tracking(update: VocabTrackingSyncIn) -> int:
    hidden_words = set(update.hidden_words)
    difficult_words = set(update.difficult_words)

    with VOCAB_WRITE_LOCK:
        path, raw_payload, raw_data = load_vocab_storage_payload()

        updated_words = 0
        for word, metadata in raw_data.items():
            if not isinstance(metadata, dict):
                metadata = {}
                raw_data[word] = metadata

            counts = update.feedback_counts.get(word)
            metadata["tracking"] = {
                "up": _coerce_non_negative_int(counts.up if counts else 0),
                "down": _coerce_non_negative_int(counts.down if counts else 0),
                "known": word in hidden_words,
                "difficult": word in difficult_words,
            }
            updated_words += 1

        write_vocab_storage_payload(path, raw_payload, raw_data)

    refresh_vocab_bank()
    return updated_words


def unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_items: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique_items.append(item)
    return unique_items


def prefetch_vocab_meanings(words: list[str]) -> None:
    ordered_words = unique_preserve_order(words)
    total_words = len(ordered_words)

    for index, word in enumerate(ordered_words, start=1):
        entry = VOCAB_BY_WORD.get(word)
        if not entry:
            continue

        logger.info("Prefetch %s/%s: %s", index, total_words, word)

        if not entry["known_translation"]:
            try:
                translate_text(word)
            except Exception:
                pass

        try:
            get_dictionary_details(word)
            logger.info("Cached meaning for %s", word)
        except Exception:
            logger.exception("Prefetch failed for %s", word)

_BG_PERSIST_BATCH_SIZE = 10


def _background_definition_worker() -> None:
    """Pre-cache AI definitions for all vocab words in JSON order."""
    logger.info("Background definition worker started (%d words)", len(VOCAB_BANK))

    cached = 0
    failed = 0
    skipped = 0
    pending: dict[str, str] = {}
    pending_failures: list[str] = []

    for item in VOCAB_BANK:
        if _background_stop.is_set():
            break

        word = item["word"]

        # Skip words where AI was already attempted (succeeded or failed)
        if item["ai_definition"] or item["ai_definition_failed"]:
            skipped += 1
            continue

        # Wait for the gate, yielding to any user requests
        while not _background_stop.is_set():
            if AI_MODEL_GATE.has_user_waiting():
                time.sleep(0.5)
                continue

            if AI_MODEL_GATE.background_acquire():
                try:
                    result = get_ai_definition(word)
                    if result:
                        cached += 1
                        pending[word] = result
                        logger.info(
                            "Background AI definition [%d cached]: %s",
                            cached, word,
                        )
                    else:
                        failed += 1
                        pending_failures.append(word)
                        logger.info(
                            "Background AI definition empty [%d failed]: %s",
                            failed, word,
                        )
                except Exception:
                    failed += 1
                    pending_failures.append(word)
                    logger.exception("Background AI definition failed: %s", word)
                finally:
                    AI_MODEL_GATE.background_release()
                break
            else:
                time.sleep(0.1)

        # Persist in batches to avoid excessive disk writes
        if len(pending) >= _BG_PERSIST_BATCH_SIZE:
            try:
                persist_ai_definitions(pending)
                logger.info("Persisted %d AI definitions to disk", len(pending))
            except Exception:
                logger.exception("Failed to persist AI definitions batch")
            pending.clear()

        if len(pending_failures) >= _BG_PERSIST_BATCH_SIZE:
            try:
                persist_ai_definition_failures(pending_failures)
            except Exception:
                logger.exception("Failed to persist AI definition failures")
            pending_failures.clear()

        # Brief pause between words to keep the system responsive
        if not _background_stop.is_set():
            time.sleep(0.1)

    # Persist any remaining definitions and failures
    if pending:
        try:
            persist_ai_definitions(pending)
            logger.info("Persisted %d AI definitions to disk (final)", len(pending))
        except Exception:
            logger.exception("Failed to persist final AI definitions batch")

    if pending_failures:
        try:
            persist_ai_definition_failures(pending_failures)
        except Exception:
            logger.exception("Failed to persist final AI definition failures")

    logger.info(
        "Background definition worker finished (cached=%d, failed=%d, skipped=%d)",
        cached, failed, skipped,
    )


def _is_backfill_enabled() -> bool:
    from dotenv import dotenv_values
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    return env.get("BACKFILL_DEFINITIONS", "true").lower() not in ("false", "0", "no")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _is_backfill_enabled():
        thread = threading.Thread(
            target=_background_definition_worker, daemon=True, name="bg-ai-definitions",
        )
        thread.start()
    else:
        logger.info("Background definition backfill disabled (BACKFILL_DEFINITIONS=false)")
    yield
    _background_stop.set()


app = FastAPI(title="Quiz Patente B", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5183", "http://127.0.0.1:5183"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_question_or_404(question_id: int) -> dict[str, Any]:
    question = QUESTION_BY_ID.get(question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found.")
    return question


@app.get("/api/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/quiz", response_model=QuizResponse)
async def get_quiz(count: int = Query(default=30, ge=1, le=100)) -> QuizResponse:
    if count > len(QUESTION_BANK):
        raise HTTPException(status_code=400, detail="Requested quiz is larger than the question bank.")

    selection = random.sample(QUESTION_BANK, count)
    questions = [
        QuestionOut(
            id=item["id"],
            text=item["text"],
            image_url=item["image_url"],
            topic=item["topic"],
        )
        for item in selection
    ]
    return QuizResponse(questions=questions)


@app.get("/api/questions/{question_id}/translation", response_model=TranslationResponse)
async def get_translation(question_id: int) -> TranslationResponse:
    question = get_question_or_404(question_id)
    try:
        translation = await asyncio.to_thread(translate_text, question["text"])
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Google Translate translation failed. Check your network connection and try again.",
        ) from exc

    return TranslationResponse(question_id=question_id, translation=translation)


@app.get("/api/questions/{question_id}/answer", response_model=AnswerRevealResponse)
async def reveal_answer(question_id: int) -> AnswerRevealResponse:
    question = get_question_or_404(question_id)
    return AnswerRevealResponse(question_id=question_id, correct_answer=question["answer"])


@app.get("/api/vocab", response_model=VocabResponse)
async def get_vocab() -> VocabResponse:
    words = [
        VocabWordOut(
            word=item["word"],
            known_translation=item["known_translation"],
            tracking=VocabTrackingOut(**item["tracking"]),
        )
        for item in VOCAB_BANK
    ]
    percent = _definitions_cached_percent()
    return VocabResponse(words=words, definitions_cached_percent=percent)


def _has_usable_definition(item: dict[str, Any]) -> bool:
    if item["known_translation"] or item["ai_definition"]:
        return True
    dc = item["dictionary_cache"]
    return bool(dc and (dc.get("meanings") or dc.get("related")))


def _definitions_cached_percent() -> int:
    total = len(VOCAB_BANK)
    if total == 0:
        return 0
    cached = sum(1 for item in VOCAB_BANK if _has_usable_definition(item))
    return round(cached * 100 / total)


@app.get("/api/vocab/cache-stats")
async def get_vocab_cache_stats() -> dict[str, int]:
    return {"definitions_cached_percent": _definitions_cached_percent()}


@app.get("/api/vocab/translate", response_model=VocabTranslationResponse)
async def translate_vocab_word(word: str = Query(min_length=1)) -> VocabTranslationResponse:
    entry = VOCAB_BY_WORD.get(word)
    if not entry:
        raise HTTPException(status_code=404, detail="Word not found.")

    translation = entry["known_translation"]

    # Check for a persisted AI definition from the background worker.
    ai_definition: str | None = entry.get("ai_definition")

    # If no persisted definition, use the local AI model live.
    # Acquire the gate so the background worker yields to us.
    if not translation and not ai_definition:
        def _user_ai_call() -> str | None:
            AI_MODEL_GATE.user_acquire()
            try:
                return get_ai_definition(word)
            finally:
                AI_MODEL_GATE.user_release()

        try:
            ai_definition = await asyncio.to_thread(_user_ai_call)
            if ai_definition:
                await asyncio.to_thread(persist_ai_definitions, {word: ai_definition})
        except Exception:
            pass

    # Fall back to Google Translate if AI unavailable.
    if not translation and not ai_definition:
        try:
            translation = await asyncio.to_thread(translate_text, word)
        except Exception:
            pass

    # AI definition becomes the primary translation.
    if ai_definition and not translation:
        translation = ai_definition

    # Use the translation as a hint to rerank dictionary meanings
    # so they align with the most common sense of the word.
    google_hint = translation or ""
    dictionary: VocabDictionaryOut | None = None
    try:
        dictionary_payload = await asyncio.to_thread(
            get_dictionary_details, word, google_hint
        )
        if dictionary_payload:
            dictionary = VocabDictionaryOut(
                lookup_word=dictionary_payload["lookup_word"],
                lemma=dictionary_payload["lemma"],
                meanings=dictionary_payload["meanings"],
                related=[
                    VocabDictionaryRelatedOut(
                        term=item["term"],
                        meaning=item["meaning"],
                        english=item["english"],
                    )
                    for item in dictionary_payload["related"]
                ],
            )
    except Exception:
        dictionary = None

    # Fall back to dictionary meanings if everything else failed.
    if not translation and dictionary and dictionary.meanings:
        translation = " / ".join(dictionary.meanings)

    if not translation:
        raise HTTPException(
            status_code=502,
            detail="Translation failed. Check your network connection and try again.",
        )

    return VocabTranslationResponse(word=word, translation=translation, dictionary=dictionary)


@app.post("/api/vocab/tracking", response_model=VocabTrackingSyncResponse)
async def sync_vocab_tracking(update: VocabTrackingSyncIn) -> VocabTrackingSyncResponse:
    try:
        updated_words = await asyncio.to_thread(persist_vocab_tracking, update)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Unable to persist vocab tracking.") from exc

    return VocabTrackingSyncResponse(updated_words=updated_words)


@app.post("/api/vocab/prefetch", response_model=VocabPrefetchResponse)
async def prefetch_vocab_batch(
    request: VocabPrefetchRequest, background_tasks: BackgroundTasks
) -> VocabPrefetchResponse:
    words = [word for word in unique_preserve_order(request.words) if word in VOCAB_BY_WORD]
    if words:
        background_tasks.add_task(prefetch_vocab_meanings, words)

    return VocabPrefetchResponse(queued_words=len(words))


@app.post("/api/score", response_model=ScoreResponse)
async def score_quiz(submission: ScoreSubmission) -> ScoreResponse:
    if not submission.answers:
        raise HTTPException(status_code=400, detail="No answers submitted.")

    details: list[ScoreDetail] = []
    for answer in submission.answers:
        if answer.selected is None:
            raise HTTPException(status_code=400, detail="All questions must be answered before scoring.")

        question = get_question_or_404(answer.question_id)
        is_correct = answer.selected == question["answer"]
        details.append(
            ScoreDetail(
                question_id=answer.question_id,
                selected=answer.selected,
                correct_answer=question["answer"],
                is_correct=is_correct,
            )
        )

    correct = sum(1 for detail in details if detail.is_correct)
    total = len(details)
    return ScoreResponse(total=total, correct=correct, wrong=total - correct, details=details)


if IMAGE_DIR.exists():
    app.mount("/img_sign", StaticFiles(directory=IMAGE_DIR), name="question-images")


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:

    @app.get("/", include_in_schema=False)
    async def frontend_missing() -> dict[str, str]:
        return {
            "message": "Frontend build not found. Run `npm install` and `npm run build` inside `frontend/`."
        }
