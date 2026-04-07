"""
Phase 1 — Canonical Game State
================================

Single source of truth for player lock / start status in a DFS slate.

Provides
--------
- ``PlayerGameState``   — per-player state record
- ``compute_game_state`` — derives state from a slate DataFrame + current UTC time

Lock rules
----------
  lock_threshold = game_start_time − lock_buffer_minutes (default 5)
  locked_flag    = now >= lock_threshold   (player's game is about to start / has started)
  started_flag   = now >= game_start_time  (tip-off has passed)
  swap_eligible  = not started_flag        (player can be used as a *replacement candidate*)

Optimizer rule (auto-lock)
--------------------------
  Any started player already present in a lineup is treated as explicitly
  locked — the optimizer and late-swap engine must not remove them.
  Candidates with started_flag=True are excluded from replacement pools.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

# Minutes before tip-off when a game-clock is considered locked on DFS sites.
LOCK_BUFFER_MINUTES: int = 5


@dataclass
class PlayerGameState:
    """Canonical per-player game-state record for a DFS slate."""

    player_name: str
    player_id: str
    start_time: Optional[datetime]  # UTC game start; None if unknown
    started_flag: bool              # tip-off has passed
    locked_flag: bool               # within lock window or past tip-off
    swap_eligible: bool             # candidate pool eligibility (not started)


# ---------------------------------------------------------------------------
# Internal: start-time parser
# ---------------------------------------------------------------------------

_ISO_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
# DK GameInfo: "AAA@BBB 03/28/2026 07:30PM ET"
_DK_GAME_INFO = re.compile(
    r"(\d{2}/\d{2}/\d{4})\s+(\d{1,2}:\d{2}(?:AM|PM|am|pm))\s*ET",
    re.IGNORECASE,
)


def _is_dst(dt: datetime) -> bool:
    """Return True if the given UTC datetime falls in US Eastern DST."""
    year = dt.year
    # DST starts: second Sunday of March at 2 AM ET (07:00 UTC)
    mar1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7, hours=7)
    # DST ends: first Sunday of November at 2 AM ET (06:00 UTC)
    nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7, hours=6)
    return dst_start <= dt < dst_end


def _parse_start_time(raw: str) -> Optional[datetime]:
    """
    Parse a game start time from slate data into a UTC-aware datetime.

    Supported formats
    -----------------
    - ISO 8601: ``"2026-03-28T19:30:00Z"``
    - DK GameInfo: ``"AAA@BBB 03/28/2026 07:30PM ET"``

    Returns ``None`` if the format is unrecognised or parsing fails.
    """
    if not raw or not isinstance(raw, str):
        return None

    # ISO format
    m = _ISO_PATTERN.search(raw)
    if m:
        try:
            return datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    # DK GameInfo format
    m = _DK_GAME_INFO.search(raw)
    if m:
        try:
            date_str = m.group(1)
            time_str = m.group(2).upper()
            dt_naive = datetime.strptime(f"{date_str} {time_str}", "%m/%d/%Y %I:%M%p")
            # Convert ET → UTC; guess DST based on month (conservative)
            et_hours = 4 if _is_dst(dt_naive.replace(tzinfo=timezone.utc)) else 5
            return (dt_naive + timedelta(hours=et_hours)).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_game_state(
    players_df: pd.DataFrame,
    *,
    now: Optional[datetime] = None,
    lock_buffer_minutes: int = LOCK_BUFFER_MINUTES,
) -> dict[str, PlayerGameState]:
    """
    Derive per-player game state for every player in a slate DataFrame.

    Parameters
    ----------
    players_df          : Slate DataFrame — must have a ``Name`` column.
                          Optional: ``DFS_ID``/``Id``/``ID`` for player IDs;
                          ``GameInfo``/``Game Info``/``Game`` for start-time parsing.
    now                 : Reference UTC time (default: ``datetime.now(timezone.utc)``).
    lock_buffer_minutes : Minutes before tip-off when a player's slot is locked.

    Returns
    -------
    Dict mapping player name (original case) → ``PlayerGameState``.

    Permissive default
    ------------------
    When a game start time cannot be parsed, both ``started_flag`` and
    ``locked_flag`` are set to ``False`` — a player is never incorrectly locked
    due to a missing or unrecognised time string.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    game_col = next(
        (c for c in ["GameInfo", "Game Info", "Game", "game_info"] if c in players_df.columns),
        None,
    )
    id_col = next(
        (c for c in ["DFS_ID", "Id", "ID"] if c in players_df.columns),
        None,
    )

    result: dict[str, PlayerGameState] = {}

    for _, row in players_df.iterrows():
        name = str(row.get("Name", "")).strip()
        if not name:
            continue

        player_id = str(row[id_col]).strip() if id_col else ""
        raw_game = str(row[game_col]).strip() if game_col else ""

        start_time = _parse_start_time(raw_game)
        if start_time is not None:
            lock_threshold = start_time - timedelta(minutes=lock_buffer_minutes)
            started_flag = now >= start_time
            locked_flag = now >= lock_threshold
        else:
            started_flag = False
            locked_flag = False

        result[name] = PlayerGameState(
            player_name=name,
            player_id=player_id,
            start_time=start_time,
            started_flag=started_flag,
            locked_flag=locked_flag,
            swap_eligible=not started_flag,
        )

    return result
