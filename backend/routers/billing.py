"""
Billing Router
==============
Stripe-backed subscription management.

Endpoints
---------
POST /billing/subscribe       — Create a Stripe Checkout session (redirect URL)
POST /billing/webhook         — Handle Stripe webhook events
GET  /billing/status          — Current user's subscription status

Environment variables required
-------------------------------
STRIPE_SECRET_KEY       sk_live_... or sk_test_...
STRIPE_PRICE_ID_PRO     price_... (the recurring price for Pro tier)
STRIPE_WEBHOOK_SECRET   whsec_... (from Stripe Dashboard → Webhooks)
FRONTEND_URL            https://app.example.com  (used for success/cancel redirects)
"""

from __future__ import annotations

import logging
import os

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from database.db import get_db
from models.user import User
from services.auth import get_current_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["Billing"])

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
_PRICE_ID_PRO = os.getenv("STRIPE_PRICE_ID_PRO", "")
_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
_FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


def _get_user_row(user_data, db: Session) -> User:
    """Resolve the SQLAlchemy User row from auth token payload."""
    uid = user_data.get("id") if isinstance(user_data, dict) else getattr(user_data, "id", None)
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    row = db.query(User).filter(User.id == uid).first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return row


# ---------------------------------------------------------------------------
# POST /billing/subscribe
# ---------------------------------------------------------------------------

@router.post("/subscribe")
async def create_checkout_session(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a Stripe Checkout session for the Pro tier.

    Returns ``{checkout_url: "https://checkout.stripe.com/..."}`` which the
    frontend should redirect the user to.
    """
    if not stripe.api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing not configured (STRIPE_SECRET_KEY missing).",
        )
    if not _PRICE_ID_PRO:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing not configured (STRIPE_PRICE_ID_PRO missing).",
        )

    user = _get_user_row(current_user, db)

    # Reuse existing Stripe customer or create a new one
    customer_id = user.stripe_customer_id
    if not customer_id:
        try:
            customer = stripe.Customer.create(
                email=user.email,
                metadata={"user_id": user.id},
            )
            customer_id = customer.id
            user.stripe_customer_id = customer_id
            db.commit()
        except stripe.StripeError as exc:
            log.error("Stripe customer create failed: %s", exc)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    try:
        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": _PRICE_ID_PRO, "quantity": 1}],
            mode="subscription",
            success_url=f"{_FRONTEND_URL}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{_FRONTEND_URL}/billing/cancel",
            metadata={"user_id": user.id},
        )
    except stripe.StripeError as exc:
        log.error("Stripe checkout session create failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return {"checkout_url": session.url}


# ---------------------------------------------------------------------------
# GET /billing/status
# ---------------------------------------------------------------------------

@router.get("/status")
async def billing_status(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the current user's subscription tier and Stripe subscription status."""
    user = _get_user_row(current_user, db)
    return {
        "tier": user.tier,
        "subscription_status": user.subscription_status,
        "stripe_customer_id": user.stripe_customer_id,
        "stripe_subscription_id": user.stripe_subscription_id,
    }


# ---------------------------------------------------------------------------
# POST /billing/webhook  (Stripe → backend, no auth header — verified by sig)
# ---------------------------------------------------------------------------

@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receive and verify Stripe webhook events.

    Handles:
    - ``checkout.session.completed``    → activate Pro tier
    - ``customer.subscription.deleted`` → downgrade to free
    - ``invoice.payment_failed``        → mark subscription as past_due
    """
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    if not _WEBHOOK_SECRET:
        log.warning("STRIPE_WEBHOOK_SECRET not set — skipping signature verification")
        try:
            event = stripe.Event.construct_from(
                stripe.util.convert_to_stripe_object(
                    stripe.util.json.loads(payload), stripe.api_key, None
                ),
                stripe.api_key,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid payload: {exc}")
    else:
        try:
            event = stripe.Webhook.construct_event(payload, sig, _WEBHOOK_SECRET)
        except stripe.SignatureVerificationError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        _handle_checkout_completed(data, db)
    elif event_type == "customer.subscription.deleted":
        _handle_subscription_deleted(data, db)
    elif event_type == "invoice.payment_failed":
        _handle_payment_failed(data, db)
    else:
        log.debug("Unhandled Stripe event: %s", event_type)

    return {"received": True}


# ---------------------------------------------------------------------------
# Webhook helpers
# ---------------------------------------------------------------------------

def _handle_checkout_completed(session_obj, db: Session) -> None:
    """Activate Pro tier after successful checkout."""
    user_id = (session_obj.get("metadata") or {}).get("user_id")
    subscription_id = session_obj.get("subscription")
    if not user_id:
        log.warning("checkout.session.completed missing user_id in metadata")
        return
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        log.warning("checkout.session.completed: user %s not found", user_id)
        return
    user.tier = "pro"
    user.subscription_status = "active"
    if subscription_id:
        user.stripe_subscription_id = subscription_id
    db.commit()
    log.info("User %s upgraded to pro (sub=%s)", user_id, subscription_id)


def _handle_subscription_deleted(subscription_obj, db: Session) -> None:
    """Downgrade user to free tier when subscription is cancelled."""
    customer_id = subscription_obj.get("customer")
    if not customer_id:
        return
    user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
    if not user:
        return
    user.tier = "free"
    user.subscription_status = "cancelled"
    user.stripe_subscription_id = None
    db.commit()
    log.info("User %s downgraded to free (sub cancelled)", user.id)


def _handle_payment_failed(invoice_obj, db: Session) -> None:
    """Mark subscription as past_due when payment fails."""
    customer_id = invoice_obj.get("customer")
    if not customer_id:
        return
    user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
    if not user:
        return
    user.subscription_status = "past_due"
    db.commit()
    log.warning("User %s payment failed — marked past_due", user.id)
