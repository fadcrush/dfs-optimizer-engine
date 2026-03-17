"""
Exposure Optimizer
==================
Computes per-player leverage scores, enforces per-player exposure caps/floors
across a portfolio of lineups, and produces a post-generation exposure report.

Concepts
--------
Edge
    ``Proj - field_mean_proj``  — how much better (or worse) our projection
    is vs. the expected field average for that player.  Positive edge means
    the player is undervalued by the field.

Ownership divergence
    ``our_own - field_own_factor * our_own`` — short-hand approximation when
    we don't have an explicit field-ownership feed.  Field ownership is
    estimated as ``our_own * field_own_factor`` (default 1.0, i.e. our
    ownership *is* the field).

Leverage score
    ``edge * (1 + max(0, -ownership_divergence) * own_sensitivity)``
    Players who are both *over-projected vs the field* and *lower owned*
    receive the highest leverage scores.  Used as the objective tie-breaker
    in the LP optimizer.

Usage
-----
    from analysis.core.exposure_optimizer import ExposureConfig, compute_leverage_scores, build_exposure_report

    cfg = ExposureConfig(
        global_max=0.55,
        player_caps={"LeBron James": 0.35, "Nikola Jokic": 0.50},
        player_floors={"Anthony Davis": 0.20},
    )

    # Enrich projections_df before optimization
    projections_df = compute_leverage_scores(projections_df, cfg)

    # After optimization:
    report = build_exposure_report(lineups_df, projections_df, n_lineups=20, cfg=cfg)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class ExposureConfig:
    """
    Per-portfolio exposure settings.

    Parameters
    ----------
    global_max
        Default maximum exposure fraction for any player  (0–1).
        Passed to ``optimize_portfolio`` as ``max_exposure``.
    global_min
        Minimum exposure fraction for a player to be considered a
        forced play.  Players with ``global_min > 0`` are treated as
        required in every lineup unless a per-player floor overrides it.
        Set per-player floors via ``player_floors`` instead for finer control.
    player_caps
        Per-player maximum exposure  {player_name: fraction 0–1}.
        Overrides ``global_max`` for the named player.
    player_floors
        Per-player minimum exposure  {player_name: fraction 0–1}.
        A floor of 0.20 means the player must appear in ≥ 20 % of lineups.
    field_own_factor
        Multiplier applied to our ownership column to estimate field
        ownership when no explicit feed is available.  1.0 = our ownership
        IS the field; 0.8 = assume field is 20 % less chalk than us.
    own_sensitivity
        How aggressively leverage penalises high-ownership players.
        Higher values reward contrarian pivots more strongly.
    edge_weight
        Scaling factor for the projection-edge component of leverage.
    """

    global_max: float = 0.60
    global_min: float = 0.0
    player_caps: dict[str, float] = field(default_factory=dict)
    player_floors: dict[str, float] = field(default_factory=dict)
    field_own_factor: float = 1.0
    own_sensitivity: float = 1.5
    edge_weight: float = 1.0

    def cap_for(self, name: str) -> float:
        """Return the effective max-exposure fraction for ``name``."""
        return self.player_caps.get(name, self.global_max)

    def floor_for(self, name: str) -> float:
        """Return the effective min-exposure fraction for ``name``."""
        return self.player_floors.get(name, self.global_min)


# ---------------------------------------------------------------------------
# Leverage scoring
# ---------------------------------------------------------------------------

def compute_leverage_scores(
    projections_df: pd.DataFrame,
    cfg: ExposureConfig | None = None,
) -> pd.DataFrame:
    """
    Enrich ``projections_df`` with leverage-related columns and return a copy.

    Added columns
    -------------
    FieldOwn      estimated field ownership (``Own * field_own_factor``)
    OwnDivergence our_own - field_own  (negative = we are below field)
    Edge          Proj - field_mean_proj  (positive = we like player more)
    Leverage      composite score used as LP tie-breaker
    LowOwnBonus   backward-compat column = ``(1 - own/100).clip(0)``

    The ``Leverage`` column replaces the plain ``LowOwnBonus`` in the
    optimizer objective when an ``ExposureConfig`` is supplied.
    """
    if cfg is None:
        cfg = ExposureConfig()

    df = projections_df.copy()

    proj = pd.to_numeric(df.get("Proj", 0), errors="coerce").fillna(0.0)
    own = pd.to_numeric(df.get("Own", 0), errors="coerce").fillna(0.0)

    field_mean = float(proj[proj > 0].mean()) if (proj > 0).any() else 0.0
    field_own = own * cfg.field_own_factor

    edge = (proj - field_mean) * cfg.edge_weight
    own_divergence = own - field_own           # 0 when factor=1.0; non-zero otherwise
    own_penalty = np.maximum(0.0, own / 100.0) * cfg.own_sensitivity

    # Leverage: reward positive edge, penalise high ownership
    leverage = edge - own_penalty
    # Normalise to [0, 1] so it acts as a well-bounded tie-breaker
    l_min, l_max = float(leverage.min()), float(leverage.max())
    if l_max > l_min:
        leverage_norm = (leverage - l_min) / (l_max - l_min)
    else:
        leverage_norm = pd.Series(np.zeros(len(df)), index=df.index)

    df["FieldOwn"] = field_own.round(2)
    df["OwnDivergence"] = (own - field_own).round(2)
    df["Edge"] = edge.round(3)
    df["Leverage"] = leverage_norm.round(4)
    df["LowOwnBonus"] = (1.0 - own / 100.0).clip(lower=0).round(4)

    n_pos = int((leverage_norm > 0.5).sum())
    log.info(
        "Leverage scores computed — field_mean_proj=%.1f, "
        "high_leverage_players (>0.5): %d / %d",
        field_mean, n_pos, len(df),
    )
    return df


# ---------------------------------------------------------------------------
# Per-player count caps (for optimizer)
# ---------------------------------------------------------------------------

def resolve_per_player_caps(
    projections_df: pd.DataFrame,
    n_lineups: int,
    cfg: ExposureConfig,
) -> dict[str, int]:
    """
    Convert ``ExposureConfig`` fractions into absolute lineup-count caps
    and floors, keyed by ``DFS_ID``.

    Returns
    -------
    dict mapping DFS_ID → maximum lineup count for that player
    (same shape as ``exposure_counts`` in the LP optimizer).

    A separate ``floors`` dict (DFS_ID → min count) is returned as the
    second element of a tuple.  The optimizer uses floors to enforce
    mandatory appearances.
    """
    name_to_id: dict[str, str] = {}
    if "Name" in projections_df.columns and "DFS_ID" in projections_df.columns:
        for _, row in projections_df.iterrows():
            name_to_id[str(row["Name"])] = str(row["DFS_ID"])

    caps: dict[str, int] = {}
    floors: dict[str, int] = {}

    for name, pid in name_to_id.items():
        max_pct = cfg.cap_for(name)
        min_pct = cfg.floor_for(name)
        caps[pid] = max(1, int(round(max_pct * n_lineups)))
        if min_pct > 0:
            floors[pid] = max(1, int(round(min_pct * n_lineups)))

    return caps, floors


# ---------------------------------------------------------------------------
# Post-generation exposure report
# ---------------------------------------------------------------------------

def build_exposure_report(
    lineups_df: pd.DataFrame,
    projections_df: pd.DataFrame,
    n_lineups: int,
    cfg: ExposureConfig | None = None,
) -> pd.DataFrame:
    """
    Compute actual exposure for each player across generated lineups and
    compare against targets from ``ExposureConfig``.

    Parameters
    ----------
    lineups_df      Output of ``optimize_portfolio`` (has ``LineupIndex``, ``DFS_ID``).
    projections_df  Enriched projections (has ``Name``, ``DFS_ID``, ``Proj``,
                    ``Own``, optionally ``Leverage``).
    n_lineups       Total lineups generated.
    cfg             Exposure config; defaults to ``ExposureConfig()``.

    Returns
    -------
    DataFrame sorted by ``ActualPct`` descending with columns:
        DFS_ID, Name, Team, Pos, Salary, Proj, Own, Leverage,
        ActualCount, ActualPct, CapPct, FloorPct, OverCap, UnderFloor
    """
    if cfg is None:
        cfg = ExposureConfig()

    if lineups_df.empty or n_lineups == 0:
        return pd.DataFrame()

    # Count appearances
    counts = (
        lineups_df.groupby("DFS_ID")
        .size()
        .reset_index(name="ActualCount")
    )
    counts["DFS_ID"] = counts["DFS_ID"].astype(str)
    counts["ActualPct"] = (counts["ActualCount"] / n_lineups).round(4)

    # Pull player metadata from projections
    meta_cols = [c for c in ["DFS_ID", "Name", "Team", "Pos", "Salary",
                              "Proj", "Own", "Leverage"] if c in projections_df.columns]
    meta = projections_df[meta_cols].copy()
    meta["DFS_ID"] = meta["DFS_ID"].astype(str)

    report = meta.merge(counts, on="DFS_ID", how="left")
    report["ActualCount"] = report["ActualCount"].fillna(0).astype(int)
    report["ActualPct"] = report["ActualPct"].fillna(0.0)

    # Cap / floor columns
    if "Name" in report.columns:
        report["CapPct"] = report["Name"].apply(cfg.cap_for)
        report["FloorPct"] = report["Name"].apply(cfg.floor_for)
    else:
        report["CapPct"] = cfg.global_max
        report["FloorPct"] = cfg.global_min

    report["OverCap"] = report["ActualPct"] > report["CapPct"] + 0.001
    report["UnderFloor"] = (report["FloorPct"] > 0) & (report["ActualPct"] < report["FloorPct"] - 0.001)

    # Summary log
    over_count = int(report["OverCap"].sum())
    under_count = int(report["UnderFloor"].sum())
    if over_count:
        over_names = report.loc[report["OverCap"], "Name"].tolist() if "Name" in report.columns else []
        log.warning("Exposure report: %d player(s) OVER cap: %s", over_count, over_names)
    if under_count:
        under_names = report.loc[report["UnderFloor"], "Name"].tolist() if "Name" in report.columns else []
        log.warning("Exposure report: %d player(s) UNDER floor: %s", under_count, under_names)
    if not over_count and not under_count:
        log.info("Exposure report: all %d players within bounds across %d lineups",
                 int((report["ActualCount"] > 0).sum()), n_lineups)

    return (
        report
        .sort_values("ActualPct", ascending=False)
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# Team-stacking exposure utilities
# ---------------------------------------------------------------------------

def build_stack_report(lineups_df: pd.DataFrame) -> pd.DataFrame:
    """
    Count how many times each team appears 2+, 3+, 4+ players in the same
    lineup across the portfolio.  Useful for GPP stacking diversity checks.

    Returns DataFrame with columns:
        Team, Stack2, Stack3, Stack4, TotalLineups
    """
    if lineups_df.empty:
        return pd.DataFrame(columns=["Team", "Stack2", "Stack3", "Stack4", "TotalLineups"])

    if "Team" not in lineups_df.columns:
        return pd.DataFrame()

    n_lineups = lineups_df["LineupIndex"].nunique()
    rows = []
    for team, team_df in lineups_df.groupby("Team"):
        counts_per_lineup = team_df.groupby("LineupIndex").size()
        rows.append({
            "Team": team,
            "Stack2": int((counts_per_lineup >= 2).sum()),
            "Stack3": int((counts_per_lineup >= 3).sum()),
            "Stack4": int((counts_per_lineup >= 4).sum()),
            "TotalLineups": n_lineups,
        })
    return (
        pd.DataFrame(rows)
        .sort_values("Stack3", ascending=False)
        .reset_index(drop=True)
    )
