from pathlib import Path
import os
import requests
import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

WEATHER_API_KEY = os.getenv("a28c1db7a46f42089d012818252711")

OUT_DIR = ROOT / "data" / "nfl" / "raw" / "weather"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_history(city_or_latlon: str, date_yyyy_mm_dd: str) -> dict:
    """
    WeatherAPI history endpoint:
    - city_or_latlon examples:
        "New York,NY"  or  "40.7505,-73.9934"
    - date_yyyy_mm_dd: "2025-12-07"
    """
    if not WEATHER_API_KEY:
        raise RuntimeError("Missing WEATHER_API_KEY in .env")

    url = "https://api.weatherapi.com/v1/history.json"
    params = {
        "key": WEATHER_API_KEY,
        "q": city_or_latlon,
        "dt": date_yyyy_mm_dd,
        # optional: "aqi": "no", "alerts": "no"
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def summarize_game_weather(payload: dict) -> dict:
    """
    Turn WeatherAPI payload into a compact row of features useful for NFL DFS.
    """
    loc = payload.get("location", {})
    day = (payload.get("forecast", {}).get("forecastday") or [{}])[0].get("day", {})
    astro = (payload.get("forecast", {}).get("forecastday") or [{}])[0].get("astro", {})

    return {
        "name": loc.get("name"),
        "region": loc.get("region"),
        "country": loc.get("country"),
        "date": (payload.get("forecast", {}).get("forecastday") or [{}])[0].get("date"),
        "avgtemp_f": day.get("avgtemp_f"),
        "maxtemp_f": day.get("maxtemp_f"),
        "mintemp_f": day.get("mintemp_f"),
        "maxwind_mph": day.get("maxwind_mph"),
        "totalprecip_in": day.get("totalprecip_in"),
        "avghumidity": day.get("avghumidity"),
        "daily_chance_of_rain": day.get("daily_chance_of_rain"),
        "daily_chance_of_snow": day.get("daily_chance_of_snow"),
        "condition": (day.get("condition") or {}).get("text"),
        "sunrise": astro.get("sunrise"),
        "sunset": astro.get("sunset"),
    }


def main():
    # Example test: MetLife area / NYC on a past date
    city = "East Rutherford,NJ"
    date = "2025-12-07"

    payload = fetch_history(city, date)
    row = summarize_game_weather(payload)

    df = pd.DataFrame([row])
    out_path = OUT_DIR / f"weather_{city.replace(' ', '_').replace(',', '-')}_{date}.csv"
    df.to_csv(out_path, index=False)

    print("✅ WeatherAPI fetch OK")
    print(f"Saved: {out_path}")
    print(row)


if __name__ == "__main__":
    main()
