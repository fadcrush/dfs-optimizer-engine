from __future__ import annotations

from io import BytesIO

import pandas as pd


def _build_projection_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Name": "Locked Guard", "Pos": "SG", "Salary": 5000, "Proj": 30, "Own": 20, "DFS_ID": "1", "Team": "AAA", "GameInfo": "AAA@BBB"},
            {"Name": "Scratched Guard", "Pos": "PG/SG", "Salary": 6000, "Proj": 25, "Own": 18, "DFS_ID": "2", "Team": "AAA", "GameInfo": "AAA@BBB"},
            {"Name": "PG Candidate", "Pos": "PG", "Salary": 5900, "Proj": 28, "Own": 12, "DFS_ID": "3", "Team": "CCC", "GameInfo": "CCC@DDD"},
            {"Name": "SG Candidate", "Pos": "SG", "Salary": 5900, "Proj": 29, "Own": 10, "DFS_ID": "4", "Team": "EEE", "GameInfo": "EEE@FFF"},
        ]
    )


async def _fake_save_slate_file(file, user_id: str = ""):
    return {"file_path": "ignored.csv", "file_id": "slate-id"}


def _fake_pipeline(*args, **kwargs):
    return {"projections_df": _build_projection_frame(), "site": "DK"}


def test_late_swap_without_slot_labels_uses_generic_position_matching(make_authed_client, monkeypatch):
    from routers import optimizer

    monkeypatch.setattr(optimizer, "save_slate_file", _fake_save_slate_file)
    monkeypatch.setattr(optimizer, "run_dfs_pipeline", _fake_pipeline)

    client = make_authed_client(optimizer.router)
    response = client.post(
        "/api/optimizer/late-swap",
        files={"file": ("slate.csv", BytesIO(b"Position,Name\nPG,Test\n"), "text/csv")},
        params={
            "site": "DK",
            "lineup": "Locked Guard,Scratched Guard",
            "scratched": "Scratched Guard",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    candidates = payload["swaps"][0]["candidates"]
    assert [candidate["name"] for candidate in candidates] == ["SG Candidate", "PG Candidate"]


def test_late_swap_with_slot_labels_filters_to_exact_scratched_slot(make_authed_client, monkeypatch):
    from routers import optimizer

    monkeypatch.setattr(optimizer, "save_slate_file", _fake_save_slate_file)
    monkeypatch.setattr(optimizer, "run_dfs_pipeline", _fake_pipeline)

    client = make_authed_client(optimizer.router)
    response = client.post(
        "/api/optimizer/late-swap",
        files={"file": ("slate.csv", BytesIO(b"Position,Name\nPG,Test\n"), "text/csv")},
        params={
            "site": "DK",
            "lineup": "Locked Guard,Scratched Guard",
            "slot_labels": "SG,PG",
            "scratched": "Scratched Guard",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    candidates = payload["swaps"][0]["candidates"]
    assert [candidate["name"] for candidate in candidates] == ["PG Candidate"]


def test_late_swap_rejects_scratching_locked_players(make_authed_client, monkeypatch):
    from routers import optimizer

    monkeypatch.setattr(optimizer, "save_slate_file", _fake_save_slate_file)
    monkeypatch.setattr(optimizer, "run_dfs_pipeline", _fake_pipeline)

    client = make_authed_client(optimizer.router)
    response = client.post(
        "/api/optimizer/late-swap",
        files={"file": ("slate.csv", BytesIO(b"Position,Name\nPG,Test\n"), "text/csv")},
        params={
            "site": "DK",
            "lineup": "Locked Guard,Scratched Guard",
            "slot_labels": "SG,PG",
            "scratched": "Locked Guard",
            "locked_players": "Locked Guard",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Locked players cannot be scratched: Locked Guard"


def test_batch_late_swap_does_not_swap_in_locked_candidates(make_authed_client, monkeypatch):
    from routers import optimizer

    monkeypatch.setattr(optimizer, "save_slate_file", _fake_save_slate_file)
    monkeypatch.setattr(optimizer, "run_dfs_pipeline", _fake_pipeline)

    client = make_authed_client(optimizer.router)
    response = client.post(
        "/api/optimizer/batch-late-swap",
        files={"file": ("slate.csv", BytesIO(b"Position,Name\nPG,Test\n"), "text/csv")},
        params={
            "site": "DK",
            "lineups": '[["Locked Guard","Scratched Guard"]]',
            "scratched": "Scratched Guard",
            "locked_players": "Locked Guard,SG Candidate",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    swap_log = payload["swap_log"][0]
    assert swap_log["swaps"][0]["scratched"] == "Scratched Guard"
    assert swap_log["swaps"][0]["replacement"] == "PG Candidate"


def test_batch_late_swap_keeps_players_listed_as_locked_even_if_scratched(make_authed_client, monkeypatch):
    from routers import optimizer

    monkeypatch.setattr(optimizer, "save_slate_file", _fake_save_slate_file)
    monkeypatch.setattr(optimizer, "run_dfs_pipeline", _fake_pipeline)

    client = make_authed_client(optimizer.router)
    response = client.post(
        "/api/optimizer/batch-late-swap",
        files={"file": ("slate.csv", BytesIO(b"Position,Name\nPG,Test\n"), "text/csv")},
        params={
            "site": "DK",
            "lineups": '[["Locked Guard","Scratched Guard"]]',
            "scratched": "Locked Guard",
            "locked_players": "Locked Guard",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    swap_log = payload["swap_log"][0]
    assert swap_log["swaps"] == []
    assert swap_log["players_kept"] == 2
    assert swap_log["swaps_made"] == 0