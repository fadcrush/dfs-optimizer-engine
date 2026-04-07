"""
stat_projection_breakdown.py
────────────────────────────
Per-minute DFS-rate enrichment for projections.

Since eree's ``player_game_logs`` table only stores ``dk_pts`` and ``fd_pts``
(not individual box-score stats), this module projects DFS fantasy points
via per-minute rate x projected minutes rather than per-stat category.

Adds these supplemental columns to a projections DataFrame:
    projected_minutes   – from minutes_confidence_lite
    minutes_confidence  – from minutes_confidence_lite
    est_dk_pts          – blended per-minute DK rate × projected_minutes
    est_fd_pts          – blended per-minute FD rate × projected_minutes
    stat_confidence     – composite: sample quality + rate stability

**Does NOT overwrite Proj, Floor, or Ceiling.**

Gated externally by DFS_ENABLE_STAT_ENRICHMENT=1 in orchestrator.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from analysis.shared.db import get_conn
from analysis.nba.minutes_confidence_lite import add_minutes_confidence

log = logging.getLogger(__name__)

_DATA_DIR   = Path(__file__).resolve().parents[2] / "data"
_DEFAULT_DB = _DATA_DIR / "game_logs.duckdb"      # dedicated seeded file
_FALLBACK_DB = _DATA_DIR / "dfs_edge.duckdb"       # used if game_logs.duckdb absent

# Same window weights as minutes model
W_SEASON = 0.20
W_LAST_10 = 0.30
W_LAST_5 = 0.40

# Minimum per-minute rate to avoid division noise
_MIN_RATE_GUARD = 0.0


# ---------------------------------------------------------------------------
# SQL: per-player per-minute DK/FD rate — all windows in one pass
# ---------------------------------------------------------------------------
_RATE_SQL = """
WITH ranked AS (
    SELECT
        player_name,
        CASE WHEN minutes > 0 THEN dk_pts / minutes ELSE NULL END AS dk_rate,
        CASE WHEN minutes > 0 THEN fd_pts / minutes ELSE NULL END AS fd_rate,
        ROW_NUMBER() OVER (
            PARTITION BY player_name ORDER BY game_date DESC
        ) AS rn
    FROM player_game_logs
    WHERE minutes > 0
)
SELECT
    player_name,
    -- Season
    COUNT(*)                                                AS games_season,
    ROUND(AVG(dk_rate), 4)                                  AS dk_rate_season,
    ROUND(AVG(fd_rate), 4)                                  AS fd_rate_season,
    ROUND(STDDEV_SAMP(dk_rate), 4)                          AS dk_rate_std_season,
    -- L10
    COUNT(*) FILTER (WHERE rn <= 10)                        AS games_l10,
    ROUND(AVG(dk_rate) FILTER (WHERE rn <= 10), 4)          AS dk_rate_l10,
    ROUND(AVG(fd_rate) FILTER (WHERE rn <= 10), 4)          AS fd_rate_l10,
    -- L5
    COUNT(*) FILTER (WHERE rn <= 5)                         AS games_l5,
    ROUND(AVG(dk_rate) FILTER (WHERE rn <= 5),  4)          AS dk_rate_l5,
    ROUND(AVG(fd_rate) FILTER (WHERE rn <= 5),  4)          AS fd_rate_l5
FROM ranked
GROUP BY player_name
"""


def _load_rate_windows(db_path: Path) -> pd.DataFrame:
    """Query per-minute rate windows from player_game_logs."""
    if not db_path.exists():
        log.warning("stat_projection_breakdown: DB not found at %s", db_path)
        return pd.DataFrame()
    try:
        con = get_conn(db_path, db_key=db_path.stem, read_only=False)
        return con.execute(_RATE_SQL).fetchdf()
    except Exception as exc:
        log.warning("stat_projection_breakdown: rate query failed: %s", exc)
        return pd.DataFrame()


def _blend_rate(row: pd.Series, site: str) -> tuple[float, float]:
    """
    Return (blended_rate, stat_confidence) for one player-window row.

    site: "DK" or "FD"
    """
    prefix = "dk_rate" if site.upper() == "DK" else "fd_rate"

    games_season = int(row.get("games_season", 0) or 0)
    games_l10 = int(row.get("games_l10", 0) or 0)
    games_l5 = int(row.get("games_l5", 0) or 0)

    r_season = float(row.get(f"{prefix}_season") or 0.0)
    r_l10 = float(row.get(f"{prefix}_l10") or 0.0)
    r_l5 = float(row.get(f"{prefix}_l5") or 0.0)

    dk_std = float(row.get("dk_rate_std_season") or 0.0)

    # Adaptive weights (same pattern as minutes model)
    w_s, w_10, w_5 = W_SEASON, W_LAST_10, W_LAST_5
    if games_l5 == 0:
        w_5 = 0.0
        if games_l10 > 0:
            w_10 += W_LAST_5
        else:
            w_s += W_LAST_5 + W_LAST_10
            w_10 = 0.0
    elif games_l10 < 5:
        w_10 *= 0.5
        w_5 += W_LAST_10 * 0.5

    total = w_s + w_10 + w_5
    if total == 0:
        return 0.0, 0.0

    w_s /= total
    w_10 /= total
    w_5 /= total

    blended = w_s * r_season + w_10 * r_l10 + w_5 * r_l5

    # Confidence: sample size + rate stability
    sample = min(games_season / 40.0, 1.0) * 0.65
    if games_l5 >= 5:
        sample += 0.05
    if games_l10 >= 10:
        sample += 0.05

    # Rate coefficient of variation (lower = more stable)
    mean_rate = (r_season + r_l10 + r_l5) / max(
        sum(1 for v in [r_season, r_l10, r_l5] if v > 0), 1
    )
    if mean_rate > 0 and dk_std > 0:
        cv = dk_std / mean_rate
        stability = max(0.0, 1.0 - cv) * 0.25
    else:
        stability = 0.0

    confidence = min(1.0, sample + stability)
    return round(blended, 4), round(confidence, 3)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enrich_with_stat_breakdown(
    df: pd.DataFrame,
    site: str,
    db_path: Optional[Path] = None,
) -> pd.DataFrame:
    """
    Add stat enrichment columns to *df*.

    Columns added:
        projected_minutes   – from minutes_confidence_lite
        minutes_confidence  – from minutes_confidence_lite
        est_dk_pts          – projected_minutes × blended DK rate
        est_fd_pts          – projected_minutes × blended FD rate
        stat_confidence     – composite quality score [0, 1]

    Columns never overwritten: Proj, Floor, Ceiling, Own, Salary,
    DFS_ID, Name, Team, Pos.

    Parameters
    ----------
    df:
        Projections DataFrame from orchestrator.
    site:
        "DK" or "FD" — selects which per-minute rate to surface as primary.
    db_path:
        Path to dfs_edge.duckdb.  Defaults to ``data/dfs_edge.duckdb``.

    Returns a copy — input is not mutated.
    """
    if df.empty:
        return df.copy()

    if db_path:
        _db = Path(db_path)
    elif _DEFAULT_DB.exists():
        _db = _DEFAULT_DB
    else:
        _db = _FALLBACK_DB

    # Step 1: add projected_minutes + minutes_confidence
    enriched = add_minutes_confidence(df, db_path=_db)

    # Step 2: load per-minute rate windows
    rates = _load_rate_windows(_db)
    if rates.empty:
        log.warning("stat_projection_breakdown: no rate windows — est_dk/fd_pts defaulting to NaN")
        enriched["est_dk_pts"] = float("nan")
        enriched["est_fd_pts"] = float("nan")
        enriched["stat_confidence"] = 0.0
        return enriched

    name_col = next(
        (c for c in ["Name", "player_name", "Player"] if c in enriched.columns),
        enriched.columns[0],
    )

    # Build slug lookup
    def _slug(s: str) -> str:
        return str(s).lower().strip()

    rates["_slug"] = rates["player_name"].map(_slug)
    rate_lookup = rates.set_index("_slug")

    est_dk_list: list[float] = []
    est_fd_list: list[float] = []
    stat_conf_list: list[float] = []

    for _, row in enriched.iterrows():
        slug = _slug(str(row[name_col]))
        proj_min = float(row.get("projected_minutes") or 0.0)

        if slug in rate_lookup.index and proj_min > 0:
            rate_row = rate_lookup.loc[slug]
            dk_rate, dk_conf = _blend_rate(rate_row, "DK")
            fd_rate, _ = _blend_rate(rate_row, "FD")

            est_dk = round(dk_rate * proj_min, 2)
            est_fd = round(fd_rate * proj_min, 2)

            # stat_confidence: average of dk blended confidence and minutes confidence
            min_conf = float(row.get("minutes_confidence") or 0.0)
            stat_conf = round((dk_conf + min_conf) / 2.0, 3)
        else:
            est_dk = float("nan")
            est_fd = float("nan")
            stat_conf = 0.0

        est_dk_list.append(est_dk)
        est_fd_list.append(est_fd)
        stat_conf_list.append(stat_conf)

    enriched = enriched.copy()
    enriched["est_dk_pts"] = est_dk_list
    enriched["est_fd_pts"] = est_fd_list
    enriched["stat_confidence"] = stat_conf_list

    n_enriched = sum(1 for v in est_dk_list if pd.notna(v) and v > 0)
    log.info(
        "stat_projection_breakdown: enriched %d/%d players (site=%s)",
        n_enriched, len(enriched), site.upper(),
    )
    return enriched
