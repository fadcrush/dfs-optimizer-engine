"""
File Service - Handle file uploads and storage
"""

from fastapi import UploadFile
from fastapi import HTTPException
import shutil
import os
import time
import threading
from pathlib import Path
from datetime import datetime
import uuid

import logging
log = logging.getLogger(__name__)

_last_injury_refresh: float = 0.0
_INJURY_COOLDOWN_SECS: float = 300.0  # skip if a refresh ran within the last 5 minutes
_INJURY_COOLDOWN_LOCK: threading.Lock = threading.Lock()


def _trigger_injury_refresh() -> None:
    """Run in a background thread so upload responses aren't delayed.

    Includes a cooldown: if an injury refresh completed within the last
    5 minutes, skip this one – the lock in job_refresh_injuries() will
    serialize any concurrent calls, but this avoids stacking up threads.
    """
    global _last_injury_refresh
    with _INJURY_COOLDOWN_LOCK:
        elapsed = time.time() - _last_injury_refresh
        if elapsed < _INJURY_COOLDOWN_SECS:
            log.info(
                "[file_service] Injury refresh skipped – last refresh %.0fs ago (cooldown=%.0fs)",
                elapsed, _INJURY_COOLDOWN_SECS,
            )
            return
        _last_injury_refresh = time.time()
    try:
        from workers.schedulers.daily import job_refresh_injuries
        job_refresh_injuries()
    except Exception as exc:
        log.warning("[file_service] Background injury refresh failed: %s", exc)

# Maximum allowed upload size (10 MB) — guards against disk-exhaustion attacks (S9)
MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024

# Base upload directory
UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
SLATES_DIR = UPLOAD_DIR / "slates"
PROJECTIONS_DIR = UPLOAD_DIR / "projections"
LINEUPS_DIR = UPLOAD_DIR / "lineups"

# Ensure directories exist
SLATES_DIR.mkdir(parents=True, exist_ok=True)
PROJECTIONS_DIR.mkdir(parents=True, exist_ok=True)
LINEUPS_DIR.mkdir(parents=True, exist_ok=True)


def _normalize_user_id(user_id: str | None) -> str:
    value = str(user_id or "anonymous").strip()
    return value or "anonymous"


def _safe_path_component(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)


def _safe_file_name(file_name: str) -> str:
    safe_name = Path(file_name).name
    if safe_name != file_name or safe_name in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid file name")
    return safe_name


def get_user_storage_dir(file_type: str, user_id: str | None) -> Path:
    normalized_user_id = _safe_path_component(_normalize_user_id(user_id))
    if file_type == "slate":
        base_dir = SLATES_DIR
    elif file_type == "projection":
        base_dir = PROJECTIONS_DIR
    elif file_type == "lineup":
        base_dir = LINEUPS_DIR
    else:
        raise ValueError(f"Unknown file type: {file_type}")

    user_dir = base_dir / normalized_user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def get_slate_index_file(user_id: str | None) -> Path:
    return get_user_storage_dir("slate", user_id) / "index.json"

async def save_slate_file(file: UploadFile, user_id: str | None = None) -> dict:
    """
    Save uploaded slate file
    
    Returns:
        dict with file_path, file_name, file_id
    """
    
    # Reject uploads that exceed the size limit before writing anything to disk
    first_chunk = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(first_chunk) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large — maximum upload size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )
    await file.seek(0)

    # Generate unique file ID
    file_id = str(uuid.uuid4())[:8]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Create filename: slate_YYYYMMDD_HHMMSS_ID.csv
    file_name = f"slate_{timestamp}_{file_id}.csv"
    file_path = get_user_storage_dir("slate", user_id) / file_name
    
    # Save file
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    print(f"✅ Slate file saved: {file_name}")

    # Trigger injury scrape in background so the response isn't delayed
    t = threading.Thread(target=_trigger_injury_refresh, daemon=True, name="injury-refresh-on-upload")
    t.start()
    log.info("[file_service] Injury refresh triggered in background (thread %s)", t.name)

    return {
        "file_id": file_id,
        "file_name": file_name,
        "file_path": str(file_path),
        "original_name": file.filename,
        "size_bytes": os.path.getsize(file_path)
    }

async def save_projection_file(data: str, file_id: str, user_id: str | None = None) -> dict:
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
    file_path = get_user_storage_dir("projection", user_id) / file_name

    # Save file
    with open(file_path, "w") as f:
        f.write(data)

    print(f"✅ Projection file saved: {file_name}")

    return {
        "file_name": file_name,
        "file_path": str(file_path),
        "size_bytes": os.path.getsize(file_path)
    }


async def save_lineup_file(data: str, file_id: str, user_id: str | None = None) -> dict:
    """
    Save lineup results to CSV

    Args:
        data: CSV string content
        file_id: Unique identifier

    Returns:
        dict with file_path, file_name
    """

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"lineups_{timestamp}_{file_id}.csv"
    file_path = get_user_storage_dir("lineup", user_id) / file_name

    with open(file_path, "w") as f:
        f.write(data)

    print(f"✅ Lineup file saved: {file_name}")

    return {
        "file_name": file_name,
        "file_path": str(file_path),
        "size_bytes": os.path.getsize(file_path)
    }

def get_file_path(file_name: str, file_type: str = "slate", user_id: str | None = None) -> Path:
    """Return the resolved path for a user-owned file.

    Validates that the resolved path stays within the expected per-user
    directory to prevent path-traversal attacks.
    """
    safe_name = _safe_file_name(file_name)
    base_dir = get_user_storage_dir(file_type, user_id)
    resolved = (base_dir / safe_name).resolve()
    if not str(resolved).startswith(str(base_dir.resolve())):
        raise HTTPException(status_code=400, detail="Invalid file path")
    return resolved