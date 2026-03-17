"""
Canonical data schemas for the DFS Edge platform.

These Pydantic models define the authoritative data contracts between all
layers of the system — ingestion, projection, optimization, simulation, and API.

Usage
-----
    from analysis.schemas import Player, Slate, Projection, Lineup, ProjectionContext

All schemas are importable directly from this package.
"""

from .player import Player, InjuryStatus
from .slate import Slate
from .projection import Projection, ProjectionBatch
from .lineup import LineupPlayer, Lineup, LineupPortfolio

# Re-export ProjectionContext from its existing home so callers can use
# either import path.
from analysis.core.schemas import ProjectionContext

__all__ = [
    "Player",
    "InjuryStatus",
    "Slate",
    "Projection",
    "ProjectionBatch",
    "LineupPlayer",
    "Lineup",
    "LineupPortfolio",
    "ProjectionContext",
]
