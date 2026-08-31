#!/bin/sh
set -e

# Railway injects PORT at runtime — must not be passed as literal '$PORT'
PORT="${PORT:-8000}"

exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
