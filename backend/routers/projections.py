"""
Projections Routes - Generate DFS projections via API!
"""

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from datetime import datetime
import io

from database.db import get_db
from models.user import User
from services.auth import verify_token
from services.file_service import save_slate_file, save_projection_file
from services.projection_service import generate_projections, projections_to_csv

router = APIRouter(prefix="/api/projections", tags=["Projections"])

@router.post("/upload-slate")
async def upload_slate(
    file: UploadFile = File(...),
    token: str = None,
    db: Session = Depends(get_db)
):
    """
    Upload a slate file (CSV from FanDuel/DraftKings)
    
    This is step 1 - uploading the slate
    Next: generate projections for this slate
    """
    
    # Verify user (optional for now, but good practice)
    user_id = "demo-user"  # For testing
    if token:
        try:
            user_id = verify_token(token)
        except:
            pass  # Allow without token for testing
    
    # Validate file type
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a CSV")
    
    # Save file
    file_info = await save_slate_file(file)
    
    return {
        "message": "Slate uploaded successfully! 🎉",
        "file": file_info,
        "next_step": "Call /generate to create projections",
        "progress": "Step 1/2 complete"
    }

@router.post("/generate")
async def generate(
    slate_file_name: str,
    token: str = None,
    db: Session = Depends(get_db)
):
    """
    Generate projections for an uploaded slate
    
    This calls your DFS algorithm to create projections!
    """
    
    # Get user
    user_id = "demo-user"
    if token:
        try:
            user_id = verify_token(token)
        except:
            pass
    
    # Get file path
    from services.file_service import get_file_path
    slate_path = get_file_path(slate_file_name, "slate")
    
    if not slate_path.exists():
        raise HTTPException(status_code=404, detail="Slate file not found")
    
    # Generate projections
    result = await generate_projections(str(slate_path), user_id)
    
    if not result['success']:
        raise HTTPException(status_code=500, detail=result.get('error', 'Projection generation failed'))
    
    # Update user stats (if authenticated)
    if token:
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                user.slates_processed += 1
                db.commit()
        except:
            pass
    
    return {
        "message": "Projections generated successfully! 🎯",
        "result": result,
        "progress": "Step 2/2 complete",
        "your_algorithm": "Currently using placeholder. Will integrate your nba_today_projections_v3 next!"
    }

@router.post("/generate-from-upload")
async def generate_from_upload(
    file: UploadFile = File(...),
    token: str = None,
    db: Session = Depends(get_db)
):
    """
    One-step: Upload slate AND generate projections
    
    This combines upload + generate into one API call!
    Perfect for the frontend later!
    """
    
    # Get user
    user_id = "demo-user"
    if token:
        try:
            user_id = verify_token(token)
            user = db.query(User).filter(User.id == user_id).first()
        except:
            user = None
    else:
        user = None
    
    # Validate file
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a CSV")
    
    # Save file
    file_info = await save_slate_file(file)
    
    # Generate projections
    result = await generate_projections(file_info['file_path'], user_id)
    
    if not result['success']:
        raise HTTPException(status_code=500, detail=result.get('error', 'Generation failed'))
    
    # Save projection file
    csv_data = projections_to_csv(result['projections'])
    proj_file = await save_projection_file(csv_data, file_info['file_id'])
    
    # Update user stats
    if user:
        user.slates_processed += 1
        db.commit()
    
    return {
        "message": "🎉 Projections generated successfully!",
        "slate_file": file_info,
        "projections": result['projections'][:10],  # First 10 for preview
        "stats": result['stats'],
        "total_projections": len(result['projections']),
        "download_file": proj_file['file_name'],
        "note": result.get('note', '')
    }

@router.get("/download/{file_name}")
async def download_projections(file_name: str):
    """
    Download projection results as CSV
    """
    from services.file_service import get_file_path
    
    file_path = get_file_path(file_name, "projection")
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    # Read file
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Return as downloadable CSV
    return StreamingResponse(
        io.StringIO(content),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={file_name}"}
    )