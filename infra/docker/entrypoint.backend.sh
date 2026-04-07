#!/bin/sh
# entrypoint.backend.sh
# Ensure volume-mounted write directories are accessible before dropping to appuser.
set -e

# These paths are overridden by volume mounts – permissions may arrive from the
# host as root-owned. Fix them here so appuser can write.
for dir in \
    /app/backend/uploads/slates \
    /app/backend/uploads/projections \
    /app/backend/uploads/lineups \
    /app/outputs/projections \
    /app/outputs/lineups \
    /app/data \
    /app/logs/backend \
    /app/logs/workers; do
    mkdir -p "$dir"
    chmod 777 "$dir"
done

# Drop to the non-root appuser and exec the server.
# When SERVICE_TYPE=worker (Render Celery service) run Celery instead of
# the default CMD (uvicorn). This avoids needing a separate Dockerfile.
if [ "${SERVICE_TYPE}" = "worker" ]; then
    exec gosu appuser celery -A celery_app worker --loglevel=info --concurrency=2
fi
exec gosu appuser "$@"
