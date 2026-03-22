"""
Ownership Model v2
==================
Calibrated ownership estimation using GradientBoostingRegressor.

Falls back to the legacy percentile-rank model when no trained model file
exists (i.e., before any contest data has been imported).

Training
--------
    from analysis.nba.ownership_v2 import train_ownership_model
    train_ownership_model(sport="NBA", site="DK")

Prediction
----------
    from analysis.nba.ownership_v2 import predict_ownership
    df = predict_ownership(df, sport="NBA", site="DK")

Model columns required in DataFrame
-------------------------------------
  Proj, Salary — always required
  team_total, is_home, replacement_boost — optional (imputed with defaults)
"""

from __future__ import annotations

import logging
import pickle
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = ROOT / "data" / "models"
OWNERSHIP_DB = ROOT / "data" / "ownership_history.duckdb"
MIN_TRAINING_ROWS = 300  # warn if below this
LEAGUE_AVG_TEAM_TOTAL = 112.0


# ── DB helpers ────────────────────────────────────────────────────────────────

def _ensure_ownership_db() -> None:
    """No-op — ownership_history table is now managed by SQLAlchemy in Postgres.
    Kept for call-site compatibility only."""
    pass


def _load_training_data(sport: str, site: str) -> pd.DataFrame:
    """
    Load ownership history for model training, enriched with game-log features.

    Base features come from the postgres ownership_history table (seeded from
    salary files / lineup imports).  Enriched features come from
    dfs_edge.duckdb::player_game_logs:

      l10_avg  — rolling 10-game average DK/FD pts (better proj proxy)
      l10_std  — rolling 10-game std dev (consistency — low σ → higher ownership)
      is_home  — filled in from game_logs where NULL in training rows

    Falls back gracefully if the DB is unavailable or locked.
    """
    import os as _os

    db_url = _os.getenv("DATABASE_URL")
    if not db_url:
        log.warning("DATABASE_URL not set — cannot load ownership training data")
        return pd.DataFrame()

    try:
        from sqlalchemy import create_engine, text as _text

        engine = create_engine(db_url, pool_pre_ping=True)
        with engine.connect() as conn:
            result = conn.execute(
                _text("""
                    SELECT player_name, game_date, actual_own_pct, proj_at_lock,
                           salary, team_total, is_home, contest_type
                    FROM ownership_history
                    WHERE site = :site
                    ORDER BY game_date ASC
                """),
                {"site": site.upper()},
            )
            rows = result.fetchall()
            df = pd.DataFrame(rows, columns=list(result.keys()))
        engine.dispose()
    except Exception as exc:
        log.warning("Could not load ownership training data from Postgres: %s", exc)
        return pd.DataFrame()

    if df.empty:
        return df

    return _enrich_with_game_logs(df, site)


def _enrich_with_game_logs(df: pd.DataFrame, site: str) -> pd.DataFrame:
    """
    Enrich ownership training data with rolling performance features from
    player_game_logs in dfs_edge.duckdb.

    New features added:
      l10_avg  — rolling 10-game average pts (as of slate date)
      l10_std  — rolling 10-game std dev (consistency proxy)
      is_home  — filled from game_logs where training row has NULL

    Uses pandas merge_asof (as-of join) so each ownership row gets the
    most recent rolling stats computed *before* the slate date, avoiding
    any look-ahead bias.
    """
    edge_db = ROOT / "data" / "dfs_edge.duckdb"
    if not edge_db.exists():
        log.debug("dfs_edge.duckdb not found — skipping game-log enrichment")
        df["l10_avg"] = np.nan
        df["l10_std"] = np.nan
        return df

    pts_col = "dk_pts" if site.upper() == "DK" else "fd_pts"

    try:
        # Use the shared get_conn singleton so we share the existing process-level
        # connection rather than opening a competing lock on dfs_edge.duckdb.
        # Falls back to bare duckdb.connect only when running outside the backend.
        _own_conn = False
        try:
            from analysis.shared.db import get_conn as _gc
            edge_con = _gc(edge_db, db_key="dfs_edge", read_only=False)
        except ImportError:
            import duckdb as _ddb
            edge_con = _ddb.connect(str(edge_db), read_only=True)
            _own_conn = True

        gl = edge_con.execute(f"""
            SELECT player_name, game_date, {pts_col} AS pts,
                   CAST(is_home AS FLOAT) AS is_home_gl
            FROM player_game_logs
            WHERE {pts_col} IS NOT NULL
            ORDER BY player_name, game_date
        """).df()
        if _own_conn:
            edge_con.close()
    except Exception as exc:
        log.warning("player_game_logs unavailable for enrichment: %s", exc)
        df["l10_avg"] = np.nan
        df["l10_std"] = np.nan
        return df

    if gl.empty:
        log.debug("player_game_logs is empty — skipping enrichment")
        df["l10_avg"] = np.nan
        df["l10_std"] = np.nan
        return df

    log.info("Enriching %d training rows with %d game-log rows", len(df), len(gl))

    gl["game_date"] = pd.to_datetime(gl["game_date"])
    df["game_date"] = pd.to_datetime(df["game_date"])

    # Rolling L10 average and std dev per player.
    # shift(1) → excludes the game on that day (no look-ahead at lock time)
    gl = gl.sort_values(["player_name", "game_date"])
    gl["l10_avg"] = (
        gl.groupby("player_name")["pts"]
        .transform(lambda x: x.shift(1).rolling(10, min_periods=3).mean())
    )
    gl["l10_std"] = (
        gl.groupby("player_name")["pts"]
        .transform(lambda x: x.shift(1).rolling(10, min_periods=3).std())
    )

    # merge_asof: for each training row (player, slate_date), find the most
    # recent game_log entry on or before that date → backward look-ahead safe
    gl_stats = (
        gl[["player_name", "game_date", "l10_avg", "l10_std", "is_home_gl"]]
        .dropna(subset=["l10_avg"])
        .sort_values("game_date")
    )
    enriched = pd.merge_asof(
        df.sort_values("game_date"),
        gl_stats,
        by="player_name",
        on="game_date",
        direction="backward",
    )

    # Fill is_home from game_logs where training row has NULL
    orig_is_home = pd.to_numeric(
        enriched["is_home"].apply(lambda v: float(v) if not pd.isna(v) else np.nan),
        errors="coerce",
    )
    enriched["is_home"] = orig_is_home.combine_first(
        pd.to_numeric(enriched["is_home_gl"], errors="coerce")
    )
    enriched = enriched.drop(columns=["is_home_gl"], errors="ignore")

    l10_cov = enriched["l10_avg"].notna().mean()
    home_cov = enriched["is_home"].notna().mean()
    log.info(
        "Enrichment coverage — l10_avg: %.0f%% (%d/%d), is_home: %.0f%%",
        l10_cov * 100,
        enriched["l10_avg"].notna().sum(),
        len(enriched),
        home_cov * 100,
    )
    return enriched


def import_lineups_to_history(
    lineup_dir: Optional[str] = None,
    site: str = "DK",
    contest_type: str = "gpp",
    game_date: Optional[str] = None,
) -> dict:
    """
    Import historical contest lineup CSVs into ownership_history.duckdb so the
    GBR ownership model can be trained on real data.

    lineup_dir
        Folder containing past contest export CSVs (default: data/uploads/).
        Each CSV is a set of lineups — the function counts player appearances
        across all lineups in the file and converts them to ownership %.

    site
        "DK" or "FD"

    game_date
        ISO date "YYYY-MM-DD".  If None, today is used for all imported files.

    Returns a summary dict: {status, rows_imported, files_read}
    """
    import os as _os
    from pathlib import Path as _Path
    import re as _re

    uploads = _Path(lineup_dir) if lineup_dir else ROOT / "data" / "uploads"
    if not uploads.exists():
        return {"status": "skipped", "reason": f"Lineup dir not found: {uploads}"}

    csv_files = list(uploads.glob("*.csv"))
    if not csv_files:
        return {"status": "skipped", "reason": "No CSV files found in lineup_dir"}

    ROSTER_KEYWORDS = ["PG", "SG", "SF", "PF", "C", "UTIL", "FLEX", "G", "F"]
    rows_imported = 0
    files_read = 0

    db_url = _os.getenv("DATABASE_URL")
    if not db_url:
        return {"status": "error", "error": "DATABASE_URL not set"}

    try:
        from sqlalchemy import create_engine
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        # Inline import of the ORM model — avoids circular import at module level
        import sys as _sys
        _backend = ROOT / "backend"
        if str(_backend) not in _sys.path:
            _sys.path.insert(0, str(_backend))
        from models.analytics import OwnershipHistory  # type: ignore[import]
        from sqlalchemy.orm import Session

        engine = create_engine(db_url, pool_pre_ping=True)

        for fpath in csv_files:
            try:
                df = pd.read_csv(fpath)
            except Exception:
                continue

            # Detect roster columns
            roster_cols = [c for c in df.columns if any(k in c.upper() for k in ROSTER_KEYWORDS)]
            if not roster_cols:
                continue

            # Parse date from filename (e.g. "lineups_2026-01-15_DK.csv")
            date_match = _re.search(r"(\d{4}-\d{2}-\d{2})", fpath.name)
            row_date = date_match.group(1) if date_match else (game_date or "2026-01-01")

            # Site detection from filename
            row_site = site.upper()
            if "fd" in fpath.name.lower() or "fanduel" in fpath.name.lower():
                row_site = "FD"
            elif "dk" in fpath.name.lower() or "draftkings" in fpath.name.lower():
                row_site = "DK"

            total_lineups = len(df)
            if total_lineups == 0:
                continue

            melted = df.melt(value_vars=roster_cols, value_name="player").dropna(subset=["player"])
            melted["player"] = melted["player"].astype(str).str.strip()

            ownership = (
                melted.groupby("player")
                .size()
                .reset_index(name="appearances")
            )
            ownership["ownership_pct"] = (
                ownership["appearances"] / total_lineups * 100
            ).round(2)

            insert_rows = [
                {
                    "player_name": str(row["player"]),
                    "game_date": row_date,
                    "site": row_site,
                    "slate_id": fpath.stem,
                    "actual_own_pct": float(row["ownership_pct"]),
                    "proj_at_lock": None,
                    "salary": None,
                    "team_total": None,
                    "is_home": None,
                    "contest_type": contest_type,
                    "own_source": "lineup_import",
                }
                for _, row in ownership.iterrows()
            ]

            if insert_rows:
                with Session(engine) as session:
                    stmt = pg_insert(OwnershipHistory).values(insert_rows)
                    stmt = stmt.on_conflict_do_update(
                        constraint="uq_ownership_history",
                        set_={"actual_own_pct": stmt.excluded.actual_own_pct},
                    )
                    session.execute(stmt)
                    session.commit()
                rows_imported += len(insert_rows)
                files_read += 1

        engine.dispose()

    except Exception as exc:
        log.warning("Ownership history import failed: %s", exc)
        return {"status": "error", "error": str(exc)}

    log.info(
        "Ownership history import: %d rows from %d files → Postgres ownership_history",
        rows_imported, files_read,
    )
    return {"status": "ok", "rows_imported": rows_imported, "files_read": files_read}


# ── Feature engineering ──────────────────────────────────────────────────────

# DK slot capacity by canonical position prefix (used for scarcity weighting)
_POS_SLOT_COUNTS: dict[str, int] = {
    "PG": 2, "SG": 2, "SF": 2, "PF": 2, "C": 1,  # DK
    "G": 1, "F": 1, "UTIL": 1,  # DK flex
    "D": 1, "K": 1,  # NFL
}


def _canonical_pos(raw: str) -> str:
    """Return the first slash-delimited position (e.g. 'PG/G' → 'PG')."""
    return str(raw).split("/")[0].strip().upper()


def _fetch_l10_for_predict(player_names: list, site: str) -> dict:
    """
    Return {player_name: {'l10_avg': float, 'l10_std': float}} for the most
    recent 10 games (excluding game-day itself) from player_game_logs.

    Used at prediction time so the model gets the same l10 features it was
    trained on.  HistGBT handles NaN for players with <3 games gracefully.
    """
    edge_db = ROOT / "data" / "dfs_edge.duckdb"
    if not edge_db.exists() or not player_names:
        return {}
    pts_col = "dk_pts" if site.upper() == "DK" else "fd_pts"
    try:
        try:
            from analysis.shared.db import get_conn as _gc
            con = _gc(edge_db, db_key="dfs_edge", read_only=False)
            _own_conn = False
        except ImportError:
            import duckdb as _ddb
            con = _ddb.connect(str(edge_db), read_only=True)
            _own_conn = True

        gl = con.execute(f"""
            SELECT player_name, {pts_col} AS pts
            FROM (
                SELECT player_name, {pts_col},
                       ROW_NUMBER() OVER (
                           PARTITION BY player_name
                           ORDER BY game_date DESC
                       ) AS rn
                FROM player_game_logs
                WHERE {pts_col} IS NOT NULL
                  AND game_date < CURRENT_DATE
            ) t
            WHERE rn <= 10
        """).df()
        if _own_conn:
            con.close()
    except Exception as exc:
        log.debug("_fetch_l10_for_predict: %s", exc)
        return {}

    if gl.empty:
        return {}

    result = {}
    for name, grp in gl.groupby("player_name"):
        vals = grp["pts"].dropna().values
        if len(vals) < 3:
            continue
        result[str(name)] = {
            "l10_avg": float(np.mean(vals)),
            "l10_std": float(np.std(vals, ddof=1)) if len(vals) > 1 else np.nan,
        }
    log.debug("_fetch_l10_for_predict: %d/%d players matched", len(result), len(player_names))
    return result


def _build_features(df: pd.DataFrame, position_groups: Optional[pd.Series] = None) -> pd.DataFrame:
    """Build model features from projections DataFrame."""
    feats = pd.DataFrame(index=df.index)

    proj = pd.to_numeric(df.get("Proj", df.get("proj_at_lock", 0)), errors="coerce").fillna(0.0)
    salary = pd.to_numeric(df.get("Salary", df.get("salary", 5000)), errors="coerce").fillna(5000.0)

    # ── Base features the model was trained on ────────────────────────────
    # These must match the training feature_cols in train_ownership_model()
    feats["proj_at_lock"] = proj
    feats["salary"] = salary
    sal_k = salary / 1000.0
    feats["proj_pct_rank"] = proj.rank(pct=True).fillna(0.5)
    feats["value_score"] = (proj / sal_k.replace(0, 1)).clip(0, 20)
    feats["salary_pct"] = salary.rank(pct=True).fillna(0.5)

    # ── Per-position scarcity features ─────────────────────────────────────
    # Ownership varies dramatically by position — a C at 90th pct is far more
    # chalky than a PG at 90th pct because there's only one C slot vs two PG slots.
    pos_col = next((c for c in ["Pos", "Position", "position", "pos"] if c in df.columns), None)
    if pos_col is not None:
        canonical = df[pos_col].apply(_canonical_pos)
        # Projection percentile rank WITHIN position group
        feats["proj_pct_rank_pos"] = df.groupby(canonical)["Proj"].transform(
            lambda x: pd.to_numeric(x, errors="coerce").fillna(0.0).rank(pct=True)
        ).fillna(0.5) if "Proj" in df.columns else 0.5
        # Slot scarcity: fewer slots for this position → higher ownership at top
        feats["pos_slot_count"] = canonical.map(
            lambda p: _POS_SLOT_COUNTS.get(p, 1)
        ).astype(float)
        # Scarce-position flag (C, K, D) — single-slot positions have compressed ownership
        feats["is_scarce_pos"] = (feats["pos_slot_count"] <= 1).astype(float)
    else:
        feats["proj_pct_rank_pos"] = feats["proj_pct_rank"]
        feats["pos_slot_count"] = 1.0
        feats["is_scarce_pos"] = 0.0

    # ── Context features ───────────────────────────────────────────────────
    # Team total (Vegas)
    feats["team_total"] = pd.to_numeric(
        df.get("team_total", pd.Series(LEAGUE_AVG_TEAM_TOTAL, index=df.index)),
        errors="coerce",
    ).fillna(LEAGUE_AVG_TEAM_TOTAL)

    # Home/away
    feats["is_home"] = pd.to_numeric(
        df.get("is_home", pd.Series(0.5, index=df.index)), errors="coerce"
    ).fillna(0.5)

    # Replacement boost — continuous absorbed-minutes column preferred, binary flag fallback
    abs_min_col = next(
        (c for c in ["absorbed_minutes", "replacement_minutes"] if c in df.columns), None
    )
    if abs_min_col:
        # Normalize absorbed minutes 0-40 → 0-1 importance weight
        feats["has_replacement_boost"] = (
            pd.to_numeric(df[abs_min_col], errors="coerce").fillna(0).clip(0, 40) / 40.0
        )
    else:
        rb_col = next(
            (c for c in ["replacement_boost", "has_replacement_boost"] if c in df.columns), None
        )
        feats["has_replacement_boost"] = (
            pd.to_numeric(df[rb_col], errors="coerce").fillna(0).clip(0, 1)
            if rb_col else 0.0
        )

    # Salary tier (0=min, 1=punt, 2=value, 3=mid, 4=upper-mid, 5=stud)
    bins = [0, 3500, 5000, 6500, 8000, 10000, 99999]
    labels = [0, 1, 2, 3, 4, 5]
    feats["salary_tier"] = pd.cut(salary, bins=bins, labels=labels, right=True).astype(float).fillna(2.0)

    # ── Game-log rolling features (l10_avg, l10_std, l10_cv) ───────────────
    # Look up most recent 10 games per player from dfs_edge.duckdb.
    # NaN for players with no game log history — HistGBT handles this natively.
    name_col = next((c for c in ["Name", "Player", "player_name", "Player Name"] if c in df.columns), None)
    site_hint = getattr(df, "_site", "DK")  # may be set by caller; defaults DK
    # Allow caller to pass site via a df attribute or column; fall back to DK
    if "_site" in df.columns:
        site_hint = str(df["_site"].iloc[0]) if len(df) > 0 else "DK"

    if name_col is not None:
        player_names = df[name_col].tolist()
        l10_lookup = _fetch_l10_for_predict(player_names, site=site_hint)
        feats["l10_avg"] = df[name_col].map(lambda n: l10_lookup.get(str(n), {}).get("l10_avg", np.nan))
        feats["l10_std"] = df[name_col].map(lambda n: l10_lookup.get(str(n), {}).get("l10_std", np.nan))
    else:
        feats["l10_avg"] = np.nan
        feats["l10_std"] = np.nan

    # l10_cv = std/avg — boom-bust proxy (NaN propagates to HistGBT cleanly)
    l10_base = feats["l10_avg"].combine_first(proj)
    feats["l10_cv"] = (feats["l10_std"] / l10_base.clip(1)).clip(0, 2)

    return feats


# ── Model training ────────────────────────────────────────────────────────────

def train_ownership_model(sport: str = "NBA", site: str = "DK") -> Optional[object]:
    """
    Train a HistGradientBoostingRegressor on ownership history.

    HistGBT is 10-20× faster than the classic GradientBoostingRegressor for
    the same accuracy — it uses histogram-binned splits (same design as
    LightGBM) and handles NaN natively, removing the need for SimpleImputer.

    Saves the model to data/models/ownership_{sport}_{site}.pkl.
    Returns the fitted model, or None if insufficient data.
    """
    try:
        from sklearn.ensemble import HistGradientBoostingRegressor
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        log.warning("scikit-learn not installed — ownership model training skipped")
        return None

    data = _load_training_data(sport, site)
    if data.empty:
        log.warning("No ownership training data found for %s/%s", sport, site)
        return None

    if len(data) < MIN_TRAINING_ROWS:
        log.warning(
            "Only %d training rows (< %d minimum) — model may be unreliable",
            len(data), MIN_TRAINING_ROWS,
        )

    # Build features — new game-log features from enrichment:
    #   l10_avg  rolling 10-game avg pts (best projection proxy)
    #   l10_std  rolling 10-game std dev (consistency — low σ → more ownership)
    #   l10_cv   coefficient of variation = l10_std/l10_avg (boom-bust flag)
    feature_cols = ["proj_at_lock", "salary", "team_total", "is_home", "l10_avg", "l10_std"]
    for col in feature_cols:
        if col not in data.columns:
            data[col] = np.nan

    X = data[feature_cols].copy().astype(float)
    # HistGBT handles NaN natively — just need float dtype
    # Use l10_avg as the ranking base when available, else fall back to proj_at_lock
    proj_base = X["l10_avg"].combine_first(X["proj_at_lock"])
    X["proj_pct_rank"] = proj_base.rank(pct=True)
    X["value_score"] = (proj_base / (X["salary"].fillna(5000) / 1000)).clip(0, 20)
    X["salary_pct"] = X["salary"].rank(pct=True)
    X["team_total"] = X["team_total"].fillna(LEAGUE_AVG_TEAM_TOTAL)
    # Consistency: coefficient of variation (std/mean) — lower = more consistent = more owned
    # NaN stays NaN and HistGBT handles it
    X["l10_cv"] = (X["l10_std"] / proj_base.clip(1)).clip(0, 2)

    y = data["actual_own_pct"].clip(0, 80)

    import time as _time
    t0 = _time.perf_counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = Pipeline([
            ("scaler", StandardScaler()),
            ("hgb", HistGradientBoostingRegressor(
                max_iter=200,          # equivalent to n_estimators
                max_depth=4,
                learning_rate=0.05,
                random_state=42,
                early_stopping=False,  # deterministic — no val split overhead
            )),
        ])
        model.fit(X, y)
    elapsed = _time.perf_counter() - t0

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODELS_DIR / f"ownership_{sport.upper()}_{site.upper()}.pkl"
    with open(model_path, "wb") as f:
        pickle.dump(model, f)

    log.info(
        "Ownership model trained on %d rows in %.1fs, saved to %s",
        len(data), elapsed, model_path,
    )
    return model


def _load_model(sport: str, site: str) -> Optional[object]:
    model_path = MODELS_DIR / f"ownership_{sport.upper()}_{site.upper()}.pkl"
    if not model_path.exists():
        return None
    try:
        with open(model_path, "rb") as f:
            model = pickle.load(f)
        log.debug("Loaded ownership model from %s", model_path)
        return model
    except Exception as exc:
        log.warning("Could not load ownership model: %s", exc)
        return None


# ── Prediction ────────────────────────────────────────────────────────────────

# Output range by contest type — GPP has fat ownership tails, cash is compressed
_OWN_RANGES: dict[str, tuple[float, float]] = {
    "gpp":              (0.5, 65.0),
    "tournament":       (0.5, 65.0),
    "double_up":        (5.0, 45.0),
    "cash":             (5.0, 40.0),
    "winner_take_all":  (0.5, 70.0),
}


def predict_ownership(
    df: pd.DataFrame,
    sport: str = "NBA",
    site: str = "DK",
    contest_type: str = "gpp",
) -> pd.DataFrame:
    """
    Add ``Own_Est`` column to a projections DataFrame.

    Uses the calibrated GBR model when available, falls back to the percentile
    rank heuristic.  Ownership output is rescaled to the contest_type range.

    Leverage scoring is intentionally NOT computed here — use
    ``analysis.core.exposure_optimizer.compute_leverage_scores()`` which applies
    the full ExposureConfig parameters consistently across the whole pipeline.

    Parameters
    ----------
    df           : Projections DataFrame (must have Proj and Salary).
    sport        : "NBA" or "NFL"
    site         : "DK" or "FD"
    contest_type : "gpp" | "cash" | "double_up" | "winner_take_all"

    Returns
    -------
    Copy of df with ``Own_Est`` and ``own_source`` ("model" | "fallback") added.
    """
    df = df.copy()
    own_min, own_max = _OWN_RANGES.get(contest_type.lower(), (0.5, 65.0))
    model = _load_model(sport, site)

    if model is not None:
        try:
            feats = _build_features(df)
            # Align feature columns to what the model was trained on
            trained_cols = list(model.feature_names_in_) if hasattr(model, "feature_names_in_") else [
                c for step in model.steps for c in (getattr(step[1], "feature_names_in_", []) or [])
            ]
            if trained_cols:
                for c in trained_cols:
                    if c not in feats.columns:
                        feats[c] = np.nan  # NaN lets HistGBT use its learned split direction
                feats = feats[trained_cols]
            raw_pred = model.predict(feats)
            df["Own_Est"] = np.clip(raw_pred, own_min, own_max)
            df["Own"] = df["Own_Est"]          # primary column read by pipeline & UI
            df["own_source"] = "model"
            log.info(
                "Ownership predicted via calibrated GBR model (n=%d, contest=%s, range=%.0f–%.0f%%)",
                len(df), contest_type, own_min, own_max,
            )
        except Exception as exc:
            log.warning("Ownership model prediction failed (%s) — using fallback", exc)
            df = _fallback_ownership(df, own_min=own_min, own_max=own_max)
            df["Own"] = df["Own_Est"]
            df["own_source"] = "fallback"
    else:
        log.info("No ownership model for %s/%s — using percentile fallback", sport, site)
        df = _fallback_ownership(df, own_min=own_min, own_max=own_max)
        df["Own"] = df["Own_Est"]
        df["own_source"] = "fallback"

    return df


def _fallback_ownership(
    df: pd.DataFrame,
    own_min: float = 0.5,
    own_max: float = 60.0,
) -> pd.DataFrame:
    """
    Percentile-rank fallback.  Respects the contest-type output range so that,
    e.g., cash game ownership estimates are compressed vs GPP.
    """
    proj = pd.to_numeric(df.get("Proj", df.get("Proj_Final", 0)), errors="coerce").fillna(0.0)
    salary = pd.to_numeric(df.get("Salary", 5000), errors="coerce").fillna(5000.0)

    proj_rank = proj.rank(pct=True).fillna(0.5)
    sal_rank = salary.rank(pct=True).fillna(0.5)
    own_score = 0.6 * proj_rank + 0.4 * sal_rank
    max_score = own_score.max()
    if max_score <= 0:
        df["Own_Est"] = own_min
    else:
        scaled = own_score / max_score  # 0→1
        df["Own_Est"] = (scaled * (own_max - own_min) + own_min).round(2)
    df["Own"] = df["Own_Est"]
    return df
