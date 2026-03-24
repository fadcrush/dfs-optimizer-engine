from __future__ import annotations

from pathlib import Path

import pandas as pd

from analysis.core.injury_intelligence import InjuryIntelligenceService


def _injury_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "player_id": "lebron_james",
                "player_name": "LeBron James",
                "status": "OUT",
                "detail": "Ankle soreness",
                "team": "LAL",
                "game_date": "2026-03-23",
                "confidence": 0.95,
            },
            {
                "player_id": "anthony_davis",
                "player_name": "Anthony Davis",
                "status": "Q",
                "detail": "Warmup decision",
                "team": "LAL",
                "game_date": "2026-03-23",
                "confidence": 0.90,
            },
        ]
    )


def test_sync_current_injuries_inserts_and_dedupes(tmp_path: Path) -> None:
    db_path = tmp_path / "nba_news.duckdb"
    service = InjuryIntelligenceService(db_path)

    first = service.sync_current_injuries(_injury_df(), source="official_report")
    second = service.sync_current_injuries(_injury_df(), source="official_report")

    assert first["built_events"] == 2
    assert first["inserted_events"] == 2
    assert second["inserted_events"] == 0


def test_rebuild_states_creates_probabilistic_state(tmp_path: Path) -> None:
    db_path = tmp_path / "nba_news.duckdb"
    service = InjuryIntelligenceService(db_path)
    service.sync_current_injuries(_injury_df(), source="official_report")

    states = service.load_player_states_df()
    assert not states.empty

    lebron = states.loc[states["player_id"] == "lebron_james"].iloc[0]
    ad = states.loc[states["player_id"] == "anthony_davis"].iloc[0]

    assert lebron["current_status"] == "OUT"
    assert float(lebron["p_play"]) < 0.10
    assert float(lebron["expected_minutes_mid"]) == 0.0

    assert ad["current_status"] == "Q"
    assert 0.40 < float(ad["p_play"]) < 0.90
    assert float(ad["p_limited"]) > 0.20


def test_scenarios_sum_to_one(tmp_path: Path) -> None:
    db_path = tmp_path / "nba_news.duckdb"
    service = InjuryIntelligenceService(db_path)
    service.sync_current_injuries(_injury_df(), source="official_report")

    conn = service._conn()
    scenarios = conn.execute(
        "SELECT scenario_name, scenario_probability FROM injury_scenarios WHERE player_id = ?",
        ["anthony_davis"],
    ).df()

    assert set(scenarios["scenario_name"].tolist()) == {"OUT", "IN_LIMITED", "IN_FULL"}
    assert abs(float(scenarios["scenario_probability"].sum()) - 1.0) < 1e-6


def test_noise_classification_for_low_impact_active_event(tmp_path: Path) -> None:
    db_path = tmp_path / "nba_news.duckdb"
    service = InjuryIntelligenceService(db_path)
    df = pd.DataFrame(
        [
            {
                "player_id": "jayson_tatum",
                "player_name": "Jayson Tatum",
                "status": "ACTIVE",
                "detail": "",
                "team": "BOS",
                "game_date": "2026-03-23",
                "confidence": 0.90,
            }
        ]
    )

    service.sync_current_injuries(df, source="official_report")
    conn = service._conn()
    row = conn.execute(
        "SELECT event_classification FROM injury_events WHERE player_id = ?",
        ["jayson_tatum"],
    ).fetchone()

    assert row is not None
    assert row[0] == "noise"


def test_higher_priority_out_event_wins_arbitration(tmp_path: Path) -> None:
    db_path = tmp_path / "nba_news.duckdb"
    service = InjuryIntelligenceService(db_path)

    beat_df = pd.DataFrame(
        [
            {
                "player_id": "jimmy_butler",
                "player_name": "Jimmy Butler",
                "status": "Q",
                "detail": "Beat reporter concern",
                "team": "MIA",
                "game_date": "2026-03-23",
                "confidence": 0.70,
            }
        ]
    )
    official_df = pd.DataFrame(
        [
            {
                "player_id": "jimmy_butler",
                "player_name": "Jimmy Butler",
                "status": "OUT",
                "detail": "Officially ruled out",
                "team": "MIA",
                "game_date": "2026-03-23",
                "confidence": 0.98,
            }
        ]
    )

    service.sync_current_injuries(beat_df, source="beat_report")
    service.sync_current_injuries(official_df, source="official_lineup")

    states = service.load_player_states_df()
    row = states.loc[states["player_id"] == "jimmy_butler"].iloc[0]
    assert row["current_status"] == "OUT"
    assert float(row["confidence_score"]) > 0.50