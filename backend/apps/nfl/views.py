"""
NFL Dashboard Views
"""

from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django.http import HttpResponse


@require_http_methods(["GET", "POST"])
def nfl_dashboard(request):
    """NFL DFS Dashboard"""
    
    context = {
        "note": "NFL analysis coming soon! Upload your files to test.",
        "uploaded_lineups": None,
        "uploaded_projections": None,
    }
    
    if request.method == "POST":
        lineups_file = request.FILES.get("lineups_file")
        proj_file = request.FILES.get("projections_file")
        
        if lineups_file:
            context["uploaded_lineups"] = lineups_file.name
            context["note"] = f"✅ Uploaded {lineups_file.name}"
        
        if proj_file:
            context["uploaded_projections"] = proj_file.name
            context["note"] = "✅ Files uploaded successfully!"
    
    return render(request, "nfl/dashboard.html", context)