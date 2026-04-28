"""Mint bearer tokens for legacy users that pre-date authentication.

Usage:
    QPB_LOAD_DOTENV=0 \\
    AUTH_TOKEN_PEPPER=... \\
    QPB_USER_DATA_DIR=/home/azureuser/quizpatenteb/user_data \\
    python -m backend.scripts.mint_user_tokens

Prints one tab-separated row per migrated user: email\\ttoken.
Idempotent: skips users that already have a token_hash.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    from backend.app.auth import generate_token, hash_token

    user_data_dir_raw = os.environ.get("QPB_USER_DATA_DIR")
    if not user_data_dir_raw:
        print("QPB_USER_DATA_DIR is not set.", file=sys.stderr)
        return 2

    user_data_dir = Path(user_data_dir_raw)
    registry_path = user_data_dir / "_users.json"
    if not registry_path.exists():
        print(f"No registry at {registry_path}", file=sys.stderr)
        return 1

    with registry_path.open() as f:
        users = json.load(f)

    minted = 0
    for entry in users:
        if entry.get("token_hash"):
            continue
        token = generate_token()
        entry["token_hash"] = hash_token(token)
        print(f"{entry['email']}\t{token}")
        minted += 1

    if minted:
        tmp = registry_path.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump(users, f, indent=2)
        tmp.replace(registry_path)

    print(f"Minted {minted} new tokens.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
