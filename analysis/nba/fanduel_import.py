"""
FanDuel CSV Importer
Imports real FanDuel player data and merges with projections
"""

import pandas as pd
from pathlib import Path
import logging
from rapidfuzz import fuzz, process
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
    
    def _fuzzy_match_name(
        self,
        proj_name: str,
        fd_names: List[str],
        threshold: int = 85,
        *,
        _norm_to_orig: dict | None = None,
    ) -> str:
        """
        Fuzzy match a player name against a list of FanDuel names.

        Uses rapidfuzz (C++ backend) with score_cutoff pruning for O(n)
        behaviour rather than the old O(n²) fuzzywuzzy loop.

        Pass ``_norm_to_orig`` (pre-computed by ``merge_with_projections``)
        to avoid re-normalizing ``fd_names`` on every call.
        """
        proj_normalized = self._normalize_name(proj_name)

        if _norm_to_orig is None:
            _norm_to_orig = {self._normalize_name(n): n for n in fd_names}

        result = process.extractOne(
            proj_normalized,
            list(_norm_to_orig.keys()),
            scorer=fuzz.ratio,
            score_cutoff=threshold,
        )
        if result is None:
            return ""

        matched_normalized, score, _ = result
        best_match = _norm_to_orig[matched_normalized]
        logger.debug("Matched '%s' to '%s' (score: %d)", proj_name, best_match, score)
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

        # Pre-compute normalized→original mapping ONCE for the whole merge
        # (avoids re-normalizing all fd_names on every fuzzy call)
        _norm_to_orig = {self._normalize_name(n): n for n in fd_names}

        # Match each projection to FanDuel data
        matches: list[dict] = []
        unmatched: list[str] = []

        for _, proj_row in projections.iterrows():
            proj_name = proj_row['player_name']

            # Try exact match first
            if proj_name in fd_lookup:
                fd_match = fd_lookup[proj_name]
                matched_name = proj_name
            else:
                # Try fuzzy match using pre-computed normalized map
                matched_name = self._fuzzy_match_name(
                    proj_name, fd_names, _norm_to_orig=_norm_to_orig
                )

            if matched_name and matched_name in fd_lookup:
                fd_match = fd_lookup[matched_name]
                row = proj_row.to_dict()
                row.update({
                    'dfs_id': fd_match.get('dfs_id', ''),
                    'salary': fd_match.get('salary', proj_row.get('salary', 0)),
                    'position': fd_match.get('position', proj_row.get('position', '')),
                    'team': fd_match.get('team', proj_row.get('team', '')),
                    'opponent': fd_match.get('opponent', proj_row.get('opponent', '')),
                })
                matches.append(row)
            else:
                unmatched.append(proj_name)
                matches.append(proj_row.to_dict())

        if unmatched:
            logger.warning("%d projection players not matched to FanDuel data: %s",
                           len(unmatched), unmatched[:10])

        result_df = pd.DataFrame(matches)
        logger.info("Merged %d projections; %d matched, %d unmatched",
                    len(result_df), len(matches) - len(unmatched), len(unmatched))
        return result_df