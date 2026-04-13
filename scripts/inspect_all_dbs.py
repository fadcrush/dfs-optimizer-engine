import duckdb, pathlib, sys

ROOT = pathlib.Path(r"F:\Dev\N_B_A_and_N_F_L\data")
dbs = {
    "dfs_edge":    ROOT / "dfs_edge.duckdb",

    "contest_results": ROOT / "contest_results.duckdb",
    "projection_cache": ROOT / "projection_cache.duckdb",
    "ownership_history": ROOT / "ownership_history.duckdb",
}

for key, path in dbs.items():
    if not path.exists():
        print(f"\n=== {key} — FILE NOT FOUND ===")
        continue
    print(f"\n=== {key} ({path.stat().st_size//1024} KB) ===")
    try:
        con = duckdb.connect(str(path), read_only=True)
        tables = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main' AND table_type='BASE TABLE'"
        ).fetchall()
        for (t,) in tables:
            if t == 'schema_version':
                continue
            try:
                count = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                if count == 0:
                    print(f"  {t}: 0 rows (empty)")
                    continue
                # Show column names and a sample row
                cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")').fetchall()]
                print(f"  {t}: {count:,} rows")
                print(f"    cols ({len(cols)}): {cols}")
                # Show a sample row
                sample = con.execute(f'SELECT * FROM "{t}" LIMIT 1').fetchone()
                if sample:
                    sample_str = str(dict(zip(cols, sample)))[:200]
                    print(f"    sample: {sample_str}")
            except Exception as e:
                print(f"  {t}: ERROR — {e}")
        con.close()
    except Exception as e:
        print(f"  LOCKED/ERROR: {e}")
