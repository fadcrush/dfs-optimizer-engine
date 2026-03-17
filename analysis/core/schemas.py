from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ProjectionContext(BaseModel):
    sport: Literal["NBA", "NFL"] = "NBA"
    site: Literal["FD", "DK"]
    slate_date: date | None = None
    slate_id: str | None = None          # optional cache key (e.g. upload filename stem)
    injuries: dict[str, Any] = Field(default_factory=dict)
    vegas: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Stacking settings
    enable_stacking: bool = False
    min_game_stack: int = 0          # minimum players from same game
    bring_back_count: int = 0        # players from opposing team when stacking
    max_from_team: int = 8           # max from one team (no real constraint by default)
    max_from_game: int = 6           # max from one game

    # Lock / exclude
    locks: list[str] = Field(default_factory=list)    # player names forced into every lineup
    fades: list[str] = Field(default_factory=list)    # player names excluded from pool

    # Projection overrides (player name → override value)
    projection_overrides: dict[str, float] = Field(default_factory=dict)

    # Cache control
    force_refresh: bool = False

    # Contest mode preset
    contest_mode: Literal["cash", "gpp", "gpp_large", "single_entry", "custom"] = "custom"
