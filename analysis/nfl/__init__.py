"""NFL analysis package — projection engine, optimizer, and player pool utilities."""

from analysis.nfl.projection_engine import NFLProjectionEngine
from analysis.nfl.optimizer import NFLOptimizer, NFLLineup

__all__ = [
    "NFLProjectionEngine",
    "NFLOptimizer",
    "NFLLineup",
]
