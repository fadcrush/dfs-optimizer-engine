# analysis/shared/scoring.py

from dataclasses import dataclass
from typing import Mapping, Any


@dataclass
class StatLine:
    pts: float = 0.0
    two_pt_made: float = 0.0
    three_pt_made: float = 0.0
    reb: float = 0.0
    ast: float = 0.0
    stl: float = 0.0
    blk: float = 0.0
    tov: float = 0.0


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def dk_score(stat: StatLine, double_double: bool = False, triple_double: bool = False) -> float:
    """DraftKings NBA scoring."""
    score = 0.0
    score += stat.pts * 1.0
    score += stat.three_pt_made * 0.5
    score += stat.reb * 1.25
    score += stat.ast * 1.5
    score += stat.stl * 2.0
    score += stat.blk * 2.0
    score += stat.tov * -0.5
    if triple_double:
        score += 3.0
    elif double_double:
        score += 1.5
    return score


def fd_score(stat: StatLine) -> float:
    """FanDuel NBA scoring."""
    two = stat.two_pt_made
    three = stat.three_pt_made
    pts = stat.pts

    fgm = two + three
    ftm = max(0.0, pts - 2 * two - 3 * three)

    score = 0.0
    score += fgm * 2.0
    score += ftm * 1.0
    score += three * 1.0
    score += stat.reb * 1.2
    score += stat.ast * 1.5
    score += stat.stl * 3.0
    score += stat.blk * 3.0
    score += stat.tov * -1.0
    return score


def score_nba_statline(site: str, stat: StatLine, double_double: bool = False, triple_double: bool = False) -> float:
    """Single entry point for NBA scoring by DFS site."""
    normalized_site = site.upper().strip()
    if normalized_site == "DK":
        return dk_score(stat, double_double=double_double, triple_double=triple_double)
    if normalized_site == "FD":
        return fd_score(stat)
    raise ValueError(f"Unsupported site: {site}. Expected DK or FD.")


def score_nba_row(
    site: str,
    row: Mapping[str, Any],
    double_double: bool = False,
    triple_double: bool = False,
) -> float:
    """Score a row-like object using canonical NBA scoring.

    Supported aliases:
    - points: PTS, Points
    - rebounds: TRB, REB, Rebounds
    - assists: AST, Assists
    - steals: STL, Steals
    - blocks: BLK, Blocks
    - turnovers: TOV, Turnovers
    - 2PM: 2PM, two_pt_made
    - 3PM: 3PM, FG3M, ThreePointersMade, three_pt_made
    """
    stat = StatLine(
        pts=_safe_float(row.get("PTS", row.get("Points", 0))),
        two_pt_made=_safe_float(row.get("2PM", row.get("two_pt_made", 0))),
        three_pt_made=_safe_float(
            row.get("3PM", row.get("FG3M", row.get("ThreePointersMade", row.get("three_pt_made", 0))))
        ),
        reb=_safe_float(row.get("TRB", row.get("REB", row.get("Rebounds", 0)))),
        ast=_safe_float(row.get("AST", row.get("Assists", 0))),
        stl=_safe_float(row.get("STL", row.get("Steals", 0))),
        blk=_safe_float(row.get("BLK", row.get("Blocks", 0))),
        tov=_safe_float(row.get("TOV", row.get("Turnovers", 0))),
    )
    return score_nba_statline(
        site=site,
        stat=stat,
        double_double=double_double,
        triple_double=triple_double,
    )
