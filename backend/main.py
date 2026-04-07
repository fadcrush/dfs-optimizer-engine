"""
DFS Edge Pro - Backend API
The path to $150-300M starts here!
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Add backend and project root to path
backend_dir = str(Path(__file__).parent)
parent_dir = str(Path(__file__).parent.parent)
sys.path.insert(0, backend_dir)
sys.path.insert(0, parent_dir)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

log = logging.getLogger("dfs_edge")
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    stream=sys.stdout,
)

_sentry_dsn = os.getenv("SENTRY_DSN", "")
if _sentry_dsn:
    try:
        import sentry_sdk  # type: ignore[import]
        sentry_sdk.init(dsn=_sentry_dsn, traces_sample_rate=0.1)
        log.info("Sentry error tracking enabled")
    except ImportError:
        log.warning("SENTRY_DSN is set but sentry-sdk is not installed; run: pip install sentry-sdk[fastapi]")

# Import database and routes
from database.db import init_db, test_connection
from routers import auth, projections, optimizer, slates, analytics, games, contests, events, injuries, billing
from routers import admin as admin_router
from routers.pipeline import router as pipeline_router
from routers import tasks as tasks_router
from routers import player_trends as player_trends_router
from routers import health as health_router

def _run_startup_tasks() -> None:
    """Initialize external services and local storage on API startup."""
    log.info("DFS Edge Pro API starting up")

    # Log presence of load-bearing API keys so silent failures are visible immediately.
    _odds_key = os.getenv("THE_ODDS_API_KEY", "")
    if _odds_key:
        log.info("THE_ODDS_API_KEY loaded [OK] (len=%d) - Vegas enrichment active", len(_odds_key))
    else:
        log.warning(
            "THE_ODDS_API_KEY not set -- Vegas enrichment will be skipped on every pipeline run. "
            "Add THE_ODDS_API_KEY to your .env file (see .env.example)."
        )

    if test_connection():
        init_db()
        log.info("Authentication system ready")
    else:
        log.warning("Database connection failed — check your .env file")

    try:
        from workers.schedulers.daily import start_scheduler
        start_scheduler()
        log.info("Background job scheduler started")
    except Exception as exc:
        log.warning("Scheduler startup skipped: %s", exc)

    try:
        _root = Path(__file__).resolve().parent.parent
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        from analysis.shared.db import get_conn

        _db_dir = _root / "data"
        for _db_key in ("dfs_edge", "dfs_master", "contest_results", "nba_news"):
            _db_path = _db_dir / f"{_db_key}.duckdb"
            try:
                get_conn(_db_path, db_key=_db_key)
            except Exception as _exc:
                log.warning("Migration skipped for %s: %s", _db_key, _exc)
        log.info("DuckDB schemas migrated")
    except Exception as exc:
        log.warning("DuckDB migrations skipped: %s", exc)


def _run_shutdown_tasks() -> None:
    """Release background jobs and shared DB resources on API shutdown."""
    try:
        from workers.schedulers.daily import stop_scheduler
        stop_scheduler()
    except Exception:
        pass

    try:
        from analysis.shared.db import close_all
        close_all()
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    # Run blocking startup tasks in a thread so the event loop stays
    # responsive while DuckDB connections and the scheduler initialise.
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _run_startup_tasks)
    try:
        yield
    finally:
        _run_shutdown_tasks()


app = FastAPI(
    title="DFS Edge Pro API",
    description="Professional DFS Platform",
    version="1.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# CORS — restrict to explicit origins; never use "*" in production
# ---------------------------------------------------------------------------
_raw_origins = os.getenv(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173"
)
ALLOWED_ORIGINS: list[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# Include routers
app.include_router(auth.router)
app.include_router(projections.router)
app.include_router(optimizer.router)
app.include_router(slates.router)
app.include_router(analytics.router)
app.include_router(games.router)
app.include_router(contests.router)
app.include_router(events.router)
app.include_router(injuries.router)
app.include_router(billing.router)
app.include_router(pipeline_router)
app.include_router(admin_router.router)
app.include_router(tasks_router.router)
app.include_router(player_trends_router.router)
app.include_router(health_router.router)

@app.get("/")
async def root():
    """Welcome to DFS Edge Pro"""
    return {
        "message": "🚀 DFS Edge Pro API - Day 2 Complete!",
        "version": "1.0.0",
        "status": "Authentication Ready! 🔐",
        "new_features": {
            "signup": "POST /auth/signup",
            "login": "POST /auth/login",
            "get_user": "GET /auth/me"
        },
        "phase": "Phase 1: Validate",
        "progress": "Day 2: Users can now sign up and log in!",
        "next": "Day 3: Wrap your projection code",
        "docs": "/docs"
    }

@app.get("/health")
async def health():
    """Root health check — includes scheduler and data pipeline status."""
    import datetime as _dt
    scheduler_status: dict = {"running": False, "jobs": []}
    try:
        from workers.schedulers.daily import get_scheduler_status
        scheduler_status = get_scheduler_status()
    except Exception:
        pass

    cache_status: dict = {"available": False}
    try:
        from analysis.core.projection_cache import get_cache
        _cache = get_cache()
        cache_status = {"available": _cache._redis is not None or _cache._duckdb_con is not None}
    except Exception:
        pass

    return {
        "status": "healthy",
        "version": "1.0.0",
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "scheduler": scheduler_status,
        "projection_cache": cache_status,
    }

@app.get("/api/vision")
async def vision():
    """The 5-year vision"""
    return {
        "vision": "Become #1 DFS platform globally",
        "current_milestone": "Day 2: Users can create accounts! 🎉",
        "milestones": {
            "year_1": "1,000 users, $348K revenue",
            "year_2": "5,000 users, $1.74M revenue",
            "year_3": "15,000 users, $5.22M revenue",
            "year_4": "30,000 users, $10.44M revenue",
            "year_5": "Exit for $150-300M"
        },
        "education_impact": "$2.1M+ to JSMS Academy over 5 years"
    }

if __name__ == "__main__":
    import uvicorn
    reload_enabled = os.getenv("DFS_BACKEND_RELOAD", "0").strip().lower() in {"1", "true", "yes", "on"}
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=reload_enabled)