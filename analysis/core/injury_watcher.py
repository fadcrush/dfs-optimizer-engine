"""
Phase 23: Real-Time Injury Status Watcher
==========================================

``InjuryWatcher`` completes the late-swap monitoring pipeline introduced in
Phase 22: it polls fresh injury data on demand (or on a schedule), diffs the
current status snapshot against the previous one, and returns a ``PollResult``
containing any ``SwapSignal`` events that require lineup attention.

Design principles
-----------------
* **Pure analysis layer** — no HTTP, no FastAPI, no threading.  The caller
  (a Celery beat task, background thread, or CLI script) decides how often to
  call ``poll()``.
* **Snapshot-diff model** — each ``poll()`` call compares the latest injury
  DataFrame against the stored previous snapshot via
  ``LateSwapEngine.detect_changes()``.  Only severity *increases* produce
  signals (see Phase 22 for the severity ordering).
* **Graceful degradation** — if the DB is unavailable, ``poll()`` returns an
  empty ``PollResult`` and does *not* replace the current snapshot, so the
  next successful poll can still detect changes.

Typical usage
-------------
::

    watcher = InjuryWatcher(source="sportsdata")

    # On each 60-second tick:
    result = watcher.poll()

    for signal in result.signals:
        if signal.is_actionable:
            print(f"{signal.player_name} is now {signal.new_status}")
            # → feed into LateSwapEngine.find_replacements()
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from analysis.core.late_swap import LateSwapEngine, SwapSignal
from analysis.schemas.player import InjuryStatus
from analysis.shared.injury_utils import load_injury_status


# ──────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PollResult:
    """Outcome of a single ``InjuryWatcher.poll()`` call."""

    signals: list[SwapSignal]
    """All status-change signals detected since the last successful poll."""

    snapshot: dict[str, str]
    """{player_name: normalized_status} for every player in the current report."""

    polled_at: datetime
    """UTC timestamp when the poll was executed."""

    player_count: int
    """Number of players in the current injury report snapshot."""

    @property
    def actionable_signals(self) -> list[SwapSignal]:
        """Subset of ``signals`` where ``is_actionable`` is ``True`` (OUT / GTD)."""
        return [s for s in self.signals if s.is_actionable]

    @property
    def has_changes(self) -> bool:
        """``True`` when at least one status change was detected."""
        return bool(self.signals)


# ──────────────────────────────────────────────────────────────────────────────
# InjuryWatcher
# ──────────────────────────────────────────────────────────────────────────────

class InjuryWatcher:
    """
    Stateful injury-status poller.

    Maintains an internal ``_prev_snapshot`` dict of the most recent
    ``{player_name: normalized_status}`` values so that consecutive
    ``poll()`` calls can detect changes.

    Parameters
    ----------
    source     : Label attached to every emitted ``SwapSignal`` (e.g. "sportsdata",
                  "csv", "manual").
    name_col   : Column name in the injury DataFrame that holds player names.
    status_col : Column name in the injury DataFrame that holds raw status strings.
    """

    def __init__(
        self,
        *,
        source: str = "watcher",
        name_col: str = "player_name",
        status_col: str = "status",
    ) -> None:
        self._prev_snapshot: dict[str, str] = {}
        self._source    = source
        self._name_col  = name_col
        self._status_col = status_col

    # ── Snapshot helpers ──────────────────────────────────────────────────────

    def snapshot_from_df(
        self,
        df: pd.DataFrame,
        *,
        name_col: str | None = None,
        status_col: str | None = None,
    ) -> dict[str, str]:
        """
        Convert a raw injury DataFrame into a ``{player_name: norm_status}`` dict.

        Player names that are empty or whitespace are skipped.
        Status strings are normalised through ``InjuryStatus.from_raw()`` so that
        "OUT", "O", and "out" all produce ``InjuryStatus.OUT.value``.

        Parameters
        ----------
        df         : DataFrame with player name and status columns.
        name_col   : Override the instance ``name_col`` for this call.
        status_col : Override the instance ``status_col`` for this call.
        """
        nc = name_col   or self._name_col
        sc = status_col or self._status_col

        snapshot: dict[str, str] = {}
        if df.empty:
            return snapshot

        # Resolve column names gracefully
        if nc not in df.columns:
            # Try common alternatives before giving up
            for alt in ("Name", "player", "PLAYER_NAME", "name"):
                if alt in df.columns:
                    nc = alt
                    break
            else:
                return snapshot

        sc_actual = sc if sc in df.columns else None

        for _, row in df.iterrows():
            name = str(row.get(nc, "")).strip()
            if not name:
                continue
            raw_status = str(row.get(sc_actual, "") if sc_actual else "").strip()
            snapshot[name] = InjuryStatus.from_raw(raw_status).value

        return snapshot

    # ── Core poll ─────────────────────────────────────────────────────────────

    def poll(
        self,
        injury_df: pd.DataFrame | None = None,
        *,
        force: bool = True,
        timestamp: datetime | None = None,
    ) -> PollResult:
        """
        Execute a single polling cycle.

        1. Load a fresh injury DataFrame (or use the provided ``injury_df``).
        2. Convert to a ``{name: status}`` snapshot.
        3. Diff against ``_prev_snapshot`` using ``LateSwapEngine.detect_changes()``.
        4. Update ``_prev_snapshot`` only when the load succeeded (non-empty DF).
        5. Return a ``PollResult``.

        Parameters
        ----------
        injury_df  : Inject a pre-built DataFrame (used in tests / batch pipelines).
                     When ``None``, calls ``load_injury_status(force=force)``.
        force      : Bypass the 60-second TTL cache in ``load_injury_status``.
        timestamp  : Override the signal timestamp (defaults to ``datetime.now(timezone.utc)``).
        """
        ts = timestamp or datetime.now(timezone.utc)

        if injury_df is None:
            injury_df = load_injury_status(force=force)

        # Empty DF → graceful degradation; don't reset the stored snapshot
        if injury_df.empty:
            return PollResult(
                signals=[],
                snapshot=dict(self._prev_snapshot),
                polled_at=ts,
                player_count=0,
            )

        curr = self.snapshot_from_df(injury_df)

        signals = LateSwapEngine.detect_changes(
            self._prev_snapshot,
            curr,
            source=self._source,
            timestamp=ts,
        )

        # Only advance the snapshot after a successful load
        self._prev_snapshot = curr

        return PollResult(
            signals=signals,
            snapshot=curr,
            polled_at=ts,
            player_count=len(curr),
        )

    # ── Utility ───────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """
        Clear the internal snapshot.

        The next ``poll()`` call will treat every player as new (no prior
        status known), so only genuine status increases vs. the new baseline
        will be emitted.
        """
        self._prev_snapshot = {}

    @property
    def current_snapshot(self) -> dict[str, str]:
        """Read-only copy of the most recent ``{player_name: status}`` snapshot."""
        return dict(self._prev_snapshot)
