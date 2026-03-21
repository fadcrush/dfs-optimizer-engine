"""
Unit tests for backend/routers/billing.py

Tests use FastAPI TestClient with mocked Stripe calls and an in-memory SQLite
database so they never need a real Stripe account or Postgres connection.

Coverage:
  - GET  /billing/status          — current tier / subscription state
  - POST /billing/subscribe       — missing config, Stripe error, happy path
  - POST /billing/portal          — missing customer, Stripe error, happy path
  - POST /billing/webhook         — checkout.session.completed, subscription.deleted,
                                    invoice.payment_failed, unknown events
"""
from __future__ import annotations

import importlib
import json
import sys
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

# ── psycopg2 stub ────────────────────────────────────────────────────────────
if "psycopg2" not in sys.modules:
    _stub = MagicMock()
    _stub.__version__ = "2.9.0"
    sys.modules["psycopg2"] = _stub
    sys.modules["psycopg2.extensions"] = MagicMock()
    sys.modules["psycopg2.extras"] = MagicMock()

from models.user import Base, User
from database.db import get_db
from services.auth import create_access_token, get_current_user
import routers.billing as billing_module
from routers.billing import router as billing_router


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
def test_user(db_session):
    user = User(
        id="user-billing-1",
        email="billing@test.com",
        password_hash="hashed",
        tier="free",
        subscription_status="active",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def pro_user(db_session):
    user = User(
        id="user-billing-pro",
        email="pro@test.com",
        password_hash="hashed",
        tier="pro",
        subscription_status="active",
        stripe_customer_id="cus_test123",
        stripe_subscription_id="sub_test456",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_client(db_session, user_id: str) -> TestClient:
    app = FastAPI()
    app.include_router(billing_router)

    app.dependency_overrides[get_db] = lambda: db_session
    # Override get_current_user to return a user-dict matching the test user
    app.dependency_overrides[get_current_user] = lambda: {"id": user_id, "tier": "free"}
    return TestClient(app, raise_server_exceptions=False)


# ─────────────────────────────────────────────────────────────────────────────
# GET /billing/status
# ─────────────────────────────────────────────────────────────────────────────

class TestBillingStatus:

    def test_returns_tier_and_status(self, db_session, test_user):
        client = _make_client(db_session, test_user.id)
        response = client.get("/billing/status")
        assert response.status_code == 200
        data = response.json()
        assert data["tier"] == "free"
        assert data["subscription_status"] == "active"
        assert data["stripe_customer_id"] is None

    def test_pro_user_returns_stripe_ids(self, db_session, pro_user):
        client = _make_client(db_session, pro_user.id)
        response = client.get("/billing/status")
        assert response.status_code == 200
        data = response.json()
        assert data["tier"] == "pro"
        assert data["stripe_customer_id"] == "cus_test123"
        assert data["stripe_subscription_id"] == "sub_test456"

    def test_unknown_user_returns_404(self, db_session):
        client = _make_client(db_session, "does-not-exist")
        response = client.get("/billing/status")
        assert response.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# POST /billing/subscribe
# ─────────────────────────────────────────────────────────────────────────────

class TestBillingSubscribe:

    def test_503_when_stripe_key_missing(self, db_session, test_user):
        client = _make_client(db_session, test_user.id)
        with patch.object(billing_module.stripe, "api_key", ""), \
             patch.object(billing_module, "_PRICE_ID_PRO", "price_test"):
            response = client.post("/billing/subscribe")
        assert response.status_code == 503
        assert "STRIPE_SECRET_KEY" in response.json()["detail"]

    def test_503_when_price_id_missing(self, db_session, test_user):
        client = _make_client(db_session, test_user.id)
        with patch.object(billing_module.stripe, "api_key", "sk_test_fake"), \
             patch.object(billing_module, "_PRICE_ID_PRO", ""):
            response = client.post("/billing/subscribe")
        assert response.status_code == 503
        assert "STRIPE_PRICE_ID_PRO" in response.json()["detail"]

    def test_creates_new_stripe_customer_and_session(self, db_session, test_user):
        client = _make_client(db_session, test_user.id)

        mock_customer = MagicMock()
        mock_customer.id = "cus_new_123"

        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/pay/cs_test_abc"

        with patch.object(billing_module.stripe, "api_key", "sk_test_fake"), \
             patch.object(billing_module, "_PRICE_ID_PRO", "price_pro"), \
             patch("routers.billing.stripe.Customer.create", return_value=mock_customer), \
             patch("routers.billing.stripe.checkout.Session.create", return_value=mock_session):
            response = client.post("/billing/subscribe")

        assert response.status_code == 200
        assert response.json()["checkout_url"] == mock_session.url

    def test_reuses_existing_stripe_customer(self, db_session, pro_user):
        client = _make_client(db_session, pro_user.id)

        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/pay/cs_test_xyz"

        with patch.object(billing_module.stripe, "api_key", "sk_test_fake"), \
             patch.object(billing_module, "_PRICE_ID_PRO", "price_pro"), \
             patch("routers.billing.stripe.Customer.create") as mock_create_customer, \
             patch("routers.billing.stripe.checkout.Session.create", return_value=mock_session):
            response = client.post("/billing/subscribe")
            mock_create_customer.assert_not_called()

        assert response.status_code == 200

    def test_stripe_error_returns_502(self, db_session, test_user):
        client = _make_client(db_session, test_user.id)

        with patch.object(billing_module.stripe, "api_key", "sk_test_fake"), \
             patch.object(billing_module, "_PRICE_ID_PRO", "price_pro"), \
             patch("routers.billing.stripe.Customer.create", side_effect=billing_module.stripe.StripeError("network error")):
            response = client.post("/billing/subscribe")

        assert response.status_code == 502


# ─────────────────────────────────────────────────────────────────────────────
# POST /billing/portal
# ─────────────────────────────────────────────────────────────────────────────

class TestBillingPortal:

    def test_400_when_no_stripe_customer(self, db_session, test_user):
        client = _make_client(db_session, test_user.id)
        with patch.object(billing_module.stripe, "api_key", "sk_test_fake"):
            response = client.post("/billing/portal")
        assert response.status_code == 400
        assert "subscribe first" in response.json()["detail"].lower()

    def test_returns_portal_url(self, db_session, pro_user):
        client = _make_client(db_session, pro_user.id)

        mock_portal = MagicMock()
        mock_portal.url = "https://billing.stripe.com/p/session_xyz"

        with patch.object(billing_module.stripe, "api_key", "sk_test_fake"), \
             patch("routers.billing.stripe.billing_portal.Session.create", return_value=mock_portal):
            response = client.post("/billing/portal")

        assert response.status_code == 200
        assert response.json()["portal_url"] == mock_portal.url

    def test_stripe_error_returns_502(self, db_session, pro_user):
        client = _make_client(db_session, pro_user.id)

        with patch.object(billing_module.stripe, "api_key", "sk_test_fake"), \
             patch("routers.billing.stripe.billing_portal.Session.create",
                   side_effect=billing_module.stripe.StripeError("stripe down")):
            response = client.post("/billing/portal")
        assert response.status_code == 502


# ─────────────────────────────────────────────────────────────────────────────
# POST /billing/webhook
# ─────────────────────────────────────────────────────────────────────────────

class _WebhookClient:
    """Thin wrapper to post fake Stripe webhook payloads."""

    def __init__(self, db_session, user_id: str):
        app = FastAPI()
        app.include_router(billing_router)
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_current_user] = lambda: {"id": user_id}
        self._client = TestClient(app, raise_server_exceptions=False)

    def send(self, event_type: str, obj: dict) -> "TestClient":
        payload = {
            "type": event_type,
            "data": {"object": obj},
        }
        return self._client.post(
            "/billing/webhook",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json", "stripe-signature": ""},
        )


class TestStripeWebhook:

    def _make_event(self, db_session, user_id: str, event_type: str, obj: dict):
        """Build and send a webhook event with signature verification bypassed."""
        app = FastAPI()
        app.include_router(billing_router)
        app.dependency_overrides[get_db] = lambda: db_session
        payload_bytes = json.dumps({"type": event_type, "data": {"object": obj}}).encode()
        fake_event = {"type": event_type, "data": {"object": obj}}

        # Bypass sig check by mocking construct_event so we never touch the real
        # Stripe SDK parser (avoids fragile construct_from path with minimal payload).
        with patch.object(billing_module, "_WEBHOOK_SECRET", "whsec_fake"), \
             patch.object(billing_module.stripe.Webhook, "construct_event",
                          return_value=fake_event):
            client = TestClient(app, raise_server_exceptions=False)
            return client.post(
                "/billing/webhook",
                content=payload_bytes,
                headers={
                    "Content-Type": "application/json",
                    "stripe-signature": "t=1,v1=fake",
                },
            )

    def test_checkout_completed_upgrades_user(self, db_session, test_user):
        response = self._make_event(
            db_session, test_user.id,
            "checkout.session.completed",
            {
                "metadata": {"user_id": test_user.id},
                "subscription": "sub_new_999",
            },
        )
        assert response.status_code == 200
        db_session.refresh(test_user)
        assert test_user.tier == "pro"
        assert test_user.stripe_subscription_id == "sub_new_999"

    def test_subscription_deleted_downgrades_user(self, db_session, pro_user):
        response = self._make_event(
            db_session, pro_user.id,
            "customer.subscription.deleted",
            {"customer": "cus_test123"},
        )
        assert response.status_code == 200
        db_session.refresh(pro_user)
        assert pro_user.tier == "free"
        assert pro_user.subscription_status == "cancelled"
        assert pro_user.stripe_subscription_id is None

    def test_payment_failed_marks_past_due(self, db_session, pro_user):
        # Reset tier in case prior test ran first
        pro_user.tier = "pro"
        pro_user.subscription_status = "active"
        db_session.commit()

        response = self._make_event(
            db_session, pro_user.id,
            "invoice.payment_failed",
            {"customer": "cus_test123"},
        )
        assert response.status_code == 200
        db_session.refresh(pro_user)
        assert pro_user.subscription_status == "past_due"

    def test_unknown_event_returns_received(self, db_session, test_user):
        response = self._make_event(
            db_session, test_user.id,
            "customer.updated",
            {"id": "evt_whatever"},
        )
        assert response.status_code == 200
        assert response.json() == {"received": True}
