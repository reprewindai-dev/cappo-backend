#!/bin/sh
set -e

# Run database migrations
echo "Running database migrations..."
alembic upgrade head

# Start application
echo "Starting FastAPI server..."
exec uvicorn cappo_backend.main:app --host 0.0.0.0 --port 8000
