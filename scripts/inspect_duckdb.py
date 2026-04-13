from pathlib import Path

import duckdb


db_paths = [
    Path("data/dfs_edge.duckdb"),

    Path("data/nba_news.duckdb"),
]

for db_path in db_paths:
    if not db_path.exists():
        continue
    print(f"\n=== {db_path} ===")
    con = duckdb.connect(str(db_path))
    tables = con.execute("show tables").fetchall()
    if not tables:
        print("(no tables)")
        con.close()
        continue
    for (table_name,) in tables:
        print(f"\n-- {table_name} --")
        cols = con.execute(f"describe {table_name}").fetchall()
        for col in cols:
            print(" ", col)
    con.close()
