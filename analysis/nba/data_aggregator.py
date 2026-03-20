"""
NBA Data Aggregator
Fetches and combines data from all sources
"""

import pandas as pd
from datetime import datetime, timedelta
import logging
import os
from typing import Optional, Dict, List

from analysis.shared.api_clients import (
    TheOddsAPIClient,
    NBAFreeDataClient,
)
from analysis.core.projection_engine import CanonicalNBAProjectionEngine

logger = logging.getLogger(__name__)


class NBADataAggregator:
    """
    Aggregates data from multiple APIs and creates projections
    """
    
    def __init__(self):
        # Initialize API clients
        self.odds_client = TheOddsAPIClient(os.getenv('THEODDS_API_KEY', ''))
        self.nba_client = NBAFreeDataClient()  # free, no API key — uses nba_api / stats.nba.com
        
        # Initialize projection engine
        self.projection_engine = CanonicalNBAProjectionEngine()
    
    def generate_daily_projections(self, target_date: Optional[str] = None) -> pd.DataFrame:
        """
        Generate complete projections for a date
        
        Args:
            target_date: YYYY-MM-DD format (defaults to today)
        
        Returns:
            DataFrame with complete projections
        """
        if not target_date:
            target_date = datetime.now().strftime('%Y-%m-%d')
        
        logger.info(f"Generating projections for {target_date}")
        
        # Step 1: Get vegas lines and game info
        logger.info("Fetching vegas lines...")
        vegas_data = self.odds_client.get_nba_odds(target_date)
        vegas_df = self._parse_vegas_data(vegas_data)
        
        # Step 2: Get player season stats
        logger.info("Fetching season stats...")
        season = datetime.now().year
        season_stats = self.nba_client.get_player_stats_season(str(season))
        season_df = pd.DataFrame(season_stats)
        
        # Step 3: Get recent game stats (last 15 days)
        logger.info("Fetching recent games...")
        recent_games = []
        for i in range(15):
            past_date = (datetime.strptime(target_date, '%Y-%m-%d') - timedelta(days=i+1)).strftime('%Y-%m-%d')
            games = self.nba_client.get_player_game_stats(past_date)
            recent_games.extend(games)
        
        recent_df = pd.DataFrame(recent_games)
        
        # Step 4: Get injuries
        logger.info("Fetching injury report...")
        injuries = self.nba_client.get_injuries()
        injuries_df = pd.DataFrame(injuries)
        
        # Step 5: Get starting lineups (if available)
        logger.info("Fetching starting lineups...")
        lineups = self.nba_client.get_starting_lineups(target_date)
        
        # Step 6: Create game info DataFrame
        game_info_df = self._create_game_info(vegas_df, season_df)
        
        # Step 7: Generate projections
        logger.info("Generating projections...")
        projections = self.projection_engine.create_projections(
            season_stats=season_df,
            recent_games=recent_df,
            game_info=game_info_df,
            injuries=injuries_df,
            vegas_totals=vegas_df
        )
        
        # Step 8: Add metadata
        projections['date'] = target_date
        projections['generated_at'] = datetime.now()
        
        logger.info(f"✓ Generated {len(projections)} projections")
        
        return projections
    
    def _parse_vegas_data(self, vegas_data: List[Dict]) -> pd.DataFrame:
        """Parse vegas odds into usable DataFrame"""
        games = []
        
        for game in vegas_data:
            home_team = game['home_team']
            away_team = game['away_team']
            odds = game['odds']
            
            total = odds.get('total', 225)  # Default to league average
            
            # Calculate implied totals from spread
            spread = odds.get('spread', 0)
            home_implied = (total / 2) - (spread / 2)
            away_implied = (total / 2) + (spread / 2)
            
            games.append({
                'team': home_team,
                'opponent': away_team,
                'game_total': total,
                'implied_total': home_implied,
                'spread': spread,
                'is_home': True
            })
            
            games.append({
                'team': away_team,
                'opponent': home_team,
                'game_total': total,
                'implied_total': away_implied,
                'spread': -spread,
                'is_home': False
            })
        
        return pd.DataFrame(games)
    
    def _create_game_info(
        self,
        vegas_df: pd.DataFrame,
        season_df: pd.DataFrame
    ) -> pd.DataFrame:
        """Create comprehensive game info DataFrame"""
        
        # Calculate team averages for pace and defensive rating
        team_stats = season_df.groupby('Team').agg({
            'MP': 'sum',  # Total minutes
            'PTS': 'sum',  # Total points
        }).reset_index()
        
        # Estimate pace (possessions per game)
        team_stats['pace'] = (team_stats['PTS'] / 82) * 100 / 110  # Simplified
        team_stats['avg_pace'] = team_stats['pace'].mean()
        
        # Merge with vegas data
        game_info = vegas_df.merge(
            team_stats[['Team', 'pace', 'avg_pace']],
            left_on='team',
            right_on='Team',
            how='left'
        )
        
        # Add game pace (average of both teams)
        game_info['game_pace'] = game_info['pace']  # Simplified
        
        # Add defensive rating: use opponent's Vegas implied total as a per-game
        # proxy for points-allowed.  Falls back to the historic league average (112)
        # when we have no Vegas data for a matchup.
        implied_map = dict(zip(vegas_df['team'], vegas_df['implied_total']))
        game_info['opp_def_rating'] = (
            game_info['opponent'].map(implied_map).fillna(112.0)
        )
        
        return game_info
    
    def export_projections(
        self,
        projections: pd.DataFrame,
        output_path: str,
        site: str = 'FD'
    ):
        """
        Export projections in DFS upload format
        
        Args:
            projections: Projections DataFrame
            output_path: Where to save CSV
            site: 'FD' or 'DK' for proper formatting
        """
        export_df = projections[[
            'player_name',
            'team',
            'position',
            'opponent',
            'salary',
            'base_projection',
            'ceiling',
            'floor',
            'value',
            'minutes_proj'
        ]].copy()
        
        # Rename columns for clarity
        export_df.columns = [
            'Name',
            'Team',
            'Pos',
            'Opp',
            'Salary',
            'My Proj',
            'Ceiling',
            'Floor',
            'Value',
            'Min Proj'
        ]
        
        # Add ownership estimate (placeholder - would use ownership model)
        export_df['Proj Own'] = (export_df['Value'] / export_df['Value'].max() * 40).clip(1, 50)
        
        # Sort by value
        export_df = export_df.sort_values('Value', ascending=False)
        
        # Export
        export_df.to_csv(output_path, index=False)
        logger.info(f"Exported projections to {output_path}")