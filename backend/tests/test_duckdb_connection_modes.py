from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))


def test_analytics_service_uses_shared_writable_edge_conn(monkeypatch):
    import services.analytics_service as svc

    captured = {}

    def fake_get_conn(path, *, db_key=None, read_only=False):
        captured["path"] = path
        captured["db_key"] = db_key
        captured["read_only"] = read_only
        return object()

    monkeypatch.setattr(svc, "_get_conn", fake_get_conn)

    svc._edge()

    assert captured["db_key"] == "dfs_edge"
    assert captured["read_only"] is False


def test_player_trends_service_uses_shared_writable_edge_conn(monkeypatch):
    import services.player_trends_service as svc

    captured = {}

    def fake_get_conn(path, *, db_key=None, read_only=False):
        captured["path"] = path
        captured["db_key"] = db_key
        captured["read_only"] = read_only
        return object()

    monkeypatch.setattr(svc, "_get_conn", fake_get_conn)

    svc._edge()

    assert captured["db_key"] == "dfs_edge"
    assert captured["read_only"] is False