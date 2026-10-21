#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
command -v uv >/dev/null || { echo 'Install uv before starting.' >&2; exit 1; }
command -v npm >/dev/null || { echo 'Install Node.js 22+ and npm before starting.' >&2; exit 1; }
cd "$ROOT/backend"
uv sync --frozen
.venv/bin/python -m app.cli migrate
cd "$ROOT/frontend"
if [[ ! -d node_modules ]]; then npm ci; fi
npm run build
cd "$ROOT/backend"
.venv/bin/python -m app.worker &
worker_pid=$!
cleanup(){ kill "$worker_pid" 2>/dev/null || true; wait "$worker_pid" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
printf '%s\n' 'Open http://127.0.0.1:8788 . Press Ctrl+C to stop API and worker.'
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8788 --workers 1
