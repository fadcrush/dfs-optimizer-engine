import asyncio
import importlib
import os
import sys
from pathlib import Path

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from analysis.nba.optimizer import optimize_portfolio, validate_lineup
from analysis.shared.scoring import StatLine, dk_score, fd_score
from services.projection_service import generate_projections


os.environ.setdefault("DATABASE_URL", "sqlite:///./test_dfs.db")


def _write_csv(tmp_path: Path, content: str) -> Path:
    target = tmp_path / "slate.csv"
    target.write_text(content, encoding="utf-8")
    return target


def test_scoring_unit_dk_and_fd():
    stat = StatLine(
        pts=25,
        two_pt_made=6,
        three_pt_made=3,
        reb=10,
        ast=8,
        stl=2,
        blk=1,
        tov=3,
    )
    dk = dk_score(stat, double_double=True, triple_double=False)
    fd = fd_score(stat)

    assert round(dk, 2) == 57.0
    assert round(fd, 2) == 55.0


def test_optimizer_lineup_validity_fd_and_dk():
    players = pd.DataFrame(
        [
            {"DFS_ID": "p1", "Name": "A", "Pos": "PG/G", "Salary": 8000, "Proj": 45, "Own": 20},
            {"DFS_ID": "p2", "Name": "B", "Pos": "PG", "Salary": 7200, "Proj": 39, "Own": 18},
            {"DFS_ID": "p3", "Name": "C", "Pos": "SG/G", "Salary": 7600, "Proj": 42, "Own": 17},
            {"DFS_ID": "p4", "Name": "D", "Pos": "SG", "Salary": 6500, "Proj": 35, "Own": 12},
            {"DFS_ID": "p5", "Name": "E", "Pos": "SF/F", "Salary": 7000, "Proj": 36, "Own": 11},
            {"DFS_ID": "p6", "Name": "F", "Pos": "SF", "Salary": 6100, "Proj": 32, "Own": 10},
            {"DFS_ID": "p7", "Name": "G", "Pos": "PF/F", "Salary": 6900, "Proj": 34, "Own": 15},
            {"DFS_ID": "p8", "Name": "H", "Pos": "PF", "Salary": 5800, "Proj": 30, "Own": 9},
            {"DFS_ID": "p9", "Name": "I", "Pos": "C", "Salary": 7400, "Proj": 38, "Own": 14},
            {"DFS_ID": "p10", "Name": "J", "Pos": "C", "Salary": 5200, "Proj": 26, "Own": 8},
            {"DFS_ID": "p11", "Name": "K", "Pos": "PG/SG", "Salary": 5000, "Proj": 28, "Own": 7},
            {"DFS_ID": "p12", "Name": "L", "Pos": "SF/PF", "Salary": 5400, "Proj": 29, "Own": 6},
        ]
    )

    fd_portfolio = optimize_portfolio(players=players, site="FD", n_lineups=1)
    assert not fd_portfolio.empty
    assert validate_lineup(fd_portfolio[fd_portfolio["LineupIndex"] == 0], "FD")

    dk_portfolio = optimize_portfolio(players=players, site="DK", n_lineups=1)
    assert not dk_portfolio.empty
    assert validate_lineup(dk_portfolio[dk_portfolio["LineupIndex"] == 0], "DK")


def test_dev_placeholder_disabled_in_production(tmp_path: Path):
    os.environ["DFS_ENABLE_DEV_PLACEHOLDER"] = "0"
    slate_path = _write_csv(
        tmp_path,
        "Id,Position,Nickname,Salary,Team,Opponent\n1,PG,Alpha,7000,AAA,BBB\n2,SG,Beta,6500,CCC,DDD\n",
    )

    result = asyncio.run(generate_projections(str(slate_path), user_id="test-user", site="FD", sport="NBA"))
    assert result.get("algorithm") != "DEV_ONLY placeholder"
    assert result.get("note") != "Enabled via DFS_ENABLE_DEV_PLACEHOLDER=1"


def test_api_upload_generate_download_flow(tmp_path: Path, monkeypatch, isolated_upload_dirs):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test_dfs.db")
    db_module = importlib.import_module("database.db")
    importlib.reload(db_module)
    projections_router = importlib.import_module("routers.projections")
    projections_router = importlib.reload(projections_router)

    app = FastAPI()
    app.include_router(projections_router.router)
    from services.auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: {"id": "user-1", "tier": "pro"}
    client = TestClient(app)

    async def fake_generate_projections(_: str, user_id: str, site: str = "FD", sport: str = "NBA"):
        assert user_id == "user-1"
        return {
            "success": True,
            "projections": [
                {"dfs_id": "1", "name": "Jalen Johnson", "projection": 50.1},
                {"dfs_id": "2", "name": "Onyeka Okongwu", "projection": 35.2},
                {"dfs_id": "3", "name": "Dyson Daniels", "projection": 32.4},
            ],
            "stats": {"total_players": 3, "avg_projection": 39.23, "total_salary_available": 26200, "slate_file": "slate.csv", "site": site, "sport": sport},
            "algorithm": "test-double",
        }

    monkeypatch.setattr(projections_router, "generate_projections", fake_generate_projections)

    csv_content = (
        "Id,Position,Nickname,Salary,Team,Opponent,FPPG\n"
        "125392-157833,PF/SF,Jalen Johnson,10700,ATL,DEN,54.55\n"
        "125392-145304,C/PF,Onyeka Okongwu,7400,ATL,DEN,36.52\n"
        "125392-171784,SG/PG,Dyson Daniels,8100,ATL,DEN,33.63\n"
    )

    response = client.post(
        "/api/projections/generate-from-upload?site=FD&sport=NBA",
        files={"file": ("slate.csv", csv_content, "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_projections"] == 3

    download_name = body["download_file"]
    download_response = client.get(f"/api/projections/download/{download_name}")
    assert download_response.status_code == 200
    assert "text/csv" in download_response.headers.get("content-type", "")

