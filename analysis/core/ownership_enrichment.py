"""
Ownership-Aware Beneficiary Enrichment
=======================================
Enriches injury-beneficiary lists with ownership context drawn from
``ownership_history.duckdb`` and last-5/vacancy usage deltas from
``game_logs.duckdb``.

Each enriched beneficiary gains four new fields:

    proj_ownership_pct  — most-recent ``proj_at_lock`` from ownership_history
                          (0.0 when the player has no history)
    is_chalk            — True when proj_ownership_pct >= chalk_threshold
    ownership_tier      — "chalk" | "moderate" | "contrarian"
    last5_usage_delta   — beneficiary's avg minutes in vacancy games (games where
                          the injured starter has no game-log entry) minus their
                          season-average minutes.  Falls back to last-5 vs
                          prior-5 trend when no vacancy games are found.

``enrich_beneficiary_list()`` also returns a ``salary_freed`` mapping:
    {injured_player_id -> float}
derived from ``ownership_history.salary`` for the injured player (0.0 when
unavailable).  The optimizer can use this to surface valid upgrade paths.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import duckdb

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_OWNERSHIP_DB = _ROOT / "data" / "ownership_history.duckdb"
_GAME_LOGS_DB = _ROOT / "data" / "game_logs.duckdb"

# Environment-configurable default chalk threshold (can be overridden per request).
_DEFAULT_CHALK_THRESHOLD: float = float(os.getenv("DFS_CHALK_THRESHOLD", "35.0"))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ownership_tier(pct: float, chalk_threshold: float) -> str:
    if pct >= chalk_threshold:
        return "chalk"
    if pct >= chalk_threshold * 0.5:
        return "moderate"
    return "contrarian"


def _lookup_ownership(con: duckdb.DuckDBPyConnection, player_name: str, site: str) -> tuple[float, int]:
    """Return (proj_at_lock %, salary $) for the player's most recent entry.

    Falls back to (0.0, 0) when the player is not in ownership_history.
    Matches site case-insensitively; passing site="" skips the site filter.
    """
    row = con.execute(
        """
        SELECT COALESCE(proj_at_lock, 0.0), COALESCE(salary, 0)
        FROM ownership_history
        WHERE LOWER(player_name) = LOWER(?)
          AND (? = '' OR LOWER(site) = LOWER(?))
        ORDER BY game_date DESC
        LIMIT 1
        """,
        [player_name, site, site],
    ).fetchone()
    return (float(row[0]), int(row[1])) if row else (0.0, 0)


def _vacancy_usage_delta(
    log_con: duckdb.DuckDBPyConnection,
    ben_name: str,
    inj_name: str,
) -> float:
    """Compute the beneficiary's usage bump when the starter was absent.

    Primary path — vacancy games:
        Games (last 90 days) where the beneficiary played but the injured
        player has *no* game-log entry (the starter sat or was sidelined).
        Returns (vacancy_avg_minutes - overall_baseline_avg_minutes).

    Fallback — last-5 trend:
        When fewer than 2 vacancy games are found, falls back to
        (last-5-game avg minutes) - (last-10-game avg minutes).

    Returns 0.0 when not enough data is available.
    """
    vacancy_rows = log_con.execute(
        """
        SELECT gl_ben.minutes
        FROM player_game_logs gl_ben
        WHERE gl_ben.player_name = ?
          AND gl_ben.game_date >= (CURRENT_DATE - INTERVAL '90 days')
          AND NOT EXISTS (
              SELECT 1
              FROM player_game_logs gl_inj
              WHERE gl_inj.player_name = ?
                AND gl_inj.game_date = gl_ben.game_date
          )
        ORDER BY gl_ben.game_date DESC
        LIMIT 5
        """,
        [ben_name, inj_name],
    ).fetchall()
    vacancy_mins = [float(r[0]) for r in vacancy_rows if r[0] is not None]

    # Overall baseline: last 10 games regardless of starter status
    baseline_rows = log_con.execute(
        """
        SELECT minutes
        FROM player_game_logs
        WHERE player_name = ?
        ORDER BY game_date DESC
        LIMIT 10
        """,
        [ben_name],
    ).fetchall()
    baseline_mins = [float(r[0]) for r in baseline_rows if r[0] is not None]

    if not baseline_mins:
        return 0.0

    baseline_avg = sum(baseline_mins) / len(baseline_mins)

    if len(vacancy_mins) >= 2:
        vacancy_avg = sum(vacancy_mins) / len(vacancy_mins)
        return round(vacancy_avg - baseline_avg, 2)

    # Fallback: last-5 vs baseline trend
    if len(baseline_mins) >= 5:
        last5_avg = sum(baseline_mins[:5]) / 5
        return round(last5_avg - baseline_avg, 2)

    return 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enrich_beneficiary_list(
    beneficiaries: list[dict[str, Any]],
    *,
    injured_lookup: dict[str, dict[str, Any]],
    site: str = "DK",
    chalk_threshold: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Enrich a flat list of beneficiary dicts with ownership-awareness fields.

    Args:
        beneficiaries:   Flat list of beneficiary dicts.  Each must contain
                         ``injured_player_id`` so enrichment can join back to
                         the correct starter.  The player name is read from
                         ``beneficiary_name`` or ``player_name`` (either key).
        injured_lookup:  Mapping of ``{injured_player_id -> {player_name, ...}}``.
                         Used to resolve salary_freed and vacancy detection.
        site:            DFS site for ownership lookup ('DK' or 'FD').
                         Pass '' to match any site.
        chalk_threshold: Ownership % at or above which a replacement is flagged
                         as 'chalk'.  Defaults to DFS_CHALK_THRESHOLD env var
                         (35.0 if unset).

    Returns:
        (enriched_beneficiaries, salary_freed) where:
          enriched_beneficiaries — original dicts augmented with the four
                                   ownership fields described above.
          salary_freed           — {injured_player_id -> float}: the freed DFS
                                   salary when that player sits, from the most
                                   recent matched ownership_history row.
                                   0.0 when the player is not in the history.
    """
    threshold = chalk_threshold if chalk_threshold is not None else _DEFAULT_CHALK_THRESHOLD

    if not beneficiaries:
        return [], {}

    # Graceful degradation when DBs are absent
    if not _OWNERSHIP_DB.exists():
        log.warning(
            "ownership_history.duckdb not found at %s; returning beneficiaries without ownership enrichment",
            _OWNERSHIP_DB,
        )
        stub_fields = {"proj_ownership_pct": 0.0, "is_chalk": False, "ownership_tier": "contrarian", "last5_usage_delta": 0.0}
        return [{**b, **stub_fields} for b in beneficiaries], {}

    game_logs_available = _GAME_LOGS_DB.exists()
    if not game_logs_available:
        log.warning(
            "game_logs.duckdb not found at %s; last5_usage_delta will be 0.0",
            _GAME_LOGS_DB,
        )

    own_con = duckdb.connect(str(_OWNERSHIP_DB), read_only=True)
    log_con = duckdb.connect(str(_GAME_LOGS_DB), read_only=True) if game_logs_available else None

    try:
        # --- Step 1: salary_freed per injured player ---
        salary_freed: dict[str, float] = {}
        for inj_id, inj in injured_lookup.items():
            inj_name = str(inj.get("player_name") or "")
            if inj_name:
                _, sal = _lookup_ownership(own_con, inj_name, site)
                salary_freed[inj_id] = float(sal)
            else:
                salary_freed[inj_id] = 0.0

        # --- Step 2: enrich each beneficiary ---
        enriched: list[dict[str, Any]] = []
        for b in beneficiaries:
            ben_name = str(b.get("beneficiary_name") or b.get("player_name") or "")
            inj_id = str(b.get("injured_player_id") or "")
            inj_info = injured_lookup.get(inj_id, {})
            inj_name = str(inj_info.get("player_name") or "")

            proj_own, _ = _lookup_ownership(own_con, ben_name, site)

            if log_con is not None and inj_name and ben_name:
                usage_delta = _vacancy_usage_delta(log_con, ben_name, inj_name)
            else:
                usage_delta = 0.0

            enriched.append(
                {
                    **b,
                    "proj_ownership_pct": round(proj_own, 2),
                    "is_chalk": proj_own >= threshold,
                    "ownership_tier": _ownership_tier(proj_own, threshold),
                    "last5_usage_delta": usage_delta,
                }
            )

        return enriched, salary_freed

    finally:
        own_con.close()
        if log_con is not None:
            log_con.close()
