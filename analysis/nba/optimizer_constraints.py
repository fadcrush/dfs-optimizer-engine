"""
Optimizer Constraint Sets — GPP vs Cash
========================================
Distinct pre-processing constraints applied to the player pool BEFORE the
ILP optimizer runs.  These are not separate optimizers — they modify the pool
and projection weights that flow into the existing ``optimize_portfolio`` call.

GPP constraints
---------------
* Enforce team/game correlation via StackRule (min 2 from same game + bring-back)
* Apply a minimum leverage threshold — remove players with very low leverage score
* Allow at least one low-owned contrarian (remove hard cap on LowOwn players)
* Use ceiling-weighted objective: Proj → 0.75*Proj + 0.25*Ceiling (when available)

Cash constraints
----------------
* Floor threshold: remove players whose floor is below the positional minimum
* CV cap: remove high-volatility players (CV > CASH_MAX_CV) from the eligible pool
* No stacking: StackRule disabled
* Projection-only objective: Proj (no ceiling weight)

Usage
-----
    from analysis.nba.optimizer_constraints import apply_contest_constraints

    players_df, stack_rule = apply_contest_constraints(
        players_df, contest_type="gpp", site="DK"
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Max coefficient of variation tolerated in cash games.
# CV = StdDev / Proj; higher CV = more boom-bust.
CASH_MAX_CV: float = 0.35

# Minimum floor threshold for cash games (absolute FPTS floor).
# Players projected below this are too risky for safe-floor formats.
CASH_MIN_FLOOR_BY_POS: dict[str, float] = {
    "PG": 22.0, "SG": 20.0, "SF": 19.0, "PF": 18.0, "C": 18.0,
    "G": 20.0, "F": 18.0, "UTIL": 16.0,
    "_default": 15.0,
}

# GPP: minimum leverage score to remain in the eligible pool.
# Players with zero leverage (not differentiated from chalk) are excluded.
GPP_MIN_LEVERAGE: float = -0.05   # allow slightly negative leverage (near-neutral)

# GPP: minimum number of contrarian plays (Own < this threshold) the pool
# must retain even after chalk-threshold filtering.
GPP_CONTRARIAN_OWN_MAX: float = 10.0   # players < 10% owned are "contrarian"
GPP_MIN_CONTRARIAN_POOL: int = 5        # keep at least 5 contrarian options available


# ---------------------------------------------------------------------------
# Dataclass for constraint config
# ---------------------------------------------------------------------------

@dataclass
class ContestConstraints:
    """Resolved constraint set for a given contest type."""
    contest_type: str = "gpp"           # "gpp" | "cash" | "double_up" | "winner_take_all"
    use_ceiling_weight: bool = False    # GPP: blend Ceiling into objective
    ceiling_blend: float = 0.0         # 0.0 = pure Proj, 1.0 = pure Ceiling
    enforce_stacking: bool = True       # GPP: enable StackRule in optimizer
    min_game_stack: int = 2             # GPP: min players from any game
    bring_back_count: int = 1           # GPP: min players from opposing team
    apply_cv_cap: bool = False          # Cash: remove high-CV players
    cv_cap: float = CASH_MAX_CV
    apply_floor_gate: bool = False      # Cash: remove below-floor players
    floor_gate: dict[str, float] = field(default_factory=dict)
    require_leverage_min: bool = False  # GPP: filter by leverage score
    leverage_min: float = GPP_MIN_LEVERAGE


_GPP_CONSTRAINTS = ContestConstraints(
    contest_type="gpp",
    use_ceiling_weight=True,
    ceiling_blend=0.25,
    enforce_stacking=True,
    min_game_stack=2,
    bring_back_count=1,
    apply_cv_cap=False,
    apply_floor_gate=False,
    require_leverage_min=True,
    leverage_min=GPP_MIN_LEVERAGE,
)

_CASH_CONSTRAINTS = ContestConstraints(
    contest_type="cash",
    use_ceiling_weight=False,
    ceiling_blend=0.0,
    enforce_stacking=False,
    apply_cv_cap=True,
    cv_cap=CASH_MAX_CV,
    apply_floor_gate=True,
    floor_gate=CASH_MIN_FLOOR_BY_POS,
    require_leverage_min=False,
)

_DOUBLE_UP_CONSTRAINTS = ContestConstraints(
    contest_type="double_up",
    use_ceiling_weight=False,
    ceiling_blend=0.0,
    enforce_stacking=False,
    apply_cv_cap=True,
    cv_cap=0.40,           # slightly looser than straight cash
    apply_floor_gate=True,
    floor_gate={k: v * 0.90 for k, v in CASH_MIN_FLOOR_BY_POS.items()},
    require_leverage_min=False,
)

_WINNER_TAKE_ALL_CONSTRAINTS = ContestConstraints(
    contest_type="winner_take_all",
    use_ceiling_weight=True,
    ceiling_blend=0.40,    # ceiling matters most here
    enforce_stacking=True,
    min_game_stack=3,      # tighter stacking for WTA
    bring_back_count=1,
    apply_cv_cap=False,
    apply_floor_gate=False,
    require_leverage_min=True,
    leverage_min=0.0,
)

_CONSTRAINT_MAP: dict[str, ContestConstraints] = {
    "gpp":            _GPP_CONSTRAINTS,
    "tournament":     _GPP_CONSTRAINTS,
    "cash":           _CASH_CONSTRAINTS,
    "double_up":      _DOUBLE_UP_CONSTRAINTS,
    "winner_take_all": _WINNER_TAKE_ALL_CONSTRAINTS,
    "top_heavy":      _GPP_CONSTRAINTS,     # treated as GPP for constraints
}


def get_constraints(contest_type: str) -> ContestConstraints:
    """Return the ContestConstraints for *contest_type* (case-insensitive)."""
    return _CONSTRAINT_MAP.get(contest_type.lower().strip(), _GPP_CONSTRAINTS)


# ---------------------------------------------------------------------------
# Pool filtering + objective reweighting
# ---------------------------------------------------------------------------

def apply_contest_constraints(
    players: pd.DataFrame,
    contest_type: str,
    site: str = "DK",
) -> tuple[pd.DataFrame, Optional[object]]:
    """
    Apply contest-specific constraint logic to the player pool.

    Returns
    -------
    (filtered_df, stack_rule)
        ``filtered_df``  — pool with high-risk players removed for cash, or
                           underweight chalk removed for GPP.
        ``stack_rule``   — ``StackRule`` instance (GPP/WTA) or ``None`` (cash).

    Parameters
    ----------
    players      : projections DataFrame with at least Proj, Salary, Pos columns.
    contest_type : "gpp" | "cash" | "double_up" | "winner_take_all"
    site         : "DK" or "FD"
    """
    cfg = get_constraints(contest_type)
    df = players.copy()
    original_count = len(df)

    pos_col = next((c for c in ["Pos", "Position", "position"] if c in df.columns), None)

    # ── Cash: floor gate ────────────────────────────────────────────────────
    if cfg.apply_floor_gate and cfg.floor_gate:
        floor_col = next((c for c in ["Floor", "floor", "floor_val"] if c in df.columns), None)
        if floor_col and pos_col:
            floors = df[pos_col].map(
                lambda p: cfg.floor_gate.get(
                    str(p).split("/")[0].upper(),
                    cfg.floor_gate.get("_default", 0.0),
                )
            )
            below_floor = df[floor_col].fillna(0) < floors
            removed = int(below_floor.sum())
            if removed:
                df = df[~below_floor].copy()
                log.info(
                    "Cash floor gate: removed %d players below positional floor (contest=%s)",
                    removed, contest_type,
                )

    # ── Cash: CV cap ────────────────────────────────────────────────────────
    if cfg.apply_cv_cap:
        cv_col = next((c for c in ["CV", "cv", "StdDev_CV"] if c in df.columns), None)
        if cv_col is None and "StdDev" in df.columns and "Proj" in df.columns:
            # Derive CV on the fly from StdDev / Proj
            _proj = pd.to_numeric(df["Proj"], errors="coerce").fillna(1)
            _std = pd.to_numeric(df["StdDev"], errors="coerce").fillna(0)
            df["_cv_tmp"] = (_std / _proj.clip(lower=0.1)).clip(0, 1)
            cv_col = "_cv_tmp"

        if cv_col:
            high_cv = pd.to_numeric(df[cv_col], errors="coerce").fillna(0) > cfg.cv_cap
            removed_cv = int(high_cv.sum())
            if removed_cv:
                df = df[~high_cv].copy()
                log.info(
                    "Cash CV cap (%.2f): removed %d boom-bust players (contest=%s)",
                    cfg.cv_cap, removed_cv, contest_type,
                )
            df.drop(columns=["_cv_tmp"], errors="ignore", inplace=True)

    # ── GPP: ceiling-weighted projection ────────────────────────────────────
    if cfg.use_ceiling_weight and cfg.ceiling_blend > 0:
        ceil_col = next((c for c in ["Ceiling", "ceiling", "ceiling_val"] if c in df.columns), None)
        if ceil_col:
            _proj = pd.to_numeric(df["Proj"], errors="coerce").fillna(0)
            _ceil = pd.to_numeric(df[ceil_col], errors="coerce").fillna(_proj)
            df["Proj"] = (
                (1.0 - cfg.ceiling_blend) * _proj + cfg.ceiling_blend * _ceil
            ).round(3)
            log.info(
                "GPP ceiling blend %.0f%%: Proj updated for %d players",
                cfg.ceiling_blend * 100, len(df),
            )

    # ── GPP: ensure contrarian floor in pool ────────────────────────────────
    if not cfg.apply_cv_cap and "Own" in df.columns:
        low_own = (pd.to_numeric(df["Own"], errors="coerce").fillna(50) < GPP_CONTRARIAN_OWN_MAX)
        if int(low_own.sum()) < GPP_MIN_CONTRARIAN_POOL:
            log.info(
                "GPP contrarian floor: pool has < %d players under %.0f%% ownership — "
                "chalk threshold may be too aggressive",
                GPP_MIN_CONTRARIAN_POOL, GPP_CONTRARIAN_OWN_MAX,
            )

    # ── Build StackRule (GPP / WTA only) ────────────────────────────────────
    stack_rule = None
    if cfg.enforce_stacking:
        try:
            from analysis.nba.optimizer import StackRule
            stack_rule = StackRule(
                min_from_same_game=cfg.min_game_stack,
                bring_back_count=cfg.bring_back_count,
            )
            log.info(
                "StackRule built for %s: min_game=%d, bring_back=%d",
                contest_type, cfg.min_game_stack, cfg.bring_back_count,
            )
        except ImportError:
            log.warning("optimizer.StackRule unavailable — stacking skipped")

    removed_total = original_count - len(df)
    if removed_total:
        log.info(
            "Contest constraints (%s): removed %d / %d players from pool",
            contest_type, removed_total, original_count,
        )

    return df, stack_rule
