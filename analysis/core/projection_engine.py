from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import pandas as pd

from analysis.shared.scoring import score_nba_row
from analysis.nba.dvp import load_dvp_table
from analysis.shared.db import get_conn

from .schemas import ProjectionContext

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default path to the game-log DuckDB (relative to this file, two levels up)
# ---------------------------------------------------------------------------
_DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "dfs_edge.duckdb"


def _slugify(name: str) -> str:
    """Lowercase, normalize unicode accents, strip non-alphanumeric — fuzzy name matching."""
    if not isinstance(name, str):
        return ""
    import unicodedata
    # Decompose accented chars (ć → c + combining cedilla) then encode to ASCII
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", normalized.lower())


def _load_game_log_baseline(
    site: str,
    db_path: Path = _DEFAULT_DB,
    lookback_games: int = 10,
) -> dict[str, dict]:
    """
    Query the last ``lookback_games`` per player from ``player_game_logs``
    and return a dict keyed by name slug:

        {"lebronsjames": {"dk": 48.5, "fd": 42.1, "min": 35.2, "games": 10}}

    Returns an empty dict on any error (engine degrades gracefully to box
    score or Base_Proj fallback).
    """
    try:
        import duckdb  # optional at module level — only needed at runtime
    except ImportError:
        log.warning("duckdb not installed — game log baseline unavailable")
        return {}

    if not db_path.exists():
        log.warning("dfs_edge.duckdb not found at %s — game log baseline skipped", db_path)
        return {}

    try:
        con = get_conn(db_path)
        sql = f"""
            WITH ranked AS (
                SELECT
                    player_name,
                    dk_pts,
                    fd_pts,
                    minutes,
                    ROW_NUMBER() OVER (
                        PARTITION BY player_name
                        ORDER BY game_date DESC
                    ) AS rn
                FROM player_game_logs
                WHERE minutes > 0
            )
            SELECT
                player_name,
                ROUND(AVG(dk_pts), 3)  AS avg_dk,
                ROUND(AVG(fd_pts), 3)  AS avg_fd,
                ROUND(AVG(minutes), 2) AS avg_min,
                COUNT(*)               AS games
            FROM ranked
            WHERE rn <= {lookback_games}
            GROUP BY player_name
        """
        rows = con.execute(sql).fetchall()
    except Exception as exc:
        log.warning("Game log baseline query failed: %s", exc)
        return {}

    baseline: dict[str, dict] = {}
    for player_name, avg_dk, avg_fd, avg_min, games in rows:
        slug = _slugify(player_name)
        baseline[slug] = {
            "name": player_name,
            "dk": float(avg_dk or 0),
            "fd": float(avg_fd or 0),
            "min": float(avg_min or 0),
            "games": int(games),
        }
    log.debug("Game log baseline loaded: %d players (L%d)", len(baseline), lookback_games)
    return baseline


class ProjectionEngine(Protocol):
    def generate(self, slate_df: pd.DataFrame, context: ProjectionContext) -> pd.DataFrame:
        ...


@dataclass
class CanonicalNBAProjectionEngine:
    """
    Canonical projection engine used by backend + orchestration.

    Projection priority per player
    ───────────────────────────────
    1. ``Base_Proj > 0``  — explicit manual / imported projection (CSV column).
    2. Game-log L10 avg  — last ``gl_lookback`` games from ``dfs_edge.duckdb``.
    3. Box-score scoring — DK/FD formula applied to stat columns in the slate.
    4. Zero             — no signal; player should be excluded by the pool filter.
    """

    variance_pct: float = 0.18
    gl_lookback: int = 10  # number of past games for the L10 average
    gl_db_path: Path = field(default_factory=lambda: _DEFAULT_DB)
    dvp_lookback_days: int = 30  # days of game logs used for DvP calculation
    dvp_enabled: bool = True      # set False to disable DvP for clean A/B testing

    def generate(self, slate_df: pd.DataFrame, context: ProjectionContext) -> pd.DataFrame:
        if context.sport != "NBA":
            raise ValueError(f"CanonicalNBAProjectionEngine supports NBA only. Got: {context.sport}")

        df = slate_df.copy()
        has_base = "Base_Proj" in df.columns
        has_box = {"PTS", "TRB", "AST", "STL", "BLK", "TOV"}.issubset(set(df.columns))

        # Load game-log baseline once (fails gracefully to empty dict)
        gl_baseline = _load_game_log_baseline(
            context.site, self.gl_db_path, self.gl_lookback
        )
        site_key = "dk" if context.site == "DK" else "fd"

        # Load Defense-vs-Player table (fails gracefully to empty dict → neutral)
        dvp_table: dict[str, float] = {}
        if self.dvp_enabled:
            dvp_table = load_dvp_table(
                site=context.site,
                db_path=self.gl_db_path,
                lookback_days=self.dvp_lookback_days,
            )

        scored_projection: list[float] = []
        gl_l10_values: list[float] = []

        for _, row in df.iterrows():
            # Layer 1: explicit Base_Proj from CSV
            base_proj = float(row.get("Base_Proj", 0.0)) if has_base else 0.0

            # Layer 2 lookup — game-log L10 average
            slug = _slugify(str(row.get("Name", "")))
            gl_entry = gl_baseline.get(slug)
            gl_avg = float(gl_entry[site_key]) if gl_entry else 0.0
            gl_l10_values.append(round(gl_avg, 2))

            if base_proj > 0:
                # Layer 1 wins — keep the user-supplied value
                scored_projection.append(base_proj)
            elif gl_avg > 0:
                # Layer 2: use game-log L10 average
                scored_projection.append(gl_avg)
            elif has_box:
                # Layer 3: derive from box-score stat columns in the slate
                scored_projection.append(score_nba_row(context.site, row))
            else:
                # No signal — zero; pool filter will drop if below floor
                scored_projection.append(0.0)

        df["Proj"] = pd.Series(scored_projection, index=df.index, dtype=float)
        df["GL_L10"] = pd.Series(gl_l10_values, index=df.index, dtype=float)

        # ── Layer 4: Defense-vs-Player adjustment ─────────────────────────────
        # Look up each player's Opp (opponent team) in the DvP table and apply
        # the multiplier to their projection.  Neutral (1.0) if no data.
        opp_col = next((c for c in ["Opp", "opp", "Opponent"] if c in df.columns), None)
        if dvp_table and opp_col:
            dvp_multipliers = df[opp_col].map(lambda t: dvp_table.get(str(t).strip(), 1.0))
            df["DvP"] = dvp_multipliers.round(4)
            df["Proj"] = (df["Proj"] * dvp_multipliers).round(4)
            n_dvp_applied = int((dvp_multipliers != 1.0).sum())
            log.info("DvP applied to %d/%d players", n_dvp_applied, len(df))
        else:
            df["DvP"] = 1.0

        df["Floor"] = (df["Proj"] * (1.0 - self.variance_pct)).clip(lower=0)
        df["Ceiling"] = df["Proj"] * (1.0 + self.variance_pct * 1.5)
        df["Value"] = (df["Proj"] / (df["Salary"] / 1000.0).replace(0, pd.NA)).fillna(0.0)

        n_base = sum(1 for v in scored_projection if v > 0 and has_base)
        n_gl = sum(1 for gl, base in zip(gl_l10_values, scored_projection)
                   if gl > 0 and (not has_base or float(0) == base))
        log.info(
            "Projections built — Base_Proj: %d, GL_L10: %d, box: %d  (total %d players)",
            n_base, n_gl, has_box, len(df),
        )

        out_cols = ["DFS_ID", "Raw_DFS_ID", "Name", "Team", "Opp", "Pos", "Salary",
                    "Proj", "GL_L10", "DvP", "Floor", "Ceiling", "Own", "Value",
                    "InjuryStatus"]
        return df[[c for c in out_cols if c in df.columns]]
