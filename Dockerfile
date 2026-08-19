# Multi-stage production-ready Dockerfile
FROM ghcr.io/astral-sh/uv:0.3.0 AS uv

FROM python:3.11-slim AS builder

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy packaging configuration
COPY pyproject.toml .
COPY README.md .

# Create dummy package directory to install dependencies first
COPY cappo_backend/ /app/cappo_backend/

RUN uv pip install \
    --system \
    . psycopg2-binary

# Final image stage
FROM python:3.11-slim AS runner

# Create low-privilege system user and group
RUN groupadd -g 10001 cappo && \
    useradd -r -u 10001 -g cappo -d /app -s /sbin/nologin cappo

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed site-packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy app code and set ownership
COPY --chown=cappo:cappo cappo_backend/ /app/cappo_backend/
COPY --chown=cappo:cappo migrations/ /app/migrations/
COPY --chown=cappo:cappo agents/ /app/agents/
COPY --chown=cappo:cappo alembic.ini /app/alembic.ini
COPY --chown=cappo:cappo entrypoint.sh /app/entrypoint.sh

# Ensure entrypoint script is executable on Linux even when built from a
# Windows checkout, and own everything in /app.
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh && chown -R cappo:cappo /app

# Switch to non-root user
USER cappo

ENV PYTHONPATH=/app

# Expose port
EXPOSE 8002

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -f http://127.0.0.1:8002/health || exit 1

ENTRYPOINT ["./entrypoint.sh"]
# Start application
CMD ["sh", "-c", "uvicorn cappo_backend.main:app --host 0.0.0.0 --port ${PORT:-8002} --ws none"]
