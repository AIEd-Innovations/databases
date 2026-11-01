#!/usr/bin/env bash
# One-command setup: venv, install, connect, create everything, verify.
#
#   bash scripts/create_db.sh
#
# Reads MONGODB_URI from .env (never committed). Safe to re-run: every step
# is idempotent and nothing is dropped or deleted.
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
VENV=".venv"

if [[ -f .env ]]; then
  echo "==> loading .env"
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
else
  echo "error: no .env file." >&2
  echo "  Create one with your Atlas connection string:" >&2
  echo "    MONGODB_URI='mongodb+srv://USER:PASSWORD@CLUSTER/?retryWrites=true&w=majority'" >&2
  echo "    MONGODB_DB=aied" >&2
  echo "  .env is gitignored. Never commit it." >&2
  exit 1
fi

if [[ -z "${MONGODB_URI:-}" ]]; then
  echo "error: MONGODB_URI is empty in .env" >&2
  exit 1
fi

if [[ ! -d "$VENV" ]]; then
  echo "==> creating $VENV"
  "$PYTHON" -m venv "$VENV"
fi

echo "==> installing aied_db and its dev extras"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e ".[dev]"

echo "==> checking the connection"
"$VENV/bin/python" scripts/init_db.py --ping

echo "==> applying schema, indexes and defaults"
"$VENV/bin/python" scripts/init_db.py "$@"

echo "==> running the test suite"
"$VENV/bin/python" -m pytest -q

echo
echo "done. Database '${MONGODB_DB:-aied}' is ready."
echo "Next: python scripts/init_db.py --show   to review what is in place."
