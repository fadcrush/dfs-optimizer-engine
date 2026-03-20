"""NFL Projection Engine — generates per-player fantasy projections from a normalized slate DataFrame.

Scoring ruleset:
  DraftKings Classic:
    PASS_YD * 0.04  | PASS_TD * 4   | PASS_INT * -1
    RUSH_YD * 0.1   | RUSH_TD * 6   | FUMBLE_LOST * -1
    REC * 1.0 (PPR) | REC_YD * 0.1  | REC_TD * 6
    DST: dynamic (see _score_dst_dk)

  FanDuel Classic:
    PASS_YD * 0.04  | PASS_TD * 4   | PASS_INT * -1
    RUSH_YD * 0.1   | RUSH_TD * 6   | FUMBLE_LOST * -1
    REC * 0.5 (half-PPR) | REC_YD * 0.1 | REC_TD * 6

Usage::
    from analysis.nfl.projection_engine import NFLProjectionEngine
    from analysis.schemas.projection import ProjectionContext
    df_proj = NFLProjectionEngine().generate(slate_df, context)
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scoring multipliers
# ---------------------------------------------------------------------------

_DK_MULTIPLIERS: dict[str, float] = {
    "PASS_YD": 0.04,
    "PASS_TD": 4.0,
    "PASS_INT": -1.0,
    "RUSH_YD": 0.1,
    "RUSH_TD": 6.0,
    "REC": 1.0,          # full PPR
    "REC_YD": 0.1,
    "REC_TD": 6.0,
    "FUMBLE_LOST": -1.0,
    "TWO_PT": 2.0,
    "RETURN_TD": 6.0,
}

_FD_MULTIPLIERS: dict[str, float] = {
    "PASS_YD": 0.04,
    "PASS_TD": 4.0,
    "PASS_INT": -1.0,
    "RUSH_YD": 0.1,
    "RUSH_TD": 6.0,
    "REC": 0.5,          # half-PPR
    "REC_YD": 0.1,
    "REC_TD": 6.0,
    "FUMBLE_LOST": -1.0,
    "TWO_PT": 2.0,
    "RETURN_TD": 6.0,
}

# ---------------------------------------------------------------------------
# Column name aliases: maps various source column spellings → canonical names
# ---------------------------------------------------------------------------

_COLUMN_ALIASES: dict[str, str] = {
    # Passing
    "passing_yards": "PASS_YD",
    "pass_yds": "PASS_YD",
    "PassYds": "PASS_YD",
    "projPassYds": "PASS_YD",
    "passing_tds": "PASS_TD",
    "pass_td": "PASS_TD",
    "PassTD": "PASS_TD",
    "projPassTD": "PASS_TD",
    "interceptions": "PASS_INT",
    "pass_int": "PASS_INT",
    "INT": "PASS_INT",
    # Rushing
    "rushing_yards": "RUSH_YD",
    "rush_yds": "RUSH_YD",
    "RushYds": "RUSH_YD",
    "projRushYds": "RUSH_YD",
    "rushing_tds": "RUSH_TD",
    "rush_td": "RUSH_TD",
    "RushTD": "RUSH_TD",
    "projRushTD": "RUSH_TD",
    # Receiving
    "receptions": "REC",
    "rec": "REC",
    "targets": "TARGETS",
    "receiving_yards": "REC_YD",
    "rec_yds": "REC_YD",
    "RecYds": "REC_YD",
    "projRecYds": "REC_YD",
    "receiving_tds": "REC_TD",
    "rec_td": "REC_TD",
    "RecTD": "REC_TD",
    "projRecTD": "REC_TD",
    # Misc
    "fumbles_lost": "FUMBLE_LOST",
    "fumble_lost": "FUMBLE_LOST",
    "two_pt_conversions": "TWO_PT",
    "2pt": "TWO_PT",
    "return_td": "RETURN_TD",
    # DST / defence
    "sacks": "DST_SACK",
    "dst_sack": "DST_SACK",
    "def_int": "DST_INT",
    "interceptions_dst": "DST_INT",
    "fumble_rec": "DST_FR",
    "fumbles_recovered": "DST_FR",
    "def_td": "DST_TD",
    "safety": "DST_SAFETY",
    "pts_allowed": "DST_PA",
    "points_allowed": "DST_PA",
    "yards_allowed": "DST_YA",
}

# DK DST points-allowed thresholds
_DST_PA_POINTS_DK = [
    (0, 10),
    (1, 7),
    (6, 4),
    (13, 1),
    (17, 0),
    (28, -1),
    (35, -4),
]

# DK DST yards-allowed thresholds
_DST_YA_POINTS_DK = [
    (0, 6),
    (100, 4),
    (200, 3),
    (300, 2),
    (350, 0),
    (400, -1),
    (450, -3),
    (550, -5),
]


def _score_dst_dk(row: pd.Series) -> float:
    pts = 0.0
    pts += row.get("DST_SACK", 0) * 1.0
    pts += row.get("DST_INT", 0) * 2.0
    pts += row.get("DST_FR", 0) * 2.0
    pts += row.get("DST_TD", 0) * 6.0
    pts += row.get("DST_SAFETY", 0) * 2.0
    pts += row.get("DST_BLK_KICK", 0) * 2.0

    pa = row.get("DST_PA", None)
    if pa is not None:
        for threshold, bonus in reversed(_DST_PA_POINTS_DK):
            if pa >= threshold:
                pts += bonus
                break

    ya = row.get("DST_YA", None)
    if ya is not None:
        for threshold, bonus in reversed(_DST_YA_POINTS_DK):
            if ya >= threshold:
                pts += bonus
                break

    return pts


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

class NFLProjectionEngine:
    """Engine that projects fantasy points for each player on an NFL slate."""

    def generate(self, slate_df: pd.DataFrame, context: Any) -> pd.DataFrame:
        """Return a copy of *slate_df* with a ``Projection`` column added.

        Args:
            slate_df: Normalized slate DataFrame (must contain at least ``Position``
                and ``Salary`` columns; stat columns are optional but increase accuracy).
            context: ``ProjectionContext`` instance (uses ``.site`` for scoring ruleset).

        Returns:
            DataFrame with ``Projection`` column (DFS fantasy points float).
        """
        df = slate_df.copy()
        df = self._rename_columns(df)

        site = (getattr(context, "site", None) or "DK").upper()
        mults = _FD_MULTIPLIERS if site == "FD" else _DK_MULTIPLIERS

        df["Proj"] = df.apply(
            lambda row: self._calc_pts(row, mults, site), axis=1
        )
        # Keep "Projection" alias so NFLOptimizer._prepare() also works
        df["Projection"] = df["Proj"]

        # Value (Proj / Salary * 1000)
        if "Salary" in df.columns:
            df["Value"] = df.apply(
                lambda row: (row["Proj"] / row["Salary"] * 1000)
                if row.get("Salary", 0) > 0
                else 0.0,
                axis=1,
            )
        else:
            df["Value"] = 0.0

        log.info(
            "NFLProjectionEngine: generated %d projections for site=%s",
            len(df),
            site,
        )
        return df

    # ------------------------------------------------------------------

    def _rename_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        rename_map = {
            src: dst
            for src, dst in _COLUMN_ALIASES.items()
            if src in df.columns
        }
        if rename_map:
            df = df.rename(columns=rename_map)
        return df

    def _calc_pts(
        self, row: pd.Series, mults: dict[str, float], site: str
    ) -> float:
        pos = str(row.get("Position", row.get("Roster Position", ""))).upper()

        if "DST" in pos or "D/ST" in pos:
            return _score_dst_dk(row) if site != "FD" else _score_dst_fd(row)

        pts = 0.0
        for stat_col, multiplier in mults.items():
            val = row.get(stat_col, 0)
            if pd.notna(val):
                pts += float(val) * multiplier

        return round(pts, 4)


def _score_dst_fd(row: pd.Series) -> float:
    """FanDuel DST scoring (single-game leagues use a different scale)."""
    pts = 0.0
    pts += row.get("DST_SACK", 0) * 1.0
    pts += row.get("DST_INT", 0) * 2.0
    pts += row.get("DST_FR", 0) * 2.0
    pts += row.get("DST_TD", 0) * 6.0
    pts += row.get("DST_SAFETY", 0) * 2.0
    pts += row.get("DST_BLK_KICK", 0) * 2.0

    pa = row.get("DST_PA", None)
    if pa is not None:
        if pa == 0:
            pts += 10
        elif pa <= 6:
            pts += 7
        elif pa <= 13:
            pts += 4
        elif pa <= 20:
            pts += 1
        elif pa <= 34:
            pts += 0
        else:
            pts -= 3

    return pts
