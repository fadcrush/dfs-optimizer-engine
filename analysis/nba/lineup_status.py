"""
NBA Starting Lineup Probability
================================
Estimates start_prob ∈ [0, 1] for each player in a projections DataFrame.

Sources (in priority order)
---------------------------
1. ``lineup_announcements`` table in nba_news.duckdb  (CONFIRMED_STARTER / CONFIRMED_BENCH)
2. Historical starts from ``player_game_logs`` in dfs_edge.duckdb  (last 20 games)
3. Fallback constant (0.85) — most rostered slate players are starters

Side effects
------------
Writes today's announcements to the DB when it refreshes from a live source.

Usage
-----
    from analysis.nba.lineup_status import add_start_prob
    df = add_start_prob(df)
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from analysis.shared.db import get_conn

ROOT = Path(__file__).resolve().parents[2]
NEWS_DB = ROOT / "data" / "nba_news.duckdb"
EDGE_DB = ROOT / "data" / "dfs_edge.duckdb"

log = logging.getLogger(__name__)

FALLBACK_START_PROB = 0.85
CONFIRMED_STARTER_PROB = 0.97
CONFIRMED_BENCH_PROB = 0.05
QUESTIONABLE_PROB = 0.50
LOOKBACK_GAMES = 20
STARTER_MIN_PROXY = 20.0  # minutes above which = starter


# ── DB helpers ─────────────────────────────────────────────────────────────

def _ensure_announcements_table() -> None:
    """Create lineup_announcements table if it doesn't exist."""
    if not NEWS_DB.exists():
        return
    try:
        con = get_conn(NEWS_DB)
        con.execute("""
            CREATE TABLE IF NOT EXISTS lineup_announcements (
                player_name  VARCHAR NOT NULL,
                game_date    DATE    NOT NULL,
                status       VARCHAR NOT NULL,
                source       VARCHAR DEFAULT 'manual',
                ingested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (player_name, game_date)
            )
        """)
    except Exception as exc:
        log.warning("Could not ensure lineup_announcements table: %s", exc)


def _load_announcements_today() -> dict[str, str]:
    """
    Returns {player_name_lower: status} for today's announcements.
    status values: CONFIRMED_STARTER | CONFIRMED_BENCH
    """
    _ensure_announcements_table()
    if not NEWS_DB.exists():
        return {}
    try:
        con = get_conn(NEWS_DB)
        today = date.today().isoformat()
        rows = con.execute("""
            SELECT LOWER(player_name) AS name, UPPER(status) AS status
            FROM lineup_announcements
            WHERE game_date = ?
        """, [today]).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception as exc:
        log.warning("Could not load lineup_announcements: %s", exc)
        return {}


def _load_historical_start_rates() -> dict[str, float]:
    """
    Returns {player_name_lower: fraction of last 20 games with MIN > 20}.
    """
    if not EDGE_DB.exists():
        return {}
    try:
        con = get_conn(EDGE_DB)
        rows = con.execute(f"""
            WITH ranked AS (
                SELECT
                    LOWER(player_name) AS name,
                    minutes AS MIN_played,
                    ROW_NUMBER() OVER (PARTITION BY player_name ORDER BY game_date DESC) AS rn
                FROM player_game_logs
            )
            SELECT
                name,
                AVG(CASE WHEN MIN_played > {STARTER_MIN_PROXY} THEN 1.0 ELSE 0.0 END) AS start_rate
            FROM ranked
            WHERE rn <= {LOOKBACK_GAMES}
            GROUP BY name
        """).fetchall()
        return {r[0]: float(r[1]) for r in rows}
    except Exception as exc:
        log.warning("Could not load historical start rates: %s", exc)
        return {}


# ── Public API ────────────────────────────────────────────────────────────

def add_start_prob(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add ``start_prob`` column to a projections DataFrame.

    Parameters
    ----------
    df : DataFrame with at least ``Name`` and optionally ``InjuryStatus`` columns.

    Returns
    -------
    Copy of df with ``start_prob`` column added.
    """
    df = df.copy()

    announcements = _load_announcements_today()
    historical = _load_historical_start_rates()

    name_col = next((c for c in ["Name", "player_name", "Player"] if c in df.columns), None)
    if name_col is None:
        log.warning("add_start_prob: no Name column found, using fallback %.2f", FALLBACK_START_PROB)
        df["start_prob"] = FALLBACK_START_PROB
        return df

    inj_col = next((c for c in ["InjuryStatus", "injury_status"] if c in df.columns), None)
    probs: list[float] = []

    for _, row in df.iterrows():
        name_lower = str(row[name_col]).lower().strip()
        inj = str(row[inj_col]).upper().strip() if inj_col and pd.notna(row.get(inj_col, "")) else ""

        # Priority 1: confirmed announcement
        if name_lower in announcements:
            status = announcements[name_lower]
            if status == "CONFIRMED_STARTER":
                probs.append(CONFIRMED_STARTER_PROB)
                continue
            if status == "CONFIRMED_BENCH":
                probs.append(CONFIRMED_BENCH_PROB)
                continue

        # Priority 2: questionable / DTD from injury data
        if inj in ("QUESTIONABLE", "DOUBTFUL", "DTD"):
            probs.append(QUESTIONABLE_PROB)
            continue

        # Priority 3: historical start rate
        if name_lower in historical:
            # Apply recency bias: clamp between 0.4 and 0.97
            rate = max(0.40, min(0.97, historical[name_lower]))
            probs.append(rate)
            continue

        # Fallback
        probs.append(FALLBACK_START_PROB)

    df["start_prob"] = probs
    log.info("start_prob added: avg=%.3f, min=%.3f, max=%.3f",
             sum(probs) / len(probs) if probs else 0,
             min(probs) if probs else 0,
             max(probs) if probs else 0)
    return df


def upsert_announcement(player_name: str, status: str, source: str = "manual") -> None:
    """
    Insert or update a lineup announcement for today.

    Parameters
    ----------
    player_name : Player name (any case)
    status      : "CONFIRMED_STARTER" or "CONFIRMED_BENCH"
    source      : Data source label
    """
    _ensure_announcements_table()
    if not NEWS_DB.exists():
        log.warning("nba_news.duckdb not found — cannot save announcement")
        return
    try:
        con = get_conn(NEWS_DB)
        today = date.today().isoformat()
        con.execute("""
            INSERT OR REPLACE INTO lineup_announcements
                (player_name, game_date, status, source, ingested_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, [player_name, today, status.upper(), source])
        log.info("Saved lineup announcement: %s → %s", player_name, status)
    except Exception as exc:
        log.warning("Could not save lineup announcement: %s", exc)
