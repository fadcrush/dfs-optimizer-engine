"""
Player — canonical player data contract.

Represents a single player entry as read from a DK/FD salary export before
any projection, optimization, or simulation has been applied.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class InjuryStatus(str, Enum):
    """Normalized injury designation across all data sources."""
    OUT = "OUT"
    GAME_TIME_DECISION = "GTD"
    QUESTIONABLE = "Q"
    DOUBTFUL = "D"
    PROBABLE = "P"
    ACTIVE = "ACTIVE"
    UNKNOWN = ""

    @classmethod
    def from_raw(cls, raw: str) -> "InjuryStatus":
        """Map messy raw strings ('Out', 'out', 'O', 'GTD') to canonical values."""
        if not raw:
            return cls.UNKNOWN
        norm = raw.strip().upper()
        mapping = {
            "OUT": cls.OUT,
            "O": cls.OUT,
            "GTD": cls.GAME_TIME_DECISION,
            "GAME TIME DECISION": cls.GAME_TIME_DECISION,
            "PROBABLE": cls.PROBABLE,
            "P": cls.PROBABLE,
            "QUESTIONABLE": cls.QUESTIONABLE,
            "Q": cls.QUESTIONABLE,
            "DOUBTFUL": cls.DOUBTFUL,
            "D": cls.DOUBTFUL,
            "ACTIVE": cls.ACTIVE,
            "A": cls.ACTIVE,
        }
        return mapping.get(norm, cls.UNKNOWN)


class Player(BaseModel):
    """
    Canonical representation of one player on a DK or FD salary slate.

    All downstream layers (projection, optimizer, simulation) accept a
    list[Player] or a DataFrame derived from list[Player].
    """

    # --- Identity ---
    player_id: str = Field(
        description="Slug used as the stable join key (e.g. 'lebron_james')."
    )
    name: str = Field(description="Display name as on the salary sheet.")
    dfs_id: Optional[str] = Field(
        default=None,
        description="Site-assigned numeric ID (DK: 'ID', FD: 'Id').",
    )

    # --- Roster ---
    position: str = Field(
        description="Raw position string from the export (e.g. 'PG/G/UTIL')."
    )
    team: str = Field(description="Team abbreviation (e.g. 'LAL').")
    opponent: str = Field(
        default="",
        description="Opponent abbreviation (e.g. 'GSW').",
        alias="Opp",
    )

    # --- Contest ---
    site: str = Field(description="'DK' or 'FD'.")
    sport: str = Field(default="NBA", description="'NBA' or 'NFL'.")
    salary: int = Field(ge=0, description="Salary in dollars.")
    game_info: str = Field(
        default="",
        description="Game info string from the salary sheet (e.g. 'LAL@GSW 07:30PM ET').",
    )
    is_home: Optional[bool] = Field(
        default=None,
        description="True if this player is on the home team. None if unknown.",
    )

    # --- Injury ---
    injury_status: InjuryStatus = Field(
        default=InjuryStatus.UNKNOWN,
        description="Normalized injury status.",
    )
    injury_detail: str = Field(
        default="",
        description="Human-readable injury reason (e.g. 'Left Knee — Out For Season').",
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

    @field_validator("team", "opponent")
    @classmethod
    def team_upper(cls, v: str) -> str:
        return v.upper() if v else v

    @property
    def is_out(self) -> bool:
        """True when the player is confirmed OUT of tonight's game."""
        return self.injury_status == InjuryStatus.OUT

    @property
    def value_baseline(self) -> float:
        """Salary / 1000 — threshold projection needed to return value."""
        return self.salary / 1_000 if self.salary > 0 else 0.0

    def __repr__(self) -> str:
        return (
            f"Player(name={self.name!r}, pos={self.position!r}, "
            f"team={self.team!r}, salary={self.salary}, "
            f"status={self.injury_status.value!r})"
        )
