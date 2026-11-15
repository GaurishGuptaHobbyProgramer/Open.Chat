#!/bin/sh
set -eu

echo "[startup] Applying pending Alembic migrations."
if ! python -m flask --app app db upgrade; then
    echo "[startup] ERROR: database migration failed; Gunicorn will not start." >&2
    exit 1
fi

echo "[startup] Verifying the PostgreSQL schema and Alembic revision."
if ! python -m flask --app app verify-schema; then
    echo "[startup] ERROR: database schema verification failed; Gunicorn will not start." >&2
    exit 1
fi

echo "[startup] Starting Gunicorn."
exec gunicorn --worker-class gthread --workers 1 --threads 4 wsgi:app
