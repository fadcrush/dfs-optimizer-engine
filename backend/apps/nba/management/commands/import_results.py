"""
Management command to import contest results
Usage: python manage.py import_results <csv_file> <date> --site FD
"""

from django.core.management.base import BaseCommand
from apps.nba.data_import import import_contest_results


class Command(BaseCommand):
    help = 'Import contest results from CSV'
    
    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Path to CSV file')
        parser.add_argument('date', type=str, help='Slate date (YYYY-MM-DD)')
        parser.add_argument('--site', type=str, default='FD', help='Site (FD or DK)')
    
    def handle(self, *args, **options):
        csv_file = options['csv_file']
        date = options['date']
        site = options['site']
        
        self.stdout.write(f"Importing {csv_file}...")
        
        slate = import_contest_results(csv_file, date, site)
        
        self.stdout.write(
            self.style.SUCCESS(
                f"✓ Successfully imported data for {slate}"
            )
        )