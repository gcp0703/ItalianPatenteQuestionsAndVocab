#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$ROOT_DIR/.run"

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8500}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-5183}"

BACKEND_PID_FILE="$RUNTIME_DIR/backend.pid"
FRONTEND_PID_FILE="$RUNTIME_DIR/frontend.pid"
BACKEND_LOG_FILE="$RUNTIME_DIR/backend.log"
FRONTEND_LOG_FILE="$RUNTIME_DIR/frontend.log"

mkdir -p "$RUNTIME_DIR"

find_launcher_python() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi

  if command -v python >/dev/null 2>&1; then
    command -v python
    return
  fi

  printf 'Python launcher was not found.\n' >&2
  exit 1
}

find_python_bin() {
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    printf '%s\n' "$ROOT_DIR/.venv/bin/python"
    return
  fi

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi

  if command -v python >/dev/null 2>&1; then
    command -v python
    return
  fi

  printf 'Python was not found. Create a virtualenv or install Python 3.\n' >&2
  exit 1
}

wait_for_shutdown() {
  local pid="$1"

  for _ in {1..20}; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done

  return 1
}

wait_for_listener() {
  local port="$1"

  for _ in {1..40}; do
    if lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done

  return 1
}

stop_pid_file_process() {
  local name="$1"
  local pid_file="$2"

  if [[ ! -f "$pid_file" ]]; then
    return
  fi

  local pid
  pid="$(<"$pid_file")"

  if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
    printf 'Stopping %s (pid %s)\n' "$name" "$pid"
    kill "$pid" >/dev/null 2>&1 || true
    if ! wait_for_shutdown "$pid"; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
  fi

  rm -f "$pid_file"
}

stop_port_processes() {
  local name="$1"
  local port="$2"
  local pids=""

  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    return
  fi

  printf 'Stopping %s listener on port %s\n' "$name" "$port"
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    kill "$pid" >/dev/null 2>&1 || true
    if ! wait_for_shutdown "$pid"; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
  done <<< "$pids"
}

spawn_detached() {
  local cwd="$1"
  local log_file="$2"
  shift 2

  local launcher_python
  launcher_python="$(find_launcher_python)"

  "$launcher_python" -c '
import os
import subprocess
import sys

cwd, log_file, *cmd = sys.argv[1:]
with open(log_file, "ab", buffering=0) as log_handle:
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

print(process.pid)
' "$cwd" "$log_file" "$@"
}

ensure_frontend_deps() {
  if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
    printf 'Missing frontend dependencies. Run `cd frontend && npm install` first.\n' >&2
    exit 1
  fi
}

ensure_backend_deps() {
  local python_bin="$1"
  if ! "$python_bin" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
    printf 'Missing backend dependencies. Run `pip install -r requirements.txt` first.\n' >&2
    exit 1
  fi
}

start_backend() {
  local python_bin="$1"
  local pid

  cd "$ROOT_DIR"
  : > "$BACKEND_LOG_FILE"

  pid="$(spawn_detached "$ROOT_DIR" "$BACKEND_LOG_FILE" \
    "$python_bin" -m uvicorn backend.app.main:app \
    --reload \
    --host "$BACKEND_HOST" \
    --port "$BACKEND_PORT")"

  echo "$pid" > "$BACKEND_PID_FILE"

  if ! wait_for_listener "$BACKEND_PORT"; then
    printf 'Backend failed to start. Check %s\n' "$BACKEND_LOG_FILE" >&2
    exit 1
  fi

  printf 'Backend started on http://%s:%s (pid %s)\n' "$BACKEND_HOST" "$BACKEND_PORT" "$pid"
}

start_frontend() {
  local pid

  cd "$ROOT_DIR/frontend"
  : > "$FRONTEND_LOG_FILE"

  pid="$(VITE_PORT="$FRONTEND_PORT" \
    VITE_API_PROXY_TARGET="http://$BACKEND_HOST:$BACKEND_PORT" \
    spawn_detached "$ROOT_DIR/frontend" "$FRONTEND_LOG_FILE" \
    npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT")"

  echo "$pid" > "$FRONTEND_PID_FILE"

  if ! wait_for_listener "$FRONTEND_PORT"; then
    printf 'Frontend failed to start. Check %s\n' "$FRONTEND_LOG_FILE" >&2
    exit 1
  fi

  printf 'Frontend started on http://%s:%s (pid %s)\n' "$FRONTEND_HOST" "$FRONTEND_PORT" "$pid"
}

main() {
  local python_bin
  python_bin="$(find_python_bin)"

  ensure_backend_deps "$python_bin"
  ensure_frontend_deps

  stop_pid_file_process "backend" "$BACKEND_PID_FILE"
  stop_pid_file_process "frontend" "$FRONTEND_PID_FILE"
  stop_port_processes "backend" "$BACKEND_PORT"
  stop_port_processes "frontend" "$FRONTEND_PORT"

  start_backend "$python_bin"
  start_frontend

  printf '\nLogs:\n'
  printf '  backend: %s\n' "$BACKEND_LOG_FILE"
  printf '  frontend: %s\n' "$FRONTEND_LOG_FILE"
}

main "$@"
