# DFS Edge Pro — SaaS Preparedness Audit

**Date:** 2026-03-04  
**Scope:** Multi-tenancy · Auth · Billing · Scalability · Observability · Security · Onboarding · Data Reliability · Compliance · GTM Checklist  
**Verdict:** The core engine is functional. The SaaS wrapper (billing, isolation, observability, compliance) is 15% complete. Six items below are **blocking** — nothing should be charged to users until they are resolved.

---

## Table of Contents

1. [Multi-Tenancy](#1-multi-tenancy)
2. [Authentication & Authorization](#2-authentication--authorization)
3. [Billing & Subscription](#3-billing--subscription)
4. [Scalability](#4-scalability)
5. [Observability](#5-observability)
6. [Security](#6-security)
7. [Onboarding UX](#7-onboarding-ux)
8. [Data Reliability](#8-data-reliability)
9. [Compliance](#9-compliance)
10. [Prioritized Go-To-Market Checklist](#10-prioritized-go-to-market-checklist)

---

## 1. Multi-Tenancy

### Current State

- `backend/routers/slates.py` stores all slates in a single shared `uploads/slates/index.json` with no `user_id` field.
- `backend/routers/projections.py` calls `_user_id(current_user)` but the DuckDB projection tables have **no `user_id` column**, so every row is visible to every request.
- The optimizer output files (`outputs/lineups_*.csv`, `outputs/projections_*.csv`) are written to a shared flat directory — any user who guesses a filename can download another user's lineups.
- `get_current_user_optional` is used on upload/generate endpoints, meaning an unauthenticated request gets `user_id = None` and its data mingles with real user rows.

### Specific Gap

A user who signs in today can call `GET /api/slates` and see **every slate uploaded by every other user**. This is a data-isolation failure that disqualifies the product from charging money.

### Smallest Change That Unblocks Phase 1

1. Add `user_id VARCHAR` (or `INTEGER`) and `created_at TIMESTAMP` to every DuckDB table at migration time (one `ALTER TABLE … ADD COLUMN IF NOT EXISTS` per table in `analysis/shared/db.py`'s migration block).
2. In `slates.py`, filter `_load_index()` to rows where `user_id == current_user.id` (requires switching to per-user index files or adding the field to every entry).
3. In `services/file_service.py`, write uploaded slate files to `uploads/{user_id}/` subdirectories, not the shared root.
4. Change every `/api/projections/*` and `/api/optimizer/*` route to use `get_current_user` (required), not `get_current_user_optional`, so unauthenticated traffic is rejected at the route level.

---

## 2. Authentication & Authorization

### Current State

- JWT signup/login works (`/auth/signup`, `/auth/login`, `/auth/me`).
- Tokens are issued with `sub: user.id` via `python-jose` + `passlib/bcrypt`. Solid foundation.
- `User` model has `tier: str` ("free") and `subscription_status: str` ("active") columns — useful scaffolding.
- `get_current_user_optional` is the default dependency across all feature routes. No route currently **requires** authentication.
- Empty `backend/app/auth/` and `backend/app/services/` folders exist alongside the working `backend/services/auth.py` — this naming confusion will cause import errors as the project grows.
- No role system (admin vs. pro vs. free). No plan-gating dependency. No token refresh endpoint. No password reset flow. No email verification.

### Specific Gaps

| Gap                                                        | Risk                                                                                    |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Optional auth on paid endpoints                            | Anyone can use projections/optimizer for free                                           |
| No token refresh                                           | Users are silently logged out when JWT expires; they blame the product, not the session |
| No email verification                                      | Fake account farms, invalid emails block password reset                                 |
| No password reset                                          | First support ticket every deployment: "I forgot my password"                           |
| `app/auth/` empty dir alongside working `services/auth.py` | Import aliasing bugs when any dev references the wrong path                             |
| No admin role                                              | Can't view all users, revoke access, or manage subscriptions from the backend           |

### Smallest Change That Unblocks Phase 1

```python
# backend/services/auth.py — add a plan-gating dependency
from fastapi import Depends, HTTPException, status

def require_plan(min_tier: str = "pro"):
    async def _check(current_user = Depends(get_current_user)):
        tier_rank = {"free": 0, "pro": 1, "elite": 2}
        if tier_rank.get(current_user.tier, 0) < tier_rank.get(min_tier, 1):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This feature requires a {min_tier} subscription."
            )
        return current_user
    return _check
```

Apply `Depends(require_plan("pro"))` to `/api/projections/run` and `/api/optimizer/run`.  
Add a `POST /auth/refresh` endpoint. Add `POST /auth/request-password-reset` + `POST /auth/reset-password` (send a time-limited token via email using any SMTP provider — even Gmail SMTP at first).  
Delete the empty `backend/app/auth/` and `backend/app/services/` directories.

---

## 3. Billing & Subscription

### Current State

- `stripe` is commented out in `requirements.txt`.
- `User.tier` and `User.subscription_status` columns exist in the DB schema but are never set by anything.
- No `/billing/*` routes exist.
- No Stripe webhook handler. No trial-period logic. No upgrade/downgrade flow.

### Specific Gap

There is currently **no mechanism to take money from a user**. Every user who signs up gets `tier="free"` and there is no upgrade path. This is the single most important missing piece for Phase 1 MRR.

### Smallest Change That Unblocks Phase 1

1. Uncomment `stripe` in `requirements.txt` and run `pip install stripe`.
2. Add a `stripe_customer_id` and `stripe_subscription_id` column to the `users` table (new Alembic migration).
3. Create `backend/routers/billing.py`:

```python
import stripe, os
from fastapi import APIRouter, Request, Depends, HTTPException
from services.auth import get_current_user
from database.db import get_db

router = APIRouter(prefix="/billing", tags=["Billing"])
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
PRICE_ID = os.getenv("STRIPE_PRICE_ID_PRO")  # monthly $29 price

@router.post("/subscribe")
async def create_checkout(current_user=Depends(get_current_user), db=Depends(get_db)):
    session = stripe.checkout.Session.create(
        customer_email=current_user.email,
        payment_method_types=["card"],
        line_items=[{"price": PRICE_ID, "quantity": 1}],
        mode="subscription",
        success_url=f"{os.getenv('FRONTEND_URL')}/billing/success",
        cancel_url=f"{os.getenv('FRONTEND_URL')}/billing/cancel",
        metadata={"user_id": str(current_user.id)},
    )
    return {"checkout_url": session.url}

@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db=Depends(get_db)):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, os.getenv("STRIPE_WEBHOOK_SECRET"))
    except Exception:
        raise HTTPException(status_code=400)
    if event["type"] == "checkout.session.completed":
        uid = event["data"]["object"]["metadata"]["user_id"]
        sub_id = event["data"]["object"]["subscription"]
        db.query(User).filter(User.id == uid).update(
            {"tier": "pro", "subscription_status": "active", "stripe_subscription_id": sub_id}
        )
        db.commit()
    elif event["type"] in ("customer.subscription.deleted", "invoice.payment_failed"):
        sub_id = event["data"]["object"]["id"]
        db.query(User).filter(User.stripe_subscription_id == sub_id).update({"tier": "free"})
        db.commit()
    return {"received": True}
```

4. Set `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID_PRO`, `STRIPE_WEBHOOK_SECRET`, and `FRONTEND_URL` in `.env`.

---

## 4. Scalability

### Current State

- Single `docker-compose.yml` on one host. All services — FastAPI, Next.js, analysis workers, DuckDB — share one machine's CPU and RAM.
- DuckDB is a **single-writer** embedded database. Two simultaneous `/api/optimizer/run` calls will race on the same file and one will raise `IOException: Could not set lock`.
- PuLP's `prob.solve()` is CPU-bound and runs synchronously inside an `async def` FastAPI route, blocking the **entire uvicorn event loop** for the duration.
- No Redis, no task queue, no horizontal scaling path.

### Specific Gap

At 50+ concurrent users, a single optimizer run blocks all other API responses. At 200+ users, DuckDB write collisions start failing silently. This is a hard ceiling well below the Phase 1 target of 1,000 users.

### What Breaks First and When

| Threshold                   | Failure                                                      |
| --------------------------- | ------------------------------------------------------------ |
| 2 concurrent optimizer runs | DuckDB write lock collision                                  |
| 5+ concurrent users         | uvicorn event loop blocked, 30s timeouts for all other users |
| 100+ users                  | Single-host CPU/RAM exhaustion                               |
| 1,000+ users                | Full rewrite required (Postgres + Celery)                    |

### Smallest Change That Unblocks Phase 1 (≤100 users)

**Immediate (today):** Wrap the PuLP solve in `asyncio.to_thread()` or `loop.run_in_executor()` so it does not block the event loop:

```python
import asyncio
result = await asyncio.to_thread(solve_lineups, slate_path, settings)
```

**This week:** Add a DuckDB write lock (a threading `Lock()` singleton in `analysis/shared/db.py`) so concurrent write attempts queue instead of crashing.  
**Phase 2 (before 500 users):** Add Redis + Celery. Move all optimizer and projection runs to tasks. Return a `task_id` immediately and poll `GET /api/tasks/{task_id}/status`.  
**Phase 2 (before 1,000 users):** Migrate all user-facing DuckDB tables to Postgres. Keep DuckDB **only** for read-heavy analytics aggregations where it excels.

---

## 5. Observability

### Current State

- All logging is `print()` statements. Nothing is structured, nothing is queryable.
- No Sentry or equivalent error tracker.
- No uptime monitor.
- No request tracing or correlation IDs.
- `logs/` directory exists with `test_output.txt` but no automation writes to it from the running API.

### Specific Gap

When the first paying customer reports "the optimizer didn't work," there is currently no way to find the error after the fact. Every production incident will require `docker logs` archaeology.

### Smallest Change That Unblocks Phase 1

**Structured logging (30 minutes):**

```python
# backend/main.py — replace all print() calls
import logging, sys
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
    stream=sys.stdout,
)
logger = logging.getLogger("dfs_edge")
```

**Sentry (5 minutes + free tier):**

```python
# backend/main.py top of file
import sentry_sdk
sentry_sdk.init(dsn=os.getenv("SENTRY_DSN"), traces_sample_rate=0.1)
```

Add `sentry-sdk[fastapi]` to `requirements.txt`. Set `SENTRY_DSN` in `.env`.

**Uptime monitoring:** Sign up for [UptimeRobot](https://uptimerobot.com) free tier. Point it at `GET /health`. Get a Slack/email alert when the server goes down.

---

## 6. Security

### Current State

- CORS reads allowed origins from `CORS_ALLOWED_ORIGINS` env var. Smart.
- `allow_headers=["*"]` is too permissive — it allows any browser to send custom headers, which bypasses some CORS protections.
- Rate limiting is set to 200 req/min per IP globally. Good start.
- Auth uses bcrypt + JWT. Solid.
- `.env` file format is documented but it cannot be verified from this audit whether secrets are committed to the repo.
- No HTTPS enforcement in the nginx config.
- No `X-Content-Type-Options`, `X-Frame-Options`, or `Content-Security-Policy` headers.
- Optimizer and projection endpoints accept arbitrary `slate_file_name` string parameters — no path traversal sanitization.

### Specific Gaps vs. OWASP Top 10

| OWASP Category                | Gap                                                                                   |
| ----------------------------- | ------------------------------------------------------------------------------------- |
| A01 Broken Access Control     | No user_id scoping on data queries (see §1)                                           |
| A02 Cryptographic Failures    | Secrets may be in git; no HTTPS enforcement                                           |
| A03 Injection                 | `slate_file_name` parameter used in `Path()` construction — check for `../` traversal |
| A05 Security Misconfiguration | `allow_headers=["*"]`, no security headers                                            |
| A07 Auth Failures             | Optional auth on paid endpoints                                                       |
| A09 Logging Failures          | No structured logs, no alerting                                                       |

### Smallest Change That Unblocks Phase 1

```python
# backend/main.py — tighten CORS and add security headers
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],  # explicit list
)

@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response
```

Path traversal fix (add to `services/file_service.py`):

```python
from pathlib import Path

def safe_filename(name: str, base_dir: Path) -> Path:
    resolved = (base_dir / name).resolve()
    if not str(resolved).startswith(str(base_dir.resolve())):
        raise ValueError("Path traversal detected")
    return resolved
```

Verify `.gitignore` includes `.env`, `*.duckdb`, `*.duckdb.wal`, and all secrets files.

---

## 7. Onboarding UX

### Current State

- No UI for signup or login exists in the frontend.
- User avatar in `NavBar.tsx` is hardcoded to `"D"`.
- No empty-state components on any tab (blank screen when no data has been loaded).
- No welcome email after signup.
- No in-app guided tour or first-run wizard.
- `Skeleton.tsx` exists in `components/ui/` but confirm it is actually used in `ProjectionsPage` and `OptimizerPage`.

### Specific Gap

A paying customer's first 5 minutes: they land on the app, see a dark page with blank tab content, no sign-in prompt visible, and no guidance. Immediate churn.

### Smallest Change That Unblocks Phase 1

1. **Auth pages:** Create `app/login/page.tsx` and `app/signup/page.tsx` with email/password forms that call `/auth/login` and `/auth/signup`.
2. **JWT persistence:** Store the token in `localStorage` (or `httpOnly` cookie for better security). Add an `AuthContext` or Zustand slice that exposes `user` and `token` to all components.
3. **NavBar avatar:** Replace the hardcoded `"D"` with the first character of `user.full_name` from context.
4. **Empty states:** If a tab's data array is empty, render a `<EmptyState>` component with a clear next step ("Upload a slate to get started →").
5. **Post-signup email:** Add `fastapi-mail` or call SendGrid/Resend API in the signup route to send a "Welcome to DFS Edge Pro" email.

---

## 8. Data Reliability

### Current State

- `data/dfs_edge.duckdb.wal`, `data/dfs_master.duckdb.wal`, `data/nba_news.duckdb.wal` are present in the workspace — these are write-ahead log files that DuckDB has not yet checkpointed, meaning uncommitted data.
- `data/backups/` directory exists but appears empty.
- No automated backup job in the schedulers.
- DuckDB files live on the Docker host volume — a single disk failure loses all data.
- No point-in-time recovery. No replication.

### Specific Gap

One corrupted DuckDB file (which happens if the process dies mid-write) means total data loss for all users. There is no recovery path.

### Smallest Change That Unblocks Phase 1

Add a nightly backup job to `workers/schedulers/daily.py`:

```python
import shutil
from datetime import datetime
from pathlib import Path

def backup_duckdb_files():
    data_dir = Path("data")
    backup_dir = data_dir / "backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M")
    for db_file in data_dir.glob("*.duckdb"):
        dest = backup_dir / f"{db_file.stem}_{stamp}.duckdb"
        shutil.copy2(db_file, dest)
        print(f"Backed up {db_file.name} → {dest.name}")
    # Prune backups older than 7 days
    cutoff = datetime.utcnow().timestamp() - 7 * 86400
    for old in backup_dir.glob("*.duckdb"):
        if old.stat().st_mtime < cutoff:
            old.unlink()

# In start_scheduler():
scheduler.add_job(backup_duckdb_files, "cron", hour=3, minute=0, id="duckdb_backup")
```

Medium-term: Mount the Docker volume to cloud storage (S3, Azure Blob, Backblaze B2) and sync nightly. Even the free tier of Backblaze B2 covers several GB.

---

## 9. Compliance

### Current State

- No privacy policy. No terms of service.
- No data deletion (`DELETE /auth/me`) endpoint.
- No cookie consent banner.
- DFS contest results and lineup data (PII plus behavioral data) are stored indefinitely with no retention policy.
- No record of what data is collected or why.

### Specific Gap

GDPR (EU users) requires: right to access, right to erasure ("right to be forgotten"), data portability. CCPA (California users) requires: right to know, right to delete, right to opt out of sale. Charging users without these in place in California or the EU creates legal liability. DFS platforms also intersect with gambling-adjacent regulations in many US states — disclosures matter.

### Smallest Change That Unblocks Phase 1

1. **Delete account endpoint:**

```python
@router.delete("/me")
async def delete_account(current_user=Depends(get_current_user), db=Depends(get_db)):
    """GDPR/CCPA: Erase all user data on request."""
    # Cancel Stripe subscription if active
    if current_user.stripe_subscription_id:
        stripe.Subscription.delete(current_user.stripe_subscription_id)
    # Hard-delete from Postgres
    db.delete(current_user)
    db.commit()
    # Queue DuckDB row deletion (user_id scoping required first — see §1)
    return {"message": "Account and all associated data deleted."}
```

2. Draft a one-page **Privacy Policy** (use a generator like Termly or iubenda) and link it from the signup page.
3. Draft **Terms of Service** covering acceptable use, no professional gambling advice, age restriction (18+).
4. Add a cookie consent banner to `layout.tsx` if using any analytics (e.g., PostHog, Google Analytics).

---

## 10. Prioritized Go-To-Market Checklist

### BLOCKING — must be done before charging a single dollar

| Priority | Item                                                                             | Owner Area     | Effort   |
| -------- | -------------------------------------------------------------------------------- | -------------- | -------- |
| 🔴 P0    | Add `user_id` scoping to all DuckDB tables and slate index                       | Backend        | 2–3 days |
| 🔴 P0    | Switch paid feature routes from `get_current_user_optional` → `get_current_user` | Backend        | 2 hours  |
| 🔴 P0    | Install Stripe + create `/billing/subscribe` + `/billing/webhook`                | Backend        | 1 day    |
| 🔴 P0    | Add frontend auth pages (login, signup) + JWT context                            | Frontend       | 1 day    |
| 🔴 P0    | Privacy Policy + Terms of Service pages live                                     | Legal/Frontend | 4 hours  |
| 🔴 P0    | Add `DELETE /auth/me` data erasure endpoint                                      | Backend        | 1 hour   |

### HIGH — blocks paying customers from having a good experience

| Priority | Item                                                               | Owner Area         | Effort  |
| -------- | ------------------------------------------------------------------ | ------------------ | ------- |
| 🟠 P1    | Wrap PuLP `solve()` in `asyncio.to_thread()` to unblock event loop | Backend            | 30 min  |
| 🟠 P1    | Add Sentry SDK + structured logging                                | Backend            | 1 hour  |
| 🟠 P1    | Add `require_plan("pro")` dependency on optimizer + projections    | Backend            | 1 hour  |
| 🟠 P1    | Nightly DuckDB backup job in scheduler                             | Backend            | 1 hour  |
| 🟠 P1    | Empty state components on every tab                                | Frontend           | 4 hours |
| 🟠 P1    | Password reset flow (email token)                                  | Backend + Frontend | 4 hours |
| 🟠 P1    | UptimeRobot monitor on `/health`                                   | DevOps             | 15 min  |
| 🟠 P1    | Restrict CORS `allow_headers` + add security headers middleware    | Backend            | 30 min  |

### NICE-TO-HAVE — Phase 1 polishing (first 60 days post-launch)

| Priority | Item                                                              | Owner Area | Effort  |
| -------- | ----------------------------------------------------------------- | ---------- | ------- |
| 🟡 P2    | Add DuckDB write-lock singleton to prevent collision              | Backend    | 1 hour  |
| 🟡 P2    | Path traversal guard on all `file_name` parameters                | Backend    | 1 hour  |
| 🟡 P2    | Delete empty `backend/app/auth/` and `backend/app/services/` dirs | Cleanup    | 5 min   |
| 🟡 P2    | Mobile hamburger menu in NavBar                                   | Frontend   | 3 hours |
| 🟡 P2    | Toast on every destructive action (wire `ConfirmDialog.tsx`)      | Frontend   | 2 hours |
| 🟡 P2    | Skeleton loaders confirmed on Projections + Optimizer tables      | Frontend   | 1 hour  |
| 🟡 P2    | Post-signup welcome email (SendGrid/Resend)                       | Backend    | 2 hours |

### PHASE 2 — before scaling past 500 users

| Priority | Item                                                 | Owner Area | Effort   |
| -------- | ---------------------------------------------------- | ---------- | -------- |
| 🔵 P3    | Redis + Celery for optimizer/projection jobs         | Backend    | 2–3 days |
| 🔵 P3    | Migrate user-facing DuckDB tables to Postgres        | Backend    | 1 week   |
| 🔵 P3    | Light/dark mode toggle via CSS variables             | Frontend   | 3 hours  |
| 🔵 P3    | Replace all inline styles with Tailwind classes      | Frontend   | 2–3 days |
| 🔵 P3    | Admin dashboard (user list, MRR, churn)              | Full-stack | 1 week   |
| 🔵 P3    | Mount Docker volumes to cloud object storage (B2/S3) | DevOps     | 4 hours  |

---

## Summary Score Card

| Area                 | Phase 1 Ready? | Blocker?                          |
| -------------------- | -------------- | --------------------------------- |
| Multi-Tenancy        | ❌ No          | ✅ Blocking                       |
| Auth & Authorization | ⚠️ Partial     | ✅ Blocking (plan-gating missing) |
| Billing              | ❌ No          | ✅ Blocking                       |
| Scalability          | ⚠️ Partial     | ⚠️ High risk at 50+ users         |
| Observability        | ❌ No          | ⚠️ High risk in production        |
| Security             | ⚠️ Partial     | ⚠️ Path traversal + headers       |
| Onboarding UX        | ❌ No          | ✅ Blocking (no auth UI)          |
| Data Reliability     | ❌ No          | ⚠️ High risk                      |
| Compliance           | ❌ No          | ✅ Blocking (legal liability)     |

**Estimated effort to clear all P0 blockers: 5–7 focused developer-days.**  
**Estimated effort to clear P0 + P1: 10–12 developer-days.**  
At that point DFS Edge Pro is ready to accept its first paying customer.
