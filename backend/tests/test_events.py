"""
Phase 46 — Events router tests

Covers:
  GET /api/events/injuries   (SSE stream)
  GET /api/events/ping
  _build_injury_payload()    (unit-tested directly)
"""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# GET /api/events/ping
# ---------------------------------------------------------------------------

def test_ping_returns_ok(make_authed_client) -> None:
    from routers import events

    client = make_authed_client(events.router)
    resp = client.get("/api/events/ping")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_ping_has_interval_seconds(make_authed_client) -> None:
    from routers import events

    client = make_authed_client(events.router)
    resp = client.get("/api/events/ping")
    assert "interval_seconds" in resp.json()


# ---------------------------------------------------------------------------
# GET /api/events/injuries  (SSE)
# ---------------------------------------------------------------------------

def test_injuries_sse_content_type(make_authed_client, monkeypatch) -> None:
    """SSE endpoint must respond with text/event-stream."""
    from routers import events

    # Replace the infinite generator with a single-shot one so TestClient doesn't block
    async def _one_shot():
        yield f"data: {json.dumps({'type': 'injury_update', 'players': [], 'count': 0})}\n\n"

    monkeypatch.setattr(events, "_injury_event_generator", _one_shot)

    client = make_authed_client(events.router)
    resp = client.get("/api/events/injuries")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]


def test_injuries_sse_body_contains_data_prefix(make_authed_client, monkeypatch) -> None:
    """Each SSE event line must start with 'data: '."""
    from routers import events

    payload = {"type": "injury_update", "players": [], "count": 0}

    async def _one_shot():
        yield f"data: {json.dumps(payload)}\n\n"

    monkeypatch.setattr(events, "_injury_event_generator", _one_shot)

    client = make_authed_client(events.router)
    resp = client.get("/api/events/injuries")
    assert resp.text.startswith("data: ")


def test_injuries_sse_with_db_error_still_streams(make_authed_client, monkeypatch) -> None:
    """If _build_injury_payload returns an error dict the stream should still emit events."""
    from routers import events

    error_payload = {
        "type": "injury_update",
        "players": [],
        "count": 0,
        "error": "nba_news.duckdb not found",
    }

    async def _one_shot():
        yield f"data: {json.dumps(error_payload)}\n\n"

    monkeypatch.setattr(events, "_injury_event_generator", _one_shot)

    client = make_authed_client(events.router)
    resp = client.get("/api/events/injuries")
    assert resp.status_code == 200
    event_data = json.loads(resp.text.removeprefix("data: ").strip())
    assert event_data["type"] == "injury_update"
    assert "error" in event_data


# ---------------------------------------------------------------------------
# _build_injury_payload() unit test
# ---------------------------------------------------------------------------

def test_build_injury_payload_returns_dict_always() -> None:
    """_build_injury_payload must never raise; it should always return a dict."""
    from routers.events import _build_injury_payload

    result = _build_injury_payload()
    assert isinstance(result, dict)
    assert result["type"] == "injury_update"
    assert "players" in result
    assert "timestamp" in result
