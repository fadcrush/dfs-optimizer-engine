"""
NBA Dashboard Views - Complete with Full Analysis
"""

import logging
from pathlib import Path
import pandas as pd
from typing import List, Dict

from django.conf import settings
from django.http import HttpResponse, FileResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)


@require_http_methods(["GET", "POST"])
def nba_dashboard(request):
    """NBA DFS Dashboard with Full Analysis"""
    
    context = {
        "error": None,
        "note": None,
        "uploaded_lineups": None,
        "uploaded_projections": None,
        "analysis_mode": None,
        "num_rows": 0,
        "num_columns": 0,
        "site_selected": "AUTO",
        "n_lineups": 150,
        "min_unique": 2,
        "max_exposure": 0.60,
        "leverage_weight": 0.25,
        # Analysis results
        "lineup_exposure": [],
        "projections_table": [],
        "core_pool": [],
        "secondary_pool": [],
        "contrarian_pool": [],
        "fades": [],
        "lineups_summary": [],
    }
    
    if request.method == "GET":
        return render(request, "nba/dashboard.html", context)
    
    # POST - Handle analysis
    try:
        lineups_file = request.FILES.get("lineups_file")
        proj_file = request.FILES.get("projections_file")
        
        # Parse user inputs
        locks_text = request.POST.get("locks", "")
        fades_text = request.POST.get("fades", "")
        caps_text = request.POST.get("caps", "")
        
        locks = [x.strip() for x in locks_text.replace('\n', ',').split(',') if x.strip()]
        fades = [x.strip() for x in fades_text.replace('\n', ',').split(',') if x.strip()]
        
        # Parse caps (format: ID=0.35)
        caps = {}
        for line in caps_text.replace('\n', ',').split(','):
            if '=' in line:
                parts = line.split('=')
                if len(parts) == 2:
                    caps[parts[0].strip()] = float(parts[1].strip())
        
        context["site_selected"] = request.POST.get("site", "AUTO")
        context["n_lineups"] = int(request.POST.get("n_lineups", 150))
        context["min_unique"] = int(request.POST.get("min_unique", 2))
        context["max_exposure"] = float(request.POST.get("max_exposure", 0.60))
        context["leverage_weight"] = float(request.POST.get("leverage_weight", 0.25))
        
        # Determine analysis mode and run analysis
        if lineups_file and proj_file:
            # MODE: LINEUPS + PROJECTIONS (Full Analysis)
            lineups_df = pd.read_csv(lineups_file)
            proj_df = pd.read_csv(proj_file)
            
            context["uploaded_lineups"] = lineups_file.name
            context["uploaded_projections"] = proj_file.name
            context["num_rows"] = len(proj_df)
            context["num_columns"] = len(proj_df.columns)
            context["analysis_mode"] = "lineups_with_projections"
            
            logger.info(f"Running full analysis: {len(lineups_df)} lineups, {len(proj_df)} projections")
            
            # Import and run full analysis
            from analysis.nba.player_pool import analyze_nba_lineups_with_projections
            result = analyze_nba_lineups_with_projections(lineups_df, proj_df)
            
            # Update context with results
            context["lineup_exposure"] = result.get("lineup_exposure", [])
            context["core_pool"] = result.get("core_pool", [])
            context["secondary_pool"] = result.get("secondary_pool", [])
            context["contrarian_pool"] = result.get("contrarian_pool", [])
            context["fades"] = result.get("fades", [])
            context["lineups_summary"] = result.get("lineups_summary", [])
            
            context["note"] = f"✅ Analyzed {len(lineups_df)} lineups with projections"
            
        elif lineups_file:
            # MODE: LINEUPS ONLY
            lineups_df = pd.read_csv(lineups_file)
            
            context["uploaded_lineups"] = lineups_file.name
            context["num_rows"] = len(lineups_df)
            context["num_columns"] = len(lineups_df.columns)
            context["analysis_mode"] = "lineups"
            
            logger.info(f"Running lineups-only analysis: {len(lineups_df)} lineups")
            
            # Import and run lineups analysis
            from analysis.nba.player_pool import analyze_nba_lineups
            result = analyze_nba_lineups(lineups_df)
            
            context["lineup_exposure"] = result.get("lineup_exposure", [])
            context["note"] = f"✅ Analyzed {len(lineups_df)} lineups"
            
        elif proj_file:
            # MODE: PROJECTIONS ONLY
            proj_df = pd.read_csv(proj_file)
            
            context["uploaded_projections"] = proj_file.name
            context["num_rows"] = len(proj_df)
            context["num_columns"] = len(proj_df.columns)
            context["analysis_mode"] = "projections"
            
            logger.info(f"Running projections-only analysis: {len(proj_df)} players")
            
            # Show top 50 projections
            proj_sorted = proj_df.nlargest(50, 'My Proj') if 'My Proj' in proj_df.columns else proj_df.head(50)
            context["projections_table"] = proj_sorted.to_dict('records')
            context["note"] = f"✅ Showing top 50 from {len(proj_df)} projections"
        
        else:
            context["error"] = "Please upload at least one file"
    
    except Exception as e:
        logger.error(f"Analysis error: {e}", exc_info=True)
        context["error"] = f"Analysis failed: {str(e)}"
    
    return render(request, "nba/dashboard.html", context)


@require_http_methods(["GET"])
def download_report(request):
    """Download generated reports"""
    file_path_str = request.GET.get("path")
    
    if not file_path_str or file_path_str == "None":
        return HttpResponse("No file specified", status=400)
    
    try:
        file_path = Path(file_path_str)
        
        if not file_path.exists():
            return HttpResponse("File not found", status=404)
        
        return FileResponse(
            open(file_path, 'rb'),
            as_attachment=True,
            filename=file_path.name
        )
    except Exception as e:
        logger.error(f"Download error: {e}")
        return HttpResponse(f"Error: {str(e)}", status=500)