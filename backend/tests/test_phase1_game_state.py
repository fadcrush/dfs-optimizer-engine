"""
Phase 1 Gate Tests — Canonical Game State
==========================================

Verifies that the three Phase 1 contracts hold:

  1. compute_game_state derives started_flag / locked_flag / swap_eligible
     correctly from slate data and a reference clock.

  2. LateSwapEngine.find_replacements excludes candidates whose game has
     already started (started_flag=True) when game_state is supplied.

  3. optimize_portfolio auto-locks players with started_flag=True so the
     solver cannot remove them from lineups.

These are *pure-unit* tests — no HTTP layer, no database, no file I/O.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

# ── helpers ────────────────────────────────────────────────────────────────

def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════
# 1. compute_game_state
# ═══════════════════════════════════════════════════════════════════════════

class TestComputeGameState:
    """Gate: game-state fields are derived correctly from slate data."""

    def _df(self, rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    # ── started_flag ───────────────────────────────────────────────────────

    def test_started_flag_true_when_now_after_start_time(self):
        from analysis.core.game_state import compute_game_state

        past = _now_utc() - timedelta(minutes=10)
        iso = past.strftime("%Y-%m-%dT%H:%M:%S")
        df = self._df([{"Name": "Player A", "GameInfo": iso}])

        gs = compute_game_state(df, now=_now_utc())
        assert gs["Player A"].started_flag is True

    def test_started_flag_false_when_now_before_start_time(self):
        from analysis.core.game_state import compute_game_state

        future = _now_utc() + timedelta(hours=2)
        iso = future.strftime("%Y-%m-%dT%H:%M:%S")
        df = self._df([{"Name": "Player B", "GameInfo": iso}])

        gs = compute_game_state(df, now=_now_utc())
        assert gs["Player B"].started_flag is False

    def test_started_flag_false_when_no_game_info(self):
        from analysis.core.game_state import compute_game_state

        df = self._df([{"Name": "Player C"}])
        gs = compute_game_state(df, now=_now_utc())
        assert gs["Player C"].started_flag is False

    # ── locked_flag ────────────────────────────────────────────────────────

    def test_locked_flag_true_within_lock_buffer(self):
        """Player should be locked when now >= start_time - buffer."""
        from analysis.core.game_state import compute_game_state, LOCK_BUFFER_MINUTES

        # Start time is 3 minutes away — within the 5-minute default buffer
        near_future = _now_utc() + timedelta(minutes=3)
        iso = near_future.strftime("%Y-%m-%dT%H:%M:%S")
        df = self._df([{"Name": "Player D", "GameInfo": iso}])

        gs = compute_game_state(df, now=_now_utc())
        assert gs["Player D"].locked_flag is True

    def test_locked_flag_false_outside_lock_buffer(self):
        """Player outside the lock window must not be marked locked."""
        from analysis.core.game_state import compute_game_state

        far_future = _now_utc() + timedelta(hours=3)
        iso = far_future.strftime("%Y-%m-%dT%H:%M:%S")
        df = self._df([{"Name": "Player E", "GameInfo": iso}])

        gs = compute_game_state(df, now=_now_utc())
        assert gs["Player E"].locked_flag is False

    # ── swap_eligible ──────────────────────────────────────────────────────

    def test_swap_eligible_false_when_game_started(self):
        from analysis.core.game_state import compute_game_state

        past = _now_utc() - timedelta(minutes=5)
        iso = past.strftime("%Y-%m-%dT%H:%M:%S")
        df = self._df([{"Name": "Player F", "GameInfo": iso}])

        gs = compute_game_state(df, now=_now_utc())
        assert gs["Player F"].swap_eligible is False

    def test_swap_eligible_true_when_game_not_started(self):
        from analysis.core.game_state import compute_game_state

        future = _now_utc() + timedelta(hours=1)
        iso = future.strftime("%Y-%m-%dT%H:%M:%S")
        df = self._df([{"Name": "Player G", "GameInfo": iso}])

        gs = compute_game_state(df, now=_now_utc())
        assert gs["Player G"].swap_eligible is True

    # ── DK GameInfo string ─────────────────────────────────────────────────

    def test_dk_gameinfo_string_parsed_correctly(self):
        """GameInfo in DK format 'AAA@BBB MM/DD/YYYY HH:MMam ET' is parsed."""
        from analysis.core.game_state import compute_game_state

        # Build a game that already started (yesterday at 7:30 PM ET)
        yesterday = _now_utc() - timedelta(days=1)
        game_str = f"AAA@BBB {yesterday.strftime('%m/%d/%Y')} 07:30PM ET"
        df = self._df([{"Name": "Player H", "GameInfo": game_str}])

        gs = compute_game_state(df, now=_now_utc())
        assert gs["Player H"].started_flag is True

    # ── multi-player slate ─────────────────────────────────────────────────

    def test_mixed_slate_started_and_not_started(self):
        """Multiple players: some started, some not."""
        from analysis.core.game_state import compute_game_state

        now = _now_utc()
        past_iso   = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        future_iso = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")

        df = pd.DataFrame([
            {"Name": "Started Player",     "GameInfo": past_iso},
            {"Name": "Not Started Player", "GameInfo": future_iso},
            {"Name": "Unknown Time Player"},
        ])

        gs = compute_game_state(df, now=now)

        assert gs["Started Player"].started_flag is True
        assert gs["Not Started Player"].started_flag is False
        assert gs["Unknown Time Player"].started_flag is False

    # ── custom lock buffer ─────────────────────────────────────────────────

    def test_custom_lock_buffer_respected(self):
        """lock_buffer_minutes override changes the lock window."""
        from analysis.core.game_state import compute_game_state

        # 20 minutes away — inside a 30-min buffer, outside the 5-min default
        near = _now_utc() + timedelta(minutes=20)
        iso = near.strftime("%Y-%m-%dT%H:%M:%S")
        df = pd.DataFrame([{"Name": "Player I", "GameInfo": iso}])

        gs_default = compute_game_state(df, now=_now_utc())
        gs_custom  = compute_game_state(df, now=_now_utc(), lock_buffer_minutes=30)

        assert gs_default["Player I"].locked_flag is False
        assert gs_custom["Player I"].locked_flag is True


# ═══════════════════════════════════════════════════════════════════════════
# 2. LateSwapEngine.find_replacements — started candidate exclusion
# ═══════════════════════════════════════════════════════════════════════════

class TestLateSwapStartedExclusion:
    """Gate: started candidates are excluded from the replacement pool."""

    def _pool(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"Name": "Available Guard",     "Pos": "PG",    "Salary": 5800, "Proj": 28.0, "Own": 12.0, "DFS_ID": "101"},
            {"Name": "Started Guard",       "Pos": "PG",    "Salary": 6200, "Proj": 35.0, "Own": 22.0, "DFS_ID": "102"},
            {"Name": "Started Small Fwd",   "Pos": "SF",    "Salary": 5500, "Proj": 30.0, "Own": 18.0, "DFS_ID": "103"},
            {"Name": "Available Center",    "Pos": "C",     "Salary": 8000, "Proj": 40.0, "Own": 15.0, "DFS_ID": "104"},
        ])

    def _make_game_state(self, started: list[str], not_started: list[str]):
        from analysis.core.game_state import PlayerGameState

        now = _now_utc()
        gs: dict = {}
        for name in started:
            gs[name] = PlayerGameState(
                player_name=name,
                player_id="",
                start_time=now - timedelta(hours=1),
                started_flag=True,
                locked_flag=True,
                swap_eligible=False,
            )
        for name in not_started:
            gs[name] = PlayerGameState(
                player_name=name,
                player_id="",
                start_time=now + timedelta(hours=2),
                started_flag=False,
                locked_flag=False,
                swap_eligible=True,
            )
        return gs

    def test_started_candidate_excluded_from_replacement_pool(self):
        """A started candidate must not appear in find_replacements output."""
        from analysis.core.late_swap import LateSwapEngine

        game_state = self._make_game_state(
            started=["Started Guard", "Started Small Fwd"],
            not_started=["Available Guard", "Available Center"],
        )

        recs = LateSwapEngine.find_replacements(
            scratched_name="Scratched PG",
            scratched_pos="PG",
            scratched_salary=6000,
            scratched_proj=25.0,
            scratched_own=15.0,
            lineup_names=["Scratched PG", "Some Center"],
            pool=self._pool(),
            salary_cap=50_000,
            current_lineup_salary=45_000,
            game_state=game_state,
        )

        names = [r.replacement_player for r in recs]
        assert "Started Guard" not in names, "Started candidate incorrectly included"

    def test_not_started_candidate_remains_in_pool(self):
        """A non-started candidate that fits must remain eligible."""
        from analysis.core.late_swap import LateSwapEngine

        game_state = self._make_game_state(
            started=["Started Guard"],
            not_started=["Available Guard"],
        )

        recs = LateSwapEngine.find_replacements(
            scratched_name="Scratched PG",
            scratched_pos="PG",
            scratched_salary=6000,
            scratched_proj=25.0,
            scratched_own=15.0,
            lineup_names=["Scratched PG"],
            pool=self._pool(),
            salary_cap=50_000,
            current_lineup_salary=44_000,
            game_state=game_state,
        )

        names = [r.replacement_player for r in recs]
        assert "Available Guard" in names, "Eligible non-started candidate was filtered out"

    def test_no_game_state_does_not_filter_started_players(self):
        """When game_state=None, the started-exclusion rule must not apply."""
        from analysis.core.late_swap import LateSwapEngine

        recs = LateSwapEngine.find_replacements(
            scratched_name="Scratched PG",
            scratched_pos="PG",
            scratched_salary=6000,
            scratched_proj=25.0,
            scratched_own=15.0,
            lineup_names=["Scratched PG"],
            pool=self._pool(),
            salary_cap=50_000,
            current_lineup_salary=44_000,
            game_state=None,
        )

        names = [r.replacement_player for r in recs]
        # Without game_state, both PG-eligible players should appear
        assert "Started Guard" in names
        assert "Available Guard" in names

    def test_all_candidates_started_returns_empty(self):
        """If every eligible candidate has started, the result is empty."""
        from analysis.core.late_swap import LateSwapEngine

        # Only PG-eligible players are "Started Guard" and "Available Guard"
        game_state = self._make_game_state(
            started=["Started Guard", "Available Guard"],
            not_started=[],
        )

        recs = LateSwapEngine.find_replacements(
            scratched_name="Scratched PG",
            scratched_pos="PG",
            scratched_salary=6000,
            scratched_proj=25.0,
            scratched_own=15.0,
            lineup_names=["Scratched PG"],
            pool=self._pool(),
            salary_cap=50_000,
            current_lineup_salary=44_000,
            game_state=game_state,
        )

        assert recs == []


# ═══════════════════════════════════════════════════════════════════════════
# 3. optimize_portfolio — auto-lock of started players
# ═══════════════════════════════════════════════════════════════════════════

class TestOptimizerAutoLock:
    """Gate: started players are auto-locked in every generated lineup."""

    # 10-player DK NBA pool — DK needs PG,SG,SF,PF,C,G,F,UTIL (8 slots).
    # With only 1 SF and 1 PF, the F flex slot is infeasible once both are
    # placed in their primary slots.  Two SF + two PF gives the LP enough
    # flexibility to fill every slot.
    _PLAYERS = [
        ("Player PG1", "PG", 6000, 38.0, "n1"),
        ("Player PG2", "PG", 6000, 32.0, "n2"),
        ("Player SG1", "SG", 6000, 42.0, "n3"),
        ("Player SG2", "SG", 6000, 28.0, "n4"),
        ("Player SF1", "SF", 6000, 44.0, "n5"),
        ("Player SF2", "SF", 6000, 36.0, "n6"),
        ("Player PF1", "PF", 6000, 41.0, "n7"),
        ("Player PF2", "PF", 6000, 34.0, "n8"),
        ("Player C1",  "C",  6000, 48.0, "n9"),
        ("Player C2",  "C",  6000, 33.0, "n10"),
    ]

    def _build_df(self) -> pd.DataFrame:
        rows = []
        for name, pos, sal, proj, did in self._PLAYERS:
            rows.append({
                "Name": name, "Pos": pos, "Salary": sal,
                "Proj": proj, "Own": 10.0, "DFS_ID": did,
                "Team": "AAA", "GameInfo": "AAA@BBB",
            })
        return pd.DataFrame(rows)

    def _make_game_state(self, started_names: list[str]) -> dict:
        from analysis.core.game_state import PlayerGameState
        now = _now_utc()
        gs = {}
        for name, *_ in self._PLAYERS:
            is_started = name in started_names
            gs[name] = PlayerGameState(
                player_name=name,
                player_id="",
                start_time=now - timedelta(hours=1) if is_started else now + timedelta(hours=2),
                started_flag=is_started,
                locked_flag=is_started,
                swap_eligible=not is_started,
            )
        return gs

    def test_started_player_appears_in_every_lineup(self):
        """A started player is auto-locked and present in all generated lineups."""
        from analysis.nba.optimizer import optimize_portfolio

        started = ["Player PG1"]
        game_state = self._make_game_state(started)
        df = self._build_df()

        # n_lineups=2 keeps within the pool's unique-lineup capacity
        result = optimize_portfolio(
            df,
            n_lineups=2,
            site="DK",
            game_state=game_state,
        )

        assert result is not None and len(result) > 0, "Optimizer returned no lineups"

        # optimize_portfolio returns a single concatenated DataFrame with LineupIndex
        for _, lineup_df in result.groupby("LineupIndex"):
            names_in_lineup = set(lineup_df["Name"].tolist())
            assert "Player PG1" in names_in_lineup, (
                "Auto-locked started player missing from lineup"
            )

    def test_multiple_started_players_all_appear_in_every_lineup(self):
        """Multiple started players all appear in every lineup."""
        from analysis.nba.optimizer import optimize_portfolio

        started = ["Player PG1", "Player C1"]
        game_state = self._make_game_state(started)
        df = self._build_df()

        result = optimize_portfolio(
            df,
            n_lineups=2,
            site="DK",
            game_state=game_state,
        )

        assert result is not None and len(result) > 0

        for _, lineup_df in result.groupby("LineupIndex"):
            names = set(lineup_df["Name"].tolist())
            assert "Player PG1" in names, "Auto-locked PG missing from lineup"
            assert "Player C1"  in names, "Auto-locked C missing from lineup"

    def test_no_game_state_does_not_lock_anyone(self):
        """Without game_state, no player is auto-locked (normal operation)."""
        from analysis.nba.optimizer import optimize_portfolio

        df = self._build_df()
        result = optimize_portfolio(df, n_lineups=2, site="DK", game_state=None)

        # Should still generate valid lineups
        assert result is not None and len(result) > 0
