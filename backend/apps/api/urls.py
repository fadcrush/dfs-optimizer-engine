from django.urls import path
from . import views

urlpatterns = [
    path('health/', views.health_check, name='api_health'),
    path('nba/analyze/', views.analyze_nba, name='api_nba_analyze'),
    path('nba/optimize/', views.optimize_nba, name='api_nba_optimize'),
]