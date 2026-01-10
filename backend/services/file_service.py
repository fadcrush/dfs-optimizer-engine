"""
File Service - Handle file uploads and storage
"""

from fastapi import UploadFile
import shutil
import os
from pathlib import Path
from datetime import datetime
import uuid

# Base upload directory
UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
SLATES_DIR = UPLOAD_DIR / "slates"
PROJECTIONS_DIR = UPLOAD_DIR / "projections"

# Ensure directories exist
SLATES_DIR.mkdir(parents=True, exist_ok=True)
PROJECTIONS_DIR.mkdir(parents=True, exist_ok=True)

async def save_slate_file(file: UploadFile) -> dict:
    """
    Save uploaded slate file
    
    Returns:
        dict with file_path, file_name, file_id
    """
    
    # Generate unique file ID
    file_id = str(uuid.uuid4())[:8]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Create filename: slate_YYYYMMDD_HHMMSS_ID.csv
    file_name = f"slate_{timestamp}_{file_id}.csv"
    file_path = SLATES_DIR / file_name
    
    # Save file
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    print(f"✅ Slate file saved: {file_name}")
    
    return {
        "file_id": file_id,
        "file_name": file_name,
        "file_path": str(file_path),
        "original_name": file.filename,
        "size_bytes": os.path.getsize(file_path)
    }

async def save_projection_file(data: str, file_id: str) -> dict:
    """
    Save projection results to CSV
    
    Args:
        data: CSV string content
        file_id: Unique identifier
    
    Returns:
        dict with file_path, file_name
    """
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"projections_{timestamp}_{file_id}.csv"
    file_path = PROJECTIONS_DIR / file_name
    
    # Save file
    with open(file_path, "w") as f:
        f.write(data)
    
    print(f"✅ Projection file saved: {file_name}")
    
    return {
        "file_name": file_name,
        "file_path": str(file_path),
        "size_bytes": os.path.getsize(file_path)
    }

def get_file_path(file_name: str, file_type: str = "slate") -> Path:
    """Get full path for a file"""
    if file_type == "slate":
        return SLATES_DIR / file_name
    elif file_type == "projection":
        return PROJECTIONS_DIR / file_name
    else:
        raise ValueError(f"Unknown file type: {file_type}")