"""
Contests Router
===============
Endpoints for contest result import and analytics data.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/contests", tags=["Contests"])

# Ensure scripts/ is importable inside the container
_scripts = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))


@router.post("/import")
async def import_contest(file: UploadFile = File(...)):
    """
    Upload a FanDuel or DraftKings contest CSV to import results.
    Auto-detects site from file headers.
    """
    try:
        from import_contest_results import import_contest_file
        import tempfile, os

        suffix = Path(file.filename or "contest.csv").suffix or ".csv"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        result = import_contest_file(tmp_path)
        os.unlink(tmp_path)

        if result.errors and result.imported == 0:
            raise HTTPException(status_code=422, detail="; ".join(result.errors))

        return {
            "success": True,
            "site": result.site,
            "imported": result.imported,
            "duplicates": result.duplicates,
            "errors": result.errors,
        }
    except HTTPException:
        raise
    except Exception as exc:
        log.error("Contest import failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/roi")
async def contest_roi(
    days: int = Query(default=30, ge=1, le=365),
    site: str = Query(default="all"),
):
    """Return ROI summary for uploaded contest results."""
    try:
        from import_contest_results import get_roi_summary
        return get_roi_summary(days=days, site=site)
    except Exception as exc:
        log.error("ROI summary error: %s", exc)
        return {"data_available": False, "error": str(exc)}


@router.get("/accuracy")
async def contest_accuracy(
    days: int = Query(default=30, ge=1, le=365),
    sport: str = Query(default="NBA"),
):
    """Return projection accuracy metrics."""
    try:
        from import_contest_results import get_accuracy_report
        return get_accuracy_report(days=days, sport=sport)
    except Exception as exc:
        log.error("Accuracy report error: %s", exc)
        return {"data_available": False, "error": str(exc)}
