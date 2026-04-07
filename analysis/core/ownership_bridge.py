"""
Ownership Bridge
================
Canonical ownership-model dispatcher for the NBA projection pipeline.

Replaces the inline closure block in ``orchestrator.py`` with a single
well-typed entry point.

Resolution order (``mode="auto"``)
-----------------------------------
1. ML model  (``ownership_v2.predict_ownership``)   → source="ml"
2. Weighted  (``ownership_weighted.estimate_ownership_weighted``) → source="weighted"
3. Simple    (``ownership.estimate_ownership``)      → source="simple"
4. None      (no model available)                    → source="none"

Any ``mode`` value other than "ml", "weighted", "simple" treats as "auto".

Output contract
---------------
After ``apply_ownership_bridge`` runs, ``projections_df`` is guaranteed to have:

    Own       — ownership estimate 0–100 % (float; present even when no model ran)
    Own_Est   — alias of Own (for backward compatibility with UI/export code)
    own_source — string tag matching ``OwnershipBridgeResult.source``

``Own`` is normalised from ``Own_Est`` when the underlying model does not set it
directly (weighted and simple models only set ``Own_Est``).

The function is sport-gated: non-NBA sports pass through unchanged with
source="none".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class OwnershipBridgeResult:
    """Lightweight result returned by ``apply_ownership_bridge``."""

    source: str
    """One of "ml" | "weighted" | "simple" | "none"."""

    model_available: bool
    """False when no model could be loaded at all (all models raised)."""


# ---------------------------------------------------------------------------
# Internal helpers — thin wrappers around each model
# ---------------------------------------------------------------------------

def _try_ml(df: pd.DataFrame, sport: str, site: str) -> pd.DataFrame | None:
    try:
        from analysis.nba.ownership_v2 import predict_ownership as _predict_v2
        result = _predict_v2(df, sport=sport, site=site)
        log.info("Ownership v2 (ML) predictions applied")
        return result
    except Exception as exc:
        log.warning("Ownership v2 skipped: %s", exc)
        return None


def _try_weighted(df: pd.DataFrame, site: str) -> pd.DataFrame | None:
    try:
        from analysis.nba.ownership_weighted import estimate_ownership_weighted
        result = estimate_ownership_weighted(df, site=site)
        log.info("Ownership weighted model applied")
        return result
    except Exception as exc:
        log.warning("Ownership weighted skipped: %s", exc)
        return None


def _try_simple(df: pd.DataFrame) -> pd.DataFrame | None:
    try:
        from analysis.nba.ownership import estimate_ownership as _estimate_simple
        result = _estimate_simple(df)
        log.info("Ownership simple (rank-based) model applied")
        return result
    except Exception as exc:
        log.warning("Ownership simple skipped: %s", exc)
        return None


def _normalise_own_column(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure ``Own`` exists as an alias of ``Own_Est``, and vice-versa.

    ``ownership_v2`` sets both; ``ownership_weighted`` and ``ownership.py``
    only set ``Own_Est``.  This normalisation makes ``compute_leverage_scores``
    (which reads ``Own``) work correctly regardless of which model ran.
    """
    if "Own" not in df.columns and "Own_Est" in df.columns:
        df = df.copy()
        df["Own"] = df["Own_Est"]
    elif "Own_Est" not in df.columns and "Own" in df.columns:
        df = df.copy()
        df["Own_Est"] = df["Own"]
    elif "Own" not in df.columns and "Own_Est" not in df.columns:
        df = df.copy()
        df["Own"] = 0.0
        df["Own_Est"] = 0.0
    return df


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_ownership_bridge(
    projections_df: pd.DataFrame,
    *,
    mode: str = "auto",
    site: str = "DK",
    sport: str = "NBA",
) -> tuple[pd.DataFrame, OwnershipBridgeResult]:
    """Apply the ownership model pipeline to ``projections_df``.

    Parameters
    ----------
    projections_df:
        Canonical projections DataFrame (``Proj``, ``Salary`` required).
    mode:
        Which model to run.  ``"auto"`` tries ML → weighted → simple.
        ``"ml"`` | ``"weighted"`` | ``"simple"`` select a single model
        with no fallback.  Any unrecognised value is treated as ``"auto"``.
    site:
        DFS site for ownership lookup (``"DK"`` or ``"FD"``).
    sport:
        Short sport tag.  Only ``"NBA"`` models are registered; other
        sports pass through untouched (``source="none"``).

    Returns
    -------
    (enriched_df, OwnershipBridgeResult)
        ``enriched_df`` is guaranteed to have ``Own``, ``Own_Est``, and
        ``own_source`` columns.
    """
    if sport.upper() != "NBA":
        df = _normalise_own_column(projections_df)
        return df, OwnershipBridgeResult(source="none", model_available=False)

    _mode = mode.strip().lower()

    enriched: pd.DataFrame | None = None
    source = "none"

    if _mode == "ml":
        enriched = _try_ml(projections_df, sport=sport, site=site)
        if enriched is not None:
            source = "ml"

    elif _mode == "weighted":
        enriched = _try_weighted(projections_df, site=site)
        if enriched is not None:
            source = "weighted"

    elif _mode == "simple":
        enriched = _try_simple(projections_df)
        if enriched is not None:
            source = "simple"

    else:  # "auto" cascade
        enriched = _try_ml(projections_df, sport=sport, site=site)
        if enriched is not None:
            source = "ml"
        else:
            enriched = _try_weighted(projections_df, site=site)
            if enriched is not None:
                source = "weighted"
            else:
                enriched = _try_simple(projections_df)
                if enriched is not None:
                    source = "simple"

    if enriched is None:
        # All models failed — return original df with zeroed Own columns
        enriched = projections_df.copy()
        enriched["Own_Est"] = enriched.get("Own_Est", 0.0)
        enriched["own_source"] = "none"
        source = "none"

    # Guarantee Own/Own_Est columns and tag source
    enriched = _normalise_own_column(enriched)
    if "own_source" not in enriched.columns:
        enriched = enriched.copy()
        enriched["own_source"] = source

    model_available = source != "none"
    return enriched, OwnershipBridgeResult(source=source, model_available=model_available)
