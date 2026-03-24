"""
Weighted Component Ownership Model
===================================
Deterministic ownership estimation using a rules-based weighted model.
No training data required — suitable as a fallback or standalone estimator.

Ported from CeekIth/app/services/ownership_service.py and adapted to
eree's canonical column names (Proj, Salary, InjuryStatus, etc.).

Algorithm
---------
1. Compute six normalized component scores (0–1).
2. Weighted sum → raw score.
3. Viability gate: suppress low-projection players (< 12 FPPG proxy ≈ < 12 min).
4. Status multiplier: zero OUT players, discount questionable/GTD.
5. Power curve (exponent 1.8) to spread the distribution.
6. Budget normalization (DK=800%, FD=900%).
7. Cap at OWNERSHIP_CAP (40%).

Expected input columns (canonical eree names):
    Proj        — DFS point projection (required)
    Salary      — player salary (required)
    InjuryStatus — "O", "Q", "GTD", "D", "" (optional; defaults to healthy)
    is_replacement_boost — bool/int, 1 if eligible as replacement play (optional)

Optional enrichment columns (produced by stat_projection_breakdown):
    stat_confidence     — composite quality score [0, 1]; 0 = no data
    minutes_confidence  — recency-weighted minutes consistency [0, 1]; 0 = no data
    Proj_Confidence     — explicit external confidence override [0, 1]

Confidence priority order:
    stat_confidence > minutes_confidence > Proj_Confidence > proj-mag proxy
    Players with stat_confidence == 0 (no game-log data) use a neutral default
    (0.75) so they are not unfairly suppressed.

Output columns added/overwritten:
    Own_Est     — estimated ownership % (0–100)
    own_source  — "weighted"
    ownership_bucket — "chalk" | "high" | "medium" | "low"
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Component weights
# ---------------------------------------------------------------------------
W_PROJECTION: float = 0.35
W_VALUE: float = 0.25
W_SALARY_TIER: float = 0.10
W_CONFIDENCE: float = 0.10
W_REPLACEMENT: float = 0.10
W_STATUS: float = 0.10

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
VIABILITY_MINUTES_THRESHOLD: float = 12.0  # FPPG proxy for ~12 min of play
POWER_EXPONENT: float = 1.8
OWNERSHIP_CAP: float = 40.0
_ROSTER_SLOTS: dict[str, int] = {"DK": 8, "FD": 9}
_DEFAULT_SLOTS: int = 8

# ---------------------------------------------------------------------------
# Bucket thresholds
# ---------------------------------------------------------------------------
BUCKET_CHALK: float = 15.0
BUCKET_HIGH: float = 8.0
BUCKET_MEDIUM: float = 3.0

# ---------------------------------------------------------------------------
# Status multiplier: map eree InjuryStatus → float
# eree uses DFS site codes: "O"=Out, "Q"=Questionable, "GTD"=Game-Time Decision
# "D"=Doubtful, "SSPD"=Suspended, ""=healthy
# ---------------------------------------------------------------------------
_STATUS_MULTIPLIER: dict[str, float] = {
    "":       1.00,  # healthy / not listed
    "A":      1.00,
    "P":      1.00,  # probable
    "Q":      0.40,
    "GTD":    0.25,
    "D2D":    0.25,  # day-to-day
    "D":      0.10,  # doubtful
    "O":      0.00,  # out
    "SSPD":   0.00,  # suspended
}


def _status_to_mult(s: object) -> float:
    if not isinstance(s, str):
        return 1.0
    return _STATUS_MULTIPLIER.get(s.strip().upper(), 0.5)


def _assign_bucket(pct: float) -> str:
    if pct >= BUCKET_CHALK:
        return "chalk"
    if pct >= BUCKET_HIGH:
        return "high"
    if pct >= BUCKET_MEDIUM:
        return "medium"
    return "low"


def estimate_ownership_weighted(
    df: pd.DataFrame,
    site: str = "DK",
    states_df: "pd.DataFrame | None" = None,
) -> pd.DataFrame:
    """
    Add weighted ownership estimates to a projections DataFrame.

    Parameters
    ----------
    df        : DataFrame with canonical eree columns (Proj, Salary required).
    site      : "DK" or "FD" — controls roster-slot budget normalization.
    states_df : Optional rows from ``player_injury_state`` (player_id, p_play
                columns at minimum).  When provided, ``p_play`` replaces the
                binary ``_STATUS_MULTIPLIER`` gate for a continuous probability
                signal.  Falls back to ``_status_to_mult`` when None.

    Returns
    -------
    Copy of df with ``Own_Est``, ``own_source``, and ``ownership_bucket`` columns.
    ``Own_Est`` is in percentage points (0–100).
    """
    df = df.copy()
    n = len(df)
    if n == 0:
        df["Own_Est"] = 0.0
        df["own_source"] = "weighted"
        df["ownership_bucket"] = "low"
        return df

    proj = pd.to_numeric(df["Proj"], errors="coerce").fillna(0.0)
    salary = pd.to_numeric(df["Salary"], errors="coerce").fillna(5000.0).clip(lower=1000)

    # ── Component 1: Projection score (0–1) ───────────────────────────────
    max_pts = proj.max()
    proj_score = proj / max_pts if max_pts > 0 else pd.Series(0.0, index=df.index)

    # ── Component 2: Value score (0–1) ────────────────────────────────────
    value_raw = proj / (salary / 1000.0)
    max_value = value_raw.max()
    value_score = value_raw / max_value if max_value > 0 else pd.Series(0.0, index=df.index)

    # ── Component 3: Salary tier score (0–1, sqrt) ────────────────────────
    max_sal = salary.max()
    salary_score = (salary / max_sal) ** 0.5 if max_sal > 0 else pd.Series(0.0, index=df.index)

    # ── Component 4: Confidence score ────────────────────────────────────
    # Priority order:
    #   1. stat_confidence  (from stat_projection_breakdown — composite quality)
    #   2. minutes_confidence (from minutes_confidence_lite — recency consistency)
    #   3. Proj_Confidence  (explicit external column)
    #   4. Proxy from projection magnitude
    # Players where enrichment produced no data (value == 0.0) fall back to the
    # neutral default (0.75) so they are not unfairly penalised.
    _DEFAULT_CONF: float = 0.75
    if "stat_confidence" in df.columns:
        raw_stat = pd.to_numeric(df["stat_confidence"], errors="coerce").fillna(0.0)
        if "minutes_confidence" in df.columns:
            raw_min = pd.to_numeric(df["minutes_confidence"], errors="coerce").fillna(0.0)
            enriched_conf = ((raw_stat + raw_min) / 2.0).clip(0.0, 1.0)
        else:
            enriched_conf = raw_stat.clip(0.0, 1.0)
        has_data = (raw_stat > 0).astype(float)
        conf_score = has_data * enriched_conf + (1.0 - has_data) * _DEFAULT_CONF
        log.debug("Ownership conf source: stat_confidence")
    elif "minutes_confidence" in df.columns:
        raw_min = pd.to_numeric(df["minutes_confidence"], errors="coerce").fillna(0.0)
        has_data = (raw_min > 0).astype(float)
        conf_score = has_data * raw_min.clip(0.0, 1.0) + (1.0 - has_data) * _DEFAULT_CONF
        log.debug("Ownership conf source: minutes_confidence")
    elif "Proj_Confidence" in df.columns:
        conf_score = pd.to_numeric(df["Proj_Confidence"], errors="coerce").fillna(_DEFAULT_CONF).clip(0.0, 1.0)
        log.debug("Ownership conf source: Proj_Confidence")
    else:
        # proxy: 0.5 for median projection, 1.0 for max
        conf_score = proj_score * 0.5 + 0.5
        log.debug("Ownership conf source: projection-magnitude proxy")

    # ── Component 5: Replacement / injury-boost signal ────────────────────
    replacement_score = pd.Series(0.0, index=df.index)
    if "is_replacement_boost" in df.columns:
        boost_flag = pd.to_numeric(df["is_replacement_boost"], errors="coerce").fillna(0).astype(bool)
        replacement_score = replacement_score.where(~boost_flag, 1.0).clip(upper=1.0)

    # ── Component 6: Status multiplier ────────────────────────────────────
    # When states_df (from player_injury_state) is provided, use the
    # continuous p_play probability instead of the binary scalar map.
    # This gives GTD players (p_play ≈ 0.45) a smoother discount than the
    # hard 0.25 gate, making ownership curves more realistic.
    if states_df is not None and not states_df.empty and "p_play" in states_df.columns:
        # Build {NAME_KEY → p_play} from states_df.  player_id is stored as
        # the slug form (upper-case name) in the intelligence service.
        _p_play_col = pd.to_numeric(states_df["p_play"], errors="coerce").fillna(1.0)
        _id_col = states_df["player_id"].astype(str).str.strip().str.upper()
        _p_play_lookup: dict[str, float] = dict(zip(_id_col, _p_play_col))

        if "Name" in df.columns:
            _name_key = df["Name"].astype(str).str.strip().str.upper()
        elif "InjuryStatus" in df.columns:
            _name_key = df.index.astype(str)
        else:
            _name_key = pd.Series("", index=df.index)

        status_mult = _name_key.map(lambda k: _p_play_lookup.get(k, None))
        # For players not in the intelligence DB, fall back to _status_to_mult
        if "InjuryStatus" in df.columns:
            _fallback = df["InjuryStatus"].map(_status_to_mult)
        else:
            _fallback = pd.Series(1.0, index=df.index)
        status_mult = status_mult.combine_first(_fallback).clip(0.0, 1.0)
        log.debug("Ownership status_mult source: p_play (probabilistic)")
    elif "InjuryStatus" in df.columns:
        status_mult = df["InjuryStatus"].map(_status_to_mult)
    else:
        status_mult = pd.Series(1.0, index=df.index)

    # ── Weighted sum ───────────────────────────────────────────────────────
    raw = (
        W_PROJECTION * proj_score
        + W_VALUE * value_score
        + W_SALARY_TIER * salary_score
        + W_CONFIDENCE * conf_score
        + W_REPLACEMENT * replacement_score
        + W_STATUS * status_mult
    )

    # ── Viability gate (proxy: project > 0 → viaibilty by DFS points) ─────
    # 0 FPPG → 0, ≥ VIABILITY_MINUTES_THRESHOLD FPPG → 1.0
    viability = (proj / VIABILITY_MINUTES_THRESHOLD).clip(0.0, 1.0)
    raw = raw * viability

    # ── Status gate (applied twice, matching CeekIth behaviour) ──────────
    raw = raw * status_mult

    # ── Power curve ────────────────────────────────────────────────────────
    raw = np.power(np.maximum(raw.to_numpy(), 0.0), POWER_EXPONENT)
    raw = pd.Series(raw, index=df.index)

    # ── Budget-based normalization ─────────────────────────────────────────
    roster_slots = _ROSTER_SLOTS.get(site.upper(), _DEFAULT_SLOTS)
    total_budget = roster_slots * 100.0
    total_raw = raw.sum()
    if total_raw > 0:
        ownership_pct = (raw / total_raw * total_budget).round(2)
    else:
        ownership_pct = pd.Series(0.0, index=df.index)

    ownership_pct = ownership_pct.clip(0.0, OWNERSHIP_CAP)

    df["Own_Est"] = ownership_pct
    df["own_source"] = "weighted"
    df["ownership_bucket"] = df["Own_Est"].map(_assign_bucket)

    log.info(
        "Weighted ownership applied (site=%s, n=%d, mean=%.1f%%, max=%.1f%%)",
        site, n, float(ownership_pct.mean()), float(ownership_pct.max()),
    )
    return df
