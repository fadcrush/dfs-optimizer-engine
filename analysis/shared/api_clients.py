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

    def get_nba_events(self, date: Optional[str] = None) -> List[Dict]:
        """
        Fetch NBA event IDs for a given date (needed to query player props).

        Returns:
            List of event dicts: {event_id, home_team, away_team, commence_time}
        """
        params = {
            'apiKey': self.api_key,
            'sport': 'basketball_nba',
            'dateFormat': 'iso',
        }
        if date:
            params['commenceTimeFrom'] = f"{date}T00:00:00Z"
            params['commenceTimeTo'] = f"{date}T23:59:59Z"

        data = self._make_request('sports/basketball_nba/events', params)
        if not isinstance(data, list):
            return []

        events = [
            {
                'event_id': e.get('id'),
                'home_team': e.get('home_team'),
                'away_team': e.get('away_team'),
                'commence_time': e.get('commence_time'),
            }
            for e in data
            if e.get('id')
        ]
        logger.info(f"Fetched {len(events)} NBA events for props lookup")
        return events

    def get_player_props(
        self,
        event_id: str,
        markets: str = "player_points,player_rebounds,player_assists,player_threes",
        regions: str = "us",
        bookmakers: str = "draftkings,fanduel,betmgm,caesars",
    ) -> List[Dict]:
        """
        Fetch player prop lines for a single NBA event.

        Returns a flat list of prop records:
            {player, market, line, over_odds, under_odds, bookmaker, event_id}

        The Odds API endpoint:
            GET /v4/sports/basketball_nba/events/{event_id}/odds
                ?apiKey=...&markets=player_points,...&regions=us&oddsFormat=american
        """
        params = {
            'apiKey': self.api_key,
            'regions': regions,
            'markets': markets,
            'oddsFormat': 'american',
            'bookmakers': bookmakers,
        }
        data = self._make_request(
            f'sports/basketball_nba/events/{event_id}/odds',
            params,
        )
        if not isinstance(data, dict):
            return []

        props: List[Dict] = []
        for book in data.get('bookmakers', []):
            bk = book.get('key', 'unknown')
            for market in book.get('markets', []):
                mkt_key = market.get('key', '')
                for outcome in market.get('outcomes', []):
                    if outcome.get('name') not in ('Over', 'Under'):
                        continue
                    desc = outcome.get('description', '')  # player name for prop markets
                    props.append({
                        'event_id': event_id,
                        'player': desc,
                        'market': mkt_key,
                        'line': outcome.get('point'),
                        'side': outcome.get('name'),   # 'Over' or 'Under'
                        'price': outcome.get('price'),
                        'bookmaker': bk,
                    })

        logger.info(
            f"Fetched {len(props)} prop outcomes for event {event_id} "
            f"across {len(data.get('bookmakers', []))} books"
        )
        return props
    
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


class NBAFreeDataClient:
    """
    Free NBA data client powered entirely by nba_api (stats.nba.com).
    Drop-in replacement for SportsDataAPIClient — no API key, no subscription.

    Endpoints used:
      • LeagueDashPlayerStats  → season averages for all players
      • LeagueGameLog          → per-game box scores by date
      • InjuryReport           → current injury list (best-effort)

    All methods return plain list-of-dict so the rest of the pipeline
    can treat them identically to the old SportsData responses.
    """

    # NBA team abbreviation → full name (for matching Vegas lines)
    _TEAM_ABBR: Dict[str, str] = {
        "ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets",
        "CHA": "Charlotte Hornets", "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers",
        "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets", "DET": "Detroit Pistons",
        "GSW": "Golden State Warriors", "HOU": "Houston Rockets", "IND": "Indiana Pacers",
        "LAC": "LA Clippers", "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies",
        "MIA": "Miami Heat", "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves",
        "NOP": "New Orleans Pelicans", "NYK": "New York Knicks", "OKC": "Oklahoma City Thunder",
        "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers", "PHX": "Phoenix Suns",
        "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings", "SAS": "San Antonio Spurs",
        "TOR": "Toronto Raptors", "UTA": "Utah Jazz", "WAS": "Washington Wizards",
    }

    def __init__(self):
        # Lazy import so the rest of the project works even if nba_api is absent
        import importlib
        self._nba_api_available = importlib.util.find_spec("nba_api") is not None
        if not self._nba_api_available:
            logger.warning("nba_api not installed — NBAFreeDataClient will return empty results")

    # ------------------------------------------------------------------
    # Public API (mirrors SportsDataAPIClient interface)
    # ------------------------------------------------------------------

    def get_player_stats_season(self, season: str = "2025") -> List[Dict]:
        """
        Season averages for every active NBA player.

        Normalises column names to the legacy SportsData schema:
          Player, PlayerID, Team, MP, PTS, REB, AST, STL, BLK,
          FGM, FGA, FG_PCT, FG3M, FG3A, FG3_PCT, FTM, FTA, FT_PCT,
          TOV, GP, PLUS_MINUS
        """
        if not self._nba_api_available:
            return []
        try:
            from nba_api.stats.endpoints import leaguedashplayerstats
            # nba_api uses "2024-25" style season IDs
            season_id = self._season_id(int(season))
            ls = leaguedashplayerstats.LeagueDashPlayerStats(
                season=season_id,
                per_mode_simple="PerGame",
                timeout=60,
            )
            df = ls.get_data_frames()[0]
            # Rename to legacy schema
            rename = {
                "PLAYER_NAME": "Player",
                "PLAYER_ID": "PlayerID",
                "TEAM_ABBREVIATION": "Team",
                "MIN": "MP",
                "PTS": "PTS",
                "REB": "REB",
                "AST": "AST",
                "STL": "STL",
                "BLK": "BLK",
                "FGM": "FGM",
                "FGA": "FGA",
                "FG_PCT": "FG_PCT",
                "FG3M": "FG3M",
                "FG3A": "FG3A",
                "FG3_PCT": "FG3_PCT",
                "FTM": "FTM",
                "FTA": "FTA",
                "FT_PCT": "FT_PCT",
                "TOV": "TOV",
                "GP": "GP",
                "PLUS_MINUS": "PLUS_MINUS",
                "NBA_FANTASY_PTS": "FantasyPoints",
            }
            df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
            logger.info(f"nba_api: fetched season stats for {len(df)} players (season {season_id})")
            return df.to_dict("records")
        except Exception as e:
            logger.warning(f"get_player_stats_season failed: {e}")
            return []

    def get_player_game_stats(self, date: str) -> List[Dict]:
        """
        Box-score stats for every player who played on *date* (YYYY-MM-DD).

        Returns rows with: Player, PlayerID, Team, GameDate, MP, PTS, REB,
        AST, STL, BLK, FGM, FGA, FG3M, FTM, FTA, TOV, PLUS_MINUS
        """
        if not self._nba_api_available:
            return []
        try:
            from nba_api.stats.endpoints import leaguegamelog
            gl = leaguegamelog.LeagueGameLog(
                season=self._season_id_from_date(date),
                date_from_nullable=date,
                date_to_nullable=date,
                player_or_team_abbreviation="P",
                timeout=60,
            )
            df = gl.get_data_frames()[0]
            rename = {
                "PLAYER_NAME": "Player",
                "PLAYER_ID": "PlayerID",
                "TEAM_ABBREVIATION": "Team",
                "GAME_DATE": "GameDate",
                "MIN": "MP",
                "PTS": "PTS",
                "REB": "REB",
                "AST": "AST",
                "STL": "STL",
                "BLK": "BLK",
                "FGM": "FGM",
                "FGA": "FGA",
                "FG3M": "FG3M",
                "FTM": "FTM",
                "FTA": "FTA",
                "TOV": "TOV",
                "PLUS_MINUS": "PLUS_MINUS",
            }
            df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
            return df.to_dict("records")
        except Exception as e:
            logger.warning(f"get_player_game_stats({date}) failed: {e}")
            return []

    def get_injuries(self) -> List[Dict]:
        """
        Current NBA injury report.  Returns list of dicts with keys:
          Player, Team, Status, Description.

        Uses nba_api InjuryReport if available; falls back to the NBA
        CDN JSON feed (no auth required).
        """
        if not self._nba_api_available:
            return []
        try:
            from nba_api.stats.endpoints import injuryreport
            rpt = injuryreport.InjuryReport(timeout=30)
            df = rpt.get_data_frames()[0]
            # normalise
            rename = {
                "PlayerName": "Player",
                "Team": "Team",
                "Status": "Status",
                "Reason": "Description",
            }
            df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
            result = df.to_dict("records")
            logger.info(f"nba_api: fetched {len(result)} injury rows")
            return result
        except Exception:
            pass
        # Fallback: NBA CDN league injury report (public, no key)
        try:
            import requests as _req
            url = "https://stats.nba.com/js/data/leaders/00_injury_report.json"
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.nba.com/",
            }
            resp = _req.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            raw = resp.json()
            injuries = []
            for row in raw.get("injury", []):
                injuries.append({
                    "Player": row.get("PLAYER_FIRST_NAME", "") + " " + row.get("PLAYER_LAST_NAME", ""),
                    "Team": row.get("TEAM_ABBREVIATION", ""),
                    "Status": row.get("INJURY_STATUS", ""),
                    "Description": row.get("INJURY_DESCRIPTION", ""),
                })
            logger.info(f"nba_api CDN: fetched {len(injuries)} injury rows")
            return injuries
        except Exception as e:
            logger.warning(f"get_injuries fallback failed: {e}")
            return []

    def get_starting_lineups(self, date: str) -> List[Dict]:
        """
        Pre-game starting lineups.

        nba_api does not expose confirmed starters before tip-off.
        Returns empty list — the projection engine treats this as
        'lineups unknown' and uses usage-rate adjustments instead.
        (Wire up a Rotowire/RotoGrinders scraper here later if needed.)
        """
        logger.debug("get_starting_lineups: pre-game lineups not available from nba_api; returning []")
        return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _season_id(year: int) -> str:
        """Convert 4-digit year to nba_api season string, e.g. 2025 → '2024-25'."""
        return f"{year - 1}-{str(year)[2:]}"

    @staticmethod
    def _season_id_from_date(date: str) -> str:
        """Derive season from a game date string (YYYY-MM-DD)."""
        dt = datetime.strptime(date, "%Y-%m-%d")
        # NBA season starts in October; dates Jan-Sep belong to the current year's season
        year = dt.year if dt.month >= 10 else dt.year
        return NBAFreeDataClient._season_id(year)


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