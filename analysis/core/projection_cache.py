"""
Projection Cache
================
Redis-backed cache for projection DataFrames.

Replaces the former DuckDB implementation (projection_cache.duckdb) to
eliminate write-lock collisions when multiple uvicorn/Celery workers run
simultaneously.  Redis TTL handles expiry natively — no purge job needed.

The Redis connection reuses CELERY_BROKER_URL (already required for the
task queue) so no new infrastructure is needed.

Usage::

    from analysis.core.projection_cache import ProjectionCache

    cache = ProjectionCache()
    df = cache.get(slate_id, sport, site)          # None on miss
    if df is None:
        df = engine.generate(...)
        cache.set(slate_id, sport, site, df)
    cache.invalidate(slate_id)                     # force-refresh

Key format : ``proj_cache:{slate_id}:{SPORT}:{SITE}``
TTL         : 6 hours by default (PROJECTION_CACHE_TTL_SECONDS env var)
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd


log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redis connection helper
# ---------------------------------------------------------------------------
# Fall back gracefully when Redis is unavailable (e.g. unit tests without
# a broker).  All public methods return safe no-op values on connection error.
# ---------------------------------------------------------------------------

_REDIS_URL = os.getenv(
    "CELERY_BROKER_URL",
    os.getenv("REDIS_URL", "redis://localhost:6379/0"),
)

# Default TTL: honour PROJECTION_CACHE_TTL_SECONDS if set, else 6 hours.
_DEFAULT_TTL_SECS: int = int(os.getenv("PROJECTION_CACHE_TTL_SECONDS", str(6 * 3600)))


def _make_redis_client():  # type: ignore[return]
    """Create a Redis client, returning None if redis is not importable."""
    try:
        import redis
        return redis.from_url(_REDIS_URL, decode_responses=True, socket_connect_timeout=2)
    except Exception as exc:
        log.warning("Redis unavailable — projection cache disabled: %s", exc)
        return None


class ProjectionCache:
    """Redis-backed projection cache with TTL expiry.

    Replaces the former DuckDB implementation (projection_cache.duckdb).
    Redis TTL handles expiry automatically — no purge job required.
    Degrades gracefully when Redis is unavailable (always returns cache miss).
    """

    def __init__(
        self,
        ttl_hours: float = _DEFAULT_TTL_SECS / 3600,
        # Legacy parameter — silently ignored (kept for call-site compat).
        db_path: Any = None,
    ) -> None:
        self._ttl_secs = int(ttl_hours * 3600)
        self._redis = _make_redis_client()

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _key(slate_id: str, sport: str, site: str) -> str:
        return f"proj_cache:{slate_id}:{sport.upper()}:{site.upper()}"

    # ── Public API ────────────────────────────────────────────────────────────

    def get(
        self,
        slate_id: str,
        sport: str,
        site: str,
    ) -> pd.DataFrame | None:
        """Return cached DataFrame or *None* on cache miss."""
        if self._redis is None:
            return None
        try:
            raw = self._redis.get(self._key(slate_id, sport, site))
            if raw is None:
                log.debug("Cache miss — %s/%s/%s", slate_id, sport, site)
                return None
            df = pd.read_json(raw, orient="records")
            log.info("Cache hit — %s/%s/%s (%d rows)", slate_id, sport, site, len(df))
            return df
        except Exception as exc:
            log.warning("Cache read failed: %s", exc)
            return None

    def set(
        self,
        slate_id: str,
        sport: str,
        site: str,
        df: pd.DataFrame,
        ttl_hours: float | None = None,
    ) -> bool:
        """Store *df* in the cache under a Redis key with TTL. Returns True on success."""
        if self._redis is None:
            return False
        try:
            ttl = int((ttl_hours * 3600) if ttl_hours is not None else self._ttl_secs)
            payload = df.to_json(orient="records", date_format="iso")
            self._redis.set(self._key(slate_id, sport, site), payload, ex=ttl)
            log.info(
                "Cache set — %s/%s/%s (%d rows, TTL %.1fh)",
                slate_id, sport, site, len(df), ttl / 3600,
            )
            return True
        except Exception as exc:
            log.warning("Cache write failed: %s", exc)
            return False

    def invalidate(
        self,
        slate_id: str,
        sport: str | None = None,
        site: str | None = None,
    ) -> int:
        """Delete one or more cache entries. Returns number of keys deleted."""
        if self._redis is None:
            return 0
        try:
            if sport and site:
                keys = [self._key(slate_id, sport, site)]
            else:
                # Wildcard scan — safe because the key space is small
                pattern = f"proj_cache:{slate_id}:*"
                keys = list(self._redis.scan_iter(pattern))
            if keys:
                self._redis.delete(*keys)
            log.info("Cache invalidated %d keys for slate_id=%s", len(keys), slate_id)
            return len(keys)
        except Exception as exc:
            log.warning("Cache invalidate failed: %s", exc)
            return 0

    def purge_expired(self) -> int:
        """No-op — Redis TTL handles expiry automatically."""
        return 0

    def stats(self) -> dict[str, Any]:
        """Return a count of live cache keys."""
        if self._redis is None:
            return {"live_entries": 0, "backend": "redis_unavailable"}
        try:
            keys = list(self._redis.scan_iter("proj_cache:*"))
            return {"live_entries": len(keys), "backend": "redis"}
        except Exception as exc:
            log.warning("Cache stats failed: %s", exc)
            return {}

    def close(self) -> None:
        """No explicit close needed for Redis client."""
        pass


# ── Module-level singleton ─────────────────────────────────────────────────
_cache: ProjectionCache | None = None


def get_cache() -> ProjectionCache:
    """Return (or create) the process-wide cache singleton."""
    global _cache
    if _cache is None:
        _cache = ProjectionCache()
    return _cache
