#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path

try:
    from simplemma import lemmatize
except ImportError as exc:  # pragma: no cover - import guard
    raise SystemExit(
        "Missing dependency `simplemma`. Install it with `pip install simplemma`."
    ) from exc


ROOT_DIR = Path(__file__).resolve().parents[1]
VOCAB_FILE = ROOT_DIR / "vocabolario_patente.json"
OUTPUT_FILE = ROOT_DIR / "vocabolario_patente.normalized.json"
CLITIC_SUFFIXES = (
    "gliele",
    "glieli",
    "glielo",
    "gliela",
    "gliene",
    "sene",
    "cene",
    "vene",
    "mene",
    "tele",
    "teli",
    "telo",
    "tela",
    "tene",
    "cele",
    "celi",
    "celo",
    "cela",
    "vele",
    "veli",
    "velo",
    "vela",
    "mele",
    "meli",
    "melo",
    "mela",
    "sele",
    "seli",
    "selo",
    "sela",
    "gli",
    "che",
    "ci",
    "ce",
    "glie",
    "la",
    "le",
    "li",
    "lo",
    "mi",
    "ne",
    "si",
    "te",
    "ti",
    "ve",
    "vi",
)
VERB_HOST_SUFFIXES = (
    "are",
    "ere",
    "ire",
    "ando",
    "endo",
    "ato",
    "ata",
    "ati",
    "ate",
    "uto",
    "uta",
    "uti",
    "ute",
    "ito",
    "ita",
    "iti",
    "ite",
    "ar",
    "er",
    "ir",
)


def coerce_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def coerce_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def strip_clitic_suffixes(word: str) -> list[str]:
    queue = [word.lower()]
    discovered: list[str] = []
    seen = {word.lower()}

    while queue:
        current = queue.pop(0)
        if current.endswith("mente"):
            continue
        for suffix in CLITIC_SUFFIXES:
            if not current.endswith(suffix) or len(current) <= len(suffix) + 1:
                continue

            stem = current[: -len(suffix)]
            candidates: list[str] = []

            if stem.endswith(VERB_HOST_SUFFIXES):
                candidates.append(stem)

            if stem.endswith(("ar", "er", "ir")):
                candidates.append(f"{stem}e")

            for candidate in candidates:
                if candidate not in seen:
                    seen.add(candidate)
                    discovered.append(candidate)
                    queue.append(candidate)

    return discovered


def normalize_word(word: str) -> str:
    base = word.strip().lower()
    base_lemma = lemmatize(base, lang="it") or base
    if base_lemma != base:
        return base_lemma

    for stripped in strip_clitic_suffixes(base):
        stripped_lemma = lemmatize(stripped, lang="it") or stripped
        if stripped_lemma != stripped or stripped != base:
            return stripped_lemma

    return base


def normalize_dictionary_cache(cache: object, normalized_word: str) -> dict[str, object] | None:
    if not isinstance(cache, dict):
        return None

    lookup_word = str(cache.get("lookup_word") or "").strip()
    meanings = [str(item).strip() for item in cache.get("meanings", []) if str(item).strip()]
    related = [item for item in cache.get("related", []) if isinstance(item, dict)]

    if not meanings and not related and lookup_word and lookup_word != normalized_word:
        return None

    return cache


def load_existing_normalized_entries() -> dict[str, dict[str, object]]:
    if not OUTPUT_FILE.exists():
        return {}

    payload = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    entries = payload.get("entries")
    return entries if isinstance(entries, dict) else {}


def merge_vocab() -> dict[str, object]:
    raw_vocab = json.loads(VOCAB_FILE.read_text(encoding="utf-8"))
    existing_normalized_entries = load_existing_normalized_entries()
    by_word: dict[str, str] = {}
    entries: dict[str, dict[str, object]] = {}

    for word, metadata in raw_vocab.items():
        normalized_word = normalize_word(word)
        by_word[word] = normalized_word

        tracking = metadata.get("tracking") if isinstance(metadata, dict) else {}
        dictionary_cache = metadata.get("dictionary_cache") if isinstance(metadata, dict) else None
        english = (metadata.get("english") or "").strip() if isinstance(metadata, dict) else ""
        topics = metadata.get("topics") if isinstance(metadata, dict) else []
        existing_entry = existing_normalized_entries.get(normalized_word, {})
        entry = entries.setdefault(
            normalized_word,
            {
                "word": normalized_word,
                "english_variants": [],
                "frequency": 0.0,
                "difficulty": 0,
                "count": 0,
                "topics": set(),
                "source_words": [],
                "tracking": {
                    "up": coerce_int(existing_entry.get("tracking", {}).get("up")),
                    "down": coerce_int(existing_entry.get("tracking", {}).get("down")),
                    "known": bool(existing_entry.get("tracking", {}).get("known", False)),
                    "difficult": bool(existing_entry.get("tracking", {}).get("difficult", False)),
                },
                "dictionary_cache": normalize_dictionary_cache(
                    existing_entry.get("dictionary_cache"), normalized_word
                ),
            },
        )

        if english and english not in entry["english_variants"]:
            entry["english_variants"].append(english)
        entry["frequency"] += coerce_float(metadata.get("frequency") if isinstance(metadata, dict) else 0)
        entry["difficulty"] = max(entry["difficulty"], coerce_int(metadata.get("difficulty") if isinstance(metadata, dict) else 0))
        entry["count"] += coerce_int(metadata.get("count") if isinstance(metadata, dict) else 0)
        entry["source_words"].append(word)
        if isinstance(topics, list):
            entry["topics"].update(topic for topic in topics if isinstance(topic, str))

        if isinstance(tracking, dict) and normalized_word not in existing_normalized_entries:
            entry["tracking"]["up"] += coerce_int(tracking.get("up"))
            entry["tracking"]["down"] += coerce_int(tracking.get("down"))
            entry["tracking"]["known"] = entry["tracking"]["known"] or bool(tracking.get("known"))
            entry["tracking"]["difficult"] = entry["tracking"]["difficult"] or bool(tracking.get("difficult"))
        if entry["dictionary_cache"] is None:
            entry["dictionary_cache"] = normalize_dictionary_cache(dictionary_cache, normalized_word)

    normalized_entries = {}
    for normalized_word, entry in sorted(entries.items()):
        english_variants = sorted(entry["english_variants"])
        normalized_entries[normalized_word] = {
            "word": normalized_word,
            "english": " / ".join(english_variants),
            "english_variants": english_variants,
            "frequency": entry["frequency"],
            "difficulty": entry["difficulty"],
            "count": entry["count"],
            "topics": sorted(entry["topics"]),
            "source_words": sorted(entry["source_words"]),
            "source_count": len(entry["source_words"]),
            "tracking": entry["tracking"],
            "dictionary_cache": entry["dictionary_cache"],
        }

    return {
        "entries": normalized_entries,
        "by_word": dict(sorted(by_word.items())),
        "meta": {
            "source_file": VOCAB_FILE.name,
            "output_file": OUTPUT_FILE.name,
            "source_word_count": len(raw_vocab),
            "normalized_word_count": len(normalized_entries),
        },
    }


def main() -> None:
    payload = merge_vocab()
    OUTPUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {payload['meta']['normalized_word_count']} normalized entries to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
