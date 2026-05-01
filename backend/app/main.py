from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import random
import re
import tempfile
import time
import unicodedata
from contextlib import asynccontextmanager
from html.parser import HTMLParser
from functools import lru_cache
from pathlib import Path
import threading
from threading import Lock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request as UrllibRequest, urlopen

from datetime import datetime, timezone

from deep_translator import GoogleTranslator
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Dev-only: load repo-root .env so local runs pick up developer overrides.
# Production sets QPB_LOAD_DOTENV=0 in the systemd unit so this is a no-op.
if os.environ.get("QPB_LOAD_DOTENV", "1") == "1":
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    except ImportError:
        pass

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_FILE = ROOT_DIR / "quizPatenteB2023.json"
VOCAB_FILE = ROOT_DIR / "vocabolario_patente.json"
NORMALIZED_VOCAB_FILE = ROOT_DIR / "vocabolario_patente.normalized.json"
IMAGE_DIR = ROOT_DIR / "img_sign"
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"
BACKEND_DATA_DIR = ROOT_DIR / "backend" / "data"
TRANSLATION_CACHE_FILE = BACKEND_DATA_DIR / "translations.en.json"
TRANSLATION_CACHE_VERSION = 1
TRANSLATION_CACHE_SOURCE_LANG = "it"
TRANSLATION_CACHE_TARGET_LANG = "en"
USER_DATA_DIR = Path(os.environ.get("QPB_USER_DATA_DIR") or (ROOT_DIR / "user_data"))
USER_REGISTRY_FILE = USER_DATA_DIR / "_users.json"
VOCAB_WRITE_LOCK = Lock()
USER_DATA_LOCK = Lock()
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
_bg_worker_status: dict[str, Any] = {
    "running": False,
    "mode": None,
    "checked": 0,
    "updated": 0,
    "last_word": None,
    "last_check_time": None,
}

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


class TopicsResponse(BaseModel):
    topics: list[str]


class TopicQuestionsResponse(BaseModel):
    topic: str
    answer: bool
    questions: list[QuestionOut]
    count: int


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


class BatchTranslationRequest(BaseModel):
    question_ids: list[int]


class BatchTranslationResponse(BaseModel):
    translations: dict[int, str]
    errors: dict[int, str]


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
    words: list[str] = Field(default_factory=list, max_length=50)


class VocabPrefetchResponse(BaseModel):
    queued_words: int


class UserOut(BaseModel):
    email: str
    created: str


class UserCreatedOut(BaseModel):
    """Returned only at registration. Contains the plaintext token shown once."""
    email: str
    created: str
    token: str


class UserCreateIn(BaseModel):
    email: str


class QuizHistoryEntry(BaseModel):
    date: str
    total: int
    correct: int


class QuizHistoryResponse(BaseModel):
    history: list[QuizHistoryEntry]


class HardQuestionsResponse(BaseModel):
    hard_question_ids: list[int]


class HardQuestionToggleIn(BaseModel):
    hard: bool


class QuestionMatchOut(BaseModel):
    id: int
    text: str
    answer: bool
    image_url: str | None
    topic: str


class VocabQuestionsResponse(BaseModel):
    word: str
    questions: list[QuestionMatchOut]
    count: int


class QuestionVariantsResponse(BaseModel):
    question_id: int
    questions: list[QuestionMatchOut]
    count: int


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


# ---------------------------------------------------------------------------
# User data helpers
# ---------------------------------------------------------------------------

def sanitize_email(email: str) -> str:
    """Convert an email address to a safe filesystem name."""
    name = email.lower().strip()
    name = name.replace("@", "_at_").replace(".", "_dot_")
    # Replace remaining non-alphanumeric (except _ and -) with _
    name = re.sub(r"[^a-z0-9_\-]", "_", name)
    return name


def get_user_file_path(email: str) -> Path:
    path = USER_DATA_DIR / f"{sanitize_email(email)}.json"
    if not path.resolve().is_relative_to(USER_DATA_DIR.resolve()):
        raise ValueError("Invalid email produces unsafe path.")
    return path


def _ensure_user_data_dir() -> None:
    USER_DATA_DIR.mkdir(exist_ok=True)
    if not USER_REGISTRY_FILE.exists():
        USER_REGISTRY_FILE.write_text('{"users": []}\n', encoding="utf-8")


def _read_user_registry_unlocked() -> list[dict[str, str]]:
    """Read user registry. Caller must hold USER_DATA_LOCK."""
    _ensure_user_data_dir()
    with USER_REGISTRY_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("users", [])


def _write_user_registry_unlocked(users: list[dict[str, str]]) -> None:
    """Write user registry. Caller must hold USER_DATA_LOCK."""
    _ensure_user_data_dir()
    with USER_REGISTRY_FILE.open("w", encoding="utf-8") as f:
        json.dump({"users": users}, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_user_registry() -> list[dict[str, str]]:
    with USER_DATA_LOCK:
        return _read_user_registry_unlocked()


def _empty_user_data(email: str) -> dict[str, Any]:
    return {
        "email": email,
        "tracking": {
            "feedback_counts": {},
            "hidden_words": [],
            "difficult_words": [],
            "hard_questions": [],
        },
        "quiz_history": [],
    }


def _read_user_data_unlocked(email: str) -> dict[str, Any]:
    """Read user data. Caller must hold USER_DATA_LOCK."""
    path = get_user_file_path(email)
    if not path.exists():
        return _empty_user_data(email)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_user_data_unlocked(email: str, data: dict[str, Any]) -> None:
    """Write user data. Caller must hold USER_DATA_LOCK."""
    _ensure_user_data_dir()
    path = get_user_file_path(email)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_user_data(email: str) -> dict[str, Any]:
    with USER_DATA_LOCK:
        return _read_user_data_unlocked(email)


def save_user_data(email: str, data: dict[str, Any]) -> None:
    with USER_DATA_LOCK:
        _write_user_data_unlocked(email, data)


def get_current_user_email(authorization: str | None = Header(None)) -> str:
    """Authenticate the caller via Authorization: Bearer <token>.

    Replaces the legacy X-User-Email header which trusted clients to self-assert.
    """
    from backend.app.auth import require_user
    return require_user(authorization)


def _admin_email() -> str:
    return os.environ.get("ADMIN_EMAIL", "").strip().lower()


def require_admin(caller_email: str = Depends(get_current_user_email)) -> str:
    admin = _admin_email()
    if not admin:
        raise HTTPException(status_code=503, detail="Admin endpoints disabled (ADMIN_EMAIL unset).")
    if caller_email != admin:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return caller_email


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
    request = UrllibRequest(url, headers={"User-Agent": DICTIONARY_USER_AGENT})

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


def _translation_cache_key(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text).strip()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


class TranslationCache:
    """Persistent, language-tagged translation cache.

    Stores the *pre-override* raw Google output keyed by sha1 of the
    NFC-normalized Italian source text. Phrase overrides are applied at
    read time on every call so override edits never invalidate entries.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()
        self._entries: dict[str, dict[str, str]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            logger.info("Translation cache file not found at %s; starting empty", self._path)
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load translation cache from %s: %s", self._path, exc)
            return
        entries = data.get("entries") if isinstance(data, dict) else None
        if isinstance(entries, dict):
            self._entries = {
                str(k): v
                for k, v in entries.items()
                if isinstance(v, dict) and isinstance(v.get("dst"), str)
            }
        logger.info(
            "Loaded %d translation cache entries from %s", len(self._entries), self._path
        )

    def get(self, text: str) -> str | None:
        key = _translation_cache_key(text)
        with self._lock:
            entry = self._entries.get(key)
            return entry["dst"] if entry else None

    def put(self, text: str, dst: str) -> None:
        key = _translation_cache_key(text)
        with self._lock:
            self._entries[key] = {"src": text, "dst": dst}
            self._write_locked()

    def _write_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": TRANSLATION_CACHE_VERSION,
            "source_lang": TRANSLATION_CACHE_SOURCE_LANG,
            "target_lang": TRANSLATION_CACHE_TARGET_LANG,
            "entries": self._entries,
        }
        fd, tmp_path = tempfile.mkstemp(
            prefix=".translations.", suffix=".tmp", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


_translation_cache = TranslationCache(TRANSLATION_CACHE_FILE)


def translate_text(text: str) -> str:
    exact_override = DRIVING_TRANSLATION_OVERRIDES.get(text.strip().lower())
    if exact_override:
        return exact_override

    cached = _translation_cache.get(text)
    if cached is not None:
        return _apply_phrase_overrides(cached)

    contextual_input = f"{TRANSLATION_CONTEXT_PREFIX}{text}"
    translated = GoogleTranslator(source="it", target="en").translate(contextual_input)
    if not translated:
        raise RuntimeError("Google Translate did not return a translation.")
    extracted = _extract_translated_target_text(translated)
    _translation_cache.put(text, extracted)
    return _apply_phrase_overrides(extracted)


_ai_model_cache: dict[str, Any] = {}


def _load_ai_model() -> tuple[Any, Any]:
    """Load the local MLX model and tokenizer, cached after first call."""
    if "model" in _ai_model_cache:
        return _ai_model_cache["model"], _ai_model_cache["tokenizer"]

    import os
    model_name = os.environ.get("AI_MODEL", "mlx-community/Qwen3.5-27B-4bit")

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
def _extract_final_answer(response: str) -> str:
    """Strip <think>...</think> reasoning blocks from a model response."""
    # Remove complete <think>...</think> blocks
    cleaned = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL)
    # Remove an unclosed leading <think>... if generation was truncated mid-thought
    cleaned = re.sub(r"^.*?</think>", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


def _get_claude_definition(word: str) -> str | None:
    """Get a definition using the Claude API as fallback when MLX is unavailable."""
    from backend.app import spend

    enabled = os.environ.get("CLAUDE_FALLBACK_ENABLED", "true").lower() not in ("false", "0", "no")
    if not enabled:
        return None

    if spend.is_over_cap():
        logger.warning(
            "Skipping Claude call for '%s': monthly cap reached (total=$%.2f).",
            word, spend.month_total_usd(),
        )
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=AI_DEFINITION_PROMPT,
            messages=[{"role": "user", "content": word}],
        )
        spend.record_claude_call(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        text = response.content[0].text.strip()
        return text if text else None
    except Exception as exc:
        logger.warning("Claude API definition failed for '%s': %s", word, exc)
        return None


def get_ai_definition(word: str) -> str | None:
    """Get a contextual English definition using the local MLX model or Claude API."""
    try:
        model, tokenizer = _load_ai_model()
    except Exception as exc:
        logger.warning("Failed to load AI model, trying Claude API: %s", exc)
        return _get_claude_definition(word)

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
        model, tokenizer, prompt=prompt, max_tokens=1024, verbose=False,
    )
    cleaned = _extract_final_answer(response)
    return cleaned if cleaned else None


QUESTION_BANK, QUESTION_BY_ID = load_question_bank()
VOCAB_BANK, VOCAB_BY_WORD = load_vocab_bank()


def refresh_vocab_bank() -> None:
    global VOCAB_BANK, VOCAB_BY_WORD

    load_vocab_bank.cache_clear()
    VOCAB_BANK, VOCAB_BY_WORD = load_vocab_bank()


def persist_dictionary_cache(word: str, dictionary_cache: dict[str, Any]) -> int:
    payload = {
        "lookup_word": dictionary_cache.get("lookup_word"),
        "lemma": dictionary_cache.get("lemma"),
        "meanings": dictionary_cache.get("meanings", []),
        "related": dictionary_cache.get("related", []),
    }

    with VOCAB_WRITE_LOCK:
        path, raw_payload, raw_data = load_vocab_storage_payload()

        # Use the word directly if it's already an entry key;
        # only fall back to normalization for raw/original word forms.
        if word in raw_data:
            target_words = {word}
        else:
            target_words = {get_normalized_vocab_word(word)}

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
            # Use the word directly if it's already an entry key;
            # only fall back to normalization for raw/original word forms.
            metadata = raw_data.get(word)
            if not isinstance(metadata, dict):
                normalized_word = get_normalized_vocab_word(word)
                metadata = raw_data.get(normalized_word)
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
            metadata = raw_data.get(word)
            if not isinstance(metadata, dict):
                normalized_word = get_normalized_vocab_word(word)
                metadata = raw_data.get(normalized_word)
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


def persist_quiz_result(email: str, total: int, correct: int) -> None:
    """Append a quiz result to the user's history."""
    user_data = load_user_data(email)
    if "quiz_history" not in user_data:
        user_data["quiz_history"] = []
    user_data["quiz_history"].append({
        "date": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "correct": correct,
    })
    save_user_data(email, user_data)


def persist_vocab_tracking_for_user(email: str, update: VocabTrackingSyncIn) -> int:
    """Save vocab tracking data to the user's personal JSON file."""
    feedback_counts = {
        word: {"up": _coerce_non_negative_int(c.up), "down": _coerce_non_negative_int(c.down)}
        for word, c in update.feedback_counts.items()
    }

    user_data = load_user_data(email)
    existing_hard = user_data.get("tracking", {}).get("hard_questions", [])
    if not isinstance(existing_hard, list):
        existing_hard = []
    user_data["tracking"] = {
        "feedback_counts": feedback_counts,
        "hidden_words": update.hidden_words,
        "difficult_words": update.difficult_words,
        "hard_questions": existing_hard,
    }
    save_user_data(email, user_data)
    return len(feedback_counts)


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
    _bg_worker_status["running"] = True
    _bg_worker_status["mode"] = "backfill"

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


def _get_fresh_ai_definition(word: str) -> str | None:
    """Generate an AI definition without using the LRU cache."""
    try:
        model, tokenizer = _load_ai_model()
    except Exception as exc:
        logger.warning("Failed to load AI model, trying Claude API: %s", exc)
        return _get_claude_definition(word)

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
        model, tokenizer, prompt=prompt, max_tokens=1024, verbose=False,
    )
    cleaned = _extract_final_answer(response)
    return cleaned if cleaned else None


def _definition_differs(old: str, new: str) -> bool:
    """Return True if two definitions differ by more than 10% in wording."""
    from difflib import SequenceMatcher
    ratio = SequenceMatcher(None, old, new).ratio()
    return ratio < 0.90


def _read_env_flags() -> tuple[bool, bool]:
    import os
    backfill = os.environ.get("BACKFILL_DEFINITIONS", "true").lower() not in ("false", "0", "no")
    checking = os.environ.get("BACKFILL_CHECKING", "false").lower() not in ("false", "0", "no")
    return backfill, checking


def _is_backfill_enabled() -> bool:
    backfill, _ = _read_env_flags()
    return backfill


def _background_checking_worker() -> None:
    """Periodically re-check a random word's AI definition for quality."""
    logger.info("Background definition checking worker started")
    _bg_worker_status["running"] = True
    _bg_worker_status["mode"] = "checking"
    checked = 0
    updated = 0

    while not _background_stop.is_set():
        # Sleep 60 seconds, checking for stop every second
        for _ in range(60):
            if _background_stop.is_set():
                _bg_worker_status["running"] = False
                return
            time.sleep(1)

        if not VOCAB_BANK:
            continue

        item = random.choice(VOCAB_BANK)
        word = item["word"]
        existing = item.get("ai_definition") or ""
        logger.info("Background checking: picking word '%s' to verify", word)
        _bg_worker_status["last_word"] = word

        # Wait for the gate, yielding to any user requests
        while not _background_stop.is_set():
            if AI_MODEL_GATE.has_user_waiting():
                time.sleep(0.5)
                continue

            if AI_MODEL_GATE.background_acquire():
                _bg_worker_status["gate_acquired_time"] = datetime.now(timezone.utc).isoformat()
                try:
                    fresh = _get_fresh_ai_definition(word)
                    checked += 1
                    _bg_worker_status["checked"] = checked
                    _bg_worker_status["last_check_time"] = datetime.now(timezone.utc).isoformat()

                    if not fresh:
                        logger.info(
                            "Checking [%d checked, %d updated]: %s — AI returned empty",
                            checked, updated, word,
                        )
                        break

                    if not existing:
                        # Word had no definition — add it
                        persist_ai_definitions({word: fresh})
                        updated += 1
                        _bg_worker_status["updated"] = updated
                        logger.info(
                            "Checking [%d checked, %d updated]: %s — added missing definition",
                            checked, updated, word,
                        )
                    elif _definition_differs(existing, fresh):
                        persist_ai_definitions({word: fresh})
                        updated += 1
                        _bg_worker_status["updated"] = updated
                        logger.info(
                            "Checking [%d checked, %d updated]: %s — definition updated",
                            checked, updated, word,
                        )
                    else:
                        logger.info(
                            "Checking [%d checked, %d updated]: %s — definition unchanged",
                            checked, updated, word,
                        )
                except Exception as exc:
                    _bg_worker_status["last_error"] = f"{word}: {exc}"
                    logger.exception("Checking failed for: %s", word)
                finally:
                    AI_MODEL_GATE.background_release()
                break
            else:
                time.sleep(0.1)

    _bg_worker_status["running"] = False
    logger.info(
        "Background checking worker finished (checked=%d, updated=%d)",
        checked, updated,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_user_data_dir()
    backfill, checking = _read_env_flags()

    if not backfill and not checking:
        logger.info("Background threads disabled (BACKFILL_DEFINITIONS=false, BACKFILL_CHECKING=false)")
    elif backfill and not checking:
        # Finish unfinished definitions, then thread dies
        thread = threading.Thread(
            target=_background_definition_worker, daemon=True, name="bg-ai-definitions",
        )
        thread.start()
        logger.info("Background backfill only (no checking)")
    elif backfill and checking:
        # Finish definitions first, then move into checking mode
        def _backfill_then_check():
            _background_definition_worker()
            if not _background_stop.is_set():
                _background_checking_worker()
        thread = threading.Thread(
            target=_backfill_then_check, daemon=True, name="bg-ai-definitions",
        )
        thread.start()
        logger.info("Background backfill + checking enabled")
    else:
        # checking only (BACKFILL_DEFINITIONS=false, BACKFILL_CHECKING=true)
        thread = threading.Thread(
            target=_background_checking_worker, daemon=True, name="bg-ai-checking",
        )
        thread.start()
        logger.info("Background checking only (no backfill)")

    yield
    _background_stop.set()


app = FastAPI(title="Quiz Patente B", lifespan=lifespan)

from backend.app.rate_limit import limiter, RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi import _rate_limit_exceeded_handler

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

_cors_origins_raw = os.environ.get("CORS_ORIGINS", "").strip()
if _cors_origins_raw:
    _cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    )
    logger.info("CORS enabled for origins: %s", _cors_origins)
else:
    logger.info("CORS disabled (CORS_ORIGINS unset). Same-origin only.")


def get_question_or_404(question_id: int) -> dict[str, Any]:
    question = QUESTION_BY_ID.get(question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found.")
    return question


@app.get("/api/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/worker-status")
async def worker_status() -> dict[str, Any]:
    status = dict(_bg_worker_status)
    status["gate_locked"] = AI_MODEL_GATE._lock.locked()
    status["gate_user_waiting"] = AI_MODEL_GATE._user_waiting
    return status


@app.get("/api/users")
async def list_users(_admin: str = Depends(require_admin)) -> list[UserOut]:
    users = load_user_registry()
    return [UserOut(email=u["email"], created=u["created"]) for u in users]


@app.post("/api/users", status_code=201, response_model=UserCreatedOut)
async def create_user(body: UserCreateIn, background_tasks: BackgroundTasks) -> UserCreatedOut:
    from backend.app.auth import generate_token, hash_token
    from backend.app.email_sender import send_welcome_token

    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address.")

    with USER_DATA_LOCK:
        users = _read_user_registry_unlocked()
        if any(u["email"] == email for u in users):
            raise HTTPException(status_code=409, detail="User already exists.")

        token = generate_token()
        created = datetime.now(timezone.utc).isoformat()
        users.append({"email": email, "created": created, "token_hash": hash_token(token)})
        _write_user_registry_unlocked(users)
        _write_user_data_unlocked(email, _empty_user_data(email))

    # Email after the response is sent so SMTP latency doesn't slow registration
    # and so SMTP failures don't break it (send_welcome_token swallows errors).
    background_tasks.add_task(send_welcome_token, email, token)

    return UserCreatedOut(email=email, created=created, token=token)


@app.delete("/api/users/{email}")
async def delete_user(
    email: str,
    caller: str = Depends(get_current_user_email),
) -> dict[str, str]:
    email = email.strip().lower()
    admin = _admin_email()
    is_admin = bool(admin) and caller == admin
    if caller != email and not is_admin:
        raise HTTPException(status_code=403, detail="You can only delete your own account.")

    with USER_DATA_LOCK:
        users = _read_user_registry_unlocked()
        updated = [u for u in users if u["email"] != email]
        if len(updated) == len(users):
            raise HTTPException(status_code=404, detail="User not found.")

        _write_user_registry_unlocked(updated)

        path = get_user_file_path(email)
        if path.exists():
            path.unlink()

    return {"status": "deleted", "email": email}


@app.get("/api/auth/whoami")
async def whoami(email: str = Depends(get_current_user_email)) -> dict[str, str]:
    return {"email": email}


# Per-email forgot-token throttle: max FORGOT_PER_EMAIL_LIMIT requests per
# FORGOT_PER_EMAIL_WINDOW_SECONDS. Stops a single email address from being
# spammed even if the attacker rotates source IPs (which would defeat the
# slowapi per-IP limit). Reset on process restart — fine for a hobby app.
FORGOT_PER_EMAIL_LIMIT = 5
FORGOT_PER_EMAIL_WINDOW_SECONDS = 3600
_forgot_token_history: dict[str, list[float]] = {}
_forgot_token_history_lock = Lock()


def _forgot_token_throttled(email: str) -> bool:
    """Return True if the per-email rate cap is exceeded. Records this attempt."""
    now = time.time()
    cutoff = now - FORGOT_PER_EMAIL_WINDOW_SECONDS
    with _forgot_token_history_lock:
        history = [t for t in _forgot_token_history.get(email, []) if t >= cutoff]
        if len(history) >= FORGOT_PER_EMAIL_LIMIT:
            _forgot_token_history[email] = history
            return True
        history.append(now)
        _forgot_token_history[email] = history
        return False


class ForgotTokenIn(BaseModel):
    email: str


@app.post("/api/auth/forgot-token")
async def forgot_token(
    body: ForgotTokenIn,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    # Rate-limited at nginx (30/min on /api/) + the per-email throttle below.
    # See prefetch_vocab_batch for why @limiter.limit is not used here.
    """Mint a new bearer token, invalidate the old, email it.

    Anti-enumeration: always returns {"status": "ok"} regardless of whether the
    email is registered or the request is throttled.
    """
    from backend.app.auth import generate_token, hash_token
    from backend.app.email_sender import send_forgot_token

    email = body.email.strip().lower()
    uniform = {"status": "ok"}

    # Validate format here (not via Pydantic) so we can keep the uniform reply.
    if not email or "@" not in email or "." not in email.split("@", 1)[1]:
        return uniform

    if _forgot_token_throttled(email):
        logger.info("forgot-token throttled for %s", email)
        return uniform

    new_token: str | None = None
    with USER_DATA_LOCK:
        users = _read_user_registry_unlocked()
        for user in users:
            if user["email"] == email:
                new_token = generate_token()
                user["token_hash"] = hash_token(new_token)
                _write_user_registry_unlocked(users)
                break

    if new_token is not None:
        background_tasks.add_task(send_forgot_token, email, new_token)

    return uniform


@app.get("/api/legacy-tracking")
async def check_legacy_tracking() -> dict[str, Any]:
    """Check if the shared vocab file has tracking data that can be migrated."""
    _, _, raw_data = load_vocab_storage_payload()
    has_tracking = False
    tracked_count = 0
    for metadata in raw_data.values():
        if not isinstance(metadata, dict):
            continue
        tracking = metadata.get("tracking")
        if isinstance(tracking, dict):
            up = tracking.get("up", 0)
            down = tracking.get("down", 0)
            known = tracking.get("known", False)
            difficult = tracking.get("difficult", False)
            if up or down or known or difficult:
                has_tracking = True
                tracked_count += 1
    return {"has_tracking": has_tracking, "tracked_count": tracked_count}


@app.post("/api/migrate")
async def migrate_legacy_tracking(email: str = Depends(get_current_user_email)) -> dict[str, Any]:
    """Import existing tracking from shared vocab file into a user's data."""
    _, _, raw_data = load_vocab_storage_payload()

    feedback_counts = {}
    hidden_words = []
    difficult_words = []

    for word, metadata in raw_data.items():
        if not isinstance(metadata, dict):
            continue
        tracking = metadata.get("tracking")
        if not isinstance(tracking, dict):
            continue
        up = _coerce_non_negative_int(tracking.get("up", 0))
        down = _coerce_non_negative_int(tracking.get("down", 0))
        known = bool(tracking.get("known", False))
        difficult = bool(tracking.get("difficult", False))

        if up or down:
            feedback_counts[word] = {"up": up, "down": down}
        if known:
            hidden_words.append(word)
        if difficult:
            difficult_words.append(word)

    user_data = load_user_data(email)
    existing_hard = user_data.get("tracking", {}).get("hard_questions", [])
    if not isinstance(existing_hard, list):
        existing_hard = []
    user_data["tracking"] = {
        "feedback_counts": feedback_counts,
        "hidden_words": hidden_words,
        "difficult_words": difficult_words,
        "hard_questions": existing_hard,
    }
    save_user_data(email, user_data)

    # Strip tracking from shared vocab file
    await asyncio.to_thread(_strip_legacy_tracking)

    return {
        "imported_feedback_counts": len(feedback_counts),
        "imported_hidden_words": len(hidden_words),
        "imported_difficult_words": len(difficult_words),
    }


def _strip_legacy_tracking() -> None:
    """Remove tracking fields from the shared vocabulary file."""
    with VOCAB_WRITE_LOCK:
        path, raw_payload, raw_data = load_vocab_storage_payload()
        for metadata in raw_data.values():
            if isinstance(metadata, dict) and "tracking" in metadata:
                del metadata["tracking"]
        write_vocab_storage_payload(path, raw_payload, raw_data)
    refresh_vocab_bank()


@app.get("/api/quiz", response_model=QuizResponse)
@limiter.limit("30/minute")
async def get_quiz(
    request: Request,
    count: int = Query(default=30, ge=1, le=100),
) -> QuizResponse:
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


@app.get("/api/topics", response_model=TopicsResponse)
@limiter.limit("60/minute")
async def get_topics(request: Request) -> TopicsResponse:
    topics = sorted({item["topic"] for item in QUESTION_BANK})
    return TopicsResponse(topics=topics)


@app.get("/api/topics/questions", response_model=TopicQuestionsResponse)
@limiter.limit("60/minute")
async def get_topic_questions(
    request: Request,
    topic: str = Query(...),
    answer: bool = Query(default=True),
) -> TopicQuestionsResponse:
    matches = [
        QuestionOut(
            id=item["id"],
            text=item["text"],
            image_url=item["image_url"],
            topic=item["topic"],
        )
        for item in QUESTION_BANK
        if item["topic"] == topic and bool(item["answer"]) == answer
    ]
    return TopicQuestionsResponse(
        topic=topic, answer=answer, questions=matches, count=len(matches)
    )


@app.get("/api/questions/{question_id}/translation", response_model=TranslationResponse)
@limiter.limit("10/minute")
async def get_translation(request: Request, question_id: int) -> TranslationResponse:
    question = get_question_or_404(question_id)
    try:
        translation = await asyncio.to_thread(translate_text, question["text"])
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Google Translate translation failed. Check your network connection and try again.",
        ) from exc

    return TranslationResponse(question_id=question_id, translation=translation)


@app.post("/api/translations/batch", response_model=BatchTranslationResponse)
async def get_translations_batch(
    payload: BatchTranslationRequest,
) -> BatchTranslationResponse:
    if len(payload.question_ids) > 100:
        raise HTTPException(status_code=400, detail="Batch is limited to 100 question IDs.")

    unique_ids = list(dict.fromkeys(payload.question_ids))
    translations: dict[int, str] = {}
    errors: dict[int, str] = {}

    for qid in unique_ids:
        question = QUESTION_BY_ID.get(qid)
        if question is None:
            errors[qid] = "not_found"
            continue
        try:
            translations[qid] = await asyncio.to_thread(translate_text, question["text"])
        except Exception as exc:
            errors[qid] = str(exc) or "translation_failed"

    return BatchTranslationResponse(translations=translations, errors=errors)


@app.get("/api/questions/{question_id}/answer", response_model=AnswerRevealResponse)
async def reveal_answer(question_id: int) -> AnswerRevealResponse:
    question = get_question_or_404(question_id)
    return AnswerRevealResponse(question_id=question_id, correct_answer=question["answer"])


@app.get("/api/questions/{question_id}/variants", response_model=QuestionVariantsResponse)
async def get_question_variants(question_id: int) -> QuestionVariantsResponse:
    question = get_question_or_404(question_id)
    topic = question["topic"]
    image_url = question.get("image_url")
    matches = [
        QuestionMatchOut(
            id=q["id"],
            text=q["text"],
            answer=q["answer"],
            image_url=q.get("image_url"),
            topic=q["topic"],
        )
        for q in QUESTION_BANK
        if q["topic"] == topic and q.get("image_url") == image_url
    ]
    return QuestionVariantsResponse(question_id=question_id, questions=matches, count=len(matches))


@app.get("/api/vocab", response_model=VocabResponse)
async def get_vocab(email: str = Depends(get_current_user_email)) -> VocabResponse:
    user_data = load_user_data(email)
    user_tracking = user_data.get("tracking", {})
    user_counts = user_tracking.get("feedback_counts", {})
    user_hidden = set(user_tracking.get("hidden_words", []))
    user_difficult = set(user_tracking.get("difficult_words", []))

    words = []
    for item in VOCAB_BANK:
        word = item["word"]
        counts = user_counts.get(word, {})
        tracking = VocabTrackingOut(
            up=_coerce_non_negative_int(counts.get("up", 0)),
            down=_coerce_non_negative_int(counts.get("down", 0)),
            known=word in user_hidden,
            difficult=word in user_difficult,
        )
        words.append(VocabWordOut(
            word=word,
            known_translation=item["known_translation"],
            tracking=tracking,
        ))

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
@limiter.limit("10/minute")
async def translate_vocab_word(
    request: Request,
    word: str = Query(min_length=1),
) -> VocabTranslationResponse:
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

    # Only fetch dictionary details if we don't already have a usable definition.
    # The dictionary lookup is slow (network scrape + translation of meanings).
    google_hint = translation or ""
    dictionary: VocabDictionaryOut | None = None
    cached_dict = entry.get("dictionary_cache")
    skip_dictionary = bool(translation or ai_definition) and not cached_dict
    try:
        dictionary_payload = (
            cached_dict if skip_dictionary
            else await asyncio.to_thread(get_dictionary_details, word, google_hint)
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


@app.get("/api/vocab/{word}/questions", response_model=VocabQuestionsResponse)
async def get_vocab_word_questions(word: str) -> VocabQuestionsResponse:
    stem = re.sub(r"[aeio]+$", "", word)
    if len(stem) >= 4:
        pattern = re.compile(rf"\b{re.escape(stem)}\w*", re.IGNORECASE)
    else:
        pattern = re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
    matches = [
        QuestionMatchOut(
            id=q["id"],
            text=q["text"],
            answer=q["answer"],
            image_url=q.get("image_url"),
            topic=q["topic"],
        )
        for q in QUESTION_BANK
        if pattern.search(q["text"])
    ]
    return VocabQuestionsResponse(word=word, questions=matches, count=len(matches))


@app.post("/api/vocab/tracking", response_model=VocabTrackingSyncResponse)
async def sync_vocab_tracking(
    update: VocabTrackingSyncIn, email: str = Depends(get_current_user_email)
) -> VocabTrackingSyncResponse:
    try:
        updated_words = await asyncio.to_thread(persist_vocab_tracking_for_user, email, update)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Unable to persist vocab tracking.") from exc

    return VocabTrackingSyncResponse(updated_words=updated_words)


@app.post("/api/vocab/prefetch", response_model=VocabPrefetchResponse)
async def prefetch_vocab_batch(
    body: VocabPrefetchRequest,
    background_tasks: BackgroundTasks,
) -> VocabPrefetchResponse:
    # Rate-limited at nginx (10/min for AI endpoints) + 50-word cap below.
    # slowapi's @limiter.limit decorator does not compose with from-future
    # annotations on POST endpoints with body params (FastAPI fails to resolve
    # the body type via typing.get_type_hints on the slowapi wrapper).
    if len(body.words) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 words per request.")
    words = [word for word in unique_preserve_order(body.words) if word in VOCAB_BY_WORD]
    if words:
        background_tasks.add_task(prefetch_vocab_meanings, words)

    return VocabPrefetchResponse(queued_words=len(words))


@app.post("/api/score", response_model=ScoreResponse)
async def score_quiz(
    submission: ScoreSubmission, email: str = Depends(get_current_user_email)
) -> ScoreResponse:
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

    # Persist quiz result to user's history
    await asyncio.to_thread(
        persist_quiz_result, email, total, correct,
    )

    return ScoreResponse(total=total, correct=correct, wrong=total - correct, details=details)


@app.get("/api/quiz/history", response_model=QuizHistoryResponse)
async def get_quiz_history(email: str = Depends(get_current_user_email)) -> QuizHistoryResponse:
    user_data = load_user_data(email)
    history = user_data.get("quiz_history", [])
    entries = []
    for h in history:
        if not isinstance(h, dict):
            continue
        try:
            entries.append(QuizHistoryEntry(
                date=h.get("date", ""),
                total=h.get("total", 0),
                correct=h.get("correct", 0),
            ))
        except (TypeError, ValueError):
            continue
    return QuizHistoryResponse(history=entries)


@app.get("/api/quiz/hard-questions", response_model=HardQuestionsResponse)
@limiter.limit("60/minute")
async def get_hard_questions(
    request: Request, email: str = Depends(get_current_user_email)
) -> HardQuestionsResponse:
    user_data = load_user_data(email)
    raw = user_data.get("tracking", {}).get("hard_questions", [])
    ids = [int(qid) for qid in raw if isinstance(qid, int) or (isinstance(qid, str) and qid.isdigit())]
    return HardQuestionsResponse(hard_question_ids=ids)


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

