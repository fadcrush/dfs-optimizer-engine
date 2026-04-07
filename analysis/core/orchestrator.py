from __future__ import annotations

import csv as _csv
import io
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from analysis.nba.optimizer import optimize_portfolio
from analysis.nba.pool_filter import PoolFilterConfig, apply_pool_filter
from analysis.shared.dfs_schema import normalize_slate_df
# Phase 2: replacement_engine is demoted — only the injury bridge may call it.
from .injury_bridge import InjuryBridgeResult, apply_injury_bridge
# Phase 4: ownership model pipeline isolated behind the ownership bridge.
from .ownership_bridge import OwnershipBridgeResult, apply_ownership_bridge

from .contest_sim import ContestConfig, enrich_simulation_with_ev
from .exposure_optimizer import (
    ExposureConfig,
    compute_leverage_scores,
    resolve_per_player_caps,
    build_exposure_report,
    build_stack_report,
)
from .projection_engine import CanonicalNBAProjectionEngine
from .simulation import SimulationConfig, simulate_lineup_scores, simulate_player_outcomes, summarize_player_sims
from .schemas import ProjectionContext
from .game_state import compute_game_state
from analysis.nfl.projection_engine import NFLProjectionEngine
from analysis.nfl.optimizer import NFLOptimizer

log = logging.getLogger(__name__)


def _auto_refresh_injuries() -> None:
    """
    Called automatically at the start of every pipeline run.
    Hits the NBA page (fast — just reads the HTML link list), compares the
    latest PDF URL against what’s in the DB, and only downloads + parses
    when there is actually something new.
    """
    try:
        from pathlib import Path as _Path
        from scripts.jobs.fetch_nba_injuries import ensure_current, DB_PATH
        result = ensure_current(db_path=DB_PATH)
        if result["status"] == "current":
            log.info("Injuries ✅ already current — %s", result["message"])
        elif result["status"] == "updated":
            log.info("Injuries 🔄 refreshed — %s", result["message"])
        else:
            log.warning("Injury refresh issue: %s", result["message"])
    except Exception as exc:
        # Never block the pipeline over an injury-fetch failure
        log.warning("Auto injury refresh skipped: %s", exc)


def _print_injury_summary(filter_report: dict) -> None:
    """Log a formatted injury summary after pool filtering."""
    removed_detail = filter_report.get("removed_injury_detail", [])
    injuries_today = filter_report.get("injuries_today", [])

    # Count by status across all of today's report
    from collections import Counter
    status_counts = Counter(r["status"] for r in injuries_today)

    log.info("=" * 60)
    log.info("  INJURY REPORT SUMMARY  (%d players listed today)", len(injuries_today))
    log.info("  OUT: %d  |  QUESTIONABLE: %d  |  DOUBTFUL: %d  |  PROBABLE: %d",
             status_counts.get("OUT", 0),
             status_counts.get("QUESTIONABLE", 0),
             status_counts.get("DOUBTFUL", 0),
             status_counts.get("PROBABLE", 0))
    log.info("=" * 60)

    if removed_detail:
        log.info("  REMOVED FROM SLATE (%d players):", len(removed_detail))
        for p in sorted(removed_detail, key=lambda x: x["status"]):
            reason = p["detail"] or "No reason provided"
            log.info("    %-28s  %-14s  %s", p["name"], p["status"], reason)
    else:
        log.info("  No players removed from slate due to injury/OUT status.")

    log.info("=" * 60)


def _read_slate_csv(path: Path) -> pd.DataFrame:
    """Read a slate CSV, handling FanDuel's combined lineup+player-pool format.

    FanDuel exports a single CSV where the left section (columns 0-14) contains
    existing lineup entries and the right section (columns 15+) contains the full
    player pool.  The two sections share the same rows, causing pandas to see
    inconsistent column counts and raise a ParserError.

    Detection: first header column is 'entry_id'.
    Recovery: find the row containing 'Player ID + Player Name', extract the
    right-hand sub-table from that row onwards, and return it as a clean DataFrame.
    """
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = [l for l in text.splitlines() if l.strip()]

    if not lines:
        return pd.read_csv(path)

    first_header = next(iter(_csv.reader([lines[0]])), [])
    if not first_header or first_header[0].strip().lower() != "entry_id":
        return pd.read_csv(path)  # standard single-section CSV

    # Locate the player-pool header row (contains "Player ID + Player Name")
    pool_header_idx = None
    pool_col_offset = None
    for i, line in enumerate(lines):
        row = next(iter(_csv.reader([line])), [])
        for j, cell in enumerate(row):
            if "player id" in cell.lower() and "player name" in cell.lower():
                pool_header_idx = i
                pool_col_offset = j
                break
        if pool_header_idx is not None:
            break

    if pool_header_idx is None or pool_col_offset is None:
        # Combined format detected but player pool not found — skip bad lines
        return pd.read_csv(io.StringIO(text), on_bad_lines="skip")

    # Extract the right-hand sub-table
    player_rows: list[list[str]] = []
    for line in lines[pool_header_idx:]:
        row = next(iter(_csv.reader([line])), [])
        if len(row) > pool_col_offset:
            player_rows.append(row[pool_col_offset:])

    if not player_rows:
        return pd.read_csv(io.StringIO(text), on_bad_lines="skip")

    header = player_rows[0]
    data = player_rows[1:]
    max_cols = max((len(r) for r in [header] + data), default=1)
    header += [""] * (max_cols - len(header))
    data = [r + [""] * (max_cols - len(r)) for r in data]

    df = pd.DataFrame(data, columns=header)
    # Drop completely empty rows (trailing blank lines in the export)
    df = df[df.apply(lambda r: r.str.strip().any(), axis=1)].reset_index(drop=True)
    log.info("FanDuel combined CSV detected — extracted %d player rows", len(df))
    return df


# ── Phase 5: Decomposed pipeline helpers ────────────────────────────────────
# run_dfs_pipeline() is now a thin coordinator; each logical stage lives in its
# own private helper so the call-graph is testable in isolation.

def _ingest_and_enrich_slate(
    slate_path: Path,
    context: "ProjectionContext",
) -> tuple["pd.DataFrame", str, "ProjectionContext"]:
    """Read & normalize the slate CSV, then layer in Vegas totals and lineup start probs.

    Returns (normalized_df, resolved_site, resolved_context).
    """
    raw_df = _read_slate_csv(slate_path)
    normalized_df, resolved_site = normalize_slate_df(raw_df, site=context.site)

    if context.sport.upper() == "NBA":
        try:
            from analysis.shared.vegas_enricher import enrich_with_vegas
            normalized_df = enrich_with_vegas(normalized_df, sport="NBA")
            log.info("Vegas enrichment applied — %d players", len(normalized_df))
        except Exception as exc:
            log.warning("Vegas enrichment skipped: %s", exc)

        try:
            from analysis.nba.lineup_status import add_start_prob
            normalized_df = add_start_prob(normalized_df)
            log.info("Lineup start probabilities added")
        except Exception as exc:
            log.warning("Lineup status skipped: %s", exc)

    resolved_context = context.model_copy(update={"site": resolved_site})
    return normalized_df, resolved_site, resolved_context


def _build_projections(
    normalized_df: "pd.DataFrame",
    resolved_context: "ProjectionContext",
    slate_path: Path,
) -> "tuple[pd.DataFrame, bool, str | None]":
    """Select & run the projection engine (with cache), then apply optional stat enrichment.

    Returns ``(projections_df, cache_hit, cached_at_iso)``.\n    ``cache_hit`` is True when the DataFrame was served from cache.\n    ``cached_at_iso`` is the ISO-8601 UTC string when the cache entry was\n    written, or None for misses / legacy entries.\n    """
    if resolved_context.sport.upper() == "NFL":
        engine: Any = NFLProjectionEngine()
    else:
        engine = CanonicalNBAProjectionEngine()

    _cache_key = resolved_context.slate_id or slate_path.stem
    projections_df: pd.DataFrame | None = None
    _cache_hit = False
    _cached_at_iso: str | None = None
    if not resolved_context.force_refresh:
        try:
            from analysis.core.projection_cache import get_cache
            projections_df, _cached_at_iso = get_cache().get_with_meta(
                _cache_key, resolved_context.sport, resolved_context.site
            )
            _cache_hit = projections_df is not None
        except Exception as exc:
            log.warning("Projection cache lookup failed: %s", exc)

    if projections_df is None:
        projections_df = engine.generate(normalized_df, resolved_context)
        try:
            from analysis.core.projection_cache import get_cache
            get_cache().set(
                _cache_key, resolved_context.sport, resolved_context.site, projections_df
            )
        except Exception as exc:
            log.warning("Projection cache write failed: %s", exc)

    import os as _os_enrich
    if (
        _os_enrich.getenv("DFS_ENABLE_STAT_ENRICHMENT", "0") == "1"
        and resolved_context.sport.upper() == "NBA"
    ):
        try:
            from analysis.nba.stat_projection_breakdown import enrich_with_stat_breakdown
            projections_df = enrich_with_stat_breakdown(
                projections_df, site=resolved_context.site
            )
            log.info("Stat/minutes enrichment applied")
        except Exception as exc:
            log.warning("Stat enrichment skipped: %s", exc)

    return projections_df, _cache_hit, _cached_at_iso


def _run_pre_sim(
    projections_df: "pd.DataFrame",
    pre_sim_sims: int,
    sim_seed: "int | None",
    correlation: str,
) -> "pd.DataFrame":
    """Run pre-solve player outcome simulation and merge summary back onto projections_df."""
    pre_config = SimulationConfig(
        n_sims=pre_sim_sims,
        seed=sim_seed,
        correlation="team" if correlation == "team" else "none",
    )
    player_sims = simulate_player_outcomes(projections_df, pre_config)
    player_summary = summarize_player_sims(projections_df, player_sims)
    if not player_summary.empty:
        projections_df = projections_df.merge(player_summary, on="DFS_ID", how="left")
    return projections_df


def _apply_injury_context(
    projections_df: "pd.DataFrame",
    context: "ProjectionContext",
) -> "pd.DataFrame":
    """Stamp any explicit context.injuries into the InjuryStatus column."""
    if not context.injuries:
        return projections_df
    name_col = next(
        (c for c in ["Name", "Player", "player_name"] if c in projections_df.columns),
        projections_df.columns[0],
    )
    inj_upper = {k.lower(): str(v).upper() for k, v in context.injuries.items()}
    if "InjuryStatus" not in projections_df.columns:
        projections_df["InjuryStatus"] = ""
    for i, row in projections_df.iterrows():
        pname_lower = str(row[name_col]).lower()
        if pname_lower in inj_upper:
            projections_df.at[i, "InjuryStatus"] = inj_upper[pname_lower]
    return projections_df


def _apply_pool_preparation(
    projections_df: "pd.DataFrame",
    context: "ProjectionContext",
    apply_filter: bool,
    pool_filter_cfg: "PoolFilterConfig | None",
) -> "tuple[pd.DataFrame, InjuryBridgeResult | None, pd.DataFrame | None, dict]":
    """Run injury bridge then pool filter.

    Returns (projections_df, bridge, replacement_boosts, filter_report).
    The injury bridge is the ONLY place replacement_engine may be called.
    """
    _bridge: InjuryBridgeResult | None = None
    replacement_boosts: pd.DataFrame | None = None
    filter_report: dict = {}

    if apply_filter and context.sport.upper() == "NBA":
        _bridge = apply_injury_bridge(projections_df, context)
        projections_df = _bridge.projections_df
        replacement_boosts = (
            _bridge.replacement_boosts_df
            if not _bridge.replacement_boosts_df.empty
            else None
        )
        if _bridge.out_players:
            log.info(
                "Injury bridge [%s]: %d OUT players — %s",
                _bridge.source,
                len(_bridge.out_players),
                _bridge.out_players,
            )

    if apply_filter:
        projections_df, filter_report = apply_pool_filter(
            projections_df,
            cfg=pool_filter_cfg,
            replacement_boosts=replacement_boosts,
        )
        log.info("Pool filter report: %s", filter_report)
        _print_injury_summary(filter_report)

    return projections_df, _bridge, replacement_boosts, filter_report


def _enrich_props_and_ownership(
    projections_df: "pd.DataFrame",
    resolved_site: str,
    context: "ProjectionContext",
) -> "tuple[pd.DataFrame, str]":
    """Apply props enrichment then the ownership model bridge.

    Returns (projections_df, ownership_model_used).
    """
    if context.sport.upper() == "NBA":
        try:
            from analysis.shared.props_enricher import enrich_with_props
            projections_df = enrich_with_props(projections_df)
            has_pts_line = (
                int(projections_df["prop_pts_line"].notna().sum())
                if "prop_pts_line" in projections_df.columns
                else 0
            )
            log.info("Props enrichment applied — %d players have a points line", has_pts_line)
        except Exception as exc:
            log.warning("Props enrichment skipped: %s", exc)

    import os as _os
    _ownership_mode = _os.environ.get("DFS_OWNERSHIP_MODE", "auto").strip().lower()
    projections_df, _ownership_bridge = apply_ownership_bridge(
        projections_df,
        mode=_ownership_mode,
        site=resolved_site,
        sport=context.sport,
    )
    return projections_df, _ownership_bridge.source


def _compute_value_and_quality(projections_df: "pd.DataFrame") -> "pd.DataFrame":
    """Add Value column, per-player data_quality_flags list, and projection_confidence score."""
    if "Proj" in projections_df.columns and "Salary" in projections_df.columns:
        _sal = pd.to_numeric(projections_df["Salary"], errors="coerce").fillna(1)
        _proj = pd.to_numeric(projections_df["Proj"], errors="coerce").fillna(0)
        projections_df["Value"] = (_proj / (_sal / 1000.0)).round(2)

    _api_key_present = bool(__import__("os").getenv("THE_ODDS_API_KEY", ""))
    import math as _math

    _flag_rows: list[list[str]] = []
    _conf_rows: list[float] = []

    for _, _row in projections_df.iterrows():
        flags: list[str] = []

        _tt = _row.get("team_total")
        _tt_valid = _tt is not None and not (isinstance(_tt, float) and _math.isnan(_tt))
        if _tt_valid:
            flags.append("vegas_applied")
        elif _api_key_present:
            flags.append("vegas_fallback")

        _own_src = str(_row.get("own_source", ""))
        if _own_src == "model":
            flags.append("ownership_trained")
        elif _own_src in ("fallback", "weighted", "simple"):
            flags.append("ownership_fallback")

        _inj = str(_row.get("InjuryStatus", "")).upper()
        if _inj and _inj not in ("", "A", "ACTIVE"):
            flags.append("injury_adjusted")
        _rb = _row.get("replacement_boost", 0) or _row.get("has_replacement_boost", 0)
        if _rb and float(_rb) > 0:
            flags.append("injury_adjusted")

        _games = _row.get("games_played") or _row.get("log_games") or 0
        if _games and int(_games) < 5:
            flags.append("low_minutes_sample")

        _stat_conf = _row.get("stat_confidence") or _row.get("StatConfidence")
        if _stat_conf is not None:
            try:
                if float(_stat_conf) < 0.5:
                    flags.append("limited_confidence")
            except (TypeError, ValueError):
                pass

        _flag_rows.append(flags)

        _conf = 1.0
        if "vegas_fallback" in flags:
            _conf -= 0.20
        if "ownership_fallback" in flags:
            _conf -= 0.10
        if "low_minutes_sample" in flags:
            _conf -= 0.20
        if "limited_confidence" in flags:
            _conf -= 0.15
        _conf_rows.append(round(max(0.0, min(1.0, _conf)), 2))

    projections_df = projections_df.copy()
    projections_df["data_quality_flags"] = _flag_rows
    projections_df["projection_confidence"] = _conf_rows
    return projections_df


def _apply_projection_overrides(
    projections_df: "pd.DataFrame",
    context: "ProjectionContext",
) -> "pd.DataFrame":
    """Apply any explicit projection_overrides from context."""
    if not context.projection_overrides:
        return projections_df
    name_col_candidates = ["Name", "Player", "player_name"]
    proj_name_col = next(
        (c for c in name_col_candidates if c in projections_df.columns), None
    )
    if proj_name_col:
        count = 0
        for player_name, override_val in context.projection_overrides.items():
            mask = projections_df[proj_name_col].str.lower() == player_name.lower()
            if mask.any():
                projections_df.loc[mask, "Proj"] = float(override_val)
                count += 1
        log.info("Applied %d projection overrides", count)
    return projections_df


def _build_initial_result(
    projections_df: "pd.DataFrame",
    resolved_site: str,
    context: "ProjectionContext",
    filter_report: dict,
    ownership_model_used: str,
    replacement_boosts: "pd.DataFrame | None",
    _bridge: "InjuryBridgeResult | None",
    cache_hit: bool = False,
    cached_at_iso: "str | None" = None,
) -> "dict[str, Any]":
    """Construct and return the initial result dict (no lineups yet)."""
    return {
        "success": True,
        "site": resolved_site,
        "sport": context.sport,
        "projections_df": projections_df,
        "lineups_df": pd.DataFrame(),
        "filter_report": filter_report,
        "ownership_model_used": ownership_model_used,
        "removed_players": filter_report.get("removed_players", []),
        "removed_injury_detail": filter_report.get("removed_injury_detail", []),
        "injuries_today": filter_report.get("injuries_today", []),
        "replacement_boosts": (
            replacement_boosts.to_dict("records")
            if replacement_boosts is not None and not replacement_boosts.empty
            else []
        ),
        "injury_bridge_source": _bridge.source if _bridge is not None else "none",
        "out_players": _bridge.out_players if _bridge is not None else [],
        "salary_freed": _bridge.out_player_salary_freed if _bridge is not None else {},
        "cache_hit": cache_hit,
        "cached_at": cached_at_iso,
        "stats": {
            "total_players": int(len(projections_df)),
            "avg_projection": (
                float(projections_df["Proj"].mean()) if len(projections_df) else 0.0
            ),
        },
    }


def _generate_lineups(
    result: "dict[str, Any]",
    projections_df: "pd.DataFrame",
    normalized_df: "pd.DataFrame",
    resolved_site: str,
    context: "ProjectionContext",
    n_lineups: int,
    num_unique: int,
    exposure_cfg: "ExposureConfig | None",
    contest_cfg: "ContestConfig | None",
) -> "dict[str, Any]":
    """Generate n_lineups via the NFL or NBA optimizer and attach to result."""
    if context.sport.upper() == "NFL":
        nfl_optimizer = NFLOptimizer(site=resolved_site, contest_type="Classic")
        nfl_lineups = nfl_optimizer.generate(
            projections_df,
            n_lineups=n_lineups,
            max_from_team=context.max_from_team,
            min_teams=2,
            locks=list(context.locks),
            fades=list(context.fades),
        )
        nfl_rows = []
        for lu in nfl_lineups:
            row = (
                {"LineupIndex": lu.lineup_id - 1}
                if hasattr(lu, "lineup_id")
                else {"LineupIndex": len(nfl_rows)}
            )
            row["TotalSalary"] = lu.total_salary
            row["Proj"] = lu.total_projection
            for sl in lu.slots:
                row[sl.slot] = sl.name
            nfl_rows.append(row)
        lineups_df = pd.DataFrame(nfl_rows)
        result["lineups_df"] = lineups_df
        result["stats"]["generated_lineup_rows"] = int(len(lineups_df))
        log.info("NFL optimizer generated %d lineups", len(nfl_lineups))
        return result

    # ── NBA path ──────────────────────────────────────────────────────────────
    eff_exp_cfg = exposure_cfg or ExposureConfig(global_max=0.60)
    projections_df = compute_leverage_scores(projections_df, eff_exp_cfg)
    result["projections_df"] = projections_df

    per_player_caps, per_player_floors = resolve_per_player_caps(
        projections_df, n_lineups, eff_exp_cfg
    )

    _ct = (contest_cfg.contest_type if contest_cfg else "gpp").lower()
    try:
        from analysis.nba.optimizer_constraints import apply_contest_constraints
        _opt_pool, _contest_stack_rule = apply_contest_constraints(
            projections_df, contest_type=_ct, site=resolved_site
        )
        log.info(
            "Contest constraints (%s) applied: %d → %d players in pool",
            _ct, len(projections_df), len(_opt_pool),
        )
    except Exception as _cc_exc:
        log.warning("Contest constraints skipped: %s", _cc_exc)
        _opt_pool = projections_df
        _contest_stack_rule = None

    stack_rule = None
    if context.enable_stacking:
        from analysis.nba.optimizer import StackRule
        stack_rule = StackRule(
            min_from_same_game=context.min_game_stack,
            bring_back_count=context.bring_back_count,
            max_from_same_team=context.max_from_team,
            max_from_same_game=context.max_from_game,
        )
        log.info(
            "Context stacking override — min_game_stack=%d, bring_back=%d, max_team=%d, max_game=%d",
            context.min_game_stack, context.bring_back_count,
            context.max_from_team, context.max_from_game,
        )
    elif _contest_stack_rule is not None:
        stack_rule = _contest_stack_rule
        log.info("Using contest-derived stack rule for %s", _ct)

    lineups_df = optimize_portfolio(
        players=_opt_pool,
        site=resolved_site,
        n_lineups=n_lineups,
        num_unique=num_unique,
        per_player_caps=per_player_caps,
        per_player_floors=per_player_floors,
        locks=list(context.locks),
        fades=list(context.fades),
        stack_rules=stack_rule,
        game_state=compute_game_state(normalized_df),
    )
    result["lineups_df"] = lineups_df
    result["stats"]["generated_lineup_rows"] = int(len(lineups_df))

    n_gen = lineups_df["LineupIndex"].nunique() if not lineups_df.empty else 0
    if n_gen > 0:
        exposure_report = build_exposure_report(
            lineups_df, projections_df, n_lineups=n_gen, cfg=eff_exp_cfg
        )
        stack_report = build_stack_report(lineups_df)
        result["exposure_report"] = exposure_report
        result["stack_report"] = stack_report
        log.info(
            "Exposure report built — %d players used, %d teams stacked",
            int((exposure_report["ActualCount"] > 0).sum()) if not exposure_report.empty else 0,
            len(stack_report),
        )

    return result


def _run_simulation(
    result: "dict[str, Any]",
    projections_df: "pd.DataFrame",
    n_sims: int,
    sim_seed: "int | None",
    correlation: str,
    contest_cfg: "ContestConfig | None",
    resolved_site: str,
) -> "dict[str, Any]":
    """Monte Carlo lineup simulation + contest EV ranking."""
    sim_config = SimulationConfig(
        n_sims=n_sims,
        seed=sim_seed,
        correlation="team" if correlation == "team" else "none",
    )
    sim_summary, scores_matrix = simulate_lineup_scores(
        result["lineups_df"], projections_df, sim_config
    )

    if not sim_summary.empty and scores_matrix.size > 0:
        eff_contest_cfg = contest_cfg or ContestConfig(
            site=resolved_site,
            contest_type="gpp",
            entry_fee=3.0,
            field_size=100,
        )
        sim_summary = enrich_simulation_with_ev(
            sim_summary, scores_matrix, eff_contest_cfg, seed=sim_seed
        )
        log.info("Contest EV enrichment complete — %d lineups ranked by ROI", len(sim_summary))

    result["simulation_df"] = sim_summary
    return result


def _export_results(
    result: "dict[str, Any]",
    export_dir: str,
    resolved_site: str,
    slate_path: Path,
    n_lineups: int,
    pre_sim: bool,
) -> "dict[str, Any]":
    """Write projections, lineups, simulation, exposure, and stack CSVs to export_dir."""
    export_root = Path(export_dir)
    export_root.mkdir(parents=True, exist_ok=True)

    projections_df = result["projections_df"]

    projections_file = export_root / f"projections_{resolved_site}_{slate_path.stem}.csv"
    projections_df.to_csv(projections_file, index=False)
    result["projections_file"] = str(projections_file)

    if n_lineups > 0 and not result["lineups_df"].empty:
        lineup_file = export_root / f"lineups_{resolved_site}_{slate_path.stem}.csv"
        result["lineups_df"].to_csv(lineup_file, index=False)
        result["lineups_file"] = str(lineup_file)

    if "simulation_df" in result and not result["simulation_df"].empty:
        sim_file = export_root / f"simulation_{resolved_site}_{slate_path.stem}.csv"
        result["simulation_df"].to_csv(sim_file, index=False)
        result["simulation_file"] = str(sim_file)

    if "exposure_report" in result and not result["exposure_report"].empty:
        exp_file = export_root / f"exposure_{resolved_site}_{slate_path.stem}.csv"
        result["exposure_report"].to_csv(exp_file, index=False)
        result["exposure_file"] = str(exp_file)

    if "stack_report" in result and not result["stack_report"].empty:
        stk_file = export_root / f"stacks_{resolved_site}_{slate_path.stem}.csv"
        result["stack_report"].to_csv(stk_file, index=False)
        result["stack_file"] = str(stk_file)

    if pre_sim and "Sim_Mean" in projections_df.columns:
        pre_sim_file = export_root / f"player_sim_{resolved_site}_{slate_path.stem}.csv"
        projections_df.to_csv(pre_sim_file, index=False)
        result["player_sim_file"] = str(pre_sim_file)

    return result


# ── Phase 5: Coordinator (thin — delegates to helpers above) ────────────────

def run_dfs_pipeline(
    slate_file_path: str,
    context: ProjectionContext,
    n_lineups: int = 0,
    n_sims: int = 0,
    sim_seed: int | None = None,
    correlation: str = "team",
    pre_sim: bool = False,
    pre_sim_sims: int = 300,
    export_dir: str | None = None,
    apply_filter: bool = True,
    pool_filter_cfg: PoolFilterConfig | None = None,
    contest_cfg: ContestConfig | None = None,
    exposure_cfg: ExposureConfig | None = None,
    num_unique: int = 2,
    skip_injury_refresh: bool = False,
) -> dict[str, Any]:
    """Single orchestrator entry point.

    ingestion -> normalize -> project -> pool_filter -> leverage -> optimize -> simulate -> contest_ev -> exposure_report -> export
    """
    slate_path = Path(slate_file_path)
    if not slate_path.exists():
        raise FileNotFoundError(f"Slate file not found: {slate_file_path}")

    if context.sport.upper() == "NBA" and not skip_injury_refresh:
        _auto_refresh_injuries()

    normalized_df, resolved_site, resolved_context = _ingest_and_enrich_slate(
        slate_path, context
    )
    projections_df, _proj_cache_hit, _proj_cached_at = _build_projections(normalized_df, resolved_context, slate_path)

    if pre_sim:
        projections_df = _run_pre_sim(projections_df, pre_sim_sims, sim_seed, correlation)

    projections_df = _apply_injury_context(projections_df, context)
    projections_df, _bridge, replacement_boosts, filter_report = _apply_pool_preparation(
        projections_df, context, apply_filter, pool_filter_cfg
    )
    projections_df, ownership_model_used = _enrich_props_and_ownership(
        projections_df, resolved_site, context
    )
    projections_df = _compute_value_and_quality(projections_df)
    projections_df = _apply_projection_overrides(projections_df, context)

    result = _build_initial_result(
        projections_df, resolved_site, context, filter_report,
        ownership_model_used, replacement_boosts, _bridge,
        cache_hit=_proj_cache_hit, cached_at_iso=_proj_cached_at,
    )

    if n_lineups > 0:
        result = _generate_lineups(
            result, projections_df, normalized_df, resolved_site,
            context, n_lineups, num_unique, exposure_cfg, contest_cfg,
        )

    if n_sims > 0 and not result["lineups_df"].empty:
        result = _run_simulation(
            result, result["projections_df"], n_sims, sim_seed,
            correlation, contest_cfg, resolved_site,
        )

    if export_dir:
        result = _export_results(
            result, export_dir, resolved_site, slate_path, n_lineups, pre_sim,
        )

    return result
