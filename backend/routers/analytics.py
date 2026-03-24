"""
Analytics Router
================
Exposes performance tracking and projection accuracy metrics via REST API.

Endpoints
---------
GET  /analytics/roi              — ROI / profit summary
GET  /analytics/accuracy         — Projection accuracy (MAE, RMSE, Bias)
POST /analytics/contest          — Log a contest result
POST /analytics/reconcile        — Trigger actuals reconciliation for a date
GET  /analytics/health           — Quick health check (DB connectivity)
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from services.analytics_service import (
    get_accuracy_report,
    get_roi_summary,
    import_ownership_actuals,
    get_ownership_accuracy_report,
    get_ownership_model_status,
    synthesize_ownership_from_slate,
    init_analytics_tables,
    log_contest_result,
    reconcile_projections,
)
from services.auth import get_current_user


def _uid(user) -> str:
    """Extract user id string from whatever get_current_user returns."""
    if isinstance(user, dict):
        return str(user.get("id", ""))
    return str(getattr(user, "id", ""))

log = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/analytics", tags=["Analytics"])

# Ensure tables exist when the router is loaded
try:
    init_analytics_tables()
except Exception as _exc:
    log.warning("analytics table init (startup): %s", _exc)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ContestResultRequest(BaseModel):
    contest_date: date
    contest_type: str = Field(
        description="'gpp', 'double_up', 'cash', 'winner_take_all'"
    )
    site: str = Field(description="'DK' or 'FD'")
    entry_fee: float = Field(gt=0)
    payout: float = Field(ge=0, default=0.0)
    final_rank: Optional[int] = None
    total_entries: Optional[int] = None
    lineup_proj: Optional[float] = None
    lineup_actual: Optional[float] = None
    notes: str = ""


class ReconcileRequest(BaseModel):
    slate_date: date
    site: str = Field(default="DK")


class ImportOwnershipRequest(BaseModel):
    game_date: date
    site: str = Field(default="DK")
    contest_type: str = Field(default="gpp")
    slate_id: str = Field(default="")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/roi")
async def roi_summary(
    days: Optional[int] = Query(default=30, ge=1, le=365, description="Lookback window in days"),
    site: Optional[str] = Query(default=None, description="Filter by site: DK or FD"),
    current_user=Depends(get_current_user),
):
    """
    Return ROI and profit metrics for contest entries over the requested window.

    ``days=0`` or omitting returns all-time stats.
    """
    period = days if days and days > 0 else None
    data = get_roi_summary(period_days=period, site=site, user_id=_uid(current_user))
    if "error" in data:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=data["error"])
    return data


@router.get("/accuracy")
async def projection_accuracy(
    days: int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
    site: str = Query(default="DK", description="'DK' or 'FD'"),
    current_user=Depends(get_current_user),
):
    """
    Return projection accuracy metrics (MAE, RMSE, bias) for reconciled projections.

    Projections are reconciled nightly when game-log actuals arrive.
    ``by_day`` in the response gives a day-by-day breakdown.
    """
    data = get_accuracy_report(period_days=days, site=site.upper(), user_id=_uid(current_user))
    if "error" in data:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=data["error"])
    return data


@router.post("/contest", status_code=status.HTTP_201_CREATED)
async def log_contest(body: ContestResultRequest, current_user=Depends(get_current_user)):
    """
    Log a single contest entry result.

    Submit after entering a contest (entry_fee, contest_type, payout = 0).
    Update again post-game with final payout and rank.
    """
    ok = log_contest_result(
        contest_date=body.contest_date,
        contest_type=body.contest_type,
        site=body.site,
        entry_fee=body.entry_fee,
        payout=body.payout,
        final_rank=body.final_rank,
        total_entries=body.total_entries,
        lineup_proj=body.lineup_proj,
        lineup_actual=body.lineup_actual,
        notes=body.notes,
        user_id=_uid(current_user),
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write contest result to analytics database.",
        )
    return {"success": True, "message": "Contest result logged."}


@router.post("/reconcile")
async def reconcile(body: ReconcileRequest, current_user=Depends(get_current_user)):
    """
    Match projection_log rows against game-log actuals for the given slate date.

    Call this the morning after a slate to populate projection accuracy data.
    Returns MAE and RMSE for the reconciled slate.
    """
    result = reconcile_projections(slate_date=body.slate_date, site=body.site)
    return result


@router.get("/health")
async def analytics_health():
    """Quick connectivity check — verifies the analytics Postgres tables are accessible."""
    try:
        from database.db import engine
        from sqlalchemy import text, inspect
        if engine is None:
            raise RuntimeError("DATABASE_URL not configured")
        insp = inspect(engine)
        tables = insp.get_table_names()
        analytics_tables = [t for t in tables if t in ("contest_results", "projection_log", "ownership_actuals")]
        return {
            "status": "ok",
            "backend": "postgres",
            "tables": analytics_tables,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Analytics DB unreachable: {exc}",
        )


# ---------------------------------------------------------------------------
# Ownership accuracy
# ---------------------------------------------------------------------------

@router.post("/import-ownership-actuals", status_code=status.HTTP_201_CREATED)
async def import_ownership_actuals_endpoint(
    file: UploadFile,
    game_date: date = Query(..., description="Date of the contest (YYYY-MM-DD)"),
    site: str = Query(default="DK", description="'DK' or 'FD'"),
    contest_type: str = Query(default="gpp"),
    slate_id: str = Query(default=""),
):
    """
    Upload a DraftKings or FanDuel contest results CSV to record actual player
    ownership percentages.  This is the primary data source for calibrating the
    GBR ownership model.

    DK CSV: contains a '%%Owned' column.  Download from:
      Contest Page → Export Results → download CSV.

    After ~30 slates of actuals are imported, trigger
    ``POST /analytics/train-ownership-model`` to retrain.
    """
    content = (await file.read()).decode("utf-8", errors="replace")
    result = import_ownership_actuals(
        csv_content=content,
        site=site,
        game_date=game_date,
        contest_type=contest_type,
        slate_id=slate_id or file.filename.split(".")[0],
    )
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])
    return {"success": True, **result}


@router.get("/ownership-accuracy")
async def ownership_accuracy(
    days: Optional[int] = Query(default=30, ge=1, le=365),
    site: str = Query(default="DK"),
):
    """
    Return ownership prediction accuracy metrics.

    Requires ownership actuals to be imported first via
    ``POST /analytics/import-ownership-actuals``.

    Metrics:
    - mae           — mean absolute error in ownership % (lower = better)
    - bias          — positive = model predicts too chalky; negative = too contrarian
    - chalk_accuracy — % of 30%+ players where prediction was within ±8 percentage points
    - model_pct     — % of rows predicted by GBR model vs percentile fallback
    """
    data = get_ownership_accuracy_report(period_days=days, site=site.upper())
    if "error" in data:
        raise HTTPException(status_code=500, detail=data["error"])
    return data


@router.get("/ownership-model-status")
async def ownership_model_status():
    """
    Return training data counts and model file ages for DK and FD ownership models.
    Use this to decide when to trigger a retrain.
    """
    return get_ownership_model_status()


@router.post("/train-ownership-model")
async def train_ownership_model_endpoint(
    site: str = Query(default="DK", description="'DK' or 'FD'"),
    sport: str = Query(default="NBA"),
):
    """
    Manually trigger a GBR ownership model retrain from accumulated
    ownership_history data.  Requires ≥300 training rows (check
    ``GET /analytics/ownership-model-status`` first).

    The weekly scheduler also runs this automatically on Sunday at 1 AM ET.
    """
    try:
        import time as _time
        from analysis.nba.ownership_v2 import train_ownership_model
        t0 = _time.perf_counter()
        model = train_ownership_model(sport=sport.upper(), site=site.upper())
        elapsed = round(_time.perf_counter() - t0, 2)
        if model is None:
            return {
                "success": False,
                "message": "Insufficient training data (< 300 rows). Import more ownership actuals first.",
            }
        return {
            "success": True,
            "message": f"Ownership model trained for {sport.upper()}/{site.upper()} in {elapsed}s",
            "train_seconds": elapsed,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Training failed: {exc}")


@router.get("/enrichment-check")
async def enrichment_check(
    site: str = Query(default="DK", description="'DK' or 'FD'"),
    sport: str = Query(default="NBA"),
):
    """
    Diagnostic endpoint: loads training data and reports enrichment coverage.
    Shows how many rows have l10_avg, l10_std, and is_home filled from game logs.
    """
    try:
        import numpy as np
        from analysis.nba.ownership_v2 import _load_training_data
        df = _load_training_data(sport.upper(), site.upper())
        if df is None or df.empty:
            return {"status": "no_data", "rows": 0}

        total = len(df)

        def cov(col):
            if col not in df.columns:
                return {"pct": 0, "count": 0}
            nn = int(df[col].notna().sum())
            return {"pct": round(100 * nn / total, 1), "count": nn}

        sample_l10 = []
        if "l10_avg" in df.columns:
            top = df[df["l10_avg"].notna()].nlargest(5, "l10_avg")[["player_name", "l10_avg", "l10_std"]].to_dict("records")
            sample_l10 = [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()} for r in top]

        return {
            "status": "ok",
            "total_rows": total,
            "coverage": {
                "l10_avg": cov("l10_avg"),
                "l10_std": cov("l10_std"),
                "is_home": cov("is_home"),
                "proj_at_lock": cov("proj_at_lock"),
                "salary": cov("salary"),
            },
            "top5_l10_avg_players": sample_l10,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Enrichment check failed: {exc}")


@router.post("/seed-ownership-from-slate", status_code=status.HTTP_201_CREATED)
async def seed_ownership_from_slate(
    file: UploadFile,
    game_date: date = Query(..., description="Slate date (YYYY-MM-DD)"),
    site: str = Query(default="DK"),
    contest_type: str = Query(default="gpp"),
    slate_id: str = Query(default=""),
):
    """
    Upload a DraftKings or FanDuel **salary/player-pool CSV** (the file you
    download to build lineups) to generate synthetic ownership estimates from
    AvgPointsPerGame via temperature-scaled softmax.

    Use this to seed the ownership training database before you have real
    contest exports.  Each slate you seed adds ~50–90 rows toward the 300
    needed to train the GBR model.

    Rows are stored with ``own_source='synthetic'`` so real actuals always
    take precedence.  Real contest exports (``POST /import-ownership-actuals``)
    are more accurate and should replace synthetic rows over time.
    """
    content = (await file.read()).decode("utf-8", errors="replace")
    result = synthesize_ownership_from_slate(
        csv_content=content,
        site=site,
        game_date=game_date,
        contest_type=contest_type,
        slate_id=slate_id or file.filename.split(".")[0],
    )
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])
    return {"success": True, **result}


# ---------------------------------------------------------------------------
# Backtester accuracy trend — exposes projection_accuracy_log to the UI
# ---------------------------------------------------------------------------

@router.get("/backtester-trend")
async def backtester_trend(
    days: int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
    site: str = Query(default="DK", description="'DK' or 'FD'"),
    current_user=Depends(get_current_user),
):
    """
    Return rolling projection accuracy metrics from the backtester log.

    Each row covers one scored slate date.  Use this to track whether
    the model is improving or regressing over time.

    Fields per row
    --------------
    run_date        : date the slate was scored
    site            : DK or FD
    n_players       : number of matched player rows
    mae             : mean absolute error (lower = better)
    rmse            : root mean squared error
    bias            : positive = model over-projects on average
    r_squared       : coefficient of determination (higher = better)
    pct_within_5    : % of players within 5 FPTS of actual
    pct_within_10   : % of players within 10 FPTS of actual

    Also returns
    ------------
    trend           : "improving" | "regressing" | "flat" | "insufficient_data"
                      Compares the MAE of the first half vs second half of the
                      returned window.
    latest          : the most recent single-date metrics dict (or null)
    """
    try:
        from analysis.core.backtester import ProjectionBacktester
        bt = ProjectionBacktester()
        trend_df = bt.get_trend(n_days=days, site=site.upper())
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Backtester unavailable: {exc}",
        )

    if trend_df.empty:
        return {
            "days": days,
            "site": site.upper(),
            "rows": [],
            "trend": "insufficient_data",
            "latest": None,
        }

    rows = trend_df.to_dict("records")
    # Serialise date/datetime objects to ISO strings
    for r in rows:
        for _k in ("run_date", "computed_at"):
            if _k in r and hasattr(r[_k], "isoformat"):
                r[_k] = r[_k].isoformat()

    # Trend direction: compare first-half vs second-half mean MAE
    trend = "insufficient_data"
    maes = [r["mae"] for r in rows if r.get("mae") is not None]
    if len(maes) >= 4:
        mid = len(maes) // 2
        old_mae = sum(maes[:mid]) / mid
        new_mae = sum(maes[mid:]) / (len(maes) - mid)
        diff = new_mae - old_mae
        if diff < -0.5:
            trend = "improving"
        elif diff > 0.5:
            trend = "regressing"
        else:
            trend = "flat"

    latest = rows[-1] if rows else None

    return {
        "days": days,
        "site": site.upper(),
        "rows": rows,
        "trend": trend,
        "latest": latest,
    }
