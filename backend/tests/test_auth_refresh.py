"""
Phase 27 — Tests for POST /auth/refresh endpoint.

Covers:
  - Unauthenticated request → 401
  - Valid user (ORM-style object) → 200 with new token
  - Valid user (dict-style) → 200 with new token
  - Response schema: access_token, token_type, user
  - Refreshed JWT contains correct sub claim
  - Refreshed JWT has fresh ~24-hour expiry
  - Response user dict contains id and email
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

# psycopg2 is not installed in the dev/test environment.
# Stub it so that SQLAlchemy's psycopg2 dialect can be imported and
# database.db can initialise a real engine object (connection attempts fail
# gracefully because our tests override get_current_user and never call get_db).
if "psycopg2" not in sys.modules:
    _psycopg2_stub = MagicMock()
    _psycopg2_stub.__version__ = "2.9.0"
    sys.modules["psycopg2"] = _psycopg2_stub
    sys.modules["psycopg2.extensions"] = MagicMock()
    sys.modules["psycopg2.extras"] = MagicMock()

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.auth import get_current_user, SECRET_KEY, ALGORITHM
from jose import jwt as jose_jwt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _MockUser:
    """Minimal ORM-style user object (mirrors models.user.User attributes)."""
    id = "test-user-abc123"
    email = "tester@dfs-edge.test"
    full_name = "Test User"
    tier = "pro"


def _make_client_with_user(user_obj) -> TestClient:
    from routers.auth import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user_obj
    return TestClient(app, raise_server_exceptions=False)


def _make_client_no_override() -> TestClient:
    from routers.auth import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_refresh_without_auth_returns_401(monkeypatch):
    """No Bearer token → 401 before any DB access."""
    monkeypatch.setenv("DFS_DISABLE_AUTH", "0")
    client = _make_client_no_override()
    resp = client.post("/auth/refresh")
    assert resp.status_code == 401


def test_refresh_with_orm_user_returns_200():
    client = _make_client_with_user(_MockUser())
    resp = client.post("/auth/refresh")
    assert resp.status_code == 200


def test_refresh_response_has_access_token():
    client = _make_client_with_user(_MockUser())
    data = client.post("/auth/refresh").json()
    assert "access_token" in data
    assert isinstance(data["access_token"], str)
    assert len(data["access_token"]) > 20


def test_refresh_response_token_type_is_bearer():
    client = _make_client_with_user(_MockUser())
    data = client.post("/auth/refresh").json()
    assert data["token_type"] == "bearer"


def test_refresh_response_has_user_dict():
    client = _make_client_with_user(_MockUser())
    data = client.post("/auth/refresh").json()
    assert "user" in data
    assert isinstance(data["user"], dict)


def test_refreshed_token_contains_correct_sub_claim():
    user = _MockUser()
    client = _make_client_with_user(user)
    data = client.post("/auth/refresh").json()
    payload = jose_jwt.decode(data["access_token"], SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["sub"] == str(user.id)


def test_refreshed_token_has_fresh_24h_expiry():
    client = _make_client_with_user(_MockUser())
    data = client.post("/auth/refresh").json()
    payload = jose_jwt.decode(data["access_token"], SECRET_KEY, algorithms=[ALGORITHM])
    expected_exp = datetime.utcnow() + timedelta(hours=24)
    actual_exp = datetime.utcfromtimestamp(payload["exp"])
    delta_seconds = abs((expected_exp - actual_exp).total_seconds())
    assert delta_seconds < 60, f"Token expiry delta unexpectedly large: {delta_seconds:.1f}s"


def test_refresh_response_user_contains_id():
    user = _MockUser()
    client = _make_client_with_user(user)
    data = client.post("/auth/refresh").json()
    assert data["user"]["id"] == str(user.id)


def test_refresh_response_user_contains_email():
    user = _MockUser()
    client = _make_client_with_user(user)
    data = client.post("/auth/refresh").json()
    assert data["user"]["email"] == user.email


def test_refresh_with_dict_user_returns_200():
    """Refresh also works when get_current_user returns a plain dict (bypass mode)."""
    dict_user = {
        "id": "dict-user-xyz",
        "email": "dict@dfs-edge.test",
        "full_name": "Dict User",
        "tier": "free",
    }
    client = _make_client_with_user(dict_user)
    resp = client.post("/auth/refresh")
    assert resp.status_code == 200


def test_refresh_dict_user_token_has_correct_sub():
    dict_user = {
        "id": "dict-user-xyz",
        "email": "dict@dfs-edge.test",
        "full_name": "Dict User",
        "tier": "free",
    }
    client = _make_client_with_user(dict_user)
    data = client.post("/auth/refresh").json()
    payload = jose_jwt.decode(data["access_token"], SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["sub"] == "dict-user-xyz"
