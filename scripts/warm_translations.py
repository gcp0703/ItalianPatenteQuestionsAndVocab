#!/usr/bin/env python3
"""Pre-warm the persistent translation cache by translating every quiz question.

Resumable and idempotent: re-running skips already-cached entries because every
call goes through TranslationCache. On Google Translate failure / 429 / empty
response the script applies exponential backoff, then logs and skips on final
failure so the next run picks the entry up.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

# Importing main also loads the repo-root .env via dotenv (when QPB_LOAD_DOTENV
# isn't disabled), so WARM_TRANSLATIONS_ENABLED becomes available below.
from backend.app.main import (  # noqa: E402  (path setup must precede import)
    DATA_FILE,
    _translation_cache,
    _translation_cache_key,
    translate_text,
)


def _warm_enabled() -> bool:
    return os.environ.get("WARM_TRANSLATIONS_ENABLED", "false").lower() in (
        "true",
        "1",
        "yes",
    )


def _flatten_questions(raw: dict) -> list[str]:
    """Walk the nested category->subcategory->[questions] structure."""
    texts: list[str] = []
    for subcats in raw.values():
        if not isinstance(subcats, dict):
            continue
        for items in subcats.values():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("q"), str):
                    texts.append(item["q"])
    return texts


def _unique_preserving_order(texts: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for text in texts:
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _translate_with_retry(text: str, max_attempts: int = 5) -> str | None:
    """Call translate_text with exponential backoff on transient failures."""
    delay = 1.0
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return translate_text(text)
        except Exception as exc:
            last_err = exc
            if attempt == max_attempts:
                break
            print(
                f"  retry {attempt}/{max_attempts - 1} after error: {exc!r}; "
                f"sleeping {delay:.1f}s",
                file=sys.stderr,
            )
            time.sleep(delay)
            delay *= 2
    print(f"  giving up: {last_err!r}", file=sys.stderr)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lang",
        default="en",
        help="Target language code (currently only 'en' is wired through translate_text)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.3,
        help="Seconds to sleep between Google Translate calls (default: 0.3)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after attempting N uncached entries (for testing)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even when WARM_TRANSLATIONS_ENABLED is not truthy in the environment",
    )
    args = parser.parse_args()

    if args.lang != "en":
        print(
            f"Only 'en' is currently supported; got --lang={args.lang}",
            file=sys.stderr,
        )
        return 2

    if not _warm_enabled() and not args.force:
        print(
            "Translation warmer is disabled. Set WARM_TRANSLATIONS_ENABLED=true "
            "in .env (or pass --force) to run.",
            file=sys.stderr,
        )
        return 0

    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    texts = _unique_preserving_order(_flatten_questions(raw))
    total = len(texts)

    cached_at_start = sum(
        1 for t in texts if _translation_cache.get(t) is not None
    )
    print(
        f"Found {total} unique question texts; {cached_at_start} already cached, "
        f"{total - cached_at_start} to translate."
    )

    attempted = 0
    succeeded = 0
    skipped = 0

    for index, text in enumerate(texts, start=1):
        if _translation_cache.get(text) is not None:
            continue

        if args.limit is not None and attempted >= args.limit:
            print(f"Reached --limit={args.limit}; stopping.")
            break

        attempted += 1
        result = _translate_with_retry(text)
        if result is None:
            skipped += 1
            print(
                f"[{index}/{total}] SKIP key={_translation_cache_key(text)[:8]} "
                f"src={text[:60]!r}"
            )
        else:
            succeeded += 1
            print(
                f"[{index}/{total}] OK key={_translation_cache_key(text)[:8]} "
                f"src={text[:60]!r}"
            )

        time.sleep(args.delay)

    cached_at_end = sum(
        1 for t in texts if _translation_cache.get(t) is not None
    )
    print(
        f"\nDone. Attempted {attempted}, succeeded {succeeded}, skipped {skipped}. "
        f"Cache now holds {cached_at_end}/{total} entries."
    )
    return 0 if skipped == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
