"""Unit tests for the NFL projection engine and optimizer."""

import pandas as pd
import pytest

from analysis.nfl.projection_engine import NFLProjectionEngine
from analysis.nfl.optimizer import NFLOptimizer, NFLLineup
from analysis.core.schemas import ProjectionContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_slate(n_qb=2, n_rb=6, n_wr=8, n_te=3, n_dst=2, site="DK") -> pd.DataFrame:
    """Build a minimal slate DataFrame with stat columns."""
    rows = []
    salary_base = {"QB": 7000, "RB": 5500, "WR": 5000, "TE": 4500, "DST": 3500}

    def _row(pos: str, i: int) -> dict:
        sal = salary_base[pos] - i * 100
        return {
            "Name": f"{pos}{i}",
            "Position": pos,
            "Salary": sal,
            "Team": f"T{i % 4}",
            "Opp": f"O{i % 4}",
            "DFS_ID": f"{pos}{i}_id",
            # Stat columns so the engine can score them
            "PASS_YD": 280 if pos == "QB" else 0,
            "PASS_TD": 2.0 if pos == "QB" else 0,
            "PASS_INT": 0.5 if pos == "QB" else 0,
            "RUSH_YD": 80 if pos == "RB" else (10 if pos == "QB" else 0),
            "RUSH_TD": 0.6 if pos == "RB" else 0,
            "REC": 5 if pos in ("WR", "TE") else (2 if pos == "RB" else 0),
            "REC_YD": 60 if pos == "WR" else (40 if pos == "TE" else 15),
            "REC_TD": 0.4 if pos in ("WR", "TE") else 0.2,
            # DST stats
            "DST_SACK": 3 if pos == "DST" else 0,
            "DST_INT": 1 if pos == "DST" else 0,
            "DST_FR": 1 if pos == "DST" else 0,
            "DST_TD": 0 if pos == "DST" else 0,
            "DST_PA": 17 if pos == "DST" else 0,
        }

    for i in range(n_qb):
        rows.append(_row("QB", i))
    for i in range(n_rb):
        rows.append(_row("RB", i))
    for i in range(n_wr):
        rows.append(_row("WR", i))
    for i in range(n_te):
        rows.append(_row("TE", i))
    for i in range(n_dst):
        rows.append(_row("DST", i))

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# NFLProjectionEngine
# ---------------------------------------------------------------------------

class TestNFLProjectionEngine:
    def test_generates_proj_column(self):
        slate = _make_slate()
        ctx = ProjectionContext(sport="NFL", site="DK")
        engine = NFLProjectionEngine()
        result = engine.generate(slate, ctx)
        assert "Proj" in result.columns
        assert "Projection" in result.columns  # backward-compat alias

    def test_proj_equals_projection_alias(self):
        slate = _make_slate()
        ctx = ProjectionContext(sport="NFL", site="DK")
        result = NFLProjectionEngine().generate(slate, ctx)
        pd.testing.assert_series_equal(result["Proj"], result["Projection"], check_names=False)

    def test_qb_scores_nonzero(self):
        slate = _make_slate(n_qb=1)
        ctx = ProjectionContext(sport="NFL", site="DK")
        result = NFLProjectionEngine().generate(slate, ctx)
        qb_proj = result.loc[result["Position"] == "QB", "Proj"].iloc[0]
        # 280*0.04 + 2*4 + 0.5*(-1) + 10*0.1 = 11.2 + 8 - 0.5 + 1 = 19.7
        assert qb_proj > 10

    def test_dst_scores_nonzero(self):
        slate = _make_slate(n_dst=1)
        ctx = ProjectionContext(sport="NFL", site="DK")
        result = NFLProjectionEngine().generate(slate, ctx)
        dst_proj = result.loc[result["Position"] == "DST", "Proj"].iloc[0]
        # sack*1 + int*2 + fr*2 = 3 + 2 + 2 = 7; PA=17 → 0 bonus
        assert dst_proj == pytest.approx(7.0)

    def test_value_column_present(self):
        slate = _make_slate()
        ctx = ProjectionContext(sport="NFL", site="DK")
        result = NFLProjectionEngine().generate(slate, ctx)
        assert "Value" in result.columns
        assert (result["Value"] >= 0).all()

    def test_fd_half_ppr(self):
        """FD uses 0.5 REC vs DK 1.0 REC."""
        slate = _make_slate(n_wr=1)
        wr = slate[slate["Position"] == "WR"].copy()
        dk_proj = NFLProjectionEngine().generate(wr, ProjectionContext(sport="NFL", site="DK"))["Proj"].iloc[0]
        fd_proj = NFLProjectionEngine().generate(wr, ProjectionContext(sport="NFL", site="FD"))["Proj"].iloc[0]
        # DK REC contribution = 5*1.0=5; FD = 5*0.5=2.5 → DK higher
        assert dk_proj > fd_proj

    def test_column_alias_rename(self):
        """Engine should accept 'passing_yards' alias."""
        slate = pd.DataFrame([{
            "Name": "QB0",
            "Position": "QB",
            "Salary": 7000,
            "Team": "T0",
            "passing_yards": 300,
            "passing_tds": 3,
        }])
        ctx = ProjectionContext(sport="NFL", site="DK")
        result = NFLProjectionEngine().generate(slate, ctx)
        assert result["Proj"].iloc[0] > 0


# ---------------------------------------------------------------------------
# NFLOptimizer
# ---------------------------------------------------------------------------

class TestNFLOptimizer:
    def test_generates_correct_count(self):
        slate = _make_slate()
        ctx = ProjectionContext(sport="NFL", site="DK")
        proj_df = NFLProjectionEngine().generate(slate, ctx)
        opt = NFLOptimizer(site="DK", contest_type="Classic")
        lineups = opt.generate(proj_df, n_lineups=5, seed=42)
        assert len(lineups) == 5

    def test_classic_lineup_has_9_slots(self):
        slate = _make_slate()
        ctx = ProjectionContext(sport="NFL", site="DK")
        proj_df = NFLProjectionEngine().generate(slate, ctx)
        opt = NFLOptimizer(site="DK", contest_type="Classic")
        lineups = opt.generate(proj_df, n_lineups=3, seed=1)
        for lu in lineups:
            assert len(lu.slots) == 9

    def test_salary_cap_respected(self):
        slate = _make_slate()
        ctx = ProjectionContext(sport="NFL", site="DK")
        proj_df = NFLProjectionEngine().generate(slate, ctx)
        opt = NFLOptimizer(site="DK")
        lineups = opt.generate(proj_df, n_lineups=10, seed=0)
        for lu in lineups:
            assert lu.total_salary <= 50_000

    def test_lineups_sorted_by_projection_desc(self):
        slate = _make_slate()
        ctx = ProjectionContext(sport="NFL", site="DK")
        proj_df = NFLProjectionEngine().generate(slate, ctx)
        lineups = NFLOptimizer(site="DK").generate(proj_df, n_lineups=10, seed=5)
        projs = [lu.total_projection for lu in lineups]
        assert projs == sorted(projs, reverse=True)

    def test_fades_respected(self):
        slate = _make_slate()
        ctx = ProjectionContext(sport="NFL", site="DK")
        proj_df = NFLProjectionEngine().generate(slate, ctx)
        faded = proj_df["Name"].iloc[0]
        lineups = NFLOptimizer(site="DK").generate(proj_df, n_lineups=10, seed=3, fades=[faded])
        for lu in lineups:
            names = {s.name for s in lu.slots}
            assert faded not in names

    def test_showdown_lineup_has_6_slots(self):
        slate = _make_slate()
        ctx = ProjectionContext(sport="NFL", site="DK")
        proj_df = NFLProjectionEngine().generate(slate, ctx)
        opt = NFLOptimizer(site="DK", contest_type="Showdown")
        lineups = opt.generate(proj_df, n_lineups=3, seed=2)
        assert len(lineups) == 3
        for lu in lineups:
            assert len(lu.slots) == 6

    def test_empty_pool_returns_empty(self):
        opt = NFLOptimizer(site="DK")
        result = opt.generate(pd.DataFrame(), n_lineups=5)
        assert result == []

    def test_lineup_to_dict(self):
        slate = _make_slate()
        ctx = ProjectionContext(sport="NFL", site="DK")
        proj_df = NFLProjectionEngine().generate(slate, ctx)
        lineups = NFLOptimizer(site="DK").generate(proj_df, n_lineups=1, seed=0)
        d = lineups[0].to_dict()
        assert "total_salary" in d
        assert "total_projection" in d
