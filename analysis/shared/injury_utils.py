"""
Shared Injury Utilities
========================
Centralised helpers for loading, matching, and summarising NBA injury data.
Used by pool_filter, slates router, injuries router, and the optimizer pipeline.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd

from analysis.shared.db import get_conn

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "nba_news.duckdb"

log = logging.getLogger(__name__)

# ── In-memory cache (avoids hitting DuckDB on every HTTP request) ────────────
_injury_cache: pd.DataFrame | None = None
_injury_cache_ts: float = 0.0
_CACHE_TTL_SECS = 60  # 1 minute — matches SSE interval


# ── Name slugifier ───────────────────────────────────────────────────────────

def slug(name: str) -> str:
    """Normalise a player name for fuzzy matching.

    >>> slug("LeBron James")
    'lebron_james'
    >>> slug("P.J. Washington Jr.")
    'pj_washington_jr'
    """
    return (
        str(name).lower()
        .replace("'", "")
        .replace(".", "")
        .replace("-", "_")
        .replace(" ", "_")
    )


# ── Column detection ─────────────────────────────────────────────────────────

def detect_name_col(df: pd.DataFrame) -> str:
    """Find the most likely player-name column in a DataFrame."""
    for col in ("Name", "name", "Player", "player_name", "PLAYER_NAME", "nickname"):
        if col in df.columns:
            return col
    return df.columns[0]


# ── Injury loader ────────────────────────────────────────────────────────────

def load_injury_status(*, force: bool = False) -> pd.DataFrame:
    """
    Query ``vw_nba_injury_status`` from nba_news.duckdb.

    Returns DataFrame with columns: player_id, player_name, status, detail,
    team, game_date, confidence.

    Results are cached for ``_CACHE_TTL_SECS`` to avoid per-request I/O.
    On any failure, returns empty DataFrame (graceful degradation).
    """
    global _injury_cache, _injury_cache_ts

    if not force and _injury_cache is not None and (time.time() - _injury_cache_ts) < _CACHE_TTL_SECS:
        return _injury_cache

    empty = pd.DataFrame(columns=[
        "player_id", "player_name", "status", "detail", "team", "game_date", "confidence",
    ])

    if not DB_PATH.exists():
        log.warning("nba_news.duckdb not found at %s – injury data unavailable", DB_PATH)
        return empty

    try:
        con = get_conn(DB_PATH)
        df = con.execute("""
            SELECT
                player_id,
                COALESCE(player_name, player_id) AS player_name,
                UPPER(COALESCE(status, ''))       AS status,
                COALESCE(detail, '')               AS detail,
                COALESCE(team, '')                 AS team,
                game_date,
                confidence
            FROM vw_nba_injury_status
        """).df()
        log.info("Loaded %d injury records from vw_nba_injury_status", len(df))
        _injury_cache = df
        _injury_cache_ts = time.time()
        return df
    except Exception as exc:
        log.warning("Could not load injury data: %s – returning empty set", exc)
        return empty


def invalidate_cache() -> None:
    """Force the next ``load_injury_status()`` call to re-query the DB."""
    global _injury_cache, _injury_cache_ts
    _injury_cache = None
    _injury_cache_ts = 0.0


# ── Match injury status into a projections/player DataFrame ──────────────────

def match_injury_status(
    df: pd.DataFrame,
    injury_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Enrich *df* with ``InjuryStatus`` and ``InjuryDetail`` columns from injury data.

    Priority (highest → lowest):
      1. Pre-existing non-empty ``InjuryStatus`` already in *df*
      2. Value from DB injury report (matched by slugified name)
      3. Empty string (no information)
    """
    df = df.copy()

    if injury_df is None:
        injury_df = load_injury_status()

    name_col = detect_name_col(df)

    # Ensure columns exist
    if "InjuryStatus" not in df.columns:
        df["InjuryStatus"] = ""
    if "InjuryDetail" not in df.columns:
        df["InjuryDetail"] = ""

    # Normalise: treat None/NaN as empty string
    df["InjuryStatus"] = df["InjuryStatus"].fillna("").astype(str)
    df["InjuryDetail"] = df["InjuryDetail"].fillna("").astype(str)

    if injury_df.empty:
        return df

    # Build slug → {status, detail} map from DB
    injury_df = injury_df.copy()
    id_col = "player_id" if "player_id" in injury_df.columns else detect_name_col(injury_df)
    injury_df["_slug"] = injury_df[id_col].apply(slug)
    injury_map = injury_df.set_index("_slug")[["status", "detail"]].to_dict("index")

    # Only fill rows where InjuryStatus is currently empty (preserve explicit values)
    for i, row in df.iterrows():
        if str(row["InjuryStatus"]).strip():
            continue
        s = slug(str(row.get(name_col, "")))
        entry = injury_map.get(s, {})
        if entry.get("status"):
            df.at[i, "InjuryStatus"] = str(entry["status"])
        if entry.get("detail"):
            df.at[i, "InjuryDetail"] = str(entry["detail"])

    return df


# ── Summarise injury data for a player list ──────────────────────────────────

def build_injury_summary(
    player_names: list[str],
    injury_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """
    Cross-reference a list of player names against the current injury report.

    Returns a summary dict ready for API responses::

        {
          "out_count": 3,
          "questionable_count": 5,
          "doubtful_count": 1,
          "probable_count": 2,
          "out_players": [{"name": ..., "team": ..., "detail": ...}],
          "questionable_players": [...],
          "doubtful_players": [...],
          "all_injuries": [...],
          "changed_since_export": 2,   # players whose DB status differs from CSV
          "last_updated": "2026-03-09T14:30:00Z",
        }
    """
    if injury_df is None:
        injury_df = load_injury_status()

    summary: dict[str, Any] = {
        "out_count": 0,
        "questionable_count": 0,
        "doubtful_count": 0,
        "probable_count": 0,
        "out_players": [],
        "questionable_players": [],
        "doubtful_players": [],
        "all_injuries": [],
        "changed_since_export": 0,
        "last_updated": None,
    }

    if injury_df.empty:
        return summary

    # Timestamp from most recent row
    if "game_date" in injury_df.columns:
        dates = injury_df["game_date"].dropna()
        if not dates.empty:
            latest = dates.max()
            summary["last_updated"] = str(latest)

    # Build slug map from injury DB
    id_col = "player_id" if "player_id" in injury_df.columns else detect_name_col(injury_df)
    injury_df = injury_df.copy()
    injury_df["_slug"] = injury_df[id_col].apply(slug)
    injury_map = {}
    for _, row in injury_df.iterrows():
        injury_map[row["_slug"]] = {
            "name": str(row.get("player_name", row.get(id_col, ""))),
            "status": str(row.get("status", "")).upper(),
            "detail": str(row.get("detail", "")),
            "team": str(row.get("team", "")),
        }

    # Match player names
    player_slugs = {slug(n): n for n in player_names}

    for p_slug, p_name in player_slugs.items():
        entry = injury_map.get(p_slug)
        if not entry:
            continue

        status = entry["status"]
        record = {"name": p_name, "team": entry["team"], "detail": entry["detail"], "status": status}
        summary["all_injuries"].append(record)

        if status in ("OUT", "O"):
            summary["out_count"] += 1
            summary["out_players"].append(record)
        elif status in ("QUESTIONABLE", "Q", "GTD"):
            summary["questionable_count"] += 1
            summary["questionable_players"].append(record)
        elif status in ("DOUBTFUL", "D"):
            summary["doubtful_count"] += 1
            summary["doubtful_players"].append(record)
        elif status in ("PROBABLE", "P"):
            summary["probable_count"] += 1

    return summary
