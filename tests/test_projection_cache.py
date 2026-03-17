"""Tests for the projection cache (analysis/core/projection_cache.py)."""

from __future__ import annotations

import time
from datetime import timedelta

import pandas as pd
import pytest

from analysis.core.projection_cache import ProjectionCache


@pytest.fixture()
def cache(tmp_duckdb):
    """Fresh in-memory-style cache backed by a temp file."""
    c = ProjectionCache(db_path=tmp_duckdb, ttl_hours=1.0)
    yield c
    c.close()


def _sample_df(n: int = 3) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Name": [f"Player {i}" for i in range(n)],
            "Proj": [float(i * 10) for i in range(n)],
            "Salary": [5000 + i * 500 for i in range(n)],
        }
    )


class TestProjectionCacheBasics:
    def test_miss_returns_none(self, cache):
        assert cache.get("no_such_slate", "NBA", "FD") is None

    def test_set_and_get_roundtrip(self, cache):
        df = _sample_df(5)
        cache.set("slate_001", "NBA", "FD", df)
        result = cache.get("slate_001", "NBA", "FD")
        assert result is not None
        assert list(result.columns) == list(df.columns)
        assert len(result) == len(df)
        pd.testing.assert_frame_equal(result.reset_index(drop=True), df.reset_index(drop=True))

    def test_different_sites_are_independent(self, cache):
        df_fd = _sample_df(3)
        df_dk = _sample_df(4)
        cache.set("slate_X", "NBA", "FD", df_fd)
        cache.set("slate_X", "NBA", "DK", df_dk)
        assert len(cache.get("slate_X", "NBA", "FD")) == 3
        assert len(cache.get("slate_X", "NBA", "DK")) == 4

    def test_different_sports_are_independent(self, cache):
        df_nba = _sample_df(3)
        df_nfl = _sample_df(6)
        cache.set("slate_Y", "NBA", "FD", df_nba)
        cache.set("slate_Y", "NFL", "DK", df_nfl)
        assert len(cache.get("slate_Y", "NBA", "FD")) == 3
        assert len(cache.get("slate_Y", "NFL", "DK")) == 6

    def test_invalidate_removes_entry(self, cache):
        cache.set("slate_002", "NBA", "FD", _sample_df())
        cache.invalidate("slate_002")
        assert cache.get("slate_002", "NBA", "FD") is None

    def test_overwrite_same_key(self, cache):
        cache.set("s", "NBA", "FD", _sample_df(2))
        cache.set("s", "NBA", "FD", _sample_df(7))
        result = cache.get("s", "NBA", "FD")
        assert len(result) == 7


class TestProjectionCacheTTL:
    def test_expired_entry_returns_none(self, tmp_duckdb):
        # Use a very short TTL to simulate expiry
        c = ProjectionCache(db_path=tmp_duckdb, ttl_hours=0.0)
        c.set("slate_exp", "NBA", "FD", _sample_df())
        # With ttl=0 the entry expires immediately; purge then confirm miss
        c.purge_expired()
        assert c.get("slate_exp", "NBA", "FD") is None
        c.close()

    def test_non_expired_entry_still_valid(self, cache):
        cache.set("slate_valid", "NBA", "FD", _sample_df())
        # TTL is 1 hour; should still be valid immediately
        assert cache.get("slate_valid", "NBA", "FD") is not None


class TestProjectionCacheStats:
    def test_stats_returns_dict(self, cache):
        cache.set("s1", "NBA", "FD", _sample_df())
        stats = cache.stats()
        assert isinstance(stats, dict)
        assert stats.get("total_entries", 0) >= 1
