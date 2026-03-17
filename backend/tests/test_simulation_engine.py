import pandas as pd

from analysis.core.simulation import SimulationConfig, simulate_lineup_scores


def test_simulation_summary():
    projections = pd.DataFrame(
        [
            {"DFS_ID": "p1", "Proj": 40, "Floor": 28, "Ceiling": 52, "Team": "AAA", "Opp": "BBB"},
            {"DFS_ID": "p2", "Proj": 35, "Floor": 24, "Ceiling": 46, "Team": "AAA", "Opp": "BBB"},
            {"DFS_ID": "p3", "Proj": 30, "Floor": 20, "Ceiling": 42, "Team": "CCC", "Opp": "DDD"},
            {"DFS_ID": "p4", "Proj": 25, "Floor": 16, "Ceiling": 36, "Team": "CCC", "Opp": "DDD"},
        ]
    )

    lineups = pd.DataFrame(
        [
            {"LineupIndex": 0, "DFS_ID": "p1", "Salary": 9000, "Proj": 40, "Pos": "PG"},
            {"LineupIndex": 0, "DFS_ID": "p3", "Salary": 7000, "Proj": 30, "Pos": "SF"},
            {"LineupIndex": 1, "DFS_ID": "p2", "Salary": 8500, "Proj": 35, "Pos": "SG"},
            {"LineupIndex": 1, "DFS_ID": "p4", "Salary": 6500, "Proj": 25, "Pos": "PF"},
        ]
    )

    config = SimulationConfig(n_sims=250, seed=7, correlation="team")
    summary, scores = simulate_lineup_scores(lineups, projections, config)

    assert not summary.empty
    assert scores.shape[0] == 2
    assert "Mean" in summary.columns
    assert "WinRate" in summary.columns
