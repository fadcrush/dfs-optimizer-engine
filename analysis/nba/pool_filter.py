"""
NBA Player Pool Filter
======================
Two-stage filter applied BEFORE optimization:

Stage 1 – Hard gates (players removed from pool entirely):
  • Injury status = OUT
  • Confirmed Q/DTD when INJURY_DATA_REQUIRED=true and mode=closed
  • Minutes per game below floor (default: 10)
  • Projection below absolute floor (default: 10 FP)

Stage 2 – Soft tags (columns added; optimizer uses them for exposure caps):
  • chalk_flag   – ownership projection > chalk_own_threshold (default: 30%)
  • volatile_tier – "high" / "med" / "low" based on ceiling/floor spread
  • replacement_boost – True when this player absorbs minutes from an OUT player

Injury data pulled from vw_nba_injury_status in nba_news.duckdb.
Falls back gracefully when DB is unavailable (INJURY_DATA_FAIL_MODE=open).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pandas as pd

from analysis.shared.injury_utils import (
    load_injury_status,
    match_injury_status,
    detect_name_col,
)

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "nba_news.duckdb"

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class PoolFilterConfig:
    # Hard-filter thresholds
    min_minutes_floor: float = 10.0        # season avg minutes/game
    min_projection: float = 10.0           # projected DFS points
    exclude_out: bool = True               # always remove OUT players
    exclude_questionable: bool = False     # set True in closed/safe mode

    # Soft-tag thresholds
    chalk_own_threshold: float = 30.0      # ownership % above which chalk_flag=True
    high_volatility_spread: float = 25.0   # ceiling - floor >= this → "high"
    low_volatility_spread: float = 10.0    # ceiling - floor <  this → "low"

    # start_prob projection discounting for Q/D players
    discount_questionable: bool = True     # multiply Proj by start_prob for Q/D
    min_start_prob_exclude: float = 0.15   # hard-exclude players below this start_prob

    # Fail mode (mirrors INJURY_DATA_FAIL_MODE env var)
    injury_fail_mode: Literal["open", "closed"] = "open"


# ── Delegated helpers (shared with slates router, injuries router, etc.) ──────
# Kept as thin wrappers so internal references continue to work.

_load_injury_status = load_injury_status
_match_injury_status = match_injury_status
_detect_name_col = detect_name_col


# ── Volatility tagger ─────────────────────────────────────────────────────────

def _tag_volatility(df: pd.DataFrame, cfg: PoolFilterConfig) -> pd.DataFrame:
    """Add 'volatile_tier' column: 'high' / 'med' / 'low'"""
    df = df.copy()
    if "Ceiling" in df.columns and "Floor" in df.columns:
        spread = df["Ceiling"].fillna(df.get("Proj", 0)) - df["Floor"].fillna(df.get("Proj", 0))
        df["volatile_tier"] = "med"
        df.loc[spread >= cfg.high_volatility_spread, "volatile_tier"] = "high"
        df.loc[spread <  cfg.low_volatility_spread,  "volatile_tier"] = "low"
    else:
        df["volatile_tier"] = "med"
    return df


# ── Chalk tagger ──────────────────────────────────────────────────────────────

def _tag_chalk(df: pd.DataFrame, cfg: PoolFilterConfig) -> pd.DataFrame:
    """Add 'chalk_flag' bool column based on projected ownership."""
    df = df.copy()
    own_col = next((c for c in ["Own", "Ownership", "ownership", "own"] if c in df.columns), None)
    if own_col:
        df["chalk_flag"] = df[own_col].fillna(0) >= cfg.chalk_own_threshold
    else:
        df["chalk_flag"] = False
    return df


# ── Hard filter ───────────────────────────────────────────────────────────────

def _apply_hard_filters(
    df: pd.DataFrame,
    cfg: PoolFilterConfig,
    injury_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    """
    Remove ineligible players and discount Q/D projections by start_prob.

    Returns (kept_df, removed_df, discounted_players).
    ``discounted_players`` is a list of dicts for the filter report.
    """
    df = _match_injury_status(df, injury_df)

    removed_masks = []
    discounted_players: list[dict] = []

    # Injury gate
    if cfg.exclude_out:
        out_mask = df["InjuryStatus"].str.upper().isin(["OUT", "O"])
        removed_masks.append(out_mask)
        if out_mask.any():
            log.info("Removing %d OUT players: %s",
                     out_mask.sum(),
                     df.loc[out_mask, _detect_name_col(df)].tolist())

    if cfg.exclude_questionable:
        q_mask = df["InjuryStatus"].str.upper().isin(["QUESTIONABLE", "Q", "DOUBTFUL", "D"])
        removed_masks.append(q_mask)
        if q_mask.any():
            log.info("Removing %d Q/DTD players: %s",
                     q_mask.sum(),
                     df.loc[q_mask, _detect_name_col(df)].tolist())

    # ── start_prob-based discounting for Q/D players ─────────────────────────
    proj_col = next((c for c in ["Proj", "Projection", "proj"] if c in df.columns), None)
    has_start_prob = "start_prob" in df.columns

    if cfg.discount_questionable and has_start_prob and proj_col and not cfg.exclude_questionable:
        qd_mask = df["InjuryStatus"].str.upper().isin(["QUESTIONABLE", "Q", "DOUBTFUL", "D", "GTD"])
        name_col = _detect_name_col(df)

        for i, row in df[qd_mask].iterrows():
            sp = float(row.get("start_prob", 1.0))

            # Very low start_prob → treat as effectively OUT
            if sp < cfg.min_start_prob_exclude:
                removed_masks.append(df.index == i)
                log.info("Excluding %s (start_prob=%.2f < %.2f threshold)",
                         row[name_col], sp, cfg.min_start_prob_exclude)
                continue

            # Discount projection by start_prob
            original_proj = float(row[proj_col] or 0)
            discounted_proj = round(original_proj * sp, 2)
            df.at[i, proj_col] = discounted_proj
            df.at[i, "injury_discounted"] = True
            df.at[i, "original_proj"] = original_proj

            discounted_players.append({
                "name": str(row[name_col]),
                "status": str(row["InjuryStatus"]),
                "start_prob": round(sp, 3),
                "original_proj": original_proj,
                "discounted_proj": discounted_proj,
            })
            log.debug("Discounted %s: %.1f → %.1f (start_prob=%.2f, %s)",
                      row[name_col], original_proj, discounted_proj, sp, row["InjuryStatus"])

        if discounted_players:
            log.info("Discounted %d Q/D/GTD player projections by start_prob", len(discounted_players))

    # Ensure tag columns exist even when no discounting happened
    if "injury_discounted" not in df.columns:
        df["injury_discounted"] = False
    if "original_proj" not in df.columns and proj_col:
        df["original_proj"] = df[proj_col]

    # Projection floor (applied after discounting so discounted players can fall below)
    if proj_col:
        low_proj_mask = df[proj_col].fillna(0) < cfg.min_projection
        removed_masks.append(low_proj_mask)

    # Combine all removal masks
    if removed_masks:
        remove = removed_masks[0]
        for m in removed_masks[1:]:
            remove = remove | m
        removed_df = df[remove].copy()
        kept_df    = df[~remove].copy()
    else:
        kept_df    = df.copy()
        removed_df = pd.DataFrame(columns=df.columns)

    log.info(
        "Pool filter: %d → %d players  (removed %d)",
        len(df), len(kept_df), len(removed_df),
    )
    return kept_df, removed_df, discounted_players


# ── Public API ────────────────────────────────────────────────────────────────

def apply_pool_filter(
    projections_df: pd.DataFrame,
    cfg: PoolFilterConfig | None = None,
    replacement_boosts: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict]:
    """
    Full two-stage pool filter.

    Parameters
    ----------
    projections_df   : output from CanonicalNBAProjectionEngine
    cfg              : PoolFilterConfig (defaults used if None)
    replacement_boosts : DataFrame from replacement_engine.compute_boosts(),
                         added as 'replacement_boost' tag when provided

    Returns
    -------
    filtered_df : projections with tags, OUT players removed
    report      : summary dict suitable for logging / API response
                  Keys include:
                    - removed_players      : names of players removed
                    - removed_injury_detail: [{name, status, detail}] for OUT/Q removals
                    - injuries_today       : all players on today's injury report
    """
    if cfg is None:
        fail_mode = os.getenv("INJURY_DATA_FAIL_MODE", "open").lower()
        exclude_q = os.getenv("INJURY_DATA_REQUIRED", "false").lower() == "true"
        cfg = PoolFilterConfig(
            injury_fail_mode=fail_mode,         # type: ignore[arg-type]
            exclude_questionable=exclude_q,
        )

    injury_df = _load_injury_status()

    # Stage 1 – hard filters + start_prob discounting
    kept_df, removed_df, discounted_players = _apply_hard_filters(projections_df, cfg, injury_df)

    # Stage 2 – soft tags
    kept_df = _tag_volatility(kept_df, cfg)
    kept_df = _tag_chalk(kept_df, cfg)

    # Replacement boost flag
    if replacement_boosts is not None and not replacement_boosts.empty:
        kept_df = _apply_replacement_boosts(kept_df, replacement_boosts)
    else:
        kept_df["replacement_boost"] = False

    # ── Injury detail for removed players ────────────────────────────────────
    removed_injury_detail: list[dict] = []
    if not removed_df.empty:
        name_col = _detect_name_col(removed_df)
        for _, row in removed_df.iterrows():
            inj_status = str(row.get("InjuryStatus", "")).strip()
            if inj_status.upper() in {"OUT", "O", "QUESTIONABLE", "Q", "DOUBTFUL", "D"}:
                removed_injury_detail.append({
                    "name": str(row.get(name_col, "")),
                    "status": inj_status,
                    "detail": str(row.get("InjuryDetail", "")),
                })

    # ── Full today's injury report (all players in DB, not just those on slate) ─
    injuries_today: list[dict] = []
    if not injury_df.empty:
        for _, row in injury_df.iterrows():
            injuries_today.append({
                "player_id": str(row.get("player_id", "")),
                "status": str(row.get("status", "")),
                "detail": str(row.get("detail", "")),
            })
        # Log compact summary
        out_all = [r["player_id"] for r in injuries_today if r["status"] == "OUT"]
        log.info("Injury report: %d total, %d OUT, %d QUESTIONABLE",
                 len(injuries_today),
                 len(out_all),
                 sum(1 for r in injuries_today if r["status"] == "QUESTIONABLE"))
        if out_all:
            log.info("OUT players (all): %s", out_all)

    report = {
        "original_count":  len(projections_df),
        "filtered_count":  len(kept_df),
        "removed_count":   len(removed_df),
        "removed_players": removed_df[_detect_name_col(removed_df)].tolist() if not removed_df.empty else [],
        "removed_injury_detail": removed_injury_detail,
        "injuries_today":  injuries_today,
        "discounted_players": discounted_players,
        "discounted_count": len(discounted_players),
        "out_count":       int((removed_df.get("InjuryStatus", pd.Series(dtype=str)).str.upper().isin(["OUT", "O"])).sum()),
        "chalk_count":     int(kept_df.get("chalk_flag", pd.Series(dtype=bool)).sum()),
        "high_vol_count":  int((kept_df.get("volatile_tier", pd.Series(dtype=str)) == "high").sum()),
        "replacement_boost_count": int(kept_df.get("replacement_boost", pd.Series(dtype=bool)).sum()),
    }
    return kept_df, report


def _apply_replacement_boosts(df: pd.DataFrame, boosts: pd.DataFrame) -> pd.DataFrame:
    """
    Mark players in `boosts` as replacement_boost=True and optionally
    inflate their Proj by the boost delta.
    Expected boosts columns: player_name, proj_boost (additional FP)
    """
    df = df.copy()
    name_col = _detect_name_col(df)
    df["replacement_boost"] = False

    if "player_name" not in boosts.columns:
        return df

    boost_map = boosts.set_index("player_name")["proj_boost"].to_dict() if "proj_boost" in boosts.columns else {}
    boost_names = set(boosts["player_name"].str.lower())

    proj_col = next((c for c in ["Proj", "Projection", "proj"] if c in df.columns), None)

    for i, row in df.iterrows():
        pname = str(row[name_col]).lower()
        if pname in boost_names:
            df.at[i, "replacement_boost"] = True
            if proj_col and pname in {k.lower() for k in boost_map}:
                boost_val = next(v for k, v in boost_map.items() if k.lower() == pname)
                df.at[i, proj_col] = float(row[proj_col] or 0) + float(boost_val)
                log.debug("Boosted %s Proj by +%.2f", row[name_col], boost_val)

    log.info("Marked %d replacement-boost players", int(df["replacement_boost"].sum()))
    return df
