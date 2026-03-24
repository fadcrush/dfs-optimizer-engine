"""
Fantasy Scoring Calculators — NBA DFS
======================================
Canonical DraftKings and FanDuel scoring implementations for NBA Classic slates.

DraftKings NBA Classic
----------------------
  PTS  = +1.0        (all scored points)
  3PM  = +0.5        (made three-pointer bonus, on top of the +1 point)
  REB  = +1.25
  AST  = +1.5
  STL  = +2.0
  BLK  = +2.0
  TOV  = -0.5
  Double-double bonus  = +1.5   (2 qualifying categories ≥ 10)
  Triple-double bonus  = +3.0   (3 qualifying categories ≥ 10; mutually exclusive with DD)
  Qualifying categories for bonuses: PTS, REB, AST, STL, BLK

FanDuel NBA
-----------
  FGM  = +2.0        (every field goal made — includes 3-pointers)
  FTM  = +1.0
  3PM  = +1.0        (additional bonus per made three, on top of FGM value)
  REB  = +1.2
  AST  = +1.5
  STL  = +3.0
  BLK  = +3.0
  TOV  = -1.0

Notes
-----
* These functions are the single source of truth used by player_trends_service.
* All inputs are floats; None / NaN / missing values are treated as 0.
* Double-double and triple-double bonuses are mutually exclusive on DraftKings
  (triple-double supersedes double-double).
"""
from __future__ import annotations

import math
from typing import Any


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _f(v: Any) -> float:
    """Safely coerce any value to float, returning 0.0 for None / NaN."""
    if v is None:
        return 0.0
    try:
        r = float(v)
        return 0.0 if math.isnan(r) or math.isinf(r) else r
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Double / Triple Double detection
# ---------------------------------------------------------------------------

def compute_dd_td(
    pts: float,
    reb: float,
    ast: float,
    blk: float,
    stl: float,
) -> tuple[bool, bool]:
    """Return ``(double_double, triple_double)`` booleans for DraftKings bonus logic.

    A stat category qualifies when the player records **≥ 10** in it.
    The five qualifying categories are: points, rebounds, assists, blocks, steals.

    Triple-double takes precedence — a player earns at most one bonus:
      - 3+ categories ≥ 10  → triple-double (+3.0)
      - 2 categories ≥ 10   → double-double (+1.5)
      - 0 or 1 category ≥ 10 → no bonus
    """
    count = sum(1 for v in (pts, reb, ast, blk, stl) if v >= 10.0)
    triple_double = count >= 3
    double_double = (not triple_double) and (count >= 2)
    return double_double, triple_double


# ---------------------------------------------------------------------------
# DraftKings
# ---------------------------------------------------------------------------

def compute_dk_score(
    pts: float,
    three_pm: float,
    reb: float,
    ast: float,
    stl: float,
    blk: float,
    tov: float,
) -> float:
    """Compute DraftKings NBA Classic fantasy score from raw stat totals.

    Args:
        pts:      Total points scored (including three-point baskets).
        three_pm: Made three-pointers (earns +0.5 bonus each).
        reb:      Total rebounds.
        ast:      Assists.
        stl:      Steals.
        blk:      Blocks.
        tov:      Turnovers.

    Returns:
        Float DK fantasy score, including any double/triple-double bonus.
    """
    pts     = _f(pts)
    three_pm = _f(three_pm)
    reb     = _f(reb)
    ast     = _f(ast)
    stl     = _f(stl)
    blk     = _f(blk)
    tov     = _f(tov)

    score = (
        pts     * 1.0
        + three_pm * 0.5
        + reb     * 1.25
        + ast     * 1.5
        + stl     * 2.0
        + blk     * 2.0
        + tov     * -0.5
    )

    dd, td = compute_dd_td(pts, reb, ast, blk, stl)
    if td:
        score += 3.0
    elif dd:
        score += 1.5

    return round(score, 4)


# ---------------------------------------------------------------------------
# FanDuel
# ---------------------------------------------------------------------------

def compute_fd_score(
    fgm: float,
    ftm: float,
    three_pm: float,
    reb: float,
    ast: float,
    stl: float,
    blk: float,
    tov: float,
) -> float:
    """Compute FanDuel NBA fantasy score from raw stat totals.

    FGM covers all field goals (including three-pointers) at +2 each.
    Each made three-pointer earns an additional +1 bonus.

    Args:
        fgm:      Total field goals made (2-pointers + 3-pointers).
        ftm:      Free throws made.
        three_pm: Made three-pointers (additional +1 bonus each).
        reb:      Total rebounds.
        ast:      Assists.
        stl:      Steals.
        blk:      Blocks.
        tov:      Turnovers.

    Returns:
        Float FD fantasy score.
    """
    fgm     = _f(fgm)
    ftm     = _f(ftm)
    three_pm = _f(three_pm)
    reb     = _f(reb)
    ast     = _f(ast)
    stl     = _f(stl)
    blk     = _f(blk)
    tov     = _f(tov)

    return round(
        fgm     * 2.0
        + ftm     * 1.0
        + three_pm * 1.0
        + reb     * 1.2
        + ast     * 1.5
        + stl     * 3.0
        + blk     * 3.0
        + tov     * -1.0,
        4,
    )


# ---------------------------------------------------------------------------
# Unified game-row entry point
# ---------------------------------------------------------------------------

def score_game_row(row: dict[str, Any]) -> tuple[float, float]:
    """Return ``(dk_score, fd_score)`` for a single game-log row dict.

    Reads column names as stored in ``player_game_logs``:
      points, three_pointers, rebounds, assists, steals, blocks,
      turnovers, fg_made, ft_made.

    Any missing or None values are treated as 0.  Safe to call on
    partial rows (e.g. rows where stat columns were not ingested).
    """
    pts      = _f(row.get("points"))
    tpm      = _f(row.get("three_pointers"))
    reb      = _f(row.get("rebounds"))
    ast      = _f(row.get("assists"))
    stl      = _f(row.get("steals"))
    blk      = _f(row.get("blocks"))
    tov      = _f(row.get("turnovers"))
    fgm      = _f(row.get("fg_made"))
    ftm      = _f(row.get("ft_made"))

    dk = compute_dk_score(pts, tpm, reb, ast, stl, blk, tov)
    fd = compute_fd_score(fgm, ftm, tpm, reb, ast, stl, blk, tov)
    return dk, fd
