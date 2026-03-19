"""
Daily DFS Edge Scheduler
========================
APScheduler-based background scheduler for recurring tasks.

Jobs (all times ET):
  - 08:00  Fetch daily NBA injury report
  - 09:30  Purge expired projection cache entries
  - 10:00  Refresh Vegas lines + implied totals
  - 11:00  Pre-warm projections for active slates
  - 23:00  Scan & import new contest results from uploads/

Start from backend/main.py via lifespan:

    from workers.schedulers.daily import start_scheduler, stop_scheduler

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        start_scheduler()
        yield
        stop_scheduler()

Or run standalone:
    python -m workers.schedulers.daily
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

# Ensure project root is importable
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger(__name__)

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    _APScheduler_available = True
except ImportError:
    _APScheduler_available = False
    log.warning("apscheduler not installed — daily scheduler disabled. pip install apscheduler")

_scheduler: "BackgroundScheduler | None" = None

# Prevents two threads (scheduler + on-upload) from writing to nba_news.duckdb concurrently.
# DuckDB connections are not safe for simultaneous multi-threaded writes via the same
# connection object. The second caller waits up to 10 min, then sees the DB is already
# current and exits quickly via the ensure_current() "already up to date" check.
_INJURY_LOCK = threading.Lock()


# ── Individual job functions ─────────────────────────────────────────────────

def job_refresh_injuries() -> None:
    """Fetch & parse the latest NBA injury report PDF."""
    if not _INJURY_LOCK.acquire(blocking=True, timeout=600):
        log.warning("[scheduler] job_refresh_injuries: could not acquire lock after 10 min – skipping")
        return
    try:
        log.info("[scheduler] job_refresh_injuries starting")
        from scripts.jobs.fetch_nba_injuries import ensure_current, DB_PATH  # type: ignore[import]
        result = ensure_current(db_path=DB_PATH)
        log.info("[scheduler] Injury refresh result: %s", result)
    except Exception as exc:
        log.error("[scheduler] job_refresh_injuries failed: %s", exc)
    finally:
        _INJURY_LOCK.release()


def job_purge_cache() -> None:
    """Remove expired entries from the projection cache."""
    log.info("[scheduler] job_purge_cache starting")
    try:
        from analysis.core.projection_cache import get_cache
        n = get_cache().purge_expired()
        log.info("[scheduler] Purged %d expired cache entries", n)
    except Exception as exc:
        log.error("[scheduler] job_purge_cache failed: %s", exc)


def job_refresh_vegas() -> None:
    """Pull fresh Vegas lines and store enriched odds for today's games."""
    log.info("[scheduler] job_refresh_vegas starting")
    try:
        from analysis.shared.vegas_enricher import TheOddsAPIClient  # type: ignore[attr-defined]
        from analysis.shared.api_clients import TheOddsAPIClient as Client
        client = Client()
        odds = client.get_nba_odds()
        log.info("[scheduler] Vegas refresh — %d games fetched", len(odds) if odds else 0)
    except Exception as exc:
        log.error("[scheduler] job_refresh_vegas failed: %s", exc)


def job_prewarm_projections() -> None:
    """
    Pre-compute projections for all slate CSVs found in data/uploads/
    so the first user request gets a cache hit.
    """
    log.info("[scheduler] job_prewarm_projections starting")
    uploads_dir = _ROOT / "data" / "uploads"
    if not uploads_dir.exists():
        log.info("[scheduler] No uploads dir found — skipping pre-warm")
        return
    try:
        from analysis.core.projection_cache import get_cache
        from analysis.core.orchestrator import run_dfs_pipeline
        from analysis.core.schemas import ProjectionContext

        cache = get_cache()
        csv_files = list(uploads_dir.glob("*.csv"))
        log.info("[scheduler] Pre-warming %d slates", len(csv_files))
        for csv_path in csv_files:
            stem = csv_path.stem
            # Detect site
            site = "FD" if "fanduel" in stem.lower() or "fd" in stem.lower() else "DK"
            sport = "NBA"
            # Skip if already cached
            if cache.get(stem, sport, site) is not None:
                log.info("[scheduler] Pre-warm skip (cached): %s", stem)
                continue
            try:
                ctx = ProjectionContext(sport=sport, site=site, slate_id=stem)  # type: ignore[arg-type]
                run_dfs_pipeline(str(csv_path), ctx)
                log.info("[scheduler] Pre-warmed: %s", stem)
            except Exception as exc:
                log.warning("[scheduler] Pre-warm failed for %s: %s", stem, exc)
    except Exception as exc:
        log.error("[scheduler] job_prewarm_projections outer failure: %s", exc)


def job_import_contest_results() -> None:
    """Scan uploads dir for new contest CSVs and import them."""
    log.info("[scheduler] job_import_contest_results starting")
    try:
        uploads_dir = str(_ROOT / "data" / "uploads")
        from scripts.jobs.import_contest_results import scan_and_import  # type: ignore[import]
        results = scan_and_import(uploads_dir)
        log.info("[scheduler] Contest import — %d files processed", len(results))
    except Exception as exc:
        log.error("[scheduler] job_import_contest_results failed: %s", exc)


def job_import_ownership_history() -> None:
    """
    Scan data/uploads/ for lineup CSVs and import player-appearance counts into
    ownership_history.duckdb.  This feeds the GBR ownership model with real data
    so it can be trained (currently the DB is empty and the model falls back to
    the legacy percentile-rank heuristic on every run).

    Runs at 00:30 ET every night.  Safe to rerun — INSERT OR REPLACE deduplicates
    by (player_name, game_date, site, slate_id).
    """
    log.info("[scheduler] job_import_ownership_history starting")
    try:
        from analysis.nba.ownership_v2 import import_lineups_to_history
        result = import_lineups_to_history(
            lineup_dir=str(_ROOT / "data" / "uploads"),
            site="DK",
        )
        log.info("[scheduler] Ownership history import: %s", result)
    except Exception as exc:
        log.error("[scheduler] job_import_ownership_history failed: %s", exc)

def job_ingest_game_logs() -> None:
    """
    Pull the last 3 days of NBA game logs from stats.nba.com (free, no key)
    into dfs_edge.duckdb::player_game_logs.  Runs nightly at 3 AM ET.

    Uses a 3-day window so it always catches any late-reported box scores without
    re-downloading the entire season.  INSERT OR REPLACE deduplicates cleanly.

    Requires nba_api>=1.4.0 in requirements.txt.
    """
    log.info("[scheduler] job_ingest_game_logs starting (catch-up: last 3 days)")
    try:
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, str(_ROOT / "scripts" / "ingest_game_logs.py"), "--days", "3"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            log.info("[scheduler] game logs ingested OK: %s", result.stdout.strip()[-500:])
        else:
            log.warning("[scheduler] ingest_game_logs exited %d: %s",
                        result.returncode, result.stderr.strip()[-500:])
    except Exception as exc:
        log.error("[scheduler] job_ingest_game_logs failed: %s", exc)
def _upload_backup_to_cloud(local_path: Path) -> None:
    """Upload a DuckDB backup to S3-compatible cloud storage.

    Environment variables (all optional; cloud upload is disabled by default):

        CLOUD_BACKUP_ENABLED      "true" / "1" / "yes" to activate (default: false)
        CLOUD_BACKUP_BUCKET       target bucket name (required when enabled)
        AWS_ACCESS_KEY_ID         key ID (AWS or Backblaze B2 application key ID)
        AWS_SECRET_ACCESS_KEY     secret (AWS secret key or B2 application key)
        CLOUD_BACKUP_ENDPOINT_URL custom S3 endpoint for B2 / MinIO / etc.
                                  e.g. https://s3.us-west-004.backblazeb2.com
        CLOUD_BACKUP_PREFIX       object key prefix (default: "dfs-edge/backups/")
    """
    if os.environ.get("CLOUD_BACKUP_ENABLED", "false").lower() not in ("1", "true", "yes"):
        return

    bucket = os.environ.get("CLOUD_BACKUP_BUCKET", "")
    if not bucket:
        log.warning("[scheduler] CLOUD_BACKUP_BUCKET not set — skipping cloud upload")
        return

    try:
        import boto3  # type: ignore[import]
        from botocore.config import Config  # type: ignore[import]

        endpoint_url = os.environ.get("CLOUD_BACKUP_ENDPOINT_URL") or None
        prefix = os.environ.get("CLOUD_BACKUP_PREFIX", "dfs-edge/backups/").rstrip("/") + "/"
        object_key = f"{prefix}{local_path.name}"

        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )
        s3.upload_file(str(local_path), bucket, object_key)
        log.info("[scheduler] Cloud backup uploaded — s3://%s/%s", bucket, object_key)
    except ImportError:
        log.warning(
            "[scheduler] boto3 not installed — cloud backup skipped. "
            "Run: pip install boto3"
        )
    except Exception as exc:
        log.error(
            "[scheduler] Cloud backup upload failed for %s: %s",
            local_path.name, exc,
        )


def job_backup_duckdb() -> None:
    """Copy every *.duckdb in data/ to data/backups/ with a UTC timestamp suffix.

    Runs at 04:00 ET nightly (after game-log ingest at 03:00 is complete).
    Keeps backups for 7 days; prunes older files automatically.
    If CLOUD_BACKUP_ENABLED=true, also uploads each backup to S3-compatible
    cloud storage (AWS S3, Backblaze B2, etc.).
    """
    import shutil
    from datetime import datetime as _dt

    log.info("[scheduler] job_backup_duckdb starting")
    data_dir = _ROOT / "data"
    backup_dir = data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.utcnow().strftime("%Y%m%d_%H%M")
    cutoff = _dt.utcnow().timestamp() - 7 * 86400

    backed_up = 0
    for db_file in data_dir.glob("*.duckdb"):
        dest = backup_dir / f"{db_file.stem}_{stamp}.duckdb"
        try:
            shutil.copy2(db_file, dest)
            backed_up += 1
            _upload_backup_to_cloud(dest)
        except Exception as exc:
            log.warning("[scheduler] Backup failed for %s: %s", db_file.name, exc)

    pruned = 0
    for old in backup_dir.glob("*.duckdb"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
                pruned += 1
        except Exception:
            pass

    log.info(
        "[scheduler] Backup complete — %d files backed up, %d old files pruned",
        backed_up, pruned,
    )

def job_retrain_ownership_model() -> None:
    """
    Retrain the GBR ownership model from accumulated ownership_history data.
    Runs weekly on Sunday at 01:00 ET.  Requires ≥300 training rows; skips
    silently if the threshold isn’t met yet.    """    """
    log.info("[scheduler] job_retrain_ownership_model starting")
    try:
        from analysis.nba.ownership_v2 import train_ownership_model
        for site in ("DK", "FD"):
            model = train_ownership_model(sport="NBA", site=site)
            if model is not None:
                log.info("[scheduler] Ownership model retrained for NBA/%s", site)
            else:
                log.info("[scheduler] Ownership model retraining skipped for NBA/%s (insufficient data)", site)
    except Exception as exc:
        log.error("[scheduler] job_retrain_ownership_model failed: %s", exc)


# ── Scheduler lifecycle ──────────────────────────────────────────────────────

def start_scheduler(timezone: str = "America/New_York") -> None:
    """Start the background scheduler with all registered jobs."""
    global _scheduler

    if not _APScheduler_available:
        log.warning("APScheduler unavailable — skipping scheduler start")
        return

    if _scheduler is not None and _scheduler.running:
        log.info("Scheduler already running")
        return

    _scheduler = BackgroundScheduler(timezone=timezone)

    # NBA injury report — 8 AM ET
    _scheduler.add_job(
        job_refresh_injuries,
        CronTrigger(hour=8, minute=0),
        id="refresh_injuries",
        replace_existing=True,
        name="Refresh NBA Injuries",
    )

    # Purge expired cache — 9:30 AM ET
    _scheduler.add_job(
        job_purge_cache,
        CronTrigger(hour=9, minute=30),
        id="purge_cache",
        replace_existing=True,
        name="Purge Projection Cache",
    )

    # Vegas lines — 10 AM ET
    _scheduler.add_job(
        job_refresh_vegas,
        CronTrigger(hour=10, minute=0),
        id="refresh_vegas",
        replace_existing=True,
        name="Refresh Vegas Lines",
    )

    # Pre-warm projections — 11 AM ET
    _scheduler.add_job(
        job_prewarm_projections,
        CronTrigger(hour=11, minute=0),
        id="prewarm_projections",
        replace_existing=True,
        name="Pre-warm Projections",
    )

    # Contest result import — 11 PM ET
    _scheduler.add_job(
        job_import_contest_results,
        CronTrigger(hour=23, minute=0),
        id="import_contests",
        replace_existing=True,
        name="Import Contest Results",
    )

    # Ownership history import — 12:30 AM ET (after contest results finish)
    _scheduler.add_job(
        job_import_ownership_history,
        CronTrigger(hour=0, minute=30),
        id="import_ownership_history",
        replace_existing=True,
        name="Import Ownership History",
    )

    # Nightly NBA game log ingest — 3:00 AM ET (after all games are final)
    _scheduler.add_job(
        job_ingest_game_logs,
        CronTrigger(hour=3, minute=0),
        id="ingest_game_logs",
        replace_existing=True,
        name="Ingest NBA Game Logs",
    )

    # Nightly DuckDB backup — 4:00 AM ET (after game-log ingest)
    _scheduler.add_job(
        job_backup_duckdb,
        CronTrigger(hour=4, minute=0),
        id="backup_duckdb",
        replace_existing=True,
        name="Backup DuckDB Files",
    )

    # Weekly ownership model retrain — Sunday 1:00 AM ET
    _scheduler.add_job(
        job_retrain_ownership_model,
        CronTrigger(day_of_week="sun", hour=1, minute=0),
        id="retrain_ownership_model",
        replace_existing=True,
        name="Retrain Ownership Model",
    )

    _scheduler.start()
    log.info(
        "DFS Edge scheduler started — %d jobs registered",
        len(_scheduler.get_jobs()),
    )


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("DFS Edge scheduler stopped")
    _scheduler = None


def get_scheduler_status() -> dict:
    """Return scheduler status for the /api/health endpoint."""
    if _scheduler is None or not _scheduler.running:
        return {"running": False, "jobs": []}
    jobs = [
        {
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
        }
        for job in _scheduler.get_jobs()
    ]
    return {"running": True, "jobs": jobs}


# ── Wire lifespan into FastAPI ────────────────────────────────────────────────
# Call this from backend/main.py AFTER importing:
#
#   from workers.schedulers.daily import lifespan
#   app = FastAPI(lifespan=lifespan)
#
# Or if you already have a lifespan, call start_scheduler() / stop_scheduler()
# inside your existing context manager.

try:
    from contextlib import asynccontextmanager
    from fastapi import FastAPI

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[misc]
        start_scheduler()
        yield
        stop_scheduler()

except ImportError:
    pass


# ── Standalone entry‑point ────────────────────────────────────────────────────
if __name__ == "__main__":
    import time

    logging.basicConfig(level=logging.INFO)
    start_scheduler()
    log.info("Scheduler running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        stop_scheduler()


