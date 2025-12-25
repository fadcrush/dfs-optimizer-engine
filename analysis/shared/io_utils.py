"""Helper functions for loading slates, lineups, and historical data."""

from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]

def load_csv(relative_path: str) -> pd.DataFrame:
    path = ROOT / relative_path
    return pd.read_csv(path)
