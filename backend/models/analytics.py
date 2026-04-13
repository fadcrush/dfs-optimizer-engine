"""
SQLAlchemy models for analytics tables — migrated from DuckDB to Postgres
so they share the same transactional store as the ``users`` table.

Tables
------
contest_results    — per-user ROI tracking (one row per contest entry)
projection_log     — per-user projection accuracy (one row per player-slate)
ownership_actuals  — ownership calibration (actual vs predicted %)
ownership_history  — GBR model training data (replaces ownership_history.duckdb)
"""

from datetime import date as _date, datetime, timezone

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Double, ForeignKey, Index, Integer,
    String, UniqueConstraint,
)
from models.user import Base


class ContestResult(Base):
    __tablename__ = "contest_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contest_date = Column(Date, nullable=False)
    contest_type = Column(String, nullable=False)  # gpp, double_up, cash, winner_take_all
    site = Column(String, nullable=False)           # DK or FD
    entry_fee = Column(Double, nullable=False)
    payout = Column(Double, nullable=False, default=0.0)
    final_rank = Column(Integer, nullable=True)
    total_entries = Column(Integer, nullable=True)
    lineup_proj = Column(Double, nullable=True)
    lineup_actual = Column(Double, nullable=True)
    notes = Column(String, default="")
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_contest_results_user_date", "user_id", "contest_date"),
        Index("ix_contest_results_site", "site"),
    )


class ProjectionLog(Base):
    __tablename__ = "projection_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slate_date = Column(Date, nullable=False)
    site = Column(String, nullable=False)
    player_id = Column(String, nullable=False)
    player_name = Column(String, nullable=False)
    salary = Column(Integer, nullable=True)
    proj = Column(Double, nullable=False)
    floor = Column(Double, nullable=True)
    ceiling = Column(Double, nullable=True)
    ownership = Column(Double, nullable=True)
    actual_pts = Column(Double, nullable=True)
    reconciled = Column(Boolean, default=False)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_projection_log_user_date", "user_id", "slate_date"),
        Index("ix_projection_log_reconcile", "slate_date", "site", "reconciled"),
    )


class OwnershipActual(Base):
    __tablename__ = "ownership_actuals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_date = Column(Date, nullable=False)
    site = Column(String, nullable=False)
    player_name = Column(String, nullable=False)
    actual_own_pct = Column(Double, nullable=False)
    predicted_own = Column(Double, nullable=True)
    own_source = Column(String, default="fallback")
    salary = Column(Integer, nullable=True)
    proj = Column(Double, nullable=True)
    contest_type = Column(String, default="gpp")
    slate_id = Column(String, default="")
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("game_date", "site", "player_name", "slate_id", name="uq_ownership_actual"),
        Index("ix_ownership_actuals_date_site", "game_date", "site"),
        Index("ix_ownership_actuals_predicted_own", "predicted_own"),
    )


class OwnershipHistory(Base):
    """GBR ownership model training data — one row per player per contest slate.

    Replaces ``data/ownership_history.duckdb``.  Moved to Postgres so concurrent
    writes from multiple uvicorn/Celery workers don't collide.

    Distinct from ``ownership_actuals``:
      - ownership_actuals  : per-user accuracy display (predicted vs actual)
      - ownership_history  : model training features (may include synthetic rows)
    """

    __tablename__ = "ownership_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_name = Column(String, nullable=False)
    game_date = Column(Date, nullable=False)
    site = Column(String, nullable=False)            # DK or FD
    slate_id = Column(String, default="")
    actual_own_pct = Column(Double, nullable=False)
    proj_at_lock = Column(Double, nullable=True)
    salary = Column(Integer, nullable=True)
    team_total = Column(Double, nullable=True)
    is_home = Column(Boolean, nullable=True)
    contest_type = Column(String, default="gpp")
    own_source = Column(String, default="real")      # "real", "synthetic", "lineup_import"
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint(
            "game_date", "site", "player_name", "slate_id", "own_source",
            name="uq_ownership_history",
        ),
        Index("ix_ownership_history_date_site", "game_date", "site"),
        Index("ix_ownership_history_player", "player_name"),
    )
