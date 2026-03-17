"""
NBA Lineup Optimization Pipeline
Generates optimal lineups based on projections and constraints
"""

import pandas as pd
from pathlib import Path
from typing import List, Dict, Optional, Union
import logging

logger = logging.getLogger(__name__)


def run_nba_pipeline(
    projections_df: pd.DataFrame,
    site: str = "AUTO",
    n_lineups: int = 150,
    num_unique: int = 2,
    max_exposure: float = 0.60,
    leverage_weight: float = 0.25,
    locks: Optional[List[str]] = None,
    fades: Optional[List[str]] = None,
    caps: Optional[Dict[str, float]] = None
) -> Dict:
    """
    Run the NBA lineup optimization pipeline.
    
    Args:
        projections_df: DataFrame with player projections
        site: DFS site ('FD', 'DK', or 'AUTO')
        n_lineups: Number of lineups to generate
        num_unique: Number of unique players required across each lineup
        max_exposure: Maximum exposure per player (0.0-1.0)
        leverage_weight: Weight for contrarian/leverage plays
        locks: List of player IDs that must be in all lineups
        fades: List of player IDs to exclude
        caps: Dict of player_id -> max_exposure
    
    Returns:
        Dictionary with pipeline results
    """
    logger.info(f"Running NBA pipeline: {n_lineups} lineups, site={site}")
    
    # Ensure we have a DataFrame
    if not isinstance(projections_df, pd.DataFrame):
        logger.error(f"Invalid projections_df type: {type(projections_df)}")
        return {
            'site': site,
            'n_lineups': n_lineups,
            'exposure_report': None,
            'portfolio_file': None,
            'success': False,
            'error': f'Invalid projections data type: {type(projections_df)}'
        }
    
    # Detect site if AUTO
    if site == "AUTO":
        site = detect_site(projections_df)
        logger.info(f"Auto-detected site: {site}")
    
    # Initialize result
    result = {
        'site': site,
        'n_lineups': n_lineups,
        'exposure_report': None,
        'portfolio_file': None,
        'success': False,
        'error': None
    }
    
    try:
        # Create exposure report
        exposure_data = create_exposure_report(
            projections_df,  # Pass the DataFrame
            max_exposure,
            locks or [],
            fades or [],
            caps or {}
        )
        
        # Save exposure report
        from django.conf import settings
        export_dir = Path(settings.EXPORT_ROOT) / 'nba'
        export_dir.mkdir(parents=True, exist_ok=True)
        
        exposure_file = export_dir / f'exposure_report_{site}.csv'
        exposure_data.to_csv(exposure_file, index=False)
        
        result['exposure_report'] = str(exposure_file)
        result['success'] = True
        
        logger.info(f"Pipeline complete. Exposure report: {exposure_file}")
        
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        result['error'] = str(e)
    
    return result


def detect_site(df: pd.DataFrame) -> str:
    """
    Detect DFS site from DataFrame structure.
    
    FanDuel: 9 roster spots (PG, PG, SG, SG, SF, SF, PF, PF, C)
    DraftKings: 8 roster spots (PG, SG, SF, PF, C, G, F, UTIL)
    """
    if 'DFS ID' in df.columns:
        sample_id = str(df['DFS ID'].iloc[0]) if len(df) > 0 else ""
        
        # FanDuel IDs have format: 124539-84669
        if '-' in sample_id:
            return 'FD'
        # DraftKings IDs are plain numbers: 41244586
        else:
            return 'DK'
    
    return 'FD'  # Default to FanDuel


def create_exposure_report(
    df: pd.DataFrame,
    max_exposure: float,
    locks: List[str],
    fades: List[str],
    caps: Dict[str, float]
) -> pd.DataFrame:
    """
    Create an exposure report based on projections and constraints.
    
    Args:
        df: DataFrame with player projections
        max_exposure: Default maximum exposure
        locks: List of player IDs that must be included
        fades: List of player IDs to exclude
        caps: Dict of player_id -> max_exposure
    
    Returns:
        DataFrame with recommended exposures
    """
    # Make a copy of the DataFrame
    report = df.copy()
    
    # Add exposure recommendations
    report['Recommended_Exposure'] = max_exposure
    
    # Apply locks (100% exposure)
    if locks:
        for lock in locks:
            # Handle both formats: "124539-84669" and "84669"
            lock_str = str(lock).strip()
            mask = report['DFS ID'].astype(str).str.contains(lock_str, na=False, regex=False)
            if mask.any():
                report.loc[mask, 'Recommended_Exposure'] = 1.0
                logger.info(f"Locked player: {lock_str}")
    
    # Apply fades (0% exposure)
    if fades:
        for fade in fades:
            fade_str = str(fade).strip()
            mask = report['DFS ID'].astype(str).str.contains(fade_str, na=False, regex=False)
            if mask.any():
                report.loc[mask, 'Recommended_Exposure'] = 0.0
                logger.info(f"Faded player: {fade_str}")
    
    # Apply custom caps
    if caps:
        for player_id, cap in caps.items():
            player_str = str(player_id).strip()
            mask = report['DFS ID'].astype(str).str.contains(player_str, na=False, regex=False)
            if mask.any():
                report.loc[mask, 'Recommended_Exposure'] = float(cap)
                logger.info(f"Custom cap for {player_str}: {cap}")
    
    # Select key columns for the report
    columns_priority = [
        'DFS ID', 'Name', 'Pos', 'Team', 'Opp', 'Salary', 
        'My Proj', 'SS Proj', 'My Own', 'Adj Own', 'Value',
        'Recommended_Exposure'
    ]
    
    # Use only columns that exist
    available_cols = [c for c in columns_priority if c in report.columns]
    
    # Ensure Recommended_Exposure is included
    if 'Recommended_Exposure' not in available_cols:
        available_cols.append('Recommended_Exposure')
    
    report = report[available_cols]
    
    # Sort by projection (descending)
    sort_col = 'My Proj' if 'My Proj' in report.columns else report.columns[0]
    report = report.sort_values(sort_col, ascending=False)
    
    logger.info(f"Created exposure report with {len(report)} players")
    
    return report
