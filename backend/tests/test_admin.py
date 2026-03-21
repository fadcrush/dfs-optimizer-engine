"""
Unit tests for backend/routers/admin.py

Coverage:
  - GET /admin/stats  — non-admin rejected; admin returns correct counts + MRR
  - GET /admin/users  — pagination, tier filter, search, total/pages math
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

if "psycopg2" not in sys.modules:
    _stub = MagicMock()
    _stub.__version__ = "2.9.0"
    sys.modules["psycopg2"] = _stub
    sys.modules["psycopg2.extensions"] = MagicMock()
    sys.modules["psycopg2.extras"] = MagicMock()

from models.user import Base, User
from database.db import get_db
from services.auth import get_current_user, require_admin
from routers.admin import router as admin_router


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def engine():
    """Fresh in-memory SQLite per test — no shared state."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db_session(engine):
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    yield session
    session.close()


def _seed_users(db_session) -> list[User]:
    users = [
        User(id="u1", email="alice@test.com", password_hash="x", tier="free",
             full_name="Alice Free", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
             last_login_at=datetime(2026, 3, 20, tzinfo=timezone.utc)),
        User(id="u2", email="bob@test.com", password_hash="x", tier="pro",
             full_name="Bob Pro", created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
             last_login_at=datetime(2026, 3, 19, tzinfo=timezone.utc)),
        User(id="u3", email="carol@test.com", password_hash="x", tier="elite",
             full_name="Carol Elite", created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
             last_login_at=None),
        User(id="u4", email="dave@test.com", password_hash="x", tier="free",
             full_name="Dave Free", created_at=datetime(2026, 3, 15, tzinfo=timezone.utc),
             last_login_at=None),
    ]
    db_session.add_all(users)
    db_session.commit()
    return users


@pytest.fixture()
def seeded_db(db_session):
    _seed_users(db_session)
    yield db_session
    # No explicit cleanup — function-scoped engine gives a fresh DB per test


def _admin_client(db_session) -> TestClient:
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[require_admin] = lambda: {"id": "admin", "is_admin": True, "tier": "admin"}
    return TestClient(app, raise_server_exceptions=False)


def _non_admin_client(db_session) -> TestClient:
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[get_db] = lambda: db_session
    # Do NOT override require_admin — let it run with a non-admin user dict
    app.dependency_overrides[get_current_user] = lambda: {"id": "user-123", "is_admin": False, "tier": "free"}
    return TestClient(app, raise_server_exceptions=False)


# ─────────────────────────────────────────────────────────────────────────────
# GET /admin/stats
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminStats:

    def test_non_admin_is_rejected(self, seeded_db):
        client = _non_admin_client(seeded_db)
        response = client.get("/admin/stats")
        assert response.status_code == 403

    def test_returns_expected_user_counts(self, seeded_db):
        client = _admin_client(seeded_db)
        response = client.get("/admin/stats")
        assert response.status_code == 200
        data = response.json()
        # 2 free + 1 pro + 1 elite = 4 total
        assert data["total_users"] == 4
        assert data["free_users"] == 2
        assert data["pro_users"] == 2   # pro + elite both counted as pro_users
        assert data["elite_users"] == 1

    def test_mrr_calculation(self, seeded_db):
        """pro=$29, elite=$79 → MRR = (2-1)*29 + 1*79 = 29 + 79 = 108"""
        client = _admin_client(seeded_db)
        response = client.get("/admin/stats")
        assert response.status_code == 200
        assert response.json()["mrr"] == 108


# ─────────────────────────────────────────────────────────────────────────────
# GET /admin/users
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminUsers:

    def test_returns_all_users_default(self, seeded_db):
        client = _admin_client(seeded_db)
        response = client.get("/admin/users")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 4
        assert len(data["users"]) == 4

    def test_tier_filter_free(self, seeded_db):
        client = _admin_client(seeded_db)
        response = client.get("/admin/users?tier=free")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        for u in data["users"]:
            assert u["tier"] == "free"

    def test_tier_filter_pro(self, seeded_db):
        client = _admin_client(seeded_db)
        response = client.get("/admin/users?tier=pro")
        assert response.status_code == 200
        assert response.json()["total"] == 1

    def test_search_by_email(self, seeded_db):
        client = _admin_client(seeded_db)
        response = client.get("/admin/users?search=alice")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["users"][0]["email"] == "alice@test.com"

    def test_search_by_name(self, seeded_db):
        client = _admin_client(seeded_db)
        response = client.get("/admin/users?search=Carol")
        assert response.status_code == 200
        assert response.json()["total"] == 1

    def test_pagination_per_page(self, seeded_db):
        client = _admin_client(seeded_db)
        response = client.get("/admin/users?per_page=2&page=1")
        assert response.status_code == 200
        data = response.json()
        assert len(data["users"]) == 2
        assert data["total"] == 4
        assert data["pages"] == 2

    def test_page_2_returns_remaining(self, seeded_db):
        client = _admin_client(seeded_db)
        response = client.get("/admin/users?per_page=3&page=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data["users"]) == 1

    def test_non_admin_is_rejected(self, seeded_db):
        client = _non_admin_client(seeded_db)
        response = client.get("/admin/users")
        assert response.status_code == 403
