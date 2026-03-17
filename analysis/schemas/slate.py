"""
Slate — canonical DK/FD salary slate data contract.

Wraps the list of players loaded from an uploaded salary CSV together with
metadata about the contest context (site, sport, date).
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from .player import Player


class Slate(BaseModel):
    """
    A fully parsed DK or FD salary slate.

    Returned by the slate ingestion layer and consumed by projection and
    optimizer layers downstream.
    """

    # --- Context ---
    site: Literal["DK", "FD"] = Field(description="Contest site.")
    sport: Literal["NBA", "NFL"] = Field(default="NBA", description="Sport.")
    slate_date: Optional[date] = Field(
        default=None,
        description="Date of the games on this slate (inferred from game_info if not supplied).",
    )
    raw_file_path: Optional[str] = Field(
        default=None,
        description="Absolute path to the original uploaded salary CSV.",
    )
    slate_id: Optional[str] = Field(
        default=None,
        description="Site-provided slate ID (e.g. DraftKings contest ID).",
    )

    # --- Players ---
    players: list[Player] = Field(
        default_factory=list,
        description="All players on this slate in their original order.",
    )

    # --- Derived stats (populated by model_validator) ---
    player_count: int = Field(default=0, description="Total players on the slate.")
    team_count: int = Field(default=0, description="Unique teams on the slate.")
    salary_min: int = Field(default=0, description="Minimum player salary.")
    salary_max: int = Field(default=0, description="Maximum player salary.")
    salary_avg: float = Field(default=0.0, description="Mean player salary.")

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _derive_stats(self) -> "Slate":
        if not self.players:
            return self
        salaries = [p.salary for p in self.players]
        self.player_count = len(self.players)
        self.team_count = len({p.team for p in self.players})
        self.salary_min = min(salaries)
        self.salary_max = max(salaries)
        self.salary_avg = round(sum(salaries) / len(salaries), 2)
        return self

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def active_players(self) -> list[Player]:
        """Players whose injury status is not OUT."""
        return [p for p in self.players if not p.is_out]

    @property
    def out_players(self) -> list[Player]:
        """Players confirmed OUT."""
        return [p for p in self.players if p.is_out]

    @property
    def salary_cap(self) -> int:
        """Site salary cap for one lineup."""
        return 50_000 if self.site == "DK" else 60_000

    def get_player(self, player_id: str) -> Optional[Player]:
        """Return a player by their slug ID, or None."""
        for p in self.players:
            if p.player_id == player_id:
                return p
        return None

    def __len__(self) -> int:
        return len(self.players)

    def __repr__(self) -> str:
        return (
            f"Slate(site={self.site!r}, sport={self.sport!r}, "
            f"date={self.slate_date}, players={self.player_count}, "
            f"teams={self.team_count})"
        )
