"""
Tests for the enhanced beneficiary engine, ownership suppression, and ownership scenarios.

These tests verify:
  1. Position-aware minute redistribution (same-position teammate gets more)
  2. reason_codes are correctly computed based on context
  3. p_play suppression in ownership_v2.predict_ownership()
  4. rebuild_ownership_scenarios writes exactly 3 rows per injured player
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from analysis.core.injury_intelligence import InjuryIntelligenceService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _insert_state(conn, *, player_id: str, player_name: str, team_id: str,
                  current_status: str, p_play: float, expected_minutes_mid: float,
                  position: str, p_start: float = 0.8, confidence_score: float = 0.85) -> None:
    """Directly insert a player_injury_state row for testing."""
    now = _now()
    from analysis.shared.db import write_lock
    with write_lock("nba_news"):
        conn.execute("DELETE FROM player_injury_state WHERE player_id = ?", [player_id])
        conn.execute(
            """
            INSERT INTO player_injury_state (
                player_id, player_name, team_id, game_id, current_status,
                p_play, expected_minutes_low, expected_minutes_mid,
                expected_minutes_high, p_start, p_limited, p_late_scratch,
                confidence_score, news_quality_score, source_agreement_score,
                staleness_score, last_event_at, state_version,
                arbitration_context, updated_at, position
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                player_id, player_name, team_id, "", current_status,
                p_play,
                max(0.0, expected_minutes_mid - 5.0),   # low
                expected_minutes_mid,                    # mid
                expected_minutes_mid + 4.0,              # high
                p_start, 0.10, 0.20,
                confidence_score, 0.8, 0.9, 0.9,
                now, 1, "{}", now, position,
            ],
        )


# ---------------------------------------------------------------------------
# Test 1: Position-aware beneficiary weighting
# ---------------------------------------------------------------------------

def test_beneficiary_position_weights(tmp_path: Path) -> None:
    """Same-position teammate should receive a larger delta_minutes than a
    different-position teammate when both start from equal baseline minutes."""
    db_path = tmp_path / "nba_news.duckdb"
    service = InjuryIntelligenceService(db_path)
    conn = service._conn()

    # Injured PG (GTD-like: p_play = 0.20 → p_out ≈ 0.80)
    _insert_state(conn, player_id="injured_pg", player_name="Injured PG",
                  team_id="BOS", current_status="GTD", p_play=0.20,
                  expected_minutes_mid=30.0, position="PG")

    # Teammate PG (same position — should get 1.6× weight)
    _insert_state(conn, player_id="teammate_pg", player_name="Teammate PG",
                  team_id="BOS", current_status="ACTIVE", p_play=0.95,
                  expected_minutes_mid=24.0, position="PG")

    # Teammate C (different position — 1.0× weight)
    _insert_state(conn, player_id="teammate_c", player_name="Teammate C",
                  team_id="BOS", current_status="ACTIVE", p_play=0.95,
                  expected_minutes_mid=24.0, position="C")

    count = service.rebuild_beneficiaries(["injured_pg"])
    assert count == 2, f"Expected 2 beneficiary rows, got {count}"

    rows = conn.execute(
        "SELECT beneficiary_player_id, delta_minutes, rank_score FROM injury_beneficiaries WHERE player_id = ? ORDER BY rank_score DESC",
        ["injured_pg"],
    ).df()

    assert len(rows) == 2
    pg_row = rows[rows["beneficiary_player_id"] == "teammate_pg"].iloc[0]
    c_row  = rows[rows["beneficiary_player_id"] == "teammate_c"].iloc[0]

    # Same-position PG should have more delta_minutes than the C
    assert float(pg_row["delta_minutes"]) > float(c_row["delta_minutes"]), (
        f"PG delta_minutes {pg_row['delta_minutes']} should exceed C "
        f"delta_minutes {c_row['delta_minutes']} due to 1.6× position weight"
    )
    # PG should rank first
    assert float(pg_row["rank_score"]) > float(c_row["rank_score"])


# ---------------------------------------------------------------------------
# Test 2: Reason codes — likely_starter, same_position_overlap
# ---------------------------------------------------------------------------

def test_reason_codes_likely_starter_and_position(tmp_path: Path) -> None:
    """Rank-1 beneficiary should get 'likely_starter' when injured p_start >= 0.7,
    and same-position teammates should get 'same_position_overlap'."""
    db_path = tmp_path / "nba_news.duckdb"
    service = InjuryIntelligenceService(db_path)
    conn = service._conn()

    # Injured SG with high p_start (starter-quality player)
    _insert_state(conn, player_id="injured_sg", player_name="Injured SG",
                  team_id="LAL", current_status="OUT", p_play=0.02,
                  expected_minutes_mid=32.0, position="SG", p_start=0.90,
                  confidence_score=0.90)

    # Rank-1 candidate: SG (same pos, high expected minutes)
    _insert_state(conn, player_id="backup_sg", player_name="Backup SG",
                  team_id="LAL", current_status="ACTIVE", p_play=0.95,
                  expected_minutes_mid=20.0, position="SG")

    # Rank-2: PF (different pos, lower expected minutes)
    _insert_state(conn, player_id="pf_player", player_name="PF Player",
                  team_id="LAL", current_status="ACTIVE", p_play=0.95,
                  expected_minutes_mid=12.0, position="PF")

    # Override expected_minutes_mid for injured to be non-zero despite OUT status
    # (the state was inserted directly above so expected_minutes_mid = 32.0)
    service.rebuild_beneficiaries(["injured_sg"])

    rows = conn.execute(
        "SELECT beneficiary_player_id, reason_codes FROM injury_beneficiaries WHERE player_id = ? ORDER BY rank_score DESC",
        ["injured_sg"],
    ).df()

    assert not rows.empty, "No beneficiary rows found"

    sg_row = rows[rows["beneficiary_player_id"] == "backup_sg"].iloc[0]
    pf_row = rows[rows["beneficiary_player_id"] == "pf_player"].iloc[0]

    sg_reasons = json.loads(str(sg_row["reason_codes"]))
    pf_reasons = json.loads(str(pf_row["reason_codes"]))

    # SG is same-position and rank 1 → should get both likely_starter and same_position_overlap
    assert "same_position_overlap" in sg_reasons, f"SG reasons: {sg_reasons}"

    # PF is different position → should NOT have same_position_overlap
    assert "same_position_overlap" not in pf_reasons, f"PF reasons: {pf_reasons}"


# ---------------------------------------------------------------------------
# Test 3: rebuild_ownership_scenarios writes 3 rows per injured player
# ---------------------------------------------------------------------------

def test_rebuild_ownership_scenarios_writes_three_rows(tmp_path: Path) -> None:
    """After syncing an injury, ownership_scenarios must have 3 rows per player
    (one each for OUT, IN_LIMITED, IN_FULL scenarios)."""
    db_path = tmp_path / "nba_news.duckdb"
    service = InjuryIntelligenceService(db_path)

    inj_df = pd.DataFrame([
        {
            "player_id": "kd_test",
            "player_name": "KD Test",
            "status": "GTD",
            "detail": "Knee load management",
            "team": "PHX",
            "game_date": "2026-03-23",
            "confidence": 0.80,
        }
    ])

    service.sync_current_injuries(inj_df, source="official_report")

    conn = service._conn()
    rows = conn.execute(
        "SELECT scenario_name FROM ownership_scenarios WHERE player_id = ?",
        ["kd_test"],
    ).df()

    assert len(rows) == 3, f"Expected 3 ownership scenario rows, got {len(rows)}: {rows['scenario_name'].tolist()}"
    scenarios = set(rows["scenario_name"].tolist())
    assert scenarios == {"OUT", "IN_LIMITED", "IN_FULL"}, f"Unexpected scenarios: {scenarios}"


# ---------------------------------------------------------------------------
# Test 4: p_play ownership suppression in ownership_v2
# ---------------------------------------------------------------------------

def test_pplay_suppression_scales_ownership() -> None:
    """A GTD player with p_play = 0.40 should get Own_Est ≈ 40% of a healthy player
    with an otherwise identical projection/salary profile."""
    from analysis.nba.ownership_v2 import _apply_pplay_suppression

    # Build a tiny projections df: two similar players
    proj_df = pd.DataFrame([
        {"Name": "Healthy Player", "Proj": 40.0, "Salary": 8000, "Own_Est": 20.0},
        {"Name": "GTD Player",     "Proj": 40.0, "Salary": 8000, "Own_Est": 20.0},
    ])

    states_df = pd.DataFrame([
        {"player_name": "GTD Player", "p_play": 0.40},
    ])

    result = _apply_pplay_suppression(proj_df, states_df)

    healthy_own = float(result.loc[result["Name"] == "Healthy Player", "Own_Est"].iloc[0])
    gtd_own     = float(result.loc[result["Name"] == "GTD Player",     "Own_Est"].iloc[0])

    # Healthy player should be unchanged at 20.0
    assert abs(healthy_own - 20.0) < 1e-6, f"Healthy player Own_Est changed: {healthy_own}"

    # GTD player should be suppressed to ~40% of 20.0 = 8.0
    assert abs(gtd_own - 8.0) < 0.5, f"GTD player Own_Est = {gtd_own}, expected ~8.0"


# ---------------------------------------------------------------------------
# Test 5: High-confidence OUT player gets reason_codes for high_confidence_beneficiary
# ---------------------------------------------------------------------------

def test_reason_codes_high_confidence_beneficiary(tmp_path: Path) -> None:
    """When p_out >= 0.7 and rank <= 2, reason_codes should contain
    'high_confidence_beneficiary'."""
    db_path = tmp_path / "nba_news.duckdb"
    service = InjuryIntelligenceService(db_path)
    conn = service._conn()

    # Injured OUT with high confidence
    _insert_state(conn, player_id="out_player", player_name="Out Player",
                  team_id="MIA", current_status="OUT", p_play=0.02,
                  expected_minutes_mid=28.0, position="SF", p_start=0.80,
                  confidence_score=0.90)

    _insert_state(conn, player_id="ben_player", player_name="Ben Player",
                  team_id="MIA", current_status="ACTIVE", p_play=0.95,
                  expected_minutes_mid=22.0, position="SF")

    service.rebuild_beneficiaries(["out_player"])

    rows = conn.execute(
        "SELECT reason_codes FROM injury_beneficiaries WHERE player_id = ? AND beneficiary_player_id = ?",
        ["out_player", "ben_player"],
    ).fetchone()

    assert rows is not None
    reasons = json.loads(rows[0])
    # p_out ≈ 0.98 >= 0.7 and rank=1 → should include high_confidence_beneficiary
    assert "high_confidence_beneficiary" in reasons, f"Reasons: {reasons}"
