"""
NBA DFS Analytics
Advanced analytics for performance tracking and optimization
"""

from typing import Dict, List, Tuple, Optional  # ← ADD THIS LINE
import pandas as pd
import numpy as np
from collections import defaultdict
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class ContestAnalytics:
    """Analytics for contest performance tracking"""
    
    def __init__(self, contests_data: pd.DataFrame):
        """
        Initialize with contest results data
        
        Args:
            contests_data: DataFrame with columns:
                - contest_id, entry_fee, final_rank, payout, total_entries,
                  projected_points, actual_points, contest_type
        """
        self.data = contests_data
    
    def calculate_roi(self, period_days: Optional[int] = None) -> Dict:
        """
        Calculate ROI metrics
        
        Args:
            period_days: Limit to last N days (None = all time)
        
        Returns:
            Dict with ROI metrics
        """
        df = self.data
        
        if period_days:
            cutoff = datetime.now() - timedelta(days=period_days)
            df = df[df['date'] >= cutoff]
        
        total_invested = df['entry_fee'].sum()
        total_won = df['payout'].sum()
        profit = total_won - total_invested
        roi_pct = (profit / total_invested * 100) if total_invested > 0 else 0
        
        # ROI by contest type
        roi_by_type = {}
        for contest_type in df['contest_type'].unique():
            type_df = df[df['contest_type'] == contest_type]
            invested = type_df['entry_fee'].sum()
            won = type_df['payout'].sum()
            type_roi = ((won - invested) / invested * 100) if invested > 0 else 0
            roi_by_type[contest_type] = {
                'invested': float(invested),
                'won': float(won),
                'roi': float(type_roi),
                'count': len(type_df)
            }
        
        return {
            'total_invested': float(total_invested),
            'total_won': float(total_won),
            'profit': float(profit),
            'roi_percentage': float(roi_pct),
            'total_contests': len(df),
            'roi_by_type': roi_by_type,
            'period_days': period_days or 'all_time'
        }
    
    def calculate_win_rate(self) -> Dict:
        """
        Calculate various win rate metrics
        
        Returns:
            Dict with win rate stats
        """
        df = self.data
        
        total_entries = len(df)
        cashed = len(df[df['payout'] > 0])          # received any payout
        profitable = len(df[df['payout'] > df['entry_fee']])  # net profit
        break_even = len(df[df['payout'] == df['entry_fee']])
        
        # Calculate percentiles
        top_1_pct = len(df[df['percentile'] <= 1.0])
        top_5_pct = len(df[df['percentile'] <= 5.0])
        top_10_pct = len(df[df['percentile'] <= 10.0])
        top_25_pct = len(df[df['percentile'] <= 25.0])
        
        return {
            'total_entries': total_entries,
            'cashed_count': cashed,
            'cash_rate': (cashed / total_entries * 100) if total_entries > 0 else 0,
            'profitable_count': profitable,
            'profit_rate': (profitable / total_entries * 100) if total_entries > 0 else 0,
            'top_1_pct_count': top_1_pct,
            'top_1_pct_rate': (top_1_pct / total_entries * 100) if total_entries > 0 else 0,
            'top_5_pct_count': top_5_pct,
            'top_5_pct_rate': (top_5_pct / total_entries * 100) if total_entries > 0 else 0,
            'top_10_pct_count': top_10_pct,
            'top_10_pct_rate': (top_10_pct / total_entries * 100) if total_entries > 0 else 0,
            'top_25_pct_count': top_25_pct,
            'top_25_pct_rate': (top_25_pct / total_entries * 100) if total_entries > 0 else 0,
        }
    
    def projection_accuracy(self) -> Dict:
        """
        Analyze projection accuracy
        
        Returns:
            Dict with accuracy metrics
        """
        df = self.data.dropna(subset=['projected_points', 'actual_points'])
        
        if len(df) == 0:
            return {'error': 'No data with both projections and actuals'}
        
        df['diff'] = df['actual_points'] - df['projected_points']
        df['abs_diff'] = abs(df['diff'])
        df['pct_error'] = (df['abs_diff'] / df['projected_points'] * 100)
        
        return {
            'mean_error': float(df['diff'].mean()),
            'mean_absolute_error': float(df['abs_diff'].mean()),
            'mean_percentage_error': float(df['pct_error'].mean()),
            'std_error': float(df['diff'].std()),
            'within_5_points': len(df[df['abs_diff'] <= 5]) / len(df) * 100,
            'within_10_points': len(df[df['abs_diff'] <= 10]) / len(df) * 100,
            'over_projected': len(df[df['diff'] > 0]) / len(df) * 100,
            'under_projected': len(df[df['diff'] < 0]) / len(df) * 100,
        }


class OwnershipPredictor:
    """Predict player ownership percentages"""
    
    def __init__(self, historical_data: pd.DataFrame):
        """
        Initialize with historical player data
        
        Args:
            historical_data: DataFrame with columns:
                - salary, proj_points, value, team, position, actual_ownership
        """
        self.data = historical_data
        self.model_weights = self._train_simple_model()
    
    def _train_simple_model(self) -> Dict:
        """Train a simple weighted model for ownership prediction"""
        df = self.data.dropna(subset=['actual_ownership'])
        
        if len(df) == 0:
            return {}
        
        # Calculate correlations with ownership
        correlations = {}
        for col in ['salary', 'proj_points', 'value']:
            if col in df.columns:
                corr = df[col].corr(df['actual_ownership'])
                correlations[col] = abs(corr) if not np.isnan(corr) else 0
        
        # Normalize to weights
        total = sum(correlations.values())
        weights = {k: v/total for k, v in correlations.items()} if total > 0 else {}
        
        logger.info(f"Ownership model weights: {weights}")
        return weights
    
    def predict_ownership(self, players_df: pd.DataFrame) -> pd.DataFrame:
        """
        Predict ownership for players
        
        Args:
            players_df: DataFrame with player projections
        
        Returns:
            DataFrame with predicted ownership column added
        """
        df = players_df.copy()
        
        if not self.model_weights:
            # Fallback: simple value-based prediction
            if 'value' in df.columns:
                df['predicted_ownership'] = df['value'] / df['value'].max() * 50
            else:
                df['predicted_ownership'] = 10.0
            return df
        
        # Weighted prediction
        df['ownership_score'] = 0
        for feature, weight in self.model_weights.items():
            if feature in df.columns:
                normalized = df[feature] / df[feature].max()
                df['ownership_score'] += normalized * weight
        
        # Scale to 0-100% range with realistic distribution
        max_score = df['ownership_score'].max()
        if max_score > 0:
            df['predicted_ownership'] = (df['ownership_score'] / max_score) * 40 + 5  # 5-45% range
        else:
            df['predicted_ownership'] = 10.0
        
        return df


class CorrelationAnalyzer:
    """Analyze player correlations"""
    
    def __init__(self, performance_data: pd.DataFrame):
        """
        Initialize with player performance data
        
        Args:
            performance_data: DataFrame with columns:
                - slate_id, player_id, actual_points
        """
        self.data = performance_data
    
    def calculate_correlation_matrix(self, min_games: int = 5) -> pd.DataFrame:
        """
        Calculate correlation matrix for players
        
        Args:
            min_games: Minimum games played together to calculate correlation
        
        Returns:
            Correlation matrix DataFrame
        """
        # Pivot data: rows = slates, columns = players, values = points
        pivot = self.data.pivot(
            index='slate_id',
            columns='player_id',
            values='actual_points'
        )
        
        # Only keep players with min_games
        pivot = pivot.dropna(thresh=min_games, axis=1)
        
        # Calculate correlation
        corr_matrix = pivot.corr()
        
        return corr_matrix
    
    def find_correlated_pairs(
        self,
        threshold: float = 0.5,
        min_games: int = 5
    ) -> List[Dict]:
        """
        Find highly correlated player pairs
        
        Args:
            threshold: Minimum correlation coefficient
            min_games: Minimum games together
        
        Returns:
            List of correlated pairs
        """
        corr_matrix = self.calculate_correlation_matrix(min_games)
        
        pairs = []
        for i in range(len(corr_matrix.columns)):
            for j in range(i+1, len(corr_matrix.columns)):
                player1 = corr_matrix.columns[i]
                player2 = corr_matrix.columns[j]
                corr = corr_matrix.iloc[i, j]
                
                if abs(corr) >= threshold:
                    pairs.append({
                        'player1': player1,
                        'player2': player2,
                        'correlation': float(corr),
                        'type': 'positive' if corr > 0 else 'negative'
                    })
        
        # Sort by absolute correlation
        pairs.sort(key=lambda x: abs(x['correlation']), reverse=True)
        
        return pairs


class LineupSelector:
    """Select optimal lineups from a pool"""
    
    def __init__(self, lineups_df: pd.DataFrame):
        """
        Initialize with lineup data
        
        Args:
            lineups_df: DataFrame with lineup metrics
        """
        self.lineups = lineups_df
    
    def select_optimal(
        self,
        n_lineups: int = 20,
        max_correlation: float = 0.8,
        diversification_weight: float = 0.3
    ) -> pd.DataFrame:
        """
        Select optimal subset of lineups
        
        Args:
            n_lineups: Number of lineups to select
            max_correlation: Max correlation between selected lineups
            diversification_weight: Weight for diversification vs projection
        
        Returns:
            DataFrame of selected lineups
        """
        df = self.lineups.copy()
        
        # Score lineups
        df['projection_score'] = df['projected_points'] / df['projected_points'].max()
        df['ownership_score'] = 1 - (df['avg_ownership'] / 100)  # Lower ownership = higher score
        df['total_score'] = (
            df['projection_score'] * (1 - diversification_weight) +
            df['ownership_score'] * diversification_weight
        )
        
        # Start with highest scoring lineup
        selected = [df.nlargest(1, 'total_score').index[0]]
        available = df.index.tolist()
        available.remove(selected[0])
        
        # Greedy selection with correlation check
        while len(selected) < min(n_lineups, len(df)):
            best_score = -1
            best_idx = None
            
            for idx in available:
                # Check correlation with already selected
                too_similar = False
                for sel_idx in selected:
                    # Simple correlation: count shared players
                    shared = self._count_shared_players(df.loc[idx], df.loc[sel_idx])
                    if shared >= 7:  # More than 7 shared players = too correlated
                        too_similar = True
                        break
                
                if not too_similar and df.loc[idx, 'total_score'] > best_score:
                    best_score = df.loc[idx, 'total_score']
                    best_idx = idx
            
            if best_idx is None:
                break  # Can't find more uncorrelated lineups
            
            selected.append(best_idx)
            available.remove(best_idx)
        
        return df.loc[selected].sort_values('total_score', ascending=False)
    
    def _count_shared_players(self, lineup1, lineup2) -> int:
        """Count shared players between two lineups"""
        # This is a simplified version - adjust based on your lineup data structure
        shared = 0
        player_cols = [col for col in lineup1.index if col.startswith('player_')]
        
        for col in player_cols:
            if lineup1[col] == lineup2[col]:
                shared += 1
        
        return shared


def generate_analytics_report(
    contests_df: pd.DataFrame,
    period: str = '30d'
) -> Dict:
    """
    Generate comprehensive analytics report
    
    Args:
        contests_df: DataFrame with contest results
        period: Time period ('7d', '30d', '90d', 'all')
    
    Returns:
        Dict with complete analytics
    """
    period_days = {
        '7d': 7,
        '30d': 30,
        '90d': 90,
        'all': None
    }.get(period, 30)
    
    analytics = ContestAnalytics(contests_df)
    
    report = {
        'period': period,
        'generated_at': datetime.now().isoformat(),
        'roi': analytics.calculate_roi(period_days),
        'win_rate': analytics.calculate_win_rate(),
        'projection_accuracy': analytics.projection_accuracy(),
    }
    
    return report

class AdvancedLineupSelector:
    """
    Advanced lineup selection with multiple optimization strategies
    """
    
    def __init__(self, lineups_df: pd.DataFrame):
        self.lineups = lineups_df.copy()
        self._prepare_data()
    
    def _prepare_data(self):
        """Prepare lineup data for optimization"""
        # Ensure required columns
        required = ['projected_points', 'avg_ownership']
        for col in required:
            if col not in self.lineups.columns:
                logger.warning(f"Missing column: {col}")
        
        # Add lineup ID if missing
        if 'lineup_id' not in self.lineups.columns:
            self.lineups['lineup_id'] = range(len(self.lineups))
    
    def select_by_ceiling(self, n_lineups: int = 20, variance_weight: float = 0.3) -> pd.DataFrame:
        """
        Select lineups optimized for tournament ceiling
        
        Args:
            n_lineups: Number of lineups to select
            variance_weight: Weight for variance/upside (0-1)
        
        Returns:
            Selected lineups DataFrame
        """
        df = self.lineups.copy()
        
        # Calculate ceiling score
        df['ceiling_score'] = (
            df['projected_points'] * (1 - variance_weight) +
            df.get('projection_std', 0) * variance_weight * 10
        )
        
        # Select top by ceiling
        selected = df.nlargest(n_lineups, 'ceiling_score')
        
        return selected
    
    def select_by_cash_safety(self, n_lineups: int = 20) -> pd.DataFrame:
        """
        Select lineups optimized for cash games (safe, high floor)
        
        Args:
            n_lineups: Number of lineups to select
        
        Returns:
            Selected lineups DataFrame
        """
        df = self.lineups.copy()
        
        # Calculate safety score (high projection, low variance, higher ownership OK)
        df['safety_score'] = df['projected_points'].copy()
        
        # Penalize if we have variance data
        if 'projection_std' in df.columns:
            df['safety_score'] -= df['projection_std'] * 2
        
        selected = df.nlargest(n_lineups, 'safety_score')
        
        return selected
    
    def select_diversified_portfolio(
        self,
        n_lineups: int = 150,
        max_shared_players: int = 7
    ) -> pd.DataFrame:
        """
        Select diversified portfolio with controlled correlation
        
        Args:
            n_lineups: Target number of lineups
            max_shared_players: Max shared players between lineups
        
        Returns:
            Selected lineups DataFrame
        """
        df = self.lineups.copy()
        
        # Start with best lineup
        selected_indices = [df['projected_points'].idxmax()]
        available = set(df.index) - set(selected_indices)
        
        while len(selected_indices) < min(n_lineups, len(df)):
            best_candidate = None
            best_score = -1
            
            for idx in available:
                # Check correlation with existing selections
                too_similar = False
                
                for sel_idx in selected_indices:
                    shared = self._count_shared_players(df.loc[idx], df.loc[sel_idx])
                    if shared > max_shared_players:
                        too_similar = True
                        break
                
                if not too_similar:
                    score = df.loc[idx, 'projected_points']
                    if score > best_score:
                        best_score = score
                        best_candidate = idx
            
            if best_candidate is None:
                logger.warning(f"Could only select {len(selected_indices)} lineups with diversity constraints")
                break
            
            selected_indices.append(best_candidate)
            available.remove(best_candidate)
        
        return df.loc[selected_indices]
    
    def _count_shared_players(self, lineup1, lineup2) -> int:
        """Count shared players between lineups"""
        shared = 0
        
        # Try to find player columns
        player_cols = [col for col in lineup1.index if 'player' in col.lower() or 'pg' in col or 'sg' in col or 'sf' in col or 'pf' in col or 'c' == col.lower()]
        
        if not player_cols:
            # Fallback: assume all non-numeric columns are players
            player_cols = lineup1.index[lineup1.apply(lambda x: isinstance(x, str))]
        
        for col in player_cols:
            if lineup1[col] == lineup2[col]:
                shared += 1
        
        return shared
    
    def optimize_for_strategy(
        self,
        strategy: str,
        n_lineups: int = 20,
        **kwargs
    ) -> pd.DataFrame:
        """
        Optimize lineup selection for a specific strategy
        
        Args:
            strategy: 'gpp', 'cash', 'balanced', 'contrarian', 'ceiling'
            n_lineups: Number to select
            **kwargs: Additional strategy-specific parameters
        
        Returns:
            Selected lineups DataFrame
        """
        if strategy == 'gpp' or strategy == 'ceiling':
            return self.select_by_ceiling(n_lineups, kwargs.get('variance_weight', 0.3))
        
        elif strategy == 'cash':
            return self.select_by_cash_safety(n_lineups)
        
        elif strategy == 'contrarian':
            df = self.lineups.copy()
            df['contrarian_score'] = df['projected_points'] * (100 - df['avg_ownership']) / 100
            return df.nlargest(n_lineups, 'contrarian_score')
        
        elif strategy == 'balanced':
            return self.select_diversified_portfolio(n_lineups, kwargs.get('max_shared', 7))
        
        else:
            logger.warning(f"Unknown strategy: {strategy}, using balanced")
            return self.select_diversified_portfolio(n_lineups)