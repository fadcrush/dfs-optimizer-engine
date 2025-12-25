"""
NBA Projection Engine
Combines data from multiple sources to create projections
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class NBAProjectionEngine:
    """
    Main projection engine for NBA DFS
    Combines multiple data sources to create player projections
    """
    
    def __init__(self):
        self.weights = {
            'season_avg': 0.30,
            'last_10_games': 0.25,
            'last_3_games': 0.20,
            'pace_adjustment': 0.10,
            'matchup': 0.10,
            'home_away': 0.05
        }
    
    def create_projections(
        self,
        season_stats: pd.DataFrame,
        recent_games: pd.DataFrame,
        game_info: pd.DataFrame,
        injuries: pd.DataFrame,
        vegas_totals: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Create projections by combining all data sources
        
        Args:
            season_stats: Season averages for players
            recent_games: Last 10-15 games for each player
            game_info: Game matchups, pace, etc.
            injuries: Injury report
            vegas_totals: Vegas lines and totals
        
        Returns:
            DataFrame with projections
        """
        logger.info("Creating projections from multiple data sources...")
        
        # Start with base projection from season stats
        projections = self._calculate_base_projection(season_stats)
        
        # Adjust for recent performance
        projections = self._adjust_for_recent_form(projections, recent_games)
        
        # Adjust for pace
        projections = self._adjust_for_pace(projections, game_info)
        
        # Adjust for matchup
        projections = self._adjust_for_matchup(projections, game_info)
        
        # Adjust for injuries (increased usage)
        projections = self._adjust_for_injuries(projections, injuries)
        
        # Adjust for vegas total (game environment)
        projections = self._adjust_for_vegas(projections, vegas_totals)
        
        # Adjust for home/away
        projections = self._adjust_for_location(projections, game_info)
        
        # Add variance/ceiling estimates
        projections = self._calculate_variance(projections)
        
        logger.info(f"Created projections for {len(projections)} players")
        
        return projections
    
    def _calculate_base_projection(self, season_stats: pd.DataFrame) -> pd.DataFrame:
        """Calculate base projection from season stats"""
        df = season_stats.copy()
        
        # DFS points formula (FanDuel scoring)
        df['base_projection'] = (
            df['PTS'] * 1.0 +
            df['TRB'] * 1.2 +
            df['AST'] * 1.5 +
            df['STL'] * 3.0 +
            df['BLK'] * 3.0 +
            df['TOV'] * -1.0
        )
        
        # Minutes projection
        df['minutes_proj'] = df['MP']
        
        return df
    
    def _adjust_for_recent_form(
        self,
        projections: pd.DataFrame,
        recent_games: pd.DataFrame
    ) -> pd.DataFrame:
        """Adjust projections based on recent performance"""
        
        # Calculate last 10 and last 3 game averages
        for player_id in projections['player_id'].unique():
            player_recent = recent_games[recent_games['player_id'] == player_id]
            
            if len(player_recent) >= 3:
                last_3 = player_recent.head(3)
                last_10 = player_recent.head(10)
                
                # Calculate recent fantasy points
                last_3_avg = self._calculate_fpts(last_3).mean()
                last_10_avg = self._calculate_fpts(last_10).mean()
                
                # Blend with base projection
                base = projections.loc[projections['player_id'] == player_id, 'base_projection'].values[0]
                
                adjusted = (
                    base * self.weights['season_avg'] +
                    last_10_avg * self.weights['last_10_games'] +
                    last_3_avg * self.weights['last_3_games']
                )
                
                projections.loc[projections['player_id'] == player_id, 'base_projection'] = adjusted
        
        return projections
    
    def _calculate_fpts(self, games_df: pd.DataFrame) -> pd.Series:
        """Calculate fantasy points for games"""
        return (
            games_df['PTS'] * 1.0 +
            games_df['TRB'] * 1.2 +
            games_df['AST'] * 1.5 +
            games_df['STL'] * 3.0 +
            games_df['BLK'] * 3.0 +
            games_df['TOV'] * -1.0
        )
    
    def _adjust_for_pace(
        self,
        projections: pd.DataFrame,
        game_info: pd.DataFrame
    ) -> pd.DataFrame:
        """Adjust for game pace"""
        
        # Merge game pace info
        projections = projections.merge(
            game_info[['team', 'game_pace', 'avg_pace']],
            on='team',
            how='left'
        )
        
        # Adjust projection based on pace
        projections['pace_multiplier'] = projections['game_pace'] / projections['avg_pace']
        projections['base_projection'] *= projections['pace_multiplier']
        
        return projections
    
    def _adjust_for_matchup(
        self,
        projections: pd.DataFrame,
        game_info: pd.DataFrame
    ) -> pd.DataFrame:
        """Adjust for opponent defensive strength"""
        
        # Merge opponent defensive rating
        projections = projections.merge(
            game_info[['team', 'opponent', 'opp_def_rating']],
            on='team',
            how='left'
        )
        
        # Teams with weak defense (high rating) = easier matchup
        # League average is ~112
        league_avg_def = 112
        projections['matchup_multiplier'] = projections['opp_def_rating'] / league_avg_def
        projections['base_projection'] *= projections['matchup_multiplier']
        
        return projections
    
    def _adjust_for_injuries(
        self,
        projections: pd.DataFrame,
        injuries: pd.DataFrame
    ) -> pd.DataFrame:
        """Adjust for missing teammates (increased usage)"""
        
        for team in projections['team'].unique():
            team_injuries = injuries[injuries['team'] == team]
            
            if len(team_injuries) > 0:
                # Calculate total minutes lost
                minutes_lost = team_injuries['avg_minutes'].sum()
                
                # Distribute among active players proportionally
                team_players = projections[projections['team'] == team]
                
                for idx in team_players.index:
                    player_share = (
                        projections.loc[idx, 'minutes_proj'] / 
                        team_players['minutes_proj'].sum()
                    )
                    
                    bonus_minutes = minutes_lost * player_share
                    
                    # Add bonus based on extra minutes
                    points_per_minute = (
                        projections.loc[idx, 'base_projection'] / 
                        projections.loc[idx, 'minutes_proj']
                    )
                    
                    bonus = bonus_minutes * points_per_minute
                    projections.loc[idx, 'base_projection'] += bonus
        
        return projections
    
    def _adjust_for_vegas(
        self,
        projections: pd.DataFrame,
        vegas_totals: pd.DataFrame
    ) -> pd.DataFrame:
        """Adjust for vegas game total (scoring environment)"""
        
        projections = projections.merge(
            vegas_totals[['team', 'game_total', 'implied_total']],
            on='team',
            how='left'
        )
        
        # Higher totals = more fantasy points
        # Average NBA total is ~225
        avg_total = 225
        projections['vegas_multiplier'] = projections['game_total'] / avg_total
        projections['base_projection'] *= (0.9 + 0.1 * projections['vegas_multiplier'])
        
        return projections
    
    def _adjust_for_location(
        self,
        projections: pd.DataFrame,
        game_info: pd.DataFrame
    ) -> pd.DataFrame:
        """Adjust for home/away"""
        
        projections = projections.merge(
            game_info[['team', 'is_home']],
            on='team',
            how='left'
        )
        
        # Home teams average slightly better
        projections.loc[projections['is_home'] == True, 'base_projection'] *= 1.02
        projections.loc[projections['is_home'] == False, 'base_projection'] *= 0.98
        
        return projections
    
    def _calculate_variance(self, projections: pd.DataFrame) -> pd.DataFrame:
        """Calculate ceiling/floor estimates"""
        
        # Standard deviation based on recent consistency
        # Higher variance = higher ceiling potential
        projections['std_dev'] = projections['base_projection'] * 0.15  # 15% default
        
        projections['ceiling'] = projections['base_projection'] + (projections['std_dev'] * 2)
        projections['floor'] = projections['base_projection'] - (projections['std_dev'] * 1.5)
        projections['floor'] = projections['floor'].clip(lower=0)  # Can't go negative
        
        return projections
    
    def calculate_value(
        self,
        projections: pd.DataFrame,
        salaries: pd.DataFrame
    ) -> pd.DataFrame:
        """Calculate value (points per $1000)"""
        
        projections = projections.merge(
            salaries[['player_id', 'salary']],
            on='player_id',
            how='left'
        )
        
        projections['value'] = (projections['base_projection'] / projections['salary']) * 1000
        
        return projections