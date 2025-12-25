"""
API Clients for Data Collection
Fetches data from all external APIs
"""

from typing import Dict, List, Optional  # ← ADD THIS LINE
import requests
import logging
from datetime import datetime, timedelta
import time
logger = logging.getLogger(__name__)


class BaseAPIClient:
    """Base class for API clients"""
    
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url
        self.session = requests.Session()
    
    def _make_request(self, endpoint: str, params: Dict = None, headers: Dict = None) -> Dict:
        """Make API request with error handling"""
        url = f"{self.base_url}/{endpoint}"
        
        default_headers = {'Accept': 'application/json'}
        if headers:
            default_headers.update(headers)
        
        try:
            response = self.session.get(url, params=params, headers=default_headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            return {}


class TheOddsAPIClient(BaseAPIClient):
    """
    TheOdds API Client
    Fetches game lines, totals, spreads, implied totals
    """
    
    def __init__(self, api_key: str):
        super().__init__(api_key, 'https://api.the-odds-api.com/v4')
    
    def get_nba_odds(self, date: Optional[str] = None) -> List[Dict]:
        """
        Get NBA game odds
        
        Returns:
            List of games with odds data
        """
        params = {
            'apiKey': self.api_key,
            'sport': 'basketball_nba',
            'regions': 'us',
            'markets': 'h2h,spreads,totals',
            'oddsFormat': 'american'
        }
        
        if date:
            params['commenceTimeFrom'] = f"{date}T00:00:00Z"
            params['commenceTimeTo'] = f"{date}T23:59:59Z"
        
        data = self._make_request('sports/basketball_nba/odds', params)
        
        if not data:
            return []
        
        # Parse odds data
        games = []
        for game in data:
            game_info = {
                'game_id': game.get('id'),
                'commence_time': game.get('commence_time'),
                'home_team': game.get('home_team'),
                'away_team': game.get('away_team'),
                'odds': self._parse_bookmaker_odds(game.get('bookmakers', []))
            }
            games.append(game_info)
        
        logger.info(f"Fetched odds for {len(games)} NBA games")
        return games
    
    def _parse_bookmaker_odds(self, bookmakers: List[Dict]) -> Dict:
        """Parse bookmaker odds to get consensus lines"""
        if not bookmakers:
            return {}
        
        # Use first bookmaker (or average across multiple)
        book = bookmakers[0]
        
        odds_data = {
            'spread': None,
            'total': None,
            'moneyline_home': None,
            'moneyline_away': None
        }
        
        for market in book.get('markets', []):
            market_key = market.get('key')
            outcomes = market.get('outcomes', [])
            
            if market_key == 'spreads':
                for outcome in outcomes:
                    if outcome.get('name') == book.get('home_team'):
                        odds_data['spread'] = outcome.get('point')
            
            elif market_key == 'totals':
                if outcomes:
                    odds_data['total'] = outcomes[0].get('point')
            
            elif market_key == 'h2h':
                for outcome in outcomes:
                    if outcome.get('name') == book.get('home_team'):
                        odds_data['moneyline_home'] = outcome.get('price')
                    else:
                        odds_data['moneyline_away'] = outcome.get('price')
        
        return odds_data


class SportsDataAPIClient(BaseAPIClient):
    """
    SportsData.io API Client
    Fetches player stats, injuries, starting lineups
    """
    
    def __init__(self, api_key: str):
        super().__init__(api_key, 'https://api.sportsdata.io/v3/nba')
    
    def get_player_stats_season(self, season: str = '2025') -> List[Dict]:
        """Get season stats for all players"""
        headers = {'Ocp-Apim-Subscription-Key': self.api_key}
        
        data = self._make_request(f'stats/json/PlayerSeasonStats/{season}', headers=headers)
        
        logger.info(f"Fetched stats for {len(data)} players")
        return data if isinstance(data, list) else []
    
    def get_player_game_stats(self, date: str) -> List[Dict]:
        """
        Get player stats for a specific date
        
        Args:
            date: Format YYYY-MM-DD
        """
        headers = {'Ocp-Apim-Subscription-Key': self.api_key}
        
        data = self._make_request(f'stats/json/PlayerGameStatsByDate/{date}', headers=headers)
        
        return data if isinstance(data, list) else []
    
    def get_injuries(self) -> List[Dict]:
        """Get current injury report"""
        headers = {'Ocp-Apim-Subscription-Key': self.api_key}
        
        data = self._make_request('scores/json/InjuredPlayers', headers=headers)
        
        logger.info(f"Fetched {len(data)} injury reports")
        return data if isinstance(data, list) else []
    
    def get_starting_lineups(self, date: str) -> List[Dict]:
        """Get starting lineups for games on date"""
        headers = {'Ocp-Apim-Subscription-Key': self.api_key}
        
        # First get games for the date
        games = self._make_request(f'scores/json/GamesByDate/{date}', headers=headers)
        
        lineups = []
        for game in games if isinstance(games, list) else []:
            game_id = game.get('GameID')
            if game_id:
                lineup = self._make_request(f'stats/json/LineupsByGameID/{game_id}', headers=headers)
                if lineup:
                    lineups.append(lineup)
        
        return lineups


class BallDontLieAPIClient(BaseAPIClient):
    """
    BallDontLie API Client
    Free NBA stats API
    """
    
    def __init__(self):
        super().__init__('', 'https://www.balldontlie.io/api/v1')
    
    def get_player_season_averages(self, player_ids: List[int], season: int = 2024) -> List[Dict]:
        """Get season averages for players"""
        params = {
            'season': season,
            'player_ids[]': player_ids
        }
        
        data = self._make_request('season_averages', params)
        
        return data.get('data', []) if isinstance(data, dict) else []
    
    def get_players(self, search: str = None) -> List[Dict]:
        """Search for players"""
        params = {}
        if search:
            params['search'] = search
        
        data = self._make_request('players', params)
        
        return data.get('data', []) if isinstance(data, dict) else []
    
    def get_recent_games(self, player_id: int, games: int = 10) -> List[Dict]:
        """Get recent game stats for a player"""
        params = {
            'player_ids[]': [player_id],
            'per_page': games
        }
        
        data = self._make_request('stats', params)
        
        return data.get('data', []) if isinstance(data, dict) else []


class WeatherAPIClient(BaseAPIClient):
    """
    Weather API Client
    For NFL game weather conditions
    """
    
    def __init__(self, api_key: str):
        super().__init__(api_key, 'https://api.weatherapi.com/v1')
    
    def get_forecast(self, location: str, date: str) -> Dict:
        """
        Get weather forecast for game location
        
        Args:
            location: City name or zip code
            date: YYYY-MM-DD
        """
        params = {
            'key': self.api_key,
            'q': location,
            'dt': date
        }
        
        data = self._make_request('forecast.json', params)
        
        if data and 'forecast' in data:
            day = data['forecast']['forecastday'][0]
            return {
                'temp_f': day['day']['avgtemp_f'],
                'wind_mph': day['day']['maxwind_mph'],
                'precip_in': day['day']['totalprecip_in'],
                'condition': day['day']['condition']['text'],
                'humidity': day['day']['avghumidity']
            }
        
        return {}


class RapidAPIClient(BaseAPIClient):
    """
    RapidAPI Client
    Access to multiple sports data endpoints
    """
    
    def __init__(self, api_key: str, host: str):
        super().__init__(api_key, f'https://{host}')
        self.host = host
    
    def _make_request(self, endpoint: str, params: Dict = None, headers: Dict = None) -> Dict:
        """Override to add RapidAPI headers"""
        rapid_headers = {
            'X-RapidAPI-Key': self.api_key,
            'X-RapidAPI-Host': self.host
        }
        if headers:
            rapid_headers.update(headers)
        
        return super()._make_request(endpoint, params, rapid_headers)