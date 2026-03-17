"""
Authentication Service - Securing our path to 100K+ users!
"""

from pathlib import Path
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

# Resolve .env from the project root (two levels up from this file)
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent.parent / ".env")

# Password hashing
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# JWT settings
_INSECURE_DEFAULTS = {"your-secret-key-change-this", "changeme_in_production", ""}
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
if SECRET_KEY in _INSECURE_DEFAULTS:
    raise RuntimeError(
        "JWT_SECRET_KEY is not set or is the insecure default. "
        "Generate a secure key with: "
        "python -c \"import secrets; print(secrets.token_hex(32))\" "
        "and set it in your .env file."
    )
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours


def is_auth_disabled() -> bool:
    return os.getenv("DFS_DISABLE_AUTH", "0").strip().lower() in {"1", "true", "yes", "on"}


def _bypass_user() -> dict:
    return {
        "id": "local-dev-user",
        "email": "local-dev@dfs-edge.local",
        "full_name": "Local Dev User",
        "tier": "admin",
        "subscription_status": "active",
        "auth_bypassed": True,
    }

def hash_password(password: str) -> str:
    """Hash a password for storing"""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash"""
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    """
    Create a JWT access token
    
    Args:
        data: Dictionary containing user info (usually {"sub": user_id})
        expires_delta: How long until token expires
    
    Returns:
        Encoded JWT token string
    """
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str) -> str:
    """
    Verify a JWT token and return the user_id
    
    Args:
        token: JWT token string
    
    Returns:
        user_id from the token
    
    Raises:
        JWTError if token is invalid
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        
        if user_id is None:
            raise JWTError("Invalid token payload")
        
        return user_id
        
    except JWTError:
        raise JWTError("Could not validate credentials")


# ── FastAPI dependency helpers ────────────────────────────────────────────────

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def get_current_user(token: str = Depends(oauth2_scheme)):
    """FastAPI dependency — validates the Bearer token and returns the user dict.

    Usage::
        @router.get("/protected")
        async def protected_route(user = Depends(get_current_user)):
            ...
    """
    if is_auth_disabled():
        return _bypass_user()

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token is None:
        raise credentials_exception
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    # Lazy DB lookup — only runs when DATABASE_URL is configured
    from database.db import SessionLocal
    if SessionLocal is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured. Authentication requires DATABASE_URL.",
        )

    from models.user import User
    from sqlalchemy.orm import Session
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
    finally:
        db.close()

    if user is None:
        raise credentials_exception
    return user


def get_current_user_optional(token: str = Depends(oauth2_scheme)):
    """Like get_current_user but returns None when no token is supplied."""
    if is_auth_disabled():
        return _bypass_user()
    if not token:
        return None
    try:
        return get_current_user(token)
    except HTTPException:
        return None