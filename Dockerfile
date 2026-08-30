# syntax=docker/dockerfile:1

# ── Stage 1: the static UI ───────────────────────────────────────────────────
# Built here rather than committed, so the image cannot ship an export that is older
# than the page it was built from. `npm run prebuild` copies the operator console out
# of dashboard/ on the way through.
FROM node:22-alpine AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci

COPY dashboard/ ./dashboard/
COPY frontend/ ./frontend/
RUN cd frontend && npm run build


# ── Stage 2: the gateway ─────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first: they change far less often than the source, so this layer is
# reused across almost every rebuild.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY dashboard/ ./dashboard/
COPY --from=frontend /build/frontend/out ./frontend/out

# The audit database, when it is a SQLite file, is the one piece of state worth
# keeping. Declared so `docker run` without an explicit mount still does not lose it
# silently.
RUN mkdir -p /var/lib/aether && \
    useradd --system --uid 10001 aether && \
    chown -R aether:aether /var/lib/aether /app
VOLUME ["/var/lib/aether"]

USER aether

ENV AETHER_AUDIT_DB_PATH=/var/lib/aether/audit.db \
    AETHER_HOST=0.0.0.0 \
    AETHER_PORT=8000

EXPOSE 8000

# /api/health checks the audit store, the state store and the policy set, so this is a
# readiness probe rather than a "the process is up" probe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=4).status==200 else 1)"

# One worker, deliberately. With SQLite and in-process state a second worker forks the
# audit chain and splits every session in half, both silently. Set AETHER_AUDIT_DSN and
# AETHER_REDIS_URL and then raise this.
CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
