FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Backend code + requirements first for layer caching.
COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# Local HuggingFace LLM provider (CPU-only torch keeps the image lean).
COPY backend/requirements-local.txt ./requirements-local.txt
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install -r requirements-local.txt

COPY backend/ ./
# Sandbox build context (used to auto-bootstrap the agent sandbox image).
COPY sandbox/ /opt/sandbox/

# Prisma engine download at build time (best-effort; entrypoint also handles it).
RUN prisma generate --schema=db/prisma/schema.prisma || true

RUN chmod +x /app/entrypoint.sh
EXPOSE 8000
# /bin/sh avoids depending on executable bit when ./backend is bind-mounted
# over /app during local development.
ENTRYPOINT ["/bin/sh", "/app/entrypoint.sh"]
