#!/bin/sh
set -e

if [ "${CAPPO_SKIP_MIGRATIONS:-0}" != "1" ]; then
  echo "Running database migrations..."
  PYTHONPATH=/app alembic upgrade head
else
  echo "Skipping database migrations."
fi

# Start application
echo "Starting FastAPI server..."
exec uvicorn cappo_backend.main:app --host 0.0.0.0 --port 8002
