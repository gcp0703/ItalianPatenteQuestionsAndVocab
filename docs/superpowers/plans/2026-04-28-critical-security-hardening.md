# Critical Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the seven critical security gaps (C1–C7) identified in the 2026-04-28 security review of the QuizPatenteB production deployment without breaking the existing single-process FastAPI app or its file-backed JSON store.

**Architecture:**
- Secrets move out of the repo entirely. Production reads a single `/etc/quizpatenteb.env` injected by systemd; the application uses `os.environ` only.
- Authentication uses server-issued opaque bearer tokens (UUID4, hashed at rest with `hashlib.sha256`) bound to an email at registration, validated by a new `Authorization: Bearer <token>` dependency. Existing users are migrated via a one-shot CLI that prints their token for out-of-band delivery.
- Rate limiting is enforced by `slowapi` in the app and `limit_req` in nginx (defense in depth), with stricter limits on the Anthropic-touching `/api/vocab/translate`, `/api/questions/{id}/translation`, and `/api/vocab/prefetch` endpoints.
- Anthropic usage is metered via per-call structured logs and bounded by a hard monthly cap configured in the Anthropic console.

**Tech Stack:** FastAPI, Starlette `SessionMiddleware` (used for the auth secret only — auth state lives in the bearer token), `slowapi` (FastAPI port of Flask-Limiter), nginx `limit_req_zone`, systemd `EnvironmentFile`, pytest + httpx `TestClient` (new, no test infrastructure exists today).

**Scope explicitly NOT covered (deferred to a later HIGH-tier plan):** NSG/firewall changes, SSH hardening, TLS audit, backups, dependency scanning, multi-tenant isolation review beyond what auth implies, CSP/header tightening, systemd sandboxing.

**Production safety:** This plan changes auth behavior, which will break existing API clients (the React SPA in `frontend/`). Tasks 6 and 7 include the frontend changes. Tasks must be deployed together — do not push tasks 5–8 to production individually. Task 9 is a one-shot data migration that must run on the production VM after deploying tasks 5–8.

**Order of operations:**
1. Tasks 1–3: Lock down secrets and CORS (no app behavior change). Safe to deploy individually.
2. Task 4: Add test infrastructure (no app behavior change).
3. Tasks 5–8: Add auth, auth-gate user endpoints, rate-limit, and update the SPA. Deploy together.
4. Task 9: Run user-token migration CLI on production.
5. Tasks 10–11: Add Anthropic spend monitoring and nginx rate limiting.

---

## File Structure

**New files:**
- `.env.example` (repo root) — replaces `backend/.env.example`; documents every env var used by the production systemd unit (`SESSION_SECRET`, `AUTH_TOKEN_PEPPER`, `ANTHROPIC_API_KEY`, `AI_MODEL`, `BACKFILL_DEFINITIONS`, `BACKFILL_CHECKING`, `CORS_ORIGINS`, `ANTHROPIC_MONTHLY_USD_CAP`).
- `backend/app/auth.py` — bearer-token validation, token issuance, password-hashing helpers. Single responsibility: identity.
- `backend/app/rate_limit.py` — `slowapi` Limiter setup and the `limiter` instance for use as a FastAPI dependency. Single responsibility: throttling configuration.
- `backend/app/spend.py` — Anthropic call accounting (per-call structured log with token counts; in-memory monthly tally; soft cap check). Single responsibility: cost telemetry.
- `backend/scripts/mint_user_tokens.py` — one-shot CLI that, for every email in `_users.json` without a `token_hash`, generates a UUID4, stores its SHA-256 (with pepper), and prints `email\ttoken` to stdout for out-of-band distribution.
- `tests/conftest.py` — pytest fixtures: temporary `USER_DATA_DIR`, `TestClient` factory, fake env.
- `tests/test_auth.py` — token validation, registration response shape, 401 on missing/bad token, session vs bearer.
- `tests/test_user_endpoints.py` — `/api/users` GET requires admin; DELETE only allows self; POST returns token once.
- `tests/test_rate_limit.py` — 11th call to `/api/vocab/translate` within a minute returns 429.
- `tests/test_cors.py` — `CORS_ORIGINS` env var produces the expected `Access-Control-Allow-Origin` header.
- `pytest.ini` (repo root) — minimal pytest config.

**Modified files:**
- `.gitignore` — add `.env`, `.env.*`, `!.env.example` (and `backend/.env*` defenses).
- `backend/app/main.py` — replace `dotenv_values(...)` calls (lines 853–855, 1255–1260) with `os.environ.get(...)`; replace `get_current_user_email` (lines 396–399) with bearer-token validator from `auth.py`; auth-gate `/api/users` GET/DELETE (lines 1419–1460); add bearer token return to `POST /api/users` (lines 1425–1441); read `CORS_ORIGINS` env var (lines 1389–1396); add `slowapi` middleware and per-endpoint limits.
- `backend/app/__main__.py` — wire the `mint_user_tokens` CLI subcommand (or document standalone invocation; we use standalone).
- `pyproject.toml` — add `slowapi`, `anthropic` (promote from optional to default — it's used in production), `pytest`, `httpx` (test only).
- `deployment/systemd/quizpatenteb.service` — change `EnvironmentFile=` to `/etc/quizpatenteb.env`.
- `deployment/nginx/patenteb.conf` — add `limit_req_zone` directives in nginx http context (separate file include) and `limit_req` in `/api/` and AI-endpoint locations.
- `docs/deployment.md` — document the new `/etc/quizpatenteb.env` location, `mint_user_tokens` migration step, and Anthropic console cap configuration.
- `frontend/src/App.jsx` and any auth-aware components — store `token` returned from registration in `localStorage`; send `Authorization: Bearer <token>` on every fetch; show login screen when token is missing/invalid (401).

**Deleted files:**
- `backend/.env` (untracked from git, kept locally, but not in repo).
- `backend/.env.example` (replaced by repo-root `.env.example`).

---

## Task 1: Untrack and gitignore all `.env` files (C1)

**Files:**
- Modify: `.gitignore`
- Delete from index: `backend/.env`, `backend/.env.example`
- Create: `.env.example` (new, at repo root)

**Context:** `git ls-files | grep .env` confirms `backend/.env` and `backend/.env.example` are tracked. `.gitignore` has no rule excluding env files. `backend/.env` currently contains only `AI_MODEL='mlx-community/Qwen3.5-27B-4bit'`, `BACKFILL_DEFINITIONS=false`, `BACKFILL_CHECKING=false` — non-sensitive — but the moment anyone adds `ANTHROPIC_API_KEY`, it leaks publicly. Audit history before assuming nothing has leaked.

- [ ] **Step 1: Audit git history for any secret that may have been committed**

Run:
```bash
cd /Users/gcp/Projects/QuizPatenteB
git log --all -p --full-history -- '*.env' 'backend/.env' 'backend/.env.example' \
  | grep -iE 'sk-ant-|api[-_]key|secret|password|token|bearer' \
  | head -40
```
Expected: No matches. If any line shows a real secret, **stop the plan, rotate that secret in the relevant console NOW**, then resume.

- [ ] **Step 2: Update `.gitignore`**

Edit `/Users/gcp/Projects/QuizPatenteB/.gitignore`. Current contents:
```
__pycache__/
.pytest_cache/
.venv/
frontend/node_modules/
# frontend/dist/ is committed so pip installs work without Node.js
.run/
*.png
*.egg-info/
user_data/
```
Append:
```
# Environment files: never commit. Real values live in /etc/quizpatenteb.env on prod.
.env
.env.*
!.env.example
backend/.env
backend/.env.*
!backend/.env.example
```

- [ ] **Step 3: Create the new repo-root `.env.example`**

Create `/Users/gcp/Projects/QuizPatenteB/.env.example`:
```
# QuizPatenteB environment variables.
# In production this file lives at /etc/quizpatenteb.env (chmod 600, root:azureuser).
# Loaded by deployment/systemd/quizpatenteb.service via EnvironmentFile=.
# Locally, copy to .env at the repo root for development.

# AI: local MLX model (Apple Silicon only). Ignored on the Linux production VM.
AI_MODEL=mlx-community/Qwen3.5-27B-4bit
BACKFILL_DEFINITIONS=false
BACKFILL_CHECKING=false

# Anthropic API key for Claude Haiku definitions. Required on the production VM.
# Per-key monthly cap MUST also be set in the Anthropic console.
ANTHROPIC_API_KEY=

# Soft monthly USD cap. App stops calling Anthropic when this is exceeded.
# Set this LOWER than the Anthropic console hard cap.
ANTHROPIC_MONTHLY_USD_CAP=10

# Authentication: 32+ random bytes hex-encoded. Generate with: python -c 'import secrets; print(secrets.token_hex(32))'
SESSION_SECRET=
# Pepper added to user-token hashes. Generate the same way. Rotating invalidates all tokens.
AUTH_TOKEN_PEPPER=

# CORS allow-list, comma-separated. Empty = same-origin only (production default).
# Dev example: CORS_ORIGINS=http://localhost:5183,http://127.0.0.1:5183
CORS_ORIGINS=
```

- [ ] **Step 4: Untrack the old env files (keep them on disk for local dev)**

Run:
```bash
cd /Users/gcp/Projects/QuizPatenteB
git rm --cached backend/.env backend/.env.example
ls backend/.env backend/.env.example  # both should still exist locally
```
Expected: Both files still exist on disk; `git status` shows them as deleted (staged).

- [ ] **Step 5: Verify the gitignore actually excludes them**

Run:
```bash
git check-ignore -v backend/.env backend/.env.example .env 2>&1
```
Expected output (each file matched against a rule):
```
.gitignore:NN:backend/.env	backend/.env
.gitignore:NN:backend/.env.example	backend/.env.example
.gitignore:NN:.env	.env
```
If any line is missing, the `.gitignore` rule is wrong — fix and re-run.

- [ ] **Step 6: Commit**

```bash
git add .gitignore .env.example
git commit -m "Untrack .env files and document required env vars

Move environment-file convention to repo root .env.example. Production
reads /etc/quizpatenteb.env via systemd; backend/.env stays for local dev
but is now gitignored so secrets cannot accidentally be committed."
```

---

## Task 2: Standardize on `os.environ` and consolidate the env file path (C2)

**Files:**
- Modify: `backend/app/main.py:853-855` and `backend/app/main.py:1255-1260` (remove `dotenv_values`)
- Modify: `deployment/systemd/quizpatenteb.service:10` (change `EnvironmentFile=` path)
- Modify: `docs/deployment.md` (document the new env file location)

**Context:** Two env-file readers exist today. systemd reads `/home/azureuser/quizpatenteb/.env` (project-root `.env`). `main.py:853-855` and `1255-1260` call `dotenv_values(Path(__file__).resolve().parents[1] / ".env")` which resolves to `backend/.env`. So `AI_MODEL`/`BACKFILL_*` come from `backend/.env` while `ANTHROPIC_API_KEY` comes from project-root `.env`. After this task, all env vars come from systemd (production) or a local `.env` loaded once at startup (dev).

- [ ] **Step 1: Replace the dotenv reader at `main.py:848-862`**

In `/Users/gcp/Projects/QuizPatenteB/backend/app/main.py`, find:
```python
def _load_ai_model() -> tuple[Any, Any]:
    """Load the local MLX model and tokenizer, cached after first call."""
    if "model" in _ai_model_cache:
        return _ai_model_cache["model"], _ai_model_cache["tokenizer"]

    from dotenv import dotenv_values
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    model_name = env.get("AI_MODEL", "mlx-community/Qwen3.5-27B-4bit")
```
Replace with:
```python
def _load_ai_model() -> tuple[Any, Any]:
    """Load the local MLX model and tokenizer, cached after first call."""
    if "model" in _ai_model_cache:
        return _ai_model_cache["model"], _ai_model_cache["tokenizer"]

    import os
    model_name = os.environ.get("AI_MODEL", "mlx-community/Qwen3.5-27B-4bit")
```

- [ ] **Step 2: Replace the dotenv reader at `main.py:1255-1260`**

Find:
```python
def _read_env_flags() -> tuple[bool, bool]:
    from dotenv import dotenv_values
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    backfill = env.get("BACKFILL_DEFINITIONS", "true").lower() not in ("false", "0", "no")
    checking = env.get("BACKFILL_CHECKING", "false").lower() not in ("false", "0", "no")
    return backfill, checking
```
Replace with:
```python
def _read_env_flags() -> tuple[bool, bool]:
    import os
    backfill = os.environ.get("BACKFILL_DEFINITIONS", "true").lower() not in ("false", "0", "no")
    checking = os.environ.get("BACKFILL_CHECKING", "false").lower() not in ("false", "0", "no")
    return backfill, checking
```

- [ ] **Step 3: Add a single dev-only `.env` loader at startup**

In `backend/app/main.py`, just after the existing `import threading` block (around line 14), add:
```python
import os as _os_for_env
from pathlib import Path as _Path_for_env
if _os_for_env.environ.get("QPB_LOAD_DOTENV", "1") == "1":
    try:
        from dotenv import load_dotenv
        # Repo-root .env for local development. Production injects via systemd.
        load_dotenv(_Path_for_env(__file__).resolve().parents[2] / ".env", override=False)
    except ImportError:
        pass
```
Production sets `QPB_LOAD_DOTENV=0` (Step 5 below) so the file is never read on the VM.

- [ ] **Step 4: Update the systemd unit**

Edit `/Users/gcp/Projects/QuizPatenteB/deployment/systemd/quizpatenteb.service`. Change line 10 from:
```
EnvironmentFile=-/home/azureuser/quizpatenteb/.env
```
To:
```
EnvironmentFile=/etc/quizpatenteb.env
Environment="QPB_LOAD_DOTENV=0"
```
The leading `-` is removed: the file MUST exist in production. The `QPB_LOAD_DOTENV=0` ensures the app never falls back to a repo-root `.env` even if one is created accidentally.

- [ ] **Step 5: Update `docs/deployment.md` env-setup steps**

Open `/Users/gcp/Projects/QuizPatenteB/docs/deployment.md`. Find the env-setup step (likely "Step 4: Configure environment" or similar). Replace it with:
```markdown
## Configure environment

1. Copy the template to the secured production location:
   ```bash
   sudo cp /home/azureuser/quizpatenteb/.env.example /etc/quizpatenteb.env
   sudo chown root:azureuser /etc/quizpatenteb.env
   sudo chmod 640 /etc/quizpatenteb.env
   ```
2. Edit `/etc/quizpatenteb.env`:
   - Set `ANTHROPIC_API_KEY=sk-ant-...`
   - Generate `SESSION_SECRET` and `AUTH_TOKEN_PEPPER` with
     `python3 -c 'import secrets; print(secrets.token_hex(32))'`
   - Set `ANTHROPIC_MONTHLY_USD_CAP` (default 10)
   - Leave `CORS_ORIGINS` empty (SPA is same-origin in prod)
3. Reload systemd: `sudo systemctl daemon-reload && sudo systemctl restart quizpatenteb`

This file is read by the systemd unit's `EnvironmentFile=` directive. Never store
production secrets inside the repo working tree. Setting a hard monthly cap in
the Anthropic console (Settings → Limits) is also required.
```

- [ ] **Step 6: Manual verification (local dev)**

Run:
```bash
cd /Users/gcp/Projects/QuizPatenteB
echo 'AI_MODEL=test-model-from-root-env' > .env
.venv/bin/python -c "
import sys; sys.path.insert(0, '.')
from backend.app import main
import os
print('AI_MODEL =', os.environ.get('AI_MODEL'))
"
rm .env
```
Expected: `AI_MODEL = test-model-from-root-env`. The repo-root `.env` is loaded only because `QPB_LOAD_DOTENV` is unset (defaults to `1`).

- [ ] **Step 7: Commit**

```bash
git add backend/app/main.py deployment/systemd/quizpatenteb.service docs/deployment.md
git commit -m "Consolidate env loading on os.environ + systemd EnvironmentFile

Remove inline dotenv_values calls in main.py. Single dev-only loader at
startup reads repo-root .env when QPB_LOAD_DOTENV=1 (default). Production
systemd unit sets QPB_LOAD_DOTENV=0 and reads /etc/quizpatenteb.env."
```

---

## Task 3: Restrict CORS to env-configured origins (C7)

**Files:**
- Modify: `backend/app/main.py:1389-1396`
- Test: `tests/test_cors.py` (created in Task 4 — the test for this lands then)

**Context:** Current `CORSMiddleware` hardcodes `http://localhost:5183` and `http://127.0.0.1:5183` with `allow_credentials=True` and `allow_methods=["*"]`. In production the SPA is served same-origin from nginx so CORS isn't needed at all; leaving it on widens attack surface. Read `CORS_ORIGINS` from env (comma-separated). Empty = no CORS middleware registered.

- [ ] **Step 1: Replace the CORSMiddleware setup**

In `/Users/gcp/Projects/QuizPatenteB/backend/app/main.py`, find lines 1389–1396:
```python
app = FastAPI(title="Quiz Patente B", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5183", "http://127.0.0.1:5183"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
Replace with:
```python
app = FastAPI(title="Quiz Patente B", lifespan=lifespan)

import os
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
```

- [ ] **Step 2: Document the local-dev convention**

Add to `backend/.env` (the local dev copy, NOT committed):
```
CORS_ORIGINS=http://localhost:5183,http://127.0.0.1:5183
```

- [ ] **Step 3: Manual verification**

Run two startup checks:
```bash
cd /Users/gcp/Projects/QuizPatenteB
# (a) production-style: no CORS_ORIGINS
unset CORS_ORIGINS
.venv/bin/uvicorn backend.app.main:app --port 8501 &
sleep 2
curl -s -i -X OPTIONS http://127.0.0.1:8501/api/health \
  -H 'Origin: http://evil.example' -H 'Access-Control-Request-Method: GET' \
  | grep -i 'access-control' || echo 'No CORS headers (correct)'
kill %1
# (b) dev-style: with CORS_ORIGINS
CORS_ORIGINS=http://localhost:5183 .venv/bin/uvicorn backend.app.main:app --port 8501 &
sleep 2
curl -s -i -X OPTIONS http://127.0.0.1:8501/api/health \
  -H 'Origin: http://localhost:5183' -H 'Access-Control-Request-Method: GET' \
  | grep -i 'access-control-allow-origin'
kill %1
```
Expected: (a) prints `No CORS headers (correct)`; (b) prints `access-control-allow-origin: http://localhost:5183`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py
git commit -m "Read CORS_ORIGINS from env; default to same-origin only

Production unsets CORS_ORIGINS so the middleware is not registered at all
(SPA is served same-origin via nginx). Dev sets CORS_ORIGINS to the Vite
dev-server URL."
```

---

## Task 4: Set up pytest infrastructure

**Files:**
- Create: `pytest.ini`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Modify: `pyproject.toml` (add test deps)

**Context:** No test infrastructure exists. Tasks 5–8 use TDD; we need pytest + httpx `TestClient` first. The app uses module-level state (`USER_DATA_DIR`, `VOCAB_BANK`); fixtures must isolate the user-data directory per test to avoid clobbering real user files.

- [ ] **Step 1: Create `pytest.ini`**

Create `/Users/gcp/Projects/QuizPatenteB/pytest.ini`:
```ini
[pytest]
testpaths = tests
addopts = -ra --strict-markers --tb=short
filterwarnings =
    error
    ignore::DeprecationWarning:dateutil
markers =
    integration: tests that hit the network or filesystem outside the temp dir
```

- [ ] **Step 2: Add test dependencies to `pyproject.toml`**

Open `/Users/gcp/Projects/QuizPatenteB/pyproject.toml`. After the `[project.optional-dependencies]` block, add:
```toml
[project.optional-dependencies.dev]
pytest = ">=8.0"
httpx = ">=0.27"
```
And install:
```bash
cd /Users/gcp/Projects/QuizPatenteB
.venv/bin/pip install -e '.[dev]'
```

- [ ] **Step 3: Create `tests/__init__.py`**

```bash
mkdir -p tests
```
Create empty file `/Users/gcp/Projects/QuizPatenteB/tests/__init__.py`:
```python
```

- [ ] **Step 4: Create `tests/conftest.py`**

Create `/Users/gcp/Projects/QuizPatenteB/tests/conftest.py`:
```python
"""Shared pytest fixtures for QuizPatenteB tests."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect USER_DATA_DIR to a temp dir and provide deterministic auth secrets.

    Every test gets a clean filesystem and a known SESSION_SECRET / AUTH_TOKEN_PEPPER.
    Network-touching env (ANTHROPIC_API_KEY) is cleared by default; tests that need
    it should set it explicitly.
    """
    user_data_dir = tmp_path / "user_data"
    user_data_dir.mkdir()
    monkeypatch.setenv("QPB_USER_DATA_DIR", str(user_data_dir))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-" + "0" * 56)
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", "test-pepper-" + "0" * 52)
    monkeypatch.setenv("QPB_LOAD_DOTENV", "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    yield user_data_dir


@pytest.fixture
def client(isolated_env: Path):
    """Return a FastAPI TestClient with the isolated env applied.

    Imports main inside the fixture so the module picks up the patched env vars.
    """
    # Force a fresh import so module-level USER_DATA_DIR is evaluated under the patch.
    import importlib
    import sys
    for mod in list(sys.modules):
        if mod.startswith("backend.app"):
            del sys.modules[mod]
    from fastapi.testclient import TestClient
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c
```

- [ ] **Step 5: Wire `QPB_USER_DATA_DIR` into `main.py`**

In `/Users/gcp/Projects/QuizPatenteB/backend/app/main.py`, find line 35:
```python
USER_DATA_DIR = ROOT_DIR / "user_data"
```
Replace with:
```python
import os as _os_user_data
USER_DATA_DIR = Path(_os_user_data.environ.get("QPB_USER_DATA_DIR") or (ROOT_DIR / "user_data"))
```

- [ ] **Step 6: Create the first sanity test**

Create `/Users/gcp/Projects/QuizPatenteB/tests/test_health.py`:
```python
def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 7: Run the test**

```bash
cd /Users/gcp/Projects/QuizPatenteB
.venv/bin/pytest tests/test_health.py -v
```
Expected: `1 passed`. If it fails because of MLX import errors during app startup, set `BACKFILL_DEFINITIONS=false` and `BACKFILL_CHECKING=false` in `conftest.py` `monkeypatch.setenv` calls.

- [ ] **Step 8: Commit**

```bash
git add pytest.ini pyproject.toml tests/
git commit -m "Add pytest infrastructure with isolated USER_DATA_DIR

Every test gets a clean tmp user_data dir, deterministic auth secrets,
and a fresh main.py import. First test exercises /api/health."
```

---

## Task 5: Add bearer-token authentication module (C3 — backend)

**Files:**
- Create: `backend/app/auth.py`
- Test: `tests/test_auth.py`

**Context:** The existing `get_current_user_email` (lines 396–399 of `main.py`) trusts a self-asserted `X-User-Email` header. We replace this with bearer-token validation. Tokens are UUID4 strings; only the SHA-256 hash (with `AUTH_TOKEN_PEPPER`) is persisted in the user registry. The plaintext token is shown to the user once at registration.

The user registry today is a list `[{"email": ..., "created": ...}, ...]` in `_users.json`. We add a `token_hash` field to each entry. Existing rows without `token_hash` become "unmigrated" — they cannot log in until the migration CLI in Task 9 fills in their hashes.

- [ ] **Step 1: Write failing tests for `hash_token` and `verify_token`**

Create `/Users/gcp/Projects/QuizPatenteB/tests/test_auth.py`:
```python
import pytest


def test_hash_token_is_deterministic_with_pepper(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", "fixed-pepper")
    from backend.app.auth import hash_token
    assert hash_token("abc") == hash_token("abc")
    assert len(hash_token("abc")) == 64  # sha256 hex


def test_hash_token_changes_with_pepper(monkeypatch):
    from backend.app.auth import hash_token
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", "pepper-A")
    a = hash_token("abc")
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", "pepper-B")
    b = hash_token("abc")
    assert a != b


def test_hash_token_requires_pepper(monkeypatch):
    monkeypatch.delenv("AUTH_TOKEN_PEPPER", raising=False)
    from backend.app.auth import hash_token
    with pytest.raises(RuntimeError, match="AUTH_TOKEN_PEPPER"):
        hash_token("abc")


def test_generate_token_returns_uuid4_hex(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", "p")
    from backend.app.auth import generate_token
    t1 = generate_token()
    t2 = generate_token()
    assert t1 != t2
    assert len(t1) == 32  # uuid4().hex
    assert all(c in "0123456789abcdef" for c in t1)


def test_verify_token_matches_hash(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", "p")
    from backend.app.auth import generate_token, hash_token, verify_token
    token = generate_token()
    h = hash_token(token)
    assert verify_token(token, h) is True
    assert verify_token("wrong-token", h) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/gcp/Projects/QuizPatenteB
.venv/bin/pytest tests/test_auth.py -v
```
Expected: All 5 tests fail with `ModuleNotFoundError: No module named 'backend.app.auth'`.

- [ ] **Step 3: Implement `auth.py`**

Create `/Users/gcp/Projects/QuizPatenteB/backend/app/auth.py`:
```python
"""Bearer-token authentication for QuizPatenteB.

Tokens are UUID4 hex strings. We store SHA-256(pepper || token) only.
The plaintext token is shown to the user once at registration, sent on every
request as `Authorization: Bearer <token>`, and never persisted server-side.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import uuid


def _pepper() -> bytes:
    pepper = os.environ.get("AUTH_TOKEN_PEPPER", "")
    if not pepper:
        raise RuntimeError(
            "AUTH_TOKEN_PEPPER is not set. Generate one with "
            "`python -c 'import secrets; print(secrets.token_hex(32))'` "
            "and add it to /etc/quizpatenteb.env."
        )
    return pepper.encode("utf-8")


def hash_token(token: str) -> str:
    """Return hex SHA-256 of (pepper || token). Raises if pepper is unset."""
    h = hashlib.sha256()
    h.update(_pepper())
    h.update(token.encode("utf-8"))
    return h.hexdigest()


def generate_token() -> str:
    """Return a fresh UUID4 hex token (32 chars, 128 bits of entropy)."""
    return uuid.uuid4().hex


def verify_token(token: str, expected_hash: str) -> bool:
    """Constant-time comparison of hash_token(token) and expected_hash."""
    return hmac.compare_digest(hash_token(token), expected_hash)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_auth.py -v
```
Expected: `5 passed`.

- [ ] **Step 5: Add a FastAPI dependency `require_user`**

Append to `/Users/gcp/Projects/QuizPatenteB/backend/app/auth.py`:
```python


from fastapi import Header, HTTPException


def parse_bearer(authorization: str | None) -> str | None:
    """Return the token from `Bearer <token>`, or None if absent/malformed."""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def require_user(authorization: str | None = Header(None)) -> str:
    """FastAPI dependency: validate bearer token, return the caller's email.

    Lazy-imports the user-registry loader from main to avoid a circular import.
    """
    token = parse_bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header.")

    # Lazy import to avoid circular dependency on backend.app.main at module load.
    from backend.app.main import load_user_registry

    expected_hash = hash_token(token)
    for user in load_user_registry():
        stored = user.get("token_hash")
        if stored and hmac.compare_digest(stored, expected_hash):
            return user["email"]
    raise HTTPException(status_code=401, detail="Invalid token.")
```

- [ ] **Step 6: Test the bearer parser and `require_user` (without main wired up yet)**

Append to `tests/test_auth.py`:
```python
def test_parse_bearer_strips_prefix():
    from backend.app.auth import parse_bearer
    assert parse_bearer("Bearer abc123") == "abc123"
    assert parse_bearer("bearer  abc  ") == "abc"  # case-insensitive, trims


def test_parse_bearer_rejects_malformed():
    from backend.app.auth import parse_bearer
    assert parse_bearer(None) is None
    assert parse_bearer("") is None
    assert parse_bearer("Token abc") is None
    assert parse_bearer("Bearer ") is None
```
Run:
```bash
.venv/bin/pytest tests/test_auth.py -v
```
Expected: `7 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/auth.py tests/test_auth.py
git commit -m "Add bearer-token auth module (hash, verify, FastAPI dependency)

Tokens are UUID4 hex; we store only sha256(pepper||token). The plaintext
is returned to the user once at registration and sent in Authorization:
Bearer headers thereafter."
```

---

## Task 6: Auth-gate user endpoints and return token at registration (C3 + C5)

**Files:**
- Modify: `backend/app/main.py:396-399` (replace `get_current_user_email`)
- Modify: `backend/app/main.py:1419-1460` (`/api/users` GET, POST, DELETE)
- Modify: `backend/app/main.py` (Pydantic models near top: add `token` to user-registration response)
- Test: `tests/test_user_endpoints.py`

**Context:** Today GET `/api/users` lists everyone, POST creates without auth, DELETE deletes any user without auth. After this task: GET requires admin token (a single env-configured admin email), POST returns the new token once, DELETE requires the caller's bearer token to belong to the email being deleted (or the admin).

`get_current_user_email` is the dependency used elsewhere in the app (e.g. `/api/score`, `/api/quiz/history`, `/api/vocab/tracking`). After this task it becomes a thin alias for `auth.require_user`.

- [ ] **Step 1: Write failing tests**

Create `/Users/gcp/Projects/QuizPatenteB/tests/test_user_endpoints.py`:
```python
def _register(client, email):
    r = client.post("/api/users", json={"email": email})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == email
    assert "token" in body and len(body["token"]) == 32
    return body["token"]


def test_post_users_returns_token_once(client):
    token = _register(client, "alice@example.com")
    # Second registration of same email returns 409 and no token
    r = client.post("/api/users", json={"email": "alice@example.com"})
    assert r.status_code == 409


def test_get_users_requires_admin_token(client):
    _register(client, "alice@example.com")
    # No auth header
    assert client.get("/api/users").status_code == 401
    # Non-admin token
    bob_token = _register(client, "bob@example.com")
    r = client.get("/api/users", headers={"Authorization": f"Bearer {bob_token}"})
    assert r.status_code == 403


def test_delete_user_requires_self_token(client):
    alice_token = _register(client, "alice@example.com")
    bob_token = _register(client, "bob@example.com")
    # Bob tries to delete Alice
    r = client.delete(
        "/api/users/alice@example.com",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert r.status_code == 403
    # Alice deletes herself: ok
    r = client.delete(
        "/api/users/alice@example.com",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert r.status_code == 200


def test_user_scoped_endpoint_requires_token(client):
    # /api/quiz/history is user-scoped
    assert client.get("/api/quiz/history").status_code == 401
    token = _register(client, "alice@example.com")
    r = client.get("/api/quiz/history", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/gcp/Projects/QuizPatenteB
.venv/bin/pytest tests/test_user_endpoints.py -v
```
Expected: 4 failures (currently `/api/users` POST does not return `token`, GET works without auth, DELETE works without auth, `/api/quiz/history` reads `X-User-Email` instead of `Authorization`).

- [ ] **Step 3: Update Pydantic models to expose `token` on registration response**

In `/Users/gcp/Projects/QuizPatenteB/backend/app/main.py`, find the existing `UserOut` model (search `class UserOut`). Replace its definition with:
```python
class UserOut(BaseModel):
    email: str
    created: str


class UserCreatedOut(BaseModel):
    """Returned only at registration. Contains the plaintext token shown once."""
    email: str
    created: str
    token: str
```

- [ ] **Step 4: Replace `get_current_user_email`**

In `main.py`, find lines 396–399:
```python
def get_current_user_email(x_user_email: str | None = Header(None)) -> str:
    if not x_user_email:
        raise HTTPException(status_code=400, detail="X-User-Email header is required.")
    return x_user_email.strip().lower()
```
Replace with:
```python
def get_current_user_email(authorization: str | None = Header(None)) -> str:
    """Authenticate the caller via Authorization: Bearer <token>.

    Replaces the legacy X-User-Email header which trusted clients to self-assert.
    """
    from backend.app.auth import require_user
    return require_user(authorization)
```

- [ ] **Step 5: Add `require_admin` dependency**

In `main.py`, just below `get_current_user_email`, add:
```python
def get_admin_email() -> str:
    admin = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    if not admin:
        raise HTTPException(status_code=503, detail="Admin endpoint disabled (ADMIN_EMAIL unset).")
    return admin


def require_admin(caller_email: str = Depends(get_current_user_email)) -> str:
    if caller_email != get_admin_email():
        raise HTTPException(status_code=403, detail="Admin access required.")
    return caller_email
```
Add at the top of the file with other imports (around line 24):
```python
import os
```
(may already be present — check before adding).

- [ ] **Step 6: Update `POST /api/users` to issue a token**

Find lines 1425–1441 in `main.py` and replace with:
```python
@app.post("/api/users", status_code=201, response_model=UserCreatedOut)
async def create_user(body: UserCreateIn) -> UserCreatedOut:
    from backend.app.auth import generate_token, hash_token

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

    return UserCreatedOut(email=email, created=created, token=token)
```

- [ ] **Step 7: Auth-gate `GET /api/users` (admin only) and `DELETE /api/users/{email}` (self only)**

Find lines 1419–1422:
```python
@app.get("/api/users")
async def list_users() -> list[UserOut]:
    users = load_user_registry()
    return [UserOut(email=u["email"], created=u["created"]) for u in users]
```
Replace with:
```python
@app.get("/api/users")
async def list_users(_admin: str = Depends(require_admin)) -> list[UserOut]:
    users = load_user_registry()
    return [UserOut(email=u["email"], created=u["created"]) for u in users]
```

Find lines 1444–1460 and replace `delete_user` with:
```python
@app.delete("/api/users/{email}")
async def delete_user(
    email: str,
    caller: str = Depends(get_current_user_email),
) -> dict[str, str]:
    email = email.strip().lower()
    is_admin = (caller == os.environ.get("ADMIN_EMAIL", "").strip().lower()
                and bool(os.environ.get("ADMIN_EMAIL", "").strip()))
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
```

- [ ] **Step 8: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_user_endpoints.py tests/test_auth.py tests/test_health.py -v
```
Expected: `12 passed` (5 + 2 + 4 + 1).

- [ ] **Step 9: Commit**

```bash
git add backend/app/main.py backend/app/auth.py tests/test_user_endpoints.py
git commit -m "Auth-gate user endpoints; return token once at registration

POST /api/users now issues a UUID4 bearer token (shown once). GET /api/users
requires ADMIN_EMAIL. DELETE /api/users/{email} requires the caller's token
to match the target email (or be the admin). All previously self-asserted
X-User-Email endpoints now require Authorization: Bearer."
```

---

## Task 7: Update the React SPA to use bearer tokens (C3 — frontend)

**Files:**
- Modify: `frontend/src/App.jsx` (auth state, API call wrapper, login/registration UI)
- Modify: any other frontend file that issues `fetch('/api/...')` with `X-User-Email` (search and replace)

**Context:** The SPA currently sends `X-User-Email` on every API call. We replace that with `Authorization: Bearer <token>` from `localStorage`. Registration response now includes a `token` field that must be persisted. On 401, the SPA must clear the token and show the login screen.

- [ ] **Step 1: Find every `X-User-Email` use in the frontend**

```bash
cd /Users/gcp/Projects/QuizPatenteB
grep -rn 'X-User-Email\|x-user-email' frontend/src
```
List every file/line. Each one needs the same replacement.

- [ ] **Step 2: Add a centralized fetch helper**

Create `/Users/gcp/Projects/QuizPatenteB/frontend/src/api.js` (if it does not already exist; otherwise modify):
```javascript
const TOKEN_KEY = "qpb_auth_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export async function apiFetch(path, options = {}) {
  const token = getToken();
  const headers = new Headers(options.headers || {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    setToken(null);
    window.dispatchEvent(new CustomEvent("qpb-auth-required"));
  }
  return response;
}
```

- [ ] **Step 3: Replace every direct `fetch('/api/...')` call in the SPA with `apiFetch`**

For each file from Step 1, replace:
```javascript
fetch('/api/whatever', { headers: { 'X-User-Email': email } })
```
with:
```javascript
import { apiFetch } from './api';
apiFetch('/api/whatever')
```
And remove every `X-User-Email` header set anywhere in the codebase.

- [ ] **Step 4: Update registration UI to capture and store the token**

In `frontend/src/App.jsx`, find the user-creation flow (component that POSTs to `/api/users`). After receiving a successful response, extract `body.token` and call `setToken(body.token)`. Show the token to the user once with a "save this somewhere safe — you cannot retrieve it again" notice.

Pseudocode (adapt to actual JSX):
```jsx
import { setToken } from './api';

async function handleRegister(email) {
  const res = await fetch('/api/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  });
  if (!res.ok) throw new Error(await res.text());
  const body = await res.json();
  setToken(body.token);
  setRegistrationToken(body.token);  // local component state to display
}
```

- [ ] **Step 5: Add a login screen for users with an existing token**

Existing users (who registered before this change) need a way to enter their migration token (Task 9 will mint these). Add an "I already have an account" form: takes email + token, validates by calling `apiFetch('/api/auth/whoami')` (a new endpoint — see Step 6), and on success calls `setToken(token)`.

- [ ] **Step 6: Add a `/api/auth/whoami` endpoint to backend**

In `/Users/gcp/Projects/QuizPatenteB/backend/app/main.py`, near the other `/api/users` routes:
```python
@app.get("/api/auth/whoami")
async def whoami(email: str = Depends(get_current_user_email)) -> dict[str, str]:
    return {"email": email}
```

- [ ] **Step 7: Listen for `qpb-auth-required` events to redirect to login**

In `App.jsx`:
```jsx
useEffect(() => {
  const handler = () => setRequireLogin(true);
  window.addEventListener('qpb-auth-required', handler);
  return () => window.removeEventListener('qpb-auth-required', handler);
}, []);
```

- [ ] **Step 8: Build and manually verify**

```bash
cd /Users/gcp/Projects/QuizPatenteB/frontend
npm install
npm run build
cd ..
# Run backend with dev settings:
SESSION_SECRET=$(python -c 'import secrets; print(secrets.token_hex(32))') \
AUTH_TOKEN_PEPPER=$(python -c 'import secrets; print(secrets.token_hex(32))') \
ADMIN_EMAIL=admin@example.com \
QPB_LOAD_DOTENV=0 \
.venv/bin/uvicorn backend.app.main:app --port 8500 &
# Open the SPA, register a new user, confirm token shown, verify quiz history works.
```
Expected: registration shows the token, subsequent API calls succeed, refreshing the page keeps you logged in (token in localStorage), clearing localStorage shows the login screen.

- [ ] **Step 9: Commit**

```bash
git add frontend/src backend/app/main.py
git commit -m "SPA: send Authorization: Bearer header; add login/register flows

Centralized apiFetch wrapper handles token retrieval and 401 redirects.
Backend exposes /api/auth/whoami for token validation by the login form."
```

---

## Task 8: Add `slowapi` rate limiting on Anthropic-touching endpoints (C4 — app layer)

**Files:**
- Create: `backend/app/rate_limit.py`
- Modify: `backend/app/main.py` (wire limiter, decorate hot endpoints, cap prefetch list)
- Modify: `pyproject.toml` (add `slowapi`)
- Test: `tests/test_rate_limit.py`

**Context:** `/api/vocab/translate`, `/api/questions/{id}/translation`, `/api/vocab/prefetch` can each cause a paid Claude call or a third-party API hit. With auth in place we can rate-limit per-user; for unauthenticated endpoints (e.g. `/api/quiz`) we limit per-IP. `nginx`-layer limiting is added in Task 11.

`slowapi`'s rate-limit key function must read `X-Forwarded-For` because nginx is the apparent client. Authenticated endpoints key on the bearer token's email, falling back to remote IP.

- [ ] **Step 1: Add `slowapi` to dependencies**

Edit `/Users/gcp/Projects/QuizPatenteB/pyproject.toml`. In the `dependencies` array under `[project]`, add `"slowapi>=0.1.9"`. Then:
```bash
.venv/bin/pip install -e .
```

- [ ] **Step 2: Write failing test**

Create `/Users/gcp/Projects/QuizPatenteB/tests/test_rate_limit.py`:
```python
def test_vocab_translate_rate_limited(client):
    r = client.post("/api/users", json={"email": "alice@example.com"})
    token = r.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Pick a real word from the vocabulary so the endpoint doesn't 404.
    # The limit for /api/vocab/translate is 10/minute per user.
    statuses = []
    for _ in range(12):
        # Use a known good word from VOCAB_BY_WORD; at minimum the response
        # should be 200/404/502 — but never 200 then suddenly 429.
        resp = client.get("/api/vocab/translate?word=galleria", headers=headers)
        statuses.append(resp.status_code)
    # The 11th and 12th calls in the same minute must be 429.
    assert 429 in statuses, f"expected at least one 429, got {statuses}"


def test_unauthed_endpoint_rate_limited(client):
    statuses = [client.get("/api/quiz?count=1").status_code for _ in range(35)]
    assert 429 in statuses, f"expected at least one 429, got {statuses}"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_rate_limit.py -v
```
Expected: 2 failures with no 429 anywhere.

- [ ] **Step 4: Create `backend/app/rate_limit.py`**

```python
"""slowapi limiter setup. Keys requests by authenticated email if available,
otherwise by client IP (X-Forwarded-For aware)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

if TYPE_CHECKING:
    from starlette.requests import Request


def _key(request: "Request") -> str:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        # Use the token (not the email) as the key so we don't leak identity into
        # the limiter store, and unauthenticated repeat-attackers can't pre-claim
        # an authed quota.
        return f"bearer:{auth.split(None, 1)[1].strip()}"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=_key, default_limits=["60/minute"], headers_enabled=True)


__all__ = ["limiter", "RateLimitExceeded"]
```

- [ ] **Step 5: Wire limiter into FastAPI app**

In `/Users/gcp/Projects/QuizPatenteB/backend/app/main.py`, after the `app = FastAPI(...)` line (after Task 3 changes, around line 1389):
```python
from backend.app.rate_limit import limiter, RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi import _rate_limit_exceeded_handler

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
```

- [ ] **Step 6: Apply per-endpoint limits**

Decorate the following endpoints (replace the existing `@app.get(...)` line with the decorator stack):

`/api/vocab/translate` (line 1645):
```python
@app.get("/api/vocab/translate", response_model=VocabTranslationResponse)
@limiter.limit("10/minute")
async def translate_vocab_word(
    request: Request,
    word: str = Query(min_length=1),
) -> VocabTranslationResponse:
    # body unchanged
```
Note: `slowapi` requires the function to accept a `Request` parameter named `request`. Add `from starlette.requests import Request` to imports.

`/api/questions/{id}/translation` (line 1558):
```python
@app.get("/api/questions/{question_id}/translation", response_model=TranslationResponse)
@limiter.limit("10/minute")
async def get_question_translation(
    request: Request,
    question_id: int,
) -> TranslationResponse:
    # body unchanged
```

`/api/vocab/prefetch` (line 1758) — apply 5/minute and cap the list size:
```python
@app.post("/api/vocab/prefetch", response_model=VocabPrefetchResponse)
@limiter.limit("5/minute")
async def prefetch_vocab(
    request: Request,
    body: VocabPrefetchRequest,
) -> VocabPrefetchResponse:
    if len(body.words) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 words per request.")
    # rest of body unchanged
```

`/api/quiz` (line 1540) — broad endpoint, 30/minute:
```python
@app.get("/api/quiz", response_model=QuizResponse)
@limiter.limit("30/minute")
async def get_quiz(request: Request, count: int = Query(10, ge=1, le=100)) -> QuizResponse:
    # body unchanged
```

- [ ] **Step 7: Cap `VocabPrefetchRequest.words` at the schema layer too**

Find `class VocabPrefetchRequest` in `main.py`. Update:
```python
from pydantic import Field
class VocabPrefetchRequest(BaseModel):
    words: list[str] = Field(default_factory=list, max_length=50)
```

- [ ] **Step 8: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_rate_limit.py -v
```
Expected: `2 passed`.

- [ ] **Step 9: Run all tests**

```bash
.venv/bin/pytest -v
```
Expected: all green. Fix any regressions before committing.

- [ ] **Step 10: Commit**

```bash
git add backend/app/main.py backend/app/rate_limit.py pyproject.toml tests/test_rate_limit.py
git commit -m "Rate-limit AI-touching endpoints with slowapi

10/min on /api/vocab/translate and /api/questions/{id}/translation,
5/min and 50-word cap on /api/vocab/prefetch, 30/min on /api/quiz.
Limiter keys on bearer token (authed) or X-Forwarded-For (unauthed)."
```

---

## Task 9: One-shot user-token migration CLI

**Files:**
- Create: `backend/scripts/__init__.py` (empty)
- Create: `backend/scripts/mint_user_tokens.py`
- Modify: `docs/deployment.md` (add migration step)

**Context:** After Task 6 lands in production, every entry in `_users.json` without a `token_hash` becomes unable to log in. This CLI mints a token for each such entry, persists the hash, and prints `email\ttoken` to stdout. The operator distributes tokens out-of-band (email, SMS, paper) to existing users, who then enter the token in the SPA's login form (Task 7 step 5).

- [ ] **Step 1: Create the CLI**

Create `/Users/gcp/Projects/QuizPatenteB/backend/scripts/__init__.py` (empty file).

Create `/Users/gcp/Projects/QuizPatenteB/backend/scripts/mint_user_tokens.py`:
```python
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
import sys
from pathlib import Path


def main() -> int:
    import os
    from backend.app.auth import generate_token, hash_token

    user_data_dir = Path(os.environ["QPB_USER_DATA_DIR"])
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
        # Atomic write: rename a tmp file over the registry.
        tmp = registry_path.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump(users, f, indent=2)
        tmp.replace(registry_path)

    print(f"Minted {minted} new tokens.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Test the CLI locally**

```bash
cd /Users/gcp/Projects/QuizPatenteB
mkdir -p /tmp/qpb-test-data
cat > /tmp/qpb-test-data/_users.json <<'EOF'
[{"email":"alice@example.com","created":"2026-01-01T00:00:00+00:00"},
 {"email":"bob@example.com","created":"2026-01-02T00:00:00+00:00","token_hash":"already-set"}]
EOF
QPB_LOAD_DOTENV=0 \
AUTH_TOKEN_PEPPER=test-pepper \
QPB_USER_DATA_DIR=/tmp/qpb-test-data \
.venv/bin/python -m backend.scripts.mint_user_tokens
cat /tmp/qpb-test-data/_users.json
```
Expected: stdout shows `alice@example.com\t<32-hex-token>`. The registry file now has a `token_hash` for alice; bob's existing `token_hash` is preserved. Re-running prints nothing (idempotent).

- [ ] **Step 3: Document the migration in `docs/deployment.md`**

Append to `/Users/gcp/Projects/QuizPatenteB/docs/deployment.md`:
```markdown
## Migrating existing users to bearer-token auth (one-time, after C3 deploy)

After the auth update is deployed, existing users in `user_data/_users.json`
need bearer tokens. SSH to the production VM and run:

```bash
sudo -u azureuser bash -c '
  set -a
  source /etc/quizpatenteb.env
  set +a
  cd /home/azureuser/quizpatenteb
  QPB_USER_DATA_DIR=/home/azureuser/quizpatenteb/user_data \
    QPB_LOAD_DOTENV=0 \
    .venv/bin/python -m backend.scripts.mint_user_tokens > /tmp/qpb-tokens.tsv
'
```

The output `/tmp/qpb-tokens.tsv` contains `email<TAB>token` rows. Distribute
each token to its user out-of-band. Then **delete the file**:
```bash
shred -u /tmp/qpb-tokens.tsv
```

Users log in via the SPA's "I already have an account" form by entering
their email and token. The token is then stored in localStorage on their device.
```

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/ docs/deployment.md
git commit -m "Add one-shot CLI to mint bearer tokens for pre-auth users

Idempotent: only fills in token_hash for entries that don't have one.
Prints plaintext token once to stdout for out-of-band distribution."
```

---

## Task 10: Anthropic spend monitoring (C6)

**Files:**
- Create: `backend/app/spend.py`
- Modify: `backend/app/main.py` (wire spend tracking into `_get_claude_definition`)
- Test: `tests/test_spend.py`

**Context:** Even with rate limiting, a misconfigured background worker or a slow attack drip can rack up Claude costs. We add (1) per-call structured logs (token counts) so journald-grep alerts work, (2) an in-memory monthly tally that short-circuits API calls when over `ANTHROPIC_MONTHLY_USD_CAP`, (3) deployment doc for setting a hard cap in the Anthropic console. Hard cap is enforced by Anthropic; soft cap is the app's belt-and-suspenders.

The pricing for `claude-haiku-4-5-20251001` (per Anthropic docs at the cutoff): $1 / 1M input tokens, $5 / 1M output tokens. Hardcode these as constants; revise if pricing changes.

- [ ] **Step 1: Write failing test**

Create `/Users/gcp/Projects/QuizPatenteB/tests/test_spend.py`:
```python
def test_record_call_increments_monthly_total(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MONTHLY_USD_CAP", "1.00")
    from backend.app import spend
    spend.reset_for_test()

    spend.record_claude_call(input_tokens=1_000_000, output_tokens=0)  # = $1.00 input
    assert spend.is_over_cap() is True


def test_under_cap_allows_calls(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MONTHLY_USD_CAP", "10.00")
    from backend.app import spend
    spend.reset_for_test()

    spend.record_claude_call(input_tokens=1000, output_tokens=500)
    assert spend.is_over_cap() is False
    assert spend.month_total_usd() < 0.01


def test_no_cap_set_means_unlimited(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MONTHLY_USD_CAP", raising=False)
    from backend.app import spend
    spend.reset_for_test()
    spend.record_claude_call(input_tokens=10**9, output_tokens=10**9)
    assert spend.is_over_cap() is False
```

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_spend.py -v
```
Expected: 3 errors `ModuleNotFoundError: backend.app.spend`.

- [ ] **Step 3: Implement `spend.py`**

Create `/Users/gcp/Projects/QuizPatenteB/backend/app/spend.py`:
```python
"""Anthropic cost telemetry and soft monthly cap.

Pricing for claude-haiku-4-5 (USD per 1M tokens): $1 input, $5 output.
Update CLAUDE_HAIKU_INPUT_USD_PER_TOKEN / _OUTPUT_USD_PER_TOKEN if pricing changes.

We track an in-memory total per (year, month). Restarts reset to zero — if you need
durable accounting, mirror to disk. Hard-cap enforcement remains Anthropic's job
(set the per-key monthly cap in the Anthropic console).
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger("uvicorn.error")

CLAUDE_HAIKU_INPUT_USD_PER_TOKEN = 1.0 / 1_000_000
CLAUDE_HAIKU_OUTPUT_USD_PER_TOKEN = 5.0 / 1_000_000

_lock = threading.Lock()
_state: dict[str, float] = {"month_key": "", "total_usd": 0.0}


def _current_month_key() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def reset_for_test() -> None:
    with _lock:
        _state["month_key"] = _current_month_key()
        _state["total_usd"] = 0.0


def record_claude_call(input_tokens: int, output_tokens: int, model: str = "claude-haiku-4-5") -> float:
    """Add the call's USD cost to the monthly total, log it, and return the cost."""
    cost = (
        input_tokens * CLAUDE_HAIKU_INPUT_USD_PER_TOKEN
        + output_tokens * CLAUDE_HAIKU_OUTPUT_USD_PER_TOKEN
    )
    key = _current_month_key()
    with _lock:
        if _state["month_key"] != key:
            _state["month_key"] = key
            _state["total_usd"] = 0.0
        _state["total_usd"] += cost
        running_total = _state["total_usd"]

    logger.info(
        "ANTHROPIC_CALL model=%s in=%d out=%d cost_usd=%.6f month=%s month_total_usd=%.4f",
        model, input_tokens, output_tokens, cost, key, running_total,
    )
    return cost


def month_total_usd() -> float:
    with _lock:
        if _state["month_key"] != _current_month_key():
            return 0.0
        return _state["total_usd"]


def is_over_cap() -> bool:
    cap_str = os.environ.get("ANTHROPIC_MONTHLY_USD_CAP", "").strip()
    if not cap_str:
        return False
    try:
        cap = float(cap_str)
    except ValueError:
        logger.warning("ANTHROPIC_MONTHLY_USD_CAP=%r is not a number; ignoring.", cap_str)
        return False
    return month_total_usd() >= cap
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_spend.py -v
```
Expected: `3 passed`.

- [ ] **Step 5: Wire into `_get_claude_definition`**

In `/Users/gcp/Projects/QuizPatenteB/backend/app/main.py`, find `_get_claude_definition` (lines 888–907). Replace with:
```python
def _get_claude_definition(word: str) -> str | None:
    """Get a definition using the Claude API as fallback when MLX is unavailable."""
    import os
    from backend.app import spend

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
```

- [ ] **Step 6: Document the Anthropic console hard cap**

Append to `docs/deployment.md`:
```markdown
## Anthropic monthly hard cap (REQUIRED)

The application enforces a *soft* cap via `ANTHROPIC_MONTHLY_USD_CAP` in
`/etc/quizpatenteb.env` — it stops calling Claude when the in-memory monthly
total exceeds the cap. **A soft cap alone is not enough**: a worker restart
resets the counter, and a malformed env value silently disables it.

Set a *hard* cap in the Anthropic console too:
1. https://console.anthropic.com/settings/limits
2. Under "Spend limits", set a monthly USD cap on the QuizPatenteB API key.
3. Set the soft cap (`ANTHROPIC_MONTHLY_USD_CAP`) to ~50% of the hard cap so
   the app stops voluntarily before Anthropic forces a 429.

Use a dedicated API key for QuizPatenteB so rotation does not affect the
RePortfolio or OpenSesame backends sharing the same VM.
```

- [ ] **Step 7: Run all tests**

```bash
.venv/bin/pytest -v
```
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add backend/app/spend.py backend/app/main.py docs/deployment.md tests/test_spend.py
git commit -m "Track Anthropic spend; soft monthly cap stops Claude calls

Per-call structured log line ANTHROPIC_CALL ... month_total_usd=$X enables
journalctl-grep alerting. ANTHROPIC_MONTHLY_USD_CAP env var enforces a soft
cap; deployment doc requires a matching hard cap in the Anthropic console."
```

---

## Task 11: nginx-layer rate limiting (C4 — defense in depth)

**Files:**
- Modify: `deployment/nginx/patenteb.conf`
- Create: `deployment/nginx/limit_req_zones.conf` (snippet for `http {}` context)
- Modify: `docs/deployment.md`

**Context:** App-layer rate limiting in Task 8 protects the Python process; nginx-layer protects the OS from connection-flood attacks that never reach the app. nginx `limit_req_zone` lives in the `http {}` context (typically `/etc/nginx/nginx.conf`), and `limit_req` lives in `server {}` or `location {}`. We supply a snippet to be `include`d.

- [ ] **Step 1: Add the zone-definition include**

Create `/Users/gcp/Projects/QuizPatenteB/deployment/nginx/limit_req_zones.conf`:
```nginx
# Place at /etc/nginx/conf.d/qpb-zones.conf (loaded inside http {}).
# Both zones are 10MB each, holding ~160k unique IPs.
limit_req_zone $binary_remote_addr zone=qpb_api:10m rate=30r/m;
limit_req_zone $binary_remote_addr zone=qpb_ai:10m  rate=10r/m;
```

- [ ] **Step 2: Update `patenteb.conf` to apply limits**

Edit `/Users/gcp/Projects/QuizPatenteB/deployment/nginx/patenteb.conf`. Replace the existing `location /api/ { ... }` block (lines 56–68) with:
```nginx
    # AI / Anthropic-touching endpoints — 10 r/m, brief burst
    location ~ ^/api/(vocab/translate|questions/[0-9]+/translation|vocab/prefetch) {
        limit_req zone=qpb_ai burst=5 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:8500;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_read_timeout 120;
        proxy_connect_timeout 60;
        proxy_send_timeout 60;
    }

    # General API — 30 r/m, larger burst
    location /api/ {
        limit_req zone=qpb_api burst=20 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:8500;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_read_timeout 120;
        proxy_connect_timeout 60;
        proxy_send_timeout 60;
    }
```

- [ ] **Step 3: Document deployment of the zones include**

Append to `docs/deployment.md`:
```markdown
## nginx rate-limit zones

Copy the zone-definition snippet to nginx's conf.d (loaded inside http {}):

```bash
sudo cp /home/azureuser/quizpatenteb/deployment/nginx/limit_req_zones.conf \
        /etc/nginx/conf.d/qpb-zones.conf
sudo cp /home/azureuser/quizpatenteb/deployment/nginx/patenteb.conf \
        /etc/nginx/sites-available/patenteb
sudo nginx -t  # MUST succeed before reload
sudo systemctl reload nginx
```

Verify limiting works from an off-server host:
```bash
for i in $(seq 1 40); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    https://patenteb.eventhorizon.llc/api/health
done | sort | uniq -c
```
Expected: a mix of `200` and `429`. The `429` rows confirm nginx-layer
limiting is active.
```

- [ ] **Step 4: Manual verification (production VM only)**

This step runs on the production VM after the nginx config is deployed.

```bash
ssh -i ~/.ssh/ReportFolio_key.pem azureuser@172.173.116.28 \
  'sudo nginx -t && sudo systemctl reload nginx'
# Then from your laptop:
for i in $(seq 1 40); do
  curl -s -o /dev/null -w "%{http_code}\n" https://patenteb.eventhorizon.llc/api/health
done | sort | uniq -c
```
Expected: at least one `429`.

- [ ] **Step 5: Commit**

```bash
git add deployment/nginx/ docs/deployment.md
git commit -m "Add nginx limit_req zones for /api/ and AI endpoints

Defense in depth on top of slowapi: 30 r/m on /api/, 10 r/m on AI-touching
endpoints. Zone snippet documented for inclusion in /etc/nginx/conf.d/."
```

---

## Self-Review Checklist

Run this checklist against the plan after writing it:

- [ ] **Spec coverage:** Every C1–C7 critical item has at least one task.
  - C1 (untrack `.env`) → Task 1
  - C2 (single env path, `os.environ`) → Task 2
  - C3 (real auth) → Tasks 5, 6, 7, 9 (backend + SPA + migration)
  - C4 (rate-limit Anthropic endpoints) → Tasks 8 (app), 11 (nginx)
  - C5 (auth-gate `/api/users`) → Task 6 (folded in)
  - C6 (Anthropic spend monitoring) → Task 10
  - C7 (CORS) → Task 3

- [ ] **No placeholders:** Every step has actual file paths, line numbers, code blocks, and expected output. No "TBD", "implement later", or "similar to Task N".

- [ ] **Type/name consistency:**
  - `hash_token`, `verify_token`, `generate_token`, `parse_bearer`, `require_user` defined in Task 5; used unchanged in Tasks 6, 9.
  - `UserCreatedOut.token` field defined in Task 6 step 3; consumed by Task 7 step 4.
  - `apiFetch` function defined in Task 7 step 2; used in step 3.
  - `record_claude_call`, `is_over_cap`, `month_total_usd`, `reset_for_test` defined in Task 10 step 3; used in step 5.
  - `limiter`, `RateLimitExceeded` exported from `backend.app.rate_limit` (Task 8 step 4); imported in step 5.
  - Env var `QPB_USER_DATA_DIR` introduced in Task 4 step 4; honored by `main.py:35` change in same step; used by Task 9.
  - Env var `QPB_LOAD_DOTENV` introduced in Task 2 step 3; set to `0` in Task 2 step 4 (systemd) and Task 4 step 4 (tests).

- [ ] **Test coverage matches behavior changes:** auth (Task 5), user endpoints (Task 6), rate limit (Task 8), spend (Task 10). CORS (Task 3) and env path (Task 2) have manual verification steps because they are config-only.

- [ ] **Deployment ordering:** Tasks 1–4 are individually safe to deploy. Tasks 5–8 must deploy together (auth + SPA changes). Task 9 runs once on production after Tasks 5–8 deploy. Tasks 10 and 11 are independently deployable. The plan calls this out explicitly at the top.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-28-critical-security-hardening.md`.

This plan covers the seven CRITICAL items (C1–C7) from the security review. The HIGH-tier items (NSG, SSH hardening, TLS audit, backups, dependency scanning, etc.) are intentionally out of scope and belong in a follow-up plan.

Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best for this plan because Tasks 5–8 are large enough that a focused agent per task will produce cleaner work.

2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints. Faster for the small config-only Tasks 1–3.

Which approach?
