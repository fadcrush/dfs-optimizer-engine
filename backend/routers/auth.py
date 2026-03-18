"""
Authentication Routes - The gateway to our 100K+ user platform!
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from datetime import datetime

from database.db import get_db
from models.user import User
from services.auth import (
    hash_password, verify_password, create_access_token, verify_token,
    get_current_user, create_password_reset_token, hash_reset_token,
    verify_password_reset_token,
)
from services.email_service import send_welcome_email, send_password_reset_email
import logging
log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Request/Response models
class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class AuthResponse(BaseModel):
    access_token: str
    token_type: str
    user: dict

class MessageResponse(BaseModel):
    message: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str

# Routes
@router.post("/signup", response_model=AuthResponse)
async def signup(request: SignupRequest, db: Session = Depends(get_db)):
    """
    Sign up a new user - Each signup is a step toward $29/month! 💰
    """
    
    # Check if user already exists
    existing_user = db.query(User).filter(User.email == request.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Validate password length
    if len(request.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters"
        )
    
    # Create new user
    new_user = User(
        email=request.email,
        password_hash=hash_password(request.password),
        full_name=request.full_name,
        tier="free",  # Start on free tier
        subscription_status="active"
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # Create access token
    access_token = create_access_token(data={"sub": new_user.id})

    log.info("New user signed up: %s", new_user.email)

    # Fire-and-forget welcome email (never blocks the response)
    try:
        send_welcome_email(new_user.email, new_user.full_name or "")
    except Exception as exc:
        log.warning("Welcome email failed for %s: %s", new_user.email, exc)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": new_user.to_dict()
    }

@router.post("/login", response_model=AuthResponse)
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    """
    Log in an existing user - Each login is engagement! 📈
    """
    
    # Find user
    user = db.query(User).filter(User.email == request.email).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    # Verify password
    if not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    # Update last login
    user.last_login_at = datetime.utcnow()
    db.commit()

    # Create access token
    access_token = create_access_token(data={"sub": user.id})

    log.info("User logged in: %s", user.email)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user.to_dict()
    }

@router.get("/me")
async def me(current_user=Depends(get_current_user)):
    """Return the authenticated user's profile."""
    if hasattr(current_user, "to_dict"):
        return current_user.to_dict()
    return current_user


@router.delete("/me")
async def delete_account(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """GDPR/CCPA: Permanently delete the authenticated user's account and all associated data."""
    user_id = current_user.get("id") if isinstance(current_user, dict) else current_user.id
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    db.delete(user)
    db.commit()
    return {"message": "Account and all associated data have been permanently deleted."}


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

@router.post("/request-password-reset", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(body: PasswordResetRequest, db: Session = Depends(get_db)):
    """
    Send a password-reset email.  Always returns 202 — even if the email is
    not registered — to avoid user-enumeration.
    """
    user = db.query(User).filter(User.email == body.email).first()
    if user:
        plain_token, expires_at = create_password_reset_token(user.id)
        user.password_reset_token_hash = hash_reset_token(plain_token)
        user.password_reset_expires_at = expires_at
        db.commit()
        try:
            send_password_reset_email(user.email, plain_token)
        except Exception as exc:
            log.warning("Password reset email failed for %s: %s", user.email, exc)
    return {"message": "If that email is registered you will receive a reset link."}


@router.post("/reset-password")
async def reset_password(body: PasswordResetConfirm, db: Session = Depends(get_db)):
    """
    Apply a new password using a valid reset token.  The token is single-use —
    it is cleared from the DB after successful application.
    """
    if len(body.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters",
        )

    # Decode the user_id from the token prefix so we can load the stored hash.
    parts = body.token.rsplit(":", 2)
    if len(parts) != 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )
    user_id = parts[0]

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.password_reset_token_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    # Full verification: signature, expiry, and single-use hash check.
    try:
        verify_password_reset_token(body.token, user.password_reset_token_hash, user.password_reset_expires_at)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    user.password_hash = hash_password(body.new_password)
    user.password_reset_token_hash = None
    user.password_reset_expires_at = None
    db.commit()
    return {"message": "Password updated successfully. You can now log in."}