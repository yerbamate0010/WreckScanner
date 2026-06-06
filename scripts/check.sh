#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -n "${PYTHON:-}" ]]; then
    PYTHON_BIN="$PYTHON"
elif [[ -x ".venv/bin/python" ]]; then
    PYTHON_BIN=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
else
    PYTHON_BIN="python"
fi

run() {
    printf '\n==> %s\n' "$*"
    "$@"
}

run "$PYTHON_BIN" -m compileall -q app core scripts tests analyze.py server.py
run "$PYTHON_BIN" -m ruff check app core scripts tests analyze.py server.py
run "$PYTHON_BIN" -m ruff format --check app core scripts tests analyze.py server.py
run "$PYTHON_BIN" -m unittest discover -s tests

if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    run git diff --check
else
    printf '\n==> git diff --check\n'
    printf 'skip: git repository not available\n'
fi

printf '\nOK: local checks passed\n'
