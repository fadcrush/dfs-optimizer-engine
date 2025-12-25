"""
REST API endpoints for DFS analysis
"""

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
import logging

logger = logging.getLogger(__name__)


@require_http_methods(["GET"])
def health_check(request):
    """
    API health check endpoint
    
    GET /api/health/
    Returns: JSON with status and available endpoints
    """
    return JsonResponse({
        'status': 'healthy',
        'version': '2.0',
        'features': ['nba', 'nfl', 'analysis', 'optimizer'],
        'endpoints': {
            'health': '/api/health/',
            'nba_analyze': '/api/nba/analyze/',
            'nba_optimize': '/api/nba/optimize/'
        }
    })


@require_http_methods(["POST"])
@csrf_exempt
def analyze_nba(request):
    """
    Analyze NBA lineups via API
    
    POST /api/nba/analyze/
    Files: lineups (CSV), projections (CSV - optional)
    Returns: JSON with analysis results
    """
    try:
        import pandas as pd
        
        if 'lineups' not in request.FILES:
            return JsonResponse(
                {'success': False, 'error': 'Missing lineups file'},
                status=400
            )
        
        lineups_file = request.FILES['lineups']
        lineups_df = pd.read_csv(lineups_file)
        
        proj_df = None
        if 'projections' in request.FILES:
            proj_file = request.FILES['projections']
            proj_df = pd.read_csv(proj_file)
        
        # Import analysis functions
        from analysis.nba.player_pool import analyze_nba_lineups, analyze_nba_lineups_with_projections
        
        if proj_df is not None:
            result = analyze_nba_lineups_with_projections(lineups_df, proj_df)
        else:
            result = analyze_nba_lineups(lineups_df)
        
        return JsonResponse({
            'success': True,
            'data': result
        })
        
    except Exception as e:
        logger.error(f'API analysis error: {e}', exc_info=True)
        return JsonResponse(
            {'success': False, 'error': str(e)},
            status=500
        )


@require_http_methods(["POST"])
@csrf_exempt
def optimize_nba(request):
    """
    Generate optimized NBA lineups via API
    
    POST /api/nba/optimize/
    Files: projections (CSV)
    POST data: site, n_lineups, locks, fades, max_exposure
    Returns: JSON with optimization results
    """
    try:
        import pandas as pd
        
        if 'projections' not in request.FILES:
            return JsonResponse(
                {'success': False, 'error': 'Missing projections file'},
                status=400
            )
        
        proj_file = request.FILES['projections']
        proj_df = pd.read_csv(proj_file)
        
        # Get parameters from POST data
        site = request.POST.get('site', 'AUTO')
        n_lineups = int(request.POST.get('n_lineups', 150))
        max_exposure = float(request.POST.get('max_exposure', 0.60))
        
        # Parse locks and fades (comma-separated)
        locks_str = request.POST.get('locks', '')
        fades_str = request.POST.get('fades', '')
        
        locks = [l.strip() for l in locks_str.split(',') if l.strip()]
        fades = [f.strip() for f in fades_str.split(',') if f.strip()]
        
        # Import and run pipeline
        from analysis.nba.pipeline import run_nba_pipeline
        
        result = run_nba_pipeline(
            projections_df=proj_df,
            site=site,
            n_lineups=n_lineups,
            max_exposure=max_exposure,
            locks=locks,
            fades=fades
        )
        
        return JsonResponse({
            'success': result.get('success', False),
            'data': result
        })
        
    except Exception as e:
        logger.error(f'API optimizer error: {e}', exc_info=True)
        return JsonResponse(
            {'success': False, 'error': str(e)},
            status=500
        )