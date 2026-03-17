import sys, duckdb, pandas as pd
sys.path.insert(0, '/app')
from analysis.nba.ownership_v2 import _load_training_data, _enrich_with_game_logs

for site in ("DK", "FD"):
    df = _load_training_data("NBA", site)
    if df is None or df.empty:
        print(f"{site}: no training data")
        continue
    print(f"\n=== {site} ===")
    print(f"  rows: {len(df)}")
    print(f"  cols: {list(df.columns)}")
    if 'l10_avg' in df.columns:
        nn = df['l10_avg'].notna().sum()
        print(f"  l10_avg non-null: {nn} / {len(df)}  ({100*nn/len(df):.1f}%)")
    if 'l10_std' in df.columns:
        nn = df['l10_std'].notna().sum()
        print(f"  l10_std non-null: {nn} / {len(df)}  ({100*nn/len(df):.1f}%)")
    if 'is_home' in df.columns:
        nn = df['is_home'].notna().sum()
        print(f"  is_home non-null: {nn} / {len(df)}  ({100*nn/len(df):.1f}%)")
    if 'l10_avg' in df.columns and df['l10_avg'].notna().sum() > 0:
        print(f"  l10_avg range: {df['l10_avg'].min():.1f} – {df['l10_avg'].max():.1f}")
