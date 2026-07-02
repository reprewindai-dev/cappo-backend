# Multi-stage production-ready Dockerfile
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy packaging configuration
COPY pyproject.toml .
COPY README.md .

# Create dummy package directory to install dependencies first
RUN mkdir cappo_backend && touch cappo_backend/__init__.py

# Install dependencies and PostgreSQL adapter
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir . && \
    pip install --no-cache-dir psycopg2-binary

# Final image stage
FROM python:3.11-slim AS runner

# Create low-privilege system user and group
RUN groupadd -g 10001 appuser && \
    useradd -r -u 10001 -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed site-packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy app code and set ownership
COPY --chown=appuser:appuser cappo_backend/ /app/cappo_backend/
COPY --chown=appuser:appuser migrations/ /app/migrations/
COPY --chown=appuser:appuser agents/ /app/agents/
COPY --chown=appuser:appuser alembic.ini /app/alembic.ini
COPY --chown=appuser:appuser entrypoint.sh /app/entrypoint.sh

# Ensure entrypoint script is executable and own everything in /app
RUN chmod +x /app/entrypoint.sh && chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -f http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["./entrypoint.sh"]
