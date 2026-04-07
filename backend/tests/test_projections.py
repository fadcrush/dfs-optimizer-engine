"""
Phase 46 — Projections router tests

Covers:
  POST /api/projections/upload-slate
  POST /api/projections/generate
  POST /api/projections/run
  POST /api/projections/generate-from-upload
  GET  /api/projections/download/{file_name}
"""
from __future__ import annotations

from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_DK_CSV = (
    b"Name,Position,Salary,AvgPointsPerGame,TeamAbbrev,ID\n"
    b"LeBron James,F,9000,45.5,LAL,11111111\n"
    b"Nikola Jokic,C,10000,50.0,DEN,22222222\n"
)

_FAKE_PROJECTIONS = [
    {"dfs_id": "11111111", "name": "LeBron James", "position": "SF",
     "team": "LAL", "opponent": "GSW", "salary": 9000,
     "projection": 45.5, "floor": 34.1, "ceiling": 59.2, "std_dev": 9.1, "value": 5.06, "ownership": 15.0},
    {"dfs_id": "22222222", "name": "Nikola Jokic", "position": "C",
     "team": "DEN", "opponent": "PHX", "salary": 10000,
     "projection": 50.0, "floor": 37.5, "ceiling": 65.0, "std_dev": 10.0, "value": 5.0, "ownership": 22.0},
]

_FAKE_GENERATE_RESULT = {
    "success": True,
    "projections": _FAKE_PROJECTIONS,
    "stats": {"count": 2, "avg_proj": 47.75},
    "algorithm": "canonical",
    "note": "",
}


async def _fake_save_slate(file, user_id: str = ""):
    return {"file_path": "fake_slate.csv", "file_id": "abc1", "file_name": "slate.csv"}


async def _fake_generate(*args, **kwargs):
    return _FAKE_GENERATE_RESULT


async def _fake_generate_fail(*args, **kwargs):
    return {"success": False, "error": "projection engine failed"}


async def _fake_save_projection(csv_data: str, file_id: str, user_id: str = ""):
    return {"file_name": "projections_abc1.csv"}


def _fake_projections_to_csv(projections: list) -> str:
    return "name,projection\nLeBron James,45.5\n"


# ---------------------------------------------------------------------------
# POST /api/projections/upload-slate
# ---------------------------------------------------------------------------

def test_upload_slate_rejects_non_csv(make_authed_client) -> None:
    from routers import projections as proj_router

    client = make_authed_client(proj_router.router)
    resp = client.post(
        "/api/projections/upload-slate",
        files={"file": ("slate.txt", BytesIO(b"data"), "text/plain")},
    )
    assert resp.status_code == 400
    assert "CSV" in resp.json()["detail"]


def test_upload_slate_success(make_authed_client, monkeypatch) -> None:
    from routers import projections as proj_router

    monkeypatch.setattr(proj_router, "save_slate_file", _fake_save_slate)

    client = make_authed_client(proj_router.router)
    resp = client.post(
        "/api/projections/upload-slate",
        files={"file": ("slate.csv", BytesIO(_DK_CSV), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "file" in data
    assert "next_step" in data


# ---------------------------------------------------------------------------
# POST /api/projections/generate
# ---------------------------------------------------------------------------

def test_generate_404_for_missing_slate(make_authed_client, isolated_upload_dirs) -> None:
    from routers import projections as proj_router

    client = make_authed_client(proj_router.router)
    resp = client.post(
        "/api/projections/generate",
        params={"slate_file_name": "nonexistent.csv", "site": "DK"},
    )
    assert resp.status_code == 404


def test_generate_success(make_authed_client, isolated_upload_dirs, monkeypatch) -> None:
    from routers import projections as proj_router

    # Create the slate file in the isolated slates dir
    slate_file = isolated_upload_dirs / "slates" / "test-user" / "slate.csv"
    slate_file.parent.mkdir(parents=True, exist_ok=True)
    slate_file.write_bytes(_DK_CSV)

    monkeypatch.setattr(proj_router, "generate_projections", _fake_generate)

    client = make_authed_client(proj_router.router, user_id="test-user")
    resp = client.post(
        "/api/projections/generate",
        params={"slate_file_name": "slate.csv", "site": "DK"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["message"] == "Projections generated successfully!"


def test_generate_500_on_projection_failure(make_authed_client, isolated_upload_dirs, monkeypatch) -> None:
    from routers import projections as proj_router

    slate_file = isolated_upload_dirs / "slates" / "test-user" / "slate.csv"
    slate_file.parent.mkdir(parents=True, exist_ok=True)
    slate_file.write_bytes(_DK_CSV)

    monkeypatch.setattr(proj_router, "generate_projections", _fake_generate_fail)

    client = make_authed_client(proj_router.router, user_id="test-user")
    resp = client.post(
        "/api/projections/generate",
        params={"slate_file_name": "slate.csv", "site": "DK"},
    )
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/projections/run
# ---------------------------------------------------------------------------

def test_run_rejects_non_csv(make_authed_client) -> None:
    from routers import projections as proj_router

    client = make_authed_client(proj_router.router)
    resp = client.post(
        "/api/projections/run",
        files={"file": ("data.txt", BytesIO(b"x"), "text/plain")},
    )
    assert resp.status_code == 400


def test_run_happy_path(make_authed_client, monkeypatch) -> None:
    from routers import projections as proj_router

    monkeypatch.setattr(proj_router, "save_slate_file", _fake_save_slate)
    monkeypatch.setattr(proj_router, "generate_projections", _fake_generate)
    monkeypatch.setattr(proj_router, "projections_to_csv", _fake_projections_to_csv)
    monkeypatch.setattr(proj_router, "save_projection_file", _fake_save_projection)

    client = make_authed_client(proj_router.router)
    resp = client.post(
        "/api/projections/run",
        files={"file": ("slate.csv", BytesIO(_DK_CSV), "text/csv")},
        params={"site": "DK"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["download_file"] == "projections_abc1.csv"
    assert len(data["projections"]) == 2


# ---------------------------------------------------------------------------
# POST /api/projections/generate-from-upload
# ---------------------------------------------------------------------------

def test_generate_from_upload_free_tier_returns_403(make_authed_client) -> None:
    from routers import projections as proj_router

    client = make_authed_client(proj_router.router, tier="free")
    resp = client.post(
        "/api/projections/generate-from-upload",
        files={"file": ("slate.csv", BytesIO(_DK_CSV), "text/csv")},
    )
    assert resp.status_code == 403


def test_generate_from_upload_rejects_non_csv(make_authed_client) -> None:
    from routers import projections as proj_router

    client = make_authed_client(proj_router.router)
    resp = client.post(
        "/api/projections/generate-from-upload",
        files={"file": ("data.json", BytesIO(b"{}"), "application/json")},
    )
    assert resp.status_code == 400


def test_generate_from_upload_happy_path(make_authed_client, monkeypatch) -> None:
    from routers import projections as proj_router

    monkeypatch.setattr(proj_router, "save_slate_file", _fake_save_slate)
    monkeypatch.setattr(proj_router, "generate_projections", _fake_generate)
    monkeypatch.setattr(proj_router, "projections_to_csv", _fake_projections_to_csv)
    monkeypatch.setattr(proj_router, "save_projection_file", _fake_save_projection)

    client = make_authed_client(proj_router.router)
    resp = client.post(
        "/api/projections/generate-from-upload",
        files={"file": ("slate.csv", BytesIO(_DK_CSV), "text/csv")},
        params={"site": "FD"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["total_projections"] == 2


# ---------------------------------------------------------------------------
# GET /api/projections/download/{file_name}
# ---------------------------------------------------------------------------

def test_download_404_for_missing_file(make_authed_client, isolated_upload_dirs) -> None:
    from routers import projections as proj_router

    client = make_authed_client(proj_router.router)
    resp = client.get("/api/projections/download/nosuchfile.csv")
    assert resp.status_code == 404


def test_download_returns_csv(make_authed_client, isolated_upload_dirs) -> None:
    from routers import projections as proj_router

    # Create a projection file in the isolated projections dir
    proj_file = isolated_upload_dirs / "projections" / "test-user" / "proj.csv"
    proj_file.parent.mkdir(parents=True, exist_ok=True)
    proj_file.write_text("name,projection\nLeBron,45.5\n")

    client = make_authed_client(proj_router.router, user_id="test-user")
    resp = client.get("/api/projections/download/proj.csv")
    assert resp.status_code == 200
    assert "LeBron" in resp.text


# ---------------------------------------------------------------------------
# Canonical projection contract — verifies every canonical field is present
# ---------------------------------------------------------------------------

_CANONICAL_FIELDS = ("projection", "floor", "ceiling", "std_dev", "value", "ownership")


def test_run_response_includes_canonical_projection_fields(make_authed_client, monkeypatch) -> None:
    """POST /run must return all canonical projection fields in every row."""
    from routers import projections as proj_router

    monkeypatch.setattr(proj_router, "save_slate_file", _fake_save_slate)
    monkeypatch.setattr(proj_router, "generate_projections", _fake_generate)
    monkeypatch.setattr(proj_router, "projections_to_csv", _fake_projections_to_csv)
    monkeypatch.setattr(proj_router, "save_projection_file", _fake_save_projection)

    client = make_authed_client(proj_router.router)
    resp = client.post(
        "/api/projections/run",
        files={"file": ("slate.csv", BytesIO(_DK_CSV), "text/csv")},
        params={"site": "DK"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True, "response must report success"

    projections = data.get("projections", [])
    assert len(projections) > 0, "projections list must be non-empty"

    for row in projections:
        for field in _CANONICAL_FIELDS:
            assert field in row, f"canonical field '{field}' missing from projection row: {row}"
        # numeric sanity: floor ≤ projection ≤ ceiling
        assert row["floor"] <= row["projection"], (
            f"floor ({row['floor']}) must be ≤ projection ({row['projection']})"
        )
        assert row["projection"] <= row["ceiling"], (
            f"projection ({row['projection']}) must be ≤ ceiling ({row['ceiling']})"
        )
        assert row["std_dev"] >= 0, "std_dev must be non-negative"
        assert row["value"] >= 0, "value must be non-negative"
        assert row["ownership"] >= 0, "ownership must be non-negative"


def test_run_projection_algorithm_is_canonical(make_authed_client, monkeypatch) -> None:
    """POST /run must advertise the canonical algorithm, never a DEV placeholder."""
    from routers import projections as proj_router

    monkeypatch.setattr(proj_router, "save_slate_file", _fake_save_slate)
    monkeypatch.setattr(proj_router, "generate_projections", _fake_generate)
    monkeypatch.setattr(proj_router, "projections_to_csv", _fake_projections_to_csv)
    monkeypatch.setattr(proj_router, "save_projection_file", _fake_save_projection)

    client = make_authed_client(proj_router.router)
    resp = client.post(
        "/api/projections/run",
        files={"file": ("slate.csv", BytesIO(_DK_CSV), "text/csv")},
        params={"site": "DK"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    algorithm = data.get("algorithm", "")
    assert "DEV" not in algorithm.upper(), (
        f"algorithm field must not indicate a DEV placeholder, got: {algorithm!r}"
    )


def test_generate_from_upload_response_includes_canonical_fields(
    make_authed_client, monkeypatch
) -> None:
    """POST /generate-from-upload must also return all canonical fields."""
    from routers import projections as proj_router

    monkeypatch.setattr(proj_router, "save_slate_file", _fake_save_slate)
    monkeypatch.setattr(proj_router, "generate_projections", _fake_generate)
    monkeypatch.setattr(proj_router, "projections_to_csv", _fake_projections_to_csv)
    monkeypatch.setattr(proj_router, "save_projection_file", _fake_save_projection)

    client = make_authed_client(proj_router.router)
    resp = client.post(
        "/api/projections/generate-from-upload",
        files={"file": ("slate.csv", BytesIO(_DK_CSV), "text/csv")},
        params={"site": "DK"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    projections = data.get("projections", [])
    assert len(projections) > 0

    for row in projections:
        for field in _CANONICAL_FIELDS:
            assert field in row, f"canonical field '{field}' missing: {row}"
