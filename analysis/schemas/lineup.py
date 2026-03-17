"""
Lineup — canonical optimizer output data contract.

One ``Lineup`` represents a single valid DK/FD lineup produced by the
constraint solver.  A ``LineupPortfolio`` wraps multiple lineups for the
simulation and exposure optimization layers.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, computed_field, model_validator


class LineupPlayer(BaseModel):
    """
    A single player slot within an optimized lineup.

    Carries the minimum subset of projection data needed to reconstruct,
    evaluate, and export the lineup.
    """

    player_id: str
    name: str
    position: str = Field(description="Assigned slot (e.g. 'UTIL', 'G', 'PG').")
    team: str
    opponent: str = ""
    salary: int
    proj: float = Field(description="Mean projection at lineup-build time.")
    floor: float = 0.0
    ceiling: float = 0.0
    ownership: float = Field(default=0.0, description="Projected ownership %.")
    dfs_id: Optional[str] = None

    def __repr__(self) -> str:
        return f"LineupPlayer({self.name!r}, {self.position!r}, ${self.salary:,})"


class Lineup(BaseModel):
    """
    A single valid DFS lineup produced by the optimizer.

    Includes full player data plus contest simulation metrics when available.
    """

    # --- Identity ---
    lineup_id: int = Field(default=0, description="1-based index within the portfolio.")
    site: str
    sport: str = "NBA"

    # --- Roster ---
    players: list[LineupPlayer] = Field(default_factory=list)

    # --- Financials ---
    total_salary: int = Field(
        default=0,
        description="Sum of player salaries. Must be ≤ site salary cap.",
    )
    salary_remaining: int = Field(
        default=0,
        description="Cap space remaining (cap - total_salary).",
    )

    # --- Projection aggregates ---
    total_proj: float = Field(default=0.0)
    total_floor: float = Field(default=0.0)
    total_ceiling: float = Field(default=0.0)
    total_ownership: float = Field(
        default=0.0,
        description="Sum of individual player ownerships (not capped at 100).",
    )

    # --- Simulation / EV (populated after contest_sim) ---
    ev: Optional[float] = Field(default=None, description="Expected value ($).")
    roi: Optional[float] = Field(default=None, description="ROI (EV / entry_fee).")
    cash_rate: Optional[float] = Field(default=None, description="Probability of cashing.")
    top1_rate: Optional[float] = Field(default=None, description="Probability of finishing 1st.")

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _derive_aggregates(self) -> "Lineup":
        if not self.players:
            return self
        self.total_salary = sum(p.salary for p in self.players)
        cap = 50_000 if self.site.upper() == "DK" else 60_000
        self.salary_remaining = cap - self.total_salary
        self.total_proj = round(sum(p.proj for p in self.players), 3)
        self.total_floor = round(sum(p.floor for p in self.players), 3)
        self.total_ceiling = round(sum(p.ceiling for p in self.players), 3)
        self.total_ownership = round(sum(p.ownership for p in self.players), 2)
        return self

    @computed_field  # type: ignore[misc]
    @property
    def teams(self) -> list[str]:
        """Unique teams represented in this lineup."""
        return sorted({p.team for p in self.players})

    @computed_field  # type: ignore[misc]
    @property
    def team_stack(self) -> Optional[str]:
        """
        Team with the most players (the 'stack').
        Returns None when all teams have equal representation.
        """
        from collections import Counter
        if not self.players:
            return None
        counts = Counter(p.team for p in self.players)
        most_common_team, most_common_count = counts.most_common(1)[0]
        return most_common_team if most_common_count >= 2 else None

    def player_names(self) -> list[str]:
        return [p.name for p in self.players]

    def to_export_row(self) -> dict:
        """
        Produce a flat dict suitable for CSV export / FanDuel import.
        Keys match site upload format.
        """
        row: dict = {}
        for i, player in enumerate(self.players, start=1):
            row[f"player_{i}"] = player.dfs_id or player.name
        row["total_salary"] = self.total_salary
        row["total_proj"] = round(self.total_proj, 2)
        row["ev"] = round(self.ev, 2) if self.ev is not None else ""
        row["roi"] = round(self.roi, 4) if self.roi is not None else ""
        return row

    def __repr__(self) -> str:
        return (
            f"Lineup(id={self.lineup_id}, proj={self.total_proj:.1f}, "
            f"salary=${self.total_salary:,}, players={len(self.players)})"
        )


class LineupPortfolio(BaseModel):
    """
    A collection of lineups produced for one contest entry strategy.

    Input to the exposure optimizer and contest simulation layer.
    """

    site: str
    sport: str = "NBA"
    lineups: list[Lineup] = Field(default_factory=list)
    n_lineups: int = Field(default=0)

    # --- Populated by exposure optimizer ---
    exposure_report: Optional[dict] = Field(
        default=None,
        description="Per-player exposure stats from ExposureOptimizer.",
    )

    @model_validator(mode="after")
    def _set_n(self) -> "LineupPortfolio":
        self.n_lineups = len(self.lineups)
        return self

    @property
    def avg_proj(self) -> float:
        if not self.lineups:
            return 0.0
        return round(sum(lu.total_proj for lu in self.lineups) / len(self.lineups), 2)

    @property
    def avg_salary(self) -> float:
        if not self.lineups:
            return 0.0
        return round(sum(lu.total_salary for lu in self.lineups) / len(self.lineups), 2)

    def top_ev_lineups(self, n: int = 5) -> list[Lineup]:
        """Return lineups sorted by EV descending (highest ROI first)."""
        ranked = [lu for lu in self.lineups if lu.ev is not None]
        return sorted(ranked, key=lambda lu: lu.ev, reverse=True)[:n]  # type: ignore[arg-type]

    def __len__(self) -> int:
        return len(self.lineups)

    def __repr__(self) -> str:
        return (
            f"LineupPortfolio(site={self.site!r}, lineups={self.n_lineups}, "
            f"avg_proj={self.avg_proj:.1f})"
        )
