"""
NBA Game Log Ingestion Script
Pulls current season player game logs from stats.nba.com (via nba_api — free,
no API key required) and stores them in data/dfs_edge.duckdb as player_game_logs.

Also ingests season averages from stats.nba.com (also via nba_api, free) into
player_season_stats for projection baseline.  No paid API key needed at all.

Usage:
    python scripts/ingest_game_logs.py                    # full 2025-26 season
    python scripts/ingest_game_logs.py --season 2025      # 2024-25 historical
    python scripts/ingest_game_logs.py --days 14          # catch-up: last N days
    python scripts/ingest_game_logs.py --stats-only       # only season averages
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
DB_PATH = ROOT / "data" / "dfs_edge.duckdb"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── DDL ─────────────────────────────────────────────────────────────────────

CREATE_GAME_LOGS = """
CREATE TABLE IF NOT EXISTS player_game_logs (
    game_id          VARCHAR,
    player_id        VARCHAR NOT NULL,      -- stats.nba.com numeric player ID
    player_name      VARCHAR,
    team             VARCHAR,
    opponent         VARCHAR,
    game_date        DATE NOT NULL,
    season           INTEGER,
    wl               VARCHAR,              -- 'W' or 'L'
    is_home          BOOLEAN,

    minutes          DOUBLE,
    points           DOUBLE,
    rebounds         DOUBLE,
    assists          DOUBLE,
    steals           DOUBLE,
    blocks           DOUBLE,
    turnovers        DOUBLE,
    pf               DOUBLE,
    three_pointers   DOUBLE,
    fg_attempted     DOUBLE,
    fg_made          DOUBLE,
    fg_pct           DOUBLE,
    ft_attempted     DOUBLE,
    ft_made          DOUBLE,

    dk_pts           DOUBLE,
    fd_pts           DOUBLE,

    ingested_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (game_id, player_id)
);
"""

CREATE_SEASON_STATS = """
CREATE TABLE IF NOT EXISTS player_season_stats (
    player_id        VARCHAR,
    player_name      VARCHAR,
    team             VARCHAR,
    season           INTEGER,
    games_played     INTEGER,
    minutes_per_game DOUBLE,
    points           DOUBLE,
    rebounds         DOUBLE,
    assists          DOUBLE,
    steals           DOUBLE,
    blocks           DOUBLE,
    turnovers        DOUBLE,
    three_pointers   DOUBLE,
    fg_pct           DOUBLE,
    ft_pct           DOUBLE,
    dk_pts_avg       DOUBLE,
    fd_pts_avg       DOUBLE,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (player_id, season)
);
"""


# ── DK / FD scoring ─────────────────────────────────────────────────────────

def _dk(pts, reb, ast, stl, blk, to, three, **_):
    cats = sum(1 for v in [pts, reb, ast, stl, blk] if (v or 0) >= 10)
    bonus = (1.5 if cats >= 2 else 0) + (3.0 if cats >= 3 else 0)
    return round(
        (pts or 0) * 1.0 + (reb or 0) * 1.25 + (ast or 0) * 1.5 +
        (stl or 0) * 2.0 + (blk or 0) * 2.0 + (to or 0) * -0.5 +
        (three or 0) * 0.5 + bonus, 3
    )


def _fd(pts, reb, ast, stl, blk, to, **_):
    return round(
        (pts or 0) * 1.0 + (reb or 0) * 1.2 + (ast or 0) * 1.5 +
        (stl or 0) * 3.0 + (blk or 0) * 3.0 + (to or 0) * -1.0, 3
    )


# ── nba_api helpers ──────────────────────────────────────────────────────────

def season_str(year: int) -> str:
    """2026 → '2025-26'"""
    return f"{year - 1}-{str(year)[2:]}"


def fetch_league_game_log(season: int) -> pd.DataFrame:
    """Pull ALL player game logs for a season from stats.nba.com (free)."""
    from nba_api.stats.endpoints import leaguegamelog

    s = season_str(season)
    log.info("Fetching LeagueGameLog season=%s from stats.nba.com …", s)
    time.sleep(1)

    try:
        ep = leaguegamelog.LeagueGameLog(
            season=s,
            season_type_all_star="Regular Season",
            player_or_team_abbreviation="P",
            timeout=120,
        )
        df = ep.get_data_frames()[0]
        log.info("Fetched %d player-game rows", len(df))
        return df
    except Exception as exc:
        log.error("LeagueGameLog failed: %s", exc)
        return pd.DataFrame()


def fetch_recent_game_log(season: int, days: int) -> pd.DataFrame:
    """Pull game logs only for the last N days (fast catch-up)."""
    from nba_api.stats.endpoints import leaguegamelog

    end_dt   = datetime.now()
    start_dt = end_dt - timedelta(days=days)
    date_from = start_dt.strftime("%m/%d/%Y")
    date_to   = end_dt.strftime("%m/%d/%Y")
    s = season_str(season)

    log.info("Catch-up fetch: %s → %s  (season=%s)", date_from, date_to, s)
    time.sleep(1)

    try:
        ep = leaguegamelog.LeagueGameLog(
            season=s,
            season_type_all_star="Regular Season",
            player_or_team_abbreviation="P",
            date_from_nullable=date_from,
            date_to_nullable=date_to,
            timeout=60,
        )
        df = ep.get_data_frames()[0]
        log.info("Fetched %d player-game rows", len(df))
        return df
    except Exception as exc:
        log.error("LeagueGameLog (date range) failed: %s", exc)
        return pd.DataFrame()


def normalize_game_log(df: pd.DataFrame, season: int) -> list[dict]:
    """Map nba_api LeagueGameLog columns → our player_game_logs schema."""
    if df.empty:
        return []

    records = []
    for _, row in df.iterrows():
        matchup = str(row.get("MATCHUP", ""))
        is_home = "vs." in matchup
        # "LAL vs. GSW" → opponent=GSW,  "LAL @ GSW" → opponent=GSW
        opponent = matchup.split("vs. ")[-1] if is_home else matchup.split("@ ")[-1]

        # Parse "32:14" → 32.23
        mins_raw = str(row.get("MIN", "0") or "0")
        try:
            if ":" in mins_raw:
                m, s = mins_raw.split(":")
                minutes = int(m) + int(s) / 60
            else:
                minutes = float(mins_raw)
        except (ValueError, TypeError):
            minutes = 0.0

        pts   = float(row.get("PTS") or 0)
        reb   = float(row.get("REB") or 0)
        ast   = float(row.get("AST") or 0)
        stl   = float(row.get("STL") or 0)
        blk   = float(row.get("BLK") or 0)
        to    = float(row.get("TOV") or 0)
        three = float(row.get("FG3M") or 0)

        records.append({
            "game_id":        str(row.get("GAME_ID", "")),
            "player_id":      str(row.get("PLAYER_ID", "")),
            "player_name":    str(row.get("PLAYER_NAME", "")),
            "team":           str(row.get("TEAM_ABBREVIATION", "")),
            "opponent":       opponent.strip(),
            "game_date":      pd.to_datetime(row.get("GAME_DATE")).date(),
            "season":         season,
            "wl":             str(row.get("WL", "")),
            "is_home":        is_home,
            "minutes":        round(minutes, 2),
            "points":         pts,
            "rebounds":       reb,
            "assists":        ast,
            "steals":         stl,
            "blocks":         blk,
            "turnovers":      to,
            "pf":             float(row.get("PF") or 0),
            "three_pointers": three,
            "fg_attempted":   float(row.get("FGA") or 0),
            "fg_made":        float(row.get("FGM") or 0),
            "fg_pct":         float(row.get("FG_PCT") or 0),
            "ft_attempted":   float(row.get("FTA") or 0),
            "ft_made":        float(row.get("FTM") or 0),
            "dk_pts":         _dk(pts, reb, ast, stl, blk, to, three),
            "fd_pts":         _fd(pts, reb, ast, stl, blk, to),
        })
    return records


# ── Season averages via nba_api (free — no API key required) ─────────────────

def fetch_season_averages(season: int) -> list[dict]:
    """Fetch per-game season averages for all NBA players using nba_api.

    Uses LeagueDashPlayerStats (stats.nba.com) — completely free, no API key.
    Returns records in the same schema as player_season_stats.
    """
    try:
        from nba_api.stats.endpoints import leaguedashplayerstats
    except ImportError:
        log.warning("nba_api not installed — skipping season averages")
        return []

    # nba_api season IDs: 2026 → '2025-26'
    season_str = f"{season - 1}-{str(season)[2:]}"
    try:
        ls = leaguedashplayerstats.LeagueDashPlayerStats(
            season=season_str,
            per_mode_simple="PerGame",
            timeout=60,
        )
        df = ls.get_data_frames()[0]
    except Exception as exc:
        log.warning("nba_api LeagueDashPlayerStats failed: %s", exc)
        return []

    records = []
    for _, row in df.iterrows():
        pts   = float(row.get("PTS") or 0)
        reb   = float(row.get("REB") or 0)
        ast   = float(row.get("AST") or 0)
        stl   = float(row.get("STL") or 0)
        blk   = float(row.get("BLK") or 0)
        to    = float(row.get("TOV") or 0)
        three = float(row.get("FG3M") or 0)
        mins  = float(row.get("MIN") or 0)
        gp    = int(row.get("GP") or 1)

        records.append({
            "player_id":        f"nba_{row['PLAYER_ID']}",
            "player_name":      str(row.get("PLAYER_NAME") or ""),
            "team":             str(row.get("TEAM_ABBREVIATION") or ""),
            "season":           season,
            "games_played":     gp,
            "minutes_per_game": round(mins, 2),
            "points":           round(pts, 2),
            "rebounds":         round(reb, 2),
            "assists":          round(ast, 2),
            "steals":           round(stl, 2),
            "blocks":           round(blk, 2),
            "turnovers":        round(to, 2),
            "three_pointers":   round(three, 2),
            "fg_pct":           round(float(row.get("FG_PCT") or 0), 3),
            "ft_pct":           round(float(row.get("FT_PCT") or 0), 3),
            "dk_pts_avg":       _dk(pts, reb, ast, stl, blk, to, three),
            "fd_pts_avg":       _fd(pts, reb, ast, stl, blk, to),
        })

    log.info("nba_api: fetched season averages for %d players (season %s)", len(records), season_str)
    return records


# ── DB write helpers ─────────────────────────────────────────────────────────

UPSERT_LOGS = """
INSERT OR REPLACE INTO player_game_logs VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    CURRENT_TIMESTAMP
)
"""

UPSERT_SEASON = """
INSERT OR REPLACE INTO player_season_stats VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP
)
"""


def write_records(con, sql: str, records: list[dict], label: str):
    if not records:
        log.info("%s: nothing to write", label)
        return
    con.executemany(sql, [list(r.values()) for r in records])
    log.info("%s: upserted %d rows", label, len(records))


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Ingest NBA game logs into dfs_edge.duckdb")
    parser.add_argument("--season",     type=int, default=2026,
                        help="Season end-year  (2026 = 2025-26 season, default)")
    parser.add_argument("--days",       type=int, default=None,
                        help="Catch-up: only last N days instead of full season")
    parser.add_argument("--stats-only", action="store_true",
                        help="Skip game logs; only ingest SportsData season averages")
    args = parser.parse_args()

    log.info("Opening DB: %s", DB_PATH)
    con = duckdb.connect(str(DB_PATH))
    con.execute(CREATE_GAME_LOGS)
    con.execute(CREATE_SEASON_STATS)

    existing = con.execute("SELECT COUNT(*) FROM player_game_logs").fetchone()[0]
    log.info("Existing player_game_logs rows: %d", existing)

    # ── Ingest game logs ──────────────────────────────────────────────────
    if not args.stats_only:
        df = (
            fetch_recent_game_log(args.season, args.days)
            if args.days
            else fetch_league_game_log(args.season)
        )
        records = normalize_game_log(df, args.season)
        write_records(con, UPSERT_LOGS, records, "player_game_logs")

    # ── Ingest season averages (always, unless explicitly skipping) ───────
    avg_records = fetch_season_averages(args.season)
    write_records(con, UPSERT_SEASON, avg_records, "player_season_stats")

    # ── Summary ───────────────────────────────────────────────────────────
    total = con.execute("SELECT COUNT(*) FROM player_game_logs").fetchone()[0]
    seasons_avg = con.execute("SELECT COUNT(*) FROM player_season_stats").fetchone()[0]
    log.info("player_game_logs total: %d   |   player_season_stats total: %d", total, seasons_avg)

    if total > 0:
        sample = con.execute("""
            SELECT player_name, team, game_date, minutes, points, assists, rebounds, dk_pts
            FROM player_game_logs
            ORDER BY game_date DESC, dk_pts DESC
            LIMIT 8
        """).df()
        log.info("Recent top performers:\n%s", sample.to_string(index=False))

    con.close()
    log.info("Done.")


if __name__ == "__main__":
    main()
