"""
Phase 15 — Projection Backtester

Stores pre-game projection snapshots and reconciles them against actuals
(``player_game_logs``) after games are played.  Provides daily MAE / RMSE /
R² metrics and a rolling trend view so projection model quality can be
tracked over time and regressions caught quickly.

Schema additions (dfs_edge.duckdb):

    projection_snapshots       — one row per player / date / site
    projection_accuracy_log    — one row per date / site (post-game metrics)

Typical daily workflow
──────────────────────
1.  Slate uploaded, projections generated::

        bt = ProjectionBacktester()
        bt.snapshot(proj_df, slate_date=date.today(), site="DK")

2.  Nightly job after games complete::

        metrics = bt.score_date(date.today(), site="DK")
        # {"mae": 6.2, "rmse": 8.1, "bias": -0.4, "r_squared": 0.71, ...}

3.  Weekly accuracy review::

        trend_df = bt.get_trend(n_days=30, site="DK")
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from analysis.shared.db import get_conn
from analysis.core.projection_engine import _slugify

log = logging.getLogger(__name__)

_DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "dfs_edge.duckdb"


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _r_squared(actual: pd.Series, predicted: pd.Series) -> float:
    """Coefficient of determination R².  Returns 1.0 for a perfect fit."""
    ss_res = float(((actual - predicted) ** 2).sum())
    ss_tot = float(((actual - actual.mean()) ** 2).sum())
    if ss_tot == 0:
        return 1.0 if ss_res == 0 else 0.0
    return round(max(0.0, 1.0 - ss_res / ss_tot), 4)


# ---------------------------------------------------------------------------
# ProjectionBacktester
# ---------------------------------------------------------------------------

_DEFAULT_MASTER_DB = (
    Path(__file__).resolve().parent.parent.parent / "data" / "dfs_master.duckdb"
)


@dataclass
class ProjectionBacktester:
    """
    Persist projection snapshots and reconcile them against game-log actuals.

    Parameters
    ----------
    db_path:
        Path to the ``dfs_edge.duckdb`` file.  Defaults to
        ``data/dfs_edge.duckdb``.
    game_logs_db_path:
        Path to the database that contains ``player_game_logs``.  When
        ``None`` (default) the same ``db_path`` database is used — useful
        for testing.  Set to ``data/dfs_master.duckdb`` in production.
    """

    db_path: Path = field(default_factory=lambda: _DEFAULT_DB)
    game_logs_db_path: Path | None = field(default_factory=lambda: _DEFAULT_MASTER_DB)

    def _game_log_ref(self, con) -> str:
        """Return the SQL table reference for ``player_game_logs``.

        When ``game_logs_db_path`` differs from ``db_path``, attach it as a
        read-only alias so the query can reference the correct file.
        """
        if self.game_logs_db_path is None or self.game_logs_db_path == self.db_path:
            return "player_game_logs"
        alias = "_gl_db"
        path_str = str(self.game_logs_db_path).replace("\\", "/")
        con.execute(
            f"ATTACH IF NOT EXISTS '{path_str}' AS {alias} (READ_ONLY)"
        )
        return f"{alias}.player_game_logs"

    # ── snapshot ──────────────────────────────────────────────────────── #

    def snapshot(
        self,
        proj_df: pd.DataFrame,
        slate_date: date,
        site: str,
    ) -> int:
        """
        Persist a pre-game projection snapshot for *slate_date* / *site*.

        Accepts any DataFrame containing at minimum:

        * ``Name`` (or ``player_name``) — player display name
        * ``Proj`` (or ``projection`` / ``Base_Proj``) — projected FPTS

        Optional columns stored when present:
        ``StdDev``, ``Floor``, ``Ceiling``, ``Salary``.

        Upserts on ``(slate_date, site, player_slug)`` — calling twice is
        idempotent.

        Returns the number of rows written.
        """
        if proj_df is None or proj_df.empty:
            return 0

        df = proj_df.copy()

        name_col = next(
            (c for c in ["Name", "player_name", "name"] if c in df.columns), None
        )
        proj_col = next(
            (c for c in ["Proj", "projection", "Base_Proj"] if c in df.columns), None
        )
        if name_col is None:
            raise ValueError("proj_df must contain a 'Name' column")
        if proj_col is None:
            raise ValueError("proj_df must contain a 'Proj' column")

        df["_name"]  = df[name_col].astype(str)
        df["_slug"]  = df["_name"].apply(_slugify)
        df["_proj"]  = pd.to_numeric(df[proj_col], errors="coerce").fillna(0.0)
        df["_std"]   = pd.to_numeric(df["StdDev"],  errors="coerce").fillna(0.0) \
                       if "StdDev"  in df.columns else 0.0
        df["_floor"] = pd.to_numeric(df["Floor"],   errors="coerce").fillna(0.0) \
                       if "Floor"   in df.columns else 0.0
        df["_ceil"]  = pd.to_numeric(df["Ceiling"], errors="coerce").fillna(0.0) \
                       if "Ceiling" in df.columns else 0.0
        df["_sal"]   = pd.to_numeric(df["Salary"],  errors="coerce").fillna(0).astype(int) \
                       if "Salary"  in df.columns else 0

        # Capture position if the player pool CSV provides it
        pos_col = next(
            (c for c in ["Position", "position", "Pos", "pos"] if c in df.columns),
            None,
        )
        if pos_col:
            df["_pos"] = df[pos_col].astype(str).str.strip()
        else:
            df["_pos"] = None

        con = get_conn(self.db_path)
        count = 0
        for _, row in df.iterrows():
            con.execute(
                """
                INSERT INTO projection_snapshots
                    (slate_date, site, player_slug, player_name,
                     proj, std_dev, floor_val, ceiling_val, salary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (slate_date, site, player_slug) DO UPDATE SET
                    proj        = excluded.proj,
                    std_dev     = excluded.std_dev,
                    floor_val   = excluded.floor_val,
                    ceiling_val = excluded.ceiling_val,
                    salary      = excluded.salary,
                    snapped_at  = now()
                """,
                [
                    slate_date, site.upper(),
                    row["_slug"], row["_name"],
                    float(row["_proj"]),
                    float(row["_std"]),
                    float(row["_floor"]),
                    float(row["_ceil"]),
                    int(row["_sal"]),
                ],
            )

            # Upsert position into player_positions so DvP can use it
            if row["_pos"] is not None and str(row["_pos"]) not in ("None", "nan", ""):
                try:
                    con.execute(
                        """
                        INSERT INTO player_positions (player_slug, player_name, position, site)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT (player_slug, site) DO UPDATE SET
                            player_name = excluded.player_name,
                            position    = excluded.position,
                            updated_at  = now()
                        """,
                        [row["_slug"], row["_name"], str(row["_pos"]), site.upper()],
                    )
                except Exception as exc:  # noqa: BLE001
                    log.debug("Could not upsert player_positions for %s: %s", row["_name"], exc)

            count += 1

        log.info(
            "Backtest snapshot: %d players  date=%s  site=%s",
            count, slate_date, site.upper(),
        )
        return count

    # ── score_date ────────────────────────────────────────────────────── #

    def score_date(
        self,
        slate_date: date,
        site: str,
        min_players: int = 5,
    ) -> dict[str, Any] | None:
        """
        Reconcile projection snapshots against actual game-log scores for
        *slate_date* / *site*.

        Joins ``projection_snapshots`` to ``player_game_logs`` on player name
        and date, then computes:

        * ``mae``           — mean absolute error
        * ``rmse``          — root mean squared error
        * ``bias``          — signed mean error (positive = over-projected)
        * ``r_squared``     — coefficient of determination
        * ``pct_within_5``  — % of players within 5 FPTS
        * ``pct_within_10`` — % of players within 10 FPTS

        Persists results to ``projection_accuracy_log`` (upsert).

        Returns the metrics dict, or ``None`` if fewer than *min_players*
        matched rows are available (games may not have been played yet).
        """
        pts_col = "dk_pts" if site.upper() == "DK" else "fd_pts"

        try:
            con = get_conn(self.db_path)
            gl  = self._game_log_ref(con)
            rows = con.execute(
                f"""
                SELECT
                    s.player_slug,
                    s.player_name,
                    s.proj       AS projected,
                    g.{pts_col}  AS actual
                FROM projection_snapshots AS s
                JOIN {gl} AS g
                  ON g.player_name = s.player_name
                 AND g.game_date   = s.slate_date
                 AND g.minutes     > 0
                WHERE s.slate_date = ?
                  AND s.site       = ?
                """,
                [slate_date, site.upper()],
            ).fetchall()
        except Exception as exc:  # noqa: BLE001
            log.warning("score_date query failed: %s", exc)
            return None

        if len(rows) < min_players:
            log.info(
                "score_date: %d matched rows for %s/%s — need at least %d, skipping",
                len(rows), slate_date, site, min_players,
            )
            return None

        df = pd.DataFrame(rows, columns=["slug", "name", "projected", "actual"])
        df = df.dropna(subset=["projected", "actual"])
        if len(df) < min_players:
            return None

        errors = df["projected"] - df["actual"]
        mae     = float(errors.abs().mean())
        rmse    = float(math.sqrt((errors ** 2).mean()))
        bias    = float(errors.mean())
        r2      = _r_squared(df["actual"], df["projected"])
        within5  = float((errors.abs() <= 5.0).mean() * 100)
        within10 = float((errors.abs() <= 10.0).mean() * 100)

        metrics: dict[str, Any] = {
            "run_date":       slate_date,
            "site":           site.upper(),
            "n_players":      len(df),
            "mae":            round(mae,    3),
            "rmse":           round(rmse,   3),
            "bias":           round(bias,   3),
            "r_squared":      round(r2,     4),
            "pct_within_5":   round(within5,  1),
            "pct_within_10":  round(within10, 1),
        }

        try:
            con.execute(
                """
                INSERT INTO projection_accuracy_log
                    (run_date, site, n_players, mae, rmse, bias,
                     r_squared, pct_within_5, pct_within_10)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (run_date, site) DO UPDATE SET
                    n_players     = excluded.n_players,
                    mae           = excluded.mae,
                    rmse          = excluded.rmse,
                    bias          = excluded.bias,
                    r_squared     = excluded.r_squared,
                    pct_within_5  = excluded.pct_within_5,
                    pct_within_10 = excluded.pct_within_10,
                    computed_at   = now()
                """,
                [
                    slate_date, site.upper(),
                    metrics["n_players"],
                    metrics["mae"],   metrics["rmse"],  metrics["bias"],
                    metrics["r_squared"],
                    metrics["pct_within_5"], metrics["pct_within_10"],
                ],
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("score_date: could not persist accuracy log: %s", exc)

        log.info(
            "Backtest %s %s — MAE=%.2f  RMSE=%.2f  bias=%.2f  R²=%.3f  n=%d",
            slate_date, site.upper(), mae, rmse, bias, r2, len(df),
        )
        return metrics

    # ── get_trend ─────────────────────────────────────────────────────── #

    def get_trend(
        self,
        n_days: int = 30,
        site: str | None = None,
    ) -> pd.DataFrame:
        """
        Return the last *n_days* rows from ``projection_accuracy_log``,
        sorted by date ascending.

        Parameters
        ----------
        n_days:
            How many calendar days of history to return.
        site:
            If given, filters to that site (``"DK"`` or ``"FD"``).

        Returns an empty DataFrame when no accuracy data exists yet.
        """
        try:
            con = get_conn(self.db_path)
            if site:
                rows = con.execute(
                    """
                    SELECT run_date, site, n_players, mae, rmse, bias,
                           r_squared, pct_within_5, pct_within_10, computed_at
                    FROM projection_accuracy_log
                    WHERE site = ?
                    ORDER BY run_date DESC
                    LIMIT ?
                    """,
                    [site.upper(), n_days],
                ).fetchall()
            else:
                rows = con.execute(
                    """
                    SELECT run_date, site, n_players, mae, rmse, bias,
                           r_squared, pct_within_5, pct_within_10, computed_at
                    FROM projection_accuracy_log
                    ORDER BY run_date DESC
                    LIMIT ?
                    """,
                    [n_days],
                ).fetchall()
        except Exception as exc:  # noqa: BLE001
            log.warning("get_trend query failed: %s", exc)
            return pd.DataFrame()

        if not rows:
            return pd.DataFrame()

        cols = [
            "run_date", "site", "n_players", "mae", "rmse", "bias",
            "r_squared", "pct_within_5", "pct_within_10", "computed_at",
        ]
        df = pd.DataFrame(rows, columns=cols)
        return df.sort_values("run_date").reset_index(drop=True)

    # ── get_biggest_misses ────────────────────────────────────────────── #

    def get_biggest_misses(
        self,
        slate_date: date,
        site: str,
        top_n: int = 10,
    ) -> pd.DataFrame:
        """
        Return the *top_n* worst projection misses for *slate_date* / *site*,
        sorted by absolute error descending.

        Columns: ``player_name``, ``projected``, ``actual``,
        ``error`` (projected − actual), ``abs_error``.

        Returns an empty DataFrame if no matching data exists.
        """
        pts_col = "dk_pts" if site.upper() == "DK" else "fd_pts"

        try:
            con = get_conn(self.db_path)
            gl  = self._game_log_ref(con)
            rows = con.execute(
                f"""
                SELECT
                    s.player_name,
                    s.proj                    AS projected,
                    g.{pts_col}               AS actual,
                    s.proj - g.{pts_col}      AS error,
                    ABS(s.proj - g.{pts_col}) AS abs_error
                FROM projection_snapshots AS s
                JOIN {gl} AS g
                  ON g.player_name = s.player_name
                 AND g.game_date   = s.slate_date
                 AND g.minutes     > 0
                WHERE s.slate_date = ?
                  AND s.site       = ?
                ORDER BY abs_error DESC
                LIMIT ?
                """,
                [slate_date, site.upper(), top_n],
            ).fetchall()
        except Exception as exc:  # noqa: BLE001
            log.warning("get_biggest_misses failed: %s", exc)
            return pd.DataFrame()

        if not rows:
            return pd.DataFrame()

        return pd.DataFrame(
            rows,
            columns=["player_name", "projected", "actual", "error", "abs_error"],
        )
