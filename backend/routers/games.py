"""
Games / Odds Routes
Serves today's NBA matchups with betting odds.
Uses TheOdds API when THEODDS_API_KEY is set; falls back to mock data.
"""

from __future__ import annotations

import os
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from fastapi import APIRouter

router = APIRouter(prefix="/api/games", tags=["Games"])

# ---------------------------------------------------------------------------
# Team abbreviation map (full name → abbr)
# ---------------------------------------------------------------------------
NBA_ABBR: dict[str, str] = {
    "Atlanta Hawks": "ATL",
    "Boston Celtics": "BOS",
    "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI",
    "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL",
    "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW",
    "Houston Rockets": "HOU",
    "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC",
    "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}

MOCK_GAMES = [
    {"id": "BOS_MIA", "home_team": "Boston Celtics",      "away_team": "Miami Heat",            "home_abbr": "BOS", "away_abbr": "MIA", "time": "7:30 PM ET", "spread_home": -3.5, "total": 221.0,  "home_ml": -180, "away_ml": +155},
    {"id": "LAL_GSW", "home_team": "Los Angeles Lakers",  "away_team": "Golden State Warriors", "home_abbr": "LAL", "away_abbr": "GSW", "time": "10:00 PM ET","spread_home":  1.5, "total": 228.5,  "home_ml": +110, "away_ml": -130},
    {"id": "DEN_PHI", "home_team": "Denver Nuggets",      "away_team": "Philadelphia 76ers",    "home_abbr": "DEN", "away_abbr": "PHI", "time": "8:00 PM ET", "spread_home": -5.5, "total": 226.0,  "home_ml": -220, "away_ml": +185},
    {"id": "MIL_CHI", "home_team": "Milwaukee Bucks",     "away_team": "Chicago Bulls",         "home_abbr": "MIL", "away_abbr": "CHI", "time": "7:00 PM ET", "spread_home": -8.5, "total": 219.5,  "home_ml": -380, "away_ml": +305},
    {"id": "PHX_DAL", "home_team": "Phoenix Suns",        "away_team": "Dallas Mavericks",      "home_abbr": "PHX", "away_abbr": "DAL", "time": "9:00 PM ET", "spread_home":  2.5, "total": 225.5,  "home_ml": +125, "away_ml": -150},
    {"id": "OKC_NOP", "home_team": "Oklahoma City Thunder","away_team": "New Orleans Pelicans",  "home_abbr": "OKC", "away_abbr": "NOP", "time": "8:30 PM ET", "spread_home": -7.0, "total": 224.0,  "home_ml": -290, "away_ml": +240},
]


def _abbr(team_name: str) -> str:
    return NBA_ABBR.get(team_name, team_name[:3].upper())


def _et_offset(dt_utc: datetime) -> int:
    """Return the US Eastern UTC offset in hours (-4 EDT or -5 EST)."""
    # DST: second Sunday of March  → first Sunday of November
    year = dt_utc.year
    # Second Sunday in March (2 AM ET = 7 AM UTC)
    march1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    dst_start = march1 + timedelta(days=(6 - march1.weekday()) % 7 + 7)
    dst_start = dst_start.replace(hour=7)
    # First Sunday in November (2 AM ET = 6 AM UTC)
    nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    dst_end = dst_end.replace(hour=6)
    return -4 if dst_start <= dt_utc < dst_end else -5


def _fmt_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        offset = _et_offset(dt)
        et = dt + timedelta(hours=offset)
        # NBA tip-offs are always on the hour or half-hour — snap to nearest :00/:30
        m = et.minute
        if m < 15:
            et = et.replace(minute=0, second=0, microsecond=0)
        elif m < 45:
            et = et.replace(minute=30, second=0, microsecond=0)
        else:
            et = (et + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        h = et.hour
        suffix = "PM" if h >= 12 else "AM"
        h12 = h % 12 or 12
        return f"{h12}:{et.minute:02d} {suffix} ET"
    except Exception:
        return "TBD"


def _fmt_ml(val: int | None) -> str:
    if val is None:
        return "N/A"
    return f"+{val}" if val > 0 else str(val)


def _fetch_live_games() -> list[dict]:
    api_key = os.getenv("THEODDS_API_KEY", "").strip()
    if not api_key:
        return []
    try:
        # Determine today's window in ET (midnight → midnight)
        now_utc = datetime.now(timezone.utc)
        et_offset = _et_offset(now_utc)
        et_now = now_utc + timedelta(hours=et_offset)
        et_midnight = et_now.replace(hour=0, minute=0, second=0, microsecond=0)
        et_end = et_midnight + timedelta(days=1)
        # Convert ET window back to UTC for the API
        window_start = (et_midnight - timedelta(hours=et_offset)).strftime("%Y-%m-%dT%H:%M:%SZ")
        window_end = (et_end - timedelta(hours=et_offset)).strftime("%Y-%m-%dT%H:%M:%SZ")

        resp = requests.get(
            "https://api.the-odds-api.com/v4/sports/basketball_nba/odds",
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": "h2h,spreads,totals",
                "oddsFormat": "american",
                "commenceTimeFrom": window_start,
                "commenceTimeTo": window_end,
            },
            timeout=10,
        )
        if not resp.ok:
            return []

        games = []
        for g in resp.json():
            home = g.get("home_team", "")
            away = g.get("away_team", "")
            time_str = g.get("commence_time", "")

            spread_home: float | None = None
            total: float | None = None
            home_ml: int | None = None
            away_ml: int | None = None

            preferred_books = ["draftkings", "fanduel", "betmgm"]
            bookmakers = g.get("bookmakers", [])
            # Try preferred books first, fallback to any
            ordered = sorted(bookmakers, key=lambda b: preferred_books.index(b["key"]) if b["key"] in preferred_books else 99)
            for bm in ordered:
                for mkt in bm.get("markets", []):
                    if mkt["key"] == "spreads" and spread_home is None:
                        for o in mkt.get("outcomes", []):
                            if o.get("name") == home:
                                spread_home = o.get("point")
                    elif mkt["key"] == "totals" and total is None:
                        for o in mkt.get("outcomes", []):
                            if o.get("name") == "Over":
                                total = o.get("point")
                    elif mkt["key"] == "h2h" and home_ml is None:
                        for o in mkt.get("outcomes", []):
                            if o.get("name") == home:
                                home_ml = o.get("price")
                            else:
                                away_ml = o.get("price")
                if spread_home is not None and total is not None and home_ml is not None:
                    break

            games.append({
                "id": g.get("id", f"{home}_{away}"),
                "home_team": home,
                "away_team": away,
                "home_abbr": _abbr(home),
                "away_abbr": _abbr(away),
                "time": _fmt_time(time_str),
                "spread_home": spread_home,
                "total": total,
                "home_ml": home_ml,
                "away_ml": away_ml,
            })

        return games
    except Exception:
        return []


def _build_matchups_from_slate() -> list[dict]:
    """Generate mock matchups seeded with teams from the latest uploaded slate."""
    try:
        slates_dir = Path(__file__).parent.parent / "uploads" / "slates"
        index_file = slates_dir / "index.json"
        if not index_file.exists():
            return []
        slates = json.loads(index_file.read_text(encoding="utf-8"))
        if not slates:
            return []
        latest = sorted(slates, key=lambda s: s.get("created_at", ""), reverse=True)[0]
        teams: list[str] = latest.get("teams", [])
        if len(teams) < 2:
            return []

        import random
        rng = random.Random(42)
        shuffled = list(teams)
        rng.shuffle(shuffled)
        matchups = []
        times = ["7:00 PM ET", "7:30 PM ET", "8:00 PM ET", "8:30 PM ET", "9:00 PM ET", "10:00 PM ET", "10:30 PM ET"]
        for i in range(0, len(shuffled) - 1, 2):
            home, away = shuffled[i], shuffled[i + 1]
            spread = round(rng.uniform(-9.5, 9.5) * 2) / 2
            total = round(rng.uniform(215, 237) * 2) / 2
            home_ml_abs = rng.randint(105, 380)
            home_ml = -home_ml_abs if spread < 0 else home_ml_abs
            away_ml = -home_ml_abs if home_ml > 0 else home_ml_abs
            matchups.append({
                "id": f"{home}_{away}",
                "home_team": home, "away_team": away,
                "home_abbr": home, "away_abbr": away,
                "time": times[len(matchups) % len(times)],
                "spread_home": spread,
                "total": total,
                "home_ml": -home_ml_abs if spread < 0 else home_ml_abs,
                "away_ml": home_ml_abs if spread < 0 else -home_ml_abs,
            })
        return matchups
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.get("/today")
async def get_today_games():
    """
    Return today's NBA matchups with odds.
    Source priority: TheOdds API → slate-seeded mock → hardcoded mock
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    source = "mock"

    games = _fetch_live_games()
    if games:
        source = "theodds"
    else:
        games = _build_matchups_from_slate()
        if games:
            source = "slate-mock"
        else:
            games = MOCK_GAMES[:]

    return {"games": games, "date": today, "source": source, "count": len(games)}
