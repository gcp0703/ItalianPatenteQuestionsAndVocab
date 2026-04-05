#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    from simplemma import lemmatize
except ImportError as exc:  # pragma: no cover - import guard
    raise SystemExit(
        "Missing dependency `simplemma`. Install it with `pip install simplemma`."
    ) from exc

try:
    from italian_dictionary.dictionary import get_definition
    from italian_dictionary.exceptions import WordNotFoundError
except ImportError as exc:  # pragma: no cover - import guard
    raise SystemExit(
        "Missing dependency `italian-dictionary`. Install it with `pip install italian-dictionary`."
    ) from exc


ROOT_DIR = Path(__file__).resolve().parents[1]
VOCAB_FILE = ROOT_DIR / "vocabolario_patente.json"
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
    "cene",
    "vele",
    "veli",
    "velo",
    "vela",
    "vene",
    "mele",
    "meli",
    "melo",
    "mela",
    "mene",
    "sele",
    "seli",
    "selo",
    "sela",
    "sene",
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


@dataclass(frozen=True)
class LookupResult:
    original: str
    resolved: str | None
    strategy: str | None
    found: bool


def load_vocab_words() -> list[str]:
    payload = json.loads(VOCAB_FILE.read_text())
    return list(payload.keys())


def unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_items: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            unique_items.append(item)
    return unique_items


def unique_candidate_pairs(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    unique_items: list[tuple[str, str]] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique_items.append(item)
    return unique_items


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

            # Enclitic infinitives drop the final "e": fermarsi -> fermar -> fermare
            if stem.endswith(("ar", "er", "ir")):
                candidates.append(f"{stem}e")

            for candidate in candidates:
                if candidate not in seen:
                    seen.add(candidate)
                    discovered.append(candidate)
                    queue.append(candidate)

    return discovered


def refined_lookup_candidates(word: str) -> list[tuple[str, str]]:
    base = word.lower()
    stripped_forms = strip_clitic_suffixes(base)

    candidates: list[tuple[str, str]] = [("raw", base)]

    lemma = lemmatize(base, lang="it")
    if lemma:
        candidates.append(("simplemma", lemma))

    for stripped in stripped_forms:
        candidates.append(("stripped-clitic", stripped))
        stripped_lemma = lemmatize(stripped, lang="it")
        if stripped_lemma:
            candidates.append(("stripped+simplemma", stripped_lemma))

    return unique_candidate_pairs(candidates)


def dictionary_contains(word: str) -> bool:
    try:
        get_definition(word, all_data=False)
        return True
    except WordNotFoundError:
        return False


def resolve_word(word: str, contains: Callable[[str], bool]) -> LookupResult:
    for strategy, candidate in refined_lookup_candidates(word):
        if contains(candidate):
            return LookupResult(original=word, resolved=candidate, strategy=strategy, found=True)

    return LookupResult(original=word, resolved=None, strategy=None, found=False)


def analyze(words: list[str]) -> None:
    results = [resolve_word(word, dictionary_contains) for word in words]
    found = [item for item in results if item.found]
    missing = [item for item in results if not item.found]
    strategy_counts = Counter(item.strategy for item in found if item.strategy)

    print(
        json.dumps(
            {
                "total": len(results),
                "found": len(found),
                "missing": len(missing),
                "strategy_counts": dict(strategy_counts),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    print("\nFound examples:")
    for item in found[:20]:
        print(f"- {item.original} -> {item.resolved} ({item.strategy})")

    print("\nMissing examples:")
    for item in missing[:20]:
        print(f"- {item.original}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze QuizPatenteB vocabulary overlap against italian-dictionary using a refined normalizer."
    )
    parser.add_argument(
        "--word",
        help="Inspect the normalized lookup candidates for a single word instead of analyzing the whole vocabulary.",
    )
    args = parser.parse_args()

    if args.word:
        print(json.dumps(refined_lookup_candidates(args.word), ensure_ascii=False, indent=2))
        return

    analyze(load_vocab_words())


if __name__ == "__main__":
    main()
