"""
Test API Connections
Verifies all API keys are working
"""

import os
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dfs_site.settings')

import django
django.setup()

from dotenv import load_dotenv
load_dotenv('config/.env')

from analysis.shared.api_clients import (
    TheOddsAPIClient,
    NBAFreeDataClient,
    BallDontLieAPIClient,
    WeatherAPIClient
)

print("="*60)
print("  TESTING API CONNECTIONS")
print("="*60)

# Test 1: TheOdds API
print("\n1️⃣  Testing TheOdds API (Vegas Lines)...")
try:
    theodds_key = os.getenv('THEODDS_API_KEY')
    if not theodds_key or theodds_key == 'your_theodds_key_here':
        print("  ⚠️  No API key found - skipping")
    else:
        client = TheOddsAPIClient(theodds_key)
        games = client.get_nba_odds()
        if games:
            print(f"  ✓ SUCCESS! Found {len(games)} NBA games")
            if games:
                print(f"    Example: {games[0]['away_team']} @ {games[0]['home_team']}")
        else:
            print("  ⚠️  No games found (might be off-season)")
except Exception as e:
    print(f"  ✗ FAILED: {e}")

# Test 2: NBA Free Data (nba_api / stats.nba.com — no key needed)
print("\n2️⃣  Testing NBA Free Data Client (stats.nba.com via nba_api)...")
try:
    client = NBAFreeDataClient()
    stats = client.get_player_stats_season('2026')
    if stats:
        print(f"  ✓ SUCCESS! Found {len(stats)} player stats (no API key required)")
        print(f"    Example: {stats[0].get('Player', 'N/A')} - {stats[0].get('Team', 'N/A')} "
              f"| {stats[0].get('PTS', 'N/A')} PPG")
    else:
        print("  ⚠️  No stats returned (nba_api may not be installed)")
except Exception as e:
    print(f"  ✗ FAILED: {e}")

# Test 3: BallDontLie API (Free - always works)
print("\n3️⃣  Testing BallDontLie API (Free NBA Stats)...")
try:
    client = BallDontLieAPIClient()
    players = client.get_players(search='LeBron')
    if players:
        print(f"  ✓ SUCCESS! Found {len(players)} players")
        if players:
            print(f"    Example: {players[0].get('first_name')} {players[0].get('last_name')}")
    else:
        print("  ⚠️  No players found")
except Exception as e:
    print(f"  ✗ FAILED: {e}")

# Test 4: Weather API
print("\n4️⃣  Testing Weather API...")
try:
    weather_key = os.getenv('WEATHER_API_KEY')
    if not weather_key or weather_key == 'your_weather_key_here':
        print("  ⚠️  No API key found - skipping")
    else:
        client = WeatherAPIClient(weather_key)
        from datetime import datetime, timedelta
        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        weather = client.get_forecast('New York', tomorrow)
        if weather:
            print(f"  ✓ SUCCESS! Got weather forecast")
            print(f"    Example: {weather.get('temp_f')}°F, {weather.get('condition')}")
        else:
            print("  ⚠️  No weather data")
except Exception as e:
    print(f"  ✗ FAILED: {e}")

print("\n" + "="*60)
print("  TEST COMPLETE")
print("="*60)
print("\nNext Steps:")
print("  1. Fix any failed API connections")
print("  2. Run: python manage.py run_projections --site FD")
print("  3. Check exports/projections/ for output files")
print("="*60)