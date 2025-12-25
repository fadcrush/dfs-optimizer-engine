"""
NBA Lineup Optimizer & Selection Views
Advanced lineup selection and portfolio optimization
"""

import logging
import pandas as pd
import json
from pathlib import Path
from typing import Dict, List

from django.conf import settings
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse, HttpResponse

from analysis.nba.analytics import LineupSelector

logger = logging.getLogger(__name__)


@require_http_methods(["GET", "POST"])
def lineup_selector(request):
    """
    Advanced lineup selection interface
    """
    context = {
        'error': None,
        'note': None,
        'lineups': [],
        'selection_stats': {},
        'filters': {
            'min_projection': 0,
            'max_projection': 500,
            'min_ownership': 0,
            'max_ownership': 100,
            'max_correlation': 0.8,
        }
    }
    
    if request.method == "GET":
        return render(request, 'nba/lineup_selector.html', context)
    
    # POST - Process lineup selection
    try:
        lineups_file = request.FILES.get("lineups_file")
        
        if not lineups_file:
            context['error'] = "Please upload a lineups file"
            return render(request, 'nba/lineup_selector.html', context)
        
        # Read lineups
        lineups_df = pd.read_csv(lineups_file)
        
        # Get selection parameters
        n_select = int(request.POST.get('n_select', 20))
        diversification = float(request.POST.get('diversification_weight', 0.3))
        max_correlation = float(request.POST.get('max_correlation', 0.8))
        
        # Filters
        min_proj = float(request.POST.get('min_projection', 0))
        max_proj = float(request.POST.get('max_projection', 500))
        min_own = float(request.POST.get('min_ownership', 0))
        max_own = float(request.POST.get('max_ownership', 100))
        
        # Apply filters
        filtered_df = lineups_df[
            (lineups_df['projected_points'] >= min_proj) &
            (lineups_df['projected_points'] <= max_proj) &
            (lineups_df['avg_ownership'] >= min_own) &
            (lineups_df['avg_ownership'] <= max_own)
        ].copy()
        
        if len(filtered_df) == 0:
            context['error'] = "No lineups match the filters"
            return render(request, 'nba/lineup_selector.html', context)
        
        # Score and rank lineups
        scored_df = score_lineups(filtered_df, diversification)
        
        # Select optimal subset
        selector = LineupSelector(scored_df)
        selected_df = selector.select_optimal(
            n_lineups=n_select,
            max_correlation=max_correlation,
            diversification_weight=diversification
        )
        
        # Calculate selection stats
        selection_stats = calculate_selection_stats(scored_df, selected_df)
        
        # Prepare lineups for display
        lineups = selected_df.to_dict('records')
        
        # Add selection data to session for export
        request.session['selected_lineups'] = selected_df.to_json()
        
        context.update({
            'lineups': lineups,
            'selection_stats': selection_stats,
            'total_lineups': len(lineups_df),
            'filtered_count': len(filtered_df),
            'selected_count': len(selected_df),
            'note': f"✅ Selected {len(selected_df)} optimal lineups from {len(filtered_df)} candidates"
        })
    
    except Exception as e:
        logger.error(f"Lineup selection error: {e}", exc_info=True)
        context['error'] = str(e)
    
    return render(request, 'nba/lineup_selector.html', context)


@require_http_methods(["POST"])
def optimize_portfolio(request):
    """
    API endpoint for portfolio optimization
    Returns optimal lineup selection as JSON
    """
    try:
        data = json.loads(request.body)
        lineups = pd.DataFrame(data['lineups'])
        
        n_select = data.get('n_select', 20)
        diversification = data.get('diversification_weight', 0.3)
        max_correlation = data.get('max_correlation', 0.8)
        
        # Score lineups
        scored_df = score_lineups(lineups, diversification)
        
        # Select optimal
        selector = LineupSelector(scored_df)
        selected_df = selector.select_optimal(
            n_lineups=n_select,
            max_correlation=max_correlation,
            diversification_weight=diversification
        )
        
        # Stats
        stats = calculate_selection_stats(scored_df, selected_df)
        
        return JsonResponse({
            'success': True,
            'selected_lineups': selected_df.to_dict('records'),
            'stats': stats
        })
    
    except Exception as e:
        logger.error(f"Portfolio optimization error: {e}", exc_info=True)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_http_methods(["POST"])
def export_selected_lineups(request):
    """
    Export selected lineups to CSV
    """
    try:
        # Get selection mode
        mode = request.POST.get('mode', 'session')
        
        if mode == 'session':
            # Get from session
            selected_json = request.session.get('selected_lineups')
            if not selected_json:
                return HttpResponse("No lineups selected", status=400)
            
            selected_df = pd.read_json(selected_json)
        
        elif mode == 'custom':
            # Get from POST data
            lineup_ids = request.POST.getlist('lineup_ids[]')
            
            if not lineup_ids:
                return HttpResponse("No lineups selected", status=400)
            
            # This assumes you have the full dataset available
            # You might need to store this in session or database
            return HttpResponse("Custom selection not yet implemented", status=400)
        
        else:
            return HttpResponse("Invalid mode", status=400)
        
        # Export to CSV
        export_dir = Path(settings.EXPORT_ROOT) / 'nba'
        export_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f'optimal_lineups_{pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")}.csv'
        export_path = export_dir / filename
        
        selected_df.to_csv(export_path, index=False)
        
        # Return file
        response = HttpResponse(
            selected_df.to_csv(index=False),
            content_type='text/csv'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response
    
    except Exception as e:
        logger.error(f"Export error: {e}", exc_info=True)
        return HttpResponse(f"Export failed: {str(e)}", status=500)


def score_lineups(lineups_df: pd.DataFrame, diversification_weight: float) -> pd.DataFrame:
    """
    Score lineups based on projection and ownership
    
    Args:
        lineups_df: DataFrame with lineup data
        diversification_weight: Weight for ownership (0-1)
    
    Returns:
        DataFrame with scoring columns added
    """
    df = lineups_df.copy()
    
    # Ensure required columns exist
    if 'projected_points' not in df.columns:
        df['projected_points'] = df.get('Proj Sum', 0)
    
    if 'avg_ownership' not in df.columns:
        df['avg_ownership'] = df.get('Avg Exp', 0)
    
    # Calculate scores
    max_proj = df['projected_points'].max()
    if max_proj > 0:
        df['projection_score'] = (df['projected_points'] / max_proj) * 100
    else:
        df['projection_score'] = 0
    
    # Ownership score (lower ownership = higher score)
    df['ownership_score'] = 100 - df['avg_ownership']
    
    # Combined score
    df['quality_score'] = (
        df['projection_score'] * (1 - diversification_weight) +
        df['ownership_score'] * diversification_weight
    )
    
    # Add value score if salary exists
    if 'total_salary' in df.columns:
        df['value_score'] = (df['projected_points'] / df['total_salary']) * 1000
    
    # Add uniqueness score
    if 'unique_players' in df.columns:
        df['uniqueness_score'] = (df['unique_players'] / 9) * 100  # Assuming 9 roster spots
    
    return df


def calculate_selection_stats(all_lineups_df: pd.DataFrame, selected_df: pd.DataFrame) -> Dict:
    """
    Calculate statistics about the selection
    
    Args:
        all_lineups_df: All available lineups
        selected_df: Selected optimal lineups
    
    Returns:
        Dict with selection statistics
    """
    stats = {
        # Projection stats
        'avg_projection': float(selected_df['projected_points'].mean()),
        'max_projection': float(selected_df['projected_points'].max()),
        'min_projection': float(selected_df['projected_points'].min()),
        'std_projection': float(selected_df['projected_points'].std()),
        
        # Ownership stats
        'avg_ownership': float(selected_df['avg_ownership'].mean()),
        'max_ownership': float(selected_df['avg_ownership'].max()),
        'min_ownership': float(selected_df['avg_ownership'].min()),
        
        # Quality stats
        'avg_quality_score': float(selected_df['quality_score'].mean()),
        
        # Comparison to full pool
        'projection_improvement': float(
            (selected_df['projected_points'].mean() / all_lineups_df['projected_points'].mean() - 1) * 100
        ),
        'ownership_reduction': float(
            (1 - selected_df['avg_ownership'].mean() / all_lineups_df['avg_ownership'].mean()) * 100
        ),
        
        # Selection info
        'total_available': len(all_lineups_df),
        'total_selected': len(selected_df),
        'selection_rate': float(len(selected_df) / len(all_lineups_df) * 100),
    }
    
    return stats