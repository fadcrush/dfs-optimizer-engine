"""
Unit tests for:
  - backend/services/email_service.py
  - backend/routers/auth.py — DELETE /auth/me, POST /auth/request-password-reset,
                               POST /auth/reset-password, POST /auth/refresh

Tests use SQLite in-memory DB, mocked SMTP, and mocked email service calls
so they never need a real mail server or network.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

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
from services.auth import (
    create_access_token,
    create_password_reset_token,
    hash_reset_token,
    get_current_user,
)
import services.email_service as email_svc
from routers.auth import router as auth_router


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


@pytest.fixture()
def existing_user(db_session):
    user = User(
        id="auth-test-user-1",
        email="resetme@test.com",
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$fakehash",
        tier="free",
        is_admin=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_auth_client(db_session, user_override=None) -> TestClient:
    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_db] = lambda: db_session
    if user_override is not None:
        app.dependency_overrides[get_current_user] = lambda: user_override
    return TestClient(app, raise_server_exceptions=False)


# ═══════════════════════════════════════════════════════════════════
# email_service
# ═══════════════════════════════════════════════════════════════════

class TestEmailService:

    def test_logs_email_when_smtp_not_configured(self, caplog):
        """When SMTP env vars are absent, send_email logs and returns False."""
        with patch.object(email_svc, "_SMTP_HOST", ""), \
             patch.object(email_svc, "_SMTP_USER", ""), \
             patch.object(email_svc, "_SMTP_PASS", ""):
            import logging
            with caplog.at_level(logging.INFO, logger="services.email_service"):
                result = email_svc.send_email("to@test.com", "Subject", "Body")
        assert result is False

    def test_sends_email_when_configured(self):
        """When SMTP is configured, smtplib.SMTP is called and True is returned."""
        mock_server = MagicMock()
        mock_smtp_cls = MagicMock(return_value=mock_server)
        mock_server.__enter__ = MagicMock(return_value=mock_server)
        mock_server.__exit__ = MagicMock(return_value=False)

        with patch.object(email_svc, "_SMTP_HOST", "smtp.example.com"), \
             patch.object(email_svc, "_SMTP_USER", "apikey"), \
             patch.object(email_svc, "_SMTP_PASS", "secret"), \
             patch("services.email_service.smtplib.SMTP", mock_smtp_cls):
            result = email_svc.send_email(
                "user@example.com",
                "Hello",
                "Text body",
                "<p>HTML body</p>",
            )
        assert result is True
        mock_server.sendmail.assert_called_once()

    def test_send_welcome_calls_send_email(self):
        """send_welcome_email delegates to send_email with correct subject."""
        with patch.object(email_svc, "send_email", return_value=True) as mock_send:
            email_svc.send_welcome_email("new@user.com", "Jane Doe")
        mock_send.assert_called_once()
        subject = mock_send.call_args[0][1]
        assert "Welcome" in subject

    def test_send_password_reset_includes_token_url(self):
        """The reset email body contains the token embedded in the reset URL."""
        with patch.object(email_svc, "send_email", return_value=True) as mock_send, \
             patch.object(email_svc, "_FRONTEND_URL", "https://app.dfsedge.com"):
            email_svc.send_password_reset_email("user@test.com", "abc:123:xyz")
        body_text = mock_send.call_args[0][2]
        assert "abc:123:xyz" in body_text
        assert "https://app.dfsedge.com" in body_text

    def test_smtp_exception_returns_false(self):
        """Network error must not propagate — returns False and logs."""
        mock_smtp_cls = MagicMock(side_effect=ConnectionRefusedError("refused"))
        with patch.object(email_svc, "_SMTP_HOST", "smtp.example.com"), \
             patch.object(email_svc, "_SMTP_USER", "u"), \
             patch.object(email_svc, "_SMTP_PASS", "p"), \
             patch("services.email_service.smtplib.SMTP", mock_smtp_cls):
            result = email_svc.send_email("to@test.com", "S", "B")
        assert result is False


# ═══════════════════════════════════════════════════════════════════
# DELETE /auth/me
# ═══════════════════════════════════════════════════════════════════

class TestDeleteAccount:

    def test_deletes_user_and_returns_message(self, db_session):
        # Create a fresh user for this test
        user = User(
            id="to-delete-1",
            email="delete_me@test.com",
            password_hash="x",
            tier="free",
        )
        db_session.add(user)
        db_session.commit()

        client = _make_auth_client(db_session, {"id": "to-delete-1"})
        response = client.delete("/auth/me")
        assert response.status_code == 200
        assert "deleted" in response.json()["message"].lower()

        # User must be gone from DB
        from models.user import User as U
        assert db_session.query(U).filter(U.id == "to-delete-1").first() is None

    def test_404_when_user_not_in_db(self, db_session):
        client = _make_auth_client(db_session, {"id": "ghost-user-9999"})
        response = client.delete("/auth/me")
        assert response.status_code == 404


# ═══════════════════════════════════════════════════════════════════
# POST /auth/request-password-reset
# ═══════════════════════════════════════════════════════════════════

class TestRequestPasswordReset:

    def test_always_returns_202(self, db_session):
        """No user enumeration — always 202 regardless of whether email exists."""
        client = _make_auth_client(db_session)
        # Non-existent email
        response = client.post(
            "/auth/request-password-reset",
            json={"email": "nobody@nowhere.com"},
        )
        assert response.status_code == 202

    def test_stores_token_hash_for_known_user(self, db_session, existing_user):
        with patch("routers.auth.send_password_reset_email", return_value=True):
            response = _make_auth_client(db_session).post(
                "/auth/request-password-reset",
                json={"email": existing_user.email},
            )
        assert response.status_code == 202

        db_session.refresh(existing_user)
        assert existing_user.password_reset_token_hash is not None
        assert existing_user.password_reset_expires_at is not None

    def test_email_failure_does_not_crash_endpoint(self, db_session, existing_user):
        """Even if the email send fails, endpoint returns 202."""
        with patch("routers.auth.send_password_reset_email", side_effect=RuntimeError("SMTP down")):
            response = _make_auth_client(db_session).post(
                "/auth/request-password-reset",
                json={"email": existing_user.email},
            )
        assert response.status_code == 202


# ═══════════════════════════════════════════════════════════════════
# POST /auth/reset-password
# ═══════════════════════════════════════════════════════════════════

class TestResetPassword:

    def _user_with_reset_token(self, db_session, *, expired: bool = False) -> tuple[User, str]:
        """Create a user and set a valid (or expired) reset token on it."""
        user = User(
            id="reset-user-1",
            email="reset@test.com",
            password_hash="x",
            tier="free",
        )
        db_session.add(user)
        db_session.commit()

        ttl = -3600 if expired else 3600  # negative TTL → token is already expired
        plain_token, expires_at = create_password_reset_token(user.id, ttl_seconds=ttl)
        user.password_reset_token_hash = hash_reset_token(plain_token)
        user.password_reset_expires_at = expires_at
        db_session.commit()
        return user, plain_token

    def test_valid_token_changes_password(self, db_session):
        user, plain_token = self._user_with_reset_token(db_session)
        client = _make_auth_client(db_session)
        response = client.post(
            "/auth/reset-password",
            json={"token": plain_token, "new_password": "NewSecurePass1"},
        )
        assert response.status_code == 200
        assert "Password updated" in response.json()["message"]

        # Token must be cleared (single-use)
        db_session.refresh(user)
        assert user.password_reset_token_hash is None
        assert user.password_reset_expires_at is None

    def test_invalid_token_format_rejected(self, db_session):
        client = _make_auth_client(db_session)
        response = client.post(
            "/auth/reset-password",
            json={"token": "not-a-valid-token", "new_password": "NewSecurePass1"},
        )
        assert response.status_code == 400

    def test_password_too_short_rejected(self, db_session):
        user, plain_token = self._user_with_reset_token(db_session)
        client = _make_auth_client(db_session)
        response = client.post(
            "/auth/reset-password",
            json={"token": plain_token, "new_password": "short"},
        )
        assert response.status_code == 400
        assert "8 characters" in response.json()["detail"]

    def test_expired_token_rejected(self, db_session):
        """Token created with negative TTL is already expired when verified."""
        user, plain_token = self._user_with_reset_token(db_session, expired=True)
        client = _make_auth_client(db_session)
        response = client.post(
            "/auth/reset-password",
            json={"token": plain_token, "new_password": "NewSecurePass1"},
        )
        assert response.status_code == 400


# ═══════════════════════════════════════════════════════════════════
# POST /auth/refresh
# ═══════════════════════════════════════════════════════════════════

class TestRefreshToken:

    def test_valid_token_returns_new_token(self, db_session, existing_user):
        client = _make_auth_client(db_session, existing_user)
        response = client.post("/auth/refresh")
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_refresh_with_dict_user(self, db_session):
        """auth bypass returns user as dict — refresh must handle both types."""
        user_dict = {
            "id": "local-dev-user",
            "email": "dev@local",
            "full_name": "Dev",
            "tier": "admin",
        }
        client = _make_auth_client(db_session, user_dict)
        response = client.post("/auth/refresh")
        assert response.status_code == 200
        assert "access_token" in response.json()
