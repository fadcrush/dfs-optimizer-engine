"""
Player Prop Lines — Enricher
============================
Fetches NBA player prop lines from The Odds API and stores them in
``dfs_edge.duckdb::player_prop_lines``.

Enrich a projections DataFrame with prop comparison columns via
``enrich_with_props(df)``.  All errors are non-fatal — the pipeline
continues without props if the API is unavailable or the key is missing.

Tables written
--------------
player_prop_lines
    event_id, game_date, player_name, market, line, over_odds, under_odds,
    bookmaker, fetched_at

Usage
-----
    from analysis.shared.props_enricher import refresh_props, enrich_with_props

    refresh_props("2026-02-26")     # fetch + store (call once per game day)
    df = enrich_with_props(df)      # add prop_pts_line, proj_vs_prop_pts, etc.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _ROOT / "data" / "dfs_edge.duckdb"

# Stat markets we care about for DFS
_PROP_MARKETS = "player_points,player_rebounds,player_assists,player_threes"

# Market → projection column in the DataFrame
_MARKET_TO_PROJ_COL = {
    "player_points": "Proj",
    "player_rebounds": "Reb",
    "player_assists": "Ast",
    "player_threes": "Three",
}

# Market → prop line output column (added to DataFrame)
_MARKET_TO_LINE_COL = {
    "player_points": "prop_pts_line",
    "player_rebounds": "prop_reb_line",
    "player_assists": "prop_ast_line",
    "player_threes": "prop_3s_line",
}

# ─────────────────────────────────────────────────────────────────────────────


def _slugify(name: str) -> str:
    """Lowercase, strip accents and non-alpha — for fuzzy matching."""
    if not isinstance(name, str):
        return ""
    norm = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", norm.lower())


def _ensure_table(con: Any) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS player_prop_lines (
            event_id    VARCHAR,
            game_date   DATE,
            player_name VARCHAR,
            market      VARCHAR,
            line        FLOAT,
            over_odds   INTEGER,
            under_odds  INTEGER,
            bookmaker   VARCHAR,
            fetched_at  TIMESTAMP DEFAULT now(),
            PRIMARY KEY (event_id, player_name, market, bookmaker)
        )
    """)


def _consensus_lines(rows: list[dict]) -> dict[str, dict[str, float | None]]:
    """
    Collapse multi-book outcomes into one consensus line per (player, market).

    Returns:
        { "slugified_player::market": {"line": x, "over_odds": y, "under_odds": z} }
    """
    from collections import defaultdict

    bucket: dict[str, dict] = defaultdict(lambda: {
        "lines": [], "over_odds": [], "under_odds": []
    })
    for row in rows:
        player_slug = _slugify(row.get("player", ""))
        market = row.get("market", "")
        key = f"{player_slug}::{market}"
        line = row.get("line")
        side = row.get("side", "")
        price = row.get("price")
        if line is not None:
            bucket[key]["lines"].append(float(line))
        if side == "Over" and price is not None:
            bucket[key]["over_odds"].append(int(price))
        elif side == "Under" and price is not None:
            bucket[key]["under_odds"].append(int(price))

    consensus: dict[str, dict] = {}
    for key, vals in bucket.items():
        if not vals["lines"]:
            continue
        import statistics

        consensus[key] = {
            "line": round(statistics.median(vals["lines"]), 1),
            "over_odds": round(sum(vals["over_odds"]) / len(vals["over_odds"])) if vals["over_odds"] else None,
            "under_odds": round(sum(vals["under_odds"]) / len(vals["under_odds"])) if vals["under_odds"] else None,
        }
    return consensus


# ─── Public API ───────────────────────────────────────────────────────────────


def refresh_props(
    game_date: str | None = None,
    db_path: Path = _DEFAULT_DB,
) -> dict:
    """
    Fetch player props for ``game_date`` (defaults to today) and upsert
    into ``player_prop_lines`` in dfs_edge.duckdb.

    Returns a summary dict: {status, new_rows, events_fetched, error?}
    """
    api_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not api_key or api_key.lower() in ("your_theodds_key_here", ""):
        return {"status": "skipped", "reason": "THE_ODDS_API_KEY not configured"}

    target_date = game_date or date.today().isoformat()

    try:
        from analysis.shared.api_clients import TheOddsAPIClient
        client = TheOddsAPIClient(api_key)

        events = client.get_nba_events(target_date)
        if not events:
            return {"status": "no_events", "events_fetched": 0, "new_rows": 0}

        all_raw: list[dict] = []
        for ev in events:
            ev_id = ev.get("event_id")
            if not ev_id:
                continue
            ev_rows = client.get_player_props(ev_id, markets=_PROP_MARKETS)
            for r in ev_rows:
                r["event_id"] = ev_id
                r["game_date"] = target_date
            all_raw.extend(ev_rows)

        if not all_raw:
            return {"status": "no_props", "events_fetched": len(events), "new_rows": 0}

        # Build consensus lines across books
        consensus = _consensus_lines(all_raw)

        # Flatten to DuckDB rows
        insert_rows: list[tuple] = []
        seen_players: set[str] = set()
        for key, vals in consensus.items():
            player_slug, market = key.split("::", 1)
            # Find the original (non-slugified) player name
            original_name = next(
                (r["player"] for r in all_raw if _slugify(r.get("player", "")) == player_slug),
                player_slug,
            )
            seen_players.add(original_name)
            insert_rows.append((
                # Match a single event — use first matching event_id
                next(
                    (r["event_id"] for r in all_raw if _slugify(r.get("player", "")) == player_slug),
                    "unknown",
                ),
                target_date,
                original_name,
                market,
                vals["line"],
                vals["over_odds"],
                vals["under_odds"],
                "consensus",
            ))

        try:
            import duckdb
            db_path.parent.mkdir(parents=True, exist_ok=True)
            from analysis.shared.db import get_conn as _get_conn
            con = _get_conn(db_path)
            _ensure_table(con)
            # Delete today's stale data first (allow full replace)
            con.execute(
                "DELETE FROM player_prop_lines WHERE game_date = ?",
                [target_date],
            )
            con.executemany(
                """INSERT OR IGNORE INTO player_prop_lines
                   (event_id, game_date, player_name, market, line, over_odds, under_odds, bookmaker)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                insert_rows,
            )
        except Exception as db_exc:
            log.warning("Props DB write failed: %s", db_exc)
            return {"status": "db_error", "error": str(db_exc)}

        log.info(
            "Props refreshed: %d players, %d lines stored for %s",
            len(seen_players), len(insert_rows), target_date,
        )
        return {
            "status": "updated",
            "events_fetched": len(events),
            "new_rows": len(insert_rows),
            "players_covered": len(seen_players),
            "game_date": target_date,
        }

    except Exception as exc:
        log.warning("Props refresh failed: %s", exc)
        return {"status": "error", "error": str(exc)}


def load_props(
    game_date: str | None = None,
    db_path: Path = _DEFAULT_DB,
) -> pd.DataFrame:
    """
    Load prop lines for ``game_date`` from the DB.

    Returns DataFrame with columns:
        player_name, market, line, over_odds, under_odds
    or an empty DataFrame on any failure.
    """
    target_date = game_date or date.today().isoformat()
    if not db_path.exists():
        return pd.DataFrame()
    try:
        from analysis.shared.db import get_conn as _get_conn
        con = _get_conn(db_path)
        _ensure_table(con)  # create table if it doesn't exist yet
        df = con.execute(
            """
            SELECT player_name, market, line, over_odds, under_odds
            FROM player_prop_lines
            WHERE game_date = ?
            """,
            [target_date],
        ).df()
        return df
    except Exception as exc:
        log.warning("Props load failed: %s", exc)
        return pd.DataFrame()


def enrich_with_props(
    projections_df: pd.DataFrame,
    game_date: str | None = None,
    db_path: Path = _DEFAULT_DB,
) -> pd.DataFrame:
    """
    Add prop line columns and delta columns to a projections DataFrame.

    Columns added (when data is available):
        prop_pts_line   — consensus over/under points line
        prop_reb_line   — rebounds line
        prop_ast_line   — assists line
        prop_3s_line    — threes line
        proj_vs_prop    — Proj − prop_pts_line  (+ = we project above the line)

    Matching is fuzzy-slug-based so "LeBron James" matches "LeBron James Jr." etc.
    """
    df = projections_df.copy()
    props_df = load_props(game_date, db_path)
    if props_df.empty:
        return df

    # Build lookup: slugified_player → {market → line}
    lookup: dict[str, dict[str, float]] = {}
    for _, row in props_df.iterrows():
        slug = _slugify(str(row["player_name"]))
        mkt = str(row["market"])
        if slug not in lookup:
            lookup[slug] = {}
        if row["line"] is not None:
            lookup[slug][mkt] = float(row["line"])

    name_col = next(
        (c for c in ["Name", "Player", "player_name"] if c in df.columns),
        None,
    )
    if name_col is None:
        return df

    for market, line_col in _MARKET_TO_LINE_COL.items():
        vals: list[float | None] = []
        for _, row in df.iterrows():
            slug = _slugify(str(row.get(name_col, "")))
            vals.append(lookup.get(slug, {}).get(market))
        df[line_col] = vals

    # Primary edge signal: our projection vs the market's points line
    if "Proj" in df.columns and "prop_pts_line" in df.columns:
        proj = pd.to_numeric(df["Proj"], errors="coerce")
        pts_line = pd.to_numeric(df["prop_pts_line"], errors="coerce")
        df["proj_vs_prop"] = (proj - pts_line).round(2)

    covered = int(df["prop_pts_line"].notna().sum()) if "prop_pts_line" in df.columns else 0
    log.info("Props enrichment: %d/%d players have a points line", covered, len(df))
    return df
