"""
Email Service
=============
Thin wrapper around Python's stdlib ``smtplib`` that reads configuration from
environment variables.  Falls back to logging the email body when SMTP is not
configured so that local development and tests still work without a mail server.

Environment variables
---------------------
SMTP_HOST       Mail server hostname          (e.g. smtp.sendgrid.net)
SMTP_PORT       SMTP port                     (default: 587)
SMTP_USER       SMTP username / API key name  (e.g. apikey for SendGrid)
SMTP_PASS       SMTP password / API key value
SMTP_FROM       Sender address                (e.g. noreply@dfsedge.com)
FRONTEND_URL    Base URL for link generation  (e.g. https://app.dfsedge.com)
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)

_SMTP_HOST = os.getenv("SMTP_HOST", "")
_SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
_SMTP_USER = os.getenv("SMTP_USER", "")
_SMTP_PASS = os.getenv("SMTP_PASS", "")
_SMTP_FROM = os.getenv("SMTP_FROM", "noreply@dfsedge.com")
_FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


def _is_configured() -> bool:
    return bool(_SMTP_HOST and _SMTP_USER and _SMTP_PASS)


def send_email(to: str, subject: str, body_text: str, body_html: str | None = None) -> bool:
    """Send a transactional email.

    Returns True on success, False on failure.  Never raises — email errors
    are logged but must not crash user-facing request flows.
    """
    if not _is_configured():
        log.info(
            "[email] SMTP not configured — logging email instead.\n"
            "  To: %s\n  Subject: %s\n  Body:\n%s",
            to, subject, body_text,
        )
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = _SMTP_FROM
        msg["To"] = to

        msg.attach(MIMEText(body_text, "plain"))
        if body_html:
            msg.attach(MIMEText(body_html, "html"))

        ctx = ssl.create_default_context()
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
            server.ehlo()
            server.starttls(context=ctx)
            server.login(_SMTP_USER, _SMTP_PASS)
            server.sendmail(_SMTP_FROM, to, msg.as_string())

        log.info("[email] Sent '%s' to %s", subject, to)
        return True

    except Exception as exc:
        log.error("[email] Failed to send '%s' to %s: %s", subject, to, exc)
        return False


# ---------------------------------------------------------------------------
# Canned messages
# ---------------------------------------------------------------------------

def send_welcome_email(to: str, full_name: str) -> bool:
    """Send the post-signup welcome email."""
    first = full_name.split()[0] if full_name else "there"
    text = f"""\
Hi {first},

Welcome to DFS Edge Pro!

You're all set to start building better DFS lineups. Here's how to get started:

  1. Upload a DraftKings or FanDuel salary CSV on the Slates tab.
  2. Run projections to see our model's estimates.
  3. Optimize your lineup pool with the Optimizer.

If you have any questions, reply to this email — we read every one.

— The DFS Edge Pro team
{_FRONTEND_URL}
"""
    html = f"""\
<p>Hi {first},</p>
<p>Welcome to <strong>DFS Edge Pro</strong>!</p>
<p>You're all set to start building better DFS lineups. Here's how to get started:</p>
<ol>
  <li>Upload a DraftKings or FanDuel salary CSV on the <b>Slates</b> tab.</li>
  <li>Run projections to see our model's estimates.</li>
  <li>Optimize your lineup pool with the <b>Optimizer</b>.</li>
</ol>
<p>If you have any questions, reply to this email — we read every one.</p>
<p>— The DFS Edge Pro team<br><a href="{_FRONTEND_URL}">{_FRONTEND_URL}</a></p>
"""
    return send_email(to, "Welcome to DFS Edge Pro!", text, html)


def send_password_reset_email(to: str, reset_token: str) -> bool:
    """Send a password-reset link containing *reset_token*."""
    reset_url = f"{_FRONTEND_URL}/auth/reset-password?token={reset_token}"
    text = f"""\
You requested a password reset for your DFS Edge Pro account.

Click the link below within 1 hour to choose a new password:

  {reset_url}

If you did not request this, you can safely ignore this email.
"""
    html = f"""\
<p>You requested a password reset for your DFS Edge Pro account.</p>
<p>Click the button below within 1 hour to choose a new password:</p>
<p><a href="{reset_url}" style="
    display:inline-block;padding:12px 24px;background:#3b82f6;color:#fff;
    text-decoration:none;border-radius:6px;font-weight:bold;">
  Reset Password
</a></p>
<p style="color:#6b7280;font-size:13px;">
  If you did not request this, you can safely ignore this email.
</p>
"""
    return send_email(to, "Reset your DFS Edge Pro password", text, html)
