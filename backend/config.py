"""
DFS Edge Pro — Centralised environment bootstrap.

Import this module at the top of any entry point (workers, scripts, test
conftest.py, Celery tasks) that may be executed BEFORE backend/main.py has
run.  It is safe to import multiple times — ``load_dotenv`` skips already-set
variables when ``override=False``.

Usage
-----
    # At the top of any module that needs env vars before main.py boots:
    import backend.config  # noqa: F401  — side-effect: loads root .env

Or simply::

    from backend.config import ROOT, ensure_env_loaded
    ensure_env_loaded()
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger(__name__)

# Absolute path to the repository root, regardless of CWD or invocation method.
ROOT: Path = Path(__file__).resolve().parent.parent

_ENV_FILE: Path = ROOT / ".env"
_env_loaded: bool = False


def ensure_env_loaded() -> None:
    """Load root .env exactly once.  Idempotent; safe to call from multiple modules."""
    global _env_loaded
    if _env_loaded:
        return
    if _ENV_FILE.exists():
        load_dotenv(dotenv_path=_ENV_FILE, override=False)
        log.debug("Root .env loaded from %s", _ENV_FILE)
    else:
        log.warning(
            "Root .env not found at %s — copy .env.example and fill in values",
            _ENV_FILE,
        )
    _env_loaded = True


def get_required(key: str) -> str:
    """Return env var ``key`` or raise ``RuntimeError`` with a helpful message."""
    val = os.getenv(key, "")
    if not val:
        raise RuntimeError(
            f"Required environment variable '{key}' is not set.  "
            f"Add it to {_ENV_FILE} (see .env.example for reference)."
        )
    return val


# Run on import so that any module that does ``import backend.config`` gets
# the env loaded immediately.
ensure_env_loaded()
