from django.urls import path
from . import views, views_optimizer, views_projections

urlpatterns = [
    # Main dashboard
    path('dashboard/', views.nba_dashboard, name='nba_dashboard'),
    path('download/', views.download_report, name='nba_download'),
    
    # Lineup selector & optimizer
    path('selector/', views_optimizer.lineup_selector, name='lineup_selector'),
    path('api/optimize/', views_optimizer.optimize_portfolio, name='optimize_portfolio'),
    path('export/selected/', views_optimizer.export_selected_lineups, name='export_selected_lineups'),
    
    # Projections
    path('projections/', views_projections.projection_dashboard, name='projection_dashboard'),
    path('projections/accuracy/', views_projections.projection_accuracy_report, name='projection_accuracy'),
    path('api/compare-projections/', views_projections.compare_projections, name='compare_projections'),
]