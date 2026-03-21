"""
Tests for backend/routers/injuries.py

Covers:
  GET   /api/injuries/summary          — current injury report (no auth)
  GET   /api/injuries/team/{abbrev}    — team-filtered injuries (no auth)
  POST  /api/injuries/refresh          — force refresh (auth required)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from services.auth import get_current_user
import routers.injuries as injuries_module
from routers.injuries import router as injuries_router


def _anon_client() -> TestClient:
    """Client with no auth override (endpoints that don't require auth)."""
    app = FastAPI()
    app.include_router(injuries_router)
    return TestClient(app, raise_server_exceptions=False)


def _authed_client(user_id: str = "u1") -> TestClient:
    """Client with auth override for protected endpoints."""
    app = FastAPI()
    app.include_router(injuries_router)
    app.dependency_overrides[get_current_user] = lambda: {"id": user_id, "tier": "free"}
    return TestClient(app, raise_server_exceptions=False)


def _injury_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"player_name": "LeBron James",  "status": "OUT",          "detail": "knee",    "team": "LAL", "game_date": "2026-03-21"},
        {"player_name": "Anthony Davis", "status": "QUESTIONABLE", "detail": "ankle",   "team": "LAL", "game_date": "2026-03-21"},
        {"player_name": "Klay Thompson", "status": "DOUBTFUL",     "detail": "illness", "team": "GSW", "game_date": "2026-03-21"},
        {"player_name": "Draymond Green", "status": "PROBABLE",    "detail": "",        "team": "GSW", "game_date": "2026-03-21"},
    ])


# ---------------------------------------------------------------------------
# TestInjurySummary
# ---------------------------------------------------------------------------

class TestInjurySummary:
    def test_unsupported_sport_returns_empty(self) -> None:
        resp = _anon_client().get("/api/injuries/summary?sport=nfl")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["players"] == []

    def test_load_failure_returns_empty_with_error_field(self) -> None:
        with patch.object(injuries_module, "load_injury_status", side_effect=Exception("db down")):
            resp = _anon_client().get("/api/injuries/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert "error" in body

    def test_correct_counts(self) -> None:
        with patch.object(injuries_module, "load_injury_status", return_value=_injury_df()):
            resp = _anon_client().get("/api/injuries/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 4
        assert body["out_count"] == 1
        assert body["questionable_count"] == 1
        assert body["doubtful_count"] == 1

    def test_players_list_has_required_fields(self) -> None:
        with patch.object(injuries_module, "load_injury_status", return_value=_injury_df()):
            resp = _anon_client().get("/api/injuries/summary")
        player = resp.json()["players"][0]
        for field in ("player_name", "status", "detail", "team", "game_date"):
            assert field in player

    def test_status_is_uppercased(self) -> None:
        df = pd.DataFrame([
            {"player_name": "Test Player", "status": "out", "detail": "", "team": "LAL", "game_date": ""},
        ])
        with patch.object(injuries_module, "load_injury_status", return_value=df):
            resp = _anon_client().get("/api/injuries/summary")
        assert resp.json()["players"][0]["status"] == "OUT"


# ---------------------------------------------------------------------------
# TestTeamInjuries
# ---------------------------------------------------------------------------

class TestTeamInjuries:
    def test_filters_to_requested_team(self) -> None:
        with patch.object(injuries_module, "load_injury_status", return_value=_injury_df()):
            resp = _anon_client().get("/api/injuries/team/LAL")
        assert resp.status_code == 200
        body = resp.json()
        assert body["team"] == "LAL"
        assert body["count"] == 2
        names = {p["player_name"] for p in body["players"]}
        assert names == {"LeBron James", "Anthony Davis"}

    def test_case_insensitive_team_abbrev(self) -> None:
        with patch.object(injuries_module, "load_injury_status", return_value=_injury_df()):
            resp = _anon_client().get("/api/injuries/team/lal")
        assert resp.json()["count"] == 2

    def test_unknown_team_returns_empty(self) -> None:
        with patch.object(injuries_module, "load_injury_status", return_value=_injury_df()):
            resp = _anon_client().get("/api/injuries/team/XYZ")
        body = resp.json()
        assert body["count"] == 0
        assert body["players"] == []

    def test_load_failure_returns_empty_team(self) -> None:
        with patch.object(injuries_module, "load_injury_status", side_effect=Exception("db down")):
            resp = _anon_client().get("/api/injuries/team/LAL")
        assert resp.status_code == 200
        assert resp.json()["players"] == []


# ---------------------------------------------------------------------------
# TestRefresh
# ---------------------------------------------------------------------------

class TestRefresh:
    def test_refresh_requires_auth(self, monkeypatch) -> None:
        # Force real auth checking (DFS_DISABLE_AUTH=1 is set in .env for dev)
        monkeypatch.setenv("DFS_DISABLE_AUTH", "0")
        resp = _anon_client().post("/api/injuries/refresh")
        assert resp.status_code == 401

    def test_refresh_returns_started(self) -> None:
        # Mock the inner thread target so it doesn't try to fetch real data
        with patch.object(injuries_module, "invalidate_cache"):
            resp = _authed_client().post("/api/injuries/refresh")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "started"
        assert "timestamp" in body
