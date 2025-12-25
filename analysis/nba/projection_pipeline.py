"""
Complete NBA Projection Pipeline
End-to-end projection generation system
Clean version - No unicode characters for Windows compatibility
"""

from typing import Dict, List, Optional
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
import logging
import json
import os

from .data_aggregator import NBADataAggregator
from .projection_engine import NBAProjectionEngine

logger = logging.getLogger(__name__)


class ProjectionPipeline:
    """
    Complete projection pipeline
    Fetches data -> Generates projections -> Validates -> Exports
    """
    
    def __init__(self, output_dir: Optional[Path] = None):
        self.aggregator = NBADataAggregator()
        self.engine = NBAProjectionEngine()
        
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            try:
                from django.conf import settings
                self.output_dir = Path(settings.EXPORT_ROOT) / 'projections'
            except:
                self.output_dir = Path(__file__).parent.parent.parent / 'exports' / 'projections'
        
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def run_full_pipeline(
        self,
        target_date: Optional[str] = None,
        site: str = 'FD',
        save_intermediate: bool = True
    ) -> Dict:
        """Run complete projection pipeline"""
        if not target_date:
            target_date = datetime.now().strftime('%Y-%m-%d')
        
        logger.info("="*60)
        logger.info(f"STARTING PROJECTION PIPELINE FOR {target_date}")
        logger.info("="*60)
        
        pipeline_start = datetime.now()
        results = {
            'date': target_date,
            'site': site,
            'success': False,
            'files': {},
            'stats': {},
            'errors': []
        }
        
        try:
            # Step 1: Fetch all data
            logger.info("\n[1/6] Fetching data from APIs...")
            data = self._fetch_all_data(target_date)
            
            if save_intermediate:
                self._save_raw_data(data, target_date)
            
            results['stats']['data_sources'] = {
                'vegas_games': len(data.get('vegas', [])),
                'season_stats': len(data.get('season_stats', [])),
                'recent_games': len(data.get('recent_games', [])),
                'injuries': len(data.get('injuries', []))
            }
            
            # Step 2: Validate data quality
            logger.info("\n[2/6] Validating data quality...")
            validation = self._validate_data(data)
            
            if not validation['is_valid']:
                results['errors'] = validation['errors']
                logger.error(f"Data validation failed: {validation['errors']}")
                return results
            
            # Step 3: Generate projections
            logger.info("\n[3/6] Generating projections...")
            projections = self._generate_projections(data, target_date)
            
            if projections is None or len(projections) == 0:
                results['errors'].append("No projections generated")
                return results
            
            results['stats']['total_projections'] = len(projections)
            
            # Step 4: Load salaries and calculate value
            logger.info("\n[4/6] Loading salaries and calculating value...")
            projections = self._add_salaries(projections, target_date, site)
            
            # Step 5: Add ownership projections
            logger.info("\n[4/6] Loading FanDuel data and calculating value...")
            projections = self._add_fanduel_data(projections, target_date, site)
            
            # Fallback to estimated salaries if no FanDuel data
            if 'salary' not in projections.columns or projections['salary'].isna().all():
                projections = self._add_salaries(projections, target_date, site)
            
            # Step 6: Export in multiple formats
            logger.info("\n[6/6] Exporting projections...")
            export_files = self._export_projections(projections, target_date, site)
            
            results['files'] = export_files
            results['success'] = True
            
            # Calculate stats
            results['stats'].update({
                'avg_projection': float(projections['base_projection'].mean()),
                'max_projection': float(projections['base_projection'].max()),
                'avg_value': float(projections['value'].mean()) if 'value' in projections.columns else 0,
                'high_value_plays': len(projections[projections['value'] > 5.5]) if 'value' in projections.columns else 0
            })
            
            pipeline_duration = (datetime.now() - pipeline_start).total_seconds()
            results['duration_seconds'] = pipeline_duration
            
            logger.info(f"\n{'='*60}")
            logger.info(f"PIPELINE COMPLETED SUCCESSFULLY ({pipeline_duration:.1f}s)")
            logger.info(f"{'='*60}")
            
            self._save_pipeline_summary(results, target_date)
            
        except Exception as e:
            logger.error(f"Pipeline failed: {e}", exc_info=True)
            results['errors'].append(str(e))
        
        return results
    
    def _fetch_all_data(self, target_date: str) -> Dict:
        """Fetch all required data"""
        data = {}
        
        try:
            logger.info("  -> Fetching vegas lines...")
            data['vegas'] = self.aggregator.odds_client.get_nba_odds(target_date)
            logger.info(f"    OK Got {len(data['vegas'])} games")
        except Exception as e:
            logger.warning(f"    X Vegas data failed: {e}")
            data['vegas'] = []
        
        try:
            logger.info("  -> Fetching season stats...")
            season = datetime.now().year
            data['season_stats'] = self.aggregator.sportsdata_client.get_player_stats_season(str(season))
            logger.info(f"    OK Got stats for {len(data['season_stats'])} players")
        except Exception as e:
            logger.warning(f"    X Season stats failed: {e}")
            data['season_stats'] = []
        
        try:
            logger.info("  -> Fetching recent games...")
            recent = []
            for i in range(10):
                past_date = (datetime.strptime(target_date, '%Y-%m-%d') - timedelta(days=i+1)).strftime('%Y-%m-%d')
                games = self.aggregator.sportsdata_client.get_player_game_stats(past_date)
                if games:
                    recent.extend(games)
            data['recent_games'] = recent
            logger.info(f"    OK Got {len(recent)} recent games")
        except Exception as e:
            logger.warning(f"    X Recent games failed: {e}")
            data['recent_games'] = []
        
        try:
            logger.info("  -> Fetching injury report...")
            data['injuries'] = self.aggregator.sportsdata_client.get_injuries()
            logger.info(f"    OK Got {len(data['injuries'])} injury reports")
        except Exception as e:
            logger.warning(f"    X Injury data failed: {e}")
            data['injuries'] = []
        
        return data
    
    def _validate_data(self, data: Dict) -> Dict:
        """Validate data quality"""
        validation = {
            'is_valid': True,
            'warnings': [],
            'errors': []
        }
        
        # Check we have games (warning only)
        if not data.get('vegas') or len(data['vegas']) == 0:
            validation['warnings'].append("No vegas lines found - projections will lack game context")
        
        # Check we have player data
        if not data.get('season_stats') or len(data['season_stats']) == 0:
            validation['errors'].append("No season stats found")
            validation['is_valid'] = False
        
        # Warnings for missing optional data
        if not data.get('recent_games'):
            validation['warnings'].append("No recent games data - using season averages only")
        
        if not data.get('injuries'):
            validation['warnings'].append("No injury data - cannot adjust for missing players")
        
        return validation
    
    def _generate_projections(self, data: Dict, target_date: str) -> Optional[pd.DataFrame]:
        """Generate projections from data"""
        try:
            season_df = pd.DataFrame(data.get('season_stats', []))
            
            if len(season_df) == 0:
                logger.error("Cannot generate projections without season stats")
                return None
            
            projections = []
            for _, player in season_df.iterrows():
                games_played = max(player.get('Games', 1), 1)
                
                ppg = player.get('Points', 0) / games_played
                rpg = player.get('Rebounds', 0) / games_played
                apg = player.get('Assists', 0) / games_played
                spg = player.get('Steals', 0) / games_played
                bpg = player.get('Blocks', 0) / games_played
                tpg = player.get('Turnovers', 0) / games_played
                mpg = player.get('Minutes', 0) / games_played
                
                fpts = (
                    ppg * 1.0 +
                    rpg * 1.2 +
                    apg * 1.5 +
                    spg * 3.0 +
                    bpg * 3.0 +
                    tpg * -1.0
                )
                
                if mpg < 5:
                    continue
                
                projections.append({
                    'player_id': player.get('PlayerID', ''),
                    'player_name': player.get('Name', 'Unknown'),
                    'team': player.get('Team', ''),
                    'position': player.get('Position', ''),
                    'opponent': '',
                    'base_projection': round(fpts, 2),
                    'ceiling': round(fpts * 1.25, 2),
                    'floor': round(max(fpts * 0.75, 0), 2),
                    'minutes_proj': round(mpg, 1),
                    'salary': 5000,
                    'games_played': games_played
                })
            
            proj_df = pd.DataFrame(projections)
            logger.info(f"Generated {len(proj_df)} projections")
            
            return proj_df
            
        except Exception as e:
            logger.error(f"Projection generation failed: {e}", exc_info=True)
            return None
    
    def _add_fanduel_data(
        self,
        projections: pd.DataFrame,
        target_date: str,
        site: str
    ) -> pd.DataFrame:
        """Add real FanDuel data if CSV exists"""
        from .fanduel_import import FanDuelImporter
        
        # Look for FanDuel CSV
        fd_csv = self.output_dir.parent / 'fanduel' / f'{site}_{target_date}.csv'
        
        if fd_csv.exists():
            logger.info(f"  -> Loading FanDuel data from {fd_csv}")
            importer = FanDuelImporter()
            fd_data = importer.import_csv(str(fd_csv))
            projections = importer.merge_with_projections(projections, fd_data)
            logger.info("  -> Merged with FanDuel data")
        else:
            logger.warning(f"  -> FanDuel CSV not found at {fd_csv}")
            logger.warning("  -> Using estimated salaries")
        
        return projections

    
    def _add_fanduel_data(
        self,
        projections: pd.DataFrame,
        target_date: str,
        site: str
    ) -> pd.DataFrame:
        """Add real FanDuel data if CSV exists"""
        from .fanduel_import import FanDuelImporter
        
        # Look for FanDuel CSV
        fd_csv = self.output_dir.parent / 'fanduel' / f'{site}_{target_date}.csv'
        
        if fd_csv.exists():
            logger.info(f"  -> Loading FanDuel data from {fd_csv}")
            importer = FanDuelImporter()
            fd_data = importer.import_csv(str(fd_csv))
            projections = importer.merge_with_projections(projections, fd_data)
            logger.info("  -> Merged with FanDuel data")
        else:
            logger.warning(f"  -> FanDuel CSV not found at {fd_csv}")
            logger.warning("  -> Using estimated salaries")
        
        return projections
    
    def _add_salaries(self, projections: pd.DataFrame, target_date: str, site: str) -> pd.DataFrame:
        """Add DFS salaries"""
        # ... existing code ...
    def _add_salaries(self, projections: pd.DataFrame, target_date: str, site: str) -> pd.DataFrame:
        """Add DFS salaries"""
        salary_file = self.output_dir.parent / 'salaries' / f'{site}_{target_date}.csv'
        
        if salary_file.exists():
            logger.info(f"  -> Loading salaries from {salary_file}")
            salaries = pd.read_csv(salary_file)
            projections = projections.merge(
                salaries[['player_id', 'salary']],
                on='player_id',
                how='left',
                suffixes=('', '_new')
            )
            if 'salary_new' in projections.columns:
                projections['salary'] = projections['salary_new'].fillna(projections['salary'])
                projections.drop('salary_new', axis=1, inplace=True)
        else:
            logger.warning(f"  -> Salary file not found, using estimates")
            projections['salary'] = (projections['base_projection'] * 200).astype(int)
            projections['salary'] = projections['salary'].clip(3000, 12000)
        
        # Calculate value
        projections['value'] = (projections['base_projection'] / projections['salary']) * 1000
        
        return projections
    
    def _add_ownership_projections(self, projections: pd.DataFrame) -> pd.DataFrame:
        """Add ownership projections"""
        value_norm = projections['value'] / projections['value'].max()
        salary_norm = projections['salary'] / projections['salary'].max()
        ownership_score = (value_norm * 0.6) + (salary_norm * 0.4)
        projections['projected_ownership'] = (ownership_score * 40 + 5).clip(1, 50)
        return projections
    
    def _export_projections(self, projections: pd.DataFrame, target_date: str, site: str) -> Dict:
        """Export projections in multiple formats"""
        files = {}
        
        # Full export
        full_file = self.output_dir / f'{site}_{target_date}_full.csv'
        projections.to_csv(full_file, index=False)
        files['full'] = str(full_file)
        logger.info(f"  OK Full export: {full_file}")
        
        # Upload format - use FanDuel exporter if we have DFS IDs
        if 'dfs_id' in projections.columns and projections['dfs_id'].notna().any():
            from .fanduel_import import FanDuelImporter
            importer = FanDuelImporter()
            
            upload_file = self.output_dir / f'{site}_{target_date}_upload.csv'
            importer.export_for_upload(projections, str(upload_file), site)
            files['upload'] = str(upload_file)
            logger.info(f"  OK FanDuel format: {upload_file}")
        else:
            # Fallback to basic format
            upload_columns = [
                'player_name', 'team', 'position', 'opponent',
                'salary', 'base_projection', 'ceiling', 'floor',
                'value', 'projected_ownership', 'minutes_proj'
            ]
            available_cols = [col for col in upload_columns if col in projections.columns]
            upload_df = projections[available_cols].copy()
            
            upload_file = self.output_dir / f'{site}_{target_date}_upload.csv'
            upload_df.to_csv(upload_file, index=False)
            files['upload'] = str(upload_file)
            logger.info(f"  OK Upload format: {upload_file}")
    
    def _save_raw_data(self, data: Dict, target_date: str):
        """Save raw data for debugging"""
        raw_dir = self.output_dir / 'raw' / target_date
        raw_dir.mkdir(parents=True, exist_ok=True)
        
        for key, value in data.items():
            if value:
                file_path = raw_dir / f'{key}.json'
                with open(file_path, 'w') as f:
                    json.dump(value, f, indent=2, default=str)
        
        logger.info(f"  OK Saved raw data to {raw_dir}")
    
    def _save_pipeline_summary(self, results: Dict, target_date: str):
        """Save pipeline execution summary"""
        summary_file = self.output_dir / f'pipeline_summary_{target_date}.json'
        with open(summary_file, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        logger.info(f"  OK Pipeline summary: {summary_file}")


class ProjectionComparer:
    """Compare projections to actuals for accuracy tracking"""
    
    def compare_to_actuals(self, projections: pd.DataFrame, actuals: pd.DataFrame) -> Dict:
        """Compare projections to actual results"""
        comparison = projections.merge(
            actuals[['player_id', 'actual_points']],
            on='player_id',
            how='inner'
        )
        
        if len(comparison) == 0:
            return {'error': 'No matching players found'}
        
        comparison['error'] = comparison['actual_points'] - comparison['base_projection']
        comparison['abs_error'] = abs(comparison['error'])
        comparison['pct_error'] = (comparison['abs_error'] / comparison['base_projection'] * 100)
        
        metrics = {
            'total_players': len(comparison),
            'mean_error': float(comparison['error'].mean()),
            'mean_absolute_error': float(comparison['abs_error'].mean()),
            'root_mean_squared_error': float(np.sqrt((comparison['error'] ** 2).mean())),
            'mean_percentage_error': float(comparison['pct_error'].mean()),
            'r_squared': float(comparison[['base_projection', 'actual_points']].corr().iloc[0, 1] ** 2),
            'within_5_points': float((comparison['abs_error'] <= 5).sum() / len(comparison) * 100),
            'within_10_points': float((comparison['abs_error'] <= 10).sum() / len(comparison) * 100),
            'over_projected': float((comparison['error'] > 0).sum() / len(comparison) * 100),
            'under_projected': float((comparison['error'] < 0).sum() / len(comparison) * 100),
        }
        
        biggest_misses = comparison.nlargest(10, 'abs_error')[[
            'player_name', 'base_projection', 'actual_points', 'error'
        ]]
        metrics['biggest_misses'] = biggest_misses.to_dict('records')
        
        return metrics