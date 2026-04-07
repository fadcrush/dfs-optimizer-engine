"""
Phase 2 — Injury Bridge
========================

Canonical pre-solve bridge: ``injury_intelligence`` is the **sole production
injury truth**. The optimizer, pool filter, and late-swap engine must not
consume replacement or beneficiary data from any other source.

Provides
--------
- ``InjuryBridgeResult``   — structured output from ``apply_injury_bridge()``
- ``apply_injury_bridge``  — enriches projections_df with canonical injury data
                             and returns a pool-filter-compatible boosts DataFrame

Bridge responsibilities
-----------------------
1. Load canonical player states from ``player_injury_state`` (nba_news.duckdb).
2. Identify OUT players — canonical source wins; manual ``context.injuries``
   overrides always merge in (manual can *add* OUTs, never silently remove them).
3. Load ``injury_beneficiaries`` rows (from ``rebuild_beneficiaries``).
4. Compute ``proj_boost`` from ``delta_minutes × dk_per_minute``.
5. Add sentinel columns to ``projections_df``:
   - ``out_flag``           bool   — True if player is confirmed OUT
   - ``beneficiary_boost``  float  — FP boost from minutes inheritance
   - ``salary_freed``       int    — salary that opened up from OUT player
   - ``chalk_signal``       float  — volatility uplift signalling chalk shift
6. Return a ``replacement_boosts_df`` in the format that
   ``pool_filter._apply_replacement_boosts()`` expects:
   columns ``player_name``, ``proj_boost``.

Fallback chain (most preferred → least preferred)
--------------------------------------------------
  A. InjuryIntelligenceService DB (nba_news.duckdb) — production path
  B. Manual context.injuries dict                  — always merged; sole source
                                                     when DB is unavailable
  C. replacement_engine.compute_boosts()           — only invoked when the
                                                     intelligence DB holds no
                                                     beneficiary rows for the
                                                     identified OUT players

``replacement_engine`` is **never** called directly from ``orchestrator.py``.
The bridge is the only importer.

Usage (orchestrator)
--------------------
::

    from analysis.core.injury_bridge import apply_injury_bridge

    bridge = apply_injury_bridge(projections_df, context)
    projections_df = bridge.projections_df          # enriched
    ...
    # pool_filter already sees out_flag; pass boosts for Proj inflation
    projections_df, filter_report = apply_pool_filter(
        projections_df,
        cfg=pool_filter_cfg,
        replacement_boosts=bridge.replacement_boosts_df,
    )
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    from analysis.core.schemas import ProjectionContext

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DK_PER_MINUTE_FALLBACK: float = 1.15   # DK pts/min when no historical rate available
_ASSUMED_STARTER_MINUTES: float = 30.0  # baseline when no minutes col on projections_df

# Status strings that indicate a player will not play
_CONFIRMED_OUT_STATUSES: frozenset[str] = frozenset({"OUT", "O"})


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class InjuryBridgeResult:
    """
    Structured output from ``apply_injury_bridge()``.

    Attributes
    ----------
    projections_df
        Original projections DataFrame enriched with ``out_flag``,
        ``beneficiary_boost``, ``salary_freed``, ``chalk_signal`` columns.
    out_players
        Player names confirmed OUT — passed downstream to pool_filter.
    replacement_boosts_df
        pool-filter-compatible DataFrame with columns:
        ``player_name``, ``proj_boost``, ``out_player``, ``delta_minutes``,
        ``dk_rate``, ``volatility_uplift``, ``reason_codes``.
    beneficiary_rows
        Full beneficiary detail DataFrame for diagnostics / API responses.
    source
        ``"intelligence"`` when the DB beneficiary table was used,
        ``"fallback"`` when replacement_engine was invoked instead,
        ``"manual_only"`` when no DB was available and no beneficiary data
        exists (manual injuries list only).
    out_player_salary_freed
        Mapping of OUT player name → their salary in the current slate.
    """

    projections_df: pd.DataFrame
    out_players: list[str]
    replacement_boosts_df: pd.DataFrame
    beneficiary_rows: pd.DataFrame
    source: str                                    # "intelligence" | "fallback" | "manual_only"
    out_player_salary_freed: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _name_col(df: pd.DataFrame) -> str:
    """Return the first available player-name column."""
    for col in ("Name", "Player", "player_name", "PLAYER_NAME"):
        if col in df.columns:
            return col
    return df.columns[0]


def _salary_col(df: pd.DataFrame) -> str | None:
    for col in ("Salary", "salary", "SALARY"):
        if col in df.columns:
            return col
    return None


def _proj_col(df: pd.DataFrame) -> str | None:
    for col in ("Proj", "Projection", "proj", "Projected"):
        if col in df.columns:
            return col
    return None


def _minutes_col(df: pd.DataFrame) -> str | None:
    for col in ("Minutes", "minutes", "proj_minutes", "ProjMin", "AvgMin", "avg_minutes"):
        if col in df.columns:
            return col
    return None


def _dk_per_minute(row: pd.Series, proj_c: str | None, min_c: str | None) -> float:
    """
    Derive a per-minute DK rate estimate for a single player row.

    Uses projected FP / projected minutes when both are available.
    Falls back to ``_DK_PER_MINUTE_FALLBACK`` otherwise.
    """
    if proj_c and min_c:
        try:
            proj = float(row.get(proj_c, 0) or 0)
            mins = float(row.get(min_c, 0) or 0)
            if mins > 2:
                return proj / mins
        except (TypeError, ValueError):
            pass
    if proj_c:
        try:
            proj = float(row.get(proj_c, 0) or 0)
            if proj > 0:
                return proj / _ASSUMED_STARTER_MINUTES
        except (TypeError, ValueError):
            pass
    return _DK_PER_MINUTE_FALLBACK


def _build_player_rate_map(
    projections_df: pd.DataFrame,
) -> dict[str, float]:
    """
    Build a lowercased-name → dk_per_minute map from projections_df.
    Used to translate ``delta_minutes`` from the beneficiary DB into
    ``proj_boost`` values that pool_filter can apply.
    """
    nc = _name_col(projections_df)
    pc = _proj_col(projections_df)
    mc = _minutes_col(projections_df)
    rate_map: dict[str, float] = {}
    for _, row in projections_df.iterrows():
        name = str(row.get(nc, "")).strip()
        if name:
            rate_map[name.lower()] = _dk_per_minute(row, pc, mc)
    return rate_map


def _load_intelligence_states(db_path: Path) -> pd.DataFrame:
    """
    Query ``player_injury_state`` from the nba_news DB.

    Returns an empty DataFrame on any failure (permissive degradation).
    """
    try:
        from analysis.shared.db import get_conn
        conn = get_conn(db_path)
        df = conn.execute(
            """
            SELECT player_id, player_name, team_id, current_status,
                   p_play, expected_minutes_mid, confidence_score,
                   p_late_scratch, updated_at
            FROM player_injury_state
            ORDER BY updated_at DESC
            """
        ).df()
        return df
    except Exception as exc:
        log.warning("injury_bridge: could not load player_injury_state: %s", exc)
        return pd.DataFrame()


def _load_beneficiary_rows(db_path: Path, injured_player_ids: list[str]) -> pd.DataFrame:
    """
    Query ``injury_beneficiaries`` for the given injured player IDs.

    Returns an empty DataFrame on any failure or when ``injured_player_ids``
    is empty.
    """
    if not injured_player_ids:
        return pd.DataFrame()
    try:
        from analysis.shared.db import get_conn
        conn = get_conn(db_path)
        placeholders = ", ".join("?" for _ in injured_player_ids)
        df = conn.execute(
            f"""
            SELECT
                player_id         AS out_player_id,
                beneficiary_player_id,
                beneficiary_name,
                team_id,
                scenario_name,
                delta_minutes,
                confidence,
                rank_score,
                volatility_uplift,
                position,
                reason_codes
            FROM injury_beneficiaries
            WHERE player_id IN ({placeholders})
              AND scenario_name = 'OUT'
            ORDER BY rank_score DESC
            """,
            injured_player_ids,
        ).df()
        return df
    except Exception as exc:
        log.warning("injury_bridge: could not load injury_beneficiaries: %s", exc)
        return pd.DataFrame()


def _run_replacement_engine_fallback(
    out_players: list[str],
    projections_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    FALLBACK ONLY: invoke ``replacement_engine.compute_boosts()`` when the
    intelligence DB holds no beneficiary rows.

    This function must not be called on the normal (intelligence) path.
    """
    log.warning(
        "injury_bridge: no beneficiary rows in intelligence DB for OUT=%s — "
        "falling back to replacement_engine.compute_boosts()",
        out_players,
    )
    try:
        from analysis.nba.replacement_engine import compute_boosts
        boosts = compute_boosts(out_players, slate_players=projections_df)
        if not boosts.empty:
            # Normalise to bridge output schema
            if "player_name" not in boosts.columns:
                boosts = boosts.rename(columns={boosts.columns[0]: "player_name"})
            for col in ("out_player", "delta_minutes", "dk_rate", "volatility_uplift", "reason_codes"):
                if col not in boosts.columns:
                    boosts[col] = None
        return boosts
    except Exception as exc:
        log.warning("injury_bridge: replacement_engine fallback failed: %s", exc)
        return pd.DataFrame(
            columns=["player_name", "proj_boost", "out_player",
                     "delta_minutes", "dk_rate", "volatility_uplift", "reason_codes"]
        )


# ---------------------------------------------------------------------------
# Sentinel column initialisation
# ---------------------------------------------------------------------------

def _ensure_sentinel_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add bridge sentinel columns if absent (non-destructive)."""
    df = df.copy()
    if "out_flag" not in df.columns:
        df["out_flag"] = False
    if "beneficiary_boost" not in df.columns:
        df["beneficiary_boost"] = 0.0
    if "salary_freed" not in df.columns:
        df["salary_freed"] = 0
    if "chalk_signal" not in df.columns:
        df["chalk_signal"] = 0.0
    return df


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_injury_bridge(
    projections_df: pd.DataFrame,
    context: "ProjectionContext",
    *,
    db_path: Path | None = None,
) -> InjuryBridgeResult:
    """
    Canonical pre-solve injury enrichment bridge.

    Steps
    -----
    1. Resolve the nba_news DB path and attempt to load canonical player
       injury states from ``player_injury_state``.
    2. Identify OUT players:
       - canonical DB states (current_status = 'OUT' or p_play < 0.10)
       - manual ``context.injuries`` overrides (always merged in)
    3. Load ``injury_beneficiaries`` rows for those players.
    4. If no beneficiary rows in DB → invoke replacement_engine (fallback).
    5. Compute ``proj_boost = delta_minutes × dk_per_minute`` for each
       beneficiary row (rate derived from projections_df).
    6. Enrich projections_df with sentinel columns.
    7. Build a pool-filter-compatible ``replacement_boosts_df``.
    8. Return ``InjuryBridgeResult``.

    Parameters
    ----------
    projections_df : Output from the projection engine (pre-pool-filter).
    context        : ``ProjectionContext`` carrying manual injuries, site, etc.
    db_path        : Override path to nba_news.duckdb (default: auto-resolved).

    Returns
    -------
    ``InjuryBridgeResult`` containing enriched projections_df, out_players,
    a pool-filter-compatible boosts DataFrame, and bridge diagnostics.
    """
    from analysis.shared.injury_utils import DB_PATH as _NBA_NEWS_DB

    resolved_db = Path(db_path) if db_path else _NBA_NEWS_DB

    nc = _name_col(projections_df)
    sc = _salary_col(projections_df)

    # ── 1. Load canonical states ──────────────────────────────────────────────
    states_df = pd.DataFrame()
    db_available = resolved_db.exists()
    if db_available:
        states_df = _load_intelligence_states(resolved_db)

    # ── 2. Identify OUT players (canonical + manual merge) ────────────────────
    out_player_names: set[str] = set()
    out_player_ids: list[str] = []

    # Canonical DB path
    if not states_df.empty:
        out_mask = (
            states_df["current_status"].str.upper().isin(_CONFIRMED_OUT_STATUSES)
            | (states_df["p_play"].fillna(1.0) < 0.10)
        )
        out_states = states_df[out_mask]
        for _, row in out_states.iterrows():
            name = str(row.get("player_name", "")).strip()
            pid  = str(row.get("player_id", "")).strip()
            if name:
                out_player_names.add(name)
            if pid:
                out_player_ids.append(pid)
        log.info(
            "injury_bridge: canonical DB identified %d OUT players: %s",
            len(out_player_names),
            sorted(out_player_names),
        )

    # Manual overrides always merge in (they are explicit operator intent)
    manual_out: set[str] = set()
    for player_name, status in (context.injuries or {}).items():
        if str(status).upper() in _CONFIRMED_OUT_STATUSES:
            manual_out.add(str(player_name).strip())

    if manual_out:
        added = manual_out - out_player_names
        if added:
            log.info("injury_bridge: manual overrides added OUT players: %s", sorted(added))
        out_player_names.update(manual_out)

    out_players: list[str] = sorted(out_player_names)

    # ── 3. Load beneficiary rows from intelligence DB ─────────────────────────
    beneficiary_rows = pd.DataFrame()
    source = "manual_only"

    if db_available and out_player_ids:
        beneficiary_rows = _load_beneficiary_rows(resolved_db, out_player_ids)
        if not beneficiary_rows.empty:
            source = "intelligence"
            log.info(
                "injury_bridge: loaded %d beneficiary rows from intelligence DB",
                len(beneficiary_rows),
            )

    # ── 4. Fallback: replacement_engine when no intelligence beneficiary rows ──
    # We may still have manual-only OUT players that don't have DB IDs.
    # Map slate names → identify those missing from DB beneficiary coverage.
    intelligence_covered: set[str] = set()
    if not beneficiary_rows.empty and "out_player_id" in beneficiary_rows.columns:
        # Cross-reference covered out_player_ids back to names via states_df
        covered_ids = set(beneficiary_rows["out_player_id"].unique())
        if not states_df.empty:
            for _, row in states_df.iterrows():
                pid = str(row.get("player_id", ""))
                pname = str(row.get("player_name", "")).strip()
                if pid in covered_ids and pname:
                    intelligence_covered.add(pname.lower())

    uncovered_out = [
        p for p in out_players
        if p.lower() not in intelligence_covered
    ]

    fallback_boosts = pd.DataFrame()
    if uncovered_out:
        fallback_boosts = _run_replacement_engine_fallback(uncovered_out, projections_df)
        if source == "manual_only" and not fallback_boosts.empty:
            source = "fallback"

    # ── 5. Build rate map and compute proj_boost from intelligence rows ────────
    rate_map = _build_player_rate_map(projections_df)

    intelligence_boosts_rows: list[dict[str, Any]] = []
    if not beneficiary_rows.empty:
        for _, row in beneficiary_rows.iterrows():
            bname = str(row.get("beneficiary_name", "")).strip()
            if not bname:
                continue
            delta_min = float(row.get("delta_minutes", 0) or 0)
            rate = rate_map.get(bname.lower(), _DK_PER_MINUTE_FALLBACK)
            proj_boost = round(delta_min * rate, 2)
            if proj_boost <= 0:
                continue  # ignore zero / negative boosts

            # Decode reason_codes from JSON if stored as string
            raw_reasons = row.get("reason_codes", "[]")
            try:
                reasons = json.loads(raw_reasons) if isinstance(raw_reasons, str) else list(raw_reasons)
            except Exception:
                reasons = []

            intelligence_boosts_rows.append({
                "player_name":      bname,
                "proj_boost":       proj_boost,
                "out_player":       str(row.get("out_player_id", "")),
                "delta_minutes":    delta_min,
                "dk_rate":          round(rate, 4),
                "volatility_uplift": float(row.get("volatility_uplift", 0) or 0),
                "reason_codes":     reasons,
            })

    if intelligence_boosts_rows:
        # Aggregate multiple OUT players contributing to the same beneficiary
        intel_df = pd.DataFrame(intelligence_boosts_rows)
        intel_df = (
            intel_df.groupby("player_name", as_index=False)
            .agg(
                proj_boost=("proj_boost", "sum"),
                out_player=("out_player", lambda x: ", ".join(str(v) for v in x.unique())),
                delta_minutes=("delta_minutes", "sum"),
                dk_rate=("dk_rate", "mean"),
                volatility_uplift=("volatility_uplift", "sum"),
                reason_codes=("reason_codes", lambda x: list({r for rs in x for r in rs})),
            )
        )
        boost_frames = [intel_df]
    else:
        boost_frames = []

    if not fallback_boosts.empty:
        boost_frames.append(fallback_boosts)

    if boost_frames:
        replacement_boosts_df = pd.concat(boost_frames, ignore_index=True)
        # Deduplicate by player_name, keeping highest proj_boost
        replacement_boosts_df = (
            replacement_boosts_df
            .sort_values("proj_boost", ascending=False)
            .drop_duplicates(subset=["player_name"])
            .reset_index(drop=True)
        )
    else:
        replacement_boosts_df = pd.DataFrame(
            columns=["player_name", "proj_boost", "out_player",
                     "delta_minutes", "dk_rate", "volatility_uplift", "reason_codes"]
        )

    # ── 6. Build salary_freed map ──────────────────────────────────────────────
    salary_freed_map: dict[str, int] = {}
    if sc:
        slate_name_lower = projections_df[nc].str.strip().str.lower()
        for pname in out_players:
            mask = slate_name_lower == pname.lower()
            if mask.any():
                sal = projections_df.loc[mask, sc].iloc[0]
                try:
                    salary_freed_map[pname] = int(float(sal))
                except (TypeError, ValueError):
                    salary_freed_map[pname] = 0

    # ── 7. Enrich projections_df with sentinel columns ─────────────────────────
    projections_df = _ensure_sentinel_columns(projections_df)

    out_names_lower = {p.lower() for p in out_players}
    slate_name_lower = projections_df[nc].str.strip().str.lower()

    # out_flag
    projections_df["out_flag"] = slate_name_lower.isin(out_names_lower)

    # beneficiary_boost + chalk_signal
    if not replacement_boosts_df.empty and "player_name" in replacement_boosts_df.columns:
        boost_map = replacement_boosts_df.set_index("player_name")
        for i, row in projections_df.iterrows():
            pname = str(row.get(nc, "")).strip()
            pkey = pname.lower()
            # Match against boost_map (case-insensitive)
            matched_key = next(
                (k for k in boost_map.index if k.lower() == pkey), None
            )
            if matched_key is not None:
                projections_df.at[i, "beneficiary_boost"] = float(
                    boost_map.at[matched_key, "proj_boost"]
                )
                vu_raw = (
                    boost_map.at[matched_key, "volatility_uplift"]
                    if "volatility_uplift" in boost_map.columns else None
                )
                projections_df.at[i, "chalk_signal"] = float(vu_raw) if vu_raw is not None else 0.0

    # salary_freed: each beneficiary inherits the freed salary of the OUT player
    # (stored as the freed salary of the OUT player they benefit from — useful
    #  for the optimizer value signal and UI display)
    for i, row in projections_df.iterrows():
        pname = str(row.get(nc, "")).strip()
        if sc and pname.lower() in out_names_lower:
            sal = salary_freed_map.get(pname, 0)
            projections_df.at[i, "salary_freed"] = sal

    # ── 8. Summary log ────────────────────────────────────────────────────────
    n_out       = int(projections_df["out_flag"].sum())
    n_boosted   = int((projections_df["beneficiary_boost"] > 0).sum())
    log.info(
        "injury_bridge: source=%s | out=%d | boosted=%d | replacement_boosts=%d",
        source, n_out, n_boosted, len(replacement_boosts_df),
    )

    return InjuryBridgeResult(
        projections_df=projections_df,
        out_players=out_players,
        replacement_boosts_df=replacement_boosts_df,
        beneficiary_rows=beneficiary_rows,
        source=source,
        out_player_salary_freed=salary_freed_map,
    )
