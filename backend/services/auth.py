"""
Authentication Service - Securing our path to 100K+ users!
"""

from pathlib import Path
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta, timezone
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
        "is_admin": True,
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
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
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


_TIER_RANK: dict[str, int] = {"free": 0, "pro": 1, "elite": 2, "admin": 99}


def require_plan(min_tier: str = "pro"):
    """FastAPI dependency — requires the authenticated user to have at least *min_tier*.

    Usage::
        @router.post("/run")
        async def run(current_user=Depends(require_plan("pro"))):
            ...
    """
    def _check(current_user=Depends(get_current_user)):
        tier = (
            current_user.get("tier", "free")
            if isinstance(current_user, dict)
            else getattr(current_user, "tier", "free")
        )
        if _TIER_RANK.get(tier, 0) < _TIER_RANK.get(min_tier, 1):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This feature requires a {min_tier} subscription.",
            )
        return current_user
    return _check


def require_admin(current_user=Depends(get_current_user)):
    """FastAPI dependency — requires the authenticated user to have is_admin=True.

    The dev-bypass user (auth disabled) is always treated as admin.
    """
    is_admin = (
        current_user.get("is_admin", False)
        if isinstance(current_user, dict)
        else getattr(current_user, "is_admin", False)
    )
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return current_user


# ---------------------------------------------------------------------------
# Per-tier daily run enforcement
# ---------------------------------------------------------------------------

_FREE_DAILY_RUNS: int = int(os.getenv("FREE_DAILY_RUNS", "5"))


def enforce_daily_run_limit():
    """FastAPI dependency — caps free-tier users at FREE_DAILY_RUNS (default 5) per UTC day.

    Pro / elite / admin users are never counted or blocked.  The counter
    resets automatically the first time the user runs after midnight UTC.

    On any database error the request is **allowed through** — enforcement
    accuracy is sacrificed over availability.

    Response headers on success:
        X-RateLimit-Limit      — daily cap for this tier
        X-RateLimit-Remaining  — runs left today

    On 429:
        X-RateLimit-Limit      — daily cap
        X-RateLimit-Remaining  — 0
        Retry-After            — 86400 (seconds in a day)
        X-Upgrade-URL          — /billing
    """
    from datetime import timezone as _tz

    def _check(current_user=Depends(get_current_user)):
        tier = (
            current_user.get("tier", "free")
            if isinstance(current_user, dict)
            else getattr(current_user, "tier", "free")
        )
        # Dev bypass or paid tier → skip counting
        if (isinstance(current_user, dict) and current_user.get("auth_bypassed")) or \
                _TIER_RANK.get(tier, 0) >= _TIER_RANK.get("pro", 1):
            return current_user

        from database.db import SessionLocal
        if SessionLocal is None:
            return current_user  # no DB configured → allow

        from models.user import User
        db = SessionLocal()
        try:
            uid = current_user.get("id") if isinstance(current_user, dict) else current_user.id
            user = db.query(User).filter(User.id == uid).first()
            if user is None:
                return current_user

            today = datetime.now(_tz.utc).date().isoformat()
            if user.daily_runs_reset_date != today:
                user.daily_runs_used = 0
                user.daily_runs_reset_date = today

            if user.daily_runs_used >= _FREE_DAILY_RUNS:
                db.commit()
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"Free tier daily limit of {_FREE_DAILY_RUNS} projection runs reached. "
                        "Upgrade to Pro for unlimited access."
                    ),
                    headers={
                        "X-RateLimit-Limit": str(_FREE_DAILY_RUNS),
                        "X-RateLimit-Remaining": "0",
                        "Retry-After": "86400",
                        "X-Upgrade-URL": "/billing",
                    },
                )

            user.daily_runs_used += 1
            db.commit()
        except HTTPException:
            raise
        except Exception:
            pass  # DB errors: allow the request through
        finally:
            db.close()

        return current_user

    return _check


# ---------------------------------------------------------------------------
# Password-reset token helpers
# ---------------------------------------------------------------------------
# Tokens are HMAC-SHA256 over "{user_id}:{expiry_unix_ts}" using the JWT
# secret key.  We store only the SHA-256 hash in the DB so a compromised DB
# cannot be used to construct valid reset links.
# ---------------------------------------------------------------------------

import hashlib
import hmac
import time as _time


def create_password_reset_token(user_id: str, ttl_seconds: int = 3600) -> tuple[str, datetime]:
    """Generate a signed password-reset token.

    Returns:
        (plain_token, expires_at)  — store hash(plain_token) in the DB.
    """
    expiry = int(_time.time()) + ttl_seconds
    payload = f"{user_id}:{expiry}"
    sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    plain_token = f"{payload}:{sig}"
    expires_at = datetime.utcfromtimestamp(expiry)
    return plain_token, expires_at


def hash_reset_token(plain_token: str) -> str:
    """Return the SHA-256 hex digest of *plain_token* for DB storage."""
    return hashlib.sha256(plain_token.encode()).hexdigest()


def verify_password_reset_token(plain_token: str, stored_hash: str, expires_at) -> str:
    """Verify a reset token and return the user_id it was issued for.

    Raises ``ValueError`` with a user-safe message on any failure.
    """
    try:
        parts = plain_token.rsplit(":", 2)
        if len(parts) != 3:
            raise ValueError("Invalid token format")
        user_id, expiry_str, sig = parts
    except Exception:
        raise ValueError("Malformed reset token")

    # Constant-time signature check
    payload = f"{user_id}:{expiry_str}"
    expected_sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, sig):
        raise ValueError("Invalid reset token")

    # Expiry check
    if int(_time.time()) > int(expiry_str):
        raise ValueError("Reset token has expired")

    # DB-side hash check (ensures the token hasn't already been used/revoked)
    if stored_hash != hash_reset_token(plain_token):
        raise ValueError("Reset token has already been used")

    return user_id