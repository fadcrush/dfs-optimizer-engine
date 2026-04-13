"""Initial schema — users, contest_results, projection_log, ownership_actuals, ownership_history

Revision ID: 0001_initial
Revises: None
Create Date: 2026-04-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users ─────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("email_verified", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("full_name", sa.String(), nullable=True),
        sa.Column("tier", sa.String(), server_default="free"),
        sa.Column("subscription_status", sa.String(), server_default="active"),
        sa.Column("subscription_expires_at", sa.DateTime(), nullable=True),
        sa.Column("stripe_customer_id", sa.String(), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(), nullable=True),
        sa.Column("password_reset_token_hash", sa.String(), nullable=True),
        sa.Column("password_reset_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("lineups_generated", sa.Integer(), server_default="0"),
        sa.Column("slates_processed", sa.Integer(), server_default="0"),
        sa.Column("daily_runs_used", sa.Integer(), server_default="0"),
        sa.Column("daily_runs_reset_date", sa.String(), nullable=True),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.text("false")),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("stripe_customer_id"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_tier", "users", ["tier"])
    op.create_index("ix_users_created_at", "users", ["created_at"])
    op.create_index("ix_users_subscription_status", "users", ["subscription_status"])

    # ── contest_results ───────────────────────────────────────────────────
    op.create_table(
        "contest_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("contest_date", sa.Date(), nullable=False),
        sa.Column("contest_type", sa.String(), nullable=False),
        sa.Column("site", sa.String(), nullable=False),
        sa.Column("entry_fee", sa.Double(), nullable=False),
        sa.Column("payout", sa.Double(), nullable=False, server_default="0"),
        sa.Column("final_rank", sa.Integer(), nullable=True),
        sa.Column("total_entries", sa.Integer(), nullable=True),
        sa.Column("lineup_proj", sa.Double(), nullable=True),
        sa.Column("lineup_actual", sa.Double(), nullable=True),
        sa.Column("notes", sa.String(), server_default=""),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_contest_results_user_date", "contest_results", ["user_id", "contest_date"])
    op.create_index("ix_contest_results_site", "contest_results", ["site"])

    # ── projection_log ────────────────────────────────────────────────────
    op.create_table(
        "projection_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("slate_date", sa.Date(), nullable=False),
        sa.Column("site", sa.String(), nullable=False),
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("player_name", sa.String(), nullable=False),
        sa.Column("salary", sa.Integer(), nullable=True),
        sa.Column("proj", sa.Double(), nullable=False),
        sa.Column("floor", sa.Double(), nullable=True),
        sa.Column("ceiling", sa.Double(), nullable=True),
        sa.Column("ownership", sa.Double(), nullable=True),
        sa.Column("actual_pts", sa.Double(), nullable=True),
        sa.Column("reconciled", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_projection_log_user_date", "projection_log", ["user_id", "slate_date"])
    op.create_index("ix_projection_log_reconcile", "projection_log", ["slate_date", "site", "reconciled"])

    # ── ownership_actuals ─────────────────────────────────────────────────
    op.create_table(
        "ownership_actuals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("site", sa.String(), nullable=False),
        sa.Column("player_name", sa.String(), nullable=False),
        sa.Column("actual_own_pct", sa.Double(), nullable=False),
        sa.Column("predicted_own", sa.Double(), nullable=True),
        sa.Column("own_source", sa.String(), server_default="fallback"),
        sa.Column("salary", sa.Integer(), nullable=True),
        sa.Column("proj", sa.Double(), nullable=True),
        sa.Column("contest_type", sa.String(), server_default="gpp"),
        sa.Column("slate_id", sa.String(), server_default=""),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("game_date", "site", "player_name", "slate_id", name="uq_ownership_actual"),
    )
    op.create_index("ix_ownership_actuals_date_site", "ownership_actuals", ["game_date", "site"])
    op.create_index("ix_ownership_actuals_predicted_own", "ownership_actuals", ["predicted_own"])

    # ── ownership_history ─────────────────────────────────────────────────
    op.create_table(
        "ownership_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("player_name", sa.String(), nullable=False),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("site", sa.String(), nullable=False),
        sa.Column("slate_id", sa.String(), server_default=""),
        sa.Column("actual_own_pct", sa.Double(), nullable=False),
        sa.Column("proj_at_lock", sa.Double(), nullable=True),
        sa.Column("salary", sa.Integer(), nullable=True),
        sa.Column("team_total", sa.Double(), nullable=True),
        sa.Column("is_home", sa.Boolean(), nullable=True),
        sa.Column("contest_type", sa.String(), server_default="gpp"),
        sa.Column("own_source", sa.String(), server_default="real"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "game_date", "site", "player_name", "slate_id", "own_source",
            name="uq_ownership_history",
        ),
    )
    op.create_index("ix_ownership_history_date_site", "ownership_history", ["game_date", "site"])
    op.create_index("ix_ownership_history_player", "ownership_history", ["player_name"])


def downgrade() -> None:
    op.drop_table("ownership_history")
    op.drop_table("ownership_actuals")
    op.drop_table("projection_log")
    op.drop_table("contest_results")
    op.drop_table("users")
