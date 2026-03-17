"""
Late Swap Service
=================
Shared scoring utilities extracted from backend/routers/optimizer.py so that
both the single-lineup ``/late-swap`` and the ``/batch-late-swap`` endpoints
use identical, testable logic.

Scoring formula
---------------
  swap_score = w_proj  * rank_norm(projection)
             + w_value * rank_norm(value)
             + w_own   * rank_norm(own, ascending=True)
             + correlation_bonus (capped at 0.25)

Presets
-------
  BALANCED  : w_proj=0.50, w_value=0.30, w_own=0.20
  CASH      : w_proj=0.70, w_value=0.20, w_own=0.10
  GPP       : w_proj=0.30, w_value=0.20, w_own=0.50
"""

from __future__ import annotations

from typing import Sequence


# ---------------------------------------------------------------------------
# Weight presets
# ---------------------------------------------------------------------------

WEIGHT_PRESETS: dict[str, tuple[float, float, float]] = {
    "balanced":  (0.50, 0.30, 0.20),
    "cash":      (0.70, 0.20, 0.10),
    "gpp":       (0.30, 0.20, 0.50),
    "tournament": (0.30, 0.20, 0.50),  # alias for gpp
}


def resolve_weights(
    w_proj: float | None,
    w_value: float | None,
    w_own: float | None,
    contest_type: str | None,
) -> tuple[float, float, float]:
    """
    Return validated (w_proj, w_value, w_own).

    Priority:
    1. Explicit weights — if all three are provided and sum to 1.0 (±0.02).
    2. Contest-type preset — if ``contest_type`` is one of the known keys.
    3. Default balanced preset.

    Raises ``ValueError`` if explicit weights don't sum to 1.0.
    """
    if w_proj is not None or w_value is not None or w_own is not None:
        p = w_proj if w_proj is not None else 0.50
        v = w_value if w_value is not None else 0.30
        o = w_own if w_own is not None else 0.20
        total = p + v + o
        if not (0.98 <= total <= 1.02):
            raise ValueError(
                f"Scoring weights must sum to 1.0 (got {total:.3f}). "
                "Set w_proj + w_value + w_own = 1.0."
            )
        # Normalise in case of tiny float error
        return (p / total, v / total, o / total)

    if contest_type:
        preset = WEIGHT_PRESETS.get(contest_type.lower())
        if preset:
            return preset

    return WEIGHT_PRESETS["balanced"]


# ---------------------------------------------------------------------------
# Rank normalisation
# ---------------------------------------------------------------------------

def rank_normalize(vals: Sequence[float], ascending: bool = False) -> list[float]:
    """
    Return 0–1 rank-normalised values.

    ``ascending=False`` (default) → higher raw value earns higher normalised score.
    ``ascending=True``           → lower raw value earns higher normalised score
                                   (used for ownership — low own is desirable).
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


# ---------------------------------------------------------------------------
# Correlation bonus
# ---------------------------------------------------------------------------

def correlation_bonus(
    cand_game: str,
    cand_team: str,
    lineup_games: set[str],
    lineup_teams: set[str],
    game_to_teams: dict[str, set[str]],
    game_mate_count: int,
    team_mate_count: int,
) -> float:
    """
    Calculate the correlation / stacking bonus for a single candidate.

    Parameters
    ----------
    cand_game        : Game identifier string for the candidate.
    cand_team        : Team abbreviation for the candidate.
    lineup_games     : Set of game identifiers already in the lineup.
    lineup_teams     : Set of team abbreviations already in the lineup.
    game_to_teams    : Map of game_id → set of team abbreviations in that game.
    game_mate_count  : Number of non-scratched lineup players in the same game.
    team_mate_count  : Number of non-scratched lineup players on the same team.

    Returns
    -------
    Float bonus in [0, 0.25].
    """
    bonus = 0.0

    if cand_game and cand_game in lineup_games:
        # Stack bonus per game mate
        bonus += 0.05 * game_mate_count
        # Extra team-stack bonus per team mate
        bonus += 0.03 * team_mate_count

        # Bring-back bonus: candidate is on the opponent team in a game that
        # already has at least one lineup player.
        opponent_teams = game_to_teams.get(cand_game, set()) - {cand_team}
        if opponent_teams & lineup_teams:
            bonus += 0.04

    return min(0.25, bonus)


# ---------------------------------------------------------------------------
# Projection value selector (cash floor / GPP ceiling / balanced median)
# ---------------------------------------------------------------------------

def scoring_projection(
    row_proj: float,
    row_floor: float | None,
    row_ceil: float | None,
    contest_type: str | None,
    has_sim: bool,
) -> float:
    """
    Choose which projection to use for the scoring computation based on the
    contest type:

    * ``cash``        → Sim_P10 floor (or Proj * 0.75 estimate if unavailable)
    * ``gpp`` / ``tournament`` → Sim_P90 ceiling (or Proj * 1.30 estimate)
    * anything else   → median Proj
    """
    ct = (contest_type or "").lower()
    if ct == "cash":
        if has_sim and row_floor is not None and row_floor > 0:
            return row_floor
        return round(row_proj * 0.75, 2)
    if ct in ("gpp", "tournament"):
        if has_sim and row_ceil is not None and row_ceil > 0:
            return row_ceil
        return round(row_proj * 1.30, 2)
    return row_proj


# ---------------------------------------------------------------------------
# Candidate scorer
# ---------------------------------------------------------------------------

def score_candidates(
    candidates: list[dict],
    *,
    w_proj: float,
    w_value: float,
    w_own: float,
    lineup_games: set[str],
    lineup_teams: set[str],
    game_to_teams: dict[str, set[str]],
    lineup_game_counts: dict[str, int] | None = None,
    lineup_team_counts: dict[str, int] | None = None,
) -> None:
    """
    Compute and mutate ``swap_score`` in-place on each candidate dict.

    Each candidate must have: ``projection``, ``value``, ``own``,
    ``game_info``, ``team``.

    Optional pre-computed counts (for correlation bonus):
      lineup_game_counts  : game_id → count of non-scratched lineup players
      lineup_team_counts  : team    → count of non-scratched lineup players
    """
    if not candidates:
        return

    projs  = [float(c.get("projection", 0)) for c in candidates]
    values = [float(c.get("value", 0))      for c in candidates]
    owns   = [float(c.get("own", 0))        for c in candidates]

    norm_p = rank_normalize(projs)
    norm_v = rank_normalize(values)
    norm_o = rank_normalize(owns, ascending=True)

    gc = lineup_game_counts or {}
    tc = lineup_team_counts or {}

    for i, c in enumerate(candidates):
        base = w_proj * norm_p[i] + w_value * norm_v[i] + w_own * norm_o[i]
        corr = correlation_bonus(
            cand_game=str(c.get("game_info", "")),
            cand_team=str(c.get("team", "")),
            lineup_games=lineup_games,
            lineup_teams=lineup_teams,
            game_to_teams=game_to_teams,
            game_mate_count=gc.get(str(c.get("game_info", "")), 0),
            team_mate_count=tc.get(str(c.get("team", "")), 0),
        )
        c["swap_score"] = round(base + corr, 4)
