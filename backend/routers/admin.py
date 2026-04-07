"""
Admin API — user management and platform metrics.
All routes require is_admin=True on the authenticated user.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.db import get_db
from models.user import User
from services.auth import require_admin

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/stats")
def admin_stats(
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return platform-level metrics: user counts and rough MRR."""
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)

    total_users = db.query(func.count(User.id)).scalar() or 0
    pro_users = (
        db.query(func.count(User.id))
        .filter(User.tier.in_(["pro", "elite"]))
        .scalar()
        or 0
    )
    free_users = (
        db.query(func.count(User.id)).filter(User.tier == "free").scalar() or 0
    )
    new_users_30d = (
        db.query(func.count(User.id))
        .filter(User.created_at >= thirty_days_ago)
        .scalar()
        or 0
    )
    # Rough MRR: pro=$29, elite=$79
    elite_users = (
        db.query(func.count(User.id)).filter(User.tier == "elite").scalar() or 0
    )
    mrr = (pro_users - elite_users) * 29 + elite_users * 79
    # Users who logged in within the last 7 days (retention signal)
    active_users_7d = (
        db.query(func.count(User.id))
        .filter(User.last_login_at >= seven_days_ago)
        .scalar()
        or 0
    )

    return {
        "total_users": total_users,
        "pro_users": pro_users,
        "elite_users": elite_users,
        "free_users": free_users,
        "new_users_30d": new_users_30d,
        "active_users_7d": active_users_7d,
        "mrr": mrr,
    }


@router.get("/users")
def admin_users(
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    tier: Optional[str] = Query(None, description="Filter by tier: free, pro, elite"),
    search: Optional[str] = Query(None, description="Search by email or name"),
):
    """Return a paginated list of all users."""
    query = db.query(User)

    if tier:
        query = query.filter(User.tier == tier)

    if search:
        like = f"%{search}%"
        query = query.filter(
            User.email.ilike(like) | User.full_name.ilike(like)
        )

    total = query.count()
    users = (
        query.order_by(User.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return {
        "users": [u.to_dict() for u in users],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
    }
