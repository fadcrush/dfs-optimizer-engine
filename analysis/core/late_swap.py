"""
Phase 22: Late Swap Engine
==========================

Core late-swap business logic for the DFS analysis layer.

Provides:
- ``SwapSignal``         — a detected player status change event
- ``SwapRecommendation`` — a scored replacement candidate
- ``LateSwapEngine``     — change detection + replacement finding + scoring

This module is intentionally decoupled from HTTP/FastAPI.  The backend router
(``backend/routers/optimizer.py``) handles the HTTP surface; this class owns
the business rules: change detection, eligibility evaluation, and swap scoring.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from analysis.schemas.player import InjuryStatus

# ──────────────────────────────────────────────────────────────────────────────
# Status severity ordering (higher = more severe / more actionable)
# ──────────────────────────────────────────────────────────────────────────────

_SEVERITY: dict[str, int] = {
    InjuryStatus.OUT.value:                 5,
    InjuryStatus.DOUBTFUL.value:            4,
    InjuryStatus.GAME_TIME_DECISION.value:  3,
    InjuryStatus.QUESTIONABLE.value:        2,
    InjuryStatus.PROBABLE.value:            1,
    InjuryStatus.ACTIVE.value:              0,
    InjuryStatus.UNKNOWN.value:             0,
}

# ──────────────────────────────────────────────────────────────────────────────
# Position compatibility helpers (DraftKings ruleset)
# ──────────────────────────────────────────────────────────────────────────────

# Which generic roster slots a given base position can fill on DK
_DK_POS_TO_SLOTS: dict[str, set[str]] = {
    "PG": {"PG", "G", "UTIL"},
    "SG": {"SG", "G", "UTIL"},
    "SF": {"SF", "F", "UTIL"},
    "PF": {"PF", "F", "UTIL"},
    "C":  {"C",  "UTIL"},
}

# Which base positions a given slot accepts on DK (inverse lookup)
_DK_SLOT_ACCEPTS: dict[str, set[str]] = {
    "PG":   {"PG"},
    "SG":   {"SG"},
    "SF":   {"SF"},
    "PF":   {"PF"},
    "C":    {"C"},
    "G":    {"PG", "SG"},
    "F":    {"SF", "PF"},
    "UTIL": {"PG", "SG", "SF", "PF", "C"},
}


def _eligible_slots(pos: str) -> set[str]:
    """Return DK roster slots that a player's position string can fill."""
    parts = [p.strip() for p in pos.upper().split("/")]
    result: set[str] = set()
    for p in parts:
        result.update(_DK_POS_TO_SLOTS.get(p, {"UTIL"}))
    return result


def _pos_eligible_for_slot(pos: str, slot_label: str) -> bool:
    """Return True if a player with ``pos`` can fill ``slot_label``."""
    accepted = _DK_SLOT_ACCEPTS.get(slot_label.upper(), set())
    parts = {p.strip() for p in pos.upper().split("/")}
    return bool(parts & accepted)


def _rank_normalize(vals: list[float], *, ascending: bool = False) -> list[float]:
    """
    Return 0–1 rank-normalised values.

    ``ascending=False`` (default) → higher raw value earns a higher score.
    ``ascending=True``            → lower raw value earns a higher score.
    """
    n = len(vals)
    if n == 0:
        return []
    if n == 1:
        return [1.0]
    ranked = sorted(range(n), key=lambda i: vals[i], reverse=not ascending)
    result = [0.0] * n
    for rank, idx in enumerate(ranked):
        result[idx] = (n - 1 - rank) / (n - 1)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SwapSignal:
    """Represents a detected player status change that warrants a lineup swap."""

    player_name: str
    old_status: str       # InjuryStatus value before (e.g. "ACTIVE")
    new_status: str       # InjuryStatus value after  (e.g. "OUT")
    timestamp: datetime = field(default_factory=datetime.utcnow)
    source: str = "manual"   # "sportsdata" | "csv" | "manual"

    @property
    def severity(self) -> int:
        """Severity of the new status (0 = healthy, 5 = OUT)."""
        return _SEVERITY.get(self.new_status, 0)

    @property
    def is_actionable(self) -> bool:
        """True when the new status requires an immediate lineup decision (OUT or GTD)."""
        return self.new_status in (
            InjuryStatus.OUT.value,
            InjuryStatus.GAME_TIME_DECISION.value,
        )


@dataclass
class SwapRecommendation:
    """A scored, ranked replacement candidate for a scratched player."""

    scratched_player: str
    replacement_player: str
    replacement_pos: str
    replacement_salary: int
    replacement_proj: float
    proj_delta: float           # replacement.Proj − scratched.Proj
    salary_delta: int           # replacement.Salary − scratched.Salary
    ownership_delta: float      # replacement.Own − scratched.Own
    position_match_exact: bool  # True when the replacement fills the exact slot label
    swap_score: float = 0.0     # composite 0–1 score (higher = better)
    ev_impact: float = 0.0      # proj_delta / (abs(salary_delta) / 1000), magnitude-normalised


# ──────────────────────────────────────────────────────────────────────────────
# LateSwapEngine
# ──────────────────────────────────────────────────────────────────────────────

class LateSwapEngine:
    """
    Core late-swap business logic.

    Decoupled from HTTP / FastAPI — operates on plain ``dict`` snapshots and
    ``pd.DataFrame`` player pools.  The backend router handles the HTTP surface;
    this class owns:

      * Status-change detection  (``detect_changes``)
      * Eligible replacement discovery & scoring  (``find_replacements``)
    """

    DEFAULT_W_PROJ:  float = 0.50
    DEFAULT_W_VALUE: float = 0.30
    DEFAULT_W_OWN:   float = 0.20

    @staticmethod
    def detect_changes(
        prev: dict[str, str],
        curr: dict[str, str],
        *,
        source: str = "manual",
        timestamp: datetime | None = None,
    ) -> list[SwapSignal]:
        """
        Compare two ``{player_name: raw_status}`` snapshots and return a
        ``SwapSignal`` for every player whose status moved to a *more severe* level.

        Parameters
        ----------
        prev      : Prior snapshot  (empty dict → treat all players as ACTIVE).
        curr      : Current snapshot.
        source    : Label for the data source.
        timestamp : Override signal timestamp (default: ``datetime.utcnow()``).

        Returns
        -------
        List of ``SwapSignal`` sorted by severity descending (most urgent first).
        """
        ts = timestamp or datetime.utcnow()
        signals: list[SwapSignal] = []

        for name, raw_new in curr.items():
            new_status = InjuryStatus.from_raw(raw_new).value
            raw_old    = prev.get(name, InjuryStatus.ACTIVE.value)
            old_status = InjuryStatus.from_raw(raw_old).value

            if _SEVERITY.get(new_status, 0) > _SEVERITY.get(old_status, 0):
                signals.append(
                    SwapSignal(
                        player_name=name,
                        old_status=old_status,
                        new_status=new_status,
                        timestamp=ts,
                        source=source,
                    )
                )

        return sorted(signals, key=lambda s: s.severity, reverse=True)

    @staticmethod
    def find_replacements(
        scratched_name: str,
        scratched_pos: str,
        scratched_salary: int,
        scratched_proj: float,
        scratched_own: float,
        lineup_names: list[str],
        pool: pd.DataFrame,
        *,
        salary_cap: int = 50_000,
        current_lineup_salary: int = 0,
        slot_labels: list[str] | None = None,
        locked: set[str] | None = None,
        max_candidates: int = 15,
        w_proj: float | None = None,
        w_value: float | None = None,
        w_own: float | None = None,
    ) -> list[SwapRecommendation]:
        """
        Find and rank eligible replacement candidates for a single scratched player.

        Parameters
        ----------
        scratched_name        : Display name of the removed player.
        scratched_pos         : Position string (e.g. "PG/SG").
        scratched_salary      : Salary of the removed player.
        scratched_proj        : Projection of the removed player.
        scratched_own         : Ownership % of the removed player.
        lineup_names          : All current lineup player names (including scratched).
        pool                  : Projection DataFrame with Name, Pos, Salary, Proj, Own cols.
        salary_cap            : Contest salary cap (e.g. 50 000 for DK).
        current_lineup_salary : Total salary before replacing the scratched player.
        slot_labels           : Exact slot label(s) the scratched player occupied (e.g. ["PG"]).
                                When provided, only players eligible for that slot are returned.
        locked                : Set of player names that cannot be used as replacements.
        max_candidates        : Maximum number of recommendations to return.
        w_proj / w_value / w_own : Composite score weights (must sum to 1.0).

        Returns
        -------
        List of ``SwapRecommendation`` sorted by ``swap_score`` descending.
        """
        wp = w_proj  if w_proj  is not None else LateSwapEngine.DEFAULT_W_PROJ
        wv = w_value if w_value is not None else LateSwapEngine.DEFAULT_W_VALUE
        wo = w_own   if w_own   is not None else LateSwapEngine.DEFAULT_W_OWN

        locked_lower = {n.lower() for n in (locked or set())}
        lineup_lower = {
            n.lower() for n in lineup_names
            if n.lower() != scratched_name.lower()
        }
        budget = salary_cap - current_lineup_salary + scratched_salary

        scratch_elig  = _eligible_slots(scratched_pos)
        exact_slots   = [s.upper() for s in (slot_labels or [])]

        recs: list[SwapRecommendation] = []

        for _, row in pool.iterrows():
            cand_name = str(row.get("Name", "")).strip()
            if not cand_name:
                continue
            if cand_name.lower() in lineup_lower:
                continue
            if cand_name.lower() == scratched_name.lower():
                continue
            if cand_name.lower() in locked_lower:
                continue

            cand_salary = int(pd.to_numeric(row.get("Salary", 0), errors="coerce") or 0)
            if cand_salary > budget:
                continue

            cand_pos  = str(row.get("Pos", "UTIL"))
            cand_elig = _eligible_slots(cand_pos)

            if exact_slots:
                eligible    = any(_pos_eligible_for_slot(cand_pos, sl) for sl in exact_slots)
                exact_match = eligible
            else:
                shared      = cand_elig & scratch_elig
                # Require at least one shared slot that is NOT the catch-all UTIL slot;
                # otherwise any player would be eligible for any scratched player via UTIL.
                eligible    = bool(shared - {"UTIL"})
                # Exact match: the two players share a positional (non-generic) slot
                primary     = shared - {"UTIL", "G", "F"}
                exact_match = bool(primary)

            if not eligible:
                continue

            cand_proj = float(pd.to_numeric(row.get("Proj", 0), errors="coerce") or 0)
            cand_own  = float(pd.to_numeric(row.get("Own",  0), errors="coerce") or 0)

            recs.append(
                SwapRecommendation(
                    scratched_player=scratched_name,
                    replacement_player=cand_name,
                    replacement_pos=cand_pos,
                    replacement_salary=cand_salary,
                    replacement_proj=round(cand_proj, 2),
                    proj_delta=round(cand_proj - scratched_proj, 2),
                    salary_delta=cand_salary - scratched_salary,
                    ownership_delta=round(cand_own - scratched_own, 2),
                    position_match_exact=exact_match,
                )
            )

        if not recs:
            return []

        # Composite scoring: rank-normalise each dimension then take weighted sum
        projs  = [r.replacement_proj for r in recs]
        values = [
            round(r.replacement_proj / r.replacement_salary * 1000, 2)
            if r.replacement_salary > 0 else 0.0
            for r in recs
        ]
        own_deltas = [abs(r.ownership_delta) for r in recs]

        norm_p = _rank_normalize(projs,      ascending=False)
        norm_v = _rank_normalize(values,     ascending=False)
        norm_o = _rank_normalize(own_deltas, ascending=True)

        for i, rec in enumerate(recs):
            rec.swap_score = round(
                wp * norm_p[i] + wv * norm_v[i] + wo * norm_o[i],
                4,
            )
            # EV impact: projection gain per $1 k salary cost (magnitude-normalised)
            denom = max(1, abs(rec.salary_delta)) / 1000
            rec.ev_impact = round(rec.proj_delta / denom, 4)

        recs.sort(key=lambda r: (r.swap_score, r.replacement_proj), reverse=True)
        return recs[:max_candidates]
