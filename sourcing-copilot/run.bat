@echo off
REM One command to run on Windows: creates a virtualenv, installs, starts on http://localhost:8000
cd /d %~dp0
if not exist .env copy .env.example .env
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install -q --upgrade pip setuptools wheel
pip install -q -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
