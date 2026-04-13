"""Database connection module."""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool
import os
from pathlib import Path
from dotenv import load_dotenv
from contextlib import contextmanager
import logging

from alembic.config import Config as AlembicConfig
from alembic import command as alembic_command

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
    log.info(
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
    """Initialize database — run Alembic migrations to latest revision."""
    if engine is None:
        log.info("[db] Skipping init_db — DATABASE_URL not set.")
        return False
    log.info("[db] Running Alembic migrations...")
    try:
        _backend_dir = Path(__file__).resolve().parent.parent
        alembic_cfg = AlembicConfig(str(_backend_dir / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(_backend_dir / "alembic"))
        alembic_cfg.set_main_option("sqlalchemy.url", DATABASE_URL)
        alembic_command.upgrade(alembic_cfg, "head")
        log.info("[db] Database migrations applied — schema is up to date.")
        return True
    except Exception as e:
        log.error("[db] Alembic migration failed: %s", e)
        # Fall back to create_all so the app can still start on a fresh DB
        log.info("[db] Falling back to metadata.create_all()...")
        try:
            from models.user import Base
            import models.analytics  # noqa: F401
            Base.metadata.create_all(bind=engine)
            log.info("[db] Fallback create_all succeeded.")
            return True
        except Exception as fallback_err:
            log.error("[db] Fallback create_all also failed: %s", fallback_err)
            return False


def test_connection():
    """Test if database connection works."""
    if engine is None:
        log.info("[db] No DATABASE_URL — skipping connection test.")
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
