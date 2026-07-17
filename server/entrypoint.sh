#!/usr/bin/env sh
set -e

# Ensure data dir exists (SQLite file + backtest reports live here; mount as a volume).
mkdir -p ./data ./data/reports

# Apply migrations (works for both SQLite and MySQL via DATABASE_URL).
echo "Running database migrations..."
alembic upgrade head

echo "Starting Kairos API on :8000"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
