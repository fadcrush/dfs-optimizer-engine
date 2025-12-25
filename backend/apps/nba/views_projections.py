"""
Projection Management Views
View, compare, and track projection accuracy
"""

from typing import Dict, List  # ← ADD THIS
import logging
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

from django.conf import settings
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse

from .models import NbaSlate, NbaPlayerPerformance
from analysis.nba.projection_pipeline import ProjectionComparer, ProjectionPipeline

logger = logging.getLogger(__name__)


@require_http_methods(["GET", "POST"])
def projection_dashboard(request):
    """
    Projection management dashboard
    Generate new projections and view accuracy
    """
    context = {
        'error': None,
        'note': None,
        'recent_projections': [],
        'accuracy_history': []
    }
    
    if request.method == "GET":
        # Load recent projections
        proj_dir = Path(settings.EXPORT_ROOT) / 'projections'
        
        if proj_dir.exists():
            # Get last 7 days of projections
            recent_files = sorted(proj_dir.glob('FD_*_upload.csv'), reverse=True)[:7]
            
            for file in recent_files:
                date_str = file.stem.split('_')[1]
                context['recent_projections'].append({
                    'date': date_str,
                    'file': file.name,
                    'path': str(file)
                })
        
        # Load accuracy history
        context['accuracy_history'] = _load_accuracy_history()
        
        return render(request, 'nba/projections.html', context)
    
    # POST - Generate new projections
    try:
        target_date = request.POST.get('date', datetime.now().strftime('%Y-%m-%d'))
        site = request.POST.get('site', 'FD')
        
        logger.info(f"Generating projections for {target_date}")
        
        pipeline = ProjectionPipeline()
        results = pipeline.run_full_pipeline(target_date, site)
        
        if results['success']:
            context['note'] = f"✓ Generated {results['stats']['total_projections']} projections for {target_date}"
            context['projection_stats'] = results['stats']
            context['files'] = results['files']
        else:
            context['error'] = f"Projection generation failed: {', '.join(results['errors'])}"
    
    except Exception as e:
        logger.error(f"Projection error: {e}", exc_info=True)
        context['error'] = str(e)
    
    return render(request, 'nba/projections.html', context)


@require_http_methods(["POST"])
def compare_projections(request):
    """
    Compare projections to actual results
    """
    try:
        date = request.POST.get('date')
        site = request.POST.get('site', 'FD')
        
        # Load projections
        proj_file = Path(settings.EXPORT_ROOT) / 'projections' / f'{site}_{date}_full.csv'
        
        if not proj_file.exists():
            return JsonResponse({'error': 'Projections not found'}, status=404)
        
        projections = pd.read_csv(proj_file)
        
        # Get actuals from database
        try:
            slate = NbaSlate.objects.get(date=date, site=site)
            actuals = NbaPlayerPerformance.objects.filter(slate=slate)
            
            if not actuals.exists():
                return JsonResponse({'error': 'No actual results found'}, status=404)
            
            actuals_df = pd.DataFrame(list(actuals.values()))
            
            # Compare
            comparer = ProjectionComparer()
            metrics = comparer.compare_to_actuals(projections, actuals_df)
            
            # Save accuracy report
            _save_accuracy_report(date, site, metrics)
            
            return JsonResponse({
                'success': True,
                'metrics': metrics
            })
        
        except NbaSlate.DoesNotExist:
            return JsonResponse({'error': 'Slate not found'}, status=404)
    
    except Exception as e:
        logger.error(f"Comparison error: {e}", exc_info=True)
        return JsonResponse({'error': str(e)}, status=500)


@require_http_methods(["GET"])
def projection_accuracy_report(request):
    """Display projection accuracy over time"""
    
    accuracy_data = _load_accuracy_history()
    
    context = {
        'accuracy_data': accuracy_data,
        'avg_mae': sum(d['mae'] for d in accuracy_data) / len(accuracy_data) if accuracy_data else 0,
        'avg_r2': sum(d['r2'] for d in accuracy_data) / len(accuracy_data) if accuracy_data else 0,
    }
    
    return render(request, 'nba/projection_accuracy.html', context)


def _load_accuracy_history() -> list:
    """Load historical accuracy metrics"""
    accuracy_dir = Path(settings.EXPORT_ROOT) / 'projections' / 'accuracy'
    
    if not accuracy_dir.exists():
        return []
    
    history = []
    for file in sorted(accuracy_dir.glob('accuracy_*.json'), reverse=True)[:30]:
        import json
        with open(file) as f:
            data = json.load(f)
            history.append(data)
    
    return history


def _save_accuracy_report(date: str, site: str, metrics: Dict):
    """Save accuracy metrics to file"""
    accuracy_dir = Path(settings.EXPORT_ROOT) / 'projections' / 'accuracy'
    accuracy_dir.mkdir(parents=True, exist_ok=True)
    
    report = {
        'date': date,
        'site': site,
        'generated_at': datetime.now().isoformat(),
        'mae': metrics['mean_absolute_error'],
        'rmse': metrics['root_mean_squared_error'],
        'r2': metrics['r_squared'],
        'within_5': metrics['within_5_points'],
        'within_10': metrics['within_10_points'],
    }
    
    import json
    file_path = accuracy_dir / f'accuracy_{site}_{date}.json'
    with open(file_path, 'w') as f:
        json.dump(report, f, indent=2)