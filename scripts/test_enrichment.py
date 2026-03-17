import sys, logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
sys.path.insert(0, '/app')
from analysis.nba.ownership_v2 import _load_training_data, train_ownership_model

df = _load_training_data('NBA', 'DK')
print(f"rows: {len(df)}")
print(f"cols: {list(df.columns)}")
if 'l10_avg' in df.columns:
    print(f"l10_avg non-null: {df['l10_avg'].notna().sum()} / {len(df)}")
    print(f"l10_std non-null: {df['l10_std'].notna().sum()} / {len(df)}")
if 'is_home' in df.columns:
    print(f"is_home non-null: {df['is_home'].notna().sum()} / {len(df)}")

m = train_ownership_model('NBA', 'DK')
print(f"model: {type(m).__name__ if m else 'NONE'}")
