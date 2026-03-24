from __future__ import annotations

import asyncio
import json
import random
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from deep_translator import GoogleTranslator
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_FILE = ROOT_DIR / "quizPatenteB2023.json"
VOCAB_FILE = ROOT_DIR / "vocabolario_patente.json"
IMAGE_DIR = ROOT_DIR / "img_sign"
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"

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


class VocabResponse(BaseModel):
    words: list[VocabWordOut]


class VocabTranslationResponse(BaseModel):
    word: str
    translation: str


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
def load_vocab_bank() -> tuple[list[dict[str, str | None]], dict[str, dict[str, str | None]]]:
    with VOCAB_FILE.open("r", encoding="utf-8") as vocab_file:
        raw_data = json.load(vocab_file)

    items = [
        {
            "word": word,
            "known_translation": (metadata.get("english") or "").strip() or None,
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


QUESTION_BANK, QUESTION_BY_ID = load_question_bank()
VOCAB_BANK, VOCAB_BY_WORD = load_vocab_bank()

app = FastAPI(title="Quiz Patente B")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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
        VocabWordOut(word=item["word"], known_translation=item["known_translation"])
        for item in VOCAB_BANK
    ]
    return VocabResponse(words=words)


@app.get("/api/vocab/translate", response_model=VocabTranslationResponse)
async def translate_vocab_word(word: str = Query(min_length=1)) -> VocabTranslationResponse:
    entry = VOCAB_BY_WORD.get(word)
    if not entry:
        raise HTTPException(status_code=404, detail="Word not found.")

    translation = entry["known_translation"]
    if not translation:
        try:
            translation = await asyncio.to_thread(translate_text, word)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="Google Translate translation failed. Check your network connection and try again.",
            ) from exc

    return VocabTranslationResponse(word=word, translation=translation)


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
