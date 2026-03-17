"""
Projection Cache
================
Lightweight DuckDB-backed cache for projection DataFrames.

Usage::

    from analysis.core.projection_cache import ProjectionCache

    cache = ProjectionCache()
    df = cache.get(slate_id, sport, site)          # None on miss
    if df is None:
        df = engine.generate(...)
        cache.set(slate_id, sport, site, df)
    cache.invalidate(slate_id)                     # force-refresh

The cache key is (slate_id, sport, site, cache_date). By default entries expire
after ``ttl_hours`` (default=6). Rows are stored as a JSON blob for portability.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

_DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "projection_cache.duckdb"


class ProjectionCache:
    """DuckDB-backed projection cache with TTL expiry."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        ttl_hours: float = 6.0,
    ) -> None:
        self._db_path = Path(db_path or _DEFAULT_DB)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ttl = timedelta(hours=ttl_hours)
        self._conn = self._connect()
        self._ensure_schema()

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _connect(self):  # type: ignore[return]
        try:
            from analysis.shared.db import get_conn
            return get_conn(self._db_path, db_key="projection_cache")
        except ImportError as exc:
            raise ImportError("duckdb is required for ProjectionCache") from exc

    def _ensure_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS projection_cache (
                slate_id    VARCHAR,
                sport       VARCHAR,
                site        VARCHAR,
                created_at  TIMESTAMPTZ,
                expires_at  TIMESTAMPTZ,
                row_count   INTEGER,
                payload     TEXT,
                PRIMARY KEY (slate_id, sport, site)
            )
        """)
        # Migrate old schema: rename 'cached_at' → 'created_at' if needed
        # and add 'row_count' column if missing (added after initial schema).
        try:
            cols = [r[0] for r in self._conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'projection_cache'"
            ).fetchall()]
            if "cached_at" in cols and "created_at" not in cols:
                self._conn.execute(
                    "ALTER TABLE projection_cache RENAME COLUMN cached_at TO created_at"
                )
                log.info("projection_cache: migrated cached_at → created_at")
            if "row_count" not in cols:
                self._conn.execute(
                    "ALTER TABLE projection_cache ADD COLUMN row_count INTEGER DEFAULT 0"
                )
                log.info("projection_cache: migrated schema to add row_count")
        except Exception:
            pass  # tolerate any migration failure

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    # ── Public API ────────────────────────────────────────────────────────────

    def get(
        self,
        slate_id: str,
        sport: str,
        site: str,
    ) -> pd.DataFrame | None:
        """Return cached DataFrame or *None* on cache miss / expiry."""
        try:
            now = self._now()
            row = self._conn.execute(
                """
                SELECT payload, expires_at FROM projection_cache
                WHERE slate_id = ? AND sport = ? AND site = ?
                """,
                [slate_id, sport.upper(), site.upper()],
            ).fetchone()
            if row is None:
                log.debug("Cache miss — %s/%s/%s", slate_id, sport, site)
                return None
            payload, expires_at = row
            if expires_at < now:
                log.debug("Cache expired — %s/%s/%s (expired %s)", slate_id, sport, site, expires_at)
                self.invalidate(slate_id, sport, site)
                return None
            records: list[dict[str, Any]] = json.loads(payload)
            df = pd.DataFrame(records)
            log.info(
                "Cache hit — %s/%s/%s (%d rows, expires %s)",
                slate_id, sport, site, len(df), expires_at,
            )
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
        """Store *df* in the cache. Returns True on success."""
        try:
            now = self._now()
            ttl = timedelta(hours=ttl_hours) if ttl_hours is not None else self._ttl
            expires_at = now + ttl
            payload = df.to_json(orient="records", date_format="iso")
            self._conn.execute(
                """
                INSERT OR REPLACE INTO projection_cache
                (slate_id, sport, site, created_at, expires_at, row_count, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    slate_id,
                    sport.upper(),
                    site.upper(),
                    now,
                    expires_at,
                    len(df),
                    payload,
                ],
            )
            log.info(
                "Cache set — %s/%s/%s (%d rows, TTL %.1fh)",
                slate_id, sport, site, len(df), ttl.total_seconds() / 3600,
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
        """Delete cache entry/entries. Returns number of rows deleted."""
        try:
            if sport and site:
                result = self._conn.execute(
                    "DELETE FROM projection_cache WHERE slate_id=? AND sport=? AND site=?",
                    [slate_id, sport.upper(), site.upper()],
                )
            elif sport:
                result = self._conn.execute(
                    "DELETE FROM projection_cache WHERE slate_id=? AND sport=?",
                    [slate_id, sport.upper()],
                )
            else:
                result = self._conn.execute(
                    "DELETE FROM projection_cache WHERE slate_id=?",
                    [slate_id],
                )
            n = result.rowcount  # type: ignore[attr-defined]
            log.info("Cache invalidated %d rows for slate_id=%s", n, slate_id)
            return n
        except Exception as exc:
            log.warning("Cache invalidate failed: %s", exc)
            return 0

    def purge_expired(self) -> int:
        """Remove all expired entries. Returns number of rows removed."""
        try:
            now = self._now()
            result = self._conn.execute(
                "DELETE FROM projection_cache WHERE expires_at < ?", [now]
            )
            n = result.rowcount  # type: ignore[attr-defined]
            if n:
                log.info("Purged %d expired cache entries", n)
            return n
        except Exception as exc:
            log.warning("Cache purge failed: %s", exc)
            return 0

    def stats(self) -> dict[str, Any]:
        """Return summary stats about the cache contents."""
        try:
            now = self._now()
            rows = self._conn.execute(
                """
                SELECT
                    COUNT(*) AS total_entries,
                    SUM(row_count) AS total_rows,
                    SUM(CASE WHEN expires_at >= ? THEN 1 ELSE 0 END) AS live_entries,
                    SUM(CASE WHEN expires_at < ? THEN 1 ELSE 0 END) AS expired_entries
                FROM projection_cache
                """,
                [now, now],
            ).fetchone()
            return {
                "total_entries": rows[0] or 0,
                "total_rows": rows[1] or 0,
                "live_entries": rows[2] or 0,
                "expired_entries": rows[3] or 0,
            }
        except Exception as exc:
            log.warning("Cache stats failed: %s", exc)
            return {}

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


# ── Module-level singleton ─────────────────────────────────────────────────
_cache: ProjectionCache | None = None


def get_cache() -> ProjectionCache:
    """Return (or create) the process-wide cache singleton."""
    global _cache
    if _cache is None:
        _cache = ProjectionCache()
    return _cache
