# analysis/shared/scoring.py

from dataclasses import dataclass


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


def dk_score(stat: StatLine, double_double: bool = False, triple_double: bool = False) -> float:
    """
    DraftKings NBA Classic scoring (simplified, assumes double/triple flags computed elsewhere).
    Scoring from DK rules: points 1, 3PM +0.5, reb 1.25, ast 1.5, stl 2, blk 2, tov -0.5,
    double-double +1.5, triple-double +3. :contentReference[oaicite:2]{index=2}
    """
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
    """
    FanDuel NBA scoring. Rules: FG 2, FT 1, 3PM 1, reb 1.2, ast 1.5, stl 3, blk 3, tov -1. :contentReference[oaicite:3]{index=3}

    We only know PTS, 2PT, 3PT, so:
    PTS = 2*2PM + 3*3PM + 1*FTM  ->  FTM = PTS - 2*2PM - 3*3PM
    FGM = 2PM + 3PM
    """
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
