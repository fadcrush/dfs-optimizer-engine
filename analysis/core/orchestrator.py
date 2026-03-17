from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from analysis.nba.optimizer import optimize_portfolio
from analysis.nba.pool_filter import PoolFilterConfig, apply_pool_filter
from analysis.nba.replacement_engine import compute_boosts
from analysis.shared.dfs_schema import normalize_slate_df

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
        import sys as _sys
        # ensure scripts/ is importable
        _scripts = _Path(__file__).resolve().parent.parent.parent / "scripts"
        if str(_scripts) not in _sys.path:
            _sys.path.insert(0, str(_scripts))
        from fetch_nba_injuries import ensure_current, DB_PATH
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
) -> dict[str, Any]:
    """Single orchestrator entry point.

    ingestion -> normalize -> project -> pool_filter -> leverage -> optimize -> simulate -> contest_ev -> exposure_report -> export
    """
    slate_path = Path(slate_file_path)
    if not slate_path.exists():
        raise FileNotFoundError(f"Slate file not found: {slate_file_path}")

    # ── Auto-refresh injuries: check if DB has the latest PDF; fetch only if not ──
    if context.sport.upper() == "NBA":
        _auto_refresh_injuries()

    raw_df = pd.read_csv(slate_path)
    normalized_df, resolved_site = normalize_slate_df(raw_df, site=context.site)

    # ── Vegas enrichment: add team totals / implied pts before projection ────
    if context.sport.upper() == "NBA":
        try:
            from analysis.shared.vegas_enricher import enrich_with_vegas
            normalized_df = enrich_with_vegas(normalized_df, sport="NBA")
            log.info("Vegas enrichment applied — %d players", len(normalized_df))
        except Exception as exc:
            log.warning("Vegas enrichment skipped: %s", exc)

        # ── Lineup status: add start_prob per player ─────────────────────
        try:
            from analysis.nba.lineup_status import add_start_prob
            normalized_df = add_start_prob(normalized_df)
            log.info("Lineup start probabilities added")
        except Exception as exc:
            log.warning("Lineup status skipped: %s", exc)

    resolved_context = context.model_copy(update={"site": resolved_site})

    # ── Select projection engine based on sport ─────────────────────────────
    if context.sport.upper() == "NFL":
        engine: Any = NFLProjectionEngine()
    else:
        engine = CanonicalNBAProjectionEngine()

    # ── Projection cache: skip engine if fresh cache exists ─────────────────
    _cache_key = context.slate_id or slate_path.stem
    projections_df: pd.DataFrame | None = None
    if not context.force_refresh:
        try:
            from analysis.core.projection_cache import get_cache
            _cache = get_cache()
            projections_df = _cache.get(_cache_key, context.sport, resolved_site)
        except Exception as exc:
            log.warning("Projection cache lookup failed: %s", exc)

    if projections_df is None:
        projections_df = engine.generate(normalized_df, resolved_context)
        # Store in cache (non-blocking — failure is tolerated)
        try:
            from analysis.core.projection_cache import get_cache
            get_cache().set(_cache_key, context.sport, resolved_site, projections_df)
        except Exception as exc:
            log.warning("Projection cache write failed: %s", exc)

    # ── Pre-sim (optional volatility context before pool selection) ──────────
    if pre_sim:
        pre_config = SimulationConfig(
            n_sims=pre_sim_sims,
            seed=sim_seed,
            correlation="team" if correlation == "team" else "none",
        )
        player_sims = simulate_player_outcomes(projections_df, pre_config)
        player_summary = summarize_player_sims(projections_df, player_sims)
        if not player_summary.empty:
            projections_df = projections_df.merge(player_summary, on="DFS_ID", how="left")

    # ── Inject explicit injuries from context into projections_df ─────────────
    if context.injuries:
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

    # ── Replacement engine: boost teammates of OUT players ───────────────────
    replacement_boosts: pd.DataFrame | None = None
    if apply_filter:
        out_players: list[str] = [
            name for name, status in context.injuries.items()
            if str(status).upper() in {"OUT", "O"}
        ]
        if out_players:
            log.info("Computing replacement boosts for OUT players: %s", out_players)
            replacement_boosts = compute_boosts(out_players, slate_players=projections_df)

    # ── Pool filter: remove OUT players, tag chalk/volatility ────────────────
    filter_report: dict = {}
    if apply_filter:
        projections_df, filter_report = apply_pool_filter(
            projections_df,
            cfg=pool_filter_cfg,
            replacement_boosts=replacement_boosts,
        )
        log.info("Pool filter report: %s", filter_report)

        # ── Injury summary: print which players were removed + full report ─
        _print_injury_summary(filter_report)

    # ── Player prop lines: enrich projections with market lines ─────────────
    if context.sport.upper() == "NBA":
        try:
            from analysis.shared.props_enricher import enrich_with_props
            projections_df = enrich_with_props(projections_df)
            has_pts_line = int(projections_df.get("prop_pts_line", pd.Series()).notna().sum()) if "prop_pts_line" in projections_df.columns else 0
            log.info("Props enrichment applied — %d players have a points line", has_pts_line)
        except Exception as exc:
            log.warning("Props enrichment skipped: %s", exc)

    # ── Ownership model v2: calibrated ownership estimates ──────────────────
    if context.sport.upper() == "NBA":
        try:
            from analysis.nba.ownership_v2 import predict_ownership
            projections_df = predict_ownership(
                projections_df, sport=context.sport, site=resolved_site
            )
            log.info("Ownership v2 predictions applied")
        except Exception as exc:
            log.warning("Ownership v2 skipped: %s", exc)

    # ── Apply inline projection overrides from context ───────────────────────
    if context.projection_overrides:
        name_col_candidates = ["Name", "Player", "player_name"]
        proj_name_col = next((c for c in name_col_candidates if c in projections_df.columns), None)
        if proj_name_col:
            count = 0
            for player_name, override_val in context.projection_overrides.items():
                mask = projections_df[proj_name_col].str.lower() == player_name.lower()
                if mask.any():
                    projections_df.loc[mask, "Proj"] = float(override_val)
                    count += 1
            log.info("Applied %d projection overrides", count)

    result: dict[str, Any] = {
        "success": True,
        "site": resolved_site,
        "sport": context.sport,
        "projections_df": projections_df,
        "lineups_df": pd.DataFrame(),
        "filter_report": filter_report,
        # Convenience top-level keys for API/notebook consumers
        "removed_players": filter_report.get("removed_players", []),
        "removed_injury_detail": filter_report.get("removed_injury_detail", []),
        "injuries_today": filter_report.get("injuries_today", []),
        "replacement_boosts": replacement_boosts.to_dict("records") if replacement_boosts is not None and not replacement_boosts.empty else [],
        "stats": {
            "total_players": int(len(projections_df)),
            "avg_projection": float(projections_df["Proj"].mean()) if len(projections_df) else 0.0,
        },
    }

    if n_lineups > 0:

        # ── NFL path ──────────────────────────────────────────────────────────
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
            # Convert to a flat DataFrame similar to NBA lineups_df
            nfl_rows = []
            for lu in nfl_lineups:
                row = {"LineupIndex": lu.lineup_id - 1} if hasattr(lu, "lineup_id") else {"LineupIndex": len(nfl_rows)}
                row["TotalSalary"] = lu.total_salary
                row["Proj"] = lu.total_projection
                for sl in lu.slots:
                    row[sl.slot_name] = sl.player_name
                nfl_rows.append(row)
            lineups_df = pd.DataFrame(nfl_rows)
            result["lineups_df"] = lineups_df
            result["stats"]["generated_lineup_rows"] = int(len(lineups_df))
            log.info("NFL optimizer generated %d lineups", len(nfl_lineups))
        else:
            # ── NBA path ──────────────────────────────────────────────────────
            # ── Leverage scoring (optional, uses ExposureConfig) ─────────────
            eff_exp_cfg = exposure_cfg or ExposureConfig(global_max=0.60)
            projections_df = compute_leverage_scores(projections_df, eff_exp_cfg)
            # Propagate enriched df back into result so callers see Leverage column
            result["projections_df"] = projections_df
            per_player_caps, per_player_floors = resolve_per_player_caps(
                projections_df, n_lineups, eff_exp_cfg
            )

            # ── Build stacking rule from context ──────────────────────────────
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
                    "Stacking enabled — min_game_stack=%d, bring_back=%d, max_team=%d, max_game=%d",
                    context.min_game_stack, context.bring_back_count,
                    context.max_from_team, context.max_from_game,
                )

            lineups_df = optimize_portfolio(
                players=projections_df,
                site=resolved_site,
                n_lineups=n_lineups,
                num_unique=num_unique,
                per_player_caps=per_player_caps,
                per_player_floors=per_player_floors,
                locks=list(context.locks),
                fades=list(context.fades),
                stack_rules=stack_rule,
            )
            result["lineups_df"] = lineups_df
            result["stats"]["generated_lineup_rows"] = int(len(lineups_df))

            # ── Exposure report ────────────────────────────────────────────────
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

    if n_sims > 0 and not result["lineups_df"].empty:
        sim_config = SimulationConfig(
            n_sims=n_sims,
            seed=sim_seed,
            correlation="team" if correlation == "team" else "none",
        )
        sim_summary, scores_matrix = simulate_lineup_scores(result["lineups_df"], projections_df, sim_config)

        # ── Contest EV ranking ────────────────────────────────────────────
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

    if export_dir:
        export_root = Path(export_dir)
        export_root.mkdir(parents=True, exist_ok=True)

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
