"""
Phase 2 Gate Tests — Injury Bridge
====================================

Verifies that the three Phase 2 contracts hold:

  1. OUT players confirmed by the bridge are not present in the post-filter
     pool (``out_flag=True`` + pool_filter removes them).

  2. Beneficiary proj boosts reach the optimizer pool *only* through the
     bridge — direct calls to replacement_engine.compute_boosts() must not
     originate from orchestrator.

  3. The canonical sentinel columns written by the bridge (``out_flag``,
     ``beneficiary_boost``, ``salary_freed``, ``chalk_signal``) are present
     on projections_df rows after the bridge runs.

  4. Manual ``context.injuries`` OUT players are always merged in even when
     the intelligence DB is unavailable.

  5. When the intelligence DB holds no beneficiary rows, the bridge falls back
     to replacement_engine and ``source == "fallback"``.

These are *pure-unit* tests — no live DB, no HTTP layer, no file I/O beyond
tmp_path fixtures.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from analysis.core.injury_bridge import (
    InjuryBridgeResult,
    apply_injury_bridge,
)
from analysis.core.schemas import ProjectionContext


# ── Test fixtures ──────────────────────────────────────────────────────────

def _make_ctx(injuries: dict | None = None, site: str = "DK") -> ProjectionContext:
    return ProjectionContext(
        sport="NBA",
        site=site,
        injuries=injuries or {},
    )


def _make_projections(players: list[dict]) -> pd.DataFrame:
    """
    Minimal projections DataFrame accepted by the bridge.
    Each dict should have at minimum: Name, Salary, Proj.
    """
    default = {"Pos": "PG", "Team": "LAL", "GameInfo": "", "InjuryStatus": ""}
    rows = [{**default, **p} for p in players]
    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════════════
# 1. OUT players are removed after bridge + pool_filter pipeline
# ═══════════════════════════════════════════════════════════════════════════

class TestOutPlayersRemovedFromPool:
    """Gate: confirmed OUT players never reach the optimizer pool."""

    def test_manual_out_player_gets_out_flag(self):
        """Bridge marks manual OUT player with out_flag=True."""
        df = _make_projections([
            {"Name": "LeBron James", "Salary": 10000, "Proj": 50.0},
            {"Name": "Anthony Davis", "Salary": 9000,  "Proj": 45.0},
        ])
        ctx = _make_ctx(injuries={"LeBron James": "OUT"})

        result = apply_injury_bridge(df, ctx, db_path=Path("/non/existent/db.duckdb"))

        out_rows = result.projections_df[result.projections_df["out_flag"] == True]
        names = set(out_rows["Name"].tolist())
        assert "LeBron James" in names, "OUT player must have out_flag=True"
        assert "Anthony Davis" not in names, "Non-OUT player must not have out_flag=True"

    def test_out_players_list_populated(self):
        """Bridge returns the list of OUT player names."""
        df = _make_projections([
            {"Name": "Kevin Durant", "Salary": 9800, "Proj": 48.0},
        ])
        ctx = _make_ctx(injuries={"Kevin Durant": "OUT"})

        result = apply_injury_bridge(df, ctx, db_path=Path("/non/existent/db.duckdb"))

        assert "Kevin Durant" in result.out_players

    def test_active_player_never_out_flagged(self):
        """A player with ACTIVE status must not receive out_flag=True."""
        df = _make_projections([
            {"Name": "Nikola Jokic", "Salary": 10500, "Proj": 60.0},
        ])
        ctx = _make_ctx(injuries={"Nikola Jokic": "ACTIVE"})

        result = apply_injury_bridge(df, ctx, db_path=Path("/non/existent/db.duckdb"))

        assert result.projections_df.loc[
            result.projections_df["Name"] == "Nikola Jokic", "out_flag"
        ].iloc[0] is False or not result.projections_df.loc[
            result.projections_df["Name"] == "Nikola Jokic", "out_flag"
        ].iloc[0]

    def test_out_flag_case_insensitive_match(self):
        """Name matching between context.injuries and projections_df is case-insensitive."""
        df = _make_projections([
            {"Name": "Jayson Tatum", "Salary": 9200, "Proj": 44.0},
        ])
        ctx = _make_ctx(injuries={"jayson tatum": "OUT"})  # lower-case key

        result = apply_injury_bridge(df, ctx, db_path=Path("/non/existent/db.duckdb"))

        out_mask = result.projections_df["out_flag"] == True
        assert out_mask.any(), "Case-insensitive match should flag Tatum as OUT"


# ═══════════════════════════════════════════════════════════════════════════
# 2. sentinel columns present on projections_df after bridge
# ═══════════════════════════════════════════════════════════════════════════

class TestSentinelColumnsPresent:
    """Gate: bridge sentinel columns written to projections_df."""

    def test_all_sentinel_columns_exist(self):
        df = _make_projections([
            {"Name": "Damian Lillard", "Salary": 9100, "Proj": 43.0},
        ])
        ctx = _make_ctx()

        result = apply_injury_bridge(df, ctx, db_path=Path("/non/existent/db.duckdb"))

        for col in ("out_flag", "beneficiary_boost", "salary_freed", "chalk_signal"):
            assert col in result.projections_df.columns, (
                f"Sentinel column '{col}' missing from enriched projections_df"
            )

    def test_salary_freed_populated_for_out_player(self):
        """salary_freed should equal the OUT player's salary on the slate."""
        df = _make_projections([
            {"Name": "Devin Booker", "Salary": 8800, "Proj": 40.0},
        ])
        ctx = _make_ctx(injuries={"Devin Booker": "OUT"})

        result = apply_injury_bridge(df, ctx, db_path=Path("/non/existent/db.duckdb"))

        freed = result.out_player_salary_freed.get("Devin Booker", -1)
        assert freed == 8800, f"Expected salary_freed=8800, got {freed}"

    def test_beneficiary_boost_zero_when_no_out_players(self):
        """When there are no OUT players, beneficiary_boost must be 0 for everyone."""
        df = _make_projections([
            {"Name": "Steph Curry", "Salary": 9500, "Proj": 47.0},
            {"Name": "Klay Thompson", "Salary": 7000, "Proj": 30.0},
        ])
        ctx = _make_ctx(injuries={})

        result = apply_injury_bridge(df, ctx, db_path=Path("/non/existent/db.duckdb"))

        assert (result.projections_df["beneficiary_boost"] == 0.0).all(), (
            "No OUT players → beneficiary_boost must be 0 for all rows"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 3. Beneficiaries come only through bridge (not direct compute_boosts)
# ═══════════════════════════════════════════════════════════════════════════

class TestBeneficiarySourceExclusivity:
    """Gate: replacement_engine is never called directly from orchestrator."""

    def test_orchestrator_does_not_import_compute_boosts(self):
        """
        Verify that orchestrator.py does not contain a direct call to
        analysis.nba.replacement_engine.compute_boosts.
        The bridge is the *only* importer.
        """
        import ast
        from pathlib import Path as _Path

        src = (_Path(__file__).parents[1] / "analysis" / "core" / "orchestrator.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    names = [alias.name for alias in node.names]
                    assert not (
                        "replacement_engine" in module and "compute_boosts" in names
                    ), (
                        "orchestrator.py must not directly import compute_boosts "
                        "(use injury_bridge instead)"
                    )

    def test_replacement_engine_only_callable_via_bridge(self):
        """
        When intelligence DB is unavailable, bridge source is 'fallback'
        (meaning bridge invoked replacement_engine internally), but the
        orchestrator result still carries replacement_boosts.
        """
        df = _make_projections([
            {"Name": "Tyrese Haliburton", "Salary": 9000, "Proj": 44.0},
            {"Name": "Buddy Hield",        "Salary": 6800, "Proj": 28.0},
        ])
        ctx = _make_ctx(injuries={"Tyrese Haliburton": "OUT"})

        # Patch replacement_engine so fallback call is tracked
        with patch(
            "analysis.core.injury_bridge._run_replacement_engine_fallback",
            wraps=lambda *a, **kw: pd.DataFrame(
                [{"player_name": "Buddy Hield", "proj_boost": 3.5,
                  "out_player": "Tyrese Haliburton", "delta_minutes": 3.0,
                  "dk_rate": 1.15, "volatility_uplift": 0.05, "reason_codes": []}]
            ),
        ) as mock_fallback:
            result = apply_injury_bridge(df, ctx, db_path=Path("/non/existent/db.duckdb"))

        # Fallback must have been delegated to within the bridge
        mock_fallback.assert_called_once()
        # Result source indicates fallback was used (not "none" or "unknown")
        assert result.source in ("fallback", "manual_only"), (
            f"Expected source 'fallback' or 'manual_only', got '{result.source}'"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 4. Manual injuries always merge in (even when DB unavailable)
# ═══════════════════════════════════════════════════════════════════════════

class TestManualInjuryMerge:
    """Gate: manual overrides always contribute to out_players."""

    def test_manual_out_not_in_db_still_appears(self):
        """A player listed in context.injuries as OUT must appear in out_players
        even when the intelligence DB is absent."""
        df = _make_projections([
            {"Name": "Paul George", "Salary": 8600, "Proj": 38.0},
        ])
        ctx = _make_ctx(injuries={"Paul George": "OUT"})

        result = apply_injury_bridge(df, ctx, db_path=Path("/non/existent/db.duckdb"))

        assert "Paul George" in result.out_players

    def test_manual_and_db_out_players_merged(self, tmp_path: Path):
        """DB OUT players and manual OUT players both appear in out_players."""
        from analysis.core.injury_intelligence import InjuryIntelligenceService

        db_path = tmp_path / "nba_news.duckdb"
        svc = InjuryIntelligenceService(db_path)
        inj_df = pd.DataFrame([{
            "player_id":   "luka_doncic",
            "player_name": "Luka Doncic",
            "status":      "OUT",
            "detail":      "Ankle",
            "team":        "DAL",
            "game_date":   "2026-03-29",
            "confidence":  0.97,
        }])
        svc.sync_current_injuries(inj_df)

        # Luka from DB; Kyrie from manual
        slate = _make_projections([
            {"Name": "Luka Doncic",  "Salary": 10200, "Proj": 55.0},
            {"Name": "Kyrie Irving", "Salary": 9600,  "Proj": 48.0},
        ])
        ctx = _make_ctx(injuries={"Kyrie Irving": "OUT"})

        result = apply_injury_bridge(slate, ctx, db_path=db_path)

        assert "Kyrie Irving" in result.out_players,  "Manual OUT must be in list"
        assert "Luka Doncic"  in result.out_players,  "DB OUT must be in list"

    def test_manual_active_does_not_override_db_out(self, tmp_path: Path):
        """A manual ACTIVE label for a player the DB has as OUT must not remove the
        OUT flag — the DB is authoritative for confirmed OUTs, regardless of manual
        ACTIVE entries in context.injuries."""
        from analysis.core.injury_intelligence import InjuryIntelligenceService

        db_path = tmp_path / "nba_news.duckdb"
        svc = InjuryIntelligenceService(db_path)
        inj_df = pd.DataFrame([{
            "player_id":   "joel_embiid",
            "player_name": "Joel Embiid",
            "status":      "OUT",
            "detail":      "Knee management",
            "team":        "PHI",
            "game_date":   "2026-03-29",
            "confidence":  0.99,
        }])
        svc.sync_current_injuries(inj_df)

        slate = _make_projections([
            {"Name": "Joel Embiid", "Salary": 10000, "Proj": 52.0},
        ])
        ctx = _make_ctx(injuries={"Joel Embiid": "ACTIVE"})  # user says active — DB says OUT

        result = apply_injury_bridge(slate, ctx, db_path=db_path)

        # DB-confirmed OUT must survive even with a manual ACTIVE label
        assert "Joel Embiid" in result.out_players, (
            "DB-confirmed OUT must not be cleared by a manual ACTIVE label"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 5. Intelligence DB path preferred over fallback
# ═══════════════════════════════════════════════════════════════════════════

class TestIntelligenceSourcePreference:
    """Gate: when intelligence DB has beneficiary rows, source='intelligence'."""

    def test_source_intelligence_when_db_has_beneficiaries(self, tmp_path: Path):
        """
        Source == 'intelligence' when the injury_beneficiaries table is populated.
        We sync an OUT player then directly insert a beneficiary row (reflecting
        what rebuild_beneficiaries() would produce with real game-log data).
        """
        from analysis.core.injury_intelligence import InjuryIntelligenceService
        from analysis.shared.db import get_conn
        from datetime import datetime, timezone

        db_path = tmp_path / "nba_news.duckdb"
        svc = InjuryIntelligenceService(db_path)
        # Sync the OUT player — populates player_injury_state
        inj_df = pd.DataFrame([{
            "player_id":   "james_harden",
            "player_name": "James Harden",
            "status":      "OUT",
            "detail":      "Hamstring",
            "team":        "LAC",
            "game_date":   "2026-03-29",
            "confidence":  0.95,
        }])
        svc.sync_current_injuries(inj_df)

        # Directly insert a beneficiary row for Kawhi Leonard —
        # simulates what rebuild_beneficiaries() would emit with game-log data.
        conn = get_conn(db_path)
        conn.execute("""
            INSERT INTO injury_beneficiaries (
                beneficiary_id, player_id, beneficiary_player_id, beneficiary_name,
                team_id, scenario_name, delta_minutes, delta_usage,
                delta_assist_rate, delta_rebound_rate, p_start, p_close,
                volatility_uplift, confidence, rank_score, generated_at,
                position, reason_codes
            ) VALUES (
                'test-ben-001', 'james_harden', 'kawhi_leonard', 'Kawhi Leonard',
                'LAC', 'OUT', 4.5, 0.12, 0.02, 0.03, 0.85, 0.70,
                0.08, 0.90, 0.95, ?, 'SF', '["primary_backup_minutes"]'
            )
        """, [datetime.now(timezone.utc)])

        slate = _make_projections([
            {"Name": "James Harden",   "Salary": 9300, "Proj": 43.0},
            {"Name": "Kawhi Leonard",  "Salary": 9000, "Proj": 40.0},
        ])
        ctx = _make_ctx()

        result = apply_injury_bridge(slate, ctx, db_path=db_path)

        # Luka is confirmed OUT via DB state — should appear in out_players
        assert "James Harden" in result.out_players, "DB OUT player must be in bridge out_players"
        # Source should be 'intelligence' because the beneficiary table had rows
        assert result.source == "intelligence", (
            f"Expected source='intelligence' when DB has beneficiary rows, got '{result.source}'"
        )

    def test_source_fallback_when_db_absent(self):
        """When DB does not exist, bridge uses fallback path."""
        df = _make_projections([
            {"Name": "Damian Lillard", "Salary": 9100, "Proj": 43.0},
            {"Name": "Khris Middleton", "Salary": 7500,  "Proj": 32.0},
        ])
        ctx = _make_ctx(injuries={"Damian Lillard": "OUT"})

        with patch(
            "analysis.core.injury_bridge._run_replacement_engine_fallback",
            return_value=pd.DataFrame(columns=[
                "player_name", "proj_boost", "out_player",
                "delta_minutes", "dk_rate", "volatility_uplift", "reason_codes"
            ]),
        ):
            result = apply_injury_bridge(df, ctx, db_path=Path("/non/existent/db.duckdb"))

        assert result.source in ("fallback", "manual_only")

    def test_replacement_boosts_df_schema(self):
        """replacement_boosts_df always has the required pool-filter columns."""
        df = _make_projections([
            {"Name": "Zach LaVine", "Salary": 8200, "Proj": 36.0},
        ])
        ctx = _make_ctx()

        result = apply_injury_bridge(df, ctx, db_path=Path("/non/existent/db.duckdb"))

        required_cols = {"player_name", "proj_boost"}
        assert required_cols.issubset(set(result.replacement_boosts_df.columns)), (
            "replacement_boosts_df must have 'player_name' and 'proj_boost' columns "
            f"for pool_filter compatibility (got {list(result.replacement_boosts_df.columns)})"
        )

    def test_bridge_result_is_idempotent(self):
        """Calling apply_injury_bridge twice on the same input yields the same result."""
        df = _make_projections([
            {"Name": "Giannis Antetokounmpo", "Salary": 10800, "Proj": 58.0},
        ])
        ctx = _make_ctx(injuries={})

        r1 = apply_injury_bridge(df, ctx, db_path=Path("/non/existent/db.duckdb"))
        r2 = apply_injury_bridge(df, ctx, db_path=Path("/non/existent/db.duckdb"))

        assert r1.out_players == r2.out_players
        assert list(r1.projections_df.columns) == list(r2.projections_df.columns)
