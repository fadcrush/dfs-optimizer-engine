"""
Phase 47 — Analytics router tests

Covers:
  GET  /analytics/roi
  GET  /analytics/accuracy
  POST /analytics/contest          → 201
  POST /analytics/reconcile
  GET  /analytics/health
  POST /analytics/import-ownership-actuals
  GET  /analytics/ownership-accuracy
  GET  /analytics/ownership-model-status
  POST /analytics/train-ownership-model
  GET  /analytics/enrichment-check
"""
from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# GET /analytics/roi
# ---------------------------------------------------------------------------

def test_roi_returns_summary(make_authed_client, monkeypatch) -> None:
    from routers import analytics

    monkeypatch.setattr(analytics, "get_roi_summary", lambda **_: {"total_profit": 42.5, "roi_pct": 8.2})
    client = make_authed_client(analytics.router)
    resp = client.get("/analytics/roi", params={"days": 30})
    assert resp.status_code == 200
    assert resp.json()["total_profit"] == 42.5


def test_roi_service_error_returns_500(make_authed_client, monkeypatch) -> None:
    from routers import analytics

    monkeypatch.setattr(analytics, "get_roi_summary", lambda **_: {"error": "db down"})
    client = make_authed_client(analytics.router)
    resp = client.get("/analytics/roi")
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /analytics/accuracy
# ---------------------------------------------------------------------------

def test_accuracy_returns_report(make_authed_client, monkeypatch) -> None:
    from routers import analytics

    monkeypatch.setattr(analytics, "get_accuracy_report", lambda **_: {"mae": 2.1, "rmse": 3.4})
    client = make_authed_client(analytics.router)
    resp = client.get("/analytics/accuracy", params={"days": 30, "site": "DK"})
    assert resp.status_code == 200
    assert resp.json()["mae"] == 2.1


def test_accuracy_service_error_returns_500(make_authed_client, monkeypatch) -> None:
    from routers import analytics

    monkeypatch.setattr(analytics, "get_accuracy_report", lambda **_: {"error": "no data"})
    client = make_authed_client(analytics.router)
    resp = client.get("/analytics/accuracy")
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /analytics/contest
# ---------------------------------------------------------------------------

def test_log_contest_returns_201(make_authed_client, monkeypatch) -> None:
    from routers import analytics

    monkeypatch.setattr(analytics, "log_contest_result", lambda **_: True)
    client = make_authed_client(analytics.router)
    resp = client.post(
        "/analytics/contest",
        json={
            "contest_date": "2026-03-15",
            "contest_type": "gpp",
            "site": "DK",
            "entry_fee": 3.0,
            "payout": 0.0,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["success"] is True


def test_log_contest_service_failure_returns_500(make_authed_client, monkeypatch) -> None:
    from routers import analytics

    monkeypatch.setattr(analytics, "log_contest_result", lambda **_: False)
    client = make_authed_client(analytics.router)
    resp = client.post(
        "/analytics/contest",
        json={
            "contest_date": "2026-03-15",
            "contest_type": "gpp",
            "site": "DK",
            "entry_fee": 3.0,
        },
    )
    assert resp.status_code == 500


def test_log_contest_rejects_missing_fields(make_authed_client) -> None:
    from routers import analytics

    client = make_authed_client(analytics.router)
    resp = client.post("/analytics/contest", json={"site": "DK"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /analytics/reconcile
# ---------------------------------------------------------------------------

def test_reconcile_returns_result(make_authed_client, monkeypatch) -> None:
    from routers import analytics

    monkeypatch.setattr(analytics, "reconcile_projections", lambda **_: {"mae": 4.1, "rmse": 6.2, "rows": 5})
    client = make_authed_client(analytics.router)
    resp = client.post(
        "/analytics/reconcile",
        json={"slate_date": "2026-03-15", "site": "DK"},
    )
    assert resp.status_code == 200
    assert resp.json()["rows"] == 5


# ---------------------------------------------------------------------------
# GET /analytics/health
# ---------------------------------------------------------------------------

def test_health_db_unavailable_returns_503(make_authed_client) -> None:
    """Without a real DATABASE_URL the engine is None → 503."""
    from routers import analytics

    client = make_authed_client(analytics.router)
    resp = client.get("/analytics/health")
    # In test env DATABASE_URL is empty; engine is None → should raise 503
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /analytics/import-ownership-actuals
# ---------------------------------------------------------------------------

_OWN_CSV = b"player_name,pct_owned\nLeBron James,28.5\nAntony Davis,31.2\n"


def test_import_ownership_actuals_success(make_authed_client, monkeypatch) -> None:
    from routers import analytics

    monkeypatch.setattr(analytics, "import_ownership_actuals", lambda **_: {"imported": 2})
    client = make_authed_client(analytics.router)
    resp = client.post(
        "/analytics/import-ownership-actuals",
        files={"file": ("actuals.csv", BytesIO(_OWN_CSV), "text/csv")},
        params={"game_date": "2026-03-15", "site": "DK"},
    )
    assert resp.status_code == 201
    assert resp.json()["success"] is True


def test_import_ownership_actuals_error_returns_422(make_authed_client, monkeypatch) -> None:
    from routers import analytics

    monkeypatch.setattr(analytics, "import_ownership_actuals", lambda **_: {"error": "missing column"})
    client = make_authed_client(analytics.router)
    resp = client.post(
        "/analytics/import-ownership-actuals",
        files={"file": ("actuals.csv", BytesIO(_OWN_CSV), "text/csv")},
        params={"game_date": "2026-03-15", "site": "DK"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /analytics/ownership-accuracy
# ---------------------------------------------------------------------------

def test_ownership_accuracy_returns_metrics(make_authed_client, monkeypatch) -> None:
    from routers import analytics

    monkeypatch.setattr(analytics, "get_ownership_accuracy_report", lambda **_: {"mae": 5.2})
    client = make_authed_client(analytics.router)
    resp = client.get("/analytics/ownership-accuracy", params={"days": 30, "site": "DK"})
    assert resp.status_code == 200
    assert resp.json()["mae"] == 5.2


def test_ownership_accuracy_service_error_returns_500(make_authed_client, monkeypatch) -> None:
    from routers import analytics

    monkeypatch.setattr(analytics, "get_ownership_accuracy_report", lambda **_: {"error": "no data"})
    client = make_authed_client(analytics.router)
    resp = client.get("/analytics/ownership-accuracy")
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /analytics/ownership-model-status
# ---------------------------------------------------------------------------

def test_ownership_model_status_returns_dict(make_authed_client, monkeypatch) -> None:
    from routers import analytics

    monkeypatch.setattr(
        analytics,
        "get_ownership_model_status",
        lambda: {"DK": {"rows": 120, "model_age_days": 3}, "FD": {"rows": 80, "model_age_days": 7}},
    )
    client = make_authed_client(analytics.router)
    resp = client.get("/analytics/ownership-model-status")
    assert resp.status_code == 200
    assert "DK" in resp.json()


# ---------------------------------------------------------------------------
# POST /analytics/train-ownership-model
# ---------------------------------------------------------------------------

def test_train_ownership_model_success(make_authed_client) -> None:
    import sys
    from unittest.mock import MagicMock
    from routers import analytics

    mock_mod = MagicMock()
    mock_mod.train_ownership_model.return_value = object()  # truthy model

    client = make_authed_client(analytics.router)
    with patch.dict(sys.modules, {"analysis.nba.ownership_v2": mock_mod}):
        resp = client.post("/analytics/train-ownership-model", params={"site": "DK", "sport": "NBA"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_train_ownership_model_insufficient_data(make_authed_client) -> None:
    import sys
    from unittest.mock import MagicMock
    from routers import analytics

    mock_mod = MagicMock()
    mock_mod.train_ownership_model.return_value = None  # not enough rows

    client = make_authed_client(analytics.router)
    with patch.dict(sys.modules, {"analysis.nba.ownership_v2": mock_mod}):
        resp = client.post("/analytics/train-ownership-model", params={"site": "DK"})
    assert resp.status_code == 200
    assert resp.json()["success"] is False


def test_train_ownership_model_exception_returns_500(make_authed_client) -> None:
    import sys
    from unittest.mock import MagicMock
    from routers import analytics

    mock_mod = MagicMock()
    mock_mod.train_ownership_model.side_effect = RuntimeError("CUDA OOM")

    client = make_authed_client(analytics.router)
    with patch.dict(sys.modules, {"analysis.nba.ownership_v2": mock_mod}):
        resp = client.post("/analytics/train-ownership-model")
    assert resp.status_code == 500
