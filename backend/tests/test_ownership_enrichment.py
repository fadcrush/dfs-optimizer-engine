"""
Unit tests for analysis.core.ownership_enrichment.

All tests use in-memory DuckDB databases so no on-disk fixtures are required.
The _OWNERSHIP_DB / _GAME_LOGS_DB paths are monkey-patched to temporary files.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Generator

import duckdb
import pytest

import analysis.core.ownership_enrichment as oe


# ---------------------------------------------------------------------------
# Fixtures: in-memory DuckDB databases written to temp files
# ---------------------------------------------------------------------------

@pytest.fixture()
def ownership_db(tmp_path: Path) -> Generator[Path, None, None]:
    db_path = tmp_path / "ownership_history.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE ownership_history (
            player_name VARCHAR,
            game_date   DATE,
            site        VARCHAR,
            slate_id    VARCHAR DEFAULT '',
            actual_own_pct FLOAT,
            proj_at_lock   FLOAT,
            salary         INTEGER,
            team_total     FLOAT,
            is_home        BOOLEAN,
            contest_type   VARCHAR DEFAULT 'gpp',
            own_source     VARCHAR
        )
    """)
    con.execute("""
        INSERT INTO ownership_history VALUES
            ('James Harden',  '2026-03-20', 'DK', 'slate1', 18.0, 22.5, 7800, NULL, NULL, 'gpp', 'model'),
            ('Jordan Walsh',  '2026-03-20', 'DK', 'slate1',  4.5, 41.0, 3400, NULL, NULL, 'gpp', 'model'),
            ('Sam Hauser',    '2026-03-20', 'DK', 'slate1',  6.5, 18.5, 4800, NULL, NULL, 'gpp', 'model')
    """)
    con.close()
    yield db_path


@pytest.fixture()
def game_logs_db(tmp_path: Path) -> Generator[Path, None, None]:
    db_path = tmp_path / "game_logs.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE player_game_logs (
            player_name VARCHAR,
            game_date   DATE,
            minutes     DOUBLE,
            dk_pts      DOUBLE,
            fd_pts      DOUBLE,
            team        VARCHAR,
            opponent    VARCHAR
        )
    """)
    # James Harden baseline: 28 min average over last 10 games
    for i in range(10):
        con.execute(
            "INSERT INTO player_game_logs VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["James Harden", f"2026-03-{10 + i:02d}", 28.0, 42.0, 41.0, "CLE", "BKN"],
        )
    # Jordan Walsh vacancy games: played 34 min in games where Harden has NO entry
    for i in range(5):
        con.execute(
            "INSERT INTO player_game_logs VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["Jordan Walsh", f"2026-02-{10 + i:02d}", 34.0, 22.0, 20.0, "CLE", "LAL"],
        )
    # Jordan Walsh normal games (Harden present — same date)
    for i in range(5):
        con.execute(
            "INSERT INTO player_game_logs VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["Jordan Walsh", f"2026-03-{10 + i:02d}", 18.0, 12.0, 11.0, "CLE", "BKN"],
        )
    con.close()
    yield db_path


# ---------------------------------------------------------------------------
# Helper: patch module-level paths
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_db_paths(ownership_db: Path, game_logs_db: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(oe, "_OWNERSHIP_DB", ownership_db)
    monkeypatch.setattr(oe, "_GAME_LOGS_DB", game_logs_db)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _make_beneficiary(ben_name: str, inj_id: str) -> dict:
    return {
        "beneficiary_name": ben_name,
        "injured_player_id": inj_id,
        "delta_minutes": 5.0,
        "rank_score": 0.3,
    }


def test_chalk_flag_above_threshold():
    """Jordan Walsh at 41% proj ownership is chalk above the default 35 threshold."""
    bens = [_make_beneficiary("Jordan Walsh", "harden")]
    injured_lookup = {"harden": {"player_name": "James Harden"}}

    enriched, salary_freed = oe.enrich_beneficiary_list(bens, injured_lookup=injured_lookup)

    assert len(enriched) == 1
    b = enriched[0]
    assert b["is_chalk"] is True
    assert b["ownership_tier"] == "chalk"
    assert b["proj_ownership_pct"] == pytest.approx(41.0, abs=0.5)


def test_not_chalk_below_threshold():
    """Sam Hauser at 18.5% is below the default 35 threshold — not chalk."""
    bens = [_make_beneficiary("Sam Hauser", "harden")]
    injured_lookup = {"harden": {"player_name": "James Harden"}}

    enriched, _ = oe.enrich_beneficiary_list(bens, injured_lookup=injured_lookup)

    b = enriched[0]
    assert b["is_chalk"] is False
    assert b["ownership_tier"] in ("moderate", "contrarian")


def test_custom_chalk_threshold():
    """A lower threshold of 20% makes Sam Hauser (18.5%) contrarian but Jordan Walsh chalk."""
    bens = [
        _make_beneficiary("Sam Hauser", "harden"),
        _make_beneficiary("Jordan Walsh", "harden"),
    ]
    injured_lookup = {"harden": {"player_name": "James Harden"}}

    enriched, _ = oe.enrich_beneficiary_list(bens, injured_lookup=injured_lookup, chalk_threshold=20.0)

    by_name = {b["beneficiary_name"]: b for b in enriched}
    assert by_name["Sam Hauser"]["is_chalk"] is False
    assert by_name["Jordan Walsh"]["is_chalk"] is True


def test_salary_freed_populated():
    """salary_freed for Harden (salary 7800) is returned correctly."""
    bens = [_make_beneficiary("Jordan Walsh", "harden")]
    injured_lookup = {"harden": {"player_name": "James Harden"}}

    _, salary_freed = oe.enrich_beneficiary_list(bens, injured_lookup=injured_lookup)

    assert salary_freed.get("harden") == pytest.approx(7800.0)


def test_vacancy_usage_delta_positive():
    """Jordan Walsh has a positive usage delta: 34 min vacancy avg > 18 min normal avg."""
    bens = [_make_beneficiary("Jordan Walsh", "harden")]
    injured_lookup = {"harden": {"player_name": "James Harden"}}

    enriched, _ = oe.enrich_beneficiary_list(bens, injured_lookup=injured_lookup)

    delta = enriched[0]["last5_usage_delta"]
    # Baseline avg: last 10 games = 5×34 + 5×18 = 260 → avg 26 min
    # Vacancy avg (games without Harden): 34 min
    # delta = 34 - 26 = 8.0
    assert delta > 0, f"Expected positive usage delta, got {delta}"


def test_unknown_player_returns_zero_ownership():
    """A beneficiary not in ownership_history should get 0 pct and not-chalk."""
    bens = [_make_beneficiary("Unknown Player", "harden")]
    injured_lookup = {"harden": {"player_name": "James Harden"}}

    enriched, _ = oe.enrich_beneficiary_list(bens, injured_lookup=injured_lookup)

    b = enriched[0]
    assert b["proj_ownership_pct"] == 0.0
    assert b["is_chalk"] is False
    assert b["ownership_tier"] == "contrarian"


def test_empty_beneficiaries_returns_empty():
    enriched, salary_freed = oe.enrich_beneficiary_list([], injured_lookup={})
    assert enriched == []
    assert salary_freed == {}


def test_ownership_tier_values():
    """_ownership_tier returns the three correct tiers."""
    assert oe._ownership_tier(40.0, 35.0) == "chalk"
    assert oe._ownership_tier(20.0, 35.0) == "moderate"  # 20 >= 35*0.5=17.5
    assert oe._ownership_tier(5.0, 35.0) == "contrarian"


def test_missing_ownership_db_returns_stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """When ownership DB doesn't exist, function returns stub fields without crashing."""
    monkeypatch.setattr(oe, "_OWNERSHIP_DB", tmp_path / "nonexistent.duckdb")

    bens = [_make_beneficiary("Jordan Walsh", "harden")]
    enriched, salary_freed = oe.enrich_beneficiary_list(
        bens, injured_lookup={"harden": {"player_name": "James Harden"}}
    )

    assert len(enriched) == 1
    assert enriched[0]["is_chalk"] is False
    assert enriched[0]["proj_ownership_pct"] == 0.0
    assert salary_freed == {}
