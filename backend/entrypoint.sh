#!/bin/sh
# Container entrypoint: apply the Prisma schema to Postgres (idempotent),
# then hand over to whatever command compose provided (uvicorn --reload).
set -e

if [ -f db/prisma/schema.prisma ]; then
    echo "[entrypoint] applying prisma schema..."
    prisma db push --schema=db/prisma/schema.prisma --skip-generate 2>/dev/null || echo "[entrypoint] prisma db push skipped (no database yet)"
fi

exec "$@"
