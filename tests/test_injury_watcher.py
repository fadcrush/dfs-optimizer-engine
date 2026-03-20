"""
Phase 23 tests: InjuryWatcher real-time polling.

Covers:
  - PollResult.actionable_signals    — filters to OUT / GTD only
  - PollResult.has_changes           — True ↔ signals non-empty
  - InjuryWatcher.snapshot_from_df   — name normalisation, empty df, missing cols,
                                       status normalisation via InjuryStatus.from_raw
  - InjuryWatcher.poll               — first poll (no prev), change detection,
                                       no-change returns empty signals,
                                       recovery not emitted, multiple changes,
                                       graceful degradation on empty df,
                                       internal snapshot updated after poll,
                                       snapshot NOT updated when df is empty,
                                       custom timestamp preserved in signals
  - InjuryWatcher.reset              — clears internal snapshot
  - InjuryWatcher.current_snapshot   — returns copy (mutations don't affect internals)
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from analysis.core.injury_watcher import InjuryWatcher, PollResult
from analysis.core.late_swap import SwapSignal
from analysis.schemas.player import InjuryStatus


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _df(*rows: dict) -> pd.DataFrame:
    """Build a small injury DataFrame from keyword-argument dicts."""
    return pd.DataFrame(list(rows))


def _injury_row(name: str, status: str) -> dict:
    return {"player_name": name, "status": status}


# ──────────────────────────────────────────────────────────────────────────────
# PollResult properties
# ──────────────────────────────────────────────────────────────────────────────

def test_poll_result_actionable_signals_filters_to_out_and_gtd():
    ts = datetime(2026, 1, 1)
    signals = [
        SwapSignal("A", "ACTIVE", InjuryStatus.OUT.value,                ts),
        SwapSignal("B", "ACTIVE", InjuryStatus.GAME_TIME_DECISION.value,  ts),
        SwapSignal("C", "ACTIVE", InjuryStatus.QUESTIONABLE.value,        ts),
        SwapSignal("D", "ACTIVE", InjuryStatus.DOUBTFUL.value,            ts),
    ]
    result = PollResult(signals=signals, snapshot={}, polled_at=ts, player_count=4)
    actionable = result.actionable_signals
    assert len(actionable) == 2
    assert all(s.is_actionable for s in actionable)


def test_poll_result_has_changes_true_when_signals_nonempty():
    ts = datetime(2026, 1, 1)
    s = SwapSignal("A", "ACTIVE", InjuryStatus.OUT.value, ts)
    result = PollResult(signals=[s], snapshot={}, polled_at=ts, player_count=1)
    assert result.has_changes is True


def test_poll_result_has_changes_false_when_signals_empty():
    ts = datetime(2026, 1, 1)
    result = PollResult(signals=[], snapshot={}, polled_at=ts, player_count=0)
    assert result.has_changes is False


# ──────────────────────────────────────────────────────────────────────────────
# InjuryWatcher.snapshot_from_df
# ──────────────────────────────────────────────────────────────────────────────

def test_snapshot_from_df_basic():
    watcher = InjuryWatcher()
    df = _df(
        _injury_row("LeBron James", "OUT"),
        _injury_row("Kevin Durant", "ACTIVE"),
    )
    snap = watcher.snapshot_from_df(df)
    assert snap["LeBron James"] == InjuryStatus.OUT.value
    assert snap["Kevin Durant"] == InjuryStatus.ACTIVE.value


def test_snapshot_from_df_normalises_raw_aliases():
    """'O' → OUT, 'GTD' → GTD, 'Q' → Q, etc."""
    watcher = InjuryWatcher()
    df = _df(
        _injury_row("Player A", "O"),
        _injury_row("Player B", "GTD"),
        _injury_row("Player C", "Q"),
    )
    snap = watcher.snapshot_from_df(df)
    assert snap["Player A"] == InjuryStatus.OUT.value
    assert snap["Player B"] == InjuryStatus.GAME_TIME_DECISION.value
    assert snap["Player C"] == InjuryStatus.QUESTIONABLE.value


def test_snapshot_from_df_skips_empty_names():
    watcher = InjuryWatcher()
    df = _df(
        _injury_row("", "OUT"),
        _injury_row("   ", "ACTIVE"),
        _injury_row("Real Player", "ACTIVE"),
    )
    snap = watcher.snapshot_from_df(df)
    assert list(snap.keys()) == ["Real Player"]


def test_snapshot_from_df_empty_dataframe_returns_empty():
    watcher = InjuryWatcher()
    snap = watcher.snapshot_from_df(pd.DataFrame())
    assert snap == {}


def test_snapshot_from_df_missing_name_col_returns_empty():
    watcher = InjuryWatcher()
    df = pd.DataFrame([{"wrong_col": "Player A", "status": "OUT"}])
    snap = watcher.snapshot_from_df(df)
    assert snap == {}


def test_snapshot_from_df_missing_status_col_maps_to_unknown():
    """When there's no status column the status normalises to UNKNOWN (empty)."""
    watcher = InjuryWatcher()
    df = pd.DataFrame([{"player_name": "Player A"}])
    snap = watcher.snapshot_from_df(df)
    assert snap["Player A"] == InjuryStatus.UNKNOWN.value


# ──────────────────────────────────────────────────────────────────────────────
# InjuryWatcher.poll
# ──────────────────────────────────────────────────────────────────────────────

def test_poll_first_call_no_prev_emits_signal_for_non_active():
    """First poll: players with elevated status are detected (prev is empty → all ACTIVE baseline)."""
    watcher = InjuryWatcher()
    df = _df(
        _injury_row("Player OUT",  "OUT"),
        _injury_row("Player ACT",  "ACTIVE"),
    )
    result = watcher.poll(injury_df=df)
    names = [s.player_name for s in result.signals]
    assert "Player OUT" in names
    assert "Player ACT" not in names


def test_poll_no_change_returns_empty_signals():
    watcher = InjuryWatcher()
    df = _df(_injury_row("Player A", "OUT"))
    watcher.poll(injury_df=df)           # first poll — OUT detected

    result = watcher.poll(injury_df=df)  # second poll — same data
    assert result.signals == []
    assert result.has_changes is False


def test_poll_detects_new_out():
    watcher = InjuryWatcher()
    df_before = _df(_injury_row("Player A", "ACTIVE"))
    watcher.poll(injury_df=df_before)

    df_after = _df(_injury_row("Player A", "OUT"))
    result = watcher.poll(injury_df=df_after)
    assert len(result.signals) == 1
    s = result.signals[0]
    assert s.player_name  == "Player A"
    assert s.old_status   == InjuryStatus.ACTIVE.value
    assert s.new_status   == InjuryStatus.OUT.value


def test_poll_ignores_recovery():
    """OUT → ACTIVE is a severity decrease — must not produce a signal."""
    watcher = InjuryWatcher()
    df_before = _df(_injury_row("Player A", "OUT"))
    watcher.poll(injury_df=df_before)

    df_after = _df(_injury_row("Player A", "ACTIVE"))
    result = watcher.poll(injury_df=df_after)
    assert result.signals == []


def test_poll_detects_multiple_simultaneous_changes():
    watcher = InjuryWatcher()
    df_before = _df(
        _injury_row("Player A", "ACTIVE"),
        _injury_row("Player B", "ACTIVE"),
    )
    watcher.poll(injury_df=df_before)

    df_after = _df(
        _injury_row("Player A", "OUT"),
        _injury_row("Player B", "GTD"),
    )
    result = watcher.poll(injury_df=df_after)
    assert len(result.signals) == 2
    names = {s.player_name for s in result.signals}
    assert names == {"Player A", "Player B"}


def test_poll_empty_df_graceful_degradation():
    """Empty DataFrame must not raise, must not clear the stored snapshot."""
    watcher = InjuryWatcher()
    df_initial = _df(_injury_row("Player A", "ACTIVE"))
    watcher.poll(injury_df=df_initial)

    result = watcher.poll(injury_df=pd.DataFrame())
    assert result.signals       == []
    assert result.player_count  == 0
    # Stored snapshot should still be the previous valid one
    assert watcher.current_snapshot["Player A"] == InjuryStatus.ACTIVE.value


def test_poll_updates_internal_snapshot():
    watcher = InjuryWatcher()
    df = _df(_injury_row("Player A", "Q"))
    watcher.poll(injury_df=df)
    assert watcher.current_snapshot["Player A"] == InjuryStatus.QUESTIONABLE.value


def test_poll_custom_timestamp_preserved_in_signals():
    ts = datetime(2026, 3, 15, 9, 30, 0)
    watcher = InjuryWatcher()
    df = _df(_injury_row("Player A", "OUT"))
    result = watcher.poll(injury_df=df, timestamp=ts)
    assert result.polled_at == ts
    assert result.signals[0].timestamp == ts


def test_poll_source_label_propagated_to_signals():
    watcher = InjuryWatcher(source="sportsdata")
    df = _df(_injury_row("Player A", "OUT"))
    result = watcher.poll(injury_df=df)
    assert result.signals[0].source == "sportsdata"


def test_poll_player_count_reflects_current_df():
    watcher = InjuryWatcher()
    df = _df(
        _injury_row("A", "ACTIVE"),
        _injury_row("B", "OUT"),
        _injury_row("C", "Q"),
    )
    result = watcher.poll(injury_df=df)
    assert result.player_count == 3


# ──────────────────────────────────────────────────────────────────────────────
# InjuryWatcher.reset & current_snapshot
# ──────────────────────────────────────────────────────────────────────────────

def test_reset_clears_snapshot():
    watcher = InjuryWatcher()
    df = _df(_injury_row("Player A", "ACTIVE"))
    watcher.poll(injury_df=df)
    assert watcher.current_snapshot != {}

    watcher.reset()
    assert watcher.current_snapshot == {}


def test_reset_causes_next_poll_to_behave_as_first():
    """After reset, an OUT player in the next poll should produce a signal again."""
    watcher = InjuryWatcher()
    df_out = _df(_injury_row("Player A", "OUT"))

    # First poll: signal emitted
    result1 = watcher.poll(injury_df=df_out)
    assert result1.has_changes

    # Second poll with same data: no new signal (already in snapshot)
    result2 = watcher.poll(injury_df=df_out)
    assert not result2.has_changes

    # Reset and re-poll: signal emitted again
    watcher.reset()
    result3 = watcher.poll(injury_df=df_out)
    assert result3.has_changes
    assert result3.signals[0].player_name == "Player A"


def test_current_snapshot_returns_copy():
    """Mutating the returned snapshot must not change internal state."""
    watcher = InjuryWatcher()
    df = _df(_injury_row("Player A", "ACTIVE"))
    watcher.poll(injury_df=df)

    snap = watcher.current_snapshot
    snap["injected_key"] = "TAMPERED"

    assert "injected_key" not in watcher.current_snapshot
