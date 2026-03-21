"""
Phase 46 — Pipeline router tests

Covers POST /api/pipeline/run-full and the ownership sub-routes:
  POST /api/pipeline/ownership/import-history
  POST /api/pipeline/ownership/train
"""
from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_DK_CSV = (
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
    ("LeBron James", "SF", 9000, 45.5, "11111111"),
    ("Nikola Jokic", "C", 10000, 50.0, "22222222"),
    ("Kyrie Irving", "PG", 7500, 38.0, "33333333"),
    ("Kevin Durant", "SF", 9500, 47.0, "44444444"),
    ("Stephen Curry", "PG", 9800, 49.0, "55555555"),
    ("Giannis A", "PF", 11000, 55.0, "66666666"),
    ("Jayson Tatum", "SF", 9200, 48.0, "77777777"),
    ("Jimmy Butler", "SF", 7800, 35.0, "88888888"),
]


def _fake_lineups_df(n=2) -> pd.DataFrame:
    rows = []
    for i in range(n):
        for name, pos, sal, proj, did in _PLAYERS:
            rows.append({"LineupIndex": i, "Name": name, "Pos": pos,
                         "Salary": sal, "Proj": proj, "Own": 15.0, "DFS_ID": did})
    return pd.DataFrame(rows)


def _fake_pipeline(*args, **kwargs) -> dict:
    return {
        "lineups_df": _fake_lineups_df(2),
        "projections_df": pd.DataFrame([
            {"Name": n, "Pos": p, "Salary": s, "Proj": j, "DFS_ID": d,
             "Floor": j * 0.75, "Ceiling": j * 1.30, "Value": round(j / s * 1000, 2),
             "Own": 15.0, "Team": "LAL", "Opp": "GSW"}
            for n, p, s, j, d in _PLAYERS
        ]),
        "site": "DK",
        "filter_report": {},
        "replacement_boosts": [],
    }


def _fake_pipeline_empty(*args, **kwargs) -> dict:
    return {"lineups_df": pd.DataFrame(), "projections_df": pd.DataFrame(),
            "site": "DK", "filter_report": {}, "replacement_boosts": []}


async def _fake_save_slate(file, user_id: str = ""):
    return {"file_path": "fake.csv", "file_id": "abc1", "file_name": "slate.csv"}


async def _fake_save_lineup(csv_data: str, file_id: str, user_id: str = ""):
    return {"file_name": "lineups_abc1.csv"}


def _fake_assign_slots(group: pd.DataFrame, site: str) -> dict:
    names = list(group["Name"])
    slots = ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"]
    return {slot: name for slot, name in zip(slots, names)}


# ---------------------------------------------------------------------------
# POST /api/pipeline/run-full
# ---------------------------------------------------------------------------

def test_run_full_rejects_non_csv(make_authed_client) -> None:
    from routers import pipeline

    client = make_authed_client(pipeline.router)
    resp = client.post(
        "/api/pipeline/run-full",
        files={"file": ("slate.txt", BytesIO(b"data"), "text/plain")},
    )
    assert resp.status_code == 400
    assert "CSV" in resp.json()["detail"]


def test_run_full_free_tier_returns_403(make_authed_client) -> None:
    from routers import pipeline

    client = make_authed_client(pipeline.router, tier="free")
    resp = client.post(
        "/api/pipeline/run-full",
        files={"file": ("slate.csv", BytesIO(_DK_CSV), "text/csv")},
    )
    assert resp.status_code == 403


def test_run_full_empty_lineups_returns_422(make_authed_client, monkeypatch) -> None:
    from routers import pipeline

    monkeypatch.setattr(pipeline, "save_slate_file", _fake_save_slate)
    monkeypatch.setattr(pipeline, "run_dfs_pipeline", _fake_pipeline_empty)
    monkeypatch.setattr(pipeline, "assign_lineup_slots", _fake_assign_slots)

    client = make_authed_client(pipeline.router)
    resp = client.post(
        "/api/pipeline/run-full",
        files={"file": ("slate.csv", BytesIO(_DK_CSV), "text/csv")},
        params={"site": "DK"},
    )
    assert resp.status_code == 422


def test_run_full_happy_path_returns_lineups(make_authed_client, monkeypatch) -> None:
    from routers import pipeline

    monkeypatch.setattr(pipeline, "save_slate_file", _fake_save_slate)
    monkeypatch.setattr(pipeline, "run_dfs_pipeline", _fake_pipeline)
    monkeypatch.setattr(pipeline, "save_lineup_file", _fake_save_lineup)
    monkeypatch.setattr(pipeline, "assign_lineup_slots", _fake_assign_slots)

    client = make_authed_client(pipeline.router)
    resp = client.post(
        "/api/pipeline/run-full",
        files={"file": ("slate.csv", BytesIO(_DK_CSV), "text/csv")},
        params={"site": "DK", "refresh_props": "false"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["total_lineups"] == 2
    assert data["download_file"] == "lineups_abc1.csv"
    assert "pipeline_steps" in data


def test_run_full_response_has_stats(make_authed_client, monkeypatch) -> None:
    from routers import pipeline

    monkeypatch.setattr(pipeline, "save_slate_file", _fake_save_slate)
    monkeypatch.setattr(pipeline, "run_dfs_pipeline", _fake_pipeline)
    monkeypatch.setattr(pipeline, "save_lineup_file", _fake_save_lineup)
    monkeypatch.setattr(pipeline, "assign_lineup_slots", _fake_assign_slots)

    client = make_authed_client(pipeline.router)
    resp = client.post(
        "/api/pipeline/run-full",
        files={"file": ("slate.csv", BytesIO(_DK_CSV), "text/csv")},
        params={"site": "DK", "refresh_props": "false"},
    )
    stats = resp.json()["stats"]
    assert "avg_projection" in stats
    assert "avg_salary" in stats
    assert "projection_range" in stats


def test_run_full_pipeline_exception_returns_500(make_authed_client, monkeypatch) -> None:
    from routers import pipeline

    monkeypatch.setattr(pipeline, "save_slate_file", _fake_save_slate)
    monkeypatch.setattr(pipeline, "run_dfs_pipeline",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(pipeline, "assign_lineup_slots", _fake_assign_slots)

    client = make_authed_client(pipeline.router)
    resp = client.post(
        "/api/pipeline/run-full",
        files={"file": ("slate.csv", BytesIO(_DK_CSV), "text/csv")},
        params={"site": "DK", "refresh_props": "false"},
    )
    assert resp.status_code == 500


def test_run_full_bad_projection_overrides_graceful(make_authed_client, monkeypatch) -> None:
    from routers import pipeline

    monkeypatch.setattr(pipeline, "save_slate_file", _fake_save_slate)
    monkeypatch.setattr(pipeline, "run_dfs_pipeline", _fake_pipeline)
    monkeypatch.setattr(pipeline, "save_lineup_file", _fake_save_lineup)
    monkeypatch.setattr(pipeline, "assign_lineup_slots", _fake_assign_slots)

    client = make_authed_client(pipeline.router)
    resp = client.post(
        "/api/pipeline/run-full",
        files={"file": ("slate.csv", BytesIO(_DK_CSV), "text/csv")},
        params={"refresh_props": "false", "projection_overrides": "{{NOT-JSON}}"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/pipeline/ownership/import-history
# ---------------------------------------------------------------------------

def test_ownership_import_success(make_authed_client, monkeypatch) -> None:
    from routers import pipeline

    mock_mod = MagicMock()
    mock_mod.import_lineups_to_history.return_value = {"imported": 42, "status": "ok"}

    client = make_authed_client(pipeline.router)
    with patch.dict(__import__("sys").modules, {"analysis.nba.ownership_v2": mock_mod}):
        resp = client.post("/api/pipeline/ownership/import-history", params={"site": "DK"})

    assert resp.status_code == 200
    assert resp.json()["imported"] == 42


def test_ownership_import_exception_returns_500(make_authed_client) -> None:
    from routers import pipeline

    bad_mod = MagicMock()
    bad_mod.import_lineups_to_history.side_effect = RuntimeError("db error")

    client = make_authed_client(pipeline.router)
    with patch.dict(__import__("sys").modules, {"analysis.nba.ownership_v2": bad_mod}):
        resp = client.post("/api/pipeline/ownership/import-history")

    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/pipeline/ownership/train
# ---------------------------------------------------------------------------

def test_ownership_train_success(make_authed_client) -> None:
    from routers import pipeline

    mock_mod = MagicMock()
    mock_mod.train_ownership_model.return_value = object()  # non-None → success

    client = make_authed_client(pipeline.router)
    with patch.dict(__import__("sys").modules, {"analysis.nba.ownership_v2": mock_mod}):
        resp = client.post("/api/pipeline/ownership/train", params={"sport": "NBA", "site": "DK"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "trained"


def test_ownership_train_insufficient_data_returns_422(make_authed_client) -> None:
    from routers import pipeline

    mock_mod = MagicMock()
    mock_mod.train_ownership_model.return_value = None  # None → insufficient data

    client = make_authed_client(pipeline.router)
    with patch.dict(__import__("sys").modules, {"analysis.nba.ownership_v2": mock_mod}):
        resp = client.post("/api/pipeline/ownership/train")

    assert resp.status_code == 422
