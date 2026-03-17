"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

# Make sure the project root is importable from tests/
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Minimal player-pool fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_nba_player(
    name: str,
    pos: str,
    salary: int,
    proj: float,
    team: str = "BOS",
    opp: str = "MIA",
    dfs_id: str | None = None,
    own: float = 10.0,
) -> dict:
    return {
        "Name": name,
        "Pos": pos,
        "Salary": salary,
        "Proj": proj,
        "Team": team,
        "Opp": opp,
        "DFS_ID": dfs_id or name.replace(" ", "_"),
        "Own": own,
    }


FD_PLAYERS_RAW = [
    # Two PGs
    _make_nba_player("PG One",   "PG",     8000, 42.0, "BOS", "MIA", own=25),
    _make_nba_player("PG Two",   "PG",     7200, 38.5, "BOS", "MIA", own=18),
    # Two SGs
    _make_nba_player("SG One",   "SG",     7500, 40.0, "MIA", "BOS", own=20),
    _make_nba_player("SG Two",   "SG",     6800, 36.0, "MIA", "BOS", own=15),
    # Two SFs
    _make_nba_player("SF One",   "SF",     7000, 37.5, "BOS", "MIA", own=22),
    _make_nba_player("SF Two",   "SF",     6500, 34.0, "BOS", "MIA", own=12),
    # Two PFs
    _make_nba_player("PF One",   "PF",     6200, 33.0, "MIA", "BOS", own=8),
    _make_nba_player("PF Two",   "PF",     5800, 30.0, "MIA", "BOS", own=7),
    # Centre
    _make_nba_player("Centre",   "C",      5500, 28.0, "BOS", "MIA", own=5),
    # Extra players so there is a choice
    _make_nba_player("PG Three", "PG",     6000, 30.0, "CHI", "NYK"),
    _make_nba_player("SG Three", "SG",     5800, 28.0, "CHI", "NYK"),
    _make_nba_player("SF Three", "SF",     5600, 26.0, "CHI", "NYK"),
    _make_nba_player("PF Three", "PF",     5400, 24.0, "NYK", "CHI"),
    _make_nba_player("C Two",    "C",      5200, 22.0, "NYK", "CHI"),
    # Fifth team so max_from_same_team=2 constraints are feasible with FD 9-slot roster
    _make_nba_player("PF Four",  "PF",     4800, 20.0, "LAL", "GSW"),
    _make_nba_player("C Three",  "C",      4600, 19.0, "LAL", "GSW"),
]


@pytest.fixture()
def fd_players_df() -> pd.DataFrame:
    """FanDuel player pool with 14 players across all positions."""
    return pd.DataFrame(FD_PLAYERS_RAW)


@pytest.fixture()
def tmp_duckdb(tmp_path):
    """Return a temporary DuckDB file path that does not yet exist."""
    return tmp_path / "test_cache.duckdb"
