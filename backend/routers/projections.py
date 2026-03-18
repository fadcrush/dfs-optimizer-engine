"""
Projections Routes - Generate DFS projections via API!
"""

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
import io

from database.db import get_db, DATABASE_URL
from models.user import User
from services.auth import get_current_user, require_plan
from services.file_service import get_file_path, save_projection_file, save_slate_file
from services.projection_service import generate_projections, projections_to_csv

router = APIRouter(prefix="/api/projections", tags=["Projections"])


def optional_db():
    """Yield a DB session when DATABASE_URL is configured, otherwise yield None."""
    if not DATABASE_URL:
        yield None
        return
    yield from get_db()

@router.post("/upload-slate")
async def upload_slate(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    db: Optional[Session] = Depends(optional_db),
):
    """Upload a slate file (CSV from FanDuel/DraftKings)."""
    user_id = _user_id(current_user)

    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    file_info = await save_slate_file(file, user_id=user_id)
    return {
        "message": "Slate uploaded successfully!",
        "file": file_info,
        "next_step": "Call /generate to create projections",
    }


@router.post("/generate")
async def generate(
    slate_file_name: str,
    site: str = "FD",
    sport: str = "NBA",
    current_user=Depends(get_current_user),
    db: Optional[Session] = Depends(optional_db),
):
    """Generate projections for an uploaded slate."""
    user_id = _user_id(current_user)

    slate_path = get_file_path(slate_file_name, "slate", user_id)
    if not slate_path.exists():
        raise HTTPException(status_code=404, detail="Slate file not found")

    result = await generate_projections(str(slate_path), user_id, site=site, sport=sport)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Projection generation failed"))

    _increment_slates(current_user, db)
    return {"message": "Projections generated successfully!", "result": result}


@router.post("/run")
async def run(
    file: UploadFile = File(...),
    site: str = "DK",
    sport: str = "NBA",
    current_user=Depends(require_plan("pro")),
    db: Optional[Session] = Depends(optional_db),
):
    """Alias for generate-from-upload — single-step upload + project."""
    return await _do_generate_from_upload(file, site, sport, current_user, db)


@router.post("/generate-from-upload")
async def generate_from_upload(
    file: UploadFile = File(...),
    site: str = "FD",
    sport: str = "NBA",
    current_user=Depends(require_plan("pro")),
    db: Optional[Session] = Depends(optional_db),
):
    """One-step: Upload slate AND generate projections."""
    return await _do_generate_from_upload(file, site, sport, current_user, db)


async def _do_generate_from_upload(file, site, sport, current_user, db):
    user_id = _user_id(current_user)
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    file_info = await save_slate_file(file, user_id=user_id)
    result = await generate_projections(file_info["file_path"], user_id, site=site, sport=sport)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Generation failed"))

    csv_data = projections_to_csv(result["projections"])
    proj_file = await save_projection_file(csv_data, file_info["file_id"], user_id=user_id)
    _increment_slates(current_user, db)
    return {
        "success": True,
        "message": "Projections generated successfully",
        "slate_file": file_info,
        "projections": result["projections"],
        "stats": result["stats"],
        "total_projections": len(result["projections"]),
        "algorithm": result.get("algorithm", "canonical"),
        "download_file": proj_file["file_name"],
        "note": result.get("note", ""),
    }


@router.get("/download/{file_name}")
async def download_projections(file_name: str, current_user=Depends(get_current_user)):
    """Download projection results as CSV."""
    file_path = get_file_path(file_name, "projection", _user_id(current_user))
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    with open(file_path, "r") as f:
        content = f.read()
    return StreamingResponse(
        io.StringIO(content),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={file_name}"},
    )


# ── Private helpers ───────────────────────────────────────────────────────────

def _user_id(user) -> str:
    if user is None:
        return "anonymous"
    if hasattr(user, "id"):
        return str(user.id)
    if isinstance(user, dict):
        return str(user.get("id", "anonymous"))
    return "anonymous"


def _increment_slates(user, db):
    """Increment slates_processed for authenticated SQLAlchemy User objects."""
    if user is None or db is None:
        return
    if not hasattr(user, "slates_processed"):
        return
    try:
        user.slates_processed += 1
        db.commit()
    except Exception:
        pass