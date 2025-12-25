from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=True)

def masked(v):
    if not v:
        return "MISSING"
    return f"LOADED (...{v[-4:]})"

print("ENV CHECK")
print("---------")
print("WEATHER_API_KEY:", masked(os.getenv("WEATHER_API_KEY")))
print("THEODDS_API_KEY:", masked(os.getenv("THEODDS_API_KEY")))
print("SPORTSDATA_API_KEY:", masked(os.getenv("SPORTSDATA_API_KEY")))
print("BALLDONTLIE_API_KEY:", masked(os.getenv("BALLDONTLIE_API_KEY")))
print("RAPIDAPI_KEY:", masked(os.getenv("RAPIDAPI_KEY")))
print("OPENAI_API_KEY:", masked(os.getenv("OPENAI_API_KEY")))
