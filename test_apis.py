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
    SportsDataAPIClient,
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

# Test 2: SportsData API
print("\n2️⃣  Testing SportsData API (Player Stats)...")
try:
    sportsdata_key = os.getenv('SPORTSDATA_API_KEY')
    if not sportsdata_key or sportsdata_key == 'your_sportsdata_key_here':
        print("  ⚠️  No API key found - skipping")
    else:
        client = SportsDataAPIClient(sportsdata_key)
        stats = client.get_player_stats_season('2025')
        if stats:
            print(f"  ✓ SUCCESS! Found {len(stats)} player stats")
            if stats:
                print(f"    Example: {stats[0].get('Name', 'N/A')} - {stats[0].get('Team', 'N/A')}")
        else:
            print("  ⚠️  No stats found")
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