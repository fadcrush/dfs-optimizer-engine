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
        # DuckDB fallback: used when Redis is unavailable and a db_path is given.
        self._duckdb_con = None
        if self._redis is None and db_path is not None:
            try:
                import duckdb
                import os as _os
                _path = str(db_path)
                _os.makedirs(_os.path.dirname(_path) if _os.path.dirname(_path) else ".", exist_ok=True)
                self._duckdb_con = duckdb.connect(_path)
                self._duckdb_con.execute(
                    """
                    CREATE TABLE IF NOT EXISTS projection_cache (
                        cache_key VARCHAR PRIMARY KEY,
                        payload   VARCHAR NOT NULL,
                        expires_at DOUBLE NOT NULL
                    )
                    """
                )
                log.debug("Projection cache: using DuckDB fallback at %s", _path)
            except Exception as exc:
                log.warning("DuckDB fallback unavailable: %s", exc)
                self._duckdb_con = None

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _key(slate_id: str, sport: str, site: str) -> str:
        return f"proj_cache:{slate_id}:{sport.upper()}:{site.upper()}"

    # ── Public API ────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_payload(raw: str) -> "tuple[pd.DataFrame, str | None]":
        """Parse a Redis/DuckDB cache payload; return (df, cached_at_iso | None).

        Handles both the legacy format (bare JSON array) and the current
        envelope format ``{"_v":1,"_cached_at":"...","_data":[...]}``.
        """
        import io
        import json as _json
        parsed = _json.loads(raw)
        if isinstance(parsed, list):
            # Legacy bare-records format — no timestamp available
            return pd.read_json(io.StringIO(raw), orient="records"), None
        # Envelope format
        records = parsed.get("_data", [])
        df = pd.DataFrame(records) if records else pd.read_json("[]", orient="records")
        return df, parsed.get("_cached_at")

    def get(
        self,
        slate_id: str,
        sport: str,
        site: str,
    ) -> pd.DataFrame | None:
        """Return cached DataFrame or *None* on cache miss."""
        df, _ = self.get_with_meta(slate_id, sport, site)
        return df

    def get_with_meta(
        self,
        slate_id: str,
        sport: str,
        site: str,
    ) -> "tuple[pd.DataFrame | None, str | None]":
        """Return (DataFrame, cached_at_iso) or (None, None) on cache miss.

        ``cached_at_iso`` is an ISO-8601 UTC string recorded when the entry
        was written, or *None* for legacy entries that predate this feature.
        """
        if self._redis is not None:
            try:
                raw = self._redis.get(self._key(slate_id, sport, site))
                if raw is None:
                    log.debug("Cache miss — %s/%s/%s", slate_id, sport, site)
                    return None, None
                df, cached_at = self._parse_payload(raw)
                log.info("Cache hit — %s/%s/%s (%d rows)", slate_id, sport, site, len(df))
                return df, cached_at
            except Exception as exc:
                log.warning("Cache read failed: %s", exc)
                return None, None
        if self._duckdb_con is not None:
            try:
                import time as _time
                rows = self._duckdb_con.execute(
                    "SELECT payload FROM projection_cache WHERE cache_key = ? AND expires_at > ?",
                    [self._key(slate_id, sport, site), _time.time()],
                ).fetchall()
                if not rows:
                    return None, None
                df, cached_at = self._parse_payload(rows[0][0])
                return df, cached_at
            except Exception as exc:
                log.warning("Cache read (DuckDB) failed: %s", exc)
        return None, None

    def set(
        self,
        slate_id: str,
        sport: str,
        site: str,
        df: pd.DataFrame,
        ttl_hours: float | None = None,
    ) -> bool:
        """Store *df* in the cache under a Redis key with TTL. Returns True on success."""
        ttl = int((ttl_hours * 3600) if ttl_hours is not None else self._ttl_secs)
        if self._redis is not None:
            try:
                import datetime as _dt
                _cached_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
                _records = df.to_json(orient="records", date_format="iso")
                payload = f'{{"_v":1,"_cached_at":"{_cached_at}","_data":{_records}}}'
                self._redis.set(self._key(slate_id, sport, site), payload, ex=ttl)
                log.info(
                    "Cache set — %s/%s/%s (%d rows, TTL %.1fh)",
                    slate_id, sport, site, len(df), ttl / 3600,
                )
                return True
            except Exception as exc:
                log.warning("Cache write failed: %s", exc)
                return False
        if self._duckdb_con is not None:
            try:
                import time as _time
                import datetime as _dt
                key = self._key(slate_id, sport, site)
                _cached_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
                _records = df.to_json(orient="records", date_format="iso")
                payload = f'{{"_v":1,"_cached_at":"{_cached_at}","_data":{_records}}}'
                expires_at = _time.time() + ttl
                self._duckdb_con.execute(
                    """
                    INSERT OR REPLACE INTO projection_cache (cache_key, payload, expires_at)
                    VALUES (?, ?, ?)
                    """,
                    [key, payload, expires_at],
                )
                return True
            except Exception as exc:
                log.warning("Cache write (DuckDB) failed: %s", exc)
        return False

    def invalidate(
        self,
        slate_id: str,
        sport: str | None = None,
        site: str | None = None,
    ) -> int:
        """Delete one or more cache entries. Returns number of keys deleted."""
        if self._redis is not None:
            try:
                if sport and site:
                    keys = [self._key(slate_id, sport, site)]
                else:
                    pattern = f"proj_cache:{slate_id}:*"
                    keys = list(self._redis.scan_iter(pattern))
                if keys:
                    self._redis.delete(*keys)
                log.info("Cache invalidated %d keys for slate_id=%s", len(keys), slate_id)
                return len(keys)
            except Exception as exc:
                log.warning("Cache invalidate failed: %s", exc)
                return 0
        if self._duckdb_con is not None:
            try:
                if sport and site:
                    self._duckdb_con.execute(
                        "DELETE FROM projection_cache WHERE cache_key = ?",
                        [self._key(slate_id, sport, site)],
                    )
                    return 1
                else:
                    result = self._duckdb_con.execute(
                        "SELECT COUNT(*) FROM projection_cache WHERE cache_key LIKE ?",
                        [f"proj_cache:{slate_id}:%"],
                    ).fetchone()
                    self._duckdb_con.execute(
                        "DELETE FROM projection_cache WHERE cache_key LIKE ?",
                        [f"proj_cache:{slate_id}:%"],
                    )
                    return result[0] if result else 0
            except Exception as exc:
                log.warning("Cache invalidate (DuckDB) failed: %s", exc)
        return 0

    def purge_expired(self) -> int:
        """Purge expired entries. Redis handles this natively; DuckDB requires explicit delete."""
        if self._duckdb_con is not None:
            try:
                import time as _time
                result = self._duckdb_con.execute(
                    "SELECT COUNT(*) FROM projection_cache WHERE expires_at <= ?",
                    [_time.time()],
                ).fetchone()
                self._duckdb_con.execute(
                    "DELETE FROM projection_cache WHERE expires_at <= ?",
                    [_time.time()],
                )
                return result[0] if result else 0
            except Exception as exc:
                log.warning("Cache purge (DuckDB) failed: %s", exc)
        return 0

    def stats(self) -> dict[str, Any]:
        """Return a count of live cache keys."""
        if self._redis is not None:
            try:
                keys = list(self._redis.scan_iter("proj_cache:*"))
                return {"live_entries": len(keys), "backend": "redis"}
            except Exception as exc:
                log.warning("Cache stats failed: %s", exc)
                return {}
        if self._duckdb_con is not None:
            try:
                import time as _time
                result = self._duckdb_con.execute(
                    "SELECT COUNT(*) FROM projection_cache WHERE expires_at > ?",
                    [_time.time()],
                ).fetchone()
                total = result[0] if result else 0
                return {"live_entries": total, "total_entries": total, "backend": "duckdb"}
            except Exception as exc:
                log.warning("Cache stats (DuckDB) failed: %s", exc)
        return {"live_entries": 0, "backend": "redis_unavailable"}

    def close(self) -> None:
        """Close DuckDB connection if open. Redis client needs no explicit close."""
        if self._duckdb_con is not None:
            try:
                self._duckdb_con.close()
            except Exception:
                pass
            self._duckdb_con = None


# ── Module-level singleton ─────────────────────────────────────────────────
_cache: ProjectionCache | None = None


def get_cache() -> ProjectionCache:
    """Return (or create) the process-wide cache singleton."""
    global _cache
    if _cache is None:
        _cache = ProjectionCache()
    return _cache
