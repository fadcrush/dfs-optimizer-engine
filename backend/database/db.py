"""Database connection module."""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool
import os
from pathlib import Path
from dotenv import load_dotenv
from contextlib import contextmanager
import logging

log = logging.getLogger(__name__)

# Always load from the repo root .env — regardless of where the process was launched.
# main.py does this too; calling load_dotenv twice is idempotent (already-set vars are skipped).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(dotenv_path=_REPO_ROOT / ".env", override=False)

# Get database URL — may be absent in local dev without Postgres
DATABASE_URL = os.getenv("DATABASE_URL")

# Lazily initialised so the app can start even without DATABASE_URL.
# Routes that call get_db() will raise HTTP 503 instead of crashing uvicorn.
engine = None
SessionLocal = None

if DATABASE_URL:
    try:
        engine = create_engine(
            DATABASE_URL,
            poolclass=NullPool,
            echo=False,
            connect_args={"connect_timeout": 5},
        )
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    except Exception as _engine_err:
        log.warning(
            "DATABASE_URL is set but the database driver is unavailable (%s). "
            "Install psycopg2-binary (pip install psycopg2-binary) to enable "
            "PostgreSQL-backed routes.",
            _engine_err,
        )
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
        log.warning("[db] Skipping init_db — DATABASE_URL not set.")
        return False
    from models.user import Base
    # Import analytics models so Base.metadata.create_all picks them up
    import models.analytics  # noqa: F401
    log.info("[db] Initialising database...")
    try:
        Base.metadata.create_all(bind=engine)
        # Idempotent column additions for existing databases (e.g. Stripe fields
        # added after the initial table was created).
        _ddls = [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_subscription_id VARCHAR",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_token_hash VARCHAR",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_expires_at TIMESTAMP",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_runs_used INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_runs_reset_date VARCHAR",
        ]
        with engine.connect() as conn:
            for ddl in _ddls:
                try:
                    conn.execute(text(ddl))
                except Exception:
                    pass  # dialect may not support IF NOT EXISTS (SQLite < 3.37)
            conn.commit()  # DDL must be committed explicitly in SQLAlchemy 2.x
            conn.execute(text("SELECT 1"))
        log.info("[db] Database tables ready.")
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
