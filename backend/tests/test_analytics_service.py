"""
Unit tests for backend/services/analytics_service.py

Coverage:
  - log_projections: empty list, successful insert, DB error rollback
  - reconcile_projections: no actuals, no unreconciled rows, successful reconciliation
  - get_accuracy_report: no rows, windowed results with correct MAE/RMSE
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pandas as pd
import pytest

# ── Path bootstrap (mirrors conftest.py) ─────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

# Stub psycopg2 before any SQLAlchemy import touches it
if "psycopg2" not in sys.modules:
    _stub = MagicMock()
    _stub.__version__ = "2.9.0"
    sys.modules["psycopg2"] = _stub
    sys.modules["psycopg2.extensions"] = MagicMock()
    sys.modules["psycopg2.extras"] = MagicMock()

import services.analytics_service as svc
from models.analytics import ProjectionLog


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_session():
    """Return a MagicMock that quacks like a SQLAlchemy session."""
    session = MagicMock()
    session.add_all = MagicMock()
    session.commit = MagicMock()
    session.rollback = MagicMock()
    session.close = MagicMock()
    return session


def _make_proj_log_row(
    player_name: str,
    proj: float,
    actual: float | None = None,
    reconciled: bool = False,
    slate_date: date = date(2025, 1, 1),
    site: str = "DK",
) -> MagicMock:
    row = MagicMock(spec=ProjectionLog)
    row.player_name = player_name
    row.proj = proj
    row.actual_pts = actual
    row.reconciled = reconciled
    row.slate_date = slate_date
    row.site = site
    return row


# ═══════════════════════════════════════════════════════
# log_projections
# ═══════════════════════════════════════════════════════

class TestLogProjections:
    def test_empty_list_returns_zero(self):
        """No DB call when passed an empty list."""
        with patch.object(svc, "_pg_session") as mock_factory:
            result = svc.log_projections([], date(2025, 1, 1), "DK")
        assert result == 0
        mock_factory.assert_not_called()

    def test_successful_insert_returns_count(self):
        session = _make_session()
        projections = [
            {"player_id": "1", "player_name": "LeBron James", "salary": 9800, "proj": 48.5},
            {"player_id": "2", "player_name": "Stephen Curry", "salary": 9600, "proj": 45.2},
        ]
        with patch.object(svc, "_pg_session", return_value=session):
            result = svc.log_projections(projections, date(2025, 1, 1), "DK", user_id="user-abc")

        assert result == 2
        session.add_all.assert_called_once()
        session.commit.assert_called_once()
        session.close.assert_called_once()

    def test_inserted_objects_have_correct_fields(self):
        """Verify each ProjectionLog object gets correct field mapping."""
        session = _make_session()
        projections = [
            {"player_id": "p1", "player_name": "Kevin Durant", "salary": 9200,
             "proj": 42.0, "floor": 25.0, "ceiling": 60.0, "ownership": 18.5},
        ]
        captured = {}

        def capture_add_all(objs):
            captured["objs"] = objs

        session.add_all.side_effect = capture_add_all

        with patch.object(svc, "_pg_session", return_value=session):
            svc.log_projections(projections, date(2025, 3, 15), "FD", user_id="u1")

        obj = captured["objs"][0]
        assert obj.player_name == "Kevin Durant"
        assert obj.salary == 9200
        assert obj.proj == 42.0
        assert obj.floor == 25.0
        assert obj.ceiling == 60.0
        assert obj.ownership == 18.5
        assert obj.site == "FD"
        assert obj.user_id == "u1"

    def test_site_is_uppercased(self):
        session = _make_session()
        captured = {}

        def capture(objs):
            captured["objs"] = objs

        session.add_all.side_effect = capture

        with patch.object(svc, "_pg_session", return_value=session):
            svc.log_projections(
                [{"player_id": "1", "player_name": "A", "salary": 5000, "proj": 20.0}],
                date(2025, 1, 1),
                "dk",  # lowercase — should be normalised
            )

        assert captured["objs"][0].site == "DK"

    def test_db_error_triggers_rollback_and_returns_zero(self):
        session = _make_session()
        session.commit.side_effect = Exception("DB write failed")

        with patch.object(svc, "_pg_session", return_value=session):
            result = svc.log_projections(
                [{"player_id": "1", "player_name": "X", "salary": 5000, "proj": 10.0}],
                date(2025, 1, 1),
                "DK",
            )

        assert result == 0
        session.rollback.assert_called_once()
        session.close.assert_called_once()

    def test_alternate_field_names_proj_capitalized(self):
        """proj can come in as 'Proj' (capital P) — the service should handle it."""
        session = _make_session()
        captured = {}
        session.add_all.side_effect = lambda objs: captured.update({"objs": objs})

        with patch.object(svc, "_pg_session", return_value=session):
            svc.log_projections(
                [{"player_id": "2", "player_name": "Giannis", "salary": 9000, "Proj": 55.0}],
                date(2025, 1, 2),
                "DK",
            )

        assert captured["objs"][0].proj == 55.0


# ═══════════════════════════════════════════════════════
# reconcile_projections
# ═══════════════════════════════════════════════════════

class TestReconcileProjections:

    def _query_returning(self, session, rows):
        """Set up session.query(...).filter(...).all() to return ``rows``."""
        query_mock = MagicMock()
        filter_mock = MagicMock()
        filter_mock.return_value = filter_mock  # chained .filter()
        filter_mock.all.return_value = rows
        query_mock.filter.return_value = filter_mock
        session.query.return_value = query_mock
        return session

    def test_returns_no_actuals_when_duckdb_empty(self):
        session = _make_session()
        edge = MagicMock()
        edge.execute.return_value.df.return_value = pd.DataFrame()

        with patch.object(svc, "_pg_session", return_value=session), \
             patch.object(svc, "_edge", return_value=edge):
            result = svc.reconcile_projections(date(2025, 1, 5), "DK")

        assert result["reconciled"] == 0
        assert "no actuals" in result.get("note", "")

    def test_returns_no_unreconciled_when_all_done(self):
        session = _make_session()
        edge = MagicMock()
        actuals_df = pd.DataFrame({
            "player_name": ["LeBron James"],
            "avg_dk": [48.5],
            "avg_fd": [44.0],
        })
        edge.execute.return_value.df.return_value = actuals_df

        self._query_returning(session, [])  # no unreconciled rows

        with patch.object(svc, "_pg_session", return_value=session), \
             patch.object(svc, "_edge", return_value=edge):
            result = svc.reconcile_projections(date(2025, 1, 5), "DK")

        assert result["reconciled"] == 0
        assert "no unreconciled" in result.get("note", "")

    def test_successful_reconcile_computes_correct_metrics(self):
        """Two players; known proj vs actual → verify MAE and RMSE."""
        session = _make_session()
        edge = MagicMock()
        actuals_df = pd.DataFrame({
            "player_name": ["Player A", "Player B"],
            "avg_dk": [40.0, 30.0],
            "avg_fd": [38.0, 28.0],
        })
        edge.execute.return_value.df.return_value = actuals_df

        row_a = _make_proj_log_row("Player A", proj=42.0, reconciled=False)
        row_b = _make_proj_log_row("Player B", proj=28.0, reconciled=False)

        self._query_returning(session, [row_a, row_b])

        with patch.object(svc, "_pg_session", return_value=session), \
             patch.object(svc, "_edge", return_value=edge):
            result = svc.reconcile_projections(date(2025, 1, 5), "DK")

        assert result["reconciled"] == 2
        # errors: [42-40=2, 28-30=-2] → abs_errors [2,2] → MAE=2.0
        assert result["mae"] == pytest.approx(2.0, abs=0.01)
        # RMSE = sqrt((4+4)/2) = sqrt(4) = 2.0
        assert result["rmse"] == pytest.approx(2.0, abs=0.01)
        # bias = (2 + -2) / 2 = 0.0
        assert result["bias"] == pytest.approx(0.0, abs=0.01)

    def test_uses_fd_pts_column_for_fd_site(self):
        """When site=FD, actuals should read avg_fd not avg_dk."""
        session = _make_session()
        edge = MagicMock()
        # avg_dk deliberately wrong; avg_fd is the right column
        actuals_df = pd.DataFrame({
            "player_name": ["Player A"],
            "avg_dk": [99.0],
            "avg_fd": [35.0],
        })
        edge.execute.return_value.df.return_value = actuals_df

        row = _make_proj_log_row("Player A", proj=37.0, reconciled=False)
        self._query_returning(session, [row])

        with patch.object(svc, "_pg_session", return_value=session), \
             patch.object(svc, "_edge", return_value=edge):
            result = svc.reconcile_projections(date(2025, 1, 5), "FD")

        # error = 37 - 35 = 2, not 37 - 99 = -62
        assert result["mae"] == pytest.approx(2.0, abs=0.01)

    def test_unmatched_players_not_counted(self):
        """Players in projection_log but not in actuals are silently skipped."""
        session = _make_session()
        edge = MagicMock()
        actuals_df = pd.DataFrame({
            "player_name": ["Player A"],
            "avg_dk": [40.0],
            "avg_fd": [38.0],
        })
        edge.execute.return_value.df.return_value = actuals_df

        row_known = _make_proj_log_row("Player A", proj=42.0, reconciled=False)
        row_unknown = _make_proj_log_row("Mystery Player", proj=99.0, reconciled=False)

        self._query_returning(session, [row_known, row_unknown])

        with patch.object(svc, "_pg_session", return_value=session), \
             patch.object(svc, "_edge", return_value=edge):
            result = svc.reconcile_projections(date(2025, 1, 5), "DK")

        assert result["reconciled"] == 1

    def test_db_error_returns_error_key(self):
        session = _make_session()
        session.query.side_effect = Exception("PG connection failed")
        edge = MagicMock()
        edge.execute.return_value.df.return_value = pd.DataFrame({
            "player_name": ["X"], "avg_dk": [10.0], "avg_fd": [9.0],
        })

        with patch.object(svc, "_pg_session", return_value=session), \
             patch.object(svc, "_edge", return_value=edge):
            result = svc.reconcile_projections(date(2025, 1, 5), "DK")

        assert "error" in result
        assert result["reconciled"] == 0
        session.rollback.assert_called_once()


# ═══════════════════════════════════════════════════════
# get_accuracy_report
# ═══════════════════════════════════════════════════════

class TestGetAccuracyReport:

    def _setup_query(self, session, rows):
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.all.return_value = rows
        session.query.return_value = q
        return session

    def test_empty_returns_zeroed_dict(self):
        session = _make_session()
        self._setup_query(session, [])

        with patch.object(svc, "_pg_session", return_value=session):
            result = svc.get_accuracy_report(period_days=30, site="DK")

        assert result["total_projections"] == 0
        assert result["mae"] is None
        assert result["rmse"] is None

    def test_returns_correct_aggregates(self):
        """Two reconciled rows → verify totals and by_day."""
        session = _make_session()

        today = date(2025, 2, 10)
        row_a = MagicMock(spec=ProjectionLog)
        row_a.slate_date = today
        row_a.proj = 45.0
        row_a.actual_pts = 40.0   # abs_error=5, error=5

        row_b = MagicMock(spec=ProjectionLog)
        row_b.slate_date = today
        row_b.proj = 30.0
        row_b.actual_pts = 33.0   # abs_error=3, error=-3

        self._setup_query(session, [row_a, row_b])

        with patch.object(svc, "_pg_session", return_value=session):
            result = svc.get_accuracy_report(period_days=30, site="DK")

        assert result["total_projections"] == 2
        # MAE = (5+3)/2 = 4.0
        assert result["mae"] == pytest.approx(4.0, abs=0.01)
        # RMSE = sqrt((25+9)/2) = sqrt(17) ≈ 4.123
        assert result["rmse"] == pytest.approx((17.0 ** 0.5), abs=0.01)
        # bias = (5 + -3) / 2 = 1.0
        assert result["bias"] == pytest.approx(1.0, abs=0.01)
        assert len(result["by_day"]) == 1

    def test_period_days_none_returns_all_time(self):
        """period_days=None means no cutoff filter — by_day is 'all_time'."""
        session = _make_session()
        self._setup_query(session, [])

        with patch.object(svc, "_pg_session", return_value=session):
            result = svc.get_accuracy_report(period_days=None, site="DK")

        assert result["period_days"] == "all_time"
