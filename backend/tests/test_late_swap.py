from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO

import pandas as pd


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _dk_game_str(dt: datetime) -> str:
    """Produce a DK GameInfo string (e.g. 'AAA@BBB 03/28/2026 07:30PM ET') from a UTC datetime."""
    # Approximate ET for test purposes — just needs to round-trip through _parse_start_time
    return f"AAA@BBB {dt.strftime('%m/%d/%Y')} 07:30PM ET"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _build_projection_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Name": "Locked Guard", "Pos": "SG", "Salary": 5000, "Proj": 30, "Own": 20, "DFS_ID": "1", "Team": "AAA", "GameInfo": "AAA@BBB"},
            {"Name": "Scratched Guard", "Pos": "PG/SG", "Salary": 6000, "Proj": 25, "Own": 18, "DFS_ID": "2", "Team": "AAA", "GameInfo": "AAA@BBB"},
            {"Name": "PG Candidate", "Pos": "PG", "Salary": 5900, "Proj": 28, "Own": 12, "DFS_ID": "3", "Team": "CCC", "GameInfo": "CCC@DDD"},
            {"Name": "SG Candidate", "Pos": "SG", "Salary": 5900, "Proj": 29, "Own": 10, "DFS_ID": "4", "Team": "EEE", "GameInfo": "EEE@FFF"},
        ]
    )


# ---------------------------------------------------------------------------
# Projection frame with ISO game times for backend game-state derivation.
# "Started Guard" has a game that started 10 minutes ago.
# "Available Guard" and "Scratched Guard" have games 2 hours in the future.
# ---------------------------------------------------------------------------

def _build_timed_frame() -> pd.DataFrame:
    now = _now_utc()
    past_iso   = _iso(now - timedelta(minutes=10))
    future_iso = _iso(now + timedelta(hours=2))
    future2_iso = _iso(now + timedelta(hours=3))
    return pd.DataFrame([
        # started — must be excluded from swap-in candidates by backend game state
        {"Name": "Started Guard",     "Pos": "PG",    "Salary": 6200, "Proj": 35.0, "Own": 22, "DFS_ID": "10", "Team": "AAA", "GameInfo": past_iso},
        # not started — eligible as a replacement
        {"Name": "Available Guard",   "Pos": "PG",    "Salary": 5800, "Proj": 28.0, "Own": 12, "DFS_ID": "11", "Team": "BBB", "GameInfo": future_iso},
        # the scratched player (a game that hasn't started yet)
        {"Name": "Scratched Forward", "Pos": "SF",    "Salary": 7000, "Proj": 30.0, "Own": 18, "DFS_ID": "12", "Team": "CCC", "GameInfo": future2_iso},
        # started — must be excluded from swap-in candidates by backend game state
        {"Name": "Started Forward",   "Pos": "SF",    "Salary": 6500, "Proj": 33.0, "Own": 20, "DFS_ID": "13", "Team": "DDD", "GameInfo": past_iso},
        # not started — eligible for SF replacement
        {"Name": "Available Forward", "Pos": "SF",    "Salary": 6800, "Proj": 29.0, "Own": 10, "DFS_ID": "14", "Team": "EEE", "GameInfo": future_iso},
        # locked keeper in the lineup (started, but not scratched — stays in lineup)
        {"Name": "Started Keeper PG", "Pos": "PG",    "Salary": 8000, "Proj": 45.0, "Own": 30, "DFS_ID": "15", "Team": "AAA", "GameInfo": past_iso},
    ])


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


# ===========================================================================
# Smoke tests: backend-canonical lock-state (compute_game_state integration)
# ===========================================================================

async def _fake_save_slate_timed(file, user_id: str = ""):
    return {"file_path": "ignored.csv", "file_id": "slate-id"}


def _fake_pipeline_timed(*args, **kwargs):
    return {"projections_df": _build_timed_frame(), "site": "DK"}


def test_late_swap_backend_independently_excludes_started_candidates(make_authed_client, monkeypatch):
    """
    Smoke scenario: backend derives started players from GameInfo times in the
    slate data.  'Started Guard' has a game that started 10 min ago — the
    backend must exclude it from swap candidates WITHOUT the frontend sending it
    in locked_players.
    """
    from routers import optimizer

    monkeypatch.setattr(optimizer, "save_slate_file", _fake_save_slate_timed)
    monkeypatch.setattr(optimizer, "run_dfs_pipeline", _fake_pipeline_timed)

    client = make_authed_client(optimizer.router)
    response = client.post(
        "/api/optimizer/late-swap",
        files={"file": ("slate.csv", BytesIO(b"Name,Pos\nTest,PG\n"), "text/csv")},
        params={
            "site": "DK",
            "lineup": "Started Keeper PG,Scratched Forward",
            "scratched": "Scratched Forward",
            # Intentionally omit locked_players — backend must figure it out alone
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    candidates = payload["swaps"][0]["candidates"]
    candidate_names = [c["name"] for c in candidates]

    # Started Forward had a game that started — must be excluded by backend game state
    assert "Started Forward" not in candidate_names, (
        "Backend failed to exclude a started candidate — lock-state not canonical"
    )
    # Available Forward has a future game — must be in the pool
    assert "Available Forward" in candidate_names, (
        "Available Forward incorrectly excluded — non-started player missing from pool"
    )


def test_late_swap_started_lineup_player_kept_as_locked(make_authed_client, monkeypatch):
    """
    A started player already in the lineup ('Started Keeper PG') must not be
    substituted out.  The backend marks it as locked via game state and the
    endpoint must reject any attempt to scratch it.
    """
    from routers import optimizer

    monkeypatch.setattr(optimizer, "save_slate_file", _fake_save_slate_timed)
    monkeypatch.setattr(optimizer, "run_dfs_pipeline", _fake_pipeline_timed)

    client = make_authed_client(optimizer.router)
    response = client.post(
        "/api/optimizer/late-swap",
        files={"file": ("slate.csv", BytesIO(b"Name,Pos\nTest,PG\n"), "text/csv")},
        params={
            "site": "DK",
            "lineup": "Started Keeper PG,Scratched Forward",
            # Try to scratch a started player without explicitly locking it
            "scratched": "Started Keeper PG",
        },
    )

    # backend game_state marks Started Keeper PG as locked → 400 conflict
    assert response.status_code == 400
    assert "Locked players cannot be scratched" in response.json()["detail"]


def test_batch_late_swap_backend_excludes_started_candidates_without_locked_players(make_authed_client, monkeypatch):
    """
    Batch smoke scenario: backend independently excludes started candidates
    even when the frontend does not send locked_players.

    Slate has:
      - Started Keeper PG  (game started)  — kept in lineup, not scratched
      - Scratched Forward  (game not started) — scratched → needs replacement
      - Started Guard      (game started)  — eligible position but must be excluded from pool
      - Started Forward    (game started)  — eligible position but must be excluded from pool
      - Available Forward  (game not started) — the correct replacement
    """
    from routers import optimizer

    monkeypatch.setattr(optimizer, "save_slate_file", _fake_save_slate_timed)
    monkeypatch.setattr(optimizer, "run_dfs_pipeline", _fake_pipeline_timed)

    client = make_authed_client(optimizer.router)
    response = client.post(
        "/api/optimizer/batch-late-swap",
        files={"file": ("slate.csv", BytesIO(b"Name,Pos\nTest,PG\n"), "text/csv")},
        params={
            "site": "DK",
            "lineups": '[["Started Keeper PG","Scratched Forward"]]',
            "scratched": "Scratched Forward",
            # No locked_players sent — backend must guard via game state
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    swap_log = payload["swap_log"][0]

    assert swap_log["swaps_made"] == 1, "Expected exactly one successful swap"
    replacement = swap_log["swaps"][0]["replacement"]

    # Started players must never be chosen as replacements
    assert replacement not in ("Started Guard", "Started Forward"), (
        f"Backend picked a started player as replacement: {replacement}"
    )
    # Available Forward is the only eligible non-started replacement
    assert replacement == "Available Forward", (
        f"Expected 'Available Forward' as replacement, got '{replacement}'"
    )


def test_batch_late_swap_started_lineup_player_preserved(make_authed_client, monkeypatch):
    """
    Started Keeper PG is in the lineup and also listed in scratched.
    Because the backend marks it as locked via game state, it must be kept —
    the swap engine must not remove started-and-locked players.
    """
    from routers import optimizer

    monkeypatch.setattr(optimizer, "save_slate_file", _fake_save_slate_timed)
    monkeypatch.setattr(optimizer, "run_dfs_pipeline", _fake_pipeline_timed)

    client = make_authed_client(optimizer.router)
    response = client.post(
        "/api/optimizer/batch-late-swap",
        files={"file": ("slate.csv", BytesIO(b"Name,Pos\nTest,PG\n"), "text/csv")},
        params={
            "site": "DK",
            "lineups": '[["Started Keeper PG","Available Forward"]]',
            # Frontend incorrectly tries to scratch a started player
            "scratched": "Started Keeper PG",
            # No explicit locked_players — backend must derive it from game state
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    swap_log = payload["swap_log"][0]

    # Started Keeper PG is locked by backend game state — treat as kept, not swapped
    assert swap_log["swaps_made"] == 0, (
        "Started player was incorrectly swapped out"
    )
    assert swap_log["players_kept"] == 2, (
        "Started player should be counted as kept"
    )


def test_batch_late_swap_mixed_lineup_preserves_slot_structure_and_salary(make_authed_client, monkeypatch):
    """
    Full mixed-slate smoke test:
      - A lineup contains both started and unstarted players
      - The scratched player (Available Guard, unstarted) gets replaced
      - Slot structure (player count) must be preserved
      - Total salary must remain within cap
    """
    from routers import optimizer

    monkeypatch.setattr(optimizer, "save_slate_file", _fake_save_slate_timed)
    monkeypatch.setattr(optimizer, "run_dfs_pipeline", _fake_pipeline_timed)

    client = make_authed_client(optimizer.router)
    response = client.post(
        "/api/optimizer/batch-late-swap",
        files={"file": ("slate.csv", BytesIO(b"Name,Pos\nTest,PG\n"), "text/csv")},
        params={
            "site": "DK",
            "lineups": '[["Started Keeper PG","Available Forward","Available Guard"]]',
            "scratched": "Available Guard",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    swap_log = payload["swap_log"][0]

    # Slot count must stay the same (3 in → 3 out after swap)
    # players_kept = 2 (the two non-scratched) + swaps_made replaces the scratched
    assert swap_log["players_kept"] + swap_log["swaps_made"] == 3, (
        "Roster slot count changed after late swap — slot structure not preserved"
    )

    # Total salary must stay at or under the DK cap
    assert swap_log["total_salary"] <= swap_log["salary_cap"], (
        f"Salary cap exceeded after swap: {swap_log['total_salary']} > {swap_log['salary_cap']}"
    )

    # The replacement must not be a started player
    for sw in swap_log["swaps"]:
        if sw["replacement"]:
            assert sw["replacement"] not in ("Started Guard", "Started Forward"), (
                f"Backend chose a started candidate as replacement: {sw['replacement']}"
            )