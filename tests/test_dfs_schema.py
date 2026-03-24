from __future__ import annotations

import pandas as pd

from analysis.shared.dfs_schema import normalize_slate_df


def test_normalize_slate_df_derives_dk_opponent_from_game_info() -> None:
    raw = pd.DataFrame(
        {
            "ID": ["12345"],
            "Name": ["Jayson Tatum"],
            "Position": ["SF/PF"],
            "TeamAbbrev": ["BOS"],
            "Game Info": ["BOS@NYK 07:30PM ET"],
            "Salary": [9800],
            "My Proj": [47.5],
        }
    )

    normalized, site = normalize_slate_df(raw, site="DK")

    assert site == "DK"
    assert normalized.loc[0, "DFS_ID"] == "12345"
    assert normalized.loc[0, "Name"] == "Jayson Tatum"
    assert normalized.loc[0, "Team"] == "BOS"
    assert normalized.loc[0, "Opp"] == "NYK"
    assert normalized.loc[0, "Pos"] == "SF/PF"
    assert normalized.loc[0, "Salary"] == 9800
    assert normalized.loc[0, "Base_Proj"] == 47.5
    assert normalized.loc[0, "Raw_DFS_ID"] == "12345"


def test_normalize_slate_df_derives_fd_opponent_and_extracts_player_id() -> None:
    raw = pd.DataFrame(
        {
            "Id": ["126855-203801"],
            "Nickname": ["LeBron James"],
            "Position": ["SF"],
            "Team": ["LAL"],
            "Game": ["BOS@LAL 09:00PM ET"],
            "Salary": [11000],
            "Injury Indicator": ["Q"],
        }
    )

    normalized, site = normalize_slate_df(raw)

    assert site == "FD"
    assert normalized.loc[0, "DFS_ID"] == "203801"
    assert normalized.loc[0, "Raw_DFS_ID"] == "126855-203801"
    assert normalized.loc[0, "Name"] == "LeBron James"
    assert normalized.loc[0, "Opp"] == "BOS"
    assert normalized.loc[0, "InjuryStatus"] == "Q"


def test_normalize_slate_df_preserves_explicit_opponent_column() -> None:
    raw = pd.DataFrame(
        {
            "ID": ["999"],
            "Name": ["Player One"],
            "Position": ["PG"],
            "Team": ["DAL"],
            "Opp": ["HOU"],
            "Game Info": ["DAL@SAS 08:00PM ET"],
            "Salary": [7000],
        }
    )

    normalized, _ = normalize_slate_df(raw, site="DK")

    assert normalized.loc[0, "Opp"] == "HOU"


def test_normalize_slate_df_raises_when_id_missing() -> None:
    raw = pd.DataFrame(
        {
            "Name": ["No Id"],
            "Position": ["PG"],
            "Team": ["DAL"],
            "Salary": [5000],
        }
    )

    try:
        normalize_slate_df(raw, site="DK")
    except ValueError as exc:
        assert "ID column" in str(exc)
    else:
        raise AssertionError("normalize_slate_df should require an ID column")