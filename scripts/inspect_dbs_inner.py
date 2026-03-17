import sys, duckdb, pathlib
sys.path.insert(0, '/app')

ROOT = pathlib.Path('/app/data')
dbs = {
    "dfs_edge":         ROOT / "dfs_edge.duckdb",
    "dfs_master":       ROOT / "dfs_master.duckdb",
    "contest_results":  ROOT / "contest_results.duckdb",
    "projection_cache": ROOT / "projection_cache.duckdb",
}

for key, path in dbs.items():
    if not path.exists():
        print(f"\n=== {key}: NOT FOUND ===")
        continue
    print(f"\n=== {key} ({path.stat().st_size//1024} KB) ===")
    try:
        con = duckdb.connect(str(path), read_only=True)
        tables = con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main' AND table_type='BASE TABLE'"
        ).fetchall()
        for (t,) in tables:
            if t == 'schema_version':
                continue
            try:
                count = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")').fetchall()]
                print(f"  {t}: {count:,} rows | cols: {cols}")
                if count > 0:
                    sample = con.execute(f'SELECT * FROM "{t}" LIMIT 1').fetchone()
                    print(f"    sample: {dict(zip(cols, sample))}")
            except Exception as e:
                print(f"  {t}: {e}")
        con.close()
    except Exception as e:
        print(f"  ERROR: {e}")
