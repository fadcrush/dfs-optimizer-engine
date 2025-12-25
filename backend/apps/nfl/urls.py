from django.urls import path
from . import views

urlpatterns = [
    path('dashboard/', views.nfl_dashboard, name='nfl_dashboard'),
]