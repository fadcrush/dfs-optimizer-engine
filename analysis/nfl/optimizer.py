"""NFL DFS Lineup Optimizer — DraftKings Classic, DK Showdown, and FanDuel Classic.

Slot rules
----------
DraftKings Classic (salary cap $50,000):
    QB(1) | RB(2) | WR(3) | TE(1) | FLEX(1 — RB/WR/TE) | DST(1)
    Total players: 9

DraftKings Showdown (Single-Game, salary cap $50,000):
    CPT(1) — 1.5× salary, 1.5× pts
    FLEX(5 — any position)
    Total players: 6

FanDuel Classic (salary cap $60,000):
    QB(1) | RB(2) | WR(3) | TE(1) | FLEX(1 — RB/WR/TE) | DST(1)
    Total players: 9

Usage::
    from analysis.nfl.optimizer import NFLOptimizer
    optimizer = NFLOptimizer(site="DK", contest_type="Classic")
    lineups = optimizer.generate(projections_df, n_lineups=20)
"""

from __future__ import annotations

import itertools
import logging
import random
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Slot definitions
# ---------------------------------------------------------------------------

ContestType = Literal["Classic", "Showdown"]
SiteType = Literal["DK", "FD"]

_CLASSIC_SLOTS_DK = {
    "QB": 1,
    "RB": 2,
    "WR": 3,
    "TE": 1,
    "FLEX": 1,   # RB/WR/TE
    "DST": 1,
}

_CLASSIC_SLOTS_FD = {
    "QB": 1,
    "RB": 2,
    "WR": 3,
    "TE": 1,
    "FLEX": 1,   # RB/WR/TE
    "DST": 1,    # FD calls it D/ST
}

_CLASSIC_CAPS: dict[SiteType, int] = {"DK": 50_000, "FD": 60_000}

_FLEX_POSITIONS = {"RB", "WR", "TE"}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class LineupSlot:
    """One player placed in a specific slot."""
    player_id: str
    name: str
    position: str
    slot: str
    salary: float
    projection: float
    team: str = ""


@dataclass
class NFLLineup:
    """A complete DFS lineup."""
    slots: list[LineupSlot] = field(default_factory=list)

    @property
    def total_salary(self) -> float:
        return sum(s.salary for s in self.slots)

    @property
    def total_projection(self) -> float:
        return sum(s.projection for s in self.slots)

    def to_dict(self) -> dict:
        base = {
            "total_salary": self.total_salary,
            "total_projection": round(self.total_projection, 2),
        }
        for slot_ in self.slots:
            base[slot_.slot] = slot_.name
        return base

    def to_df_row(self) -> pd.Series:
        return pd.Series(self.to_dict())


# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------

class NFLOptimizer:
    """Greedy + stochastic lineup generator for NFL DFS slates.

    Not a true ILP optimizer — uses a ranked greedy approach with position
    budgets and stochastic perturbation for lineup diversity. For small
    slates (< 30 players per position group) this is fast enough in pure
    Python. For production scale, wire in PuLP or OR-Tools.
    """

    def __init__(
        self,
        site: SiteType = "DK",
        contest_type: ContestType = "Classic",
        salary_cap: int | None = None,
    ) -> None:
        self.site = site.upper()  # type: ignore[assignment]
        self.contest_type = contest_type
        self.salary_cap: int = salary_cap or _CLASSIC_CAPS.get(self.site, 50_000)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        projections_df: pd.DataFrame,
        n_lineups: int = 20,
        seed: int | None = None,
        max_from_team: int = 8,
        min_teams: int = 2,
        locks: list[str] | None = None,
        fades: list[str] | None = None,
    ) -> list[NFLLineup]:
        """Generate *n_lineups* valid lineups from *projections_df*.

        Args:
            projections_df: Must have columns ``Name``, ``Position``,
                ``Salary``, ``Projection``, and optionally ``Team``/``DFS_ID``.
            n_lineups: How many lineups to generate.
            seed: Random seed for reproducibility.
            max_from_team: Maximum players from one NFL team per lineup.
            min_teams: Minimum distinct teams per lineup (prevents full-game stacks).
            locks: Player names to force into every lineup.
            fades: Player names to exclude from the pool entirely.

        Returns:
            List of :class:`NFLLineup` objects sorted by total_projection desc.
        """
        if seed is not None:
            random.seed(seed)

        df = self._prepare(projections_df)
        if df.empty:
            log.warning("NFLOptimizer: empty player pool after preparation")
            return []

        # Apply fades — remove excluded players from pool
        if fades:
            fades_lower = {f.lower().strip() for f in fades}
            before = len(df)
            df = df[~df["Name"].str.lower().str.strip().isin(fades_lower)].copy()
            log.info("NFL: Faded %d players from pool", before - len(df))

        # Apply locks — these will be forced into every lineup via _build_*_lineup
        locked_ids: set[str] = set()
        if locks:
            locks_lower = {l.lower().strip() for l in locks}
            locked_ids = set(df.loc[df["Name"].str.lower().str.strip().isin(locks_lower), "DFS_ID"].tolist())
            log.info("NFL: Locking %d players into every lineup", len(locked_ids))

        if self.contest_type == "Showdown":
            return self._generate_showdown(df, n_lineups, max_from_team, locked_ids=locked_ids)

        return self._generate_classic(df, n_lineups, max_from_team, min_teams, locked_ids=locked_ids)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalise column names and fill defaults."""
        col_map = {}
        for col in df.columns:
            upper = col.strip().upper()
            if upper in ("PLAYER NAME", "PLAYER", "NAME"):
                col_map[col] = "Name"
            elif upper == "POSITION":
                col_map[col] = "Position"
            elif upper == "SALARY":
                col_map[col] = "Salary"
            elif upper in ("PROJECTION", "FPTS", "PROJECTED_FPTS"):
                col_map[col] = "Projection"
            elif upper in ("TEAM", "TEAMABBREV", "TEAM ABBREV"):
                col_map[col] = "Team"
            elif upper in ("ID", "DFS_ID", "PLAYER ID"):
                col_map[col] = "DFS_ID"

        df = df.rename(columns=col_map).copy()

        for required in ("Name", "Position", "Salary", "Projection"):
            if required not in df.columns:
                log.error("NFLOptimizer: required column '%s' missing", required)
                return pd.DataFrame()

        if "Team" not in df.columns:
            df["Team"] = "UNK"
        if "DFS_ID" not in df.columns:
            df["DFS_ID"] = df["Name"].str.replace(" ", "_")

        df["Salary"] = pd.to_numeric(df["Salary"], errors="coerce").fillna(0)
        df["Projection"] = pd.to_numeric(df["Projection"], errors="coerce").fillna(0)
        df["Position"] = df["Position"].str.upper().str.strip()

        # Normalise D/ST → DST
        df.loc[df["Position"] == "D/ST", "Position"] = "DST"
        df.loc[df["Position"] == "DEF", "Position"] = "DST"

        return df[df["Salary"] > 0].reset_index(drop=True)

    # ------------------------------------------------------------------
    # Classic lineup builder
    # ------------------------------------------------------------------

    def _generate_classic(
        self,
        df: pd.DataFrame,
        n_lineups: int,
        max_from_team: int,
        min_teams: int,
        locked_ids: set[str] | None = None,
    ) -> list[NFLLineup]:
        slots_cfg = _CLASSIC_SLOTS_DK if self.site == "DK" else _CLASSIC_SLOTS_FD
        lineups: list[NFLLineup] = []
        seen: set[frozenset] = set()
        attempts = 0
        max_attempts = n_lineups * 50
        _locked = locked_ids or set()

        # Sort players by projection desc
        by_pos: dict[str, pd.DataFrame] = {}
        for pos in ("QB", "RB", "WR", "TE", "DST"):
            by_pos[pos] = df[df["Position"] == pos].sort_values(
                "Projection", ascending=False
            ).reset_index(drop=True)

        while len(lineups) < n_lineups and attempts < max_attempts:
            attempts += 1
            lineup = self._build_classic_lineup(by_pos, slots_cfg, max_from_team, min_teams, locked_ids=_locked)
            if lineup is None:
                continue
            key = frozenset(s.player_id for s in lineup.slots)
            if key in seen:
                continue
            seen.add(key)
            lineups.append(lineup)

        lineups.sort(key=lambda ln: ln.total_projection, reverse=True)
        log.info(
            "NFLOptimizer: generated %d/%d lineups (%d attempts)", len(lineups), n_lineups, attempts
        )
        return lineups

    def _build_classic_lineup(
        self,
        by_pos: dict[str, pd.DataFrame],
        slots_cfg: dict[str, int],
        max_from_team: int,
        min_teams: int,
        locked_ids: set[str] | None = None,
    ) -> NFLLineup | None:
        chosen: list[LineupSlot] = []
        used_ids: set[str] = set()
        team_counts: dict[str, int] = {}
        _locked = locked_ids or set()

        # Pre-fill locked players into chosen (respecting salary/team rules)
        salary_used = 0.0
        for pos, pool in by_pos.items():
            locked_in_pos = pool[pool["DFS_ID"].isin(_locked)]
            for _, row in locked_in_pos.iterrows():
                if row["DFS_ID"] in used_ids:
                    continue
                chosen.append(LineupSlot(
                    position=pos,
                    slot=pos,
                    name=str(row["Name"]),
                    player_id=str(row["DFS_ID"]),
                    salary=float(row["Salary"]),
                    projection=float(row["Projection"]),
                    team=str(row.get("Team", "UNK")),
                ))
                used_ids.add(str(row["DFS_ID"]))
                team_counts[str(row.get("Team", "UNK"))] = team_counts.get(str(row.get("Team", "UNK")), 0) + 1
                salary_used += float(row["Salary"])

        # Pre-compute minimum costs per remaining slot type for budget headroom.
        # This guarantees we never pick a player so expensive we can't fill the rest.
        _min_cost: dict[str, float] = {}
        for pos, pool in by_pos.items():
            _min_cost[pos] = float(pool["Salary"].min()) if not pool.empty else 0.0
        _min_cost["FLEX"] = min(
            _min_cost.get("RB", 0),
            _min_cost.get("WR", 0),
            _min_cost.get("TE", 0),
        )

        def _remaining_min_cost(current_slots_remaining: list[tuple[str, int]]) -> float:
            total = 0.0
            for slot_name_r, cnt in current_slots_remaining:
                pos_key = slot_name_r if slot_name_r in _min_cost else slot_name_r
                total += _min_cost.get(pos_key, 0.0) * cnt
            return total

        def pick(
            pos_pool: pd.DataFrame,
            slot_name: str,
            slots_still_remaining: list[tuple[str, int]],
            jitter: float = 0.15,
        ) -> bool:
            """Pick one player from pos_pool not already used; returns success."""
            eligible = pos_pool[~pos_pool["DFS_ID"].isin(used_ids)]
            if eligible.empty:
                return False

            used_salary = sum(s.salary for s in chosen)
            remaining_budget = self.salary_cap - used_salary
            min_cost_rest = _remaining_min_cost(slots_still_remaining)
            max_affordable = remaining_budget - min_cost_rest

            eligible = eligible[eligible["Salary"] <= max_affordable]
            if eligible.empty:
                return False

            # Stochastic: pick from top N by projection with small random perturbation
            top_n = max(1, min(6, len(eligible)))
            top = eligible.sort_values("Projection", ascending=False).head(top_n).copy()
            top["_jitter"] = [
                row["Projection"] * (1 + random.uniform(-jitter, jitter))
                for _, row in top.iterrows()
            ]
            top = top.sort_values("_jitter", ascending=False)

            for _, player in top.iterrows():
                pid = player["DFS_ID"]
                team = player["Team"]
                if team_counts.get(team, 0) >= max_from_team:
                    continue
                chosen.append(
                    LineupSlot(
                        player_id=pid,
                        name=player["Name"],
                        position=player["Position"],
                        slot=slot_name,
                        salary=float(player["Salary"]),
                        projection=float(player["Projection"]),
                        team=team,
                    )
                )
                used_ids.add(pid)
                team_counts[team] = team_counts.get(team, 0) + 1
                return True
            return False

        # Build an ordered fill plan: (slot_name, pool_key) pairs
        fill_plan: list[tuple[str, str]] = []
        for pos, count in slots_cfg.items():
            if pos == "FLEX":
                continue
            for _ in range(count):
                fill_plan.append((pos, pos))
        fill_plan.append(("FLEX", "FLEX"))

        # Remaining-slots list is all fill_plan entries not yet processed.
        for idx, (slot_name, pool_key) in enumerate(fill_plan):
            # slots still to fill AFTER this pick
            after = fill_plan[idx + 1 :]
            after_remaining = [(s, 1) for s, _ in after]

            if pool_key == "FLEX":
                flex_pool = pd.concat(
                    [by_pos.get(p, pd.DataFrame()) for p in ("RB", "WR", "TE")],
                    ignore_index=True,
                ).sort_values("Projection", ascending=False)
                if not pick(flex_pool, "FLEX", after_remaining):
                    return None
            else:
                pool = by_pos.get(pool_key, pd.DataFrame())
                if not pick(pool, slot_name, after_remaining):
                    return None

        lineup = NFLLineup(slots=chosen)

        # Salary cap check
        if lineup.total_salary > self.salary_cap:
            return None

        # Minimum-teams check
        teams = {s.team for s in lineup.slots if s.team not in ("", "UNK")}
        if len(teams) < min_teams:
            return None

        return lineup

    # ------------------------------------------------------------------
    # Showdown lineup builder
    # ------------------------------------------------------------------

    def _generate_showdown(
        self,
        df: pd.DataFrame,
        n_lineups: int,
        max_from_team: int,
        locked_ids: set[str] | None = None,
    ) -> list[NFLLineup]:
        """Generate DK Showdown lineups: CPT(1) + FLEX(5)."""
        _locked = locked_ids or set()
        lineups: list[NFLLineup] = []
        seen: set[frozenset] = set()
        attempts = 0
        max_attempts = n_lineups * 50

        sorted_df = df.sort_values("Projection", ascending=False).reset_index(drop=True)

        while len(lineups) < n_lineups and attempts < max_attempts:
            attempts += 1
            lineup = self._build_showdown_lineup(sorted_df, max_from_team)
            if lineup is None:
                continue
            key = frozenset(s.player_id for s in lineup.slots)
            if key in seen:
                continue
            seen.add(key)
            lineups.append(lineup)

        lineups.sort(key=lambda ln: ln.total_projection, reverse=True)
        return lineups

    def _build_showdown_lineup(
        self,
        sorted_df: pd.DataFrame,
        max_from_team: int,
    ) -> NFLLineup | None:
        chosen: list[LineupSlot] = []
        used_ids: set[str] = set()
        team_counts: dict[str, int] = {}

        # CPT: top-N with jitter
        cpt_pool = sorted_df.head(min(10, len(sorted_df)))
        cpt_idx = random.randint(0, len(cpt_pool) - 1)
        cpt_row = cpt_pool.iloc[cpt_idx]
        chosen.append(
            LineupSlot(
                player_id=cpt_row["DFS_ID"] + "_CPT",
                name=cpt_row["Name"] + " (CPT)",
                position=cpt_row["Position"],
                slot="CPT",
                salary=float(cpt_row["Salary"]) * 1.5,
                projection=float(cpt_row["Projection"]) * 1.5,
                team=cpt_row["Team"],
            )
        )
        used_ids.add(cpt_row["DFS_ID"])
        team_counts[cpt_row["Team"]] = team_counts.get(cpt_row["Team"], 0) + 1

        # FLEX: 5 more players
        flex_pool = sorted_df[~sorted_df["DFS_ID"].isin(used_ids)].head(20)
        candidates = flex_pool.sample(frac=1)  # shuffle for diversity

        for _, player in candidates.iterrows():
            if len(chosen) == 6:
                break
            team = player["Team"]
            if team_counts.get(team, 0) >= max_from_team:
                continue
            chosen.append(
                LineupSlot(
                    player_id=player["DFS_ID"],
                    name=player["Name"],
                    position=player["Position"],
                    slot=f"FLEX{len(chosen)}",
                    salary=float(player["Salary"]),
                    projection=float(player["Projection"]),
                    team=team,
                )
            )
            used_ids.add(player["DFS_ID"])
            team_counts[team] = team_counts.get(team, 0) + 1

        if len(chosen) < 6:
            return None

        lineup = NFLLineup(slots=chosen)
        if lineup.total_salary > self.salary_cap:
            return None
        return lineup

    # ------------------------------------------------------------------
    # Export helpers
    # ------------------------------------------------------------------

    @staticmethod
    def lineups_to_df(lineups: list[NFLLineup]) -> pd.DataFrame:
        """Convert a list of lineups to a summary DataFrame."""
        rows = [ln.to_dict() for ln in lineups]
        return pd.DataFrame(rows)
