"""Database connection module."""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool
import os
from dotenv import load_dotenv
from contextlib import contextmanager
import logging

log = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Get database URL — may be absent in local dev without Postgres
DATABASE_URL = os.getenv("DATABASE_URL")

# Lazily initialised so the app can start even without DATABASE_URL.
# Routes that call get_db() will raise HTTP 503 instead of crashing uvicorn.
engine = None
SessionLocal = None

if DATABASE_URL:
    engine = create_engine(
        DATABASE_URL,
        poolclass=NullPool,
        echo=False,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
else:
    log.warning(
        "DATABASE_URL not set — database-backed routes will be unavailable. "
        "Set DATABASE_URL in .env to enable authentication and slate storage."
    )


def get_db() -> Session:
    """FastAPI dependency — yields a DB session or raises HTTP 503."""
    if SessionLocal is None:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail="Database not configured. Set DATABASE_URL in .env.",
        )
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context():
    """Context manager for manual database operations."""
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL not configured")
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    """Initialize database — create all tables."""
    if engine is None:
        print("[db] Skipping init_db — DATABASE_URL not set.")
        return False
    from models.user import Base
    print("[db] Initialising database...")
    try:
        Base.metadata.create_all(bind=engine)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("[db] Database tables ready.")
        return True
    except Exception as e:
        print(f"[db] Database init failed: {e}")
        return False


def test_connection():
    """Test if database connection works."""
    if engine is None:
        print("[db] No DATABASE_URL — skipping connection test.")
        return False
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.fetchone()[0]
            print(f"[db] Connected to PostgreSQL: {version}")
            return True
    except Exception as e:
        print(f"[db] Connection failed: {e}")
        return False
