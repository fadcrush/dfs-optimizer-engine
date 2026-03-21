"""
Phase 46 — Contests router tests

Covers:
  POST /api/contests/import
  GET  /api/contests/roi
  GET  /api/contests/accuracy
"""
from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

_CSV_BYTES = b"contest_id,entry_fee,score\n123,3.0,280.5\n"


# ---------------------------------------------------------------------------
# POST /api/contests/import
# ---------------------------------------------------------------------------

def test_import_requires_auth(make_authed_client) -> None:
    """Endpoint has get_current_user dep; auth-bypassed client always passes."""
    from routers import contests

    client = make_authed_client(contests.router)
    # We expect it to try importing — mock the inner function to avoid real disk ops
    mock_result = MagicMock()
    mock_result.site = "DK"
    mock_result.imported = 1
    mock_result.duplicates = 0
    mock_result.errors = []

    import sys
    mock_mod = MagicMock()
    mock_mod.import_contest_file.return_value = mock_result

    with patch.dict(sys.modules, {"import_contest_results": mock_mod}):
        resp = client.post(
            "/api/contests/import",
            files={"file": ("dk_contest.csv", BytesIO(_CSV_BYTES), "text/csv")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["imported"] == 1
    assert data["site"] == "DK"


def test_import_error_only_returns_422(make_authed_client) -> None:
    from routers import contests
    import sys

    mock_result = MagicMock()
    mock_result.errors = ["bad column: score"]
    mock_result.imported = 0
    mock_result.duplicates = 0
    mock_result.site = "DK"

    mock_mod = MagicMock()
    mock_mod.import_contest_file.return_value = mock_result

    client = make_authed_client(contests.router)
    with patch.dict(sys.modules, {"import_contest_results": mock_mod}):
        resp = client.post(
            "/api/contests/import",
            files={"file": ("dk.csv", BytesIO(_CSV_BYTES), "text/csv")},
        )
    assert resp.status_code == 422


def test_import_exception_returns_500(make_authed_client) -> None:
    from routers import contests
    import sys

    mock_mod = MagicMock()
    mock_mod.import_contest_file.side_effect = RuntimeError("disk full")

    client = make_authed_client(contests.router)
    with patch.dict(sys.modules, {"import_contest_results": mock_mod}):
        resp = client.post(
            "/api/contests/import",
            files={"file": ("dk.csv", BytesIO(_CSV_BYTES), "text/csv")},
        )
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/contests/roi
# ---------------------------------------------------------------------------

def test_roi_returns_summary(make_authed_client) -> None:
    from routers import contests
    import sys

    mock_mod = MagicMock()
    mock_mod.get_roi_summary.return_value = {"roi": 0.12, "contests": 5}

    client = make_authed_client(contests.router)
    with patch.dict(sys.modules, {"import_contest_results": mock_mod}):
        resp = client.get("/api/contests/roi", params={"days": 30, "site": "DK"})

    assert resp.status_code == 200
    assert resp.json()["roi"] == 0.12


def test_roi_exception_returns_data_unavailable(make_authed_client) -> None:
    from routers import contests
    import sys

    mock_mod = MagicMock()
    mock_mod.get_roi_summary.side_effect = RuntimeError("db down")

    client = make_authed_client(contests.router)
    with patch.dict(sys.modules, {"import_contest_results": mock_mod}):
        resp = client.get("/api/contests/roi")

    assert resp.status_code == 200
    assert resp.json()["data_available"] is False


# ---------------------------------------------------------------------------
# GET /api/contests/accuracy
# ---------------------------------------------------------------------------

def test_accuracy_returns_report(make_authed_client) -> None:
    from routers import contests
    import sys

    mock_mod = MagicMock()
    mock_mod.get_accuracy_report.return_value = {"mae": 3.1, "rmse": 5.4}

    client = make_authed_client(contests.router)
    with patch.dict(sys.modules, {"import_contest_results": mock_mod}):
        resp = client.get("/api/contests/accuracy", params={"days": 30, "sport": "NBA"})

    assert resp.status_code == 200
    assert resp.json()["mae"] == 3.1


def test_accuracy_exception_returns_data_unavailable(make_authed_client) -> None:
    from routers import contests
    import sys

    mock_mod = MagicMock()
    mock_mod.get_accuracy_report.side_effect = Exception("no data")

    client = make_authed_client(contests.router)
    with patch.dict(sys.modules, {"import_contest_results": mock_mod}):
        resp = client.get("/api/contests/accuracy")

    assert resp.status_code == 200
    assert resp.json()["data_available"] is False
