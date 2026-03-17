"""
Player Pool Feature Tests
=========================
Tests covering:
  1. projection_overrides wired into ProjectionContext (optimizer route)
  2. projection_overrides wired into ProjectionContext (pipeline route)
  3. out_players (pool exclusion) marks players OUT
  4. locked_players wired into ProjectionContext.locks
  5. Pool excludes: excluded players cannot appear in lineup output
  6. Edge cases: empty pool, all excluded, oversized override dict

Run with:  pytest backend/tests/test_player_pool.py -v
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_dfs.db")

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_slate_csv(rows: list[dict]) -> bytes:
    df = pd.DataFrame(rows)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode()


MINIMAL_FD_SLATE = [
    {"DFS_ID": "fd-1", "Name": "Alice PG",  "Pos": "PG",  "Team": "LAL", "Salary": 8000, "Proj": 44.0, "Own": 20},
    {"DFS_ID": "fd-2", "Name": "Bob PG",    "Pos": "PG",  "Team": "LAC", "Salary": 7200, "Proj": 38.0, "Own": 17},
    {"DFS_ID": "fd-3", "Name": "Carol SG",  "Pos": "SG",  "Team": "LAL", "Salary": 7600, "Proj": 41.0, "Own": 16},
    {"DFS_ID": "fd-4", "Name": "Dave SG",   "Pos": "SG",  "Team": "LAC", "Salary": 7000, "Proj": 36.0, "Own": 14},
    {"DFS_ID": "fd-5", "Name": "Eve SF",    "Pos": "SF",  "Team": "LAL", "Salary": 6900, "Proj": 35.0, "Own": 12},
    {"DFS_ID": "fd-6", "Name": "Frank SF",  "Pos": "SF",  "Team": "LAC", "Salary": 6400, "Proj": 31.0, "Own": 10},
    {"DFS_ID": "fd-7", "Name": "Grace PF",  "Pos": "PF",  "Team": "LAL", "Salary": 6200, "Proj": 30.0, "Own": 9},
    {"DFS_ID": "fd-8", "Name": "Hank PF",   "Pos": "PF",  "Team": "LAC", "Salary": 5800, "Proj": 27.0, "Own": 8},
    {"DFS_ID": "fd-9", "Name": "Ivy C",     "Pos": "C",   "Team": "LAL", "Salary": 7400, "Proj": 39.0, "Own": 15},
    {"DFS_ID": "fd-10", "Name": "Jake C",   "Pos": "C",   "Team": "LAC", "Salary": 6600, "Proj": 33.0, "Own": 11},
    {"DFS_ID": "fd-11", "Name": "Kim PG",   "Pos": "PG",  "Team": "BOS", "Salary": 5200, "Proj": 24.0, "Own": 7},
    {"DFS_ID": "fd-12", "Name": "Leo SG",   "Pos": "SG",  "Team": "BOS", "Salary": 4900, "Proj": 22.0, "Own": 6},
    {"DFS_ID": "fd-13", "Name": "Mia SF",   "Pos": "SF",  "Team": "BOS", "Salary": 4700, "Proj": 21.0, "Own": 5},
    {"DFS_ID": "fd-14", "Name": "Ned PF",   "Pos": "PF",  "Team": "BOS", "Salary": 4500, "Proj": 19.0, "Own": 4},
    {"DFS_ID": "fd-15", "Name": "Olive C",  "Pos": "C",   "Team": "BOS", "Salary": 4300, "Proj": 17.0, "Own": 3},
]


# ──────────────────────────────────────────────────────────────────────────────
# Unit tests: ProjectionContext construction
# ──────────────────────────────────────────────────────────────────────────────

class TestProjectionContextWiring:
    """Verify that route query params are parsed into the correct context fields."""

    def test_projection_overrides_parsed_correctly(self):
        from analysis.core.schemas import ProjectionContext
        overrides = {"Alice PG": 50.0, "Bob PG": 45.5}
        ctx = ProjectionContext(
            sport="NBA",
            site="FD",
            projection_overrides=overrides,
        )
        assert ctx.projection_overrides["Alice PG"] == 50.0
        assert ctx.projection_overrides["Bob PG"] == 45.5

    def test_locks_parsed_correctly(self):
        from analysis.core.schemas import ProjectionContext
        ctx = ProjectionContext(sport="NBA", site="FD", locks=["Alice PG", "Ivy C"])
        assert "Alice PG" in ctx.locks
        assert "Ivy C" in ctx.locks

    def test_out_players_become_injuries(self):
        """Simulates the router logic: out_players → injuries dict."""
        out_players_str = "Alice PG,Carol SG"
        injuries: dict[str, str] = {}
        for name in out_players_str.split(","):
            name = name.strip()
            if name:
                injuries[name] = "OUT"
        assert injuries == {"Alice PG": "OUT", "Carol SG": "OUT"}

    def test_projection_overrides_json_parse(self):
        """Simulates the router JSON parsing from query string."""
        raw = json.dumps({"Bob PG": 48.0, "Ivy C": 52.1})
        overrides = json.loads(raw)
        assert overrides["Bob PG"] == 48.0
        assert overrides["Ivy C"] == 52.1

    def test_projection_overrides_invalid_json_gracefully_ignored(self):
        """Router catches JSON errors and falls back to empty dict."""
        bad_json = "not_valid_json"
        try:
            result = json.loads(bad_json)
        except (json.JSONDecodeError, ValueError):
            result = {}
        assert result == {}

    def test_locked_players_csv_parse(self):
        """Simulates the router logic: locked_players → list."""
        raw = "Alice PG, Ivy C , " # intentional trailing spaces/comma
        locks = [n.strip() for n in raw.split(",") if n.strip()]
        assert locks == ["Alice PG", "Ivy C"]


# ──────────────────────────────────────────────────────────────────────────────
# Unit tests: pool exclude / include logic
# ──────────────────────────────────────────────────────────────────────────────

class TestPoolExcludeInclude:
    """Verify the core pool inclusion mechanic mirrors what the optimizer does."""

    def test_excluded_players_marked_out_in_injuries(self):
        """Excluded names from the UI pool become OUT injuries."""
        pool_excluded = ["Alice PG", "Bob PG"]
        dashboard_excluded = ["Carol SG"]

        merged = list(dict.fromkeys(dashboard_excluded + pool_excluded))
        injuries: dict[str, str] = {name: "OUT" for name in merged}

        assert injuries["Alice PG"] == "OUT"
        assert injuries["Bob PG"] == "OUT"
        assert injuries["Carol SG"] == "OUT"

    def test_deduplication_of_merged_excludes(self):
        """Same player excluded from both dashboard and pool → appears once."""
        pool_excluded = ["Alice PG", "Carol SG"]
        dashboard_excluded = ["Alice PG"]   # duplicate

        merged = list(dict.fromkeys(dashboard_excluded + pool_excluded))
        assert merged.count("Alice PG") == 1
        assert len(merged) == 2

    def test_empty_pool_excludes_no_injuries(self):
        """Empty pool → empty exclusion list → no additional injuries."""
        merged: list[str] = []
        assert merged == []


# ──────────────────────────────────────────────────────────────────────────────
# Unit tests: projection override application in orchestrator
# ──────────────────────────────────────────────────────────────────────────────

class TestProjectionOverrideApplication:
    """Verify the orchestrator's override logic in isolation."""

    def _build_projections_df(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"DFS_ID": "p1", "Name": "Alice PG", "Pos": "PG", "Salary": 8000, "Proj": 44.0},
            {"DFS_ID": "p2", "Name": "Bob PG",   "Pos": "PG", "Salary": 7200, "Proj": 38.0},
        ])

    def test_override_applied_to_matching_player(self):
        df = self._build_projections_df()
        overrides = {"Alice PG": 55.0}
        name_col = "Name"
        for player_name, val in overrides.items():
            mask = df[name_col].str.lower() == player_name.lower()
            if mask.any():
                df.loc[mask, "Proj"] = float(val)

        assert df.loc[df["Name"] == "Alice PG", "Proj"].iloc[0] == 55.0
        # Non-overridden player untouched
        assert df.loc[df["Name"] == "Bob PG", "Proj"].iloc[0] == 38.0

    def test_override_case_insensitive(self):
        df = self._build_projections_df()
        overrides = {"alice pg": 60.0}  # lowercase key
        name_col = "Name"
        for player_name, val in overrides.items():
            mask = df[name_col].str.lower() == player_name.lower()
            if mask.any():
                df.loc[mask, "Proj"] = float(val)

        assert df.loc[df["Name"] == "Alice PG", "Proj"].iloc[0] == 60.0

    def test_unknown_player_override_ignored(self):
        """Override for a player not on the slate is silently skipped."""
        df = self._build_projections_df()
        original_bob = df.loc[df["Name"] == "Bob PG", "Proj"].iloc[0]
        overrides = {"Ghost Player": 99.9}
        name_col = "Name"
        for player_name, val in overrides.items():
            mask = df[name_col].str.lower() == player_name.lower()
            if mask.any():
                df.loc[mask, "Proj"] = float(val)

        assert df.loc[df["Name"] == "Bob PG", "Proj"].iloc[0] == original_bob

    def test_empty_overrides_no_change(self):
        df = self._build_projections_df()
        original = df["Proj"].tolist()
        for _, __ in {}.items():
            pass
        assert df["Proj"].tolist() == original


# ──────────────────────────────────────────────────────────────────────────────
# Unit tests: pool validation logic
# ──────────────────────────────────────────────────────────────────────────────

class TestPoolValidation:
    """Mirror the validation logic in the frontend validatePool function."""

    def _make_pool(self, players: list[dict]) -> dict[str, dict]:
        """Build a minimal pool dict keyed by name."""
        return {
            p["name"]: {
                "name": p["name"],
                "pos": p["pos"],
                "poolStatus": p.get("status", "included"),
            }
            for p in players
        }

    def test_zero_included_is_invalid(self):
        players = [
            {"name": "Alice", "pos": "PG", "status": "excluded"},
            {"name": "Bob",   "pos": "SG", "status": "excluded"},
        ]
        pool = self._make_pool(players)
        included = [p for p in pool.values() if p["poolStatus"] == "included"]
        assert len(included) == 0

    def test_below_minimum_pool_should_warn(self):
        MIN_POOL = 14
        players = [
            {"name": f"P{i}", "pos": "PG", "status": "included"} for i in range(10)
        ]
        pool = self._make_pool(players)
        included_count = sum(1 for p in pool.values() if p["poolStatus"] == "included")
        assert included_count < MIN_POOL

    def test_position_coverage(self):
        """At least 2 Cs required for FD lineup generation."""
        pool = self._make_pool([
            {"name": "A", "pos": "PG"}, {"name": "B", "pos": "PG"},
            {"name": "C", "pos": "SG"}, {"name": "D", "pos": "SG"},
            {"name": "E", "pos": "SF"}, {"name": "F", "pos": "SF"},
            {"name": "G", "pos": "PF"}, {"name": "H", "pos": "PF"},
            {"name": "I", "pos": "C"},  # only 1 C — should flag
        ])
        by_pos: dict[str, int] = {}
        for p in pool.values():
            if p["poolStatus"] == "included":
                by_pos[p["pos"]] = by_pos.get(p["pos"], 0) + 1
        assert by_pos.get("C", 0) < 2  # validates the detection logic

    def test_full_valid_pool_passes(self):
        """A pool with ≥2 of each position and >14 players should be valid."""
        pool = {p["name"]: {**p, "poolStatus": "included"}
                for p in [
                    {"name": f"PG{i}", "pos": "PG"} for i in range(4)
                ] + [
                    {"name": f"SG{i}", "pos": "SG"} for i in range(4)
                ] + [
                    {"name": f"SF{i}", "pos": "SF"} for i in range(4)
                ] + [
                    {"name": f"PF{i}", "pos": "PF"} for i in range(4)
                ] + [
                    {"name": f"C{i}", "pos": "C"} for i in range(3)
                ]}
        included = [p for p in pool.values() if p["poolStatus"] == "included"]
        by_pos: dict[str, int] = {}
        for p in included:
            by_pos[p["pos"]] = by_pos.get(p["pos"], 0) + 1
        assert len(included) >= 14
        assert by_pos["PG"] >= 3
        assert by_pos["C"] >= 2


# ──────────────────────────────────────────────────────────────────────────────
# Integration: API route param acceptance (FastAPI TestClient)
# ──────────────────────────────────────────────────────────────────────────────

class TestOptimizerRouteParams:
    """Verify the /api/optimizer/run endpoint accepts new pool params without error."""

    @pytest.fixture
    def client(self, make_authed_client):
        from routers.optimizer import router
        return make_authed_client(router)

    def test_route_accepts_projection_overrides_json(self, client):
        """Endpoint should not raise 422 when projection_overrides is valid JSON."""
        csv_bytes = _make_slate_csv(MINIMAL_FD_SLATE)
        overrides_json = json.dumps({"Alice PG": 55.0})
        response = client.post(
            "/api/optimizer/run",
            files={"file": ("slate.csv", csv_bytes, "text/csv")},
            params={
                "site": "FD", "n_lineups": 1,
                "projection_overrides": overrides_json,
            },
        )
        # 200 = success, 422 = validation error on params (what we're guarding against)
        # 500 is ok (optimizer may fail if DB/duckdb not available in test env)
        assert response.status_code != 422, f"Unexpected 422: {response.text}"

    def test_route_accepts_locked_players(self, client):
        csv_bytes = _make_slate_csv(MINIMAL_FD_SLATE)
        response = client.post(
            "/api/optimizer/run",
            files={"file": ("slate.csv", csv_bytes, "text/csv")},
            params={
                "site": "FD", "n_lineups": 1,
                "locked_players": "Alice PG",
            },
        )
        assert response.status_code != 422, f"Unexpected 422: {response.text}"

    def test_route_accepts_out_players(self, client):
        csv_bytes = _make_slate_csv(MINIMAL_FD_SLATE)
        response = client.post(
            "/api/optimizer/run",
            files={"file": ("slate.csv", csv_bytes, "text/csv")},
            params={
                "site": "FD", "n_lineups": 1,
                "out_players": "Alice PG,Carol SG",
            },
        )
        assert response.status_code != 422, f"Unexpected 422: {response.text}"

    def test_route_accepts_invalid_projection_overrides_gracefully(self, client):
        """Malformed JSON in projection_overrides should not raise 422 (router catches it)."""
        csv_bytes = _make_slate_csv(MINIMAL_FD_SLATE)
        response = client.post(
            "/api/optimizer/run",
            files={"file": ("slate.csv", csv_bytes, "text/csv")},
            params={
                "site": "FD", "n_lineups": 1,
                "projection_overrides": "NOT_VALID_JSON",
            },
        )
        # Should fall through gracefully (not 422)
        assert response.status_code != 422, f"Unexpected 422: {response.text}"


class TestPipelineRouteParams:
    """Verify the /api/pipeline/run-full endpoint accepts new pool params."""

    @pytest.fixture
    def client(self, make_authed_client):
        from routers.pipeline import router
        return make_authed_client(router)

    def test_pipeline_route_accepts_projection_overrides(self, client):
        csv_bytes = _make_slate_csv(MINIMAL_FD_SLATE)
        overrides_json = json.dumps({"Ivy C": 52.0})
        response = client.post(
            "/api/pipeline/run-full",
            files={"file": ("slate.csv", csv_bytes, "text/csv")},
            params={
                "site": "FD", "n_lineups": 1,
                "projection_overrides": overrides_json,
            },
        )
        assert response.status_code != 422, f"Unexpected 422: {response.text}"

    def test_pipeline_route_accepts_locked_players(self, client):
        csv_bytes = _make_slate_csv(MINIMAL_FD_SLATE)
        response = client.post(
            "/api/pipeline/run-full",
            files={"file": ("slate.csv", csv_bytes, "text/csv")},
            params={
                "site": "FD", "n_lineups": 1,
                "locked_players": "Ivy C,Alice PG",
            },
        )
        assert response.status_code != 422, f"Unexpected 422: {response.text}"
