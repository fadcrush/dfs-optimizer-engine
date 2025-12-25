"""
Data Import Utilities
Import contest results, player performance, etc.
"""

import pandas as pd
import logging
from decimal import Decimal
from django.utils import timezone
from .models import (
    NbaSlate,
    NbaContest,
    NbaLineupEntry,
    NbaPlayerPerformance
)

logger = logging.getLogger(__name__)


def import_contest_results(csv_file, slate_date, site='FD'):
    """
    Import contest results from CSV
    
    Expected columns:
    - Entry ID, Contest Name, Entry Fee, Final Rank, Payout, Total Entries
    """
    df = pd.read_csv(csv_file)
    
    # Get or create slate
    slate, created = NbaSlate.objects.get_or_create(
        date=slate_date,
        site=site,
        defaults={'name': 'Main'}
    )
    
    if created:
        logger.info(f"Created new slate: {slate}")
    
    # Group by contest
    imported = 0
    for contest_name, entries_df in df.groupby('Contest Name'):
        # Get or create contest
        contest, created = NbaContest.objects.get_or_create(
            slate=slate,
            contest_name=contest_name,
            defaults={
                'entry_fee': Decimal(str(entries_df.iloc[0]['Entry Fee'])),
                'total_entries': int(entries_df.iloc[0]['Total Entries']),
                'contest_type': 'GPP'  # You might want to detect this
            }
        )
        
        # Import entries
        for _, row in entries_df.iterrows():
            entry, created = NbaLineupEntry.objects.update_or_create(
                contest=contest,
                entry_id=str(row['Entry ID']),
                defaults={
                    'final_rank': int(row['Final Rank']) if pd.notna(row['Final Rank']) else None,
                    'payout': Decimal(str(row['Payout'])) if pd.notna(row['Payout']) else Decimal('0'),
                }
            )
            
            # Calculate percentile
            if entry.final_rank and contest.total_entries:
                entry.percentile = (entry.final_rank / contest.total_entries) * 100
                entry.save()
            
            imported += 1
    
    # Update slate totals
    slate.update_totals()
    
    logger.info(f"Imported {imported} entries across {df['Contest Name'].nunique()} contests")
    
    return slate


def import_player_performance(csv_file, slate):
    """
    Import player actual performance
    
    Expected columns:
    - DFS ID, Name, Position, Team, Salary, Projected Points, Actual Points
    """
    df = pd.read_csv(csv_file)
    
    imported = 0
    for _, row in df.iterrows():
        perf, created = NbaPlayerPerformance.objects.update_or_create(
            slate=slate,
            dfs_id=str(row['DFS ID']),
            defaults={
                'name': row['Name'],
                'position': row['Position'],
                'team': row['Team'],
                'salary': int(row['Salary']),
                'projected_points': float(row['Projected Points']),
                'actual_points': float(row['Actual Points']) if pd.notna(row['Actual Points']) else None,
            }
        )
        imported += 1
    
    logger.info(f"Imported {imported} player performances for {slate}")
    
    return imported


def bulk_import_directory(directory_path, site='FD'):
    """
    Import all CSV files from a directory
    Expects filenames like: 2024-12-20_contests.csv, 2024-12-20_players.csv
    """
    from pathlib import Path
    
    dir_path = Path(directory_path)
    
    for csv_file in dir_path.glob('*_contests.csv'):
        # Extract date from filename
        date_str = csv_file.stem.split('_')[0]
        
        logger.info(f"Importing {csv_file}")
        slate = import_contest_results(csv_file, date_str, site)
        
        # Check for matching player file
        player_file = dir_path / f"{date_str}_players.csv"
        if player_file.exists():
            logger.info(f"Importing {player_file}")
            import_player_performance(player_file, slate)
    
    logger.info("Bulk import complete")