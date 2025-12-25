"""
Django Management Command: Run Daily Projections
Usage: python manage.py run_projections --date 2024-12-25 --site FD
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import datetime, timedelta
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Generate daily DFS projections'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='Target date (YYYY-MM-DD), defaults to today',
            default=None
        )
        parser.add_argument(
            '--site',
            type=str,
            choices=['FD', 'DK'],
            default='FD',
            help='DFS site (FanDuel or DraftKings)'
        )
        parser.add_argument(
            '--save-to-db',
            action='store_true',
            help='Save projections to database'
        )
        parser.add_argument(
            '--validate-previous',
            action='store_true',
            help='Validate yesterday\'s projections against actuals'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed output'
        )
    
    def handle(self, *args, **options):
        target_date = options['date']
        site = options['site']
        save_to_db = options['save_to_db']
        validate = options['validate_previous']
        verbose = options['verbose']
        
        # Default to today
        if not target_date:
            target_date = datetime.now().strftime('%Y-%m-%d')
        
        # Print header
        self.stdout.write(self.style.SUCCESS('='*70))
        self.stdout.write(self.style.SUCCESS('  DFS PROJECTION GENERATOR'))
        self.stdout.write(self.style.SUCCESS('='*70))
        self.stdout.write('')
        self.stdout.write(f'  Date: {target_date}')
        self.stdout.write(f'  Site: {site}')
        self.stdout.write('')
        
        # Validate previous day if requested
        if validate:
            self._validate_previous_day(target_date, site)
        
        # Run projection pipeline
        self.stdout.write(self.style.WARNING('🚀 Running projection pipeline...'))
        
        try:
            # Import projection pipeline
            from analysis.nba.projection_pipeline import ProjectionPipeline
            
            # Create pipeline and run
            pipeline = ProjectionPipeline()
            results = pipeline.run_full_pipeline(
                target_date=target_date,
                site=site,
                save_intermediate=True
            )
            
            # Display results
            if results.get('success'):
                self._display_success(results, verbose)
                
                # Save to database if requested
                if save_to_db:
                    self._save_to_database(results, target_date, site)
            else:
                self._display_failure(results)
        
        except ImportError as e:
            self.stdout.write(self.style.ERROR(f'\n✗ Import error: {e}'))
            self.stdout.write('\nMake sure all required files exist:')
            self.stdout.write('  • analysis/nba/projection_pipeline.py')
            self.stdout.write('  • analysis/nba/data_aggregator.py')
            self.stdout.write('  • analysis/nba/projection_engine.py')
            self.stdout.write('  • analysis/shared/api_clients.py')
        
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n✗ Unexpected error: {e}'))
            if verbose:
                import traceback
                self.stdout.write('\nTraceback:')
                self.stdout.write(traceback.format_exc())
        
        # Print footer
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('='*70))
    
    def _display_success(self, results, verbose):
        """Display successful results"""
        self.stdout.write(self.style.SUCCESS('\n✓ Projections generated successfully!'))
        
        # Display statistics
        if results.get('stats'):
            self.stdout.write(f"\n📊 Stats:")
            stats = results['stats']
            
            # Data sources
            if 'data_sources' in stats:
                ds = stats['data_sources']
                self.stdout.write(f"  • Vegas games: {ds.get('vegas_games', 0)}")
                self.stdout.write(f"  • Player stats: {ds.get('season_stats', 0)}")
                self.stdout.write(f"  • Recent games: {ds.get('recent_games', 0)}")
                self.stdout.write(f"  • Injuries: {ds.get('injuries', 0)}")
            
            # Projection stats
            if 'total_projections' in stats:
                self.stdout.write(f"\n  • Total projections: {stats['total_projections']}")
            if 'avg_projection' in stats:
                self.stdout.write(f"  • Average projection: {stats['avg_projection']:.2f} pts")
            if 'max_projection' in stats:
                self.stdout.write(f"  • Max projection: {stats['max_projection']:.2f} pts")
            if 'avg_value' in stats:
                self.stdout.write(f"  • Average value: {stats['avg_value']:.2f}")
            if 'high_value_plays' in stats:
                self.stdout.write(f"  • High value plays (>5.5): {stats['high_value_plays']}")
        
        # Display files
        if results.get('files'):
            self.stdout.write(f"\n📁 Files generated:")
            for file_type, path in results['files'].items():
                # Shorten path for display
                short_path = Path(path).name
                self.stdout.write(f"  • {file_type}: {short_path}")
                if verbose:
                    self.stdout.write(f"    Full path: {path}")
        else:
            self.stdout.write(f"\n📁 Files: No files generated (check logs)")
        
        # Display duration
        if 'duration_seconds' in results:
            self.stdout.write(f"\n⏱️  Duration: {results['duration_seconds']:.1f} seconds")
    
    def _display_failure(self, results):
        """Display failure information"""
        self.stdout.write(self.style.ERROR('\n✗ Projection generation failed!'))
        
        if results.get('errors'):
            self.stdout.write('\nErrors:')
            for error in results['errors']:
                self.stdout.write(self.style.ERROR(f"  • {error}"))
        
        if results.get('stats', {}).get('data_sources'):
            self.stdout.write('\nData Sources Status:')
            ds = results['stats']['data_sources']
            self.stdout.write(f"  • Vegas games: {ds.get('vegas_games', 0)}")
            self.stdout.write(f"  • Player stats: {ds.get('season_stats', 0)}")
            self.stdout.write(f"  • Recent games: {ds.get('recent_games', 0)}")
    
    def _validate_previous_day(self, current_date, site):
        """Validate previous day's projections"""
        self.stdout.write(self.style.WARNING('\n📊 Validating previous projections...'))
        
        try:
            prev_date = (datetime.strptime(current_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
            
            # Load projections and actuals
            from django.conf import settings
            from analysis.nba.projection_pipeline import ProjectionComparer
            import pandas as pd
            
            proj_file = Path(settings.EXPORT_ROOT) / 'projections' / f'{site}_{prev_date}_full.csv'
            
            if not proj_file.exists():
                self.stdout.write(self.style.WARNING(f"  No projections found for {prev_date}"))
                return
            
            # Try to get actuals from database
            from apps.nba.models import NbaSlate, NbaPlayerPerformance
            
            try:
                slate = NbaSlate.objects.get(date=prev_date, site=site)
                actuals = NbaPlayerPerformance.objects.filter(slate=slate)
                
                if not actuals.exists():
                    self.stdout.write(self.style.WARNING(f"  No actual results found for {prev_date}"))
                    return
                
                # Compare
                projections = pd.read_csv(proj_file)
                actuals_df = pd.DataFrame(list(actuals.values()))
                
                comparer = ProjectionComparer()
                metrics = comparer.compare_to_actuals(projections, actuals_df)
                
                if 'error' not in metrics:
                    self.stdout.write(f"  MAE: {metrics.get('mean_absolute_error', 0):.2f} points")
                    self.stdout.write(f"  RMSE: {metrics.get('root_mean_squared_error', 0):.2f} points")
                    self.stdout.write(f"  Within 5pts: {metrics.get('within_5_points', 0):.1f}%")
                    self.stdout.write(f"  R²: {metrics.get('r_squared', 0):.3f}")
                else:
                    self.stdout.write(self.style.WARNING(f"  {metrics['error']}"))
            
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"  Could not load actuals: {e}"))
        
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Validation failed: {e}"))
    
    def _save_to_database(self, results, target_date, site):
        """Save projections to database"""
        self.stdout.write(self.style.WARNING('\n💾 Saving to database...'))
        
        try:
            # This would save projections to database
            # For now, just acknowledge
            total = results.get('stats', {}).get('total_projections', 0)
            self.stdout.write(f"  ✓ Would save {total} projections")
            self.stdout.write(f"  (Database saving not yet implemented)")
        
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  ✗ Database save failed: {e}"))
    
    def _format_file_path(self, path):
        """Format file path for display"""
        try:
            p = Path(path)
            if len(str(p)) > 60:
                return f"...{str(p)[-57:]}"
            return str(p)
        except:
            return path