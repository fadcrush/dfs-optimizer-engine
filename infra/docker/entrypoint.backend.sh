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

# Drop to the non-root appuser and exec the server
exec gosu appuser "$@"
