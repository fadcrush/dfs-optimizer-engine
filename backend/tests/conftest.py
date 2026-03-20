from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import MagicMock


def pytest_configure(config):  # noqa: ARG001
    """Install psycopg2 stub before any backend import triggers SQLAlchemy."""
    if "psycopg2" not in sys.modules:
        stub = MagicMock()
        stub.__version__ = "2.9.0"
        sys.modules["psycopg2"] = stub
        sys.modules["psycopg2.extensions"] = MagicMock()
        sys.modules["psycopg2.extras"] = MagicMock()

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'backend'))

from services import file_service
from services.auth import get_current_user


@pytest.fixture
def isolated_upload_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    upload_dir = tmp_path / 'uploads'
    slates_dir = upload_dir / 'slates'
    projections_dir = upload_dir / 'projections'
    lineups_dir = upload_dir / 'lineups'
    slates_dir.mkdir(parents=True, exist_ok=True)
    projections_dir.mkdir(parents=True, exist_ok=True)
    lineups_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(file_service, 'UPLOAD_DIR', upload_dir)
    monkeypatch.setattr(file_service, 'SLATES_DIR', slates_dir)
    monkeypatch.setattr(file_service, 'PROJECTIONS_DIR', projections_dir)
    monkeypatch.setattr(file_service, 'LINEUPS_DIR', lineups_dir)
    return upload_dir


@pytest.fixture
def make_authed_client(isolated_upload_dirs: Path):
    def _make(router, user_id: str = 'test-user') -> TestClient:
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: {'id': user_id}
        return TestClient(app, raise_server_exceptions=False)

    return _make