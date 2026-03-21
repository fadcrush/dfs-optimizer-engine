"""
Tests for backend/routers/games.py

Covers:
  GET /api/games/today  — NBA matchups with odds, three-tier fallback
  module helpers: _abbr, _fmt_ml, _et_offset, _fmt_time
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

import routers.games as games_module
from routers.games import (
    router as games_router,
    MOCK_GAMES,
    _abbr,
    _fmt_ml,
    _et_offset,
    _fmt_time,
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(games_router)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# TestGetTodayGames
# ---------------------------------------------------------------------------

class TestGetTodayGames:
    def test_no_api_key_returns_mock_games(self) -> None:
        """Without THEODDS_API_KEY and no slate file → MOCK_GAMES."""
        with patch.object(games_module, "_fetch_live_games", return_value=[]), \
             patch.object(games_module, "_build_matchups_from_slate", return_value=[]):
            resp = _client().get("/api/games/today")
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "mock"
        assert body["count"] == len(MOCK_GAMES)
        assert len(body["games"]) == len(MOCK_GAMES)

    def test_live_games_source_is_theodds(self) -> None:
        fake_games = [{"id": "LAL_GSW", "home_team": "Los Angeles Lakers"}]
        with patch.object(games_module, "_fetch_live_games", return_value=fake_games):
            resp = _client().get("/api/games/today")
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "theodds"
        assert body["count"] == 1

    def test_slate_mock_source_when_no_live_but_slate_exists(self) -> None:
        slate_games = [{"id": "BOS_MIA", "home_team": "Boston Celtics", "away_team": "Miami Heat"}]
        with patch.object(games_module, "_fetch_live_games", return_value=[]), \
             patch.object(games_module, "_build_matchups_from_slate", return_value=slate_games):
            resp = _client().get("/api/games/today")
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "slate-mock"
        assert body["count"] == 1

    def test_response_includes_date(self) -> None:
        with patch.object(games_module, "_fetch_live_games", return_value=[]), \
             patch.object(games_module, "_build_matchups_from_slate", return_value=[]):
            resp = _client().get("/api/games/today")
        # date field should be YYYY-MM-DD
        date_str = resp.json()["date"]
        datetime.strptime(date_str, "%Y-%m-%d")  # raises ValueError if malformed


# ---------------------------------------------------------------------------
# TestHelpers
# ---------------------------------------------------------------------------

class TestHelpers:
    # _abbr
    def test_abbr_known_team(self) -> None:
        assert _abbr("Boston Celtics") == "BOS"
        assert _abbr("Los Angeles Lakers") == "LAL"
        assert _abbr("Golden State Warriors") == "GSW"

    def test_abbr_unknown_team_uses_first_three(self) -> None:
        # Fallback: first 3 chars upper
        assert _abbr("Fictional Unicorns") == "FIC"

    # _fmt_ml
    def test_fmt_ml_negative(self) -> None:
        assert _fmt_ml(-180) == "-180"

    def test_fmt_ml_positive(self) -> None:
        assert _fmt_ml(155) == "+155"

    def test_fmt_ml_none(self) -> None:
        assert _fmt_ml(None) == "N/A"

    # _et_offset
    def test_et_offset_edt_summer(self) -> None:
        # July = EDT = -4
        dt = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
        assert _et_offset(dt) == -4

    def test_et_offset_est_winter(self) -> None:
        # January = EST = -5
        dt = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        assert _et_offset(dt) == -5

    # _fmt_time
    def test_fmt_time_valid_iso(self) -> None:
        # 2026-01-15 00:00 UTC = 2025-01-14T19:00 ET (EST -5) → 7:00 PM ET
        result = _fmt_time("2026-01-15T00:00:00Z")
        assert "ET" in result
        assert ":" in result

    def test_fmt_time_invalid_returns_tbd(self) -> None:
        assert _fmt_time("not-a-date") == "TBD"
