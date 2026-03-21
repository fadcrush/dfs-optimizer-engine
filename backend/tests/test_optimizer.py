"""
Phase 45 — Optimizer router tests

Covers:
  POST /api/optimizer/run
  POST /api/optimizer/run-async
  POST /api/optimizer/parse-lineup-csv

Late-swap and batch-late-swap endpoint coverage is already in test_late_swap.py.
"""
from __future__ import annotations

import sys
from io import BytesIO
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Shared fake helpers
# ---------------------------------------------------------------------------

_DK_SLATE_BYTES = (
    b"Name,Position,Salary,AvgPointsPerGame,TeamAbbrev,ID\n"
    b"LeBron James,F,9000,45.5,LAL,11111111\n"
    b"Nikola Jokic,C,10000,50.0,DEN,22222222\n"
    b"Kyrie Irving,PG,7500,38.0,DAL,33333333\n"
    b"Kevin Durant,SF,9500,47.0,PHX,44444444\n"
    b"Stephen Curry,PG,9800,49.0,GSW,55555555\n"
    b"Giannis Antetokounmpo,PF,11000,55.0,MIL,66666666\n"
    b"Jayson Tatum,SF,9200,48.0,BOS,77777777\n"
    b"Jimmy Butler,SF,7800,35.0,MIA,88888888\n"
)

_PLAYERS = [
    ("LeBron James",           "SF", 9000,  45.5, "11111111"),
    ("Nikola Jokic",           "C",  10000, 50.0, "22222222"),
    ("Kyrie Irving",           "PG", 7500,  38.0, "33333333"),
    ("Kevin Durant",           "SF", 9500,  47.0, "44444444"),
    ("Stephen Curry",          "PG", 9800,  49.0, "55555555"),
    ("Giannis Antetokounmpo",  "PF", 11000, 55.0, "66666666"),
    ("Jayson Tatum",           "SF", 9200,  48.0, "77777777"),
    ("Jimmy Butler",           "SF", 7800,  35.0, "88888888"),
]


def _fake_lineups_df(n_lineups: int = 2) -> pd.DataFrame:
    rows = []
    for i in range(n_lineups):
        for name, pos, sal, proj, did in _PLAYERS:
            rows.append({
                "LineupIndex": i,
                "Name": name,
                "Pos": pos,
                "Salary": sal,
                "Proj": proj,
                "Own": 15.0,
                "DFS_ID": did,
            })
    return pd.DataFrame(rows)


def _fake_pipeline(*args, **kwargs) -> dict:
    return {
        "lineups_df": _fake_lineups_df(2),
        "site": "DK",
        "filter_report": {},
        "replacement_boosts": [],
    }


def _fake_pipeline_empty(*args, **kwargs) -> dict:
    return {
        "lineups_df": pd.DataFrame(),
        "site": "DK",
        "filter_report": {},
        "replacement_boosts": [],
    }


async def _fake_save_slate(file, user_id: str = ""):
    return {"file_path": "fake_slate.csv", "file_id": "abc12345", "file_name": "slate.csv"}


async def _fake_save_lineup(csv_data: str, file_id: str, user_id: str = ""):
    return {"file_name": "lineups_abc12345.csv"}


def _fake_assign_slots(group: pd.DataFrame, site: str) -> dict:
    """Return a predictable DK-style slot assignment."""
    names = list(group["Name"])
    dk_slots = ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"]
    return {slot: name for slot, name in zip(dk_slots, names)}


# ---------------------------------------------------------------------------
# POST /api/optimizer/run
# ---------------------------------------------------------------------------

def test_run_rejects_non_csv(make_authed_client) -> None:
    from routers import optimizer

    client = make_authed_client(optimizer.router)
    resp = client.post(
        "/api/optimizer/run",
        files={"file": ("slate.txt", BytesIO(b"data"), "text/plain")},
    )
    assert resp.status_code == 400
    assert "CSV" in resp.json()["detail"]


def test_run_free_tier_returns_403(make_authed_client) -> None:
    from routers import optimizer

    client = make_authed_client(optimizer.router, tier="free")
    resp = client.post(
        "/api/optimizer/run",
        files={"file": ("slate.csv", BytesIO(_DK_SLATE_BYTES), "text/csv")},
    )
    assert resp.status_code == 403


def test_run_empty_lineups_returns_500(make_authed_client, monkeypatch) -> None:
    from routers import optimizer

    monkeypatch.setattr(optimizer, "save_slate_file", _fake_save_slate)
    monkeypatch.setattr(optimizer, "run_dfs_pipeline", _fake_pipeline_empty)
    monkeypatch.setattr(optimizer, "assign_lineup_slots", _fake_assign_slots)

    client = make_authed_client(optimizer.router)
    resp = client.post(
        "/api/optimizer/run",
        files={"file": ("slate.csv", BytesIO(_DK_SLATE_BYTES), "text/csv")},
        params={"site": "DK"},
    )
    assert resp.status_code == 500


def test_run_happy_path_returns_lineups(make_authed_client, monkeypatch) -> None:
    from routers import optimizer

    monkeypatch.setattr(optimizer, "save_slate_file", _fake_save_slate)
    monkeypatch.setattr(optimizer, "run_dfs_pipeline", _fake_pipeline)
    monkeypatch.setattr(optimizer, "save_lineup_file", _fake_save_lineup)
    monkeypatch.setattr(optimizer, "assign_lineup_slots", _fake_assign_slots)

    client = make_authed_client(optimizer.router)
    resp = client.post(
        "/api/optimizer/run",
        files={"file": ("slate.csv", BytesIO(_DK_SLATE_BYTES), "text/csv")},
        params={"site": "DK"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["total_lineups"] == 2
    assert data["download_file"] == "lineups_abc12345.csv"
    assert len(data["lineups"]) == 2


def test_run_stats_fields_present(make_authed_client, monkeypatch) -> None:
    from routers import optimizer

    monkeypatch.setattr(optimizer, "save_slate_file", _fake_save_slate)
    monkeypatch.setattr(optimizer, "run_dfs_pipeline", _fake_pipeline)
    monkeypatch.setattr(optimizer, "save_lineup_file", _fake_save_lineup)
    monkeypatch.setattr(optimizer, "assign_lineup_slots", _fake_assign_slots)

    client = make_authed_client(optimizer.router)
    resp = client.post(
        "/api/optimizer/run",
        files={"file": ("slate.csv", BytesIO(_DK_SLATE_BYTES), "text/csv")},
        params={"site": "DK"},
    )
    stats = resp.json()["stats"]
    assert "avg_projection" in stats
    assert "avg_salary" in stats
    assert "projection_range" in stats


def test_run_invalid_projection_overrides_json_is_graceful(make_authed_client, monkeypatch) -> None:
    from routers import optimizer

    monkeypatch.setattr(optimizer, "save_slate_file", _fake_save_slate)
    monkeypatch.setattr(optimizer, "run_dfs_pipeline", _fake_pipeline)
    monkeypatch.setattr(optimizer, "save_lineup_file", _fake_save_lineup)
    monkeypatch.setattr(optimizer, "assign_lineup_slots", _fake_assign_slots)

    client = make_authed_client(optimizer.router)
    resp = client.post(
        "/api/optimizer/run",
        files={"file": ("slate.csv", BytesIO(_DK_SLATE_BYTES), "text/csv")},
        params={"site": "DK", "projection_overrides": "not-valid-json{{"},
    )
    # Bad JSON is silently ignored — endpoint still succeeds
    assert resp.status_code == 200


def test_run_simulation_summary_included_when_sim_df_present(make_authed_client, monkeypatch) -> None:
    from routers import optimizer

    def _pipeline_with_sim(*args, **kwargs) -> dict:
        result = _fake_pipeline()
        result["simulation_df"] = pd.DataFrame([
            {"LineupIndex": 0, "EV": 3.14, "ROI": 0.5, "Mean": 220.5},
            {"LineupIndex": 1, "EV": 2.80, "ROI": 0.4, "Mean": 215.0},
        ])
        return result

    monkeypatch.setattr(optimizer, "save_slate_file", _fake_save_slate)
    monkeypatch.setattr(optimizer, "run_dfs_pipeline", _pipeline_with_sim)
    monkeypatch.setattr(optimizer, "save_lineup_file", _fake_save_lineup)
    monkeypatch.setattr(optimizer, "assign_lineup_slots", _fake_assign_slots)

    client = make_authed_client(optimizer.router)
    resp = client.post(
        "/api/optimizer/run",
        files={"file": ("slate.csv", BytesIO(_DK_SLATE_BYTES), "text/csv")},
        params={"site": "DK"},
    )
    assert resp.status_code == 200
    assert "simulation_summary" in resp.json()


def test_run_exposure_report_included_when_present(make_authed_client, monkeypatch) -> None:
    from routers import optimizer

    def _pipeline_with_exposure(*args, **kwargs) -> dict:
        result = _fake_pipeline()
        result["exposure_report"] = pd.DataFrame([
            {
                "Name": "LeBron James", "Team": "LAL", "Pos": "SF",
                "Salary": 9000, "Proj": 45.5, "Own": 15.0, "ActualPct": 0.50,
            }
        ])
        return result

    monkeypatch.setattr(optimizer, "save_slate_file", _fake_save_slate)
    monkeypatch.setattr(optimizer, "run_dfs_pipeline", _pipeline_with_exposure)
    monkeypatch.setattr(optimizer, "save_lineup_file", _fake_save_lineup)
    monkeypatch.setattr(optimizer, "assign_lineup_slots", _fake_assign_slots)

    client = make_authed_client(optimizer.router)
    resp = client.post(
        "/api/optimizer/run",
        files={"file": ("slate.csv", BytesIO(_DK_SLATE_BYTES), "text/csv")},
        params={"site": "DK"},
    )
    assert resp.status_code == 200
    assert "exposure_report" in resp.json()


# ---------------------------------------------------------------------------
# POST /api/optimizer/run-async
# ---------------------------------------------------------------------------

def test_run_async_rejects_non_csv(make_authed_client) -> None:
    from routers import optimizer

    client = make_authed_client(optimizer.router)
    resp = client.post(
        "/api/optimizer/run-async",
        files={"file": ("slate.txt", BytesIO(b"data"), "text/plain")},
    )
    assert resp.status_code == 400
    assert "CSV" in resp.json()["detail"]


def test_run_async_free_tier_returns_403(make_authed_client) -> None:
    from routers import optimizer

    client = make_authed_client(optimizer.router, tier="free")
    resp = client.post(
        "/api/optimizer/run-async",
        files={"file": ("slate.csv", BytesIO(_DK_SLATE_BYTES), "text/csv")},
    )
    assert resp.status_code == 403


def test_run_async_celery_unavailable_returns_503(make_authed_client, monkeypatch) -> None:
    from routers import optimizer

    monkeypatch.setattr(optimizer, "save_slate_file", _fake_save_slate)
    # Remove any cached tasks module so the import in the endpoint encounters the
    # stub that either fails or whose apply_async raises (no live Redis in CI).
    # Forcing the module to None makes Python raise ImportError immediately.
    with patch.dict(sys.modules, {"backend.tasks.optimizer": None}):
        client = make_authed_client(optimizer.router)
        resp = client.post(
            "/api/optimizer/run-async",
            files={"file": ("slate.csv", BytesIO(_DK_SLATE_BYTES), "text/csv")},
        )
    assert resp.status_code == 503


def test_run_async_happy_path_returns_task_id(make_authed_client, monkeypatch) -> None:
    from routers import optimizer

    monkeypatch.setattr(optimizer, "save_slate_file", _fake_save_slate)

    mock_task = MagicMock()
    mock_task.id = "celery-task-abc123"
    mock_tasks_module = MagicMock()
    mock_tasks_module.run_optimizer_task.apply_async.return_value = mock_task

    with patch.dict(sys.modules, {"backend.tasks.optimizer": mock_tasks_module}):
        client = make_authed_client(optimizer.router)
        resp = client.post(
            "/api/optimizer/run-async",
            files={"file": ("slate.csv", BytesIO(_DK_SLATE_BYTES), "text/csv")},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "queued"
    assert data["task_id"] == "celery-task-abc123"


# ---------------------------------------------------------------------------
# POST /api/optimizer/parse-lineup-csv
# ---------------------------------------------------------------------------

_DK_SLATE_CSV = (
    "Name,Position,Salary,AvgPointsPerGame,TeamAbbrev,ID\n"
    "LeBron James,F,9000,45.5,LAL,11111111\n"
    "Nikola Jokic,C,10000,50.0,DEN,22222222\n"
    "Kyrie Irving,PG,7500,38.0,DAL,33333333\n"
    "Kevin Durant,SF,9500,47.0,PHX,44444444\n"
    "Stephen Curry,PG,9800,49.0,GSW,55555555\n"
    "Giannis Antetokounmpo,PF,11000,55.0,MIL,66666666\n"
    "Jayson Tatum,SF,9200,48.0,BOS,77777777\n"
    "Jimmy Butler,SF,7800,35.0,MIA,88888888\n"
)

# DK entry CSV in "Name (ID)" format
_DK_ENTRY_NAME_ID_CSV = (
    "Entry ID,Contest Name,Contest ID,Entry Fee,PG,SG,SF,PF,C,G,F,UTIL\n"
    ",,,,Kyrie Irving (33333333),LeBron James (11111111),"
    "Kevin Durant (44444444),Giannis Antetokounmpo (66666666),"
    "Nikola Jokic (22222222),Stephen Curry (55555555),"
    "Jayson Tatum (77777777),Jimmy Butler (88888888)\n"
)

# DK entry CSV in bare numeric ID format
_DK_ENTRY_BARE_ID_CSV = (
    "Entry ID,Contest Name,Contest ID,Entry Fee,PG,SG,SF,PF,C,G,F,UTIL\n"
    ",,,,33333333,11111111,44444444,66666666,22222222,55555555,77777777,88888888\n"
)


def test_parse_lineup_csv_no_auth_required() -> None:
    """parse-lineup-csv has no auth dependency — app without auth overrides works."""
    from routers import optimizer

    app = FastAPI()
    app.include_router(optimizer.router)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/api/optimizer/parse-lineup-csv",
        files={
            "entry_file": ("entry.csv", BytesIO(_DK_ENTRY_NAME_ID_CSV.encode()), "text/csv"),
            "slate_file": ("slate.csv", BytesIO(_DK_SLATE_CSV.encode()), "text/csv"),
        },
        params={"site": "DK"},
    )
    assert resp.status_code == 200


def test_parse_lineup_csv_dk_name_id_format_parsed(make_authed_client) -> None:
    from routers import optimizer

    client = make_authed_client(optimizer.router)
    resp = client.post(
        "/api/optimizer/parse-lineup-csv",
        files={
            "entry_file": ("entry.csv", BytesIO(_DK_ENTRY_NAME_ID_CSV.encode()), "text/csv"),
            "slate_file": ("slate.csv", BytesIO(_DK_SLATE_CSV.encode()), "text/csv"),
        },
        params={"site": "DK"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["site"] == "DK"
    assert data["count"] == 1
    assert "Nikola Jokic" in data["lineups"][0]["players"]
    assert "LeBron James" in data["lineups"][0]["players"]


def test_parse_lineup_csv_dk_bare_id_resolved_via_slate(make_authed_client) -> None:
    from routers import optimizer

    client = make_authed_client(optimizer.router)
    resp = client.post(
        "/api/optimizer/parse-lineup-csv",
        files={
            "entry_file": ("entry.csv", BytesIO(_DK_ENTRY_BARE_ID_CSV.encode()), "text/csv"),
            "slate_file": ("slate.csv", BytesIO(_DK_SLATE_CSV.encode()), "text/csv"),
        },
        params={"site": "DK"},
    )
    assert resp.status_code == 200, resp.text
    players = resp.json()["lineups"][0]["players"]
    assert "Nikola Jokic" in players
    assert "LeBron James" in players


def test_parse_lineup_csv_400_when_no_lineups_found(make_authed_client) -> None:
    from routers import optimizer

    # Entry CSV has DK-style headers but no data rows
    empty_entry = "Entry ID,Contest Name,Contest ID,Entry Fee,PG,SG,SF,PF,C,G,F,UTIL\n"

    client = make_authed_client(optimizer.router)
    resp = client.post(
        "/api/optimizer/parse-lineup-csv",
        files={
            "entry_file": ("entry.csv", BytesIO(empty_entry.encode()), "text/csv"),
            "slate_file": ("slate.csv", BytesIO(_DK_SLATE_CSV.encode()), "text/csv"),
        },
        params={"site": "DK"},
    )
    assert resp.status_code == 400
