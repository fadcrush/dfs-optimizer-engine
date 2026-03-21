"""
Tests for backend/routers/tasks.py

Covers:
  GET /api/tasks/{task_id} — poll Celery task state
  States: PENDING→queued, STARTED→running, SUCCESS→done, FAILURE→error, other→state.lower()
  Errors: ImportError (Celery unavailable) → 503, unauthenticated → 401
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from services.auth import get_current_user
from routers.tasks import router as tasks_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_celery_module(state: str, result=None, info=None) -> ModuleType:
    """Return a fake backend.celery_app module whose AsyncResult has the given state."""
    ar = MagicMock()
    ar.state = state
    ar.result = result
    ar.info = info or Exception(f"{state} failure details")

    celery_app = MagicMock()
    celery_app.AsyncResult.return_value = ar

    mod = MagicMock(spec=ModuleType)
    mod.app = celery_app
    return mod


def _authed_client(monkeypatch, celery_mod: ModuleType | None = None) -> TestClient:
    if celery_mod is not None:
        monkeypatch.setitem(sys.modules, "backend.celery_app", celery_mod)

    app = FastAPI()
    app.include_router(tasks_router)
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "tier": "free"}
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# TestTaskStates
# ---------------------------------------------------------------------------

class TestTaskStates:
    def test_pending_maps_to_queued(self, monkeypatch) -> None:
        client = _authed_client(monkeypatch, _make_celery_module("PENDING"))
        resp = client.get("/api/tasks/task-abc")
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"
        assert resp.json()["task_id"] == "task-abc"

    def test_started_maps_to_running(self, monkeypatch) -> None:
        client = _authed_client(monkeypatch, _make_celery_module("STARTED"))
        resp = client.get("/api/tasks/task-abc")
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

    def test_success_maps_to_done_with_result(self, monkeypatch) -> None:
        fake_result = {"lineups": [1, 2, 3], "count": 3}
        client = _authed_client(monkeypatch, _make_celery_module("SUCCESS", result=fake_result))
        resp = client.get("/api/tasks/task-abc")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "done"
        assert body["result"] == fake_result

    def test_failure_maps_to_error_with_message(self, monkeypatch) -> None:
        mod = _make_celery_module("FAILURE", info=Exception("optimizer timed out"))
        client = _authed_client(monkeypatch, mod)
        resp = client.get("/api/tasks/task-abc")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "error"
        assert "optimizer timed out" in body["error"]

    def test_revoked_maps_to_lowercase_state(self, monkeypatch) -> None:
        client = _authed_client(monkeypatch, _make_celery_module("REVOKED"))
        resp = client.get("/api/tasks/task-abc")
        assert resp.status_code == 200
        assert resp.json()["status"] == "revoked"


# ---------------------------------------------------------------------------
# TestTaskErrors
# ---------------------------------------------------------------------------

class TestTaskErrors:
    def test_celery_unavailable_returns_503(self, monkeypatch) -> None:
        # Remove celery_app from sys.modules so the import inside the handler raises ImportError
        monkeypatch.delitem(sys.modules, "backend.celery_app", raising=False)

        app = FastAPI()
        app.include_router(tasks_router)
        app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "tier": "free"}
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/api/tasks/task-abc")
        assert resp.status_code == 503
        assert "Celery" in resp.json()["detail"] or "Redis" in resp.json()["detail"]

    def test_unauthenticated_returns_401(self, monkeypatch) -> None:
        # Force real auth checking (DFS_DISABLE_AUTH=1 is set in .env for dev)
        monkeypatch.setenv("DFS_DISABLE_AUTH", "0")
        monkeypatch.setitem(sys.modules, "backend.celery_app", _make_celery_module("PENDING"))
        app = FastAPI()
        app.include_router(tasks_router)
        # No dependency override → real get_current_user requires JWT
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/tasks/task-abc")
        assert resp.status_code == 401
