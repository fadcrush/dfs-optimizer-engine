"""
Analytics Service
=================
Bridges the analysis layer analytics with the backend API.

User-facing tables (contest_results, projection_log, ownership_actuals) live
in **Postgres** alongside the ``users`` table — migrated from dfs_master.duckdb
so every transactional write shares the same DB, enabling proper foreign keys,
multi-worker safety, and real ACID guarantees.

Read-only reference data (game logs, DvP) still lives in DuckDB
(``data/dfs_edge.duckdb``).
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from sqlalchemy import and_, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_EDGE_DB = _ROOT / "data" / "dfs_edge.duckdb"

# Ensure the analysis package is importable from the backend service
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from analysis.shared.db import get_conn as _get_conn
except ImportError:
    import duckdb as _duckdb_mod
    def _get_conn(path, *, db_key=None, read_only=False):  # type: ignore[misc]
        return _duckdb_mod.connect(str(path), read_only=read_only)


def _edge():
    """Return the per-process read-only singleton for dfs_edge.duckdb."""
    return _get_conn(_EDGE_DB, db_key="dfs_edge", read_only=True)


# Import Postgres session factory — may be None if DATABASE_URL is unset.
from database.db import SessionLocal, engine
from models.analytics import ContestResult, ProjectionLog, OwnershipActual, OwnershipHistory


def _pg_session():
    """Create a new Postgres session. Caller must close it."""
    if SessionLocal is None:
        raise RuntimeError(
            "DATABASE_URL not configured — analytics tables require Postgres. "
            "Set DATABASE_URL in .env to enable."
        )
    return SessionLocal()


# ---------------------------------------------------------------------------
# Schema initialisation (called once at service startup)
# ---------------------------------------------------------------------------

def init_analytics_tables() -> None:
    """Create analytics tables in Postgres if they don't exist.

    Uses the same ``Base.metadata`` as the User model so ``create_all``
    picks up ContestResult, ProjectionLog, OwnershipActual.
    """
    if engine is None:
        log.warning("analytics table init skipped — DATABASE_URL not set.")
        return
    try:
        from models.analytics import ContestResult, ProjectionLog, OwnershipActual, OwnershipHistory  # noqa: F811
        from models.user import Base, User
        Base.metadata.create_all(bind=engine)
        log.info("[analytics] Postgres tables ready.")
        # In local-dev / auth-disabled mode seed a synthetic user row so FK
        # writes succeed without a real Supabase auth user.
        import os as _os
        if _os.getenv("DFS_DISABLE_AUTH"):
            _seed_dev_user()
    except Exception as exc:
        log.warning("analytics table init failed: %s", exc)


def _seed_dev_user() -> None:
    """Ensure a placeholder 'local-dev-user' row exists in the users table."""
    from sqlalchemy import text as _text
    sess = SessionLocal()
    try:
        sess.execute(_text("""
            INSERT INTO users (id, email, password_hash, email_verified, tier)
            VALUES ('local-dev-user', 'dev@local.invalid', '', true, 'pro')
            ON CONFLICT (id) DO NOTHING
        """))
        sess.commit()
        log.info("[analytics] Seeded local-dev-user row.")
    except Exception as exc:
        sess.rollback()
        log.warning("[analytics] Could not seed dev user: %s", exc)
    finally:
        sess.close()


def _ensure_user_exists(session, user_id: str) -> None:
    """Insert a minimal user stub if user_id is not in the users table.

    Prevents FK violations when analytics writes happen before the user row
    is fully propagated (e.g. dev mode, race conditions at signup).
    Uses raw SQL with only guaranteed-base columns to survive any schema version.
    """
    from sqlalchemy import text as _text
    stmt = _text("""
        INSERT INTO users (id, email, password_hash, email_verified, tier)
        VALUES (:id, :email, '', false, 'free')
        ON CONFLICT (id) DO NOTHING
    """)
    session.execute(stmt, {"id": user_id, "email": f"{user_id}@placeholder.invalid"})
    session.flush()


# ---------------------------------------------------------------------------
# Contest tracking
# ---------------------------------------------------------------------------

def log_contest_result(
    contest_date: date,
    contest_type: str,
    site: str,
    entry_fee: float,
    payout: float = 0.0,
    final_rank: Optional[int] = None,
    total_entries: Optional[int] = None,
    lineup_proj: Optional[float] = None,
    lineup_actual: Optional[float] = None,
    notes: str = "",
    user_id: str = "",
) -> bool:
    """Write a single contest result row. Returns True on success."""
    session = _pg_session()
    try:
        if user_id:
            _ensure_user_exists(session, user_id)
        row = ContestResult(
            contest_date=contest_date,
            contest_type=contest_type.lower(),
            site=site.upper(),
            entry_fee=float(entry_fee),
            payout=float(payout),
            final_rank=final_rank,
            total_entries=total_entries,
            lineup_proj=lineup_proj,
            lineup_actual=lineup_actual,
            notes=notes,
            user_id=user_id,
        )
        session.add(row)
        session.commit()
        return True
    except Exception as exc:
        session.rollback()
        log.error("log_contest_result failed: %s", exc)
        return False
    finally:
        session.close()


def get_roi_summary(period_days: Optional[int] = None, site: Optional[str] = None, user_id: str = "") -> dict[str, Any]:
    """
    Calculate ROI metrics from stored contest results.

    Returns a dict with keys:
        total_invested, total_won, profit, roi_percentage,
        total_contests, roi_by_type, period_days
    """
    session = _pg_session()
    try:
        q = session.query(ContestResult)

        if user_id:
            q = q.filter(ContestResult.user_id == user_id)
        if period_days:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=period_days)).date()
            q = q.filter(ContestResult.contest_date >= cutoff)
        if site:
            q = q.filter(ContestResult.site == site.upper())

        rows = q.order_by(ContestResult.contest_date.desc()).all()

        if not rows:
            return {
                "total_invested": 0.0,
                "total_won": 0.0,
                "profit": 0.0,
                "roi_percentage": 0.0,
                "total_contests": 0,
                "roi_by_type": {},
                "period_days": period_days or "all_time",
            }

        total_invested = sum(r.entry_fee for r in rows)
        total_won = sum(r.payout for r in rows)
        profit = total_won - total_invested
        roi_pct = (profit / total_invested * 100) if total_invested > 0 else 0.0

        # Group by contest_type
        roi_by_type: dict[str, Any] = {}
        from itertools import groupby as _groupby
        for ct, grp_iter in _groupby(sorted(rows, key=lambda r: r.contest_type), key=lambda r: r.contest_type):
            grp = list(grp_iter)
            invested = sum(r.entry_fee for r in grp)
            won = sum(r.payout for r in grp)
            roi_by_type[ct] = {
                "invested": invested,
                "won": won,
                "profit": round(won - invested, 2),
                "roi": round((won - invested) / invested * 100, 2) if invested > 0 else 0.0,
                "count": len(grp),
            }

        return {
            "total_invested": round(total_invested, 2),
            "total_won": round(total_won, 2),
            "profit": round(profit, 2),
            "roi_percentage": round(roi_pct, 2),
            "total_contests": len(rows),
            "roi_by_type": roi_by_type,
            "period_days": period_days or "all_time",
        }
    except Exception as exc:
        log.warning("get_roi_summary DB read failed: %s", exc)
        return {"error": str(exc), "total_contests": 0}
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Projection accuracy
# ---------------------------------------------------------------------------

def log_projections(projections: list[dict], slate_date: date, site: str, user_id: str = "") -> int:
    """
    Bulk-insert projection rows for later accuracy reconciliation.

    Each dict should have: player_id, player_name, salary, proj,
    floor (optional), ceiling (optional), ownership (optional).

    Returns number of rows inserted.
    """
    if not projections:
        return 0
    session = _pg_session()
    try:
        if user_id:
            _ensure_user_exists(session, user_id)
        objs = [
            ProjectionLog(
                slate_date=slate_date,
                site=site.upper(),
                player_id=str(p.get("player_id", "")),
                player_name=str(p.get("player_name", p.get("name", ""))),
                salary=int(p.get("salary", 0)),
                proj=float(p.get("proj", p.get("Proj", 0.0))),
                floor=float(p.get("floor", p.get("Floor", 0.0))),
                ceiling=float(p.get("ceiling", p.get("Ceiling", 0.0))),
                ownership=float(p.get("ownership", p.get("Own", 0.0))),
                user_id=user_id,
            )
            for p in projections
        ]
        session.add_all(objs)
        session.commit()
        return len(objs)
    except Exception as exc:
        session.rollback()
        log.error("log_projections failed: %s", exc)
        return 0
    finally:
        session.close()


def reconcile_projections(slate_date: date, site: str) -> dict[str, Any]:
    """
    Match projection_log rows against player_game_logs actuals for a given date.
    Updates the ``actual_pts`` and ``reconciled`` columns.

    Returns a summary dict: {reconciled: N, mae: float, rmse: float}
    """
    session = _pg_session()
    try:
        edge = _edge()

        # Load actuals from DuckDB game logs (read-only reference data)
        actuals_df: pd.DataFrame = edge.execute("""
            SELECT player_name,
                   ROUND(AVG(dk_pts), 3) AS avg_dk,
                   ROUND(AVG(fd_pts), 3) AS avg_fd
            FROM player_game_logs
            WHERE game_date = ?
              AND minutes > 0
            GROUP BY player_name
        """, [slate_date]).df()

        if actuals_df.empty:
            return {"reconciled": 0, "note": "no actuals available yet"}

        pts_col = "avg_dk" if site.upper() == "DK" else "avg_fd"

        # Load unreconciled projections from Postgres
        unrec = (
            session.query(ProjectionLog)
            .filter(
                ProjectionLog.slate_date == slate_date,
                ProjectionLog.site == site.upper(),
                ProjectionLog.reconciled == False,  # noqa: E712
            )
            .all()
        )

        if not unrec:
            return {"reconciled": 0, "note": "no unreconciled projections found"}

        # Build lookup from actuals
        actuals_map: dict[str, float] = dict(
            zip(actuals_df["player_name"], actuals_df[pts_col])
        )

        reconciled_count = 0
        errors: list[float] = []
        for row in unrec:
            actual = actuals_map.get(row.player_name)
            if actual is not None:
                row.actual_pts = float(actual)
                row.reconciled = True
                errors.append(row.proj - float(actual))
                reconciled_count += 1

        session.commit()

        if not errors:
            return {"reconciled": 0, "note": "no player name matches"}

        abs_errors = [abs(e) for e in errors]
        sq_errors = [e ** 2 for e in errors]
        return {
            "reconciled": reconciled_count,
            "mae": round(sum(abs_errors) / len(abs_errors), 3),
            "rmse": round((sum(sq_errors) / len(sq_errors)) ** 0.5, 3),
            "bias": round(sum(errors) / len(errors), 3),
        }

    except Exception as exc:
        session.rollback()
        log.error("reconcile_projections failed: %s", exc)
        return {"error": str(exc), "reconciled": 0}
    finally:
        session.close()


def get_accuracy_report(
    period_days: Optional[int] = 30,
    site: str = "DK",
    user_id: str = "",
) -> dict[str, Any]:
    """
    Pull reconciled projection accuracy stats for the given window.

    Returns: {total_projections, mae, rmse, bias, by_day: [...]}
    """
    session = _pg_session()
    try:
        q = session.query(ProjectionLog).filter(
            ProjectionLog.reconciled == True,  # noqa: E712
            ProjectionLog.site == site.upper(),
        )

        if user_id:
            q = q.filter(ProjectionLog.user_id == user_id)
        if period_days:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=period_days)).date()
            q = q.filter(ProjectionLog.slate_date >= cutoff)

        rows = q.order_by(ProjectionLog.slate_date.desc()).all()

        if not rows:
            return {
                "total_projections": 0,
                "mae": None,
                "rmse": None,
                "bias": None,
                "period_days": period_days or "all_time",
                "by_day": [],
            }

        # Build DataFrame for aggregation
        df = pd.DataFrame([
            {
                "slate_date": r.slate_date,
                "proj": r.proj,
                "actual_pts": r.actual_pts,
            }
            for r in rows
        ])
        df["abs_error"] = (df["proj"] - df["actual_pts"]).abs()
        df["error"] = df["proj"] - df["actual_pts"]

        by_day = (
            df.groupby("slate_date")
            .agg(
                count=("proj", "count"),
                mae=("abs_error", "mean"),
                rmse=("error", lambda x: float((x**2).mean() ** 0.5)),
                bias=("error", "mean"),
            )
            .reset_index()
            .rename(columns={"slate_date": "date"})
            .to_dict(orient="records")
        )

        return {
            "total_projections": len(df),
            "mae": round(float(df["abs_error"].mean()), 3),
            "rmse": round(float((df["error"] ** 2).mean() ** 0.5), 3),
            "bias": round(float(df["error"].mean()), 3),
            "period_days": period_days or "all_time",
            "site": site.upper(),
            "by_day": [
                {
                    "date": str(r["date"]),
                    "count": int(r["count"]),
                    "mae": round(float(r["mae"]), 3),
                    "rmse": round(float(r["rmse"]), 3),
                    "bias": round(float(r["bias"]), 3),
                }
                for r in by_day
            ],
        }
    except Exception as exc:
        log.warning("get_accuracy_report failed: %s", exc)
        return {"error": str(exc)}
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Ownership accuracy
# ---------------------------------------------------------------------------

def synthesize_ownership_from_slate(
    csv_content: str,
    site: str,
    game_date: date,
    contest_type: str = "gpp",
    slate_id: str = "",
) -> dict[str, Any]:
    """
    Accept a DraftKings/FanDuel salary (player pool) CSV and derive synthetic
    ownership estimates from AvgPointsPerGame via a temperature-scaled softmax.

    This is a seeding mechanism — synthetic rows are written to
    ownership_history.duckdb with own_source='synthetic' so the model can
    learn salary/projection → ownership relationships even before you have
    real contest export data.

    Ownership is distributed realistically:
      - GPP  : heavy tails, top player up to ~58%, many at 1-4%
      - Cash : flatter, most players 8–35%
    """
    import io
    import numpy as np

    try:
        df = pd.read_csv(io.StringIO(csv_content))
    except Exception as exc:
        return {"error": f"Could not parse CSV: {exc}", "seeded": 0}

    cols_lower = {c.strip().lower(): c for c in df.columns}

    # Must be a DK/FD salary file
    fppg_col = next(
        (cols_lower[k] for k in ("avgpointspergame", "avg points per game", "fppg", "avgfppg") if k in cols_lower),
        None,
    )
    name_col = next(
        (cols_lower[k] for k in ("name", "nickname", "player") if k in cols_lower),
        None,
    )
    salary_col = next(
        (cols_lower[k] for k in ("salary",) if k in cols_lower),
        None,
    )
    pos_col = next(
        (cols_lower[k] for k in ("position", "roster position") if k in cols_lower),
        None,
    )

    if not fppg_col or not name_col:
        return {
            "error": (
                f"Expected a DraftKings/FanDuel salary CSV with 'Name' and 'AvgPointsPerGame' columns. "
                f"Found: {list(df.columns)}"
            ),
            "seeded": 0,
        }

    work = df[[name_col, fppg_col] + ([salary_col] if salary_col else []) + ([pos_col] if pos_col else [])].copy()
    work.columns = (
        ["name", "fppg"]
        + (["salary"] if salary_col else [])
        + (["position"] if pos_col else [])
    )
    work["fppg"] = pd.to_numeric(work["fppg"], errors="coerce")
    work = work.dropna(subset=["fppg"])
    work = work[work["fppg"] > 0].reset_index(drop=True)

    if work.empty:
        return {"error": "No valid FPPG values found.", "seeded": 0}

    # Temperature-scaled softmax → realistic ownership distribution.
    # Temperature is set relative to the FPPG spread of the slate so the
    # distribution stays realistic regardless of slate size or value tier.
    # Lower temp = more concentrated at top (GPP); higher = flatter (cash).
    fppg_arr = work["fppg"].to_numpy(dtype=float)
    fppg_std = float(np.std(fppg_arr)) or 1.0
    # Aim for the top player to be ~2-3x the median ownership, not 50x.
    # A temperature of ~2× std achieves that across typical DFS slates.
    temperature = fppg_std * 2.5 if contest_type in ("gpp", "winner_take_all") else fppg_std * 4.0
    fppg_arr = work["fppg"].to_numpy(dtype=float)
    logits = fppg_arr / temperature
    logits -= logits.max()  # numerical stability
    exp_logits = np.exp(logits)
    probs = exp_logits / exp_logits.sum()

    # Scale from probability to realistic ownership %
    # GPP: 0.5 – 58%   Cash: 3 – 38%
    own_min = 0.5 if contest_type in ("gpp", "winner_take_all") else 3.0
    own_max = 58.0 if contest_type in ("gpp", "winner_take_all") else 38.0
    n = len(probs)
    # Linear rescale: p_min → own_min, p_max → own_max
    p_min, p_max = probs.min(), probs.max()
    if p_max > p_min:
        own_arr = own_min + (probs - p_min) / (p_max - p_min) * (own_max - own_min)
    else:
        own_arr = np.full(n, (own_min + own_max) / 2)

    work["synthetic_own"] = own_arr.round(1)

    # Write to ownership_history table in Postgres
    seeded = 0
    try:
        from sqlalchemy.dialects.postgresql import insert as _pg_insert
        session = _pg_session()
        try:
            for _, r in work.iterrows():
                stmt = _pg_insert(OwnershipHistory).values(
                    player_name=str(r["name"]),
                    game_date=game_date,
                    site=site.upper(),
                    slate_id=slate_id or str(game_date),
                    actual_own_pct=float(r["synthetic_own"]),
                    proj_at_lock=float(r["fppg"]),
                    salary=int(r["salary"]) if "salary" in r and pd.notna(r.get("salary")) else None,
                    team_total=None,
                    is_home=None,
                    contest_type=contest_type.lower(),
                    own_source="synthetic",
                ).on_conflict_do_update(
                    constraint="uq_ownership_history",
                    set_={"actual_own_pct": float(r["synthetic_own"]), "proj_at_lock": float(r["fppg"])},
                )
                session.execute(stmt)
            session.commit()
            seeded = len(work)
            log.info("Seeded %d synthetic ownership rows from slate", seeded)
        except Exception as exc:
            session.rollback()
            log.error("synthesize_ownership_from_slate write failed: %s", exc)
            return {"error": f"Write failed: {exc}", "seeded": 0}
        finally:
            session.close()
    except Exception as exc:
        log.error("synthesize_ownership_from_slate write failed: %s", exc)
        return {"error": f"Write failed: {exc}", "seeded": 0}

    return {
        "seeded": seeded,
        "note": (
            f"Synthetic ownership estimates written from FPPG ({contest_type}). "
            "These seed the model but real contest actuals are more accurate. "
            "Import real contest exports whenever possible."
        ),
        "top_players": [
            {"name": r["name"], "fppg": round(r["fppg"], 1), "est_own": round(r["synthetic_own"], 1)}
            for _, r in work.nlargest(5, "fppg").iterrows()
        ],
    }


def import_ownership_actuals(
    csv_content: str,
    site: str,
    game_date: date,
    contest_type: str = "gpp",
    slate_id: str = "",
) -> dict[str, Any]:
    """
    Parse a DK/FD contest results CSV and write actual ownership percentages
    to ownership_actuals + ownership_history.duckdb.

    DraftKings contest CSVs contain a "%Owned" column.
    FanDuel contest CSVs contain a "%" or "Ownership %" column.

    Returns: {imported: N, skipped: N, source: site}
    """
    import io
    try:
        df = pd.read_csv(io.StringIO(csv_content))
    except Exception as exc:
        return {"error": f"Could not parse CSV: {exc}", "imported": 0}

    cols_lower = {c.strip(): c for c in df.columns}

    # Detect player name column — ranked by preference
    _name_candidates = (
        "name", "player", "nickname", "player name", "player_name",
        # DK contest standings CSV uses this composite — strip the ID part later
        "name + id",
    )
    name_col = next(
        (cols_lower[k] for k in _name_candidates if k in cols_lower),
        None,
    )

    # Detect ownership column
    _own_candidates = (
        "%owned", "% owned", "owned %", "ownership %", "ownership", "own%",
        "pct_owned", "pct owned", "own_pct",
    )
    own_col = next(
        (cols_lower[k] for k in _own_candidates if k in cols_lower),
        # fallback: any column whose name contains "%" or "own"
        next(
            (c for c in df.columns
             if ("%" in c and "points" not in c.lower() and "salary" not in c.lower())
             or ("own" in c.lower() and "shown" not in c.lower())),
            None,
        ),
    )

    # -----------------------------------------------------------------------
    # Detect wrong file type early and give a clear, actionable error
    # -----------------------------------------------------------------------
    _is_slate_file = any(
        c.strip().lower() in ("roster position", "avgpointspergame", "avg points per game")
        for c in df.columns
    )
    if _is_slate_file:
        return {
            "error": (
                "This looks like a DraftKings slate/salaries file, not a contest results export. "
                "To get the correct file: go to your DraftKings contest → click 'Export' (after it locks) "
                "→ 'Export Results' → download the CSV.  That file will contain a '%Owned' column. "
                "Alternatively you can create a simple CSV with two columns: 'Name' and '%Owned'."
            ),
            "imported": 0,
        }

    if not name_col or not own_col:
        return {
            "error": (
                f"Could not locate name/ownership columns in the uploaded file. "
                f"Columns found: {list(df.columns)}. "
                f"Expected a '%Owned' (or similar) column and a 'Name' column. "
                f"This endpoint accepts: DK contest results exports, FD contest results exports, "
                f"or a simple two-column CSV with headers 'Name' and '%Owned'."
            ),
            "imported": 0,
        }

    # DK "Name + ID" format: "LeBron James (42099018)" → "LeBron James"
    if name_col.strip().lower() == "name + id":
        df[name_col] = df[name_col].astype(str).str.replace(r"\s*\(\d+\)\s*$", "", regex=True).str.strip()

    df = df[[name_col, own_col]].copy()
    df.columns = ["player_name", "actual_own_pct"]
    df["player_name"] = df["player_name"].astype(str).str.strip()
    df["actual_own_pct"] = (
        df["actual_own_pct"].astype(str)
        .str.replace("%", "", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
    )
    df = df.dropna(subset=["actual_own_pct"])
    df = df[df["player_name"].str.len() > 0]

    if df.empty:
        return {"imported": 0, "skipped": 0, "note": "no valid rows after parsing"}

    # Merge with projection_log for that date to get prediction + salary
    pg_session = _pg_session()
    try:
        proj_rows = (
            pg_session.query(ProjectionLog.player_name, ProjectionLog.proj, ProjectionLog.ownership)
            .filter(ProjectionLog.slate_date == game_date, ProjectionLog.site == site.upper())
            .all()
        )
        proj_df = pd.DataFrame(proj_rows, columns=["player_name", "proj", "predicted_own"])
    except Exception:
        proj_df = pd.DataFrame(columns=["player_name", "proj", "predicted_own"])

    merged = df.merge(proj_df, on="player_name", how="left")

    # Write to ownership_actuals in Postgres (upsert on unique constraint)
    imported = 0
    try:
        for _, row in merged.iterrows():
            try:
                stmt = pg_insert(OwnershipActual).values(
                    game_date=game_date,
                    site=site.upper(),
                    player_name=str(row["player_name"]),
                    actual_own_pct=float(row["actual_own_pct"]),
                    predicted_own=float(row["predicted_own"]) if pd.notna(row.get("predicted_own")) else None,
                    proj=float(row["proj"]) if pd.notna(row.get("proj")) else None,
                    contest_type=contest_type.lower(),
                    slate_id=slate_id,
                ).on_conflict_do_update(
                    constraint="uq_ownership_actual",
                    set_={
                        "actual_own_pct": float(row["actual_own_pct"]),
                        "predicted_own": float(row["predicted_own"]) if pd.notna(row.get("predicted_own")) else None,
                        "proj": float(row["proj"]) if pd.notna(row.get("proj")) else None,
                    },
                )
                pg_session.execute(stmt)
                imported += 1
            except Exception:
                pass
        pg_session.commit()
    except Exception as exc:
        pg_session.rollback()
        log.error("import_ownership_actuals insert failed: %s", exc)
    finally:
        pg_session.close()

    # Also write to ownership_history (Postgres) for model training
    try:
        from sqlalchemy.dialects.postgresql import insert as _pg_insert2
        hist_session = _pg_session()
        try:
            for _, r in merged.iterrows():
                stmt = _pg_insert2(OwnershipHistory).values(
                    player_name=str(r["player_name"]),
                    game_date=game_date,
                    site=site.upper(),
                    slate_id=slate_id,
                    actual_own_pct=float(r["actual_own_pct"]),
                    proj_at_lock=float(r["proj"]) if pd.notna(r.get("proj")) else None,
                    salary=None,
                    team_total=None,
                    is_home=None,
                    contest_type=contest_type.lower(),
                    own_source="real",
                ).on_conflict_do_update(
                    constraint="uq_ownership_history",
                    set_={"actual_own_pct": float(r["actual_own_pct"]),
                          "proj_at_lock": float(r["proj"]) if pd.notna(r.get("proj")) else None},
                )
                hist_session.execute(stmt)
            hist_session.commit()
            log.info("Wrote %d ownership actuals to training DB", len(merged))
        except Exception as exc:
            hist_session.rollback()
            log.warning("Could not write to ownership_history: %s", exc)
        finally:
            hist_session.close()
    except Exception as exc:
        log.warning("Could not write to ownership_history: %s", exc)

    return {"imported": imported, "skipped": len(df) - imported, "source": site.upper()}


def get_ownership_accuracy_report(
    period_days: Optional[int] = 30,
    site: str = "DK",
) -> dict[str, Any]:
    """
    Return ownership prediction accuracy metrics (MAE, bias, chalk accuracy).

    chalk_accuracy = % of players with actual_own_pct >= 30%
                     where |predicted - actual| <= 8 percentage points.
    """
    session = _pg_session()
    try:
        q = session.query(OwnershipActual).filter(
            OwnershipActual.site == site.upper(),
            OwnershipActual.predicted_own.isnot(None),
        )

        if period_days:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=period_days)).date()
            q = q.filter(OwnershipActual.game_date >= cutoff)

        rows = q.order_by(OwnershipActual.game_date.desc()).all()

        if not rows:
            return {
                "total_players": 0,
                "mae": None,
                "bias": None,
                "chalk_accuracy": None,
                "model_pct": None,
                "period_days": period_days or "all_time",
                "site": site.upper(),
                "note": "No ownership actuals yet. Import a contest results CSV via POST /analytics/import-ownership-actuals.",
            }

        df = pd.DataFrame([
            {
                "game_date": r.game_date,
                "player_name": r.player_name,
                "actual_own_pct": r.actual_own_pct,
                "predicted_own": r.predicted_own,
                "own_source": r.own_source or "fallback",
            }
            for r in rows
        ])

        abs_err = (df["predicted_own"] - df["actual_own_pct"]).abs()
        bias = float((df["predicted_own"] - df["actual_own_pct"]).mean())

        chalk = df[df["actual_own_pct"] >= 30.0]
        chalk_acc = (
            float((chalk["predicted_own"] - chalk["actual_own_pct"]).abs().le(8.0).mean() * 100)
            if len(chalk) > 0 else None
        )

        model_pct = float((df["own_source"] == "model").mean() * 100) if "own_source" in df.columns else None

        by_day_raw = (
            df.assign(abs_err=abs_err, error=df["predicted_own"] - df["actual_own_pct"])
            .groupby("game_date")
            .agg(count=("player_name", "count"), mae=("abs_err", "mean"), bias=("error", "mean"))
            .reset_index()
            .rename(columns={"game_date": "date"})
        )

        return {
            "total_players": len(df),
            "mae": round(float(abs_err.mean()), 3),
            "bias": round(bias, 3),
            "chalk_accuracy": round(chalk_acc, 1) if chalk_acc is not None else None,
            "model_pct": round(model_pct, 1) if model_pct is not None else None,
            "period_days": period_days or "all_time",
            "site": site.upper(),
            "by_day": [
                {
                    "date": str(r["date"]),
                    "count": int(r["count"]),
                    "mae": round(float(r["mae"]), 3),
                    "bias": round(float(r["bias"]), 3),
                }
                for _, r in by_day_raw.iterrows()
            ],
        }
    except Exception as exc:
        log.warning("get_ownership_accuracy_report failed: %s", exc)
        return {"error": str(exc)}
    finally:
        session.close()


def get_ownership_model_status() -> dict[str, Any]:
    """Return training data size and model file age for each site."""
    from pathlib import Path as _Path
    import datetime as _dt

    root = _Path(__file__).resolve().parent.parent.parent
    models_dir = root / "data" / "models"

    training_rows: dict[str, int] = {}
    try:
        session = _pg_session()
        try:
            for site in ("DK", "FD"):
                count = (
                    session.query(OwnershipHistory)
                    .filter(OwnershipHistory.site == site)
                    .count()
                )
                training_rows[site] = int(count)
        finally:
            session.close()
    except Exception:
        training_rows = {"DK": 0, "FD": 0}

    model_ages: dict[str, Any] = {}
    for site in ("DK", "FD"):
        model_path = models_dir / f"ownership_NBA_{site}.pkl"
        if model_path.exists():
            mtime = _dt.datetime.fromtimestamp(model_path.stat().st_mtime)
            model_ages[site] = {
                "exists": True,
                "trained_at": mtime.isoformat(),
                "age_days": (_dt.datetime.utcnow() - mtime).days,
            }
        else:
            model_ages[site] = {"exists": False}

    return {
        "training_rows": training_rows,
        "models": model_ages,
        "min_rows_required": 300,
        "ready": {site: training_rows.get(site, 0) >= 300 for site in ("DK", "FD")},
    }
