import duckdb, pathlib

dbs = [p for p in pathlib.Path('/app/data').glob('*.duckdb') if '.wal' not in p.name]
for db in sorted(dbs):
    print(f'\n=== {db.name} ===')
    try:
        con = duckdb.connect(str(db), read_only=True)
        tables = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
        for (t,) in tables:
            try:
                count = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")').fetchall()]
                print(f'  {t}: {count} rows')
                print(f'    cols: {cols}')
            except Exception as e:
                print(f'  {t}: ERROR {e}')
        con.close()
    except Exception as e:
        print(f'  OPEN ERROR: {e}')
