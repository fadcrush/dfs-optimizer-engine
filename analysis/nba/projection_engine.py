"""
DEPRECATED — legacy NBA projection engine.
Use ``analysis.core.projection_engine.CanonicalNBAProjectionEngine`` instead.

This module exists purely as a compatibility shim.  All calls to
``NBAProjectionEngine.generate()`` are forwarded to the canonical engine.
"""

import logging

from analysis.core.projection_engine import CanonicalNBAProjectionEngine
from analysis.shared.scoring import score_nba_row  # noqa: F401 — kept for legacy callers

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public alias — lets old imports of NBAProjectionEngine keep working without
# any code changes, while routing all logic through the canonical engine.
# ---------------------------------------------------------------------------
NBAProjectionEngine = CanonicalNBAProjectionEngine

__all__ = ["NBAProjectionEngine"]
