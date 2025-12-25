from pathlib import Path
import os
import time
import json
import requests
import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

OUT_DIR = ROOT / "data" / "nba" / "raw"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BALLDONTLIE_API_KEY = os.getenv("BALLDONTLIE_API_KEY")

CACHE_CSV = OUT_DIR / "balldontlie_players.csv"
STATE_JSON = OUT_DIR / "balldontlie_players_state.json"

def fetch_balldontlie_players(force: bool = False):
    """
    Robust fetch:
    - Caches output to CSV
    - Stores resume cursor to STATE_JSON
    - Uses exponential backoff on 429s
    """

    if CACHE_CSV.exists() and not force:
        print(f"✅ Cache exists, skipping fetch: {CACHE_CSV}")
        return

    if not BALLDONTLIE_API_KEY:
        raise RuntimeError("Missing BALLDONTLIE_API_KEY in .env")

    url = "https://api.balldontlie.io/v1/players"
    headers = {"Authorization": BALLDONTLIE_API_KEY}

    all_rows = []
    cursor = None

    # Resume support
    if STATE_JSON.exists():
        try:
            state = json.loads(STATE_JSON.read_text(encoding="utf-8"))
            cursor = state.get("next_cursor")
            all_rows = state.get("rows", [])
            if cursor:
                print(f"↩️ Resuming from cursor={cursor} with {len(all_rows)} rows cached in state.")
        except Exception:
            pass

    backoff = 5  # seconds
    max_backoff = 120

    while True:
        params = {"per_page": 100}
        if cursor:
            params["cursor"] = cursor

        r = requests.get(url, headers=headers, params=params, timeout=30)

        # Handle rate limit
        if r.status_code == 429:
            print(f"Rate limited (429) — sleeping {backoff}s...")
            time.sleep(backoff)
            backoff = min(max_backoff, int(backoff * 1.5))
            continue

        # Reset backoff on success
        backoff = 5

        r.raise_for_status()
        payload = r.json()

        data = payload.get("data", [])
        all_rows.extend(data)

        meta = payload.get("meta", {})
        cursor = meta.get("next_cursor")

        # Save resume state every page
        STATE_JSON.write_text(
            json.dumps({"next_cursor": cursor, "rows": all_rows}, ensure_ascii=False),
            encoding="utf-8",
        )

        if not cursor:
            break

        # Be polite
        time.sleep(1.0)

    df = pd.DataFrame(all_rows)
    df.to_csv(CACHE_CSV, index=False)
    print(f"✅ Saved: {CACHE_CSV} ({len(df)} rows)")

    # Clean up state file after completion
    if STATE_JSON.exists():
        STATE_JSON.unlink()

if __name__ == "__main__":
    fetch_balldontlie_players(force=False)
