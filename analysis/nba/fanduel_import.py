"""
FanDuel CSV Importer
Imports real FanDuel player data and merges with projections
"""

import pandas as pd
from pathlib import Path
import logging
from fuzzywuzzy import fuzz
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


class FanDuelImporter:
    """Import and process FanDuel CSV exports"""
    
    def __init__(self):
        self.player_mapping = {}
        self.name_variations = {
            # Common name variations
            'Nikola Jokic': ['Nikola Jokić'],
            'Luka Doncic': ['Luka Dončić'],
            'Bogdan Bogdanovic': ['Bogdan Bogdanović'],
            'Nikola Vucevic': ['Nikola Vučević'],
        }
    
    def import_csv(self, csv_path: str) -> pd.DataFrame:
        """
        Import FanDuel CSV
        
        Expected columns from FanDuel or RotoGrinders:
        - DFS ID (like "124780-9488")
        - Name
        - Pos
        - Team
        - Salary
        - Opp (optional)
        """
        try:
            df = pd.read_csv(csv_path)
            
            # Check required columns
            required = ['DFS ID', 'Name', 'Salary']
            missing = [col for col in required if col not in df.columns]
            if missing:
                logger.error(f"Missing required columns: {missing}")
                return pd.DataFrame()
            
            # Rename columns to standard format
            column_mapping = {
                'DFS ID': 'dfs_id',
                'Name': 'player_name',
                'Pos': 'position',
                'Team': 'team',
                'Salary': 'salary',
                'Opp': 'opponent',
                'Status': 'status'
            }
            
            df = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})
            
            # Clean player names
            df['player_name'] = df['player_name'].str.strip()
            df['player_name'] = df['player_name'].str.replace(r'\s+', ' ', regex=True)
            
            # Convert salary to numeric
            df['salary'] = pd.to_numeric(df['salary'], errors='coerce')
            
            # Filter out players with no salary or $0 salary
            df = df[df['salary'] > 0]
            
            # Remove injured/out players if status column exists
            if 'status' in df.columns:
                df = df[~df['status'].isin(['O', 'OUT', 'IR'])]
            
            logger.info(f"Imported {len(df)} players from FanDuel CSV")
            
            return df
            
        except Exception as e:
            logger.error(f"Failed to import FanDuel CSV: {e}")
            return pd.DataFrame()
    
    def _normalize_name(self, name: str) -> str:
        """Normalize player name for matching"""
        # Remove accents and special characters
        import unicodedata
        name = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('ASCII')
        
        # Remove Jr., Sr., III, etc.
        name = name.replace(' Jr.', '').replace(' Sr.', '').replace(' III', '').replace(' II', '')
        
        # Standardize spacing
        name = ' '.join(name.split())
        
        return name.strip()
    
    def _fuzzy_match_name(self, proj_name: str, fd_names: List[str], threshold: int = 85) -> str:
        """
        Fuzzy match player name
        
        Args:
            proj_name: Name from projections
            fd_names: List of names from FanDuel
            threshold: Minimum similarity score (0-100)
        
        Returns:
            Best matching FanDuel name, or empty string if no good match
        """
        proj_normalized = self._normalize_name(proj_name)
        
        best_match = ""
        best_score = 0
        
        for fd_name in fd_names:
            fd_normalized = self._normalize_name(fd_name)
            
            # Calculate similarity
            score = fuzz.ratio(proj_normalized, fd_normalized)
            
            if score > best_score and score >= threshold:
                best_score = score
                best_match = fd_name
        
        if best_match:
            logger.debug(f"Matched '{proj_name}' to '{best_match}' (score: {best_score})")
        
        return best_match
    
    def merge_with_projections(
        self,
        projections: pd.DataFrame,
        fanduel_data: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Merge FanDuel data with projections
        
        Matches by player name with fuzzy matching
        """
        if len(fanduel_data) == 0:
            logger.warning("No FanDuel data to merge")
            return projections
        
        # Create lookup dict for FanDuel data
        fd_lookup = fanduel_data.set_index('player_name').to_dict('index')
        fd_names = list(fd_lookup.keys())
        
        # Match each projection to FanDuel data
        matches = []
        unmatched = []
        
        for _, proj_row in projections.iterrows():
            proj_name = proj_row['player_name']
            
            # Try exact match first
            if proj_name in fd_lookup:
                fd_match = fd_lookup[proj_name]
                matched_name = proj_name
            else:
                # Try fuzzy match
                matched_name = self._fuzzy_match_name(proj_name, fd_names)