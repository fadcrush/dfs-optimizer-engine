from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import pandas as pd

from analysis.shared.scoring import score_nba_row
from analysis.nba.dvp import load_dvp_table, load_dvp_by_position
from analysis.nba.b2b import get_rest_multipliers, get_blowout_multipliers, get_game_total_multipliers
from analysis.nba.ownership_v2 import predict_ownership
from analysis.shared.db import get_conn

from .schemas import ProjectionContext

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Position-based CV (coefficient of variation) fallbacks when a player has
# insufficient game-log history for a reliable individual estimate.
# Derived from NBA historical DFS variance analysis; capped to [0.12, 0.45].
# ---------------------------------------------------------------------------
_POSITION_DEFAULT_CV: dict[str, float] = {
    "PG": 0.23,
    "SG": 0.22,
    "SF": 0.21,
    "PF": 0.20,
    "C":  0.19,
}
_POSITION_DEFAULT_CV_FALLBACK = 0.22   # unknown position
_CV_MIN = 0.12
_CV_MAX = 0.45

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


def _load_stddev_baseline(
    site: str,
    db_path: Path = _DEFAULT_DB,
    lookback_games: int = 20,
    min_games: int = 5,
) -> dict[str, dict]:
    """
    Query per-player rolling standard deviation from ``player_game_logs``.

    Uses the most recent ``lookback_games`` games (with > 0 minutes played)
    and requires at least ``min_games`` qualifying records before computing a
    reliable CV.  Returns a dict keyed by name slug:

        {"lebronsjames": {"cv": 0.19, "std": 9.2, "games": 18}}

    The ``cv`` (coefficient of variation = std / mean) is clamped to
    [``_CV_MIN``, ``_CV_MAX``] to prevent extreme outliers from distorting
    floor/ceiling estimates.

    Returns an empty dict on any error — callers fall back to position-based
    default CVs.
    """
    try:
        import duckdb  # noqa: F401 — presence check
    except ImportError:
        return {}

    if not db_path.exists():
        return {}

    pts_col = "dk_pts" if site.upper() == "DK" else "fd_pts"

    try:
        con = get_conn(db_path)
        sql = f"""
            WITH ranked AS (
                SELECT
                    player_name,
                    {pts_col} AS pts,
                    ROW_NUMBER() OVER (
                        PARTITION BY player_name
                        ORDER BY game_date DESC
                    ) AS rn
                FROM player_game_logs
                WHERE minutes > 0
            ),
            windowed AS (
                SELECT player_name, pts
                FROM ranked
                WHERE rn <= {lookback_games}
            )
            SELECT
                player_name,
                ROUND(STDDEV_SAMP(pts), 4)  AS std_pts,
                ROUND(AVG(pts), 4)          AS avg_pts,
                COUNT(*)                    AS games
            FROM windowed
            GROUP BY player_name
            HAVING COUNT(*) >= {min_games}
        """
        rows = con.execute(sql).fetchall()
    except Exception as exc:
        log.warning("StdDev baseline query failed: %s", exc)
        return {}

    result: dict[str, dict] = {}
    for player_name, std_pts, avg_pts, games in rows:
        std = float(std_pts or 0.0)
        avg = float(avg_pts or 0.0)
        if avg > 0:
            cv = max(_CV_MIN, min(_CV_MAX, std / avg))
        else:
            cv = _POSITION_DEFAULT_CV_FALLBACK
        slug = _slugify(player_name)
        result[slug] = {"cv": round(cv, 4), "std": round(std, 3), "games": int(games)}

    log.debug("StdDev baseline loaded: %d players (L%d)", len(result), lookback_games)
    return result


def _load_minutes_trend(
    db_path: Path = _DEFAULT_DB,
    recent_games: int = 5,
    prior_games: int = 5,
    min_recent: int = 3,
    min_prior: int = 3,
    cap: float = 0.10,
) -> dict[str, float]:
    """
    Compute per-player minutes trend ratio: (L5 avg min − L6-10 avg min) / L6-10 avg min.

    Returns a dict keyed by name slug → clamped trend ratio in [-cap, +cap].
    Players without sufficient history are absent from the dict (caller treats
    missing slugs as 0.0 — neutral).  Returns an empty dict on any error.

    ``cap`` defaults to 0.10 (±10% max projection adjustment).
    """
    try:
        import duckdb  # noqa: F401 — presence check
    except ImportError:
        return {}

    if not db_path.exists():
        return {}

    try:
        con = get_conn(db_path)
        sql = f"""
            WITH ranked AS (
                SELECT
                    player_name,
                    minutes,
                    ROW_NUMBER() OVER (
                        PARTITION BY player_name
                        ORDER BY game_date DESC
                    ) AS rn
                FROM player_game_logs
                WHERE minutes > 0
            ),
            recent AS (
                SELECT
                    player_name,
                    AVG(minutes) AS l_recent_avg
                FROM ranked
                WHERE rn <= {recent_games}
                GROUP BY player_name
                HAVING COUNT(*) >= {min_recent}
            ),
            prior AS (
                SELECT
                    player_name,
                    AVG(minutes) AS l_prior_avg
                FROM ranked
                WHERE rn BETWEEN {recent_games + 1} AND {recent_games + prior_games}
                GROUP BY player_name
                HAVING COUNT(*) >= {min_prior}
            )
            SELECT
                r.player_name,
                ROUND(
                    (r.l_recent_avg - p.l_prior_avg) / NULLIF(p.l_prior_avg, 0),
                    4
                ) AS trend_ratio
            FROM recent r
            JOIN prior p ON r.player_name = p.player_name
        """
        rows = con.execute(sql).fetchall()
    except Exception as exc:
        log.warning("Minutes trend query failed: %s", exc)
        return {}

    result: dict[str, float] = {}
    for player_name, trend_ratio in rows:
        if trend_ratio is not None:
            clamped = max(-cap, min(cap, float(trend_ratio)))
            result[_slugify(player_name)] = round(clamped, 4)

    log.debug("Minutes trend loaded: %d players", len(result))
    return result


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

    Variance model (Phase 14)
    ─────────────────────────
    Each player receives an individual ``StdDev`` derived from their actual
    rolling coefficient of variation over the last ``stddev_lookback`` games.
    Players with fewer than ``stddev_min_games`` qualifying logs fall back to
    a position-adjusted default CV.  ``Floor`` and ``Ceiling`` are then:

        Floor   = (Proj − 1.0 × StdDev).clip(lower=0)
        Ceiling = Proj + 1.5 × StdDev
    """

    variance_pct: float = 0.18          # kept as fallback; unused when stddev data available
    gl_lookback: int = 10               # games for the L10 mean baseline
    stddev_lookback: int = 20           # games for the per-player std-dev estimate
    stddev_min_games: int = 5           # minimum games before using individual CV
    gl_db_path: Path = field(default_factory=lambda: _DEFAULT_DB)
    dvp_lookback_days: int = 30         # days of game logs used for DvP calculation
    dvp_enabled: bool = True            # set False to disable DvP for clean A/B testing
    b2b_enabled: bool = True            # set False to disable B2B/rest-days for A/B testing
    blowout_enabled: bool = True        # set False to disable blowout risk for A/B testing
    minutes_trend_enabled: bool = True  # set False to disable minutes-trend layer
    game_total_enabled: bool = True     # set False to disable game O/U adjustment
    ownership_enabled: bool = True      # set False to skip ownership estimation
    contest_type: str = "gpp"          # "gpp" | "cash" | "double_up" | "winner_take_all"

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

        # Load per-player standard-deviation baseline (fails gracefully to {})
        stddev_baseline = _load_stddev_baseline(
            context.site, self.gl_db_path, self.stddev_lookback, self.stddev_min_games
        )

        # Load minutes-trend baseline (fails gracefully to empty dict → neutral 0.0)
        mt_baseline: dict[str, float] = {}
        if self.minutes_trend_enabled:
            mt_baseline = _load_minutes_trend(self.gl_db_path)

        # Load Defense-vs-Player table (fails gracefully to empty dict → neutral)
        dvp_table: dict[str, float] = {}
        pos_dvp_table: dict[str, dict[str, float]] = {}
        if self.dvp_enabled:
            dvp_table = load_dvp_table(
                site=context.site,
                db_path=self.gl_db_path,
                lookback_days=self.dvp_lookback_days,
            )
            # Position-specific DvP uses the same DB for player_positions
            pos_dvp_table = load_dvp_by_position(
                site=context.site,
                db_path=self.gl_db_path,
                game_logs_db_path=None,  # same DB as team-level; falls back gracefully
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
        # Use position-specific DvP when available (more granular); fall back
        # to team-level DvP, then neutral (1.0) when no data exists.
        opp_col = next((c for c in ["Opp", "opp", "Opponent"] if c in df.columns), None)
        pos_col_dvp = next((c for c in ["Position", "Pos", "position"] if c in df.columns), None)
        if dvp_table and opp_col:
            if pos_dvp_table and pos_col_dvp:
                # Per-player: look up (position, opponent) → position-specific mult
                # Fall back to team-level if no position bucket, then 1.0
                def _pos_dvp_mult(row: pd.Series) -> float:
                    team = str(row[opp_col]).strip()
                    pos  = str(row[pos_col_dvp]).strip()
                    pos_dict = pos_dvp_table.get(pos, {})
                    return pos_dict.get(team, dvp_table.get(team, 1.0))

                dvp_multipliers = df.apply(_pos_dvp_mult, axis=1)
                log.info("Position-specific DvP applied")
            else:
                # Fall back to team-level DvP for all players
                dvp_multipliers = df[opp_col].map(
                    lambda t: dvp_table.get(str(t).strip(), 1.0)
                )
                log.info("Team-level DvP applied (no position data)")

            df["DvP"] = dvp_multipliers.round(4)
            df["Proj"] = (df["Proj"] * dvp_multipliers).round(4)
            n_dvp_applied = int((dvp_multipliers != 1.0).sum())
            log.info("DvP applied to %d/%d players", n_dvp_applied, len(df))
        else:
            df["DvP"] = 1.0

        # ── Layer 5: B2B / rest-days adjustment ───────────────────────────────
        # Look up each player's Team in the rest-days table and apply a fatigue
        # or freshness multiplier.  Neutral (1.0) for any team not in the table.
        team_col = next((c for c in ["Team", "team", "TEAM"] if c in df.columns), None)
        if self.b2b_enabled and team_col:
            rest_mults = get_rest_multipliers(
                slate_date=context.slate_date,
                db_path=self.gl_db_path,
            )
            if rest_mults:
                rest_series = df[team_col].map(
                    lambda t: rest_mults.get(str(t).strip().upper(), 1.0)
                )
                df["Rest"] = rest_series.round(4)
                df["Proj"] = (df["Proj"] * rest_series).round(4)
                n_rest_applied = int((rest_series != 1.0).sum())
                log.info("Rest-days applied to %d/%d players", n_rest_applied, len(df))
            else:
                df["Rest"] = 1.0
        else:
            df["Rest"] = 1.0

        # ── Layer 6: Blowout risk adjustment ──────────────────────────────────
        # Apply a spread-based penalty for heavy underdogs.  Uses Vegas data
        # from context.vegas if available, otherwise falls back to a live fetch
        # via TheOddsAPIClient (gracefully returns {} when no API key is set).
        if self.blowout_enabled and team_col:
            # Prefer pre-fetched Vegas data from the request context
            vegas_totals: dict[str, dict] = {}
            if context.vegas and all(
                isinstance(v, dict) and "spread" in v
                for v in context.vegas.values()
            ):
                vegas_totals = context.vegas  # type: ignore[assignment]
            else:
                try:
                    from analysis.shared.vegas_enricher import _fetch_team_totals  # noqa: PLC0415
                    vegas_totals = _fetch_team_totals("NBA")
                except Exception as exc:
                    log.debug("Blowout: Vegas fetch failed: %s", exc)

            blowout_mults = get_blowout_multipliers(vegas_totals)
            if blowout_mults:
                blowout_series = df[team_col].map(
                    lambda t: blowout_mults.get(str(t).strip().upper(), 1.0)
                )
                df["Blowout"] = blowout_series.round(4)
                df["Proj"] = (df["Proj"] * blowout_series).round(4)
                n_blowout_applied = int((blowout_series != 1.0).sum())
                log.info("Blowout risk applied to %d/%d players", n_blowout_applied, len(df))
            else:
                df["Blowout"] = 1.0
        else:
            df["Blowout"] = 1.0

        # ── Layer 7: Minutes trend adjustment ──────────────────────────────────────
        # Signals role expansion (positive) or contraction (negative) based on the
        # difference between a player's L5 avg minutes and their L6-10 avg minutes.
        # Trend ratio is clamped to ±10% (cap set in _load_minutes_trend).
        name_col_mt = next((c for c in ["Name", "name"] if c in df.columns), None)
        if self.minutes_trend_enabled and mt_baseline and name_col_mt:
            mt_series = df[name_col_mt].map(
                lambda n: mt_baseline.get(_slugify(str(n)), 0.0)
            )
            df["MinutesTrend"] = mt_series.round(4)
            df["Proj"] = (df["Proj"] * (1.0 + mt_series)).round(4)
            n_mt = int((mt_series != 0.0).sum())
            log.info("Minutes trend applied to %d/%d players", n_mt, len(df))
        else:
            df["MinutesTrend"] = 0.0

        # ── Layer 8: Game over/under (game-total) adjustment ────────────────────
        # Players in high-scoring game environments (large O/U) get a modest
        # projection boost; defensive slog games get a small reduction.
        # Uses same vegas_totals dict as Layer 6 (already fetched above if
        # blowout_enabled was set).  Re-fetches only if blowout was disabled.
        if self.game_total_enabled and team_col:
            gt_vegas: dict[str, dict] = {}
            if context.vegas and all(
                isinstance(v, dict) and ("total" in v or "game_total" in v)
                for v in context.vegas.values()
            ):
                gt_vegas = context.vegas  # type: ignore[assignment]
            else:
                try:
                    from analysis.shared.vegas_enricher import _fetch_team_totals  # noqa: PLC0415
                    gt_vegas = _fetch_team_totals("NBA")
                except Exception as exc:
                    log.debug("GameTotal: Vegas fetch failed: %s", exc)

            gt_mults = get_game_total_multipliers(gt_vegas)
            if gt_mults:
                gt_series = df[team_col].map(
                    lambda t: gt_mults.get(str(t).strip().upper(), 1.0)
                )
                df["GameTotal"] = gt_series.round(5)
                df["Proj"] = (df["Proj"] * gt_series).round(4)
                n_gt = int((gt_series != 1.0).sum())
                log.info("Game-total adjustment applied to %d/%d players", n_gt, len(df))
            else:
                df["GameTotal"] = 1.0
        else:
            df["GameTotal"] = 1.0

        # ── Per-player variance model ──────────────────────────────────────────
        # Prefer individual CV from rolling game logs; fall back to position CV.
        # StdDev = Proj × CV
        # Floor   = (Proj − 1.0 × StdDev).clip(lower=0)
        # Ceiling = Proj + 1.5 × StdDev
        pos_col = next((c for c in ["Pos", "pos", "Position"] if c in df.columns), None)

        std_devs: list[float] = []
        for idx, row in df.iterrows():
            slug = _slugify(str(row.get("Name", "")))
            sd_entry = stddev_baseline.get(slug)
            if sd_entry:
                cv = sd_entry["cv"]
            else:
                raw_pos = str(row.get(pos_col, "") if pos_col else "")
                pos = raw_pos.split("/")[0].strip().upper()
                cv = _POSITION_DEFAULT_CV.get(pos, _POSITION_DEFAULT_CV_FALLBACK)
            proj_val = float(df.at[idx, "Proj"])
            std_devs.append(round(proj_val * cv, 3))

        df["StdDev"] = pd.Series(std_devs, index=df.index, dtype=float)
        df["Floor"] = (df["Proj"] - df["StdDev"]).clip(lower=0).round(4)
        df["Ceiling"] = (df["Proj"] + 1.5 * df["StdDev"]).round(4)
        df["Value"] = (df["Proj"] / (df["Salary"] / 1000.0).replace(0, pd.NA)).fillna(0.0)

        # ── Ownership estimation ──────────────────────────────────────────────
        # Uses calibrated GBR model when available; falls back to percentile-rank
        # heuristic.  Populates Own, Own_Est, own_source.
        if self.ownership_enabled:
            try:
                df = predict_ownership(
                    df,
                    sport="NBA",
                    site=context.site,
                    contest_type=self.contest_type,
                )
            except Exception as exc:
                log.warning("Ownership prediction failed: %s", exc)
                if "Own" not in df.columns:
                    df["Own"] = 0.0
                df["own_source"] = "error"
        else:
            if "Own" not in df.columns:
                df["Own"] = 0.0
            df["own_source"] = "disabled"

        # ── Leverage score (§6.3) ─────────────────────────────────────────────
        # Leverage = Proj / max(Own, 0.5)
        # High projection + low ownership = high leverage (contrarian GPP edge).
        # Capped at 30 to prevent division blow-up on near-zero ownership players.
        safe_own = df["Own"].clip(lower=0.5)
        df["Leverage"] = (df["Proj"] / safe_own).round(3).clip(upper=30.0)

        n_individual_cv = sum(
            1 for _, row in df.iterrows()
            if _slugify(str(row.get("Name", ""))) in stddev_baseline
        )
        n_base = sum(1 for v in scored_projection if v > 0 and has_base)
        n_gl = sum(1 for gl, base in zip(gl_l10_values, scored_projection)
                   if gl > 0 and (not has_base or float(0) == base))
        log.info(
            "Projections built — Base_Proj: %d, GL_L10: %d, box: %d  (total %d players) "
            "— StdDev: %d individual / %d position fallback",
            n_base, n_gl, has_box, len(df),
            n_individual_cv, len(df) - n_individual_cv,
        )

        out_cols = ["DFS_ID", "Raw_DFS_ID", "Name", "Team", "Opp", "Pos", "Salary",
                    "Proj", "GL_L10", "DvP", "Rest", "Blowout", "MinutesTrend", "GameTotal",
                    "StdDev", "Floor", "Ceiling",
                    "Own", "Own_Est", "own_source", "Leverage",
                    "Value", "InjuryStatus"]
        return df[[c for c in out_cols if c in df.columns]]
