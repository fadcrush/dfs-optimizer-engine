"""
One-time migration: DuckDB (dfs_master.duckdb) → Postgres
==========================================================
Copies the three user-facing analytics tables:

  contest_results   → postgres.contest_results  (ContestResult)
  projection_log    → postgres.projection_log    (ProjectionLog)
  ownership_actuals → postgres.ownership_actuals (OwnershipActual)

Usage::

    cd backend
    python -m scripts.migrate_analytics_to_pg

Requires:
  - DATABASE_URL set in .env (Postgres)
  - data/dfs_master.duckdb to exist with data

Safe to run multiple times — skips tables that already have rows in Postgres.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure backend/ is on sys.path so relative imports resolve
_BACKEND = Path(__file__).resolve().parent.parent
_ROOT = _BACKEND.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger("migrate")


def main() -> None:
    from database.db import engine, SessionLocal
    from models.user import Base
    from models.analytics import ContestResult, ProjectionLog, OwnershipActual

    if engine is None:
        log.error("DATABASE_URL not set — cannot migrate.  Set it in .env and retry.")
        sys.exit(1)

    # Ensure target tables exist
    Base.metadata.create_all(bind=engine)
    log.info("Postgres tables created / verified.")

    # Open DuckDB source
    master_path = _ROOT / "data" / "dfs_edge.duckdb"
    if not master_path.exists():
        log.warning("No dfs_edge.duckdb found at %s — nothing to migrate.", master_path)
        return

    try:
        from analysis.shared.db import get_conn
        duck = get_conn(master_path, db_key="migrate_src", read_only=True)
    except ImportError:
        import duckdb
        duck = duckdb.connect(str(master_path), read_only=True)

    session = SessionLocal()

    # ── contest_results ──────────────────────────────────────────────────
    existing = session.query(ContestResult).count()
    if existing > 0:
        log.info("contest_results already has %d rows in Postgres — skipping.", existing)
    else:
        try:
            df = duck.execute("SELECT * FROM contest_results ORDER BY id").df()
            if not df.empty:
                objs = [
                    ContestResult(
                        contest_date=row.get("contest_date"),
                        contest_type=row.get("contest_type", ""),
                        site=row.get("site", ""),
                        entry_fee=float(row.get("entry_fee", 0)),
                        payout=float(row.get("payout", 0)),
                        final_rank=row.get("final_rank"),
                        total_entries=row.get("total_entries"),
                        lineup_proj=row.get("lineup_proj"),
                        lineup_actual=row.get("lineup_actual"),
                        notes=row.get("notes", ""),
                        user_id=row.get("user_id", ""),
                    )
                    for row in df.to_dict("records")
                ]
                session.add_all(objs)
                session.commit()
                log.info("Migrated %d contest_results rows.", len(objs))
            else:
                log.info("contest_results is empty in DuckDB — nothing to migrate.")
        except Exception as exc:
            session.rollback()
            log.error("contest_results migration failed: %s", exc)

    # ── projection_log ───────────────────────────────────────────────────
    existing = session.query(ProjectionLog).count()
    if existing > 0:
        log.info("projection_log already has %d rows in Postgres — skipping.", existing)
    else:
        try:
            df = duck.execute("SELECT * FROM projection_log ORDER BY id").df()
            if not df.empty:
                objs = [
                    ProjectionLog(
                        slate_date=row.get("slate_date"),
                        site=row.get("site", ""),
                        player_id=str(row.get("player_id", "")),
                        player_name=str(row.get("player_name", "")),
                        salary=int(row["salary"]) if row.get("salary") is not None else None,
                        proj=float(row.get("proj", 0)),
                        floor=row.get("floor"),
                        ceiling=row.get("ceiling"),
                        ownership=row.get("ownership"),
                        actual_pts=row.get("actual_pts"),
                        reconciled=bool(row.get("reconciled", False)),
                        user_id=row.get("user_id", ""),
                    )
                    for row in df.to_dict("records")
                ]
                session.add_all(objs)
                session.commit()
                log.info("Migrated %d projection_log rows.", len(objs))
            else:
                log.info("projection_log is empty in DuckDB — nothing to migrate.")
        except Exception as exc:
            session.rollback()
            log.error("projection_log migration failed: %s", exc)

    # ── ownership_actuals ────────────────────────────────────────────────
    existing = session.query(OwnershipActual).count()
    if existing > 0:
        log.info("ownership_actuals already has %d rows in Postgres — skipping.", existing)
    else:
        try:
            df = duck.execute("SELECT * FROM ownership_actuals ORDER BY id").df()
            if not df.empty:
                objs = [
                    OwnershipActual(
                        game_date=row.get("game_date"),
                        site=row.get("site", ""),
                        player_name=str(row.get("player_name", "")),
                        actual_own_pct=float(row.get("actual_own_pct", 0)),
                        predicted_own=row.get("predicted_own"),
                        own_source=row.get("own_source", "fallback"),
                        salary=int(row["salary"]) if row.get("salary") is not None else None,
                        proj=row.get("proj"),
                        contest_type=row.get("contest_type", "gpp"),
                        slate_id=row.get("slate_id", ""),
                        user_id=row.get("user_id", ""),
                    )
                    for row in df.to_dict("records")
                ]
                session.add_all(objs)
                session.commit()
                log.info("Migrated %d ownership_actuals rows.", len(objs))
            else:
                log.info("ownership_actuals is empty in DuckDB — nothing to migrate.")
        except Exception as exc:
            session.rollback()
            log.error("ownership_actuals migration failed: %s", exc)

    session.close()
    log.info("Migration complete.")


if __name__ == "__main__":
    main()
