import pandas as pd

from routers.optimizer import _lineups_to_export_csv as optimizer_export_csv
from routers.pipeline import _lineups_to_export_csv as pipeline_export_csv


FD_ROWS = [
    {"LineupIndex": 0, "Name": "PG One", "Pos": "PG", "Salary": 7000, "Proj": 40, "DFS_ID": "1001", "Raw_DFS_ID": "127280-1001"},
    {"LineupIndex": 0, "Name": "PG Two", "Pos": "PG", "Salary": 6800, "Proj": 38, "DFS_ID": "1002", "Raw_DFS_ID": "127280-1002"},
    {"LineupIndex": 0, "Name": "SG One", "Pos": "SG", "Salary": 6600, "Proj": 36, "DFS_ID": "1003", "Raw_DFS_ID": "127280-1003"},
    {"LineupIndex": 0, "Name": "SG Two", "Pos": "SG", "Salary": 6400, "Proj": 35, "DFS_ID": "1004", "Raw_DFS_ID": "127280-1004"},
    {"LineupIndex": 0, "Name": "SF One", "Pos": "SF", "Salary": 6200, "Proj": 34, "DFS_ID": "1005", "Raw_DFS_ID": "127280-1005"},
    {"LineupIndex": 0, "Name": "SF Two", "Pos": "SF", "Salary": 6000, "Proj": 33, "DFS_ID": "1006", "Raw_DFS_ID": "127280-1006"},
    {"LineupIndex": 0, "Name": "PF One", "Pos": "PF", "Salary": 5800, "Proj": 32, "DFS_ID": "1007", "Raw_DFS_ID": "127280-1007"},
    {"LineupIndex": 0, "Name": "PF Two", "Pos": "PF", "Salary": 5600, "Proj": 31, "DFS_ID": "1008", "Raw_DFS_ID": "127280-1008"},
    {"LineupIndex": 0, "Name": "Center", "Pos": "C", "Salary": 7400, "Proj": 41, "DFS_ID": "1009", "Raw_DFS_ID": "127280-1009"},
]


DK_ROWS = [
    {"LineupIndex": 0, "Name": "PG One", "Pos": "PG", "Salary": 7000, "Proj": 40, "DFS_ID": "2001"},
    {"LineupIndex": 0, "Name": "SG One", "Pos": "SG", "Salary": 6600, "Proj": 36, "DFS_ID": "2002"},
    {"LineupIndex": 0, "Name": "SF One", "Pos": "SF", "Salary": 6200, "Proj": 34, "DFS_ID": "2003"},
    {"LineupIndex": 0, "Name": "PF One", "Pos": "PF", "Salary": 5800, "Proj": 32, "DFS_ID": "2004"},
    {"LineupIndex": 0, "Name": "Center", "Pos": "C", "Salary": 7400, "Proj": 41, "DFS_ID": "2005"},
    {"LineupIndex": 0, "Name": "Guard", "Pos": "PG/SG", "Salary": 5900, "Proj": 33, "DFS_ID": "2006"},
    {"LineupIndex": 0, "Name": "Forward", "Pos": "SF/PF", "Salary": 5700, "Proj": 31, "DFS_ID": "2007"},
    {"LineupIndex": 0, "Name": "Utility", "Pos": "PF/C", "Salary": 6100, "Proj": 35, "DFS_ID": "2008"},
]


def test_fd_export_uses_composite_ids_in_optimizer_and_pipeline():
    lineups_df = pd.DataFrame(FD_ROWS)

    optimizer_csv = optimizer_export_csv(lineups_df, "FD")
    pipeline_csv = pipeline_export_csv(lineups_df, "FD")

    for csv_text in (optimizer_csv, pipeline_csv):
        lines = csv_text.strip().splitlines()
        assert lines[0] == "entry_id,contest_id,contest_name,entry_fee,PG,PG,SG,SG,SF,SF,PF,PF,C"
        # FD upload requires composite IDs (slateId-playerId)
        assert "127280-1001" in lines[1]
        assert "127280-1009" in lines[1]


def test_dk_export_keeps_name_and_id_format():
    lineups_df = pd.DataFrame(DK_ROWS)

    optimizer_csv = optimizer_export_csv(lineups_df, "DK")
    pipeline_csv = pipeline_export_csv(lineups_df, "DK")

    for csv_text in (optimizer_csv, pipeline_csv):
        lines = csv_text.strip().splitlines()
        assert lines[0] == "Entry ID,Contest Name,Contest ID,Entry Fee,PG,SG,SF,PF,C,G,F,UTIL"
        assert "PG One (2001)" in lines[1]
        assert "Utility (2008)" in lines[1]