#!/bin/sh
set -e

if [ "${CAPPO_SKIP_MIGRATIONS:-0}" != "1" ]; then
  echo "Running database migrations..."
  PYTHONPATH=/app alembic upgrade head
else
  echo "Skipping database migrations."
fi

# Start application or execute the supplied command.
echo "Starting CAPPO command..."
exec "$@"
