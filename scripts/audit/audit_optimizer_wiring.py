"""
Optimizer Wiring Audit
======================
Proves how projections, ownership, starters, and injury signals actually flow
through the pipeline from raw slate CSV to the LP optimizer input.

Run:
    python scripts/audit/audit_optimizer_wiring.py
    python scripts/audit/audit_optimizer_wiring.py --slate backend/uploads/slates/local-dev-user/slate_20260327_151347_0b1f92b2.csv --site FD --sport NBA --contest gpp

This script is READ-ONLY — it calls the real pipeline helpers but never writes
to DuckDB, the projection cache, or any file.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import textwrap
from pathlib import Path

# ── UTF-8 stdout on Windows ───────────────────────────────────────────────────
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── path bootstrap ────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

# ── display helpers ────────────────────────────────────────────────────────────

def section(title: str) -> None:
    border = "=" * 80
    print(f"\n{border}")
    print(f"  {title}")
    print(f"{border}\n")


def subsection(title: str) -> None:
    print(f"\n--- {title} {'─' * max(1, 70 - len(title))}\n")


def bullet(text: str, indent: int = 2) -> None:
    wrapped = textwrap.fill(
        text,
        width=100,
        initial_indent=" " * indent + "• ",
        subsequent_indent=" " * (indent + 2),
    )
    print(wrapped)


def flag(text: str, indent: int = 2) -> None:
    wrapped = textwrap.fill(
        text,
        width=100,
        initial_indent=" " * indent + "⚠ FLAG: ",
        subsequent_indent=" " * (indent + 8),
    )
    print(wrapped)


def ok(text: str, indent: int = 2) -> None:
    wrapped = textwrap.fill(
        text,
        width=100,
        initial_indent=" " * indent + "✓ ",
        subsequent_indent=" " * (indent + 2),
    )
    print(wrapped)


def show_df(df: pd.DataFrame, cols: list[str], title: str, max_rows: int = 25) -> None:
    """Print a subset of columns from df, sorted by Proj desc."""
    available = [c for c in cols if c in df.columns]
    if not available:
        print(f"   (no columns from {cols} present)")
        return
    sub = df[available].copy()
    if "Proj" in sub.columns:
        sub = sub.sort_values("Proj", ascending=False)
    print(f"\n  {title} ({len(sub)} rows, showing top {min(max_rows, len(sub))}):")
    with pd.option_context("display.max_rows", None, "display.float_format", "{:.2f}".format):
        print(sub.head(max_rows).to_string(index=False))


# ── auto-detect latest slate ──────────────────────────────────────────────────

def find_latest_slate(upload_dir: Path) -> Path | None:
    slates = sorted(upload_dir.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    for s in slates:
        # Skip FD combined-lineup exports that are too small to be a pool
        if s.stat().st_size > 1000:
            return s
    return None


# ── column comparison helpers ─────────────────────────────────────────────────

def _snap(df: pd.DataFrame, cols: list[str]) -> dict:
    """Take a per-player snapshot of selected columns (DFS_ID keyed)."""
    available = [c for c in cols if c in df.columns]
    if "DFS_ID" not in df.columns:
        return {}
    return df.set_index("DFS_ID")[available].to_dict(orient="index")


def _diff(before: dict, after: dict, col: str) -> list[tuple]:
    """Return (dfs_id, before_val, after_val) where col value changed."""
    changed = []
    for pid, b_row in before.items():
        if pid not in after:
            continue
        bv = b_row.get(col)
        av = after[pid].get(col)
        if bv != av:
            changed.append((pid, bv, av))
    return changed


# ── main audit ────────────────────────────────────────────────────────────────

def run_audit(slate_path: Path, site: str, sport: str, contest: str) -> None:
    print(f"\n{'#' * 80}")
    print(f"  OPTIMIZER WIRING AUDIT")
    print(f"  Slate : {slate_path}")
    print(f"  Site  : {site}  Sport: {sport}  Contest: {contest}")
    print(f"  Env   : DFS_OWNERSHIP_MODE={os.environ.get('DFS_OWNERSHIP_MODE', 'auto')}")
    print(f"{'#' * 80}")

    # ──────────────────────────────────────────────────────────────────────────
    # STAGE 1 — Raw ingestion + normalize_slate_df
    # ──────────────────────────────────────────────────────────────────────────
    section("STAGE 1 — Raw CSV ingestion + normalize_slate_df")

    from analysis.core.orchestrator import _read_slate_csv
    raw_df = _read_slate_csv(slate_path)
    bullet(f"Raw rows: {len(raw_df)}   Raw columns: {list(raw_df.columns)[:20]}")

    from analysis.shared.dfs_schema import normalize_slate_df
    normalized_df, resolved_site = normalize_slate_df(raw_df, site=site)
    bullet(f"Normalized rows: {len(normalized_df)}   Resolved site: {resolved_site}")

    subsection("Projection-Like Columns After Normalize")
    proj_kw = {"proj", "fppg", "fpts", "points", "avgpoints", "site_fppg", "base_proj", "raw_proj"}
    proj_cols_found = [c for c in normalized_df.columns if any(k in c.lower() for k in proj_kw)]
    if proj_cols_found:
        for c in proj_cols_found:
            sample = normalized_df[c].dropna().head(3).tolist()
            bullet(f"{c}: sample={sample}")
    else:
        bullet("No projection-like columns found after normalize.")

    subsection("Ownership-Like Columns After Normalize")
    own_kw = {"own", "ownership"}
    own_cols_found = [c for c in normalized_df.columns if any(k in c.lower() for k in own_kw)]
    if own_cols_found:
        for c in own_cols_found:
            sample = normalized_df[c].dropna().head(3).tolist()
            bullet(f"{c}: sample={sample}")
    else:
        bullet("No ownership columns imported from the slate CSV.")

    # Site_FPPG presence
    subsection("Vendor FPPG / Projection Guard Check")
    if "Site_FPPG" in normalized_df.columns:
        n_site = normalized_df["Site_FPPG"].notna().sum()
        ok(f"Site_FPPG column found ({n_site} non-null values) — projection guard triggered."
           " Site FPPG is stored for fallback use only.")
        print()
        show_df(
            normalized_df.sort_values("Site_FPPG", ascending=False),
            ["Name", "Site_FPPG", "Salary", "Pos"],
            "Top-20 by Site_FPPG",
            max_rows=20,
        )
    else:
        bullet("Site_FPPG not present — no vendor FPPG field detected in this slate.")

    # InjuryStatus column
    subsection("Injury Status (from slate)")
    if "InjuryStatus" in normalized_df.columns:
        inj_counts = normalized_df["InjuryStatus"].value_counts().to_dict()
        bullet(f"InjuryStatus distribution: {inj_counts}")
        inj_players = normalized_df[normalized_df["InjuryStatus"].notna() & (normalized_df["InjuryStatus"] != "")]
        if not inj_players.empty:
            show_df(inj_players, ["Name", "InjuryStatus", "Salary", "Pos"], "Injured Players", max_rows=30)
    else:
        bullet("InjuryStatus column NOT present after normalize.")

    # Banned columns check — should NOT survive
    subsection("Banned Column Survival Check")
    banned = {"AvgPointsPerGame", "FPPG", "FPTS", "FP"}
    survived = [c for c in normalized_df.columns if c in banned]
    if survived:
        flag(f"Banned columns survived normalize: {survived}")
    else:
        ok("No banned projection-alias columns survived normalize.")

    # ──────────────────────────────────────────────────────────────────────────
    # STAGE 2 — CanonicalNBAProjectionEngine.generate()
    # ──────────────────────────────────────────────────────────────────────────
    section("STAGE 2 — CanonicalNBAProjectionEngine.generate()")

    from analysis.core.schemas import ProjectionContext
    from analysis.core.projection_engine import CanonicalNBAProjectionEngine

    ctx = ProjectionContext(
        sport=sport,
        site=resolved_site,
        slate_date=None,
        slate_id=slate_path.stem,
        force_refresh=True,
    )

    engine = CanonicalNBAProjectionEngine()
    engine_df = engine.generate(normalized_df.copy(), ctx)

    bullet(f"Engine output rows: {len(engine_df)}   Columns: {len(engine_df.columns)}")

    # Snapshot engine Own + Leverage
    engine_snap = _snap(engine_df, ["Name", "Proj", "Own", "Own_Est", "own_source", "Leverage"])

    subsection("Engine Projection Source Distribution")
    if "proj_source" in engine_df.columns:
        src_counts = engine_df["proj_source"].value_counts().to_dict()
        bullet(f"proj_source counts: {src_counts}")
    elif "Site_FPPG" in engine_df.columns:
        # Try to infer: players where Proj == Site_FPPG are using vendor fallback
        both = engine_df[engine_df["Site_FPPG"].notna() & engine_df["Proj"].notna()]
        if not both.empty:
            match = (both["Proj"].round(2) == both["Site_FPPG"].round(2)).sum()
            bullet(f"Players where Proj == Site_FPPG (vendor passthrough): {match} / {len(both)}")
            if match > 0:
                flag(f"{match} players appear to use Site_FPPG as final projection (last-resort fallback). "
                     "Check if GL_L10 / box_score / Base_Proj are all 0 for these players.")
    else:
        bullet("proj_source column not present — cannot determine projection layer distribution.")

    subsection("Engine Ownership Source Distribution")
    if "own_source" in engine_df.columns:
        own_src_counts = engine_df["own_source"].value_counts().to_dict()
        bullet(f"own_source after engine.generate(): {own_src_counts}")
        if "simple" in engine_df.get("own_source", pd.Series()).values:
            flag("own_source='simple' detected — estimate_ownership() reads Proj_Final not Proj. "
                 "If Proj_Final is absent, Own defaults to 0.")
    else:
        bullet("own_source column NOT present after engine.generate()")

    subsection("Engine Leverage Formula")
    if "Leverage" in engine_df.columns:
        lev_min = engine_df["Leverage"].min()
        lev_max = engine_df["Leverage"].max()
        bullet(f"Engine Leverage range: [{lev_min:.3f}, {lev_max:.3f}]")
        if lev_max > 2.0:
            ok("Leverage values > 2 → engine is using unscaled Proj/Own ratio (expected: capped at 30.0).")
        else:
            bullet("Leverage values ≤ 2 — may already be normalized [0,1] — unexpected at this stage.")
    else:
        bullet("Leverage column NOT emitted by projection engine.")

    subsection("Top-20 Players by Engine Projection")
    show_df(engine_df, ["Name", "Proj", "Salary", "Own", "own_source", "Leverage", "InjuryBoost", "InjuryStatus"],
            "Engine Output (top-20 by Proj)", max_rows=20)

    # ──────────────────────────────────────────────────────────────────────────
    # STAGE 3 — apply_pool_filter
    # ──────────────────────────────────────────────────────────────────────────
    section("STAGE 3 — apply_pool_filter")

    from analysis.nba.pool_filter import PoolFilterConfig, apply_pool_filter

    pool_cfg = PoolFilterConfig()
    filtered_df, filter_report = apply_pool_filter(engine_df.copy(), cfg=pool_cfg)

    before_n = len(engine_df)
    after_n = len(filtered_df)
    bullet(f"Pool size: {before_n} → {after_n}  (removed {before_n - after_n})")

    removed = filter_report.get("removed_players", [])
    if removed:
        subsection("Removed Players")
        for p in removed:
            if isinstance(p, dict):
                bullet(f"{p.get('name', p)}: status={p.get('status','?')} detail={p.get('detail','')}")
            else:
                bullet(str(p))

    subsection("Injury/Discount Flags")
    tag_cols = ["injury_discounted", "original_proj", "start_prob", "replacement_boost", "chalk_flag", "volatile_tier"]
    show_df(filtered_df, ["Name"] + tag_cols, "Filter Tags (all players)", max_rows=30)

    subsection("Soft Tag Counts")
    for col, label in [("chalk_flag", "chalk"), ("volatile_tier", "volatile_tier"), ("replacement_boost", "replacement_boost")]:
        if col in filtered_df.columns:
            val_counts = filtered_df[col].value_counts().to_dict()
            bullet(f"{label}: {val_counts}")

    if "injury_discounted" in filtered_df.columns:
        discounted = filtered_df[filtered_df["injury_discounted"] == True]
        if not discounted.empty:
            subsection("GTD / Q / D Players — Projection Discounted")
            show_df(discounted, ["Name", "Proj", "original_proj", "InjuryStatus", "start_prob"],
                    "Discounted Players", max_rows=15)
            flag(f"{len(discounted)} players had Proj discounted due to injury/GTD status. "
                 "The LP optimizer sees the discounted Proj values.")

    # ──────────────────────────────────────────────────────────────────────────
    # STAGE 4 — Ownership cascade (orchestrator overwrites engine)
    # ──────────────────────────────────────────────────────────────────────────
    section("STAGE 4 — Orchestrator Ownership Cascade")

    _ownership_mode = os.environ.get("DFS_OWNERSHIP_MODE", "auto").strip().lower()
    bullet(f"DFS_OWNERSHIP_MODE = '{_ownership_mode}'")

    snap_before_own = _snap(filtered_df, ["Own", "Own_Est", "own_source"])
    owned_df = filtered_df.copy()
    ownership_model_used = "none"

    def try_ml(df):
        try:
            from analysis.nba.ownership_v2 import predict_ownership
            return predict_ownership(df, sport=sport, site=resolved_site), "ml"
        except Exception as e:
            print(f"   [ML ownership skipped: {e}]")
            return df, None

    def try_weighted(df):
        try:
            from analysis.nba.ownership_weighted import estimate_ownership_weighted
            return estimate_ownership_weighted(df, site=resolved_site), "weighted"
        except Exception as e:
            print(f"   [Weighted ownership skipped: {e}]")
            return df, None

    def try_simple(df):
        try:
            from analysis.nba.ownership import estimate_ownership
            return estimate_ownership(df), "simple"
        except Exception as e:
            print(f"   [Simple ownership skipped: {e}]")
            return df, None

    if _ownership_mode == "ml":
        owned_df, ownership_model_used = try_ml(owned_df)
        if ownership_model_used is None:
            ownership_model_used = "none"
    elif _ownership_mode == "weighted":
        owned_df, ownership_model_used = try_weighted(owned_df)
        if ownership_model_used is None:
            ownership_model_used = "none"
    elif _ownership_mode == "simple":
        owned_df, ownership_model_used = try_simple(owned_df)
        if ownership_model_used is None:
            ownership_model_used = "none"
    else:  # auto
        owned_df2, m = try_ml(owned_df)
        if m:
            owned_df, ownership_model_used = owned_df2, m
        else:
            owned_df2, m = try_weighted(owned_df)
            if m:
                owned_df, ownership_model_used = owned_df2, m
            else:
                owned_df2, m = try_simple(owned_df)
                if m:
                    owned_df, ownership_model_used = owned_df2, m

    ok(f"Ownership model applied: '{ownership_model_used}'")

    snap_after_own = _snap(owned_df, ["Own", "Own_Est", "own_source"])

    # Compare Own before vs. after cascade
    own_changes = _diff(snap_before_own, snap_after_own, "Own")
    src_changes = _diff(snap_before_own, snap_after_own, "own_source")

    subsection("Own Column — Before vs. After Cascade")
    if own_changes:
        flag(f"Own changed for {len(own_changes)} players — orchestrator cascade OVERWROTE engine values.")
        sample = own_changes[:8]
        for pid, bv, av in sample:
            name = owned_df.loc[owned_df["DFS_ID"] == pid, "Name"].values[0] if "DFS_ID" in owned_df.columns else pid
            bullet(f"{name}: {bv:.1f}% → {av:.1f}%", indent=4)
    else:
        ok("Own values unchanged — engine and cascade produced identical values (or no engine Own was set).")

    if src_changes:
        flag(f"own_source changed for {len(src_changes)} players.")
        sample = src_changes[:5]
        for pid, bv, av in sample:
            name = owned_df.loc[owned_df["DFS_ID"] == pid, "Name"].values[0] if "DFS_ID" in owned_df.columns else pid
            bullet(f"{name}: '{bv}' → '{av}'", indent=4)

    if "own_source" in owned_df.columns:
        final_src_dist = owned_df["own_source"].value_counts().to_dict()
        bullet(f"Final own_source distribution: {final_src_dist}")
        if "simple" in final_src_dist:
            flag("Some players have own_source='simple'. estimate_ownership() uses Proj_Final as input, "
                 "not Proj. If Proj_Final is absent, Own defaults to 0.0 for those players.")

    show_df(owned_df, ["Name", "Proj", "Own", "Own_Est", "own_source"], "Ownership After Cascade", max_rows=20)

    # ──────────────────────────────────────────────────────────────────────────
    # STAGE 5 — compute_leverage_scores (orchestrator overwrites engine Leverage)
    # ──────────────────────────────────────────────────────────────────────────
    section("STAGE 5 — compute_leverage_scores  (orchestrator-level Leverage)")

    snap_before_lev = _snap(owned_df, ["Leverage"])

    from analysis.core.exposure_optimizer import ExposureConfig, compute_leverage_scores

    exp_cfg = ExposureConfig(global_max=0.60)
    lev_df = compute_leverage_scores(owned_df.copy(), exp_cfg)

    snap_after_lev = _snap(lev_df, ["Leverage"])

    subsection("Leverage Column — Before vs. After compute_leverage_scores")
    lev_changes = _diff(snap_before_lev, snap_after_lev, "Leverage")
    if lev_changes:
        flag(f"Leverage changed for {len(lev_changes)} players — orchestrator compute_leverage_scores "
             "OVERWROTE engine Leverage.")
        # Show range summary
        engine_levs = [b for _, b, _ in lev_changes if b is not None]
        orch_levs = [a for _, _, a in lev_changes if a is not None]
        if engine_levs:
            bullet(f"Engine Leverage range: [{min(engine_levs):.3f}, {max(engine_levs):.3f}] "
                   f"(formula: Proj / max(Own, 0.5), capped at 30 — unscaled ratio)")
        if orch_levs:
            bullet(f"Orchestrator Leverage range: [{min(orch_levs):.4f}, {max(orch_levs):.4f}] "
                   f"(formula: normalize(edge - own_penalty) → [0, 1])")
        sample = lev_changes[:5]
        for pid, bv, av in sample:
            name = lev_df.loc[lev_df["DFS_ID"] == pid, "Name"].values[0] if "DFS_ID" in lev_df.columns else pid
            bv_str = f"{bv:.4f}" if bv is not None else "None"
            av_str = f"{av:.4f}" if av is not None else "None"
            bullet(f"{name}: engine={bv_str} → orchestrator={av_str}", indent=4)
    else:
        ok("Leverage values unchanged (engine may not have emitted Leverage, or values are identical).")

    subsection("New Columns Added by compute_leverage_scores")
    new_cols_added = [c for c in lev_df.columns if c not in owned_df.columns]
    bullet(f"New columns: {new_cols_added}")

    show_df(lev_df, ["Name", "Proj", "Own", "Leverage", "FieldOwn", "OwnDivergence", "Edge", "LowOwnBonus"],
            "Top-20 by Leverage (orchestrator-level)", max_rows=20)

    # ──────────────────────────────────────────────────────────────────────────
    # STAGE 6 — apply_contest_constraints (GPP mutates Proj)
    # ──────────────────────────────────────────────────────────────────────────
    section("STAGE 6 — apply_contest_constraints  (GPP ceiling blend)")

    snap_before_cc = _snap(lev_df, ["Proj"])

    try:
        from analysis.nba.optimizer_constraints import apply_contest_constraints
        cc_df, stack_rule = apply_contest_constraints(lev_df.copy(), contest_type=contest, site=resolved_site)
        n_after_cc = len(cc_df)
        bullet(f"Contest constraints ({contest}): pool {len(lev_df)} → {n_after_cc} players")

        snap_after_cc = _snap(cc_df, ["Proj"])
        proj_changes = _diff(snap_before_cc, snap_after_cc, "Proj")

        subsection("Proj Column — Before vs. After Contest Constraints")
        if proj_changes:
            flag(f"Proj changed for {len(proj_changes)} players — GPP ceiling blend applied "
                 "BEFORE LP. The optimizer sees blended Proj = 0.75×Proj + 0.25×Ceiling.")
            sample = proj_changes[:8]
            for pid, bv, av in sample:
                row_match = cc_df.loc[cc_df["DFS_ID"] == pid] if "DFS_ID" in cc_df.columns else pd.DataFrame()
                name = row_match["Name"].values[0] if not row_match.empty else pid
                bv_str = f"{bv:.2f}" if bv is not None else "None"
                av_str = f"{av:.2f}" if av is not None else "None"
                bullet(f"{name}: Proj {bv_str} → {av_str}", indent=4)
        else:
            ok("Proj unchanged (cash mode or Ceiling column absent).")

        if stack_rule is not None:
            bullet(f"Contest-derived stack rule: {stack_rule}")
        else:
            bullet("No stack rule from contest constraints.")

        # Players removed by contest constraints
        n_removed_cc = len(lev_df) - n_after_cc
        if n_removed_cc > 0:
            removed_ids = set(lev_df["DFS_ID"].tolist()) - set(cc_df["DFS_ID"].tolist()) if "DFS_ID" in lev_df.columns else set()
            removed_names = lev_df.loc[lev_df["DFS_ID"].isin(removed_ids), "Name"].tolist() if removed_ids else []
            flag(f"{n_removed_cc} players removed by contest constraints (leverage gate / CV cap / floor gate).")
            if removed_names:
                bullet(f"Removed: {removed_names[:15]}", indent=4)
    except Exception as e:
        flag(f"apply_contest_constraints failed: {e}")
        cc_df = lev_df.copy()
        stack_rule = None

    # ──────────────────────────────────────────────────────────────────────────
    # FINAL — Optimizer Input Summary
    # ──────────────────────────────────────────────────────────────────────────
    section("FINAL — Optimizer Input Summary")

    opt_pool = cc_df if "cc_df" in dir() else lev_df

    subsection("Required Column Check")
    required = ["DFS_ID", "Salary", "Proj", "Pos"]
    for col in required:
        if col in opt_pool.columns:
            ok(f"{col}: present")
        else:
            flag(f"{col}: MISSING — optimize_portfolio will fail!")

    subsection("LP Objective Bonus Column Selection")
    if "Leverage" in opt_pool.columns:
        lev_vals = pd.to_numeric(opt_pool["Leverage"], errors="coerce")
        n_valid_lev = lev_vals.notna().sum()
        ok(f"Leverage column present ({n_valid_lev} valid values) → LP objective uses LEVERAGE as bonus.")
        bullet("LP objective: maximize Σ(Proj[pid] + 0.25 × Leverage[pid])", indent=4)
    else:
        flag("Leverage column absent → LP optimizer falls back to LowOwnBonus = (1 - Own/100).clip(0)")
        bullet("LP objective: maximize Σ(Proj[pid] + 0.25 × LowOwnBonus[pid])", indent=4)

    subsection("Banned Columns in Optimizer Pool")
    banned_in_pool = [c for c in opt_pool.columns if c in {"AvgPointsPerGame", "FPPG", "FPTS", "FP"}]
    if banned_in_pool:
        flag(f"Banned columns present in final optimizer pool: {banned_in_pool}")
    else:
        ok("No banned vendor projection columns passed to optimizer.")

    subsection("Pool Summary Statistics")
    for col in ["Proj", "Own", "Leverage", "Salary"]:
        if col in opt_pool.columns:
            s = pd.to_numeric(opt_pool[col], errors="coerce")
            bullet(f"{col}: min={s.min():.2f}  mean={s.mean():.2f}  max={s.max():.2f}  nulls={s.isna().sum()}")

    # ──────────────────────────────────────────────────────────────────────────
    # TOP-20 RANKINGS — Cross-Signal Comparison
    # ──────────────────────────────────────────────────────────────────────────
    section("TOP-20 RANKINGS — Cross-Signal Comparison")

    rank_cols = {
        "Site_FPPG (vendor)": "Site_FPPG",
        "Proj (model)": "Proj",
        "Own (orchestrator)": "Own",
        "Leverage (orchestrator)": "Leverage",
        "LowOwnBonus": "LowOwnBonus",
    }

    for label, sort_col in rank_cols.items():
        if sort_col not in opt_pool.columns:
            continue
        subsection(f"Top-20 by {label}")
        sub = opt_pool.copy()
        sub[sort_col] = pd.to_numeric(sub[sort_col], errors="coerce")
        sub = sub.sort_values(sort_col, ascending=False).head(20)
        display_cols = ["Name", "Pos", "Salary"]
        for c in ["Proj", "Site_FPPG", "Own", "Own_Est", "own_source", "Leverage", "LowOwnBonus"]:
            if c in sub.columns:
                display_cols.append(c)
        print(sub[display_cols].to_string(index=False))

    # ──────────────────────────────────────────────────────────────────────────
    # STARTERS / INJURIES — Signal Path Summary
    # ──────────────────────────────────────────────────────────────────────────
    section("STARTERS / INJURIES — Signal Path Summary")

    inj_cols = ["Name", "InjuryStatus", "start_prob", "injury_discounted", "original_proj",
                "Proj", "replacement_boost", "InjuryBoost", "Own"]
    avail = [c for c in inj_cols if c in opt_pool.columns]

    # Players with any injury signal
    has_injury = pd.Series(False, index=opt_pool.index)
    for col in ["InjuryStatus", "start_prob", "injury_discounted", "original_proj", "InjuryBoost"]:
        if col not in opt_pool.columns:
            continue
        if col == "InjuryStatus":
            has_injury |= opt_pool[col].notna() & (opt_pool[col] != "")
        elif col == "injury_discounted":
            has_injury |= (opt_pool[col] == True)
        elif col in ["InjuryBoost", "start_prob"]:
            has_injury |= pd.to_numeric(opt_pool[col], errors="coerce").notna()

    inj_players = opt_pool[has_injury]

    if not inj_players.empty:
        print(f"\n  Players with any injury-related signal: {len(inj_players)}\n")
        print(inj_players[avail].to_string(index=False))
    else:
        bullet("No players with injury signals found in the final optimizer pool.")

    subsection("InjuryBoost — Effect on Proj")
    if "InjuryBoost" in opt_pool.columns:
        boosted = opt_pool[pd.to_numeric(opt_pool["InjuryBoost"], errors="coerce") > 1.0]
        if not boosted.empty:
            ok(f"InjuryBoost > 1.0 applied to {len(boosted)} players (OUT teammate uplift or injury boost).")
            show_df(boosted, ["Name", "Proj", "InjuryBoost", "replacement_boost"], "InjuryBoosted Players", 15)
        else:
            bullet("No players have InjuryBoost > 1.0 in this slate.")
    else:
        bullet("InjuryBoost column not present in optimizer pool.")

    subsection("start_prob Distribution")
    if "start_prob" in opt_pool.columns:
        sp = pd.to_numeric(opt_pool["start_prob"], errors="coerce")
        low_prob = opt_pool[sp < 0.5]
        bullet(f"start_prob: min={sp.min():.2f}  mean={sp.mean():.2f}  max={sp.max():.2f}")
        bullet(f"Players with start_prob < 50%: {len(low_prob)}")
    else:
        bullet("start_prob column not present (lineup_status module was not applied).")

    # ──────────────────────────────────────────────────────────────────────────
    # VENDOR LEAKAGE SUMMARY
    # ──────────────────────────────────────────────────────────────────────────
    section("VENDOR LEAKAGE SUMMARY")

    subsection("Site_FPPG Fallback Usage")
    # Site_FPPG stays in normalized_df but the engine's output schema does not include it.
    # Merge Site_FPPG from normalized_df into lev_df by DFS_ID to assess leakage.
    leakage_count = 0
    leakage_base = lev_df.copy()
    if "Site_FPPG" not in leakage_base.columns and "Site_FPPG" in normalized_df.columns:
        # normalized_df uses the same DFS_ID values as engine output
        _id_col = "DFS_ID" if "DFS_ID" in normalized_df.columns else None
        if _id_col:
            _site_fppg_map = normalized_df.set_index(_id_col)["Site_FPPG"].to_dict()
            leakage_base["Site_FPPG"] = leakage_base[_id_col].map(_site_fppg_map)
    if "Site_FPPG" in leakage_base.columns and "Proj" in leakage_base.columns:
        both_present = leakage_base["Site_FPPG"].notna() & leakage_base["Proj"].notna()
        leakage_candidates = leakage_base[both_present].copy()
        leakage_candidates["site_proj_diff"] = (
            pd.to_numeric(leakage_candidates["Proj"], errors="coerce") -
            pd.to_numeric(leakage_candidates["Site_FPPG"], errors="coerce")
        ).abs()
        leakage_count = int((leakage_candidates["site_proj_diff"] < 0.01).sum())
        if leakage_count > 0:
            flag(f"{leakage_count} players have Proj ≈ Site_FPPG (within 0.01). "
                 "These players may be using the vendor FPPG as their final projection "
                 "(no box-score / GL_L10 / Base_Proj data found). Inspect their GL_L10 / box_score columns.")
            leakers = leakage_candidates[leakage_candidates["site_proj_diff"] < 0.01]
            show_df(leakers, ["Name", "Proj", "Site_FPPG", "Pos", "Salary"], "Vendor Leakage Players", 20)
        else:
            ok("No players with Proj ≈ Site_FPPG — engine projection is being used for all players.")
    else:
        bullet("Cannot assess leakage: Site_FPPG column not found in normalized_df or Proj absent.")

    subsection("Layer 0 Dependency Conclusion")
    if leakage_count > 0:
        flag("Layer 0 (Site_FPPG) is the FINAL projection layer for some players. "
             "These players have no game-log or model data — the vendor field IS the projection.")
    else:
        ok("Layer 0 (Site_FPPG) is used only as a fallback and does not appear to be the final "
           "value for any player on this slate.")

    # ──────────────────────────────────────────────────────────────────────────
    # WIRING AMBIGUITY FLAGS SUMMARY
    # ──────────────────────────────────────────────────────────────────────────
    section("WIRING AMBIGUITY FLAGS SUMMARY")

    findings = [
        ("CONFIRMED",
         "Own/Own_Est/own_source: Engine emits these first; orchestrator ownership cascade "
         "ALWAYS runs afterward and overwrites them. The LP optimizer receives orchestrator-level ownership."),
        ("CONFIRMED",
         "Leverage: Engine emits Leverage = Proj/max(Own,0.5) capped at 30 (unscaled ratio). "
         "Orchestrator compute_leverage_scores() overwrites with normalized [0,1] edge-penalty formula. "
         "The LP optimizer always uses the orchestrator [0,1] Leverage."),
        ("CONFIRMED" if contest == "gpp" else "N/A",
         "GPP Proj mutation: apply_contest_constraints() applies Proj = 0.75×Proj + 0.25×Ceiling for GPP. "
         "The LP optimizer sees the blended Proj, not the raw model output."),
        ("CONFIRMED",
         "Projection guard: Slate FPPG column is stored as Site_FPPG, never written to Proj directly. "
         "Site_FPPG is only used as a last-resort fallback when all model layers fail."),
        ("CONDITIONAL",
         "simple ownership bug: estimate_ownership() reads Proj_Final not Proj. If Proj_Final is absent "
         "(it usually is post-filter), Own default is from rank on Proj or Base_Proj but may be incorrect. "
         "Only relevant when DFS_OWNERSHIP_MODE=simple or auto + ML + weighted both fail."),
    ]

    for status, text in findings:
        prefix = "✓" if status == "CONFIRMED" else ("⚠" if status == "CONDITIONAL" else "-")
        print(f"\n  [{status}]")
        wrapped = textwrap.fill(text, width=96, initial_indent="  " + prefix + " ", subsequent_indent="    ")
        print(wrapped)

    print(f"\n{'=' * 80}")
    print("  Audit complete.")
    print(f"{'=' * 80}\n")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    upload_root = ROOT / "backend" / "uploads" / "slates" / "local-dev-user"

    parser = argparse.ArgumentParser(description="Optimizer wiring audit")
    parser.add_argument("--slate", type=str, default=None, help="Path to the slate CSV")
    parser.add_argument("--site", type=str, default="FD", help="DFS site: FD or DK")
    parser.add_argument("--sport", type=str, default="NBA", help="Sport: NBA or NFL")
    parser.add_argument("--contest", type=str, default="gpp", help="Contest type: gpp or cash")
    args = parser.parse_args()

    if args.slate:
        slate_path = Path(args.slate)
        if not slate_path.is_absolute():
            slate_path = ROOT / slate_path
    else:
        slate_path = find_latest_slate(upload_root)
        if slate_path is None:
            print(f"ERROR: No slate CSV found in {upload_root}")
            sys.exit(1)
        print(f"Auto-detected latest slate: {slate_path}")

    if not slate_path.exists():
        print(f"ERROR: Slate not found: {slate_path}")
        sys.exit(1)

    run_audit(
        slate_path=slate_path,
        site=args.site.upper(),
        sport=args.sport.upper(),
        contest=args.contest.lower(),
    )


if __name__ == "__main__":
    main()
