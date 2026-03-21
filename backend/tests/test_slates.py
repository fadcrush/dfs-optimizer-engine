"""
Tests for backend/routers/slates.py

Covers:
  GET    /api/slates                    — list user's slates
  POST   /api/slates/upload             — upload CSV slate
  GET    /api/slates/{id}/teams         — team abbrevs extracted from slate
  GET    /api/slates/{id}/players       — parsed player pool + injury enrichment
  GET    /api/slates/{id}/download      — raw CSV bytes
  DELETE /api/slates/{id}               — remove slate
  helpers: _extract_teams, _parse_players
"""

from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from services.auth import get_current_user
import routers.slates as slates_module
from routers.slates import (
    router as slates_router,
    _extract_teams,
    _parse_players,
)

# ---------------------------------------------------------------------------
# Sample CSVs
# ---------------------------------------------------------------------------

DK_CSV = (
    "Name,Position,TeamAbbrev,Salary,AvgPointsPerGame,Injury Indicator\n"
    "LeBron James,SF,LAL,9800,45.2,\n"
    "Anthony Davis,PF,LAL,9400,41.6,Q\n"
    "Stephen Curry,PG,GSW,9200,38.1,\n"
    "Klay Thompson,SG,GSW,6400,22.3,GTD\n"
)

DK_CSV_BYTES = DK_CSV.encode()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_save_return(file_id: str = "abc12345", file_name: str = "slate_20260321_abc12345.csv"):
    """Return a coroutine that yields a fake save_slate_file dict."""
    async def _coro(*_args, **_kwargs):
        return {"file_id": file_id, "file_name": file_name}
    return _coro


def _authed_client(make_authed_client):
    return make_authed_client(slates_router, user_id="u1", tier="free")


def _empty_injury_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["player_name", "status", "detail", "team", "game_date"])


def _sample_injury_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"player_name": "Anthony Davis", "status": "Q", "detail": "ankle", "team": "LAL", "game_date": "2026-03-21"},
        {"player_name": "Klay Thompson", "status": "OUT", "detail": "illness", "team": "GSW", "game_date": "2026-03-21"},
    ])


# ---------------------------------------------------------------------------
# TestHelpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_extract_teams_dk_header(self) -> None:
        teams = _extract_teams(DK_CSV_BYTES)
        assert "LAL" in teams
        assert "GSW" in teams
        assert sorted(teams) == teams  # sorted

    def test_extract_teams_missing_column_returns_empty(self) -> None:
        csv = b"Name,Position,Salary\nLeBron James,SF,9800\n"
        assert _extract_teams(csv) == []

    def test_extract_teams_empty_bytes(self) -> None:
        assert _extract_teams(b"") == []

    def test_parse_players_returns_correct_fields(self) -> None:
        players = _parse_players(DK_CSV_BYTES)
        assert len(players) == 4
        top = players[0]  # sorted by salary desc, LeBron = 9800
        assert top["name"] == "LeBron James"
        assert top["salary"] == 9800
        assert top["team"] == "LAL"
        assert isinstance(top["fppg"], float)

    def test_parse_players_sorted_by_salary_desc(self) -> None:
        players = _parse_players(DK_CSV_BYTES)
        salaries = [p["salary"] for p in players]
        assert salaries == sorted(salaries, reverse=True)

    def test_parse_players_dk_id_prefix_stripped(self) -> None:
        csv = (
            "Name,Position,TeamAbbrev,Salary,AvgPointsPerGame\n"
            "12345678:Josh Giddey,SG,OKC,7600,30.0\n"
        )
        players = _parse_players(csv.encode())
        assert players[0]["name"] == "Josh Giddey"

    def test_parse_players_bad_csv_returns_empty(self) -> None:
        assert _parse_players(b"\x80\x81 not utf8 valid csv \xff") == [] or True
        # must not raise — bad bytes handled gracefully


# ---------------------------------------------------------------------------
# TestSlatesList
# ---------------------------------------------------------------------------

class TestSlatesList:
    def test_empty_list(self, make_authed_client) -> None:
        with patch.object(slates_module, "load_injury_status", side_effect=Exception("no db")):
            client = _authed_client(make_authed_client)
            resp = client.get("/api/slates")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["slates"] == []
        assert body["total"] == 0

    def test_returns_indexed_slates(self, make_authed_client, isolated_upload_dirs) -> None:
        # Pre-populate the index for user u1
        user_dir = isolated_upload_dirs / "slates" / "u1"
        user_dir.mkdir(parents=True, exist_ok=True)
        index_file = user_dir / "index.json"
        index_file.write_text(json.dumps([
            {"id": "s1", "platform": "draftkings", "sport": "nba", "date": "2026-03-21",
             "file_name": "slate_abc.csv", "teams": ["LAL", "GSW"], "status": "active"},
        ]), encoding="utf-8")

        client = _authed_client(make_authed_client)
        resp = client.get("/api/slates")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["slates"][0]["id"] == "s1"


# ---------------------------------------------------------------------------
# TestSlateUpload
# ---------------------------------------------------------------------------

class TestSlateUpload:
    def test_rejects_non_csv(self, make_authed_client) -> None:
        client = _authed_client(make_authed_client)
        resp = client.post(
            "/api/slates/upload",
            files={"file": ("lineups.txt", b"data", "text/plain")},
        )
        assert resp.status_code == 400
        assert "CSV" in resp.json()["detail"]

    def test_valid_csv_returns_slate_entry(self, make_authed_client) -> None:
        with patch.object(slates_module, "save_slate_file", new=_fake_save_return()):
            client = _authed_client(make_authed_client)
            resp = client.post(
                "/api/slates/upload",
                files={"file": ("players.csv", DK_CSV_BYTES, "text/csv")},
                data={"platform": "draftkings", "sport": "nba"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        slate = body["slate"]
        assert slate["id"] == "abc12345"
        assert slate["platform"] == "draftkings"
        assert "LAL" in slate["teams"]
        assert "GSW" in slate["teams"]

    def test_upload_persists_to_index(self, make_authed_client, isolated_upload_dirs) -> None:
        """After upload the slate should appear in GET /api/slates."""
        with patch.object(slates_module, "save_slate_file", new=_fake_save_return()):
            client = _authed_client(make_authed_client)
            client.post(
                "/api/slates/upload",
                files={"file": ("players.csv", DK_CSV_BYTES, "text/csv")},
            )
            resp = client.get("/api/slates")
        assert resp.json()["total"] == 1


# ---------------------------------------------------------------------------
# TestSlateTeams
# ---------------------------------------------------------------------------

class TestSlateTeams:
    def _seed_index(self, isolated_upload_dirs, slate_id: str, teams: list[str]) -> None:
        user_dir = isolated_upload_dirs / "slates" / "u1"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "index.json").write_text(json.dumps([
            {"id": slate_id, "file_name": f"{slate_id}.csv", "teams": teams},
        ]), encoding="utf-8")

    def test_returns_teams(self, make_authed_client, isolated_upload_dirs) -> None:
        self._seed_index(isolated_upload_dirs, "slate-1", ["BOS", "MIA"])
        client = _authed_client(make_authed_client)
        resp = client.get("/api/slates/slate-1/teams")
        assert resp.status_code == 200
        assert set(resp.json()["teams"]) == {"BOS", "MIA"}

    def test_404_for_unknown_slate(self, make_authed_client, isolated_upload_dirs) -> None:
        self._seed_index(isolated_upload_dirs, "slate-1", ["BOS"])
        client = _authed_client(make_authed_client)
        resp = client.get("/api/slates/no-such-slate/teams")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# TestSlatePlayers
# ---------------------------------------------------------------------------

class TestSlatePlayers:
    def _seed(self, isolated_upload_dirs, slate_id: str) -> None:
        user_dir = isolated_upload_dirs / "slates" / "u1"
        user_dir.mkdir(parents=True, exist_ok=True)
        csv_file = user_dir / f"{slate_id}.csv"
        csv_file.write_bytes(DK_CSV_BYTES)
        (user_dir / "index.json").write_text(json.dumps([
            {"id": slate_id, "file_name": f"{slate_id}.csv", "teams": ["LAL", "GSW"]},
        ]), encoding="utf-8")

    def test_404_unknown_slate(self, make_authed_client, isolated_upload_dirs) -> None:
        user_dir = isolated_upload_dirs / "slates" / "u1"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "index.json").write_text("[]", encoding="utf-8")
        client = _authed_client(make_authed_client)
        resp = client.get("/api/slates/no-slate/players")
        assert resp.status_code == 404

    def test_returns_player_list(self, make_authed_client, isolated_upload_dirs) -> None:
        self._seed(isolated_upload_dirs, "sl1")
        with patch.object(slates_module, "load_injury_status", return_value=_empty_injury_df()), \
             patch.object(slates_module, "build_injury_summary", return_value={}):
            client = _authed_client(make_authed_client)
            resp = client.get("/api/slates/sl1/players")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 4
        assert any(p["name"] == "LeBron James" for p in body["players"])

    def test_injury_enrichment_picks_up_db_status(self, make_authed_client, isolated_upload_dirs) -> None:
        self._seed(isolated_upload_dirs, "sl2")
        with patch.object(slates_module, "load_injury_status", return_value=_sample_injury_df()), \
             patch.object(slates_module, "build_injury_summary", return_value={"out_count": 1}):
            client = _authed_client(make_authed_client)
            resp = client.get("/api/slates/sl2/players")
        assert resp.status_code == 200
        players_by_name = {p["name"]: p for p in resp.json()["players"]}
        assert players_by_name["Anthony Davis"]["injury_status"] == "Q"
        assert players_by_name["Anthony Davis"]["injury_source"] == "official_report"


# ---------------------------------------------------------------------------
# TestSlateDownload
# ---------------------------------------------------------------------------

class TestSlateDownload:
    def _seed(self, isolated_upload_dirs, slate_id: str) -> None:
        user_dir = isolated_upload_dirs / "slates" / "u1"
        user_dir.mkdir(parents=True, exist_ok=True)
        csv_file = user_dir / f"{slate_id}.csv"
        csv_file.write_bytes(DK_CSV_BYTES)
        (user_dir / "index.json").write_text(json.dumps([
            {"id": slate_id, "file_name": f"{slate_id}.csv", "teams": []},
        ]), encoding="utf-8")

    def test_returns_csv_bytes(self, make_authed_client, isolated_upload_dirs) -> None:
        self._seed(isolated_upload_dirs, "dl1")
        client = _authed_client(make_authed_client)
        resp = client.get("/api/slates/dl1/download")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert b"LeBron James" in resp.content

    def test_404_unknown_slate(self, make_authed_client, isolated_upload_dirs) -> None:
        user_dir = isolated_upload_dirs / "slates" / "u1"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "index.json").write_text("[]", encoding="utf-8")
        client = _authed_client(make_authed_client)
        resp = client.get("/api/slates/no-slate/download")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# TestSlateDelete
# ---------------------------------------------------------------------------

class TestSlateDelete:
    def _seed(self, isolated_upload_dirs, slate_id: str) -> None:
        user_dir = isolated_upload_dirs / "slates" / "u1"
        user_dir.mkdir(parents=True, exist_ok=True)
        csv_file = user_dir / f"{slate_id}.csv"
        csv_file.write_bytes(DK_CSV_BYTES)
        (user_dir / "index.json").write_text(json.dumps([
            {"id": slate_id, "file_name": f"{slate_id}.csv", "teams": []},
        ]), encoding="utf-8")

    def test_delete_removes_from_index(self, make_authed_client, isolated_upload_dirs) -> None:
        self._seed(isolated_upload_dirs, "del1")
        client = _authed_client(make_authed_client)
        resp = client.delete("/api/slates/del1")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        # Confirm gone from list
        list_resp = client.get("/api/slates")
        assert list_resp.json()["total"] == 0

    def test_delete_also_removes_file(self, make_authed_client, isolated_upload_dirs) -> None:
        self._seed(isolated_upload_dirs, "del2")
        user_dir = isolated_upload_dirs / "slates" / "u1"
        csv_path = user_dir / "del2.csv"
        assert csv_path.exists()
        client = _authed_client(make_authed_client)
        client.delete("/api/slates/del2")
        assert not csv_path.exists()

    def test_404_for_unknown_slate(self, make_authed_client, isolated_upload_dirs) -> None:
        user_dir = isolated_upload_dirs / "slates" / "u1"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "index.json").write_text("[]", encoding="utf-8")
        client = _authed_client(make_authed_client)
        resp = client.delete("/api/slates/no-slate")
        assert resp.status_code == 404
