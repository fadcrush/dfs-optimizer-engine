"""
Minutes Confidence Lite
=======================
Deterministic minutes projection with confidence score.

Ported from CeekIth/app/services/minutes_model.py and adapted to use
eree's ``player_game_logs`` table in dfs_edge.duckdb.

Uses only columns already present in eree's game log schema:
    player_name, minutes, game_date

No CeekIth DuckDB connection dependency — DB path is an optional argument.

Algorithm
---------
Three time-window weighted blend (season / last-10 / last-5) + trend
adjustment, with small-sample dampening and [0, 42] guardrails.

Output columns added to DataFrame:
    projected_minutes   — float, clamped [0, 42]
    minutes_confidence  — float, clamped [0, 1]
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Algorithm weights (mirrors CeekIth exactly)
# ---------------------------------------------------------------------------
W_SEASON: float = 0.20
W_LAST_10: float = 0.30
W_LAST_5: float = 0.40
W_TREND: float = 0.10

# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------
MIN_MINUTES: float = 0.0
MAX_MINUTES: float = 42.0
SMALL_SAMPLE_THRESHOLD: int = 5   # games_season below this triggers dampening
REPLACEMENT_MINUTES: float = 12.0  # conservative default for thin samples

# ---------------------------------------------------------------------------
# Default DB path (same convention as projection_engine.py)
# ---------------------------------------------------------------------------
_DATA_DIR   = Path(__file__).resolve().parents[2] / "data"
_DEFAULT_DB = _DATA_DIR / "game_logs.duckdb"      # dedicated seeded file
_FALLBACK_DB = _DATA_DIR / "dfs_edge.duckdb"       # used if game_logs.duckdb absent


# ---------------------------------------------------------------------------
# Core per-row computation (identical to CeekIth)
# ---------------------------------------------------------------------------

def project_minutes(row: "pd.Series[Any]") -> dict:
    """
    Compute projected minutes and confidence for a single row.

    Expects these keys in *row* (all optional — missing values default to 0):
        avg_minutes_season      avg_minutes_last_10     avg_minutes_last_5
        games_season            games_last_10           games_last_5
        minutes_trend_5_vs_10   minutes_trend_5_vs_season

    Returns
    -------
    dict with ``projected_minutes`` (float) and ``minutes_confidence`` (float).
    """
    games_season = int(row.get("games_season", 0) or 0)
    games_last_10 = int(row.get("games_last_10", 0) or 0)
    games_last_5 = int(row.get("games_last_5", 0) or 0)

    avg_season = float(row.get("avg_minutes_season", 0.0) or 0.0)
    avg_last_10 = float(row.get("avg_minutes_last_10", 0.0) or 0.0)
    avg_last_5 = float(row.get("avg_minutes_last_5", 0.0) or 0.0)

    trend_5v10 = float(row.get("minutes_trend_5_vs_10", 0.0) or 0.0)
    trend_5vseason = float(row.get("minutes_trend_5_vs_season", 0.0) or 0.0)

    # ── Weight redistribution when a window is missing ─────────────────────
    w_s, w_10, w_5, w_t = W_SEASON, W_LAST_10, W_LAST_5, W_TREND

    if games_last_5 == 0:
        w_5 = 0.0
        w_t = 0.0
        if games_last_10 > 0:
            w_10 += 0.50
        else:
            w_s += 0.50
            w_10 = 0.0
    elif games_last_10 < 5:
        w_10 *= 0.5
        w_5 += w_10 * 0.5

    total_w = w_s + w_10 + w_5 + w_t
    if total_w == 0:
        return {"projected_minutes": 0.0, "minutes_confidence": 0.0}

    w_s /= total_w
    w_10 /= total_w
    w_5 /= total_w
    w_t /= total_w

    # Trend adjustment: average of both trend signals, capped to avoid wild swings
    trend_adj = (trend_5v10 + trend_5vseason) / 2.0
    trend_adj = max(-4.0, min(4.0, trend_adj))

    projected = (
        w_s * avg_season
        + w_10 * avg_last_10
        + w_5 * avg_last_5
        + w_t * trend_adj
    )

    # ── Small-sample dampening ─────────────────────────────────────────────
    if games_season < SMALL_SAMPLE_THRESHOLD:
        dampen = games_season / SMALL_SAMPLE_THRESHOLD
        projected = dampen * projected + (1 - dampen) * REPLACEMENT_MINUTES

    projected = max(MIN_MINUTES, min(MAX_MINUTES, projected))

    confidence = _compute_confidence(
        games_season=games_season,
        games_last_10=games_last_10,
        games_last_5=games_last_5,
        avg_season=avg_season,
        avg_last_10=avg_last_10,
        avg_last_5=avg_last_5,
    )

    return {
        "projected_minutes": round(projected, 2),
        "minutes_confidence": round(confidence, 3),
    }


def _compute_confidence(
    games_season: int,
    games_last_10: int,
    games_last_5: int,
    avg_season: float,
    avg_last_10: float,
    avg_last_5: float,
) -> float:
    """Confidence in [0, 1] based on sample depth and window agreement."""
    # Sample component (0–0.7)
    sample_score = min(games_season / 40.0, 1.0) * 0.60
    if games_last_5 >= 5:
        sample_score += 0.05
    if games_last_10 >= 10:
        sample_score += 0.05

    # Agreement component (0–0.3)
    vals = [v for v in [avg_season, avg_last_10, avg_last_5] if v > 0]
    if len(vals) >= 2:
        spread = max(vals) - min(vals)
        agreement_score = max(0.0, 1.0 - spread / 10.0) * 0.30
    else:
        agreement_score = 0.0

    return min(1.0, sample_score + agreement_score)


# ---------------------------------------------------------------------------
# Data loading (eree game log schema → window averages)
# ---------------------------------------------------------------------------

def _load_minutes_windows(
    player_names: list[str],
    db_path: Path,
) -> pd.DataFrame:
    """
    Query ``player_game_logs`` in *db_path* and return per-player window
    averages suitable for ``project_minutes()``.

    Returns an empty DataFrame (with correct columns) on any DB failure.
    """
    _empty = pd.DataFrame(columns=[
        "player_name",
        "avg_minutes_season", "games_season",
        "avg_minutes_last_10", "games_last_10",
        "avg_minutes_last_5", "games_last_5",
        "minutes_trend_5_vs_10", "minutes_trend_5_vs_season",
    ])

    if not db_path.exists():
        log.debug("minutes_confidence_lite: DB not found at %s — skipping", db_path)
        return _empty

    try:
        import duckdb
    except ImportError:
        log.warning("minutes_confidence_lite: duckdb not installed — skipping")
        return _empty

    try:
        con = duckdb.connect(str(db_path), read_only=True)
        sql = """
            WITH ranked AS (
                SELECT
                    player_name,
                    minutes,
                    ROW_NUMBER() OVER (
                        PARTITION BY player_name ORDER BY game_date DESC
                    ) AS rn
                FROM player_game_logs
                WHERE minutes > 0
            ),
            season_w AS (
                SELECT player_name,
                       ROUND(AVG(minutes), 2) AS avg_minutes_season,
                       COUNT(*)               AS games_season
                FROM ranked
                GROUP BY player_name
            ),
            l10 AS (
                SELECT player_name,
                       ROUND(AVG(minutes), 2) AS avg_minutes_last_10,
                       COUNT(*)               AS games_last_10
                FROM ranked
                WHERE rn <= 10
                GROUP BY player_name
            ),
            l5 AS (
                SELECT player_name,
                       ROUND(AVG(minutes), 2) AS avg_minutes_last_5,
                       COUNT(*)               AS games_last_5
                FROM ranked
                WHERE rn <= 5
                GROUP BY player_name
            )
            SELECT
                s.player_name,
                s.avg_minutes_season,
                s.games_season,
                COALESCE(l10.avg_minutes_last_10, 0.0) AS avg_minutes_last_10,
                COALESCE(l10.games_last_10, 0)         AS games_last_10,
                COALESCE(l5.avg_minutes_last_5, 0.0)   AS avg_minutes_last_5,
                COALESCE(l5.games_last_5, 0)           AS games_last_5,
                -- trend = recent_avg - comparison_avg (positive = rising)
                COALESCE(l5.avg_minutes_last_5, 0.0)
                    - COALESCE(l10.avg_minutes_last_10, s.avg_minutes_season)
                    AS minutes_trend_5_vs_10,
                COALESCE(l5.avg_minutes_last_5, 0.0)
                    - s.avg_minutes_season
                    AS minutes_trend_5_vs_season
            FROM season_w s
            LEFT JOIN l10 USING (player_name)
            LEFT JOIN l5  USING (player_name)
        """
        df = con.execute(sql).fetchdf()
        con.close()
        return df
    except Exception as exc:
        log.warning("minutes_confidence_lite: query failed: %s", exc)
        return _empty


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_minutes_confidence(
    df: pd.DataFrame,
    db_path: Path | str | None = None,
) -> pd.DataFrame:
    """
    Add ``projected_minutes`` and ``minutes_confidence`` columns to *df*.

    Looks up game-log window averages from ``player_game_logs`` in
    *dfs_edge.duckdb* (or *db_path*) and joins them by player name.

    Missing DB / missing players → projected_minutes=NaN,
    minutes_confidence=0.0.

    Returns a copy — the input DataFrame is not mutated.
    NEVER overwrites ``Proj``, ``Floor``, or ``Ceiling``.
    """
    if db_path:
        resolved_db = Path(db_path)
    elif _DEFAULT_DB.exists():
        resolved_db = _DEFAULT_DB
    else:
        resolved_db = _FALLBACK_DB
    df = df.copy()

    # Detect name column
    name_col = next(
        (c for c in ["Name", "player_name", "Player"] if c in df.columns),
        None,
    )
    if name_col is None:
        log.warning("minutes_confidence_lite: no name column found — skipping")
        df["projected_minutes"] = float("nan")
        df["minutes_confidence"] = 0.0
        return df

    player_names = df[name_col].dropna().tolist()
    windows = _load_minutes_windows(player_names, resolved_db)

    if windows.empty:
        df["projected_minutes"] = float("nan")
        df["minutes_confidence"] = 0.0
        return df

    # Join windows to df
    merged = df.merge(
        windows,
        left_on=name_col,
        right_on="player_name",
        how="left",
        suffixes=("", "_gl"),
    )

    # Fill NaN in game-log columns (left-join misses) so project_minutes
    # receives 0 instead of NaN (which would cause int() to raise ValueError)
    _gl_numeric = [
        "games_season", "games_last_10", "games_last_5",
        "avg_minutes_season", "avg_minutes_last_10", "avg_minutes_last_5",
        "minutes_trend_5_vs_10", "minutes_trend_5_vs_season",
    ]
    for _col in _gl_numeric:
        if _col in merged.columns:
            merged[_col] = merged[_col].fillna(0)

    # Apply row-wise model
    result_cols = merged.apply(project_minutes, axis=1, result_type="expand")
    merged["projected_minutes"] = result_cols["projected_minutes"]
    merged["minutes_confidence"] = result_cols["minutes_confidence"]

    # Drop the game-log window columns we joined in (keep df clean)
    gl_cols = [
        "player_name",
        "avg_minutes_season", "games_season",
        "avg_minutes_last_10", "games_last_10",
        "avg_minutes_last_5", "games_last_5",
        "minutes_trend_5_vs_10", "minutes_trend_5_vs_season",
    ]
    merged = merged.drop(columns=[c for c in gl_cols if c in merged.columns and c != name_col], errors="ignore")

    log.info(
        "minutes_confidence_lite: projected_minutes added for %d/%d players",
        int(merged["projected_minutes"].notna().sum()),
        len(merged),
    )
    return merged.reset_index(drop=True)
