"""
Contest Simulation & EV Ranking
================================
Models your lineups competing against a simulated field to compute:
  - EV       (expected value = expected_payout - entry_fee)
  - ROI      (EV / entry_fee)
  - CashRate  (probability of cashing)
  - Top1Rate  (probability of finishing 1st)
  - AvgFinishPct  (0.0 = 1st, 1.0 = last)

Usage
-----
    from analysis.core.contest_sim import ContestConfig, simulate_contest_ev

    cfg = ContestConfig(entry_fee=3.0, field_size=100, contest_type="gpp")
    ev_df = simulate_contest_ev(scores_matrix, lineup_indices, cfg)
    # Returns DataFrame sorted by ROI descending
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

Site = Literal["DK", "FD"]
ContestType = Literal["gpp", "double_up", "top_heavy", "winner_take_all"]


# ---------------------------------------------------------------------------
# Payout schedule builders
# ---------------------------------------------------------------------------

def _gpp_schedule(entry_fee: float, field_size: int) -> list[tuple[int, float]]:
    """
    Realistic GPP schedule: top ~20% cash, front-loaded prizes.
    Prize pool = entry_fee × field_size × 0.85 (15% platform rake).
    """
    prize_pool = entry_fee * field_size * 0.85
    n_paid = max(1, round(field_size * 0.20))

    # Tiered prize allocation — percentages of prize pool by rank bucket
    if field_size <= 50:
        tier_pcts = [(1, 0.35), (2, 0.20), (3, 0.13), (4, 0.09), (5, 0.07)]
    elif field_size <= 250:
        tier_pcts = [
            (1, 0.28), (2, 0.16), (3, 0.11), (4, 0.08), (5, 0.06),
            (10, 0.04), (15, 0.03),
        ]
    else:
        tier_pcts = [
            (1, 0.20), (2, 0.12), (3, 0.08), (5, 0.05), (10, 0.03),
            (25, 0.02), (50, 0.015), (75, 0.01),
        ]

    schedule: list[tuple[int, float]] = []
    allocated_pool = 0.0
    allocated_ranks = 0
    for rank, pct in tier_pcts:
        if rank <= n_paid:
            payout = round(prize_pool * pct, 2)
            schedule.append((rank, max(payout, entry_fee)))
            allocated_pool += prize_pool * pct
            allocated_ranks = rank

    # Fill remaining paid spots with floor prize
    remaining_spots = n_paid - allocated_ranks
    if remaining_spots > 0:
        floor_prize = round(
            max(prize_pool - allocated_pool, 0) / remaining_spots, 2
        )
        floor_prize = max(floor_prize, entry_fee)  # at least entry fee back
        for rank in range(allocated_ranks + 1, n_paid + 1):
            schedule.append((rank, floor_prize))

    return schedule


def _double_up_schedule(entry_fee: float, field_size: int) -> list[tuple[int, float]]:
    """Top ~45% double their entry fee (house keeps ~10% for rake)."""
    n_paid = max(1, round(field_size * 0.45))
    payout = round(entry_fee * 2.0, 2)
    return [(rank, payout) for rank in range(1, n_paid + 1)]


def _top_heavy_schedule(entry_fee: float, field_size: int) -> list[tuple[int, float]]:
    """Top 10% pay; heavily weighted to top 3."""
    prize_pool = entry_fee * field_size * 0.85
    n_paid = max(1, round(field_size * 0.10))
    # Rough split: 40% / 25% / 15% / 10% / 10% over paid spots
    pcts = [0.40, 0.25, 0.15, 0.10, 0.10]
    schedule: list[tuple[int, float]] = []
    allocated = 0.0
    for i, pct in enumerate(pcts):
        rank = i + 1
        if rank <= n_paid:
            payout = round(prize_pool * pct, 2)
            schedule.append((rank, max(payout, entry_fee)))
            allocated += prize_pool * pct
    # Floor prizes for remaining paid ranks
    remaining = n_paid - len(schedule)
    if remaining > 0:
        floor = round(max((prize_pool - allocated) / remaining, entry_fee), 2)
        for rank in range(len(schedule) + 1, n_paid + 1):
            schedule.append((rank, floor))
    return schedule


def _winner_take_all_schedule(entry_fee: float, field_size: int) -> list[tuple[int, float]]:
    prize_pool = round(entry_fee * field_size * 0.85, 2)
    return [(1, prize_pool)]


_SCHEDULE_BUILDERS = {
    "gpp": _gpp_schedule,
    "double_up": _double_up_schedule,
    "top_heavy": _top_heavy_schedule,
    "winner_take_all": _winner_take_all_schedule,
}


# ---------------------------------------------------------------------------
# ContestConfig
# ---------------------------------------------------------------------------

@dataclass
class ContestConfig:
    """
    Describes a single DFS contest.

    Parameters
    ----------
    site            "DK" or "FD"
    contest_type    "gpp" | "double_up" | "top_heavy" | "winner_take_all"
    entry_fee       Dollar amount per entry (default $3)
    field_size      Total number of entrants including yours (default 100)
    field_mean_score
        Expected average lineup score of the external field.
        ``None`` → auto-computed from your lineup means (conservative default).
    field_score_std
        DK NBA realistic spread ≈ 18-22 pts; FD ≈ 16-20 pts.
        Adjust up for high-variance slates, down for lock-heavy slates.
    custom_payout   Override auto-built schedule with [(rank, prize_$), ...].
    """

    site: Site = "DK"
    contest_type: ContestType = "gpp"
    entry_fee: float = 3.0
    field_size: int = 100
    field_mean_score: float | None = None
    field_score_std: float = 20.0
    custom_payout: list[tuple[int, float]] | None = None

    def payout_schedule(self) -> list[tuple[int, float]]:
        """Return the resolved payout schedule [(rank, prize_dollars), …]."""
        if self.custom_payout:
            return sorted(self.custom_payout, key=lambda x: x[0])
        builder = _SCHEDULE_BUILDERS.get(self.contest_type, _gpp_schedule)
        return builder(self.entry_fee, self.field_size)


# ---------------------------------------------------------------------------
# Core simulation
# ---------------------------------------------------------------------------

def simulate_contest_ev(
    scores_matrix: np.ndarray,
    lineup_indices: list[int],
    config: ContestConfig,
    seed: int | None = 42,
) -> pd.DataFrame:
    """
    Rank each lineup against a simulated field and compute contest EV.

    Parameters
    ----------
    scores_matrix   Shape ``(n_lineups, n_sims)`` — per-trial lineup scores
                    from ``simulate_lineup_scores``.
    lineup_indices  List of ``LineupIndex`` values matching axis-0 of scores_matrix.
    config          Contest configuration.
    seed            RNG seed for the field score draws (reproducible by default).

    Returns
    -------
    DataFrame with columns:
        LineupIndex, EV, ROI, ExpectedPayout, CashRate, Top1Rate,
        AvgFinishPct, EntryFee, FieldSize, ContestType
    Sorted by ROI descending.
    """
    if scores_matrix.ndim != 2 or scores_matrix.shape[0] == 0:
        log.warning("simulate_contest_ev: empty scores_matrix — returning empty DataFrame")
        return pd.DataFrame()

    n_lineups, n_sims = scores_matrix.shape
    n_external = max(0, config.field_size - n_lineups)

    rng = np.random.default_rng(seed)

    # ── Field mean: auto or user-supplied ────────────────────────────────────
    field_mean = (
        config.field_mean_score
        if config.field_mean_score is not None
        else float(scores_matrix.mean())
    )

    # ── External contestant scores: (n_external, n_sims) ─────────────────────
    if n_external > 0:
        external = rng.normal(
            loc=field_mean,
            scale=config.field_score_std,
            size=(n_external, n_sims),
        )
        external = np.clip(external, 0.0, None)
        all_scores = np.vstack([scores_matrix, external])  # (total, n_sims)
    else:
        all_scores = scores_matrix.copy()

    n_total = all_scores.shape[0]

    # ── Vectorized rank across all sims in one pass ───────────────────────────
    # Add small jitter to break score ties (simulates random submission time)
    jitter = rng.uniform(0, 1e-6, size=all_scores.shape)
    all_scores_j = all_scores + jitter

    # argsort descending → argsort again = 0-indexed ascending rank for each col
    # rank_desc[i, t] = 1-indexed descending rank of contestant i in trial t
    asc_rank = np.argsort(np.argsort(-all_scores_j, axis=0), axis=0)  # 0 = highest
    rank_desc = asc_rank + 1  # 1-indexed

    our_ranks = rank_desc[:n_lineups, :]  # (n_lineups, n_sims)

    # ── Payout lookup ─────────────────────────────────────────────────────────
    schedule = config.payout_schedule()
    if not schedule:
        log.warning("simulate_contest_ev: empty payout schedule — all payouts zero")
        payout_array = np.zeros(n_total + 2)
    else:
        payout_array = np.zeros(n_total + 2)
        for rank, prize in schedule:
            if 1 <= rank <= n_total:
                payout_array[rank] = prize

    # Vectorized payout: (n_lineups, n_sims)
    payouts = payout_array[np.clip(our_ranks, 1, n_total)]  # safe index

    # ── Aggregate stats ───────────────────────────────────────────────────────
    expected_payout = payouts.mean(axis=1)           # (n_lineups,)
    ev = expected_payout - config.entry_fee
    roi = np.where(config.entry_fee > 0, ev / config.entry_fee, 0.0)
    cash_rate = (payouts > 0).mean(axis=1)
    top1_rate = (our_ranks == 1).mean(axis=1)
    avg_finish_pct = (our_ranks / n_total).mean(axis=1)  # 0.0=top, 1.0=bottom

    result = pd.DataFrame(
        {
            "LineupIndex": lineup_indices,
            "EV": np.round(ev, 3),
            "ROI": np.round(roi, 4),
            "ExpectedPayout": np.round(expected_payout, 3),
            "CashRate": np.round(cash_rate, 4),
            "Top1Rate": np.round(top1_rate, 6),
            "AvgFinishPct": np.round(avg_finish_pct, 4),
            "EntryFee": config.entry_fee,
            "FieldSize": config.field_size,
            "ContestType": config.contest_type,
        }
    ).sort_values("ROI", ascending=False).reset_index(drop=True)

    best = result.iloc[0]
    log.info(
        "Contest EV — %d lineups vs %d-person %s ($%.0f entry) | "
        "Best ROI: %.1f%% | Best EV: $%.2f | Best CashRate: %.1f%%",
        n_lineups,
        config.field_size,
        config.contest_type,
        config.entry_fee,
        best["ROI"] * 100,
        best["EV"],
        best["CashRate"] * 100,
    )
    return result


# ---------------------------------------------------------------------------
# Convenience: merge EV ranking into an existing simulation summary
# ---------------------------------------------------------------------------

def enrich_simulation_with_ev(
    sim_summary: pd.DataFrame,
    scores_matrix: np.ndarray,
    config: ContestConfig,
    seed: int | None = 42,
) -> pd.DataFrame:
    """
    Merge contest EV columns into an existing simulation summary DataFrame.

    The simulation summary must contain ``LineupIndex``.  The returned
    DataFrame is the summary merged with EV columns, sorted by ROI.
    """
    if sim_summary.empty:
        return sim_summary

    lineup_indices = sim_summary["LineupIndex"].tolist()
    ev_df = simulate_contest_ev(scores_matrix, lineup_indices, config, seed)

    merged = sim_summary.merge(
        ev_df.drop(columns=["EntryFee", "FieldSize", "ContestType"], errors="ignore"),
        on="LineupIndex",
        how="left",
    )
    return merged.sort_values("ROI", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Pre-built contest configs for common DK/FD formats
# ---------------------------------------------------------------------------

PRESETS: dict[str, ContestConfig] = {
    "dk_gpp_small":   ContestConfig(site="DK", contest_type="gpp",        entry_fee=3.0,  field_size=100),
    "dk_gpp_medium":  ContestConfig(site="DK", contest_type="gpp",        entry_fee=5.0,  field_size=500),
    "dk_gpp_large":   ContestConfig(site="DK", contest_type="gpp",        entry_fee=20.0, field_size=2000),
    "dk_double_up":   ContestConfig(site="DK", contest_type="double_up",  entry_fee=5.0,  field_size=100),
    "dk_top_heavy":   ContestConfig(site="DK", contest_type="top_heavy",  entry_fee=10.0, field_size=200),
    "fd_gpp_small":   ContestConfig(site="FD", contest_type="gpp",        entry_fee=3.0,  field_size=100),
    "fd_double_up":   ContestConfig(site="FD", contest_type="double_up",  entry_fee=4.0,  field_size=100),
}
