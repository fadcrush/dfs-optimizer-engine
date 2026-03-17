"""
Projection — canonical projection output data contract.

One ``Projection`` record represents a single player's statistical
projection output from the engine, enriched with value, ownership, and
leverage data ready for the optimizer.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, computed_field, field_validator


class Projection(BaseModel):
    """
    Full projection record for one player on one slate.

    Produced by ``CanonicalNBAProjectionEngine.generate()`` and enriched by
    the orchestrator before being passed to the optimizer.
    """

    # --- Identity (mirrors Player) ---
    player_id: str = Field(description="Slug key (e.g. 'lebron_james').")
    name: str
    position: str
    team: str
    opponent: str = Field(default="", alias="Opp")
    site: str = Field(description="'DK' or 'FD'.")
    sport: str = Field(default="NBA")
    salary: int = Field(ge=0)
    dfs_id: Optional[str] = Field(default=None)

    # --- Projection outputs ---
    proj: float = Field(
        ge=0.0,
        description="Mean projected DFS points.",
        alias="Proj",
    )
    floor: float = Field(
        ge=0.0,
        description="10th-percentile projected DFS points.",
        alias="Floor",
    )
    ceiling: float = Field(
        ge=0.0,
        description="90th-percentile projected DFS points.",
        alias="Ceiling",
    )

    # --- Market / ownership ---
    ownership: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Projected ownership % (0–100).",
        alias="Own",
    )
    leverage_score: float = Field(
        default=0.0,
        description="Edge × (1 + ownership divergence). Higher = more GPP value.",
    )

    # --- Context ---
    gl_baseline: Optional[float] = Field(
        default=None,
        description="Game-log L10 baseline used as projection anchor (None if unavailable).",
    )
    gl_games: Optional[int] = Field(
        default=None,
        description="Number of game-log games used to compute gl_baseline.",
    )
    slate_date: Optional[date] = Field(default=None)
    projection_source: str = Field(
        default="canonical",
        description="Which engine produced this projection ('canonical', 'user_override', etc.).",
    )

    model_config = {"populate_by_name": True}

    @field_validator("site")
    @classmethod
    def site_upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("sport")
    @classmethod
    def sport_upper(cls, v: str) -> str:
        return v.upper()

    @computed_field  # type: ignore[misc]
    @property
    def value(self) -> float:
        """Points-per-$1000 of salary — classic DFS value metric."""
        return round(self.proj / (self.salary / 1_000), 3) if self.salary > 0 else 0.0

    @computed_field  # type: ignore[misc]
    @property
    def value_tier(self) -> str:
        """Human-readable value tier based on pts/$1000."""
        v = self.value
        if v >= 5.0:
            return "elite"
        if v >= 4.0:
            return "strong"
        if v >= 3.0:
            return "solid"
        if v >= 2.0:
            return "weak"
        return "punt"

    def __repr__(self) -> str:
        return (
            f"Projection(name={self.name!r}, proj={self.proj:.1f}, "
            f"salary={self.salary}, value={self.value:.2f}x, "
            f"own={self.ownership:.1f}%)"
        )


class ProjectionBatch(BaseModel):
    """
    Full projection output for an entire slate.

    Returned by the projection engine and passed directly to the optimizer.
    """

    site: str
    sport: str = "NBA"
    slate_date: Optional[date] = None
    projections: list[Projection] = Field(default_factory=list)
    engine_version: str = Field(default="canonical-v1")
    generated_at: Optional[str] = Field(
        default=None,
        description="ISO-format datetime string when this batch was produced.",
    )

    @property
    def player_count(self) -> int:
        return len(self.projections)

    @property
    def avg_proj(self) -> float:
        if not self.projections:
            return 0.0
        return round(sum(p.proj for p in self.projections) / len(self.projections), 2)

    def top_n(self, n: int = 10) -> list[Projection]:
        """Return the n highest-projected players."""
        return sorted(self.projections, key=lambda p: p.proj, reverse=True)[:n]

    def by_position(self, position: str) -> list[Projection]:
        """Return projections for players eligible at the given position."""
        pos_upper = position.upper()
        return [p for p in self.projections if pos_upper in p.position.upper()]

    def __repr__(self) -> str:
        return (
            f"ProjectionBatch(site={self.site!r}, players={self.player_count}, "
            f"avg_proj={self.avg_proj:.1f})"
        )
