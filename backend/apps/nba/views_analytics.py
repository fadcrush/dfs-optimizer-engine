"""
NBA Analytics Dashboard Views
"""

import logging
import pandas as pd
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse

from .models import NbaSlate, NbaContest, NbaLineupEntry
from analysis.nba.analytics import (
    ContestAnalytics,
    OwnershipPredictor,
    CorrelationAnalyzer,
    generate_analytics_report
)

logger = logging.getLogger(__name__)


@require_http_methods(["GET"])
def analytics_dashboard(request):
    """
    Main analytics dashboard
    Shows ROI, win rates, and performance metrics
    """
    # Get period from query params
    period = request.GET.get('period', '30d')
    
    # Fetch contest data
    contests = NbaContest.objects.select_related('slate').prefetch_related('entries')
    
    # Convert to DataFrame
    entries_data = []
    for contest in contests:
        for entry in contest.entries.all():
            entries_data.append({
                'date': contest.slate.date,
                'contest_id': contest.id,
                'contest_name': contest.contest_name,
                'contest_type': contest.contest_type,
                'entry_fee': float(contest.entry_fee),
                'entry_id': entry.entry_id,
                'projected_points': entry.projected_points,
                'actual_points': entry.actual_points,
                'final_rank': entry.final_rank,
                'payout': float(entry.payout),
                'percentile': entry.percentile,
                'total_entries': contest.total_entries,
            })
    
    if not entries_data:
        context = {
            'note': 'No contest data yet. Start tracking your contests to see analytics!',
            'has_data': False
        }
        return render(request, 'nba/analytics.html', context)
    
    df = pd.DataFrame(entries_data)
    
    # Generate report
    try:
        report = generate_analytics_report(df, period)
        
        context = {
            'has_data': True,
            'period': period,
            'roi': report['roi'],
            'win_rate': report['win_rate'],
            'accuracy': report['projection_accuracy'],
            'total_slates': NbaSlate.objects.count(),
            'total_contests': NbaContest.objects.count(),
            'total_entries': NbaLineupEntry.objects.count(),
        }
    except Exception as e:
        logger.error(f"Analytics generation error: {e}", exc_info=True)
        context = {
            'error': str(e),
            'has_data': False
        }
    
    return render(request, 'nba/analytics.html', context)


@require_http_methods(["GET"])
def ownership_predictions(request):
    """
    Show ownership predictions for uploaded projections
    """
    # This would integrate with your uploaded projections
    context = {
        'note': 'Upload projections to see ownership predictions'
    }
    
    return render(request, 'nba/ownership_predictions.html', context)


@require_http_methods(["GET"])
def correlation_matrix(request):
    """
    Display player correlation matrix
    """
    # Get historical performance data
    # This is a placeholder - you'd fetch from NbaPlayerPerformance
    
    context = {
        'note': 'Correlation analysis coming soon'
    }
    
    return render(request, 'nba/correlation_matrix.html', context)


@require_http_methods(["POST"])
def api_analytics_report(request):
    """
    API endpoint for analytics report
    Returns JSON
    """
    period = request.POST.get('period', '30d')
    
    try:
        contests = NbaContest.objects.select_related('slate').prefetch_related('entries')
        
        entries_data = []
        for contest in contests:
            for entry in contest.entries.all():
                entries_data.append({
                    'date': contest.slate.date,
                    'contest_type': contest.contest_type,
                    'entry_fee': float(contest.entry_fee),
                    'projected_points': entry.projected_points,
                    'actual_points': entry.actual_points,
                    'payout': float(entry.payout),
                    'percentile': entry.percentile,
                    'total_entries': contest.total_entries,
                })
        
        if not entries_data:
            return JsonResponse({'error': 'No data available'}, status=404)
        
        df = pd.DataFrame(entries_data)
        report = generate_analytics_report(df, period)
        
        return JsonResponse(report)
    
    except Exception as e:
        logger.error(f"API analytics error: {e}", exc_info=True)
        return JsonResponse({'error': str(e)}, status=500)