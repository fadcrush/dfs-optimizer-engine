from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import pandas as pd

from analysis.shared.scoring import score_nba_row
from analysis.nba.dvp import load_dvp_table, load_dvp_by_position
from analysis.nba.b2b import (
    get_rest_multipliers,
    get_blowout_multipliers,
    get_game_total_multipliers,
    get_injury_boost_multipliers,
    get_scenario_weighted_injury_multipliers,
)
from analysis.nba.ownership_v2 import predict_ownership
from analysis.shared.db import get_conn
from .simulation import SimulationConfig, simulate_player_outcomes, summarize_player_sims

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
        # Recompute fantasy scores from raw stat columns using canonical formulas
        # so stale or missing stored dk_pts/fd_pts never corrupt the baseline.
        # DraftKings: PTS*1 + 3PM*0.5 + REB*1.25 + AST*1.5 + STL*2 + BLK*2 + TOV*-0.5
        #   + DD bonus (+1.5) / TD bonus (+3.0) — approximated here without bonus
        #   because we only have averages not per-game rows.  The bonus is captured
        #   by the stored dk_pts as a fallback when it differs from the recomputed value.
        # FanDuel: FGM*2 + FTM*1 + 3PM*1 + REB*1.2 + AST*1.5 + STL*3 + BLK*3 + TOV*-1
        #
        # Strategy: use COALESCE(stored, recomputed) so correct stored values are
        # preferred (they include DD/TD bonuses), but NULL/zero stored values fall
        # back to the canonical recomputation from raw stats.
        sql = f"""
            WITH ranked AS (
                SELECT
                    player_name,
                    minutes,
                    COALESCE(
                        NULLIF(dk_pts, 0),
                        COALESCE(points,0)*1.0
                        + COALESCE(three_pointers,0)*0.5
                        + COALESCE(rebounds,0)*1.25
                        + COALESCE(assists,0)*1.5
                        + COALESCE(steals,0)*2.0
                        + COALESCE(blocks,0)*2.0
                        + COALESCE(turnovers,0)*(-0.5)
                    ) AS dk_pts_canon,
                    COALESCE(
                        NULLIF(fd_pts, 0),
                        COALESCE(fg_made,0)*2.0
                        + COALESCE(ft_made,0)*1.0
                        + COALESCE(three_pointers,0)*1.0
                        + COALESCE(rebounds,0)*1.2
                        + COALESCE(assists,0)*1.5
                        + COALESCE(steals,0)*3.0
                        + COALESCE(blocks,0)*3.0
                        + COALESCE(turnovers,0)*(-1.0)
                    ) AS fd_pts_canon,
                    ROW_NUMBER() OVER (
                        PARTITION BY player_name
                        ORDER BY game_date DESC
                    ) AS rn
                FROM player_game_logs
                WHERE minutes > 0
            )
            SELECT
                player_name,
                ROUND(AVG(dk_pts_canon), 3) AS avg_dk,
                ROUND(AVG(fd_pts_canon), 3) AS avg_fd,
                ROUND(AVG(minutes), 2)      AS avg_min,
                COUNT(*)                    AS games
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

    # Build canonical recompute expressions for each site — used as fallback
    # when stored dk_pts/fd_pts is NULL or zero (stale ingestion).
    # Only reference raw stat columns that actually exist in the table so the
    # query doesn't fail against minimal test schemas.
    try:
        existing_cols = {
            row[0]
            for row in get_conn(db_path)
            .execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'player_game_logs'")
            .fetchall()
        }
    except Exception:
        existing_cols = set()

    def _col(name: str, default: float = 0.0) -> str:
        return f"COALESCE({name}, {default})" if name in existing_cols else str(default)

    if site.upper() == "DK":
        _recompute = (
            f"{_col('points')}*1.0 + {_col('three_pointers')}*0.5"
            f" + {_col('rebounds')}*1.25 + {_col('assists')}*1.5"
            f" + {_col('steals')}*2.0 + {_col('blocks')}*2.0"
            f" + {_col('turnovers')}*(-0.5)"
        )
        _stored = "dk_pts"
    else:
        _recompute = (
            f"{_col('fg_made')}*2.0 + {_col('ft_made')}*1.0"
            f" + {_col('three_pointers')}*1.0 + {_col('rebounds')}*1.2"
            f" + {_col('assists')}*1.5 + {_col('steals')}*3.0"
            f" + {_col('blocks')}*3.0 + {_col('turnovers')}*(-1.0)"
        )
        _stored = "fd_pts"

    try:
        con = get_conn(db_path)
        sql = f"""
            WITH ranked AS (
                SELECT
                    player_name,
                    COALESCE(NULLIF({_stored}, 0), {_recompute}) AS pts,
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
    injury_boost_enabled: bool = True   # set False to disable injury usage-boost layer
    simulation_enabled: bool = True     # set False to skip Monte Carlo sim columns
    sim_n_sims: int = 500               # number of Monte Carlo trials
    ownership_enabled: bool = True      # set False to skip ownership estimation
    contest_type: str = "gpp"          # "gpp" | "cash" | "double_up" | "winner_take_all"

    def generate(self, slate_df: pd.DataFrame, context: ProjectionContext) -> pd.DataFrame:
        if context.sport != "NBA":
            raise ValueError(f"CanonicalNBAProjectionEngine supports NBA only. Got: {context.sport}")

        df = slate_df.copy()
        has_base = "Base_Proj" in df.columns
        has_box = {"PTS", "TRB", "AST", "STL", "BLK", "TOV"}.issubset(set(df.columns))

        # Pre-load injury state once — shared by Layer 9 (scenario-weighted boost) and
        # ownership estimation (p_play suppression).  Fails gracefully to None.
        _injury_states_df: pd.DataFrame | None = None
        try:
            from analysis.core.injury_intelligence import InjuryIntelligenceService  # noqa: PLC0415
            _injury_states_df = InjuryIntelligenceService().load_player_states_df()
            if _injury_states_df is not None and _injury_states_df.empty:
                _injury_states_df = None
        except Exception:
            pass

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

        # ── Vectorised projection scoring (replaces row-by-row iterrows) ─────
        # One slugified name column is computed once and reused by the StdDev
        # block below, reducing total slug work to a single pass.
        df["_slug"] = df["Name"].astype(str).apply(_slugify)

        # Layer 2: game-log L10 lookup (vectorised Series map)
        _gl_map = {s: round(float(v[site_key]), 2) for s, v in gl_baseline.items()}
        df["GL_L10"] = df["_slug"].map(_gl_map).fillna(0.0)

        # Layer 1: user-supplied base projection
        base_col = df["Base_Proj"].astype(float) if has_base else pd.Series(0.0, index=df.index)

        # Layer 3: box-score derivation (still uses apply; called once per player)
        if has_box:
            box_col = df.apply(lambda r: float(score_nba_row(context.site, r)), axis=1)
        else:
            box_col = pd.Series(0.0, index=df.index)

        # Priority chain (low → high):
        #   layer 0: Site_FPPG  — site's own avg, fallback only when we have no data
        #   layer 3: box_score  — derived from raw stats in the slate
        #   layer 2: GL_L10     — our rolling 10-game avg (beats site FPPG)
        #   layer 1: Base_Proj  — user-supplied projection (beats everything)
        fppg_col = df["Site_FPPG"].fillna(0.0) if "Site_FPPG" in df.columns else pd.Series(0.0, index=df.index)
        proj_col = fppg_col.copy()                                    # layer 0 base
        proj_col = proj_col.mask(box_col > 0, box_col)               # layer 3 overrides
        proj_col = proj_col.mask(df["GL_L10"] > 0, df["GL_L10"])     # layer 2 overrides
        if has_base:
            proj_col = proj_col.mask(base_col > 0, base_col)         # layer 1 overrides all

        # Count how many players fell through to Site_FPPG fallback
        no_gl = (df["GL_L10"] <= 0) & (box_col <= 0)
        no_base = (base_col <= 0) if has_base else pd.Series(True, index=df.index)
        n_fppg_fallback = int(((fppg_col > 0) & no_gl & no_base).sum())
        if n_fppg_fallback:
            log.info(
                "Site_FPPG fallback used for %d players with no game-log history "
                "(GL_L10=0, no box score) — DvP/B2B adjustments will still apply.",
                n_fppg_fallback,
            )

        df["Proj"] = proj_col.astype(float)

        # Logging counters (vectorised)
        scored_projection = df["Proj"].tolist()     # for n_base / n_gl below
        gl_l10_values = df["GL_L10"].tolist()        # for n_gl below

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
                    vegas_totals, _ = _fetch_team_totals("NBA")
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
                    gt_vegas, _ = _fetch_team_totals("NBA")
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

        # ── Layer 9: Injury usage-boost adjustment ─────────────────────────────────
        # Prefer probabilistic scenario-weighted multipliers from injury_intelligence.
        # Falls back to binary OUT/SSPD static boosts when no state data is available.
        name_col_inj = next((c for c in ["Name", "name"] if c in df.columns), None)
        if self.injury_boost_enabled and name_col_inj and team_col and pos_col_dvp:
            # Pass pre-loaded states so we avoid a second DB read
            inj_mults = get_scenario_weighted_injury_multipliers(
                df,
                states_df=_injury_states_df,
                team_col=team_col,
                pos_col=pos_col_dvp,
                name_col=name_col_inj,
            )
            source = "scenario-weighted"
            if not inj_mults:
                # Fall back to static binary boost
                inj_mults = get_injury_boost_multipliers(
                    df,
                    team_col=team_col,
                    pos_col=pos_col_dvp,
                    name_col=name_col_inj,
                )
                source = "static"
            if inj_mults:
                name_key_series = df[name_col_inj].map(lambda n: str(n).strip().upper())
                inj_series = name_key_series.map(lambda k: inj_mults.get(k, 1.0))
                df["InjuryBoost"] = inj_series.round(5)
                df["Proj"] = (df["Proj"] * inj_series).round(4)
                n_inj = int((inj_series != 1.0).sum())
                log.info(
                    "Injury boost applied to %d/%d players (%s)",
                    n_inj, len(df), source,
                )
            else:
                df["InjuryBoost"] = 1.0
        else:
            df["InjuryBoost"] = 1.0

        # ── Per-player variance model ──────────────────────────────────────────
        # Prefer individual CV from rolling game logs; fall back to position CV.
        # StdDev = Proj × CV
        # Floor   = (Proj − 1.0 × StdDev).clip(lower=0)
        # Ceiling = Proj + 1.5 × StdDev
        pos_col = next((c for c in ["Pos", "pos", "Position"] if c in df.columns), None)

        # ── Vectorised StdDev (replaces row-by-row iterrows) ─────────────────
        # Reuses df["_slug"] computed in the scoring block above.
        _cv_map = {s: v["cv"] for s, v in stddev_baseline.items()}
        cv_from_baseline = df["_slug"].map(_cv_map)  # NaN where player not in baseline
        if pos_col:
            pos_cv_series = (
                df[pos_col].astype(str)
                .str.split("/").str[0]
                .str.strip().str.upper()
                .map(_POSITION_DEFAULT_CV)
                .fillna(_POSITION_DEFAULT_CV_FALLBACK)
            )
        else:
            pos_cv_series = pd.Series(_POSITION_DEFAULT_CV_FALLBACK, index=df.index)
        cv_series = cv_from_baseline.fillna(pos_cv_series)
        df["StdDev"] = (df["Proj"] * cv_series).round(3)
        df["Floor"] = (df["Proj"] - df["StdDev"]).clip(lower=0).round(4)
        df["Ceiling"] = (df["Proj"] + 1.5 * df["StdDev"]).round(4)
        df["Value"] = (df["Proj"] / (df["Salary"] / 1000.0).replace(0, pd.NA)).fillna(0.0)

        # ── Monte Carlo simulation ─────────────────────────────────────────────
        # After Floor/Ceiling are computed, run a player-outcome simulation to
        # add Sim_P90 (90th-percentile score), Sim_Boost (correlation uplift), and
        # Boom_Rate (P(score > 1.5×Proj)) so lineups can be ranked on upside.
        _sim_cols = ["Sim_P90", "Sim_Boost", "Boom_Rate"]
        if self.simulation_enabled:
            try:
                _sim_cfg = SimulationConfig(n_sims=self.sim_n_sims, seed=None, correlation="team")
                _sims = simulate_player_outcomes(df, _sim_cfg)
                _sim_summary = summarize_player_sims(df, _sims)
                _to_merge = ["DFS_ID"] + [c for c in _sim_cols if c in _sim_summary.columns]
                df = df.merge(_sim_summary[_to_merge], on="DFS_ID", how="left")
                log.info(
                    "Monte Carlo simulation merged: %d sims, %d players",
                    self.sim_n_sims, len(df),
                )
            except Exception as exc:
                log.warning("Monte Carlo simulation failed: %s — skipping", exc)
                for _c in _sim_cols:
                    df[_c] = float("nan")
        else:
            for _c in _sim_cols:
                df[_c] = float("nan")

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
                    states_df=_injury_states_df,
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

        n_individual_cv = int(cv_from_baseline.notna().sum())
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
                    "Proj", "GL_L10", "DvP", "Rest", "Blowout", "MinutesTrend", "GameTotal", "InjuryBoost",
                    "StdDev", "Floor", "Ceiling", "Sim_P90", "Sim_Boost", "Boom_Rate",
                    "Own", "Own_Est", "own_source", "Leverage",
                    "Value", "InjuryStatus"]
        return df[[c for c in out_cols if c in df.columns]]
