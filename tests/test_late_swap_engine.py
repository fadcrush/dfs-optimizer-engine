"""
Phase 22 tests: LateSwapEngine core module.

Covers:
  - SwapSignal.severity ordering  (OUT > D > GTD > Q > P > ACTIVE)
  - SwapSignal.is_actionable      (OUT and GTD only)
  - LateSwapEngine.detect_changes : new OUT, recovery, unchanged, multiple players,
                                    custom timestamp / source, empty prev
  - LateSwapEngine.find_replacements : basic matching, slot labels, locked players,
                                        salary cap, existing lineup exclusion,
                                        proj_delta / salary_delta correctness,
                                        max_candidates cap, swap_score range,
                                        empty pool
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from analysis.core.late_swap import (
    LateSwapEngine,
    SwapRecommendation,
    SwapSignal,
)
from analysis.schemas.player import InjuryStatus


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def basic_pool() -> pd.DataFrame:
    """Small projection pool for replacement tests."""
    return pd.DataFrame(
        [
            {"Name": "PG Candidate",  "Pos": "PG",    "Salary": 5900, "Proj": 32.0, "Own": 12.0},
            {"Name": "SG Candidate",  "Pos": "SG",    "Salary": 5800, "Proj": 29.5, "Own": 10.0},
            {"Name": "SF Candidate",  "Pos": "SF",    "Salary": 5500, "Proj": 27.0, "Own":  8.0},
            {"Name": "Over Budget",   "Pos": "PG",    "Salary": 9999, "Proj": 50.0, "Own":  5.0},
            {"Name": "Scratched Guy", "Pos": "PG/SG", "Salary": 6000, "Proj": 28.0, "Own": 15.0},
        ]
    )


# ──────────────────────────────────────────────────────────────────────────────
# SwapSignal — severity and actionability
# ──────────────────────────────────────────────────────────────────────────────

def test_swap_signal_severity_ordering():
    """OUT > DOUBTFUL > GTD > QUESTIONABLE > PROBABLE > ACTIVE."""
    ts = datetime(2026, 1, 1)

    def make(s: str) -> SwapSignal:
        return SwapSignal("Player", InjuryStatus.ACTIVE.value, s, ts)

    assert make(InjuryStatus.OUT.value).severity                == 5
    assert make(InjuryStatus.DOUBTFUL.value).severity           == 4
    assert make(InjuryStatus.GAME_TIME_DECISION.value).severity == 3
    assert make(InjuryStatus.QUESTIONABLE.value).severity       == 2
    assert make(InjuryStatus.PROBABLE.value).severity           == 1
    assert make(InjuryStatus.ACTIVE.value).severity             == 0


def test_swap_signal_is_actionable_only_for_out_and_gtd():
    ts = datetime(2026, 1, 1)

    assert SwapSignal("P", "ACTIVE", InjuryStatus.OUT.value, ts).is_actionable               is True
    assert SwapSignal("P", "ACTIVE", InjuryStatus.GAME_TIME_DECISION.value, ts).is_actionable is True
    assert SwapSignal("P", "ACTIVE", InjuryStatus.QUESTIONABLE.value, ts).is_actionable       is False
    assert SwapSignal("P", "ACTIVE", InjuryStatus.DOUBTFUL.value, ts).is_actionable           is False
    assert SwapSignal("P", "ACTIVE", InjuryStatus.ACTIVE.value, ts).is_actionable             is False


# ──────────────────────────────────────────────────────────────────────────────
# LateSwapEngine.detect_changes
# ──────────────────────────────────────────────────────────────────────────────

def test_detect_changes_new_out_player():
    """Player absent from prev defaults to ACTIVE; becoming OUT emits a signal."""
    signals = LateSwapEngine.detect_changes({}, {"LeBron James": "OUT"})
    assert len(signals) == 1
    s = signals[0]
    assert s.player_name  == "LeBron James"
    assert s.old_status   == InjuryStatus.ACTIVE.value
    assert s.new_status   == InjuryStatus.OUT.value
    assert s.is_actionable is True


def test_detect_changes_no_change_returns_empty():
    prev = {"Player A": "ACTIVE", "Player B": "Q"}
    curr = {"Player A": "ACTIVE", "Player B": "Q"}
    assert LateSwapEngine.detect_changes(prev, curr) == []


def test_detect_changes_recovery_does_not_emit_signal():
    """OUT → ACTIVE is a *decrease* in severity and must not produce a signal."""
    prev = {"Player A": "OUT"}
    curr = {"Player A": "ACTIVE"}
    assert LateSwapEngine.detect_changes(prev, curr) == []


def test_detect_changes_multiple_players_sorted_by_severity():
    """Multiple status changes are returned most-severe-first."""
    prev = {}
    curr = {
        "Player Q":   "Q",
        "Player OUT": "OUT",
        "Player GTD": "GTD",
    }
    signals = LateSwapEngine.detect_changes(prev, curr)
    assert len(signals) == 3
    assert signals[0].new_status == InjuryStatus.OUT.value
    assert signals[1].new_status == InjuryStatus.GAME_TIME_DECISION.value
    assert signals[2].new_status == InjuryStatus.QUESTIONABLE.value


def test_detect_changes_same_severity_no_signal():
    """Q → QUESTIONABLE (both map to the same InjuryStatus) must not emit a signal."""
    prev = {"Player A": "Q"}
    curr = {"Player A": "QUESTIONABLE"}
    assert LateSwapEngine.detect_changes(prev, curr) == []


def test_detect_changes_custom_timestamp():
    ts = datetime(2026, 3, 15, 12, 0, 0)
    signals = LateSwapEngine.detect_changes({}, {"Player": "OUT"}, timestamp=ts)
    assert signals[0].timestamp == ts


def test_detect_changes_custom_source():
    signals = LateSwapEngine.detect_changes({}, {"Player": "OUT"}, source="sportsdata")
    assert signals[0].source == "sportsdata"


def test_detect_changes_active_to_questionable_emits_signal():
    """Q is more severe than ACTIVE, so this must produce a signal."""
    signals = LateSwapEngine.detect_changes({"Player": "ACTIVE"}, {"Player": "Q"})
    assert len(signals) == 1
    assert signals[0].new_status == InjuryStatus.QUESTIONABLE.value


# ──────────────────────────────────────────────────────────────────────────────
# LateSwapEngine.find_replacements
# ──────────────────────────────────────────────────────────────────────────────

def test_find_replacements_basic(basic_pool):
    """PG/SG scratched → PG and SG candidates included; SF and self excluded."""
    recs = LateSwapEngine.find_replacements(
        scratched_name="Scratched Guy",
        scratched_pos="PG/SG",
        scratched_salary=6000,
        scratched_proj=28.0,
        scratched_own=15.0,
        lineup_names=["Scratched Guy"],
        pool=basic_pool,
        salary_cap=50_000,
        current_lineup_salary=6000,
    )
    names = [r.replacement_player for r in recs]
    assert "PG Candidate"  in names
    assert "SG Candidate"  in names
    assert "SF Candidate"  not in names   # SF cannot fill PG or SG slots
    assert "Scratched Guy" not in names   # self excluded


def test_find_replacements_empty_pool_returns_empty():
    recs = LateSwapEngine.find_replacements(
        scratched_name="Player",
        scratched_pos="PG",
        scratched_salary=5000,
        scratched_proj=25.0,
        scratched_own=10.0,
        lineup_names=["Player"],
        pool=pd.DataFrame(),
        salary_cap=50_000,
        current_lineup_salary=5000,
    )
    assert recs == []


def test_find_replacements_excludes_locked(basic_pool):
    recs = LateSwapEngine.find_replacements(
        scratched_name="Scratched Guy",
        scratched_pos="PG/SG",
        scratched_salary=6000,
        scratched_proj=28.0,
        scratched_own=15.0,
        lineup_names=["Scratched Guy"],
        pool=basic_pool,
        salary_cap=50_000,
        current_lineup_salary=6000,
        locked={"PG Candidate"},
    )
    names = [r.replacement_player for r in recs]
    assert "PG Candidate" not in names
    assert "SG Candidate"  in names


def test_find_replacements_excludes_existing_lineup_players(basic_pool):
    """Players already in the lineup (other than scratched) cannot be replacements."""
    recs = LateSwapEngine.find_replacements(
        scratched_name="Scratched Guy",
        scratched_pos="PG/SG",
        scratched_salary=6000,
        scratched_proj=28.0,
        scratched_own=15.0,
        lineup_names=["Scratched Guy", "SG Candidate"],
        pool=basic_pool,
        salary_cap=50_000,
        current_lineup_salary=11800,
    )
    names = [r.replacement_player for r in recs]
    assert "SG Candidate" not in names


def test_find_replacements_excludes_over_budget(basic_pool):
    """Total lineup salary would exceed the cap → candidate excluded."""
    recs = LateSwapEngine.find_replacements(
        scratched_name="Scratched Guy",
        scratched_pos="PG",
        scratched_salary=6000,
        scratched_proj=28.0,
        scratched_own=15.0,
        lineup_names=["Scratched Guy"],
        pool=basic_pool,
        salary_cap=50_000,
        current_lineup_salary=49_800,  # budget = 50000 - 49800 + 6000 = 6200; Over Budget costs 9999
    )
    names = [r.replacement_player for r in recs]
    assert "Over Budget" not in names


def test_find_replacements_slot_label_filters_position(basic_pool):
    """slot_labels=["PG"] restricts candidates to PG-eligible players only,
    while still requiring them to be within the salary budget."""
    recs = LateSwapEngine.find_replacements(
        scratched_name="Scratched Guy",
        scratched_pos="PG/SG",
        scratched_salary=6000,
        scratched_proj=28.0,
        scratched_own=15.0,
        lineup_names=["Scratched Guy"],
        pool=basic_pool,
        salary_cap=50_000,
        current_lineup_salary=47_000,   # budget = 50000 - 47000 + 6000 = 9000; Over Budget (9999) excluded
        slot_labels=["PG"],
    )
    names = [r.replacement_player for r in recs]
    assert names == ["PG Candidate"]


def test_find_replacements_proj_delta_and_salary_delta_correct(basic_pool):
    recs = LateSwapEngine.find_replacements(
        scratched_name="Scratched Guy",
        scratched_pos="PG",
        scratched_salary=6000,
        scratched_proj=28.0,
        scratched_own=15.0,
        lineup_names=["Scratched Guy"],
        pool=basic_pool,
        salary_cap=50_000,
        current_lineup_salary=6000,
    )
    pg = next(r for r in recs if r.replacement_player == "PG Candidate")
    assert pg.proj_delta   == pytest.approx(32.0 - 28.0)
    assert pg.salary_delta == 5900 - 6000


def test_find_replacements_respects_max_candidates(basic_pool):
    recs = LateSwapEngine.find_replacements(
        scratched_name="Scratched Guy",
        scratched_pos="PG/SG",
        scratched_salary=6000,
        scratched_proj=28.0,
        scratched_own=15.0,
        lineup_names=["Scratched Guy"],
        pool=basic_pool,
        salary_cap=50_000,
        current_lineup_salary=6000,
        max_candidates=1,
    )
    assert len(recs) == 1


def test_find_replacements_swap_score_in_range(basic_pool):
    """swap_score is a rank-weighted composite in [0, 1]."""
    recs = LateSwapEngine.find_replacements(
        scratched_name="Scratched Guy",
        scratched_pos="PG/SG",
        scratched_salary=6000,
        scratched_proj=28.0,
        scratched_own=15.0,
        lineup_names=["Scratched Guy"],
        pool=basic_pool,
        salary_cap=50_000,
        current_lineup_salary=6000,
    )
    for rec in recs:
        assert 0.0 <= rec.swap_score <= 1.0, f"swap_score {rec.swap_score} out of expected range"


def test_find_replacements_ownership_delta_correct(basic_pool):
    recs = LateSwapEngine.find_replacements(
        scratched_name="Scratched Guy",
        scratched_pos="PG/SG",
        scratched_salary=6000,
        scratched_proj=28.0,
        scratched_own=15.0,
        lineup_names=["Scratched Guy"],
        pool=basic_pool,
        salary_cap=50_000,
        current_lineup_salary=6000,
    )
    pg = next(r for r in recs if r.replacement_player == "PG Candidate")
    assert pg.ownership_delta == pytest.approx(12.0 - 15.0)
