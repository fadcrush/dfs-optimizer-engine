"""
Phase 24 tests: Vectorised match_injury_status() and build_injury_summary().

Covers:
  - match_injury_status() basic enrichment (empty → filled)
  - match_injury_status() preserves explicit non-empty InjuryStatus
  - match_injury_status() fills InjuryDetail correctly
  - match_injury_status() leaves InjuryDetail blank when DB has no detail
  - match_injury_status() empty injury_df → returns df unchanged
  - match_injury_status() player not in injury DB → stays blank
  - match_injury_status() duplicate slug in injury_df → uses first match
  - match_injury_status() accepts player_id column in injury_df
  - match_injury_status() auto-detects non-standard name column in player df
  - build_injury_summary() counts OUT / Q / D / P correctly
  - build_injury_summary() empty injury_df → zero counts, empty lists
  - build_injury_summary() last_updated set from game_date max
  - build_injury_summary() all_injuries list populated for matched players
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from analysis.shared.injury_utils import match_injury_status, build_injury_summary


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _player_df(*rows: dict) -> pd.DataFrame:
    """Build a minimal player pool DataFrame."""
    base = {"Name": "Unknown", "InjuryStatus": "", "InjuryDetail": ""}
    return pd.DataFrame([{**base, **r} for r in rows])


def _injury_df(*rows: dict) -> pd.DataFrame:
    """Build a minimal injury report DataFrame."""
    base = {"player_name": "Unknown", "status": "", "detail": "", "team": "", "game_date": None}
    return pd.DataFrame([{**base, **r} for r in rows])


# ──────────────────────────────────────────────────────────────────────────────
# match_injury_status — basic fill
# ──────────────────────────────────────────────────────────────────────────────

def test_match_injury_status_fills_blank_status():
    players = _player_df({"Name": "LeBron James", "InjuryStatus": ""})
    injuries = _injury_df({"player_name": "LeBron James", "status": "OUT", "detail": "knee"})

    result = match_injury_status(players, injuries)

    assert result.loc[0, "InjuryStatus"] == "OUT"


def test_match_injury_status_fills_injury_detail():
    players = _player_df({"Name": "LeBron James", "InjuryStatus": ""})
    injuries = _injury_df({"player_name": "LeBron James", "status": "GTD", "detail": "ankle soreness"})

    result = match_injury_status(players, injuries)

    assert result.loc[0, "InjuryDetail"] == "ankle soreness"


def test_match_injury_status_leaves_detail_blank_when_db_has_none():
    players = _player_df({"Name": "LeBron James", "InjuryStatus": ""})
    injuries = _injury_df({"player_name": "LeBron James", "status": "OUT", "detail": ""})

    result = match_injury_status(players, injuries)

    assert result.loc[0, "InjuryStatus"] == "OUT"
    assert result.loc[0, "InjuryDetail"] == ""


# ──────────────────────────────────────────────────────────────────────────────
# match_injury_status — preservation logic
# ──────────────────────────────────────────────────────────────────────────────

def test_match_injury_status_preserves_explicit_status():
    """Pre-existing non-empty InjuryStatus must never be overwritten."""
    players = _player_df({"Name": "LeBron James", "InjuryStatus": "PROBABLE"})
    injuries = _injury_df({"player_name": "LeBron James", "status": "OUT"})

    result = match_injury_status(players, injuries)

    assert result.loc[0, "InjuryStatus"] == "PROBABLE"


def test_match_injury_status_mixed_preserves_and_fills():
    """One player already set, the other blank — only the blank one is filled."""
    players = _player_df(
        {"Name": "LeBron James", "InjuryStatus": "PROBABLE"},
        {"Name": "Stephen Curry", "InjuryStatus": ""},
    )
    injuries = _injury_df(
        {"player_name": "LeBron James", "status": "OUT"},
        {"player_name": "Stephen Curry", "status": "GTD"},
    )

    result = match_injury_status(players, injuries)

    assert result.loc[0, "InjuryStatus"] == "PROBABLE"
    assert result.loc[1, "InjuryStatus"] == "GTD"


# ──────────────────────────────────────────────────────────────────────────────
# match_injury_status — edge cases
# ──────────────────────────────────────────────────────────────────────────────

def test_match_injury_status_empty_injury_df_returns_unchanged():
    players = _player_df({"Name": "LeBron James", "InjuryStatus": ""})
    injuries = pd.DataFrame(columns=["player_name", "status", "detail"])

    result = match_injury_status(players, injuries)

    assert result.loc[0, "InjuryStatus"] == ""
    assert result.loc[0, "InjuryDetail"] == ""


def test_match_injury_status_player_not_in_db_stays_blank():
    players = _player_df({"Name": "Unknown Player", "InjuryStatus": ""})
    injuries = _injury_df({"player_name": "LeBron James", "status": "OUT"})

    result = match_injury_status(players, injuries)

    assert result.loc[0, "InjuryStatus"] == ""


def test_match_injury_status_duplicate_slug_uses_first():
    """When injury_df has duplicate slugs, drop_duplicates keeps the first row."""
    players = _player_df({"Name": "LeBron James", "InjuryStatus": ""})
    injuries = pd.DataFrame([
        {"player_name": "LeBron James", "status": "OUT", "detail": "knee", "team": "LAL", "game_date": None},
        {"player_name": "LeBron James", "status": "GTD", "detail": "rest",  "team": "LAL", "game_date": None},
    ])

    result = match_injury_status(players, injuries)

    # First row (OUT) wins
    assert result.loc[0, "InjuryStatus"] == "OUT"


def test_match_injury_status_accepts_player_id_column():
    """injury_df with player_id column (no player_name) should still work."""
    players = _player_df({"Name": "LeBron James", "InjuryStatus": ""})
    injuries = pd.DataFrame([
        {"player_id": "LeBron James", "status": "OUT", "detail": "", "team": "LAL", "game_date": None},
    ])

    result = match_injury_status(players, injuries)

    assert result.loc[0, "InjuryStatus"] == "OUT"


def test_match_injury_status_does_not_mutate_input():
    """The function must return a copy — original df stays clean."""
    players = _player_df({"Name": "LeBron James", "InjuryStatus": ""})
    original_status = players["InjuryStatus"].copy()
    injuries = _injury_df({"player_name": "LeBron James", "status": "OUT"})

    match_injury_status(players, injuries)

    pd.testing.assert_series_equal(players["InjuryStatus"], original_status)


def test_match_injury_status_name_col_detection_fuzzy():
    """Player df with non-standard 'Player Name' column is still auto-detected."""
    players = pd.DataFrame([{"Player Name": "LeBron James", "InjuryStatus": "", "InjuryDetail": ""}])
    injuries = _injury_df({"player_name": "LeBron James", "status": "GTD"})

    result = match_injury_status(players, injuries)

    assert result.loc[0, "InjuryStatus"] == "GTD"


# ──────────────────────────────────────────────────────────────────────────────
# build_injury_summary — counts
# ──────────────────────────────────────────────────────────────────────────────

def test_build_injury_summary_counts_out():
    injuries = _injury_df({"player_name": "LeBron James", "status": "OUT"})
    result = build_injury_summary(["LeBron James"], injuries)
    assert result["out_count"] == 1
    assert result["out_players"][0]["name"] == "LeBron James"


def test_build_injury_summary_counts_questionable():
    injuries = _injury_df({"player_name": "Stephen Curry", "status": "QUESTIONABLE"})
    result = build_injury_summary(["Stephen Curry"], injuries)
    assert result["questionable_count"] == 1
    assert result["questionable_players"][0]["name"] == "Stephen Curry"


def test_build_injury_summary_counts_gtd_as_questionable():
    injuries = _injury_df({"player_name": "Kevin Durant", "status": "GTD"})
    result = build_injury_summary(["Kevin Durant"], injuries)
    assert result["questionable_count"] == 1


def test_build_injury_summary_counts_doubtful():
    injuries = _injury_df({"player_name": "Giannis Antetokounmpo", "status": "DOUBTFUL"})
    result = build_injury_summary(["Giannis Antetokounmpo"], injuries)
    assert result["doubtful_count"] == 1
    assert result["doubtful_players"][0]["name"] == "Giannis Antetokounmpo"


def test_build_injury_summary_counts_probable():
    injuries = _injury_df({"player_name": "Joel Embiid", "status": "PROBABLE"})
    result = build_injury_summary(["Joel Embiid"], injuries)
    assert result["probable_count"] == 1


def test_build_injury_summary_mixed_statuses():
    injuries = _injury_df(
        {"player_name": "LeBron James", "status": "OUT"},
        {"player_name": "Stephen Curry", "status": "GTD"},
        {"player_name": "Kevin Durant", "status": "DOUBTFUL"},
        {"player_name": "Joel Embiid", "status": "PROBABLE"},
    )
    names = ["LeBron James", "Stephen Curry", "Kevin Durant", "Joel Embiid"]
    result = build_injury_summary(names, injuries)
    assert result["out_count"] == 1
    assert result["questionable_count"] == 1
    assert result["doubtful_count"] == 1
    assert result["probable_count"] == 1


# ──────────────────────────────────────────────────────────────────────────────
# build_injury_summary — edge cases
# ──────────────────────────────────────────────────────────────────────────────

def test_build_injury_summary_empty_injury_df_zero_counts():
    injuries = pd.DataFrame(columns=["player_name", "status", "detail", "team", "game_date"])
    result = build_injury_summary(["LeBron James"], injuries)
    assert result["out_count"] == 0
    assert result["questionable_count"] == 0
    assert result["all_injuries"] == []
    assert result["last_updated"] is None


def test_build_injury_summary_player_not_in_db_ignored():
    injuries = _injury_df({"player_name": "LeBron James", "status": "OUT"})
    result = build_injury_summary(["Unknown Player"], injuries)
    assert result["out_count"] == 0
    assert result["all_injuries"] == []


def test_build_injury_summary_last_updated_from_game_date():
    injuries = pd.DataFrame([
        {"player_name": "LeBron James", "status": "OUT", "detail": "", "team": "LAL",
         "game_date": "2026-03-10"},
        {"player_name": "Stephen Curry", "status": "GTD", "detail": "", "team": "GSW",
         "game_date": "2026-03-09"},
    ])
    result = build_injury_summary(["LeBron James", "Stephen Curry"], injuries)
    assert result["last_updated"] == "2026-03-10"


def test_build_injury_summary_all_injuries_populated():
    injuries = _injury_df(
        {"player_name": "LeBron James", "status": "OUT", "team": "LAL", "detail": "knee"},
    )
    result = build_injury_summary(["LeBron James"], injuries)
    assert len(result["all_injuries"]) == 1
    record = result["all_injuries"][0]
    assert record["team"] == "LAL"
    assert record["detail"] == "knee"
    assert record["status"] == "OUT"
