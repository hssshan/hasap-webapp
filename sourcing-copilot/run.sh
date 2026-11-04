#!/usr/bin/env bash
# One command to run locally: creates a virtualenv, installs, starts on http://localhost:8000
set -e
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q --upgrade pip setuptools wheel
pip install -q -r requirements.txt
echo "Open http://localhost:8000  (on your phone: http://<this-computer-ip>:8000)"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
