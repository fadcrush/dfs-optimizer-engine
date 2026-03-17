import sys, pandas as pd, numpy as np
sys.path.insert(0, '/app')
from analysis.nba.ownership_v2 import predict_ownership, _fetch_l10_for_predict

# Quick l10 lookup check
l10 = _fetch_l10_for_predict(['LeBron James', 'Steph Curry', 'Jalen Johnson', 'NoPlayer'], 'DK')
print(f"l10 lookup: {len(l10)} players matched")
for k, v in list(l10.items())[:3]:
    print(f"  {k}: avg={v['l10_avg']:.1f} std={v['l10_std']:.1f}")

# Simulate a projections df
df = pd.DataFrame({
    'Name': ['LeBron James', 'Jalen Johnson', 'Victor Wembanyama'],
    'Proj': [48.5, 42.1, 45.2],
    'Salary': [11200, 8800, 10400],
    'team_total': [116.5, 112.0, 108.0],
    'is_home': [1, 0, 1],
})
result = predict_ownership(df, sport='NBA', site='DK')
print("\n=== predict_ownership ===")
print(result[['Name', 'Proj', 'Own_Est', 'own_source']].to_string())
